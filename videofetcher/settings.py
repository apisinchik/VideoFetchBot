from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class VideoFetcherSettings:
    """Configuration for :class:`videofetcher.service.VideoService`."""

    temp_dir: str = os.getenv("VIDEOFETCHER_TEMP_DIR", "temp")

    proxy_url: str | None = (
        os.getenv("VIDEOFETCHER_PROXY_URL")
        or os.getenv("PROXY_URL")
        or None
    )

    force_proxy_download: bool = os.getenv("VIDEOFETCHER_FORCE_PROXY_DOWNLOAD", "0") in (
        "1",
        "true",
        "True",
    )

    enable_browser_fallback: bool = os.getenv("VIDEOFETCHER_ENABLE_BROWSER_FALLBACK", "1") in (
        "1",
        "true",
        "True",
    )

    extraction_timeout: int = int(os.getenv("VIDEOFETCHER_EXTRACTION_TIMEOUT", "30"))
    download_timeout: int = int(os.getenv("VIDEOFETCHER_DOWNLOAD_TIMEOUT", "300"))
    connection_timeout: int = int(os.getenv("VIDEOFETCHER_CONNECTION_TIMEOUT", "10"))

    max_retries: int = int(os.getenv("VIDEOFETCHER_MAX_RETRIES", "3"))
    retry_delay: int = int(os.getenv("VIDEOFETCHER_RETRY_DELAY", "2"))

    ytdlp_cookies_file: str | None = (
        os.getenv("VIDEOFETCHER_YTDLP_COOKIES_FILE")
        or os.getenv("YTDLP_COOKIES_FILE")
        or None
    )
    ytdlp_cookies_from_browser: str | None = (
        os.getenv("VIDEOFETCHER_YTDLP_COOKIES_FROM_BROWSER")
        or os.getenv("YTDLP_COOKIES_FROM_BROWSER")
        or None
    )
    ytdlp_auto_refresh_cookies: bool = os.getenv(
        "VIDEOFETCHER_YTDLP_AUTO_REFRESH_COOKIES", "0"
    ) in ("1", "true", "True")

    ytdlp_retry_on_auth_error: bool = os.getenv("VIDEOFETCHER_YTDLP_RETRY_ON_AUTH_ERROR", "1") in (
        "1",
        "true",
        "True",
    )
    ytdlp_wait_cookie_update_seconds: int = int(
        os.getenv(
            "VIDEOFETCHER_YTDLP_WAIT_COOKIE_UPDATE_SECONDS",
            os.getenv("YTDLP_COOKIES_WAIT_UPDATE_SECONDS", "0"),
        )
    )

    ytdlp_fallback_to_mp4_on_hls_403: bool = os.getenv(
        "VIDEOFETCHER_YTDLP_FALLBACK_TO_MP4_ON_HLS_403", "1"
    ) in ("1", "true", "True")

    ytdlp_retry_on_forbidden_error: bool = os.getenv(
        "VIDEOFETCHER_YTDLP_RETRY_ON_FORBIDDEN_ERROR", "1"
    ) in ("1", "true", "True")
