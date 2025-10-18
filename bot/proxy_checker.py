# proxy_checker.py
import asyncio
import aiohttp
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ProxyChecker:
    def __init__(self):
        self.timeout = aiohttp.ClientTimeout(total=10)

    async def check_proxy(self, proxy_url: str) -> Tuple[bool, str]:
        """Проверяем работоспособность прокси"""
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector, timeout=self.timeout) as session:
                # Пробуем подключиться к YouTube через прокси
                async with session.get('https://www.youtube.com/', proxy=proxy_url, ssl=False) as response:
                    if response.status == 200:
                        return True, "Proxy is working"
                    else:
                        return False, f"Proxy returned status {response.status}"
        except asyncio.TimeoutError:
            return False, "Proxy timeout"
        except Exception as e:
            return False, f"Proxy error: {str(e)}"

    async def test_direct_connection(self) -> bool:
        """Проверяем прямое соединение без прокси"""
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector, timeout=self.timeout) as session:
                async with session.get('https://www.google.com/', ssl=False) as response:
                    return response.status == 200
        except:
            return False