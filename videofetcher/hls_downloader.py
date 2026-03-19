import asyncio
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import aiohttp
import m3u8

from .url_safety import is_public_http_url

logger = logging.getLogger(__name__)

class HLSVideoDownloader:
    """Класс для скачивания HLS видео с правильными заголовками"""

    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url
        self.session = None
        self._use_proxy_param = False
        self.downloaded_segments = 0
        self.total_segments = 0
        self.downloaded_video_segments = 0
        self.downloaded_audio_segments = 0
        self.total_video_segments = 0
        self.total_audio_segments = 0
        try:
            self.segment_concurrency = max(
                1, int(os.getenv("VIDEOFETCHER_HLS_SEGMENT_CONCURRENCY", "1") or 1)
            )
        except Exception:
            self.segment_concurrency = 1
        if self.segment_concurrency > 10:
            self.segment_concurrency = 10
        try:
            self.segment_timeout_s = max(
                5, int(os.getenv("VIDEOFETCHER_HLS_SEGMENT_TIMEOUT", "30") or 30)
            )
        except Exception:
            self.segment_timeout_s = 30
        self._curl_logged_segments = set()
        self._byterange_logged = False

    def _build_curl_command(
        self,
        url: str,
        referer: str | None,
        headers: Dict[str, str],
        byte_range: str | None = None,
    ) -> str:
        """Build a curl command for manual segment testing."""
        parts = ["curl", "-v", f"--max-time", str(self.segment_timeout_s)]

        proxy = (self.proxy_url or '').strip()
        if proxy:
            pl = proxy.lower()
            if pl.startswith(("http://", "https://")):
                parts += ["-x", proxy]
            elif pl.startswith(("socks5://", "socks5h://")):
                parts += ["--socks5-hostname", proxy]
            elif pl.startswith(("socks4://", "socks4a://")):
                parts += ["--socks4", proxy]

        ua = headers.get("User-Agent")
        if ua:
            parts += ["-H", f"User-Agent: {ua}"]
        if referer:
            parts += ["-H", f"Referer: {referer}"]
            try:
                p = urlparse(referer)
                if p.scheme and p.netloc:
                    parts += ["-H", f"Origin: {p.scheme}://{p.netloc}"]
            except Exception:
                pass
        accept = headers.get("Accept")
        if accept:
            parts += ["-H", f"Accept: {accept}"]
        lang = headers.get("Accept-Language")
        if lang:
            parts += ["-H", f"Accept-Language: {lang}"]
        if byte_range:
            parts += ["-H", f"Range: {byte_range}"]

        parts.append(url)
        return " ".join(f"'{p}'" if " " in p else p for p in parts)

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=600, connect=20, sock_connect=20, sock_read=120)

        proxy = (self.proxy_url or '').strip() if self.proxy_url else ''

        if proxy.lower().startswith(('socks5://', 'socks4://', 'socks5h://', 'socks4a://')):
            try:
                from aiohttp_socks import ProxyConnector  # type: ignore

                connector = ProxyConnector.from_url(proxy, rdns=True, ssl=False, limit=10)
                self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
                self._use_proxy_param = False
                logger.info(f"Using SOCKS proxy for aiohttp (rdns): {proxy}")
                return self
            except Exception as e:
                logger.warning(f"SOCKS proxy configured ({proxy}), but aiohttp-socks is not available/failed ({e}). "
                    "Proceeding without SOCKS support. "
                    "Tip: try `pip install -U aiohttp-socks setuptools`."
                )

        connector = aiohttp.TCPConnector(ssl=False, limit=10)
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        self._use_proxy_param = bool(proxy and proxy.lower().startswith(('http://', 'https://')))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def generate_headers(self, url: str, referer: str = None) -> Dict[str, str]:
        """Универсальные заголовки для всех запросов."""

        def _origin(maybe_url: str) -> str:
            try:
                if not maybe_url:
                    return ""
                p = urlparse(maybe_url)
                if p.scheme and p.netloc:
                    return f"{p.scheme}://{p.netloc}"
            except Exception:
                return ""
            return ""

        origin = _origin(referer) or _origin(url)
        ref = referer or origin or url

        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Encoding': 'identity',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Pragma': 'no-cache',
            'Referer': ref,
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'DNT': '1',
            'Origin': origin or 'https://www.google.com',
        }

        if '.ts' in url or url.endswith('.ts'):
            headers['Accept'] = 'video/mp2t,video/MP2T,audio/mp2t,audio/MP2T,*/*'
        elif '.m3u8' in url:
            headers['Accept'] = 'application/vnd.apple.mpegurl,application/x-mpegurl,*/*'
            headers['Accept-Encoding'] = 'gzip, deflate, br'

        return headers

    def _rewrite_url_host(self, url: str, new_host: str) -> Optional[str]:
        try:
            if not url or not new_host:
                return None
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return None
            return parsed._replace(netloc=new_host).geturl()
        except Exception:
            return None

    async def download_segment(
        self,
        url: str,
        segment_type: str,
        segment_index: int,
        referer: str = None,
        max_retries: int = 3,
        byte_range: str | None = None,
    ) -> Optional[bytes]:
        """Скачиваем один сегмент (видео или аудио)"""
        if not is_public_http_url(url):
            logger.warning(f"Blocked unsafe segment URL: {url}")
            return None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    wait_time = random.uniform(1.0, 3.0)
                    await asyncio.sleep(wait_time)

                headers = self.generate_headers(url, referer)
                if byte_range:
                    headers["Range"] = byte_range

                range_note = f" (range {byte_range})" if byte_range else ""
                logger.info(f"Скачиваем {segment_type} сегмент {segment_index + 1}{range_note}...")

                async with asyncio.timeout(self.segment_timeout_s):
                    async with self.session.get(
                            url,
                            headers=headers,
                            ssl=False,
                            proxy=(self.proxy_url if self._use_proxy_param else None)
                    ) as response:

                        if response.status in [200, 206]:
                            content = await response.read()

                            if len(content) > 0:
                                if segment_type == "video":
                                    self.downloaded_video_segments += 1
                                    total = self.total_video_segments
                                    current = self.downloaded_video_segments
                                else:
                                    self.downloaded_audio_segments += 1
                                    total = self.total_audio_segments
                                    current = self.downloaded_audio_segments

                                progress = (current / total) * 100 if total > 0 else 0
                                logger.info(f"{segment_type} сегмент {segment_index + 1} загружен ({len(content)} байт) - Прогресс: {progress:.1f}%")
                                return content
                            else:
                                logger.warning(f"{segment_type} сегмент {segment_index + 1} пустой, пробуем снова...")
                                continue

                        elif response.status == 412:
                            logger.warning(f"412 для {segment_type} сегмента {segment_index + 1}, попытка {attempt + 1}")
                            continue

                        elif response.status == 410:
                            logger.error(f"410 Gone для {segment_type} сегмента {segment_index + 1}")
                            return None

                        else:
                            logger.warning(f"HTTP {response.status} для {segment_type} сегмента {segment_index + 1}")
                            continue

            except asyncio.TimeoutError:
                logger.warning(f"⏰ Таймаут для {segment_type} сегмента {segment_index + 1}, попытка {attempt + 1}")
                if segment_index not in self._curl_logged_segments:
                    self._curl_logged_segments.add(segment_index)
                    try:
                        curl_cmd = self._build_curl_command(url, referer, headers, byte_range=byte_range)
                        logger.info(f"curl для проверки сегмента: {curl_cmd}")
                    except Exception as e:
                        logger.warning(f"Не удалось сформировать curl команду: {e}")
            except Exception as e:
                logger.error(f"Ошибка для {segment_type} сегмента {segment_index + 1}: {e}")

                logger.error(f"Не удалось скачать {segment_type} сегмент {segment_index + 1} после {max_retries} попыток")
        return None

    async def _download_bytes(
        self,
        url: str,
        referer: str = None,
        byte_range: str | None = None,
        max_retries: int = 3,
    ) -> Optional[bytes]:
        """Скачивает данные без побочных эффектов (для init-сегментов и служебных запросов)."""
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    await asyncio.sleep(random.uniform(0.5, 1.5))

                headers = self.generate_headers(url, referer)
                if byte_range:
                    headers["Range"] = byte_range

                async with asyncio.timeout(self.segment_timeout_s):
                    async with self.session.get(
                            url,
                            headers=headers,
                            ssl=False,
                            proxy=(self.proxy_url if self._use_proxy_param else None)
                    ) as response:
                        if response.status in [200, 206]:
                            content = await response.read()
                            if content:
                                return content
                            continue
                        if response.status == 412:
                            continue
                        if response.status == 410:
                            return None
                        continue
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue
        return None

    async def _download_segments_to_file(
        self,
        playlist_obj: m3u8.M3U8,
        playlist_url: str,
        output_path: str,
        segment_type: str,
        referer: str = None,
        progress_callback=None,
        progress_base: float | None = None,
        progress_span: float | None = None,
    ) -> bool:
        """Скачивает сегменты плейлиста (с ограниченной параллельностью) и пишет в файл по порядку."""
        if not playlist_obj or not getattr(playlist_obj, "segments", None):
            return False

        segments = list(playlist_obj.segments)
        total = len(segments)
        if total <= 0:
            return False

        def _parse_byterange(br):
            if not br:
                return None, None
            if isinstance(br, str):
                m = re.match(r"(\d+)(?:@(\d+))?", br.strip())
                if m:
                    length = int(m.group(1))
                    offset = int(m.group(2)) if m.group(2) is not None else None
                    return length, offset
            if isinstance(br, (tuple, list)):
                length = None
                offset = None
                if len(br) >= 1:
                    try:
                        length = int(br[0])
                    except Exception:
                        length = None
                if len(br) >= 2:
                    try:
                        offset = int(br[1])
                    except Exception:
                        offset = None
                return length, offset
            if isinstance(br, dict):
                try:
                    length = int(br.get("length")) if br.get("length") is not None else None
                except Exception:
                    length = None
                try:
                    offset = int(br.get("offset")) if br.get("offset") is not None else None
                except Exception:
                    offset = None
                return length, offset
            try:
                length = int(getattr(br, "length", None))
            except Exception:
                length = None
            try:
                offset = int(getattr(br, "offset", None))
            except Exception:
                offset = None
            return length, offset

        init_info = None
        for segment in segments:
            init_section = getattr(segment, "init_section", None) or getattr(segment, "map", None)
            if init_section:
                try:
                    init_url = getattr(init_section, "uri", None)
                    if init_url and not init_url.startswith(("http://", "https://")):
                        init_url = urljoin(playlist_url, init_url)
                    br = getattr(init_section, "byterange", None)
                    length, offset = _parse_byterange(br)
                    range_header = None
                    if length:
                        if offset is None:
                            offset = 0
                        end = offset + length - 1
                        range_header = f"bytes={offset}-{end}"
                    if init_url:
                        init_info = {"url": init_url, "range": range_header}
                    break
                except Exception:
                    init_info = None
                    break

        segment_infos: List[Dict[str, str | None]] = []
        next_offset_by_url: Dict[str, int] = {}
        for segment in segments:
            try:
                segment_url = segment.uri
                if not segment_url:
                    segment_infos.append({"url": "", "range": None})
                    continue
                if not segment_url.startswith(('http://', 'https://')):
                    segment_url = urljoin(playlist_url, segment_url)

                br = getattr(segment, "byterange", None)
                length, offset = _parse_byterange(br)
                range_header = None
                if length:
                    if offset is None:
                        offset = next_offset_by_url.get(segment_url, 0)
                    end = offset + length - 1
                    next_offset_by_url[segment_url] = end + 1
                    range_header = f"bytes={offset}-{end}"
                    if not self._byterange_logged:
                        self._byterange_logged = True
                        logger.info("HLS использует BYTERANGE, запрашиваем сегменты через Range")

                segment_infos.append({"url": segment_url, "range": range_header})
            except Exception:
                segment_infos.append({"url": "", "range": None})

        concurrency = max(1, int(self.segment_concurrency or 1))

        async def _progress_tick():
            if not progress_callback:
                return
            if progress_base is not None and progress_span is not None:
                total_segments = 0
                downloaded_segments = 0
                if segment_type == "video" and self.total_video_segments > 0:
                    total_segments = self.total_video_segments
                    downloaded_segments = self.downloaded_video_segments
                elif segment_type == "audio" and self.total_audio_segments > 0:
                    total_segments = self.total_audio_segments
                    downloaded_segments = self.downloaded_audio_segments
                if total_segments > 0:
                    progress = progress_base + (downloaded_segments / total_segments) * progress_span
                    await progress_callback(progress)
                return

            if segment_type == "video" and self.total_video_segments > 0:
                progress = (self.downloaded_video_segments / self.total_video_segments) * 50
                await progress_callback(progress)
            elif segment_type == "audio" and self.total_audio_segments > 0:
                progress = 50 + (self.downloaded_audio_segments / self.total_audio_segments) * 40
                await progress_callback(progress)

        if concurrency <= 1:
            had_errors = False
            with open(output_path, 'wb') as out_file:
                if init_info and init_info.get("url"):
                    init_bytes = await self._download_bytes(
                        init_info["url"], referer, byte_range=init_info.get("range")
                    )
                    if not init_bytes:
                        logger.error("Не удалось скачать init-сегмент (EXT-X-MAP)")
                        return False
                    out_file.write(init_bytes)
                for i, info in enumerate(segment_infos):
                    segment_url = info.get("url") or ""
                    byte_range = info.get("range")
                    if not segment_url:
                        logger.error(f"Пропущен {segment_type} сегмент {i + 1} (empty URL)")
                        had_errors = True
                        continue
                    segment_data = await self.download_segment(
                        segment_url, segment_type, i, referer, byte_range=byte_range
                    )
                    if segment_data:
                        out_file.write(segment_data)
                        out_file.flush()
                        await _progress_tick()
                    else:
                        logger.error(f"Пропущен {segment_type} сегмент {i + 1}")
                        had_errors = True
            return not had_errors

        next_index = 0
        pending_tasks: Dict[asyncio.Task, int] = {}
        results: Dict[int, Optional[bytes]] = {}
        idx = 0
        had_errors = False

        async def _fetch(i: int, url: str, byte_range: str | None) -> Optional[bytes]:
            return await self.download_segment(url, segment_type, i, referer, byte_range=byte_range)

        with open(output_path, 'wb') as out_file:
            if init_info and init_info.get("url"):
                init_bytes = await self._download_bytes(
                    init_info["url"], referer, byte_range=init_info.get("range")
                )
                if not init_bytes:
                    logger.error("Не удалось скачать init-сегмент (EXT-X-MAP)")
                    return False
                out_file.write(init_bytes)
            while idx < total or pending_tasks:
                while idx < total and len(pending_tasks) < concurrency:
                    info = segment_infos[idx]
                    url = info.get("url") or ""
                    byte_range = info.get("range")
                    if not url:
                        results[idx] = None
                        had_errors = True
                        idx += 1
                        continue
                    task = asyncio.create_task(_fetch(idx, url, byte_range))
                    pending_tasks[task] = idx
                    idx += 1

                if not pending_tasks:
                    break

                done, _ = await asyncio.wait(
                    pending_tasks.keys(),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in done:
                    i = pending_tasks.pop(task, None)
                    if i is None:
                        continue
                    try:
                        results[i] = task.result()
                    except Exception as e:
                        logger.error(f"Ошибка загрузки {segment_type} сегмента {i + 1}: {e}")
                        results[i] = None

                while next_index in results:
                    data = results.pop(next_index)
                    if data:
                        out_file.write(data)
                        out_file.flush()
                        await _progress_tick()
                    else:
                        logger.error(f"Пропущен {segment_type} сегмент {next_index + 1}")
                        had_errors = True
                    next_index += 1

        return not had_errors

    async def download_playlist(self, url: str, referer: str = None, max_retries: int = 3) -> Optional[str]:
        """Скачиваем плейлист"""
        if not is_public_http_url(url):
            logger.warning(f"Blocked unsafe playlist URL: {url}")
            return None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    await asyncio.sleep(random.uniform(1.0, 2.0))

                headers = self.generate_headers(url, referer)

                logger.info(f"Загружаем плейлист...")

                async with asyncio.timeout(25):
                    async with self.session.get(
                            url,
                            headers=headers,
                            ssl=False,
                            proxy=(self.proxy_url if self._use_proxy_param else None)
                    ) as response:

                        if response.status == 200:
                            content = await response.text(errors='ignore')
                            if '#EXTM3U' in content:
                                logger.info(f"Плейлист загружен: {len(content)} символов")
                                return content
                            else:
                                logger.warning("Ответ не является M3U8 плейлистом")
                                continue

                        elif response.status == 412:
                            logger.warning(f"412 для плейлиста, попытка {attempt + 1}")
                            continue

                        elif response.status == 410:
                            logger.error("410 Gone для плейлиста")
                            return None

                        else:
                            logger.warning(f"HTTP {response.status} для плейлиста")
                            continue

            except TimeoutError:
                logger.warning(f"⏰ Таймаут загрузки плейлиста, попытка {attempt + 1}")
            except Exception as e:
                logger.error(f"Ошибка загрузки плейлиста: {e}")

        return None
    def get_available_qualities(self, master_playlist, master_url: str) -> List[Dict]:
        """Получаем список доступных качеств видео."""
        qualities: List[Dict] = []
        grouped: Dict[tuple, Dict] = {}

        if not hasattr(master_playlist, 'playlists') or not master_playlist.playlists:
            logger.warning("В мастер-плейлисте нет доступных плейлистов")
            return qualities

        def _safe_int(v) -> int:
            try:
                return int(v)
            except Exception:
                return 0

        for i, playlist in enumerate(master_playlist.playlists):
            try:
                stream_info = getattr(playlist, 'stream_info', None)
                if not stream_info:
                    logger.warning(f"Плейлист {i} не имеет stream_info, пропускаем")
                    continue

                resolution = getattr(stream_info, 'resolution', "Unknown")
                bandwidth = getattr(stream_info, 'bandwidth', "Unknown")
                codecs = getattr(stream_info, 'codecs', "Unknown")

                playlist_uri = getattr(playlist, 'uri', None)
                if not playlist_uri:
                    logger.warning(f"Плейлист {i} не имеет URI, пропускаем")
                    continue

                url = playlist_uri if playlist_uri.startswith(('http://', 'https://')) else urljoin(master_url, playlist_uri)

                width = 0
                height = 0
                if isinstance(resolution, (tuple, list)) and len(resolution) == 2:
                    try:
                        width = int(resolution[0] or 0)
                        height = int(resolution[1] or 0)
                    except Exception:
                        width = 0
                        height = 0
                elif isinstance(resolution, str):
                    m = re.search(r'(\d+)\s*[xX]\s*(\d+)', resolution)
                    if m:
                        try:
                            width = int(m.group(1))
                            height = int(m.group(2))
                        except Exception:
                            width = 0
                            height = 0

                audio_group_id = getattr(stream_info, 'audio', None)
                key = (width, height, _safe_int(bandwidth), str(codecs or ''))
                existing = grouped.get(key)

                if existing:
                    if url != existing.get('url') and url not in existing.setdefault('alternate_urls', []):
                        existing['alternate_urls'].append(url)
                    if audio_group_id:
                        group_ids = existing.setdefault('audio_group_ids', [])
                        if audio_group_id not in group_ids:
                            group_ids.append(audio_group_id)
                    continue

                grouped[key] = {
                    'index': i,
                    'resolution': resolution,
                    'width': width,
                    'height': height,
                    'bandwidth': bandwidth,
                    'codecs': codecs,
                    'url': url,
                    'alternate_urls': [],
                    'audio_group_id': audio_group_id,
                    'audio_group_ids': [audio_group_id] if audio_group_id else [],
                }

            except Exception as e:
                logger.error(f"Ошибка обработки плейлиста {i}: {e}")
                continue

        qualities = list(grouped.values())

        qualities.sort(
            key=lambda q: (int(q.get('height') or 0), _safe_int(q.get('bandwidth'))),
            reverse=True
        )
        return qualities

    def get_available_audio_tracks(self, master_playlist, master_url: str, page_content: str = None) -> List[Dict]:
        """Получаем список доступных аудиодорожек."""
        audio_tracks: List[Dict] = []

        mk_meta = self._extract_audio_meta_from_mk_player(page_content)
        mk_raw_names: List[str] = mk_meta.get('raw_names', []) or []
        mk_order: List[int] = mk_meta.get('order', []) or []

        audio_names_from_js = mk_raw_names or self._extract_audio_names_from_javascript(page_content)

        if mk_raw_names:
            logger.info(f"mk-player raw names: {mk_raw_names}")
            if mk_order:
                logger.info(f"mk-player order: {mk_order}")
        else:
            logger.info(f"Извлеченные названия озвучек из JS: {audio_names_from_js}")

        if not hasattr(master_playlist, 'media') or not master_playlist.media:
            logger.info("В мастер-плейлисте нет доступных аудио дорожек")
            if audio_names_from_js:
                for i, name in enumerate(audio_names_from_js):
                    nm = (name or '').strip()
                    if not nm:
                        continue
                    if nm.lower() in ('delete', 'deleted', 'remove', 'none', 'null'):
                        continue
                    audio_tracks.append({
                        'name': nm,
                        'technical_name': f'audio{i}',
                        'language': self._detect_language(nm),
                        'url': None,
                        'group_id': f'audio_{i}',
                        'index': i,
                        'technical_index': i,
                    })
                    logger.info(f"Создана аудиодорожка из JS (без URI): {nm}")
            return audio_tracks

        audio_media = []
        tech_numbers: List[int] = []
        for media in master_playlist.media or []:
            try:
                if getattr(media, 'type', None) != 'AUDIO':
                    continue
                tn = getattr(media, 'name', '') or ''
                m = re.search(r'(\d+)', tn)
                if m:
                    tech_numbers.append(int(m.group(1)))
                audio_media.append(media)
            except Exception:
                continue

        tech_base = 0 if 0 in tech_numbers else 1
        if tech_numbers:
            logger.info(f"Detected audio technical index base: {tech_base} (numbers={sorted(set(tech_numbers))})")

        def _idx0_from_technical(technical_name: str, fallback_i: int) -> int:
            m = re.search(r'(\d+)', technical_name or '')
            if not m:
                return fallback_i
            raw = int(m.group(1))
            return raw if tech_base == 0 else max(0, raw - 1)

        def _media_url(media_obj) -> Optional[str]:
            media_uri = getattr(media_obj, 'uri', None)
            if not media_uri:
                return None
            return media_uri if media_uri.startswith(('http://', 'https://')) else urljoin(master_url, media_uri)

        grouped_media: Dict[int, Dict] = {}
        for i, media in enumerate(audio_media):
            try:
                technical_name = getattr(media, 'name', '') or f'Audio{i}'
                idx0 = _idx0_from_technical(technical_name, i)
                url = _media_url(media)
                group_id = getattr(media, 'group_id', None)
                is_default = bool(getattr(media, 'default', False))

                slot = grouped_media.get(idx0)
                if slot is None:
                    grouped_media[idx0] = {
                        'media': media,
                        'technical_name': technical_name,
                        'url': url,
                        'alternate_urls': [],
                        'group_id': group_id,
                        'group_ids': [group_id] if group_id else [],
                        'is_default': is_default,
                    }
                    continue

                if group_id and group_id not in slot['group_ids']:
                    slot['group_ids'].append(group_id)

                current_url = slot.get('url')
                if is_default and not slot.get('is_default'):
                    if current_url and current_url != url and current_url not in slot['alternate_urls']:
                        slot['alternate_urls'].append(current_url)
                    slot.update({
                        'media': media,
                        'technical_name': technical_name,
                        'url': url,
                        'group_id': group_id,
                        'is_default': True,
                    })
                    continue

                if url and url != current_url and url not in slot['alternate_urls']:
                    slot['alternate_urls'].append(url)
            except Exception:
                continue

        if mk_raw_names:
            ordered = mk_order[:] if mk_order else list(range(len(mk_raw_names)))

            seen_idx = set()
            for idx0 in ordered:
                if idx0 in seen_idx:
                    continue
                seen_idx.add(idx0)

                if not (0 <= idx0 < len(mk_raw_names)):
                    continue

                display_name = (mk_raw_names[idx0] or '').strip()
                if not display_name:
                    continue
                if display_name.lower() in ('delete', 'deleted', 'remove', 'none', 'null'):
                    continue

                slot = grouped_media.get(idx0) or {}
                media = slot.get('media')
                technical_name = slot.get('technical_name') or (getattr(media, 'name', f'audio{idx0}') if media else f'audio{idx0}')
                language = getattr(media, 'language', None) or self._detect_language(display_name)
                group_id = slot.get('group_id') or (getattr(media, 'group_id', None) if media else f'audio_{idx0}')
                url = slot.get('url')
                alternate_urls = list(slot.get('alternate_urls') or [])
                group_ids = list(slot.get('group_ids') or ([group_id] if group_id else []))

                logger.info(f"Аудиодорожка {idx0}: техническое имя='{technical_name}', отображаемое имя='{display_name}'")

                audio_tracks.append({
                    'name': display_name,
                    'technical_name': technical_name,
                    'language': language,
                    'url': url,
                    'alternate_urls': alternate_urls,
                    'group_id': group_id,
                    'group_ids': group_ids,
                    'index': idx0,
                    'technical_index': idx0,
                })

            counts: Dict[str, int] = {}
            for t in audio_tracks:
                nm = t.get('name') or ''
                counts[nm] = counts.get(nm, 0) + 1
            if any(v > 1 for v in counts.values()):
                seen_local: Dict[str, int] = {}
                for t in audio_tracks:
                    nm = t.get('name') or ''
                    if counts.get(nm, 0) <= 1:
                        continue
                    seen_local[nm] = seen_local.get(nm, 0) + 1
                    t['name'] = f"{nm} ({seen_local[nm]})"

                    logger.info(f"Итоговый список аудиодорожек: {[track.get('name') for track in audio_tracks]}")
            return audio_tracks

        for idx0 in sorted(grouped_media.keys()):
            try:
                slot = grouped_media[idx0]
                media = slot.get('media')
                url = slot.get('url')
                if not url:
                    continue

                technical_name = slot.get('technical_name') or getattr(media, 'name', f'Audio{idx0}')

                display_name = self._get_display_name_for_audio(
                    technical_name, idx0, audio_names_from_js, technical_base=tech_base
                )
                if (display_name or '').strip().lower() in ('delete', 'deleted', 'remove', 'none', 'null'):
                    continue

                language = getattr(media, 'language', None) or self._detect_language(display_name)
                group_id = slot.get('group_id') or getattr(media, 'group_id', None)

                logger.info(f"Аудиодорожка {idx0}: техническое имя='{technical_name}', отображаемое имя='{display_name}'")

                audio_tracks.append({
                    'name': display_name,
                    'technical_name': technical_name,
                    'language': language,
                    'url': url,
                    'alternate_urls': list(slot.get('alternate_urls') or []),
                    'group_id': group_id,
                    'group_ids': list(slot.get('group_ids') or ([group_id] if group_id else [])),
                    'index': idx0,
                    'technical_index': idx0,
                })

            except Exception as e:
                logger.error(f"Ошибка обработки аудио дорожки: {e}")
                continue

                logger.info(f"Итоговый список аудиодорожек: {[track.get('name') for track in audio_tracks]}")
        return audio_tracks

    def _extract_audio_names_from_javascript(self, page_content: str) -> List[str]:

        """Извлекает названия озвучек из JavaScript кода."""
        if not page_content:
            return []

        names = self._extract_audio_names_from_mk_player(page_content)
        if names:
            return names

        names = self._extract_audio_names_primary(page_content)
        if names:
            return names

        names = self._extract_audio_names_fallback(page_content)
        if names:
            return names

        return []

    def _extract_audio_meta_from_mk_player(self, page_content: str) -> Dict[str, List]:
        """Извлекает meta об озвучках из <script data-name="mk">."""
        try:
            if not page_content:
                return {}

            head = page_content[:2_500_000]

            scripts = re.findall(
                r'<script[^>]*data-name=["\']mk["\'][^>]*>(.*?)</script>',
                head,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not scripts:
                return {}

            for script in scripts:
                if not script:
                    continue

                m_audio = re.search(r"\baudio\s*:\s*\{", script)
                if not m_audio:
                    continue
                audio_start = script.find('{', m_audio.end() - 1)
                if audio_start < 0:
                    continue
                audio_blob = self._extract_balanced_block(script, audio_start)
                if not audio_blob:
                    continue

                raw_names: List[str] = []
                order: List[int] = []

                try:
                    obj = json.loads(audio_blob)
                    if isinstance(obj, dict):
                        if isinstance(obj.get('names'), list):
                            raw_names = [str(x) for x in obj.get('names') if isinstance(x, (str, int, float))]
                        if isinstance(obj.get('order'), list):
                            order = [int(x) for x in obj.get('order') if str(x).isdigit()]
                except Exception:
                    candidate = re.sub(r"([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', audio_blob)
                    candidate = re.sub(r",\s*([\]\}])", r"\1", candidate)
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            if isinstance(obj.get('names'), list):
                                raw_names = [str(x) for x in obj.get('names') if isinstance(x, (str, int, float))]
                            if isinstance(obj.get('order'), list):
                                order = [int(x) for x in obj.get('order') if str(x).isdigit()]
                    except Exception:
                        pass

                if raw_names:
                    return {'raw_names': raw_names, 'order': order}

            return {}
        except Exception as e:
            logger.error(f"Ошибка извлечения meta озвучек из mk-player: {e}")
            return {}

    def _extract_audio_names_from_mk_player(self, page_content: str) -> List[str]:
        """Извлекает названия озвучек из блока makePlayer(...) внутри <script data-name="mk">."""
        try:
            if not page_content:
                return []

            head = page_content[:2_500_000]

            scripts = re.findall(
                r'<script[^>]*data-name=["\']mk["\'][^>]*>(.*?)</script>',
                head,
                flags=re.IGNORECASE | re.DOTALL
            )

            if not scripts:
                return []

            def _parse_string_list(list_blob: str) -> List[str]:
                list_blob = (list_blob or '').strip()
                if not list_blob:
                    return []
                candidate = list_blob
                candidate = re.sub(r",\s*\]", "]", candidate)
                candidate = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", lambda m: '"' + m.group(1).replace('"', '\\"') + '"', candidate)
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, list):
                        return [str(x) for x in obj if isinstance(x, (str, int, float))]
                except Exception:
                    pass
                raw = re.findall(r"[\"']([^\"']{1,120})[\"']", list_blob)
                return [x for x in raw if x]

            def _parse_int_list(list_blob: str) -> List[int]:
                return [int(x) for x in re.findall(r"\d+", list_blob or '')]

            for script in scripts:
                if not script:
                    continue

                m_audio = re.search(r"\baudio\s*:\s*\{", script)
                if not m_audio:
                    continue

                audio_start = script.find('{', m_audio.end() - 1)
                if audio_start < 0:
                    continue

                audio_blob = self._extract_balanced_block(script, audio_start)
                if not audio_blob:
                    continue

                names: List[str] = []
                order: List[int] = []

                try:
                    obj = json.loads(audio_blob)
                    if isinstance(obj, dict):
                        raw_names = obj.get('names')
                        raw_order = obj.get('order')
                        if isinstance(raw_names, list):
                            names = [str(x) for x in raw_names if isinstance(x, (str, int, float))]
                        if isinstance(raw_order, list):
                            order = [int(x) for x in raw_order if str(x).isdigit()]
                except Exception:
                    m_names = re.search(r"['\"]?names['\"]?\s*:\s*\[", audio_blob)
                    if m_names:
                        names_start = audio_blob.find('[', m_names.end() - 1)
                        names_blob = self._extract_balanced_block(audio_blob, names_start) if names_start >= 0 else None
                        names = _parse_string_list(names_blob or '')

                    m_order = re.search(r"['\"]?order['\"]?\s*:\s*\[", audio_blob)
                    if m_order:
                        order_start = audio_blob.find('[', m_order.end() - 1)
                        order_blob = self._extract_balanced_block(audio_blob, order_start) if order_start >= 0 else None
                        order = _parse_int_list(order_blob or '')

                if not names:
                    continue

                ordered: List[str]
                if order and all(0 <= i < len(names) for i in order):
                    ordered = [names[i] for i in order]
                else:
                    ordered = list(names)

                out: List[str] = []
                seen = set()
                for n in ordered:
                    s = (n or '').strip()
                    if not s:
                        continue
                    if s.lower() in ('delete', 'deleted', 'remove', 'none', 'null'):
                        continue
                    if s not in seen:
                        seen.add(s)
                        out.append(s)

                out = self._filter_audio_names(out)
                if out:
                    logger.info(f"mk-player: найдены названия озвучек: {out}")
                    return out

            return []
        except Exception as e:
            logger.error(f"Ошибка извлечения озвучек из mk-player: {e}")
            return []

    def _extract_audio_names_primary(self, page_content: str) -> List[str]:
        """Основной метод извлечения названий озвучек из JavaScript."""
        try:
            if not page_content:
                return []

            text = page_content
            if len(text) > 2_000_000:
                text = text[:2_000_000]

            anchors = [
                'audioTracks', 'audiotracks', 'audio_tracks', 'audio_tracks_list',
                'voices', 'voice', 'translations', 'translation', 'dubs', 'dub',
                'soundtracks', 'soundtrack', 'audios', 'audio'
            ]

            results: List[str] = []

            for key in anchors:
                for m in re.finditer(rf"{re.escape(key)}\s*[:=]\s*([\[\{{])", text, flags=re.IGNORECASE):
                    start = m.start(1)
                    blob = self._extract_balanced_block(text, start)
                    if not blob:
                        continue
                    for name in self._names_from_json_like(blob):
                        if name not in results:
                            results.append(name)
                    if len(results) >= 20:
                        break
                if len(results) >= 20:
                    break

            if not results:
                for m in re.finditer(r"(?:translations|voices|audioTracks)\s*[:=]\s*\{", text, flags=re.IGNORECASE):
                    start = text.find("{", m.end() - 1)
                    if start < 0:
                        continue
                    blob = self._extract_balanced_block(text, start)
                    if not blob:
                        continue
                    for name in self._names_from_json_like(blob):
                        if name not in results:
                            results.append(name)
                    if results:
                        break

            results = self._filter_audio_names(results)
            if results:
                logger.info(f"Primary: найдены названия озвучек: {results}")
            return results

        except Exception as e:
            logger.error(f"Ошибка primary извлечения названий озвучек: {e}")
            return []

    def _extract_balanced_block(self, text: str, start: int) -> Optional[str]:
        """Достаёт сбалансированный блок JSON-подобного текста начиная с '[' или '{'."""
        if start < 0 or start >= len(text):
            return None
        open_ch = text[start]
        if open_ch not in ('[', '{'):
            return None
        close_ch = ']' if open_ch == '[' else '}'

        depth = 0
        in_str = False
        str_ch = ''
        escape = False

        for i in range(start, len(text)):
            ch = text[i]

            if in_str:
                if escape:
                    escape = False
                    continue
                if ch == '\\':
                    escape = True
                    continue
                if ch == str_ch:
                    in_str = False
                    str_ch = ''
                continue

            if ch in ('"', "'"):
                in_str = True
                str_ch = ch
                continue

            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return None

    def _names_from_json_like(self, blob: str) -> List[str]:
        """Пытается извлечь названия озвучек из JSON/JS-подобного блока."""
        blob = (blob or '').strip()
        if not blob:
            return []

        def _collect_from_obj(obj) -> List[str]:
            out: List[str] = []
            if isinstance(obj, list):
                for item in obj:
                    out.extend(_collect_from_obj(item))
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, (str, int, float)):
                        out.append(str(v))
                    else:
                        out.extend(_collect_from_obj(v))
                for key in ('name', 'title', 'label', 'text', 'translation'):
                    if key in obj and isinstance(obj[key], (str, int, float)):
                        out.append(str(obj[key]))
            elif isinstance(obj, (str, int, float)):
                out.append(str(obj))
            return out

        for attempt in range(3):
            try:
                candidate = blob
                if attempt >= 1:
                    candidate = re.sub(r"([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', candidate)
                    candidate = re.sub(r",\s*([\]\}])", r"\1", candidate)
                if attempt >= 2:
                    candidate = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", lambda m: '"' + m.group(1).replace('"', '\\"') + '"', candidate)

                obj = json.loads(candidate)
                names = _collect_from_obj(obj)
                return names
            except Exception:
                pass

        raw = re.findall(r"[\"']([^\"']{2,80})[\"']", blob)
        return raw

    def _filter_audio_names(self, names: List[str]) -> List[str]:
        """Фильтрует мусорные строки и оставляет похожие на названия озвучек."""
        out: List[str] = []
        seen = set()

        for name in names or []:
            n = (name or '').strip()
            if not n:
                continue
            if len(n) < 2 or len(n) > 80:
                continue
            lower = n.lower()
            if any(x in lower for x in ['http://', 'https://', '.m3u8', '.mp4', '.ts', 'playlist', 'manifest', 'file', 'poster', 'subtitle']):
                continue
            if not re.search(r"[A-Za-zА-Яа-я]", n):
                continue
            if n not in seen:
                seen.add(n)
                out.append(n)

        return out

    def _extract_audio_names_fallback(self, page_content: str) -> List[str]:
        """Альтернативный метод извлечения названий озвучек"""
        try:
            russian_audio_patterns = [
                r'["\'](Рус\.?\s*Дублированный)["\']',
                r'["\'](Рус\.?\s*Проф\.?\s*двухголосый)["\']',
                r'["\'](Рус\.?\s*Одноголосый)["\']',
                r'["\'](Рус\.?\s*Люб\.?\s*одноголосый)["\']',
                r'["\'](Укр\.?\s*Дубльований)["\']',
                r'["\'](Eng\.?\s*Original)["\']',
            ]

            found_names = []
            for pattern in russian_audio_patterns:
                matches = re.findall(pattern, page_content, re.IGNORECASE)
                found_names.extend(matches)

            if found_names:
                logger.info(f"Fallback: найдены названия {found_names}")
                return found_names

            return []
        except Exception as e:
            logger.error(f"Ошибка в fallback методе: {e}")
            return []

    def _parse_audio_names(self, names_str: str) -> List[str]:
        """Парсит строку с названиями озвучек"""
        try:
            cleaned = re.sub(r'[\n\r\t]', '', names_str)
            cleaned = re.sub(r'\s+', ' ', cleaned)

            names = []
            current_name = ""
            in_quotes = False
            quote_char = None

            for char in cleaned:
                if char in ['"', "'"] and not in_quotes:
                    in_quotes = True
                    quote_char = char
                elif char == quote_char and in_quotes:
                    in_quotes = False
                    quote_char = None
                    if current_name.strip():
                        names.append(current_name.strip())
                    current_name = ""
                elif char == ',' and not in_quotes:
                    if current_name.strip():
                        names.append(current_name.strip())
                    current_name = ""
                else:
                    current_name += char

            if current_name.strip():
                names.append(current_name.strip())

            names = [name for name in names if name and name not in [',', '"', "'"]]

            return names

        except Exception as e:
            logger.error(f"Ошибка парсинга названий: {e}")
            return [name.strip().strip('"\'') for name in names_str.split(',') if name.strip()]

    def _get_display_name_for_audio(
            self,
            technical_name: str,
            index: int,
            audio_names: List[str],
            technical_base: Optional[int] = None,
    ) -> str:
        """Получает человеко‑читаемое имя для аудиодорожки."""
        logger.debug("Display name lookup: technical_name=%r index=%s base=%s names_len=%s",
            technical_name, index, technical_base, len(audio_names or []),
        )

        if not audio_names:
            return technical_name

        tech_match = re.search(r'(\d+)', technical_name or '')
        if tech_match:
            raw = int(tech_match.group(1))
            base = technical_base
            if base is None:
                base = 0 if raw == 0 else 1
            idx = raw if base == 0 else raw - 1
            if 0 <= idx < len(audio_names):
                return audio_names[idx]

        if 0 <= index < len(audio_names):
            return audio_names[index]

        return technical_name

    def _detect_language(self, name: str) -> str:
        """Определяет язык по названию озвучки"""
        name_lower = name.lower()

        if any(word in name_lower for word in ['рус', 'rus', 'русс', 'russian']):
            return 'ru'
        elif any(word in name_lower for word in ['укр', 'ukr', 'укра', 'ukrainian']):
            return 'uk'
        elif any(word in name_lower for word in ['eng', 'англ', 'english', 'американ']):
            return 'en'
        elif any(word in name_lower for word in ['нем', 'герм', 'german']):
            return 'de'
        elif any(word in name_lower for word in ['фран', 'french']):
            return 'fr'
        else:
            return 'Unknown'

    def _merge_with_ffmpeg(self, video_path: str, audio_path: str, output_path: str) -> bool:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            logger.error("ffmpeg не найден в PATH, не могу объединить видео и аудио")
            return False

        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-c",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-movflags",
            "+faststart",
            output_path,
        ]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()
                if err:
                    tail = "\n".join(err.splitlines()[-12:])
                    logger.error("ffmpeg завершился с ошибкой:\n%s", tail)
                else:
                    logger.error("ffmpeg завершился с кодом %s", proc.returncode)
                return False
            return True
        except Exception as e:
            logger.error("Ошибка запуска ffmpeg: %s", e)
            return False

    def _extract_audio_with_ffmpeg(self, audio_path: str, output_path: str) -> bool:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            logger.error("ffmpeg не найден в PATH, не могу подготовить аудио-файл")
            return False

        copy_cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            audio_path,
            "-vn",
            "-map",
            "0:a:0",
            "-c",
            "copy",
            output_path,
        ]
        aac_cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            audio_path,
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            output_path,
        ]

        for cmd in (copy_cmd, aac_cmd):
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if proc.returncode == 0:
                    return True
            except Exception as e:
                logger.error("Ошибка запуска ffmpeg для аудио: %s", e)
                return False

                logger.error("ffmpeg не смог подготовить audio-only файл")
        return False

    async def download_complete_stream(self, master_url: str, output_filename: str = "complete_video.mp4",
                                       selected_quality: Dict = None, selected_audio: Dict = None,
                                       progress_callback=None, referer: str = None,
                                       duration_s: int = 0,
                                       available_audio_tracks: Optional[List[Dict]] = None) -> bool:
        """Полное скачивание видео с выбором качества и аудио"""
        logger.info(f"Начинаем скачивание видео и аудио...")
        logger.info(f"URL: {master_url}")

        start_time = time.time()

        try:
            logger.info("\n1. Загружаем мастер-плейлист...")
            master_content = await self.download_playlist(master_url, referer)

            if not master_content:
                logger.error("Не удалось загрузить мастер-плейлист")
                return False

            master_playlist = m3u8.loads(master_content)

            if getattr(master_playlist, 'playlists', None):
                qualities = self.get_available_qualities(master_playlist, master_url)
                if isinstance(available_audio_tracks, list):
                    audio_tracks = [dict(track) for track in available_audio_tracks if isinstance(track, dict)]
                else:
                    audio_tracks = self.get_available_audio_tracks(master_playlist, master_url)

                if not qualities:
                    logger.error("Нет доступных качеств видео")
                    return False
            else:
                logger.info("Playlist has no variants, treating as a single HLS stream")
                qualities = [{
                    'index': 0,
                    'resolution': 'auto',
                    'width': 0,
                    'height': 0,
                    'bandwidth': 0,
                    'codecs': 'unknown',
                    'url': master_url,
                }]
                audio_tracks = []

            if not selected_quality:
                selected_quality = qualities[0]

            video_url = selected_quality.get('url') if isinstance(selected_quality, dict) else None
            if not video_url:
                logger.error("Не удалось получить URL видео")
                return False

            audio_url = None
            if selected_audio and isinstance(selected_audio, dict):
                audio_url = (selected_audio.get('url') or '').strip() or None

            skip_auto_audio = bool(selected_audio and isinstance(selected_audio, dict) and selected_audio.get('download_via_ytdlp'))
            if not audio_url and audio_tracks and not skip_auto_audio:
                wanted_name = ''
                if selected_audio and isinstance(selected_audio, dict):
                    wanted_name = (selected_audio.get('name') or '').strip().lower()

                chosen = None
                if wanted_name:
                    for track in audio_tracks:
                        if not isinstance(track, dict):
                            continue
                        track_url = (track.get('url') or '').strip()
                        if not track_url:
                            continue
                        track_name = (track.get('name') or '').strip().lower()
                        if track_name == wanted_name:
                            chosen = track
                            break

                if not chosen:
                    for track in audio_tracks:
                        if not isinstance(track, dict):
                            continue
                        track_url = (track.get('url') or '').strip()
                        if track_url:
                            chosen = track
                            break

                if chosen:
                    selected_audio = chosen
                    audio_url = (chosen.get('url') or '').strip() or None

                    logger.info(f"\nВыбранные настройки:")
                    logger.info(f"- Качество: {selected_quality.get('resolution', 'Unknown')}")
                    logger.info(f"- Аудио: {selected_audio.get('name', 'Нет') if selected_audio else 'Нет'}")


            if audio_url and not shutil.which("ffmpeg"):
                logger.warning("ffmpeg не найден — скачиваю видео без аудио")
                audio_url = None
                selected_audio = None

            master_host = ''
            try:
                master_host = urlparse(master_url).netloc or ''
            except Exception:
                master_host = ''

            def _candidate_urls(primary_url: Optional[str], alternates: Optional[List[str]] = None) -> List[str]:
                urls: List[str] = []
                for candidate in [primary_url, *(alternates or [])]:
                    url_s = (candidate or '').strip()
                    if url_s and url_s not in urls:
                        urls.append(url_s)

                    rewritten = self._rewrite_url_host(url_s, master_host)
                    if rewritten and rewritten not in urls:
                        urls.append(rewritten)
                return urls

            async def _load_first_working_playlist(urls: List[str], kind: str) -> tuple[Optional[str], Optional[str]]:
                for candidate_url in urls:
                    content = await self.download_playlist(candidate_url, referer)
                    if content:
                        if candidate_url != urls[0]:
                            logger.info(f"Для {kind} переключились на failover playlist: {candidate_url}")
                        return candidate_url, content
                return None, None

                logger.info("\n2. Загружаем видео плейлист...")
            video_url, video_content = await _load_first_working_playlist(
                _candidate_urls(video_url, selected_quality.get('alternate_urls') if isinstance(selected_quality, dict) else None),
                "video",
            )

            if not video_content:
                logger.error("Не удалось загрузить видео плейлист")
                return False

            video_playlist_obj = m3u8.loads(video_content)
            self.total_video_segments = len(video_playlist_obj.segments)
            logger.info(f"Видео плейлист: {self.total_video_segments} сегментов")

            base = os.path.splitext(output_filename)[0]
            video_temp_file = base + ".video.ts"
            audio_temp_file = base + ".audio.ts"
            final_file = output_filename

            logger.info(f"\n4. Скачиваем {self.total_video_segments} видео сегментов...")
            video_download_ok = await self._download_segments_to_file(
                video_playlist_obj,
                video_url,
                video_temp_file,
                "video",
                referer,
                progress_callback,
            )
            if not video_download_ok:
                logger.error("Не удалось полностью скачать все видео сегменты")
                return False

            if progress_callback:
                await progress_callback(50 if audio_url else 90)

            if audio_url:
                logger.info("\n3. Загружаем аудио плейлист...")
                audio_url, audio_content = await _load_first_working_playlist(
                    _candidate_urls(audio_url, selected_audio.get('alternate_urls') if isinstance(selected_audio, dict) else None),
                    "audio",
                )
                if not audio_content:
                    logger.error("Не удалось загрузить аудио плейлист")
                    return False

                audio_playlist_obj = m3u8.loads(audio_content)
                self.total_audio_segments = len(audio_playlist_obj.segments)
                if self.total_audio_segments <= 0:
                    logger.error("Аудио плейлист пустой")
                    return False
                    logger.info(f"Аудио плейлист: {self.total_audio_segments} сегментов")

                    logger.info(f"\n5. Скачиваем {self.total_audio_segments} аудио сегментов...")
                audio_download_ok = await self._download_segments_to_file(
                    audio_playlist_obj,
                    audio_url,
                    audio_temp_file,
                    "audio",
                    referer,
                    progress_callback,
                )
                if not audio_download_ok:
                    logger.error("Не удалось полностью скачать все аудио сегменты")
                    return False

            if audio_url:
                logger.info("\n6. Объединяем видео и аудио через ffmpeg...")
                success = self._merge_with_ffmpeg(video_temp_file, audio_temp_file, final_file)
            else:
                logger.info("\n5. Сохраняем видео...")
                shutil.copy2(video_temp_file, final_file)
                success = True

            if os.path.exists(video_temp_file):
                os.remove(video_temp_file)
            if os.path.exists(audio_temp_file):
                os.remove(audio_temp_file)

            end_time = time.time()
            total_time = end_time - start_time

            if success and os.path.exists(final_file):
                file_size = os.path.getsize(final_file)
                file_size_mb = file_size / (1024 * 1024)

                logger.info(f"\n СКАЧИВАНИЕ ЗАВЕРШЕНО!")
                logger.info(f"Статистика:")
                logger.info(f"- Качество: {selected_quality.get('resolution', 'Unknown')}")
                logger.info(f"- Аудио: {selected_audio.get('name', 'Нет') if selected_audio else 'Нет'}")
                logger.info(f"- Видео сегментов: {self.downloaded_video_segments}/{self.total_video_segments}")
                if audio_url:
                    logger.info(f"- Аудио сегментов: {self.downloaded_audio_segments}/{self.total_audio_segments}")
                    logger.info(f"- Размер файла: {file_size_mb:.2f} MB")
                    logger.info(f"- Общее время: {total_time:.2f} секунд")
                    logger.info(f"- Выходной файл: {final_file}")

                if progress_callback:
                    await progress_callback(100)

                return True
            else:
                logger.error("Не удалось создать финальный файл")
                return False

        except Exception as e:
            logger.error(f"Критическая ошибка при скачивании: {e}")
            import traceback
            logger.error(f"Детали ошибки: {traceback.format_exc()}")
            return False

    async def download_audio_only_stream(
        self,
        master_url: str,
        output_filename: str = "audio_only.m4a",
        selected_audio: Dict = None,
        progress_callback=None,
        referer: str = None,
        duration_s: int = 0,
        available_audio_tracks: Optional[List[Dict]] = None,
    ) -> bool:
        logger.info("Начинаем скачивание только аудио...")
        logger.info(f"URL: {master_url}")

        audio_temp_file = ""
        start_time = time.time()

        try:
            logger.info("\n1. Загружаем мастер-плейлист...")
            master_content = await self.download_playlist(master_url, referer)
            if not master_content:
                logger.error("Не удалось загрузить мастер-плейлист")
                return False

            master_playlist = m3u8.loads(master_content)
            if isinstance(available_audio_tracks, list):
                audio_tracks = [dict(track) for track in available_audio_tracks if isinstance(track, dict)]
            else:
                audio_tracks = self.get_available_audio_tracks(master_playlist, master_url)

            if not audio_tracks:
                logger.error("Нет доступных аудиодорожек")
                return False

            audio_url = None
            if selected_audio and isinstance(selected_audio, dict):
                audio_url = (selected_audio.get('url') or '').strip() or None

            if not audio_url:
                wanted_name = ''
                if selected_audio and isinstance(selected_audio, dict):
                    wanted_name = (selected_audio.get('name') or '').strip().lower()

                chosen = None
                if wanted_name:
                    for track in audio_tracks:
                        if not isinstance(track, dict):
                            continue
                        track_url = (track.get('url') or '').strip()
                        if not track_url:
                            continue
                        track_name = (track.get('name') or '').strip().lower()
                        if track_name == wanted_name:
                            chosen = track
                            break
                if not chosen:
                    chosen = next(
                        (
                            track for track in audio_tracks
                            if isinstance(track, dict) and (track.get('url') or '').strip()
                        ),
                        None,
                    )

                if not chosen:
                    logger.error("Не удалось выбрать аудиодорожку")
                    return False

                selected_audio = chosen
                audio_url = (chosen.get('url') or '').strip() or None

                logger.info("\nВыбранные настройки:")
                logger.info(f"- Аудио: {selected_audio.get('name', 'Нет') if selected_audio else 'Нет'}")

            master_host = ''
            try:
                master_host = urlparse(master_url).netloc or ''
            except Exception:
                master_host = ''

            def _candidate_urls(primary_url: Optional[str], alternates: Optional[List[str]] = None) -> List[str]:
                urls: List[str] = []
                for candidate in [primary_url, *(alternates or [])]:
                    url_s = (candidate or '').strip()
                    if url_s and url_s not in urls:
                        urls.append(url_s)

                    rewritten = self._rewrite_url_host(url_s, master_host)
                    if rewritten and rewritten not in urls:
                        urls.append(rewritten)
                return urls

            async def _load_first_working_playlist(urls: List[str]) -> tuple[Optional[str], Optional[str]]:
                for candidate_url in urls:
                    content = await self.download_playlist(candidate_url, referer)
                    if content:
                        return candidate_url, content
                return None, None

                logger.info("\n2. Загружаем аудио плейлист...")
            audio_url, audio_content = await _load_first_working_playlist(
                _candidate_urls(
                    audio_url,
                    selected_audio.get('alternate_urls') if isinstance(selected_audio, dict) else None,
                )
            )
            if not audio_content:
                logger.error("Не удалось загрузить аудио плейлист")
                return False

            audio_playlist_obj = m3u8.loads(audio_content)
            self.total_audio_segments = len(audio_playlist_obj.segments)
            if self.total_audio_segments <= 0:
                logger.error("Аудио плейлист пустой")
                return False
                logger.info(f"Аудио плейлист: {self.total_audio_segments} сегментов")

            base = os.path.splitext(output_filename)[0]
            audio_temp_file = base + ".audio.ts"

            logger.info(f"\n3. Скачиваем {self.total_audio_segments} аудио сегментов...")
            audio_download_ok = await self._download_segments_to_file(
                audio_playlist_obj,
                audio_url,
                audio_temp_file,
                "audio",
                referer,
                progress_callback,
                progress_base=0,
                progress_span=90,
            )
            if not audio_download_ok:
                logger.error("Не удалось полностью скачать все аудио сегменты")
                return False

            ffmpeg_available = bool(shutil.which("ffmpeg"))
            if ffmpeg_available and not output_filename.lower().endswith(".ts"):
                logger.info("\n4. Готовим audio-only файл через ffmpeg...")
                success = self._extract_audio_with_ffmpeg(audio_temp_file, output_filename)
            else:
                logger.info("\n4. Сохраняем audio-only поток...")
                shutil.copy2(audio_temp_file, output_filename)
                success = True

            if os.path.exists(audio_temp_file):
                os.remove(audio_temp_file)

            if success and os.path.exists(output_filename):
                total_time = time.time() - start_time
                file_size = os.path.getsize(output_filename)
                logger.info("\n СКАЧИВАНИЕ AUDIO-ONLY ЗАВЕРШЕНО!")
                logger.info(f"Размер файла: {file_size / (1024 * 1024):.1f} MB")
                logger.info(f"⏱ Время: {total_time:.1f}s")
                if progress_callback:
                    await progress_callback(100)
                return True
            return False

        except Exception as e:
            logger.error(f"Ошибка скачивания только аудио: {e}")
            return False
        finally:
            if audio_temp_file and os.path.exists(audio_temp_file):
                try:
                    os.remove(audio_temp_file)
                except Exception:
                    pass

    async def get_master_playlist_info(self, master_url: str, referer: str = None, page_content: str = None) -> Dict:
        """Получает полную информацию о мастер-плейлисте с учетом содержимого страницы"""
        try:
            content = await self.download_playlist(master_url, referer)
            if not content:
                return {'qualities': [], 'audio_tracks': []}

            master_playlist = m3u8.loads(content)

            qualities = self.get_available_qualities(master_playlist, master_url)
            audio_tracks = self.get_available_audio_tracks(master_playlist, master_url, page_content)

            result = {
                'qualities': qualities,
                'audio_tracks': audio_tracks,
                'master_url': master_url
            }

            logger.info(f"Найдено {len(result['qualities'])} качеств и {len(result['audio_tracks'])} аудиодорожек")
            return result

        except Exception as e:
            logger.error(f"Ошибка анализа мастер-плейлиста: {e}")
            return {'qualities': [], 'audio_tracks': []}
