"""Network/proxy utilities."""

import asyncio
import logging
from typing import Tuple

import aiohttp

logger = logging.getLogger(__name__)


class ProxyChecker:
    def __init__(self, timeout_seconds: int = 10):
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def check_proxy(self, proxy_url: str) -> Tuple[bool, str]:
        """Check whether the proxy works."""
        proxy = (proxy_url or "").strip()
        if not proxy:
            return False, "Proxy URL is empty"

        proxy_lower = proxy.lower()
        try:
            if proxy_lower.startswith(("socks5://", "socks5h://", "socks4://", "socks4a://")):
                try:
                    from aiohttp_socks import ProxyConnector  # type: ignore
                except Exception as e:
                    return False, f"aiohttp-socks not available for SOCKS proxy: {e}"

                connector = ProxyConnector.from_url(proxy, rdns=True, ssl=False, limit=10)
                async with aiohttp.ClientSession(connector=connector, timeout=self.timeout) as session:
                    async with session.get("https://www.youtube.com/", ssl=False) as response:
                        if response.status == 200:
                            return True, "Proxy is working"
                        return False, f"Proxy returned status {response.status}"

            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector, timeout=self.timeout) as session:
                async with session.get("https://www.youtube.com/", proxy=proxy, ssl=False) as response:
                    if response.status == 200:
                        return True, "Proxy is working"
                    return False, f"Proxy returned status {response.status}"
        except asyncio.TimeoutError:
            return False, "Proxy timeout"
        except Exception as e:
            return False, f"Proxy error: {e}"

    async def test_direct_connection(self) -> bool:
        """Check a direct connection without proxy."""
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector, timeout=self.timeout) as session:
                async with session.get("https://www.google.com/", ssl=False) as response:
                    return response.status == 200
        except Exception:
            return False
