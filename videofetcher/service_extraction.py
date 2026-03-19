import asyncio
import logging
import os
import re
import shutil
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import m3u8
import yt_dlp

from .hls_downloader import HLSVideoDownloader
from .url_safety import is_public_http_url

logger = logging.getLogger(__name__)


class VideoServiceExtractionMixin:
    def _build_ydl_http_headers(self) -> Dict[str, str]:
        """Возвращает базовый набор заголовков для yt-dlp запросов."""
        return {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'ru,en;q=0.9',
        }

    def _get_ydl_opts(self, download: bool = False, format_id: str = None,
                      use_proxy: bool = None) -> Dict:
        """Получаем опции для yt-dlp с возможностью отключения прокси"""
        if use_proxy is None:
            use_proxy = self.use_proxy

        opts = self.ydl_opts.copy()

        if use_proxy and self.proxy_url:
            opts['proxy'] = self.proxy_url
            logger.info("Using proxy for yt-dlp")
        else:
            logger.info("Using direct connection for yt-dlp")

        opts.update({
            'socket_timeout': self.settings.connection_timeout,
            'extractor_retries': self.settings.max_retries,
            'retries': self.settings.max_retries,
            'fragment_retries': self.settings.max_retries,
            'ignoreerrors': False,
            'noplaylist': True,
            'no_check_certificate': True,
            'prefer_insecure': True,
            'http_headers': self._build_ydl_http_headers(),
        })

        if download:
            opts.update({
                'format': format_id,
                'outtmpl': os.path.join(self.temp_dir, '%(id)s.%(ext)s'),
                'merge_output_format': 'mp4',
            })
        else:
            opts.update({
                'extract_flat': False,
            })

        self._apply_ytdlp_js_challenge_opts(opts)
        self._apply_ytdlp_cookie_source(opts)
        return opts

    def _parse_cookies_from_browser(self, value: str) -> Optional[Tuple[str, Optional[str], Optional[str], Optional[str]]]:
        if not value:
            return None
        mobj = re.fullmatch(r'''(?x)
            (?P<name>[^+:]+)
            (?:\s*\+\s*(?P<keyring>[^:]+))?
            (?:\s*:\s*(?!:)(?P<profile>.+?))?
            (?:\s*::\s*(?P<container>.+))?
        ''', value)
        if mobj is None:
            logger.warning("Invalid YTDLP_COOKIES_FROM_BROWSER value: %s", value)
            return None

        browser_name, keyring, profile, container = mobj.group(
            "name", "keyring", "profile", "container"
        )
        browser_name = (browser_name or "").lower()
        keyring = keyring.upper() if keyring else None
        return browser_name, profile, keyring, container

    def _apply_ytdlp_cookie_source(self, opts: Dict) -> None:
        """Подключает предпочитаемый источник cookie к опциям yt-dlp."""
        if self._ytdlp_cookies_from_browser:
            opts["cookiesfrombrowser"] = self._ytdlp_cookies_from_browser
            return

        if not self._ytdlp_cookies_file:
            return

        try:
            if os.path.exists(self._ytdlp_cookies_file):
                opts['cookiefile'] = self._ytdlp_cookies_file
                try:
                    self._ytdlp_cookies_mtime = os.path.getmtime(self._ytdlp_cookies_file)
                except Exception:
                    pass
            else:
                logger.warning(f"YTDLP_COOKIES_FILE is set but file does not exist: {self._ytdlp_cookies_file}")
        except Exception as e:
            logger.warning(f"Failed to attach cookiefile to yt-dlp options: {e}")

    def _apply_ytdlp_js_challenge_opts(self, opts: Dict) -> None:
        remote_components = (
            os.getenv("VIDEOFETCHER_YTDLP_REMOTE_COMPONENTS")
            or os.getenv("YTDLP_REMOTE_COMPONENTS")
            or ""
        ).strip()
        if remote_components:
            components = [c.strip() for c in remote_components.split(",") if c.strip()]
            if components:
                opts["remote_components"] = components

        js_runtimes_env = (
            os.getenv("VIDEOFETCHER_YTDLP_JS_RUNTIMES")
            or os.getenv("YTDLP_JS_RUNTIMES")
            or ""
        ).strip()
        js_runtimes: Dict[str, Dict] = {}
        if js_runtimes_env:
            for part in js_runtimes_env.split(","):
                item = part.strip()
                if not item:
                    continue
                if ":" in item:
                    name, path = item.split(":", 1)
                    name = name.strip().lower()
                    path = path.strip()
                    if name:
                        js_runtimes[name] = {"path": path} if path else {}
                else:
                    js_runtimes[item.lower()] = {}
        else:
            runtime_candidates = [
                ("node", "node"),
                ("deno", "deno"),
                ("bun", "bun"),
                ("quickjs", "qjs"),
            ]
            for name, exe in runtime_candidates:
                if shutil.which(exe):
                    js_runtimes[name] = {}
                    break
        if js_runtimes:
            opts["js_runtimes"] = js_runtimes

    def _load_cookies_manager(self):
        if self._cookies_manager or self._cookies_manager_failed:
            return self._cookies_manager
        try:
            from .cookies_manager import CookiesManager

            self._cookies_manager = CookiesManager()
        except Exception as exc:
            self._cookies_manager_failed = True
            logger.warning("CookiesManager unavailable: %s", exc)
        return self._cookies_manager

    async def _ensure_cookiefile(self, source_url: Optional[str] = None) -> bool:
        if self._ytdlp_cookies_from_browser:
            return True

        if self._ytdlp_cookies_file:
            return os.path.exists(self._ytdlp_cookies_file)

        default_cookiefile = os.path.join(self.temp_dir, "ytdlp_cookies.txt")
        if os.path.exists(default_cookiefile):
            self._ytdlp_cookies_file = default_cookiefile
            logger.info("Using existing default yt-dlp cookies file: %s", self._ytdlp_cookies_file)
            return True

        if not self._is_youtube_url(source_url or ""):
            return False

        if not self._ytdlp_auto_refresh_cookies:
            return False

        self._ytdlp_cookies_file = default_cookiefile
        logger.info("YTDLP cookies file not set, using default: %s", self._ytdlp_cookies_file)
        if os.path.exists(self._ytdlp_cookies_file):
            return True
        return await self._refresh_cookiefile(source_url)

    async def _refresh_cookiefile(self, source_url: Optional[str] = None) -> bool:
        if self._ytdlp_cookies_from_browser:
            return False

        if source_url and not self._is_youtube_url(source_url):
            logger.info("Skipping cookie refresh for non-YouTube URL")
            return False

        if self._ytdlp_cookies_file_explicit:
            logger.info("Explicit cookies file configured; skipping automatic refresh")
            return False

        default_cookiefile = os.path.join(self.temp_dir, "ytdlp_cookies.txt")
        cookiefile_path = self._ytdlp_cookies_file or default_cookiefile
        implicit_default_cookiefile = (
            os.path.normpath(cookiefile_path) == os.path.normpath(default_cookiefile)
        )

        if not self._ytdlp_auto_refresh_cookies and not implicit_default_cookiefile:
            logger.info("Automatic cookie refresh is disabled; keeping existing cookie source untouched")
            return False

        if not self._ytdlp_auto_refresh_cookies and implicit_default_cookiefile:
            logger.info("Refreshing implicit default yt-dlp cookies via CookiesManager")

        if not self._ytdlp_cookies_file:
            self._ytdlp_cookies_file = default_cookiefile
            logger.info("YTDLP cookies file not set, using default: %s", self._ytdlp_cookies_file)
        manager = self._load_cookies_manager()
        if not manager:
            return False
        try:
            logger.info("Refreshing cookies via CookiesManager for %s", self._ytdlp_cookies_file)
            if hasattr(manager, "fetch_and_save_cookies"):
                updated = await manager.fetch_and_save_cookies(self._ytdlp_cookies_file)
            else:
                logger.warning("CookiesManager has no fetch/save method; restart the bot to load new code")
                return False
            if updated:
                try:
                    self._ytdlp_cookies_mtime = os.path.getmtime(self._ytdlp_cookies_file)
                except Exception:
                    pass
            return updated
        except Exception as exc:
            logger.warning("Failed to refresh cookies via CookiesManager: %s", exc)
            return False

    def _is_youtube_auth_error(self, err: str) -> bool:
        """Определяет типичные сообщения yt-dlp об авторизации YouTube и антибот-защите."""
        s = (err or "").lower()
        s = s.replace("’", "'")
        return (
            ("sign in to confirm" in s and "not a bot" in s)
            or ("confirm you're not a bot" in s)
            or ("confirm you are not a bot" in s)
            or ("the page needs to be reloaded" in s)
            or ("page needs to be reloaded" in s)
            or ("cookies-from-browser" in s)
            or ("--cookies-from-browser" in s)
            or ("use --cookies" in s)
            or ("pass cookies" in s and "yt-dlp" in s)
            or ("authentication" in s and "cookies" in s)
        )



    def _is_forbidden_fragment_error(self, err: str) -> bool:
        """Определяет ошибки фрагментов 403 и пустых файлов, типичные для HLS."""
        s = (err or "").lower()
        return (
            ("http error 403" in s and "forbidden" in s)
            or ("the downloaded file is empty" in s)
            or ("got error: http error 403" in s)
        )

    def _apply_referer_headers(self, opts: dict, referer: str | None) -> None:
        """Гарантирует наличие Referer и Origin в http_headers для yt-dlp."""
        if not referer:
            return
        try:
            headers = opts.setdefault('http_headers', {})
            headers.setdefault('Referer', referer)
            try:
                p = urlparse(referer)
                if p.scheme and p.netloc:
                    headers.setdefault('Origin', f"{p.scheme}://{p.netloc}")
            except Exception:
                pass
        except Exception:
            pass

    async def _wait_for_cookiefile_update(self) -> bool:
        """При необходимости ждет внешнее обновление cookie-файла и возвращает True при изменении."""
        wait_s = int(getattr(self.settings, "ytdlp_wait_cookie_update_seconds", 0) or 0)
        if wait_s <= 0 or not self._ytdlp_cookies_file:
            return False
        try:
            if not os.path.exists(self._ytdlp_cookies_file):
                return False
            old = None
            try:
                old = os.path.getmtime(self._ytdlp_cookies_file)
            except Exception:
                old = None

            deadline = time.time() + wait_s
            while time.time() < deadline:
                await asyncio.sleep(1)
                try:
                    if not os.path.exists(self._ytdlp_cookies_file):
                        continue
                    new = os.path.getmtime(self._ytdlp_cookies_file)
                    if old is None or new > old:
                        self._ytdlp_cookies_mtime = new
                        return True
                except Exception:
                    continue
        except Exception:
            return False
        return False

    async def extract_video_info(self, url: str) -> Tuple[Optional[Dict], Optional[List], str]:
        """Извлекает метаданные и доступные форматы."""
        return await self._extract_video_info_once(url, use_proxy=self.use_proxy)

    async def _extract_video_info_once(self, url: str, use_proxy: bool) -> Tuple[Optional[Dict], Optional[List], str]:
        """Извлекает метаданные и форматы с явным выбором прокси."""

        if self._is_initem_embed_url(url):
            logger.info("Embed URL detected, trying mk-player parser first")
            info, formats, status = await self._extract_with_initem_embed(url, use_proxy=use_proxy)
            if status == "success" and info and formats:
                return info, formats, status

                logger.info("Trying yt-dlp extractor")
        info, formats, status = await self._extract_with_ytdlp(url, use_proxy=use_proxy)
        if status == "success" and info and formats:
            return info, formats, status
        if status == "youtube_auth_required" and self._is_youtube_url(url):
            return info, formats, status
        if status == "youtube_auth_required":
            logger.info("Ignoring embedded YouTube auth error on non-YouTube page and continuing fallback")

        logger.info(status)

        enable_browser_fallback = getattr(self.settings, "enable_browser_fallback", True)
        if enable_browser_fallback and self.playwright_available:
            logger.info("Falling back to browser analysis (Playwright)")
            info2, formats2, status2 = await self._extract_with_enhanced_analysis(url, use_proxy=use_proxy)
            if status2 == "success" and info2 and formats2:
                return info2, formats2, status2

        return info, formats, status

    def _is_initem_embed_url(self, url: str) -> bool:
        """Возвращает True, если URL похож на embed-страницу."""
        try:
            if not url:
                return False
            parsed = urlparse(url)
            return '/embed/' in (parsed.path or '').lower()
        except Exception:
            return False

    async def _extract_with_initem_embed(
        self,
        url: str,
        use_proxy: Optional[bool] = None,
    ) -> Tuple[Optional[Dict], Optional[List], str]:
        """Извлекает master.m3u8 и метаданные из embed-страниц initem."""
        try:
            if use_proxy is None:
                use_proxy = self.use_proxy

            proxy = None
            if use_proxy and self.proxy_url:
                proxy_url = self.proxy_url.strip()
                if proxy_url.lower().startswith(('http://', 'https://')):
                    proxy = proxy_url
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ru,en;q=0.9',
                'Connection': 'keep-alive',
                'Referer': url,
            }

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, ssl=False, proxy=proxy) as resp:
                    if resp.status != 200:
                        logger.warning(f"Embed page returned HTTP {resp.status}")
                        return await self._extract_with_ytdlp(url, use_proxy=use_proxy)
                    page_content = await resp.text()

            mk_match = re.search(
                r'<script[^>]*data-name=["\']mk["\'][^>]*>(.*?)</script>',
                page_content,
                flags=re.IGNORECASE | re.DOTALL,
            )
            mk_script = mk_match.group(1) if mk_match else page_content

            m_hls = re.search(r'\bhls\s*:\s*["\']([^"\']+master\.m3u8[^"\']*)["\']', mk_script)
            if not m_hls:
                m_hls = re.search(r'(["\'])(https?://[^"\']+master\.m3u8[^"\']*)\1', mk_script)
            master_url = m_hls.group(1) if m_hls else None
            if m_hls and len(m_hls.groups()) >= 2 and master_url in ('"', "'"):
                master_url = m_hls.group(2)

            if not master_url:
                logger.warning("Could not find master.m3u8 on embed page")
                return await self._extract_with_ytdlp(url, use_proxy=use_proxy)

            title = None
            m_title = re.search(r'\btitle\s*:\s*["\']([^"\']{1,200})["\']', mk_script)
            if m_title:
                title = m_title.group(1).strip()
            if not title:
                m_html_title = re.search(r'<title[^>]*>(.*?)</title>', page_content, flags=re.IGNORECASE | re.DOTALL)
                title = (m_html_title.group(1).strip() if m_html_title else None) or "Фильм"

            thumbnail = None
            m_poster = re.search(r'\bposter\s*:\s*["\']([^"\']+)["\']', mk_script)
            if m_poster:
                thumbnail = m_poster.group(1).strip()

            proxy_url = self.proxy_url if use_proxy and self.proxy_url else None

            try:
                duration_s = int(await self._estimate_master_playlist_duration_seconds(master_url, url, proxy_url) or 0)
            except Exception:
                duration_s = 0

            all_formats = await self._create_formats_from_master_url(
                master_url=master_url,
                webpage_url=url,
                proxy_url=proxy_url,
                title=title,
                page_content=page_content,
                duration_s=duration_s,
            )

            if not all_formats:
                logger.warning("No formats built from embed master playlist, falling back to yt-dlp")
                return await self._extract_with_ytdlp(url, use_proxy=use_proxy)

            info = {
                'title': title,
                'duration': duration_s,
                'uploader': 'Фильм сайт',
                'extractor': 'initem_embed',
                'webpage_url': url,
                'thumbnail': thumbnail,
                'formats': all_formats,
                'is_movie': True,
                'master_playlists': [{'master_url': master_url}],
            }

            processed_formats = self._process_formats_improved(all_formats, url, duration_s)
            return info, processed_formats, "success"

        except Exception as e:
            logger.error(f"Initem embed extraction error: {e}", exc_info=True)
            return await self._extract_with_ytdlp(url, use_proxy=use_proxy)

    async def _extract_with_enhanced_analysis(
        self,
        url: str,
        use_proxy: Optional[bool] = None,
    ) -> Tuple[Optional[Dict], Optional[List], str]:
        """Улучшенный анализ (Playwright) для сайтов типа Lordfilm."""
        if not self.playwright_available:
            logger.warning("Playwright not available, falling back to yt-dlp")
            return await self._extract_with_ytdlp(url, use_proxy=use_proxy)

        try:
            from playwright.async_api import async_playwright

            logger.info(f"Starting enhanced analysis for: {url}")

            if use_proxy is None:
                use_proxy = self.use_proxy

            proxy_url = self.proxy_url if use_proxy and self.proxy_url else None
            if proxy_url:
                logger.info(f"Using proxy: {proxy_url}")
            else:
                logger.info("Using direct connection for Playwright")

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=False,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-web-security',
                    ]
                )

                try:
                    context_options = {
                        'user_agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36',
                        'viewport': {'width': 1920, 'height': 1080},
                        'ignore_https_errors': True,
                    }

                    if proxy_url:
                        context_options['proxy'] = {'server': proxy_url}
                        logger.info("Proxy configured for Playwright")

                    context = await browser.new_context(**context_options)

                    candidate_master_urls: set[str] = set()
                    found_m3u8_urls: set[str] = set()

                    audio_page_contents: List[str] = []

                    async def handle_response(response):
                        """Собираем кандидатные M3U8/Master ссылки."""
                        try:
                            response_url = response.url
                            content_type = response.headers.get('content-type', '').lower()

                            if (
                                content_type.startswith(('audio/', 'video/', 'image/'))
                                or 'application/octet-stream' in content_type
                            ):
                                return
                            if 'googlevideo.com/videoplayback' in response_url.lower():
                                return

                            if self._is_known_embedded_video_platform_url(response_url, url):
                                logger.debug(f"Ignoring embedded third-party video URL: {response_url}")
                                return

                            if self._is_m3u8_url(response_url):
                                logger.info(f"Found direct M3U8: {response_url}")
                                if self._is_valid_m3u8_url(response_url):
                                    found_m3u8_urls.add(response_url)
                                    if await self._is_master_playlist(response_url, url, proxy_url):
                                        candidate_master_urls.add(response_url)
                                        if self._looks_like_trailer_url(response_url):
                                            logger.info(f"Candidate master playlist (looks like trailer/preview): {response_url}")
                                        else:
                                            logger.info(f"Candidate master playlist: {response_url}")
                                return

                            if any(keyword in response_url.lower() for keyword in [
                                'api', 'embed', 'movie', 'video', 'stream', 'player'
                            ]):
                                if response.status != 200:
                                    return

                                if 'text/html' in content_type and '/embed/' in response_url.lower():
                                    try:
                                        body = await response.body()
                                        html = body.decode('utf-8', errors='ignore')
                                        if html and ('data-name="mk"' in html or 'makePlayer(' in html):
                                            audio_page_contents.append(html)
                                            logger.info(f"Captured mk-player embed HTML ({len(html)} chars): {response_url}")
                                    except Exception:
                                        pass

                                try:
                                    m3u8_urls: List[str] = []

                                    if 'application/json' in content_type:
                                        try:
                                            json_data = await response.json()
                                        except Exception:
                                            body = await response.body()
                                            text = body.decode('utf-8', errors='ignore')
                                            import json as _json

                                            json_data = _json.loads(text) if text else {}
                                        m3u8_urls = self._extract_m3u8_from_json_deep(json_data, response_url)

                                    elif 'javascript' in content_type or 'application/javascript' in content_type:
                                        body = await response.body()
                                        text = body.decode('utf-8', errors='ignore')
                                        m3u8_urls = self._extract_m3u8_from_javascript(text, response_url)

                                    else:
                                        body = await response.body()
                                        text = body.decode('utf-8', errors='ignore')
                                        m3u8_urls = self._extract_m3u8_from_text(text, response_url)

                                    for m3u8_url in m3u8_urls or []:
                                        if self._is_known_embedded_video_platform_url(m3u8_url, url):
                                            continue
                                        if not self._is_valid_m3u8_url(m3u8_url):
                                            continue

                                        found_m3u8_urls.add(m3u8_url)
                                        logger.info(f"Found M3U8: {m3u8_url}")

                                        if await self._is_master_playlist(m3u8_url, url, proxy_url):
                                            candidate_master_urls.add(m3u8_url)
                                            if self._looks_like_trailer_url(m3u8_url):
                                                logger.info(f"Candidate master playlist (looks like trailer/preview): {m3u8_url}")
                                            else:
                                                logger.info(f"Candidate master playlist: {m3u8_url}")

                                except Exception as e:
                                    logger.warning(f"Could not parse response from {response_url}: {e}")

                        except Exception as e:
                            logger.error(f"Error in response handler: {e}")

                    context.on("response", handle_response)

                    page = await context.new_page()
                    await page.set_extra_http_headers({
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'ru,en;q=0.9',
                    })

                    logger.info("Loading page...")

                    try:
                        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                        logger.info("DOM loaded, starting interaction")
                    except Exception as e:
                        logger.warning(f"Page load issue: {e}")
                        try:
                            await page.goto(url, wait_until='commit', timeout=15000)
                        except Exception:
                            pass

                            logger.info("Starting immediate scrolling...")
                    await self._active_immediate_scroll(page)

                    logger.info("Waiting for initial content load...")
                    await asyncio.sleep(8)

                    await self._quick_activate_video(page)

                    max_wait_time = 35
                    start_time = time.time()

                    while (time.time() - start_time) < max_wait_time:
                        await asyncio.sleep(1)

                        if found_m3u8_urls:
                            for m3u8_url in list(found_m3u8_urls):
                                if self._is_known_embedded_video_platform_url(m3u8_url, url):
                                    continue
                                if m3u8_url in candidate_master_urls:
                                    continue
                                if await self._is_master_playlist(m3u8_url, url, proxy_url):
                                    candidate_master_urls.add(m3u8_url)
                                    if self._looks_like_trailer_url(m3u8_url):
                                        logger.info(f"Candidate master playlist (looks like trailer/preview): {m3u8_url}")
                                    else:
                                        logger.info(f"Candidate master playlist: {m3u8_url}")

                    page_content = await page.content()
                    page_content_for_audio = page_content
                    if audio_page_contents:
                        page_content_for_audio = max(audio_page_contents, key=lambda x: len(x or ''))
                        logger.info(f"Using embed HTML for audio names: {len(page_content_for_audio)} chars (instead of {len(page_content)} chars)")
                    else:
                        logger.info(f"Получено содержимое страницы: {len(page_content)} символов")

                    best_master_url = await self._pick_best_master_playlist(
                        candidate_urls=list(candidate_master_urls),
                        referer=url,
                        proxy_url=proxy_url,
                    )

                    if best_master_url:
                        logger.info(f"Using best master playlist: {best_master_url}")

                        try:
                            duration_s = int(
                                await self._estimate_master_playlist_duration_seconds(best_master_url, url, proxy_url) or 0
                            )
                        except Exception:
                            duration_s = 0

                        title = await page.title() or "Фильм"
                        thumbnail = await self._extract_thumbnail_safe(page, url)

                        await page.close()
                        await context.close()

                        all_formats = await self._create_formats_from_master_url(
                            best_master_url,
                            url,
                            proxy_url,
                            title,
                            page_content_for_audio,
                            duration_s=duration_s,
                        )

                        if all_formats:
                            info = {
                                'title': title,
                                'duration': duration_s,
                                'uploader': 'Фильм сайт',
                                'extractor': 'enhanced_analysis',
                                'webpage_url': url,
                                'thumbnail': thumbnail,
                                'formats': all_formats,
                                'is_movie': True,
                                'master_playlists': [{'master_url': best_master_url}]
                            }

                            processed_formats = self._process_formats_improved(all_formats, url, duration_s)
                            logger.info(f"Successfully found {len(processed_formats)} formats from master playlist")
                            return info, processed_formats, "success"

                            logger.warning("No valid master playlist found within time limit")
                    await page.close()
                    await context.close()
                    return await self._extract_with_ytdlp(url, use_proxy=use_proxy)

                finally:
                    await browser.close()

        except Exception as e:
            logger.error(f"Enhanced analysis error: {str(e)}")
            return await self._extract_with_ytdlp(url, use_proxy=use_proxy)


    def _is_youtube_url(self, url: str) -> bool:
        """Проверяет, является ли ссылка YouTube ссылкой"""
        if not url:
            return False

        youtube_domains = [
            'youtube.com', 'youtu.be', 'www.youtube.com',
            'm.youtube.com', 'youtube-nocookie.com'
        ]

        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            for yt_domain in youtube_domains:
                if yt_domain in domain:
                    return True

            if 'youtu.be' in domain:
                return True

            return False
        except Exception:
            return False

    def _get_url_host(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or '').lower()
            if '@' in host:
                host = host.split('@', 1)[1]
            if ':' in host:
                host = host.split(':', 1)[0]
            if host.startswith('www.'):
                host = host[4:]
            return host
        except Exception:
            return ''

    def _is_known_embedded_video_platform_url(self, url: str, page_url: Optional[str] = None) -> bool:
        """Возвращает True для сторонних видео-платформ, встроенных в страницу."""
        host = self._get_url_host(url)
        if not host:
            return False

        platform_domains = (
            'youtube.com',
            'youtu.be',
            'youtube-nocookie.com',
            'rutube.ru',
            'vimeo.com',
            'dailymotion.com',
            'ok.ru',
            'twitch.tv',
            'twitchcdn.net',
        )
        matched_domain = next(
            (
                domain for domain in platform_domains
                if host == domain or host.endswith(f'.{domain}')
            ),
            None,
        )
        if not matched_domain:
            return False

        page_host = self._get_url_host(page_url or '')
        if page_host and (page_host == matched_domain or page_host.endswith(f'.{matched_domain}')):
            return False

        return True

    def _looks_like_trailer_url(self, url: str) -> bool:
        """Грубая эвристика: по URL понять, что это трейлер/превью/реклама."""
        if not url:
            return False
        s = url.lower()
        trailer_keywords = [
            'trailer', 'teaser', 'promo', 'preview', 'sample',
            'тизер', 'трейлер', 'превью', 'реклама', 'advert', 'ads',
        ]
        return any(k in s for k in trailer_keywords)

    async def _estimate_master_playlist_duration_seconds(self, master_url: str, referer: str, proxy_url: str) -> float:
        """Оценивает длительность (в секундах) по одному из variant-playlist."""
        try:
            async with HLSVideoDownloader(proxy_url=proxy_url) as downloader:
                playlist_info = await downloader.get_master_playlist_info(master_url, referer)
                if not isinstance(playlist_info, dict):
                    return 0.0

                qualities = playlist_info.get('qualities') or []
                if not qualities:
                    return 0.0

                def q_key(q: Dict) -> tuple:
                    h = q.get('height') or 0
                    bw = q.get('bandwidth')
                    try:
                        bw_i = int(bw)
                    except Exception:
                        bw_i = 0
                    return (int(h), bw_i)

                best_quality = sorted(
                    [q for q in qualities if isinstance(q, dict)],
                    key=q_key,
                    reverse=True
                )[0]

                master_host = ''
                try:
                    master_host = urlparse(master_url).netloc or ''
                except Exception:
                    master_host = ''

                def _candidate_urls(primary_url: str, alternates: List[str]) -> List[str]:
                    urls: List[str] = []
                    for candidate in [primary_url, *(alternates or [])]:
                        url_s = (candidate or '').strip()
                        if not url_s:
                            continue
                        if url_s not in urls:
                            urls.append(url_s)
                        rewritten = downloader._rewrite_url_host(url_s, master_host)
                        if rewritten and rewritten not in urls:
                            urls.append(rewritten)
                    return urls

                variant_content = None
                for variant_url in _candidate_urls(
                    best_quality.get('url') or master_url,
                    best_quality.get('alternate_urls') or [],
                ):
                    variant_content = await downloader.download_playlist(variant_url, referer)
                    if variant_content:
                        break
                if not variant_content:
                    return 0.0

                pl = m3u8.loads(variant_content)

                dur = 0.0
                for seg in getattr(pl, 'segments', []) or []:
                    try:
                        dur += float(getattr(seg, 'duration', 0) or 0)
                    except Exception:
                        continue

                if dur <= 0:
                    try:
                        segs = getattr(pl, 'segments', None)
                        if segs is not None:
                            dur = float(len(segs))
                    except Exception:
                        dur = 0.0

                return dur

        except Exception as e:
            logger.warning(f"Could not estimate duration for {master_url}: {e}")
            return 0.0

    async def _pick_best_master_playlist(self, candidate_urls: List[str], referer: str, proxy_url: str) -> Optional[str]:
        """Выбирает лучший master-playlist среди кандидатов."""
        if not candidate_urls:
            return None

        seen = set()
        unique: List[str] = []
        for u in candidate_urls:
            if not u or u in seen or not is_public_http_url(u):
                continue
            seen.add(u)
            unique.append(u)

        unique = unique[:10]

        scored: List[Tuple[float, str]] = []
        for u in unique:
            dur = await self._estimate_master_playlist_duration_seconds(u, referer, proxy_url)

            penalty = 0.0
            if self._looks_like_trailer_url(u):
                penalty += 600.0
            if self._is_known_embedded_video_platform_url(u, referer):
                penalty += 7200.0

            bonus = 1.0 if (dur == 0 and not self._looks_like_trailer_url(u)) else 0.0

            score = (dur + bonus) - penalty
            logger.info(f"Candidate score: url={u} dur≈{dur:.1f}s score={score:.1f}")
            scored.append((score, u))

        scored.sort(key=lambda t: t[0], reverse=True)
        return scored[0][1] if scored else unique[0]


    async def _is_master_playlist(self, m3u8_url: str, referer: str, proxy_url: str) -> bool:
        """Проверяет, является ли URL рабочим мастер-плейлистом"""
        try:
            async with HLSVideoDownloader(proxy_url=proxy_url) as downloader:
                playlist_info = await downloader.get_master_playlist_info(m3u8_url, referer)

                if (isinstance(playlist_info, dict) and
                        playlist_info.get('qualities') and
                        len(playlist_info['qualities']) > 0):
                    logger.info(f"Valid master playlist with {len(playlist_info['qualities'])} qualities")
                    return True

        except Exception as e:
            logger.warning(f"Not a valid master playlist {m3u8_url}: {e}")

        return False

    def _format_duration(self, seconds: int) -> str:
        """Форматирует длительность в читаемый вид"""
        if not seconds or seconds <= 0:
            return "Неизвестно"

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    def _is_m3u8_url(self, url: str) -> bool:
        """Проверяет, является ли URL M3U8 ссылкой"""
        if not url:
            return False
        try:
            parsed = urlparse(url)
            target = ((parsed.path or url) + (f"?{parsed.query}" if parsed.query else "")).lower()
        except Exception:
            target = url.lower()
        return '.m3u8' in target and not target.endswith('.mp4')

    async def _create_formats_from_master_url(
        self,
        master_url: str,
        webpage_url: str,
        proxy_url: str,
        title: str,
        page_content: str = None,
        duration_s: int = 0,
    ) -> List[Dict]:
        """Создает форматы из мастер-плейлиста с учетом содержимого страницы"""
        all_formats = []

        try:
            async with HLSVideoDownloader(proxy_url=proxy_url) as downloader:
                playlist_info = await downloader.get_master_playlist_info(master_url, webpage_url, page_content)

                if not isinstance(playlist_info, dict):
                    return all_formats

                qualities = playlist_info.get('qualities', [])
                audio_tracks = playlist_info.get('audio_tracks', [])

                if not qualities:
                    return all_formats

                for quality in qualities:
                    if not isinstance(quality, dict):
                        continue

                    height = 0
                    width = 0
                    try:
                        height = int(quality.get('height') or 0)
                    except Exception:
                        height = 0
                    try:
                        width = int(quality.get('width') or 0)
                    except Exception:
                        width = 0

                    resolution = quality.get('resolution', 'Unknown')
                    if height <= 0:
                        if isinstance(resolution, (tuple, list)) and len(resolution) == 2:
                            try:
                                width = int(resolution[0] or 0)
                                height = int(resolution[1] or 0)
                            except Exception:
                                pass
                        elif isinstance(resolution, str):
                            mm = re.search(r'(\d+)\s*[xX]\s*(\d+)', resolution)
                            if mm:
                                try:
                                    width = int(mm.group(1))
                                    height = int(mm.group(2))
                                except Exception:
                                    pass

                    if width > 0:
                        quality_label = f"{width}"
                    elif height > 0:
                        quality_label = f"{height}p"
                    else:
                        quality_label = "HLS"

                    q_index = quality.get('index')
                    try:
                        q_index_i = int(q_index) if q_index is not None else len(all_formats)
                    except Exception:
                        q_index_i = len(all_formats)

                    bandwidth = quality.get('bandwidth')
                    try:
                        bandwidth_i = int(bandwidth) if bandwidth is not None else 0
                    except Exception:
                        bandwidth_i = 0

                    filesize_raw = None
                    if duration_s > 0 and bandwidth_i > 0:
                        filesize_raw = int((bandwidth_i * duration_s) / 8)

                    format_info = {
                        'format_id': f"hls_{height or 'unk'}p_{q_index_i}",
                        'url': master_url,
                        'ext': 'mp4',
                        'vcodec': 'h264',
                        'acodec': 'aac',
                        'width': width,
                        'height': height,
                        'filesize': self._format_filesize(filesize_raw),
                        'filesize_raw': filesize_raw,
                        'quality': quality_label,
                        'is_m3u8': True,
                        'master_url': master_url,
                        'quality_info': quality,
                        'audio_tracks': audio_tracks,
                        'webpage_url': webpage_url
                    }
                    all_formats.append(format_info)

                    logger.info(f"Created {len(all_formats)} formats from master playlist")

        except Exception as e:
            logger.error(f"Error creating formats from master URL: {e}")

        return all_formats

    async def _extract_with_ytdlp(
        self,
        url: str,
        use_proxy: Optional[bool] = None,
    ) -> Tuple[Optional[Dict], Optional[List], str]:
        """Извлекает информацию с помощью yt-dlp"""
        retry_on_auth = bool(getattr(self.settings, "ytdlp_retry_on_auth_error", True))
        is_direct_youtube = self._is_youtube_url(url)
        log_lines: list[str] = []
        if use_proxy is None:
            use_proxy = self.use_proxy

        class _YtdlpLogCapture:
            def debug(self, msg):
                pass

            def info(self, msg):
                pass

            def warning(self, msg):
                if msg:
                    log_lines.append(str(msg))

            def error(self, msg):
                if msg:
                    log_lines.append(str(msg))

        for attempt in range(2 if retry_on_auth else 1):
            try:
                log_lines.clear()
                await self._ensure_cookiefile(url)
                ydl_opts = self._get_ydl_opts(download=False, use_proxy=use_proxy)
                ydl_opts["logger"] = _YtdlpLogCapture()
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await asyncio.to_thread(ydl.extract_info, url, download=False)

                if not info:
                    if any(self._is_youtube_auth_error(line) for line in log_lines):
                        if use_proxy and self.proxy_url:
                            logger.info("YouTube auth error via proxy in yt-dlp, retrying without proxy")
                            return await self._extract_with_ytdlp(url, use_proxy=False)
                        if is_direct_youtube and attempt == 0 and retry_on_auth:
                            refreshed = await self._refresh_cookiefile(url)
                            if not refreshed:
                                await self._wait_for_cookiefile_update()
                            continue
                        return None, None, "youtube_auth_required"
                    return None, None, "video_info_error"

                formats = info.get('formats', [])
                if not isinstance(formats, list):
                    formats = []

                processed_formats = self._process_formats_ytdlp(formats, url, info.get('duration', 0))

                if not processed_formats:
                    if any(self._is_youtube_auth_error(line) for line in log_lines):
                        if use_proxy and self.proxy_url:
                            logger.info("YouTube auth error via proxy in yt-dlp, retrying without proxy")
                            return await self._extract_with_ytdlp(url, use_proxy=False)
                        if is_direct_youtube and attempt == 0 and retry_on_auth:
                            refreshed = await self._refresh_cookiefile(url)
                            if not refreshed:
                                await self._wait_for_cookiefile_update()
                            continue
                        return None, None, "youtube_auth_required"
                    if use_proxy and self.proxy_url:
                        logger.info("No formats via proxy in yt-dlp, retrying without proxy")
                        info2, formats2, status2 = await self._extract_with_ytdlp(url, use_proxy=False)
                        if formats2:
                            return info2, formats2, status2
                    return None, None, "no_formats_found"

                if use_proxy and self.proxy_url:
                    has_video = any(not fmt.get("is_audio") for fmt in processed_formats)
                    if not has_video:
                        logger.info("Only audio formats via proxy in yt-dlp, retrying without proxy")
                        info2, formats2, status2 = await self._extract_with_ytdlp(url, use_proxy=False)
                        if formats2:
                            return info2, formats2, status2
                        if status2 == "youtube_auth_required":
                            return info2, formats2, status2

                return info, processed_formats, "success"

            except Exception as e:
                err = str(e)
                logger.error(f"yt-dlp extraction error: {err}")

                if self._is_youtube_auth_error(err):
                    if use_proxy and self.proxy_url:
                        logger.info("YouTube auth error via proxy in yt-dlp, retrying without proxy")
                        return await self._extract_with_ytdlp(url, use_proxy=False)
                    if is_direct_youtube and attempt == 0 and retry_on_auth:
                        refreshed = await self._refresh_cookiefile(url)
                        if not refreshed:
                            await self._wait_for_cookiefile_update()
                        continue
                    return None, None, "youtube_auth_required"

                return None, None, f"ytdlp_error: {err}"

        return None, None, "video_info_error"

    def _process_formats_ytdlp(self, formats: List[Dict], url: str, duration: int) -> List[Dict]:
        """Преобразует сырые форматы yt-dlp в список, удобный для интерфейса."""
        muxed_formats: List[Dict] = []
        direct_video_only_formats: List[Dict] = []
        hls_video_only_formats: List[Dict] = []
        audio_formats: List[Dict] = []

        logger.info("Processing %s total formats", len(formats))

        for raw_format in formats:
            format_info = self._build_ytdlp_format_info(raw_format, url, duration)
            if not format_info:
                continue

            if format_info.get("is_audio"):
                audio_formats.append(format_info)
            elif format_info.get("acodec") != "none":
                muxed_formats.append(format_info)
            elif format_info.get("is_m3u8"):
                hls_video_only_formats.append(format_info)
            else:
                direct_video_only_formats.append(format_info)

                logger.info("Found %s muxed video formats, %s direct video-only, %s HLS video-only, and %s audio formats",
            len(muxed_formats),
            len(direct_video_only_formats),
            len(hls_video_only_formats),
            len(audio_formats),
        )

        best_hls_audio_track = self._pick_best_external_audio_track(audio_formats)
        if best_hls_audio_track:
            for fmt in hls_video_only_formats:
                fmt["audio_tracks"] = [dict(best_hls_audio_track)]
                fmt["quality"] = self._cleanup_video_only_quality_label(fmt)

        for fmt in direct_video_only_formats:
            default_audio_track = self._pick_best_external_audio_track(audio_formats, fmt)
            if not default_audio_track:
                continue
            fmt["default_audio_track"] = default_audio_track
            fmt["quality"] = self._cleanup_video_only_quality_label(fmt)

        muxed_formats.sort(key=lambda item: item.get("quality_score", 0), reverse=True)
        direct_video_only_formats.sort(key=lambda item: item.get("quality_score", 0), reverse=True)
        hls_video_only_formats.sort(key=lambda item: item.get("quality_score", 0), reverse=True)

        processed_formats = muxed_formats + direct_video_only_formats + hls_video_only_formats

        if audio_formats:
            best_audio = max(audio_formats, key=self._get_audio_quality_score)
            audio_entry = dict(best_audio)
            audio_entry["quality"] = "🎵 Аудио"
            processed_formats.append(audio_entry)

            logger.info("Final processed formats: %s", len(processed_formats))
        return processed_formats

    def _build_ytdlp_format_info(self, fmt: Dict, webpage_url: str, duration: int) -> Optional[Dict]:
        if not isinstance(fmt, dict):
            logger.warning("Skipping invalid yt-dlp format: %r", fmt)
            return None

        format_id = fmt.get("format_id", "")
        vcodec = fmt.get("vcodec", "none")
        acodec = fmt.get("acodec", "none")
        if vcodec == "none" and acodec == "none":
            return None

        protocol = str(fmt.get("protocol", "") or "").lower()
        manifest_url = str(fmt.get("manifest_url") or "").strip()
        direct_url = str(fmt.get("url") or "").strip()
        hls_url = manifest_url or direct_url
        is_hls = bool(hls_url and ".m3u8" in hls_url.lower()) or "m3u8" in protocol
        hls_variant = direct_url if direct_url and ".m3u8" in direct_url.lower() else ""
        if manifest_url and ".m3u8" in manifest_url.lower():
            hls_master = manifest_url
        else:
            hls_master = hls_variant

        filesize = fmt.get("filesize") or fmt.get("filesize_approx")
        if not filesize and duration > 0:
            tbr = fmt.get("tbr", 0) or 0
            abr = fmt.get("abr", 0) or 0
            bitrate = tbr or abr
            if bitrate:
                filesize = (bitrate * 1000 * duration) / 8

        format_info = {
            "format_id": format_id,
            "quality": self._format_quality(fmt),
            "filesize": self._format_filesize(filesize),
            "ext": fmt.get("ext", "mp4"),
            "url": hls_master if (is_hls and vcodec != "none" and hls_master) else (direct_url or webpage_url),
            "direct_url": direct_url,
            "vcodec": vcodec,
            "acodec": acodec,
            "filesize_raw": filesize,
            "width": fmt.get("width", 0) or 0,
            "height": fmt.get("height", 0) or 0,
            "fps": fmt.get("fps", 0) or 0,
            "quality_score": self._get_quality_score(fmt),
            "webpage_url": webpage_url,
            "abr": fmt.get("abr") or 0,
            "tbr": fmt.get("tbr") or 0,
            "protocol": protocol,
        }

        if vcodec == "none" and acodec != "none":
            format_info["is_audio"] = True

        if is_hls and vcodec != "none" and hls_master:
            format_info["is_m3u8"] = True
            format_info["master_url"] = hls_master
            if hls_variant:
                format_info["quality_info"] = {
                    "url": hls_variant,
                    "resolution": format_info["quality"],
                }

        return format_info

    def _pick_best_external_audio_track(self, audio_formats: List[Dict], video_format: Optional[Dict] = None) -> Optional[Dict]:
        candidates = [
            fmt for fmt in audio_formats
            if isinstance(fmt, dict) and fmt.get("format_id")
        ]
        if not candidates:
            return None

        video_ext = ((video_format or {}).get("ext") or "").lower()

        def candidate_score(audio_fmt: Dict) -> int:
            score = self._get_audio_quality_score(audio_fmt)
            audio_ext = (audio_fmt.get("ext") or "").lower()
            if video_ext == "mp4" and audio_ext in {"m4a", "mp4"}:
                score += 1000
            elif video_ext == "webm" and audio_ext == "webm":
                score += 1000
            return score

        best_audio = max(candidates, key=candidate_score)
        return {
            "name": "Аудио",
            "format_id": best_audio.get("format_id"),
            "ext": best_audio.get("ext"),
            "abr": best_audio.get("abr") or 0,
            "tbr": best_audio.get("tbr") or 0,
            "url": best_audio.get("url"),
            "webpage_url": best_audio.get("webpage_url"),
            "download_via_ytdlp": True,
            "quality_score": self._get_audio_quality_score(best_audio),
        }

    def _cleanup_video_only_quality_label(self, fmt: Dict) -> str:
        quality = (fmt.get("quality") or "").strip()
        cleaned = re.sub(r"\b(со\s+звуком|без\s+звука)\b", "", quality, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        if cleaned:
            return cleaned

        height = int(fmt.get("height") or 0)
        width = int(fmt.get("width") or 0)
        if height > 0:
            return f"{height}p"
        if width > 0:
            return str(width)
        return "Видео"

    def _format_quality(self, fmt: Dict) -> str:
        """Форматируем информацию о качестве"""
        quality_parts = []

        height = fmt.get('height')
        if height:
            quality_parts.append(f"{height}p")
        elif fmt.get('width') and fmt.get('height'):
            quality_parts.append(f"{fmt['width']}x{fmt['height']}")

        vcodec = fmt.get('vcodec', 'none')
        acodec = fmt.get('acodec', 'none')

        if acodec == 'none' and vcodec != 'none':
            quality_parts.append('без звука')

        return ' '.join(quality_parts) if quality_parts else 'авто'

    def _format_filesize(self, size_bytes: int) -> str:
        """Форматируем размер файла"""
        if not size_bytes:
            return "~"

        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def _get_quality_score(self, fmt: Dict) -> int:
        """Оценка качества для сортировки"""
        score = 0

        height = fmt.get('height', 0) or 0
        if height >= 2160:
            score += 4000
        elif height >= 1440:
            score += 2000
        elif height >= 1080:
            score += 1000
        elif height >= 720:
            score += 500
        elif height >= 480:
            score += 250
        elif height >= 360:
            score += 100
        else:
            score += 50

        fps = fmt.get('fps', 0) or 0
        if fps >= 60:
            score += 200
        elif fps >= 50:
            score += 150
        elif fps >= 30:
            score += 100
        elif fps > 0:
            score += 50

        acodec = fmt.get('acodec', 'none')
        if acodec == 'none':
            score -= 500

        return score

    def _get_audio_quality_score(self, fmt: Dict) -> int:
        """Оценка качества аудио"""
        if fmt.get('abr'):
            return int(fmt['abr'])
        elif fmt.get('tbr'):
            return int(fmt['tbr'])
        elif fmt.get('filesize_raw'):
            return fmt['filesize_raw'] // 1000
        return 0
