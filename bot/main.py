import asyncio
import logging
import os
import pathlib
import sys

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import Config
from bot.handlers import router
from videofetcher.initialize import init_videofetcher
from bot.download_registry import DownloadRegistry
from bot.telegram_sender import TelegramFileSender
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from db.postgres_db import create_pool, init_schema
from db.postgres_queue import requeue_running_jobs, start_slots
from bot.queue_manager import QueueManager


class CustomAiohttpSession(AiohttpSession):
    """Aiohttp session with extended timeouts."""

    def __init__(self, api: TelegramAPIServer = None, timeout: int = 3600):
        self.timeout = aiohttp.ClientTimeout(total=timeout, connect=60, sock_connect=60, sock_read=timeout)
        super().__init__(api=api)

    def make_session(self) -> aiohttp.ClientSession:
        """Build aiohttp client session."""
        return aiohttp.ClientSession(
            timeout=self.timeout,
            connector=self.connector
        )


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )

    logger = logging.getLogger(__name__)

    os.makedirs(Config.TEMP_DIR, exist_ok=True)

    api = TelegramAPIServer.from_base(
        Config.TELEGRAM_API_BASE_URL,
        is_local=Config.TELEGRAM_API_IS_LOCAL,
    )

    session = CustomAiohttpSession(
        api=api,
        timeout=100 * 60,
    )

    bot = Bot(
        token=Config.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(
            parse_mode="Markdown",
            link_preview_is_disabled=True
        )
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    download_registry = DownloadRegistry(retention_seconds=Config.FILE_RETENTION_HOURS * 3600)
    bot.download_registry = download_registry
    bot.telegram_sender = TelegramFileSender(
        bot,
        max_upload_mb=Config.TELEGRAM_MAX_UPLOAD_MB,
        max_retries=max(3, getattr(Config, "MAX_RETRIES", 3)),
        base_timeout_s=1800,
    )

    async def registry_cleanup_loop():
        while True:
            await asyncio.sleep(600)
            removed = download_registry.cleanup()
            if removed:
                logger.info(f"Cleaned {removed} expired cached downloads")

    cleanup_task = asyncio.create_task(registry_cleanup_loop())

    db_pool = await create_pool(Config.POSTGRES_DSN, min_size=1, max_size=max(2, Config.QUEUE_GENERAL_SLOTS + Config.QUEUE_SHORT_SLOTS))
    await init_schema(db_pool, ROOT_DIR / "db" / "schema.sql")
    bot.db_pool = db_pool
    if getattr(Config, "QUEUE_REQUEUE_RUNNING_ON_STARTUP", False):
        try:
            requeued = await requeue_running_jobs(db_pool)
            if requeued:
                logger.warning(f"Requeued {requeued} running jobs after restart")
        except Exception as e:
            logger.error(f"Failed to requeue running jobs after restart: {e}")

    video_service = await init_videofetcher()

    bot.video_service = video_service

    await start_slots(db_pool, getattr(Config, "MAX_CONCURRENT_ANALYSIS", 2))

    queue_manager = QueueManager(
        bot=bot,
        db_pool=db_pool,
        video_service=video_service,
        telegram_sender=bot.telegram_sender,
        download_registry=download_registry,
    )
    queue_manager.start()
    bot.queue_manager = queue_manager

    dp.include_router(router)

    try:
        logger.info("Bot starting...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot error: {str(e)}")
    finally:
        cleanup_task.cancel()
        try:
            await queue_manager.stop()
        except Exception:
            pass
        await video_service.close()
        try:
            await db_pool.close()
        except Exception:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
