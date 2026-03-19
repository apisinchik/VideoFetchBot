import logging
import os
import time
from typing import Dict, List, Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)


class CookiesManager:
    def __init__(
        self,
        profile_dir: Optional[str] = None,
        target_url: Optional[str] = None,
        proxy_url: Optional[str] = None,
    ):
        self.profile_dir = profile_dir or os.getenv("BROWSER_PROFILE_DIR", "chrome_profile")
        self.target_url = (
            target_url
            or os.getenv("VIDEOFETCHER_COOKIES_TARGET_URL")
            or os.getenv("COOKIES_TARGET_URL")
            or "https://www.youtube.com/"
        )
        self.proxy_url = (
            proxy_url
            or os.getenv("VIDEOFETCHER_PROXY_URL")
            or os.getenv("PROXY_URL")
            or ""
        ).strip()
        self.storage_state_path = (
            os.getenv("VIDEOFETCHER_COOKIES_STORAGE_STATE")
            or os.getenv("COOKIES_STORAGE_STATE")
            or os.path.join(self.profile_dir, "storage_state.json")
        ).strip()
        self.browser_channel = (
            os.getenv("VIDEOFETCHER_COOKIES_CHANNEL")
            or os.getenv("COOKIES_BROWSER_CHANNEL")
            or ""
        ).strip()
        self.headless = os.getenv("VIDEOFETCHER_COOKIES_HEADLESS", "0") in ("1", "true", "True")
        self.require_proxy = os.getenv("VIDEOFETCHER_COOKIES_REQUIRE_PROXY", "0") in ("1", "true", "True")
        self.no_sandbox = os.getenv("VIDEOFETCHER_COOKIES_NO_SANDBOX", "0") in ("1", "true", "True")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.no_sandbox = True

    async def fetch_cookies(self) -> List[Dict]:
        """Открывает target_url в контексте Playwright и возвращает cookies."""
        os.makedirs(self.profile_dir, exist_ok=True)

        try:
            if self.require_proxy and not self.proxy_url:
                logger.error("CookiesManager requires proxy, but PROXY_URL is not set")
                return []

            async with async_playwright() as playwright:
                launch_args = [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                    "--start-maximized",
                ]
                if self.no_sandbox:
                    launch_args.extend(["--no-sandbox", "--disable-setuid-sandbox"])

                context_options = {
                    "user_agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36"
                    ),
                    "no_viewport": True,
                    "ignore_https_errors": True,
                }
                if self.proxy_url:
                    context_options["proxy"] = {"server": self.proxy_url}

                launch_kwargs = {
                    "headless": self.headless,
                    "args": launch_args,
                }
                if self.browser_channel:
                    launch_kwargs["channel"] = self.browser_channel

                    logger.info("CookiesManager launching browser (headless=%s, proxy=%s, channel=%s)",
                    self.headless,
                    "enabled" if self.proxy_url else "disabled",
                    self.browser_channel or "chromium",
                )

                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=self.profile_dir,
                    **launch_kwargs,
                    **context_options,
                )
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(self.target_url, wait_until="networkidle")
                    if self.storage_state_path:
                        os.makedirs(os.path.dirname(self.storage_state_path) or ".", exist_ok=True)
                        await context.storage_state(path=self.storage_state_path)
                    return await context.cookies()
                finally:
                    await context.close()
        except PlaywrightTimeoutError:
            logger.error("Timed out while loading %s", self.target_url)
        except Exception as exc:
            msg = str(exc)
            if "Executable doesn't exist" in msg or "browser_type.launch" in msg:
                logger.error("Playwright browsers are not installed. Run: python -m playwright install chromium")
                logger.exception("Failed to fetch cookies from %s: %s", self.target_url, exc)

        return []

    async def save_cookies_to_file(self, cookies: List[Dict], file_path: str) -> bool:
        """Сохраняет cookies в формате Netscape (cookies.txt) для yt-dlp."""
        if not cookies:
            return False

        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)

        lines = [
            "# Netscape HTTP Cookie File",
            "# Generated: " + time.strftime("%Y-%m-%d %H:%M:%S"),
            "",
        ]

        for cookie in cookies:
            domain = (cookie or {}).get("domain") or ""
            name = (cookie or {}).get("name") or ""
            value = (cookie or {}).get("value") or ""
            if not domain or not name:
                continue
            path = (cookie or {}).get("path") or "/"
            secure = "TRUE" if (cookie or {}).get("secure") else "FALSE"
            expires = (cookie or {}).get("expires")
            if not isinstance(expires, (int, float)) or expires < 0:
                expires = 0
            domain_flag = "TRUE" if domain.startswith(".") else "FALSE"
            lines.append("\t".join([domain, domain_flag, path, secure, str(int(expires)), name, value]))

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return True

    async def fetch_and_save_cookies(self, file_path: str) -> bool:
        """Получает cookies с target_url и сохраняет их в файл."""
        cookies = await self.fetch_cookies()
        return await self.save_cookies_to_file(cookies, file_path)

    async def fetch_django_cookies(self) -> List[Dict]:
        """Deprecated: use fetch_cookies()."""
        return await self.fetch_cookies()

    async def fetch_and_save_django_cookies(self, file_path: str) -> bool:
        """Deprecated: use fetch_and_save_cookies()."""
        return await self.fetch_and_save_cookies(file_path)
