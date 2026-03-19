import os
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


class Config:
    """Настройки проекта."""

    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Put it into .env")

    TEMP_DIR = os.getenv("TEMP_DIR", "temp")
    MEDIA_ROOT = os.getenv("MEDIA_ROOT", os.path.join(PROJECT_ROOT, "media"))

    PROXY_URL = (
        os.getenv("VIDEOFETCHER_PROXY_URL")
        or os.getenv("PROXY_URL")
        or None
    )
    FORCE_PROXY_DOWNLOAD = os.getenv(
        "VIDEOFETCHER_FORCE_PROXY_DOWNLOAD",
        os.getenv("FORCE_PROXY_DOWNLOAD", "0"),
    ) in ("1", "true", "True")

    EXTRACTION_TIMEOUT = int(os.getenv("EXTRACTION_TIMEOUT", "30"))
    DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))
    CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", "10"))

    MAX_CONCURRENT_ANALYSIS = max(1, int(os.getenv("MAX_CONCURRENT_ANALYSIS", "2")))
    ANALYSIS_USER_COOLDOWN_SECONDS = float(os.getenv("ANALYSIS_USER_COOLDOWN_SECONDS", "3"))
    USER_MAX_ACTIVE_JOBS = max(1, int(os.getenv("USER_MAX_ACTIVE_JOBS", "1")))
    RETRY_USER_COOLDOWN_SECONDS = float(os.getenv("RETRY_USER_COOLDOWN_SECONDS", "30"))

    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_DELAY = float(os.getenv("RETRY_DELAY", "2"))

    TELEGRAM_API_BASE_URL = os.getenv("TELEGRAM_API_BASE_URL")
    TELEGRAM_API_IS_LOCAL = os.getenv("TELEGRAM_API_IS_LOCAL", "1") in ("1", "true", "True")

    if not TELEGRAM_API_BASE_URL:
        raise RuntimeError(
            "TELEGRAM_API_BASE_URL is not set. Local Bot API Server is required. "
            "Example: http://127.0.0.1:8081"
        )
    if not TELEGRAM_API_IS_LOCAL:
        raise RuntimeError(
            "TELEGRAM_API_IS_LOCAL must be enabled (set to 1/true). Local Bot API Server is required."
        )

    TELEGRAM_MAX_UPLOAD_MB = int(os.getenv("TELEGRAM_MAX_UPLOAD_MB", "2000"))

    FILE_RETENTION_HOURS = int(os.getenv("FILE_RETENTION_HOURS", "6"))

    POSTGRES_DSN = os.getenv("POSTGRES_DSN")
    if not POSTGRES_DSN:
        raise RuntimeError(
            "POSTGRES_DSN is not set. PostgreSQL queue is required. "
            "Example: postgresql://videofetch:pass@127.0.0.1:5432/videofetch"
        )

    _cpu = os.cpu_count() or 2
    QUEUE_GENERAL_SLOTS = int(os.getenv("QUEUE_GENERAL_SLOTS", str(max(1, _cpu // 2))))
    QUEUE_SHORT_SLOTS = int(os.getenv("QUEUE_SHORT_SLOTS", "1"))

    SHORT_MAX_SECONDS = int(os.getenv("SHORT_MAX_SECONDS", "300"))

    QUEUE_POLL_INTERVAL = float(os.getenv("QUEUE_POLL_INTERVAL", "1.0"))

    QUEUE_REQUEUE_RUNNING_ON_STARTUP = os.getenv("QUEUE_REQUEUE_RUNNING_ON_STARTUP", "1") in ("1", "true", "True")

    WORKER_DOWNLOAD_TIMEOUT = int(os.getenv("WORKER_DOWNLOAD_TIMEOUT", str(100 * 60)))
    BROADCAST_POLL_INTERVAL = float(os.getenv("BROADCAST_POLL_INTERVAL", "1.0"))
    BROADCAST_MAX_RETRIES = int(os.getenv("BROADCAST_MAX_RETRIES", "5"))
    BROADCAST_RETRY_DELAY_SECONDS = float(os.getenv("BROADCAST_RETRY_DELAY_SECONDS", "5"))
    BROADCAST_STALE_RUNNING_SECONDS = int(os.getenv("BROADCAST_STALE_RUNNING_SECONDS", "300"))
    BROADCAST_SEND_DELAY_SECONDS = float(os.getenv("BROADCAST_SEND_DELAY_SECONDS", "0.15"))
