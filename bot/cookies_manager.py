import asyncio
import os
import logging
import aiohttp
import random
import time
from typing import Dict, Optional
from config import Config

logger = logging.getLogger(__name__)


class CookiesManager:
    def __init__(self):
        self.cookies_dir = Config.COOKIES_DIR
        self.session = None
        self.initialized = False

        os.makedirs(self.cookies_dir, exist_ok=True)

        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]

    async def initialize(self):
        """Простая инициализация"""
        if self.initialized:
            return True

        try:
            connector = aiohttp.TCPConnector(ssl=False)
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={'User-Agent': random.choice(self.user_agents)}
            )
            self.initialized = True
            return True
        except Exception as e:
            logger.error(f"Failed to initialize CookiesManager: {str(e)}")
            return False

    async def generate_youtube_cookies(self) -> Optional[str]:
        """Простая генерация cookies"""
        if not self.initialized:
            await self.initialize()

        try:
            cookies_dict = {}
            url = "https://www.youtube.com/"

            async with self.session.get(url, ssl=False) as response:
                # Собираем cookies
                for cookie in self.session.cookie_jar:
                    cookies_dict[cookie.key] = cookie.value

                # Также из response
                for cookie in response.cookies.values():
                    cookies_dict[cookie.key] = cookie.value

            if cookies_dict:
                cookies_file = os.path.join(self.cookies_dir, "youtube_cookies.txt")
                await self._save_cookies(cookies_dict, cookies_file)
                return cookies_file

        except Exception as e:
            logger.error(f"Error generating cookies: {str(e)}")

        return None

    async def _save_cookies(self, cookies_dict: Dict, file_path: str):
        """Сохраняем cookies"""
        lines = ["# Netscape HTTP Cookie File", "# Generated: " + time.strftime("%Y-%m-%d %H:%M:%S"), ""]

        for key, value in cookies_dict.items():
            line = "\t".join([".youtube.com", "TRUE", "/", "FALSE", "0", key, value])
            lines.append(line)

        with open(file_path, 'w') as f:
            f.write("\n".join(lines))

    async def ensure_fresh_cookies(self) -> bool:
        """Обеспечиваем свежие cookies"""
        try:
            cookies_file = await self.generate_youtube_cookies()
            return cookies_file is not None and os.path.exists(cookies_file)
        except Exception as e:
            logger.error(f"Error ensuring fresh cookies: {str(e)}")
            return False

    async def close(self):
        """Закрываем сессию"""
        if self.session:
            await self.session.close()
            self.initialized = False