import logging
import os
from typing import Optional

from .hls_downloader import HLSVideoDownloader
from .proxy import ProxyChecker
from .service_download import VideoServiceDownloadMixin
from .service_extraction import VideoServiceExtractionMixin
from .service_playwright import VideoServicePlaywrightMixin, normalize_audio_tracks
from .settings import VideoFetcherSettings

logger = logging.getLogger(__name__)


class VideoService(
    VideoServiceDownloadMixin,
    VideoServiceExtractionMixin,
    VideoServicePlaywrightMixin,
):
    """Facade class that composes extraction, download and browser helpers."""

    def __init__(self, settings: Optional[VideoFetcherSettings] = None):
        self.settings = settings or VideoFetcherSettings()
        self.temp_dir = self.settings.temp_dir
        self.proxy_url = self.settings.proxy_url
        self.proxy_checker = ProxyChecker(timeout_seconds=self.settings.connection_timeout)
        self.use_proxy = True
        os.makedirs(self.temp_dir, exist_ok=True)

        self.ydl_opts = {
            'quiet': False,
            'no_warnings': False,
            'extract_flat': False,
        }

        explicit_cookiefile = (getattr(self.settings, "ytdlp_cookies_file", None) or "").strip()
        self._ytdlp_cookies_file = explicit_cookiefile or None
        self._ytdlp_cookies_file_explicit = bool(explicit_cookiefile)
        self._ytdlp_cookies_from_browser_raw = (
            getattr(self.settings, "ytdlp_cookies_from_browser", None) or ""
        ).strip()
        self._ytdlp_cookies_from_browser = self._parse_cookies_from_browser(
            self._ytdlp_cookies_from_browser_raw
        )
        self._ytdlp_auto_refresh_cookies = bool(
            getattr(self.settings, "ytdlp_auto_refresh_cookies", False)
        )
        self._ytdlp_cookies_mtime = None
        self._cookies_manager = None
        self._cookies_manager_failed = False

        self.playwright_available = False
        self._init_playwright()

    def _init_playwright(self):
        """Инициализация Playwright"""
        try:
            import playwright  # noqa: F401
            self.playwright_available = True
            logger.info("Playwright is available")
        except ImportError:
            logger.warning("Playwright not available")

    async def initialize(self):
        """Инициализация сервиса с проверкой прокси"""
        logger.info("Initializing VideoService...")

        force_proxy = bool(getattr(self.settings, "force_proxy_download", False))
        if self.proxy_url:
            is_working, message = await self.proxy_checker.check_proxy(self.proxy_url)
            if is_working:
                logger.info(f"Proxy is working: {message}")
                self.use_proxy = True
            else:
                logger.warning(f"Proxy not working: {message}")
                if force_proxy:
                    logger.warning("force_proxy_download enabled; continuing with proxy despite failed check")
                    self.use_proxy = True
                elif await self.proxy_checker.test_direct_connection():
                    logger.info("Direct connection is available, disabling proxy")
                    self.use_proxy = False
                else:
                    logger.error("No network connection available")
                    return False
        else:
            logger.info("No proxy configured, using direct connection")
            self.use_proxy = False

        return True
