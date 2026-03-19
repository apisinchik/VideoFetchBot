from __future__ import annotations

import asyncio
import logging
import math
import os
import pathlib

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile, InputMediaDocument, InputMediaPhoto, InputMediaVideo

from bot.broadcast_media import build_broadcast_send_plan
from config import Config
from db.postgres_broadcast import (
    BroadcastAttachmentRef,
    BroadcastDeliveryJob,
    claim_next_broadcast_delivery,
    mark_broadcast_delivery_failed,
    mark_broadcast_delivery_sent,
    requeue_running_broadcast_deliveries,
    reschedule_broadcast_delivery,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


def split_broadcast_text(text: str, limit: int = 4096) -> list[str]:
    text = (text or '').strip()
    if not text:
        return []

    chunks: list[str] = []
    current = text
    while len(current) > limit:
        split_at = current.rfind('\n', 0, limit)
        if split_at <= 0:
            split_at = current.rfind(' ', 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(current[:split_at].strip())
        current = current[split_at:].strip()
    if current:
        chunks.append(current)
    return chunks


class BroadcastManager:
    def __init__(self, *, bot: Bot, db_pool) -> None:
        self.bot = bot
        self.db_pool = db_pool
        self.poll_interval = float(getattr(Config, 'BROADCAST_POLL_INTERVAL', 1.0))
        self.max_retries = int(getattr(Config, 'BROADCAST_MAX_RETRIES', 5))
        self.retry_delay = float(getattr(Config, 'BROADCAST_RETRY_DELAY_SECONDS', 5))
        self.send_delay = float(getattr(Config, 'BROADCAST_SEND_DELAY_SECONDS', 0.15))
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info('BroadcastManager started')

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        try:
            requeued = await requeue_running_broadcast_deliveries(self.db_pool)
            if requeued:
                logger.warning(f'Requeued {requeued} running broadcast deliveries after restart')
        except Exception as exc:
            logger.exception(f'Broadcast recovery failed: {exc}')

        while True:
            try:
                delivery = await claim_next_broadcast_delivery(
                    self.db_pool,
                    worker_id=f'broadcast:{os.getpid()}',
                )
                if not delivery:
                    await asyncio.sleep(self.poll_interval)
                    continue
                await self._process_delivery(delivery)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(f'Broadcast loop error: {exc}')
                await asyncio.sleep(1.0)

    async def _process_delivery(self, delivery: BroadcastDeliveryJob) -> None:
        try:
            await self._send_delivery(delivery)
            await mark_broadcast_delivery_sent(self.db_pool, delivery_id=delivery.delivery_id)
        except TelegramRetryAfter as exc:
            delay = max(float(exc.retry_after), self.retry_delay)
            logger.warning(
                f'Broadcast delivery {delivery.delivery_id} hit retry_after={delay:.1f}s'
            )
            await reschedule_broadcast_delivery(
                self.db_pool,
                delivery_id=delivery.delivery_id,
                delay_seconds=delay,
                error_message=str(exc),
            )
        except (TelegramNetworkError, asyncio.TimeoutError) as exc:
            await self._retry_or_fail(delivery, exc)
        except (TelegramForbiddenError, TelegramBadRequest, FileNotFoundError) as exc:
            logger.warning(f'Broadcast delivery {delivery.delivery_id} failed permanently: {exc}')
            await mark_broadcast_delivery_failed(
                self.db_pool,
                delivery_id=delivery.delivery_id,
                error_message=str(exc),
            )
        except Exception as exc:
            await self._retry_or_fail(delivery, exc)

    async def _retry_or_fail(self, delivery: BroadcastDeliveryJob, exc: Exception) -> None:
        error_message = str(exc)
        if delivery.attempts >= self.max_retries:
            logger.warning(
                f'Broadcast delivery {delivery.delivery_id} exhausted retries: {error_message}'
            )
            await mark_broadcast_delivery_failed(
                self.db_pool,
                delivery_id=delivery.delivery_id,
                error_message=error_message,
            )
            return

        delay = min(300.0, self.retry_delay * (2 ** max(0, delivery.attempts - 1)))
        logger.warning(
            f'Broadcast delivery {delivery.delivery_id} failed, retry in {delay:.1f}s: {error_message}'
        )
        await reschedule_broadcast_delivery(
            self.db_pool,
            delivery_id=delivery.delivery_id,
            delay_seconds=delay,
            error_message=error_message,
        )

    async def _send_delivery(self, delivery: BroadcastDeliveryJob) -> None:
        plan = build_broadcast_send_plan(delivery.text, delivery.attachments)

        if plan.mode == 'text':
            text_chunks = split_broadcast_text(plan.caption)
            for chunk in text_chunks:
                await self.bot.send_message(
                    chat_id=delivery.chat_id,
                    text=chunk,
                    parse_mode=None,
                )
                await asyncio.sleep(self.send_delay)
            return

        paths = [self._resolve_attachment_path(item) for item in delivery.attachments]
        timeout = self._request_timeout_for_paths(paths)

        if plan.mode == 'single_photo':
            attachment = delivery.attachments[0]
            await self.bot.send_photo(
                chat_id=delivery.chat_id,
                photo=FSInputFile(paths[0], filename=attachment.original_name or os.path.basename(paths[0])),
                caption=plan.caption or None,
                parse_mode=None,
                request_timeout=timeout,
            )
            return

        if plan.mode == 'single_video':
            attachment = delivery.attachments[0]
            await self.bot.send_video(
                chat_id=delivery.chat_id,
                video=FSInputFile(paths[0], filename=attachment.original_name or os.path.basename(paths[0])),
                caption=plan.caption or None,
                parse_mode=None,
                supports_streaming=True,
                request_timeout=timeout,
            )
            return

        if plan.mode == 'single_document':
            attachment = delivery.attachments[0]
            await self.bot.send_document(
                chat_id=delivery.chat_id,
                document=FSInputFile(paths[0], filename=attachment.original_name or os.path.basename(paths[0])),
                caption=plan.caption or None,
                parse_mode=None,
                request_timeout=timeout,
            )
            return

        if plan.mode == 'group_media':
            media = []
            for idx, (attachment, path, kind) in enumerate(zip(delivery.attachments, paths, plan.attachment_kinds)):
                caption = plan.caption if idx == 0 and plan.caption else None
                if kind == 'photo':
                    media.append(
                        InputMediaPhoto(
                            media=FSInputFile(path, filename=attachment.original_name or os.path.basename(path)),
                            caption=caption,
                            parse_mode=None,
                        )
                    )
                else:
                    media.append(
                        InputMediaVideo(
                            media=FSInputFile(path, filename=attachment.original_name or os.path.basename(path)),
                            caption=caption,
                            parse_mode=None,
                            supports_streaming=True,
                        )
                    )
            await self.bot.send_media_group(
                chat_id=delivery.chat_id,
                media=media,
                request_timeout=timeout,
            )
            return

        if plan.mode == 'group_document':
            media = []
            for idx, (attachment, path) in enumerate(zip(delivery.attachments, paths)):
                media.append(
                    InputMediaDocument(
                        media=FSInputFile(path, filename=attachment.original_name or os.path.basename(path)),
                        caption=plan.caption if idx == 0 and plan.caption else None,
                        parse_mode=None,
                    )
                )
            await self.bot.send_media_group(
                chat_id=delivery.chat_id,
                media=media,
                request_timeout=timeout,
            )
            return

        raise RuntimeError(f'Unsupported broadcast send plan: {plan.mode}')

    def _resolve_attachment_path(self, attachment: BroadcastAttachmentRef) -> str:
        raw_path = (attachment.file or '').strip()
        if not raw_path:
            raise FileNotFoundError('Broadcast attachment path is empty')

        path = pathlib.Path(raw_path)
        if not path.is_absolute():
            path = pathlib.Path(getattr(Config, 'MEDIA_ROOT', PROJECT_ROOT / 'media')) / path
        path = path.resolve()

        media_root = pathlib.Path(getattr(Config, 'MEDIA_ROOT', PROJECT_ROOT / 'media')).resolve()
        if media_root not in path.parents and path != media_root:
            raise FileNotFoundError(f'Attachment path is outside media root: {path}')
        if not path.exists():
            raise FileNotFoundError(str(path))
        return str(path)

    def _request_timeout(self, file_path: str) -> int:
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        timeout = int(180 + math.ceil(size_mb * 6))
        return min(timeout, 4 * 3600)

    def _request_timeout_for_paths(self, paths: list[str]) -> int:
        if not paths:
            return 180
        return max(self._request_timeout(path) for path in paths)
