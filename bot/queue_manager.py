from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from typing import Any, Dict, Optional

from aiogram import Bot

from config import Config
from bot.media_utils import estimate_voice_size_bytes
from db.postgres_queue import (
    QueueJob,
    claim_next,
    update_progress,
    mark_done,
    mark_failed,
    mark_canceled,
)

logger = logging.getLogger(__name__)


class QueueManager:
    """Background workers consuming jobs from PostgreSQL queue."""

    def __init__(self, *, bot: Bot, db_pool, video_service, telegram_sender, download_registry) -> None:
        self.bot = bot
        self.db_pool = db_pool
        self.video_service = video_service
        self.telegram_sender = telegram_sender
        self.download_registry = download_registry

        self.general_slots = int(getattr(Config, "QUEUE_GENERAL_SLOTS", 1))
        self.short_slots = int(getattr(Config, "QUEUE_SHORT_SLOTS", 1))
        self.poll_interval = float(getattr(Config, "QUEUE_POLL_INTERVAL", 1.0))
        self.worker_download_timeout = int(getattr(Config, "WORKER_DOWNLOAD_TIMEOUT", 6000))

        self._tasks: list[asyncio.Task] = []
        self._job_tasks: dict[int, asyncio.Task] = {}

    def start(self) -> None:
        host = socket.gethostname()
        pid = os.getpid()

        for i in range(self.short_slots):
            wid = f"{host}:{pid}:short:{i}"
            self._tasks.append(asyncio.create_task(self._worker_loop(is_short=True, worker_id=wid)))

        for i in range(self.general_slots):
            wid = f"{host}:{pid}:general:{i}"
            self._tasks.append(asyncio.create_task(self._worker_loop(is_short=False, worker_id=wid)))

            logger.info(f"QueueManager started. short_slots={self.short_slots}, general_slots={self.general_slots}")

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _worker_loop(self, *, is_short: bool, worker_id: str) -> None:
        while True:
            try:
                job = await claim_next(self.db_pool, is_short=is_short, worker_id=worker_id)
                if not job:
                    await asyncio.sleep(self.poll_interval)
                    continue

                if job.created_via == 'telegram':
                    task = asyncio.create_task(self._tg_process_job(job))
                elif job.created_via == 'web':
                    task = asyncio.create_task(self._web_process_job(job))

                self._job_tasks[job.id] = task
                try:
                    await task
                finally:
                    self._job_tasks.pop(job.id, None)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Worker loop error ({worker_id}): {e}")
                await asyncio.sleep(1.0)

    async def cancel_job(self, job_id: int, reason: str = "canceled by user") -> bool:
        """Cancel a running job if possible. Returns True if a running task was signaled."""
        task = self._job_tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return True
        return False

    async def _web_process_job(self, job: QueueJob) -> None:
        selected_format: Dict[str, Any] = job.selected_format or {}
        selected_audio: Optional[Dict[str, Any]] = job.selected_audio
        media_kind = "audio" if selected_format.get("is_audio") else "video"

        title = job.title or selected_format.get("title") or "Видео"
        quality = selected_format.get("quality") or selected_format.get("format_id") or "?"
        audio_name = (selected_audio or {}).get("name")

        async def progress_cb(p: float) -> None:
            prog_int = max(0, min(99, int(p)))
            await update_progress(self.db_pool, job_id=job.id, progress=prog_int, stage="downloading")

        try:

            await update_progress(self.db_pool, job_id=job.id, progress=0, stage="starting")
            

            file_path = await asyncio.wait_for(
                self.video_service.download_video(
                    selected_format,
                    job.id,
                    progress_cb,
                    selected_audio,
                    source_url=job.source_url,
                ),
                timeout=self.worker_download_timeout,
            )

            if not file_path or not os.path.exists(file_path):
                raise RuntimeError("download_video returned empty path")

            if media_kind == "audio":
                duration_s = int(selected_format.get("duration") or job.duration_seconds or 0)
                size_bytes = estimate_voice_size_bytes(duration_s) or os.path.getsize(file_path)
            else:
                size_bytes = os.path.getsize(file_path)
            caption = f"🎬 **{title}**\n\n✅ **Качество:** {quality}\n"
            if audio_name:
                caption += f"🎵 **Озвучка:** {audio_name}\n"
            caption += f"📦 **Готово** ({size_bytes / (1024*1024):.1f} MB)"

            if self.download_registry and job.telegram_user_id:
                self.download_registry.set(job.telegram_user_id, file_path, caption, media_kind=media_kind)

            await update_progress(self.db_pool, job_id=job.id, progress=99, stage="finalizing")
            

            await mark_done(
                self.db_pool,
                job_id=job.id,
                result_path=file_path,
                result_size_bytes=size_bytes,
                result_meta={"quality": quality, "audio": audio_name},
            )

        except asyncio.CancelledError:
            await mark_canceled(self.db_pool, job_id=job.id, reason="canceled by user")
            try:
                await self.video_service.cleanup_user_files(job.id)
            except Exception:
                pass
            return
        except Exception as e:
            err = str(e)
            logger.exception(f"Job {job.id} failed: {err}")
            await mark_failed(self.db_pool, job_id=job.id, error_message=err)
            try:
                low = (err or "").lower()
                msg = (
                        "❌ **Не удалось обработать задачу**\n\n"
                        f"Ошибка: `{err[:300]}`\n\n"
                        "Если файл уже скачался, но не отправился — попробуйте /retry."
                    )
            except Exception:
                pass

    async def _tg_process_job(self, job: QueueJob) -> None:
        chat_id = job.telegram_chat_id
        msg_id = job.progress_msg_id
        if not chat_id or not msg_id or not job.id:
            await mark_failed(self.db_pool, job_id=job.id, error_message="Missing telegram context")
            return

        selected_format: Dict[str, Any] = job.selected_format or {}
        selected_audio: Optional[Dict[str, Any]] = job.selected_audio
        media_kind = "audio" if selected_format.get("is_audio") else "video"

        title = job.title or selected_format.get("title") or "Видео"
        quality = selected_format.get("quality") or selected_format.get("format_id") or "?"
        audio_name = (selected_audio or {}).get("name")

        start_time = time.time()

        async def progress_cb(p: float) -> None:
            prog_int = max(0, min(99, int(p)))
            await update_progress(self.db_pool, job_id=job.id, progress=prog_int, stage="downloading")
            try:
                elapsed = int(time.time() - start_time)
                text = f"⏳ **Скачивание...**\n\n🎬 **{title}**\n🎯 **Качество:** {quality}\n"
                if audio_name:
                    text += f"🎵 **Озвучка:** {audio_name}\n"
                text += f"\n📥 Прогресс: **{prog_int}%**\n⏱ Прошло: {elapsed}s"
                await self.bot.edit_message_text(text=text, chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass

        try:
            await update_progress(self.db_pool, job_id=job.id, progress=0, stage="starting")
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"🚀 **Стартуем загрузку**\n\n🎬 **{title}**\n🎯 **Качество:** {quality}",
                )
            except Exception:
                pass

            file_path = await asyncio.wait_for(
                self.video_service.download_video(
                    selected_format,
                    job.id,
                    progress_cb,
                    selected_audio,
                    source_url=job.source_url,
                ),
                timeout=self.worker_download_timeout,
            )

            if not file_path or not os.path.exists(file_path):
                raise RuntimeError("download_video returned empty path")

            if media_kind == "audio":
                duration_s = int(selected_format.get("duration") or job.duration_seconds or 0)
                size_bytes = estimate_voice_size_bytes(duration_s) or os.path.getsize(file_path)
            else:
                size_bytes = os.path.getsize(file_path)
            caption = f"🎬 **{title}**\n\n✅ **Качество:** {quality}\n"
            if audio_name:
                caption += f"🎵 **Озвучка:** {audio_name}\n"
            caption += f"📦 **Готово** ({size_bytes / (1024*1024):.1f} MB)"

            if self.download_registry and job.telegram_user_id:
                self.download_registry.set(job.telegram_user_id, file_path, caption, media_kind=media_kind)

            await update_progress(self.db_pool, job_id=job.id, progress=99, stage="uploading")
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"📤 **Отправляю в Telegram...**\n\n🎬 **{title}**\n🎯 **{quality}**",
                )
            except Exception:
                pass

            await self.telegram_sender.send_media(chat_id, file_path, caption, media_kind=media_kind)

            await mark_done(
                self.db_pool,
                job_id=job.id,
                result_path=file_path,
                result_size_bytes=size_bytes,
                result_meta={"quality": quality, "audio": audio_name},
            )

            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"✅ **Готово!** Файл отправлен.\n\n🎬 **{title}**",
                )
            except Exception:
                pass

        except asyncio.CancelledError:
            await mark_canceled(self.db_pool, job_id=job.id, reason="canceled by user")
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="❌ **Скачивание отменено пользователем.**",
                )
            except Exception:
                pass
            try:
                await self.video_service.cleanup_user_files(job.id)
            except Exception:
                pass
            return
        except Exception as e:
            err = str(e)
            logger.exception(f"Job {job.id} failed: {err}")
            await mark_failed(self.db_pool, job_id=job.id, error_message=err)
            try:
                low = (err or "").lower()
                if ("youtube_auth_required" in low) or ("sign in to confirm" in low and "not a bot" in low):
                    msg = (
                        "❌ **YouTube запросил подтверждение/авторизацию**\n\n"
                        "Администратор должен обновить cookies.txt (см. `YTDLP_COOKIES_FILE`) и повторить попытку."
                    )
                else:
                    msg = (
                        "❌ **Не удалось обработать задачу**\n\n"
                        f"Ошибка: `{err[:300]}`\n\n"
                        "Если файл уже скачался, но не отправился — попробуйте /retry."
                    )
                await self.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=msg)
            except Exception:
                pass
