import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Настройки бота
    BOT_TOKEN = os.getenv('BOT_TOKEN')

    # Настройки путей
    TEMP_DIR = 'temp'

    # Настройки прокси (SOCKS)
    PROXY_URL = 'socks://127.0.0.1:10810/'

    # Настройки таймаутов (в секундах)
    EXTRACTION_TIMEOUT = 30
    DOWNLOAD_TIMEOUT = 300
    CONNECTION_TIMEOUT = 10

    # Настройки повторных попыток
    MAX_RETRIES = 3
    RETRY_DELAY = 2