from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge, TelegramNetworkError
from aiogram.types import FSInputFile

logger = logging.getLogger(__name__)


class FileTooLargeForTelegram(Exception):
    """Выбрасывается, если размер файла превышает настроенный лимит загрузки Telegram."""


@dataclass
class SendResult:
    ok: bool
    message_id: Optional[int] = None
    method: Optional[str] = None


class TelegramFileSender:
    """Отвечает за загрузку файлов в Telegram с повторами и нарастающей паузой."""

    def __init__(
        self,
        bot: Bot,
        *,
        max_upload_mb: int,
        max_retries: int = 3,
        base_timeout_s: int = 1800,
    ) -> None:
        self.bot = bot
        self.max_upload_mb = max_upload_mb
        self.max_retries = max_retries
        self.base_timeout_s = base_timeout_s

    def _file_size_mb(self, file_path: str) -> float:
        return os.path.getsize(file_path) / (1024 * 1024)

    def _request_timeout(self, file_size_mb: float) -> int:
        timeout = int(self.base_timeout_s + (file_size_mb * 6))
        return min(timeout, 4 * 3600)

    def _prepare_voice_file(self, file_path: str) -> str:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError("ffmpeg is required to send audio as voice message")

        temp_dir = os.path.dirname(file_path) or None
        fd, voice_path = tempfile.mkstemp(suffix=".ogg", prefix="voice_", dir=temp_dir)
        os.close(fd)

        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            file_path,
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "libopus",
            "-b:a",
            "64k",
            "-vbr",
            "on",
            "-compression_level",
            "10",
            "-application",
            "voip",
            voice_path,
        ]

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0 or not os.path.exists(voice_path) or os.path.getsize(voice_path) <= 0:
            err = (proc.stderr or proc.stdout or "").strip()
            try:
                if os.path.exists(voice_path):
                    os.remove(voice_path)
            except OSError:
                pass
            raise RuntimeError(f"ffmpeg failed to prepare voice message: {err[-500:]}")

        return voice_path

    async def send_media(self, chat_id: int, file_path: str, caption: str, media_kind: str = "video") -> SendResult:
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        prepared_path = file_path
        cleanup_prepared = False
        if media_kind == "audio":
            prepared_path = self._prepare_voice_file(file_path)
            cleanup_prepared = prepared_path != file_path

        file_size_mb = self._file_size_mb(prepared_path)
        try:
            if file_size_mb > self.max_upload_mb:
                raise FileTooLargeForTelegram(
                    f"File is {file_size_mb:.1f} MB, limit is {self.max_upload_mb} MB"
                )

            timeout = self._request_timeout(file_size_mb)
            logger.info(f"Uploading {file_size_mb:.1f}MB as {media_kind} (timeout={timeout}s): {prepared_path}")

            last_exc: Optional[Exception] = None

            for attempt in range(1, self.max_retries + 1):
                try:
                    if media_kind == "audio":
                        msg = await self.bot.send_voice(
                            chat_id=chat_id,
                            voice=FSInputFile(prepared_path),
                            caption=caption,
                            request_timeout=timeout,
                        )
                        return SendResult(ok=True, message_id=msg.message_id, method="send_voice")

                    msg = await self.bot.send_video(
                        chat_id=chat_id,
                        video=FSInputFile(prepared_path),
                        caption=caption,
                        supports_streaming=True,
                        request_timeout=timeout,
                    )
                    return SendResult(ok=True, message_id=msg.message_id, method="send_video")

                except (TelegramEntityTooLarge, TelegramBadRequest):
                    if media_kind == "audio":
                        raise
                    try:
                        msg = await self.bot.send_document(
                            chat_id=chat_id,
                            document=FSInputFile(prepared_path),
                            caption=caption,
                            request_timeout=timeout,
                        )
                        return SendResult(ok=True, message_id=msg.message_id, method="send_document")
                    except TelegramEntityTooLarge:
                        raise
                    except Exception as e:
                        last_exc = e

                except TelegramNetworkError as e:
                    last_exc = e

                except asyncio.TimeoutError as e:
                    last_exc = e

                except Exception as e:
                    last_exc = e

                if attempt < self.max_retries:
                    backoff = min(30, (2 ** (attempt - 1)) * 3)
                    jitter = random.uniform(0.2, 1.2)
                    sleep_s = backoff + jitter
                    logger.warning(f"Upload attempt {attempt} failed: {last_exc}. Retrying in {sleep_s:.1f}s")
                    await asyncio.sleep(sleep_s)

            if last_exc:
                raise last_exc
            raise RuntimeError("Upload failed without exception")
        finally:
            if cleanup_prepared:
                try:
                    if os.path.exists(prepared_path):
                        os.remove(prepared_path)
                except OSError:
                    pass

    async def send_video_or_document(self, chat_id: int, file_path: str, caption: str) -> SendResult:
        return await self.send_media(chat_id, file_path, caption, media_kind="video")
