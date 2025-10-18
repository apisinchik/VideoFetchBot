import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import Config
from handlers import router
from services import VideoService


async def main():
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )

    logger = logging.getLogger(__name__)

    # Создаем необходимые директории
    os.makedirs(Config.TEMP_DIR, exist_ok=True)

    # Инициализируем бот с увеличенными таймаутами
    bot = Bot(token=Config.BOT_TOKEN, timeout=90)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Инициализируем сервис видео
    video_service = VideoService()

    # Инициализируем сервис (проверяем прокси и т.д.)
    if not await video_service.initialize():
        logger.error("Failed to initialize video service")
        return

    # Сохраняем сервис в данных бота
    bot.video_service = video_service

    # Подключаем роутер
    dp.include_router(router)

    try:
        logger.info("Bot starting...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot error: {str(e)}")
    finally:
        await video_service.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())