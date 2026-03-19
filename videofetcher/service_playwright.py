import asyncio
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from .url_safety import is_public_http_url

logger = logging.getLogger(__name__)


class VideoServicePlaywrightMixin:
    def _extract_m3u8_from_javascript_deep(self, js_code: str, base_url: str) -> List[str]:
        """Глубокий поиск M3U8 URL в JavaScript коде"""
        m3u8_urls = self._extract_m3u8_from_javascript(js_code, base_url)

        json_patterns = [
            r'\{[^{}]*["\']hls["\']\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\'][^{}]*\}',
            r'\{[^{}]*["\']url["\']\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\'][^{}]*\}',
            r'\{[^{}]*["\']src["\']\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\'][^{}]*\}',
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, js_code, re.IGNORECASE | re.DOTALL)
            for match in matches:
                m3u8_url = match
                if not m3u8_url.startswith(('http://', 'https://')):
                    m3u8_url = urljoin(base_url, m3u8_url)
                if (
                    m3u8_url not in m3u8_urls
                    and self._is_valid_m3u8_url(m3u8_url)
                    and not self._is_known_embedded_video_platform_url(m3u8_url)
                ):
                    m3u8_urls.append(m3u8_url)
                    logger.info(f"Found M3U8 in JavaScript object: {m3u8_url}")

        concat_pattern = r'["\'][^"\']*\.m3u8[^"\']*["\']\s*\+\s*["\'][^"\']*["\']'
        concat_matches = re.findall(concat_pattern, js_code, re.IGNORECASE)
        for match in concat_matches:
            parts = re.findall(r'["\']([^"\']*)["\']', match)
            if len(parts) >= 2:
                potential_url = parts[0] + parts[1]
                if '.m3u8' in potential_url:
                    m3u8_url = potential_url
                    if not m3u8_url.startswith(('http://', 'https://')):
                        m3u8_url = urljoin(base_url, m3u8_url)
                    if (
                        m3u8_url not in m3u8_urls
                        and self._is_valid_m3u8_url(m3u8_url)
                        and not self._is_known_embedded_video_platform_url(m3u8_url)
                    ):
                        m3u8_urls.append(m3u8_url)
                        logger.info(f"Found M3U8 in JavaScript concatenation: {m3u8_url}")

        return m3u8_urls

    def _is_valid_m3u8_url(self, url: str) -> bool:
        """Проверяет валидность M3U8 URL"""
        if not url or ';' in url or 'local' in url.lower():
            return False

        try:
            parsed = urlparse(url)
            return (parsed.scheme in ['http', 'https'] and
                    parsed.netloc and
                    '.m3u8' in parsed.path and
                    is_public_http_url(url))
        except Exception:
            return False

    def _extract_m3u8_from_json_deep(self, json_data, base_url: str) -> List[str]:
        """Глубокий поиск M3U8 URL в JSON структуре с игнорированием YouTube"""
        m3u8_urls = []

        def recursive_search(obj, path=""):
            if isinstance(obj, dict):
                for key in ['hls', 'm3u8', 'url', 'video_url', 'stream_url', 'src', 'file']:
                    if key in obj and isinstance(obj[key], str) and '.m3u8' in obj[key]:
                        m3u8_url = obj[key]
                        if not m3u8_url.startswith(('http://', 'https://')):
                            m3u8_url = urljoin(base_url, m3u8_url)
                        if m3u8_url not in m3u8_urls and self._is_valid_m3u8_url(m3u8_url) and not self._is_known_embedded_video_platform_url(
                                m3u8_url):
                            m3u8_urls.append(m3u8_url)
                            logger.info(f"Found M3U8 in JSON path '{path}.{key}': {m3u8_url}")

                for key, value in obj.items():
                    recursive_search(value, f"{path}.{key}" if path else key)

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    recursive_search(item, f"{path}[{i}]")

            elif isinstance(obj, str) and '.m3u8' in obj:
                found_urls = re.findall(r'https?://[^\s"\']+\.m3u8[^\s"\']*', obj)
                for found_url in found_urls:
                    if found_url not in m3u8_urls and self._is_valid_m3u8_url(found_url) and not self._is_known_embedded_video_platform_url(
                            found_url):
                        m3u8_urls.append(found_url)

        recursive_search(json_data)
        return m3u8_urls

    def _extract_m3u8_from_javascript(self, js_code: str, base_url: str) -> List[str]:
        """Ищет M3U8 URL в JavaScript коде с игнорированием YouTube"""
        m3u8_urls = []

        js_clean = re.sub(r'//.*?$|/\*.*?\*/', '', js_code, flags=re.MULTILINE | re.DOTALL)

        patterns = [
            r'hls\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'm3u8\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'url\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'source\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'src\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, js_clean, re.IGNORECASE)
            for match in matches:
                m3u8_url = match
                if not m3u8_url.startswith(('http://', 'https://')):
                    m3u8_url = urljoin(base_url, m3u8_url)
                if m3u8_url not in m3u8_urls and self._is_valid_m3u8_url(m3u8_url) and not self._is_known_embedded_video_platform_url(
                        m3u8_url):
                    m3u8_urls.append(m3u8_url)
                    logger.info(f"Found M3U8 with JavaScript pattern: {m3u8_url}")

        var_patterns = [
            r'var\s+\w+\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'let\s+\w+\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'const\s+\w+\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'\w+\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
        ]

        for pattern in var_patterns:
            matches = re.findall(pattern, js_clean, re.IGNORECASE)
            for match in matches:
                m3u8_url = match
                if not m3u8_url.startswith(('http://', 'https://')):
                    m3u8_url = urljoin(base_url, m3u8_url)
                if m3u8_url not in m3u8_urls and self._is_valid_m3u8_url(m3u8_url) and not self._is_known_embedded_video_platform_url(
                        m3u8_url):
                    m3u8_urls.append(m3u8_url)
                    logger.info(f"Found M3U8 in JavaScript variable: {m3u8_url}")

        return m3u8_urls

    def _extract_m3u8_from_text(self, text: str, base_url: str) -> List[str]:
        """Ищет M3U8 URL в тексте с игнорированием YouTube"""
        m3u8_urls = []

        url_pattern = r'https?://[^\s"\']+\.m3u8[^\s"\']*'
        found_urls = re.findall(url_pattern, text, re.IGNORECASE)

        for url in found_urls:
            url = re.sub(r'[\\,;]+$', '', url)
            if self._is_valid_m3u8_url(url) and not self._is_known_embedded_video_platform_url(url):
                m3u8_urls.append(url)

        return m3u8_urls

    async def _search_api_endpoints(self, page, base_url: str):
        """Специальный поиск API endpoints"""
        try:
            common_endpoints = [
                f"{base_url.rstrip('/')}/embed/movie/",
                f"{base_url.rstrip('/')}/api/movie/",
                f"{base_url.rstrip('/')}/api/video/",
                f"{base_url.rstrip('/')}/api/stream/",
                f"{base_url.rstrip('/')}/movie/",
                f"{base_url.rstrip('/')}/video/",
            ]

            for endpoint in common_endpoints:
                for i in range(1, 10):
                    api_url = f"{endpoint}{i}"
                    try:
                        await page.evaluate(f"""() => {{
                            fetch('{api_url}', {{method: 'GET'}})
                                .then(response => response.json())
                                .then(data => console.log('API Response:', data))
                                .catch(err => console.log('API Error:', err));
                        }}""")
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Error searching API endpoints: {e}")

    async def _click_video_iframes(self, page):
        """Активирует iframe с видео"""
        try:
            iframes = await page.query_selector_all('iframe')
            for iframe in iframes:
                try:
                    await iframe.scroll_into_view_if_needed()
                    await asyncio.sleep(1)
                    await iframe.click()
                    await asyncio.sleep(2)
                    logger.info("Clicked on video iframe")
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Error clicking iframes: {e}")

    async def _active_immediate_scroll(self, page):
        """Активный скроллинг"""
        try:
            scroll_steps = [100, 300, 600, 900, 1200, 1500, 1800, 2100, 2400]

            for i, step in enumerate(scroll_steps):
                await page.evaluate(f"window.scrollTo(0, {step})")
                logger.info(f"Scrolled to {step}px ({i + 1}/{len(scroll_steps)})")
                await asyncio.sleep(1.5)

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            logger.info("Scrolled to bottom")
            await asyncio.sleep(2.5)

            await page.evaluate("window.scrollTo(0, 800)")
            await asyncio.sleep(1.5)

        except Exception as e:
            logger.error(f"Error in immediate scroll: {e}")

    async def _quick_activate_video(self, page):
        """Активация видео элементов"""
        try:
            await page.evaluate("""
                () => {
                    const videos = document.querySelectorAll('video');
                    videos.forEach(video => {
                        try {
                            if (video.paused) {
                                video.play().catch(e => console.log('Play failed'));
                            }
                        } catch(e) {}
                    });

                    const buttons = document.querySelectorAll('button, [role="button"], .play, .video-play');
                    buttons.forEach(btn => {
                        try {
                            const text = btn.textContent?.toLowerCase() || '';
                            const className = btn.className?.toLowerCase() || '';
                            if (text.includes('play') || text.includes('воспроизвести') || 
                                className.includes('play') || className.includes('video')) {
                                btn.click();
                            }
                        } catch(e) {}
                    });
                }
            """)

            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"Error in quick activation: {e}")

    async def _additional_scroll(self, page):
        """Дополнительный скроллинг"""
        try:
            additional_scrolls = [500, 1000, 1500, 2000]

            for i, scroll_pos in enumerate(additional_scrolls):
                await page.evaluate(f"window.scrollTo(0, {scroll_pos})")
                logger.info(f"Additional scroll to {scroll_pos}px ({i + 1}/{len(additional_scrolls)})")
                await asyncio.sleep(1.2)

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
            await page.evaluate("window.scrollTo(0, 400)")
            await asyncio.sleep(1.5)

        except Exception as e:
            logger.error(f"Error in additional scroll: {e}")

    async def _extract_thumbnail_safe(self, page, url: str) -> Optional[str]:
        """Извлекает thumbnail"""
        try:
            thumbnail = await page.evaluate("""() => {
                const metaThumbnail = document.querySelector('meta[property="og:image"]');
                if (metaThumbnail) return metaThumbnail.content;

                const poster = document.querySelector('video[poster]');
                if (poster) return poster.getAttribute('poster');

                return null;
            }""")

            if thumbnail:
                return self._make_absolute_url(thumbnail, url)

        except Exception as e:
            logger.error(f"Error extracting thumbnail: {str(e)}")

        return None

    def _make_absolute_url(self, url: str, base_url: str) -> Optional[str]:
        """Делает URL абсолютным"""
        if not url or url.startswith('blob:'):
            return None

        if url.startswith(('http://', 'https://')):
            return url if is_public_http_url(url) else None

        if url.startswith('//'):
            candidate = f'https:{url}'
            return candidate if is_public_http_url(candidate) else None

        if url.startswith('/'):
            parsed_base = urlparse(base_url)
            candidate = f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
            return candidate if is_public_http_url(candidate, resolve_dns=False) else None

        candidate = urljoin(base_url, url)
        return candidate if is_public_http_url(candidate, resolve_dns=False) else None

    def _process_formats_improved(self, formats: List[Dict], url: str, duration: int) -> List[Dict]:
        """Обрабатывает форматы"""
        if not formats:
            return []

        processed_formats = []
        for fmt in formats:
            if not isinstance(fmt, dict):
                logger.warning(f"Пропускаем невалидный формат в _process_formats_improved: {fmt} (тип: {type(fmt)})")
                continue

            try:
                format_info = {
                    'format_id': str(fmt.get('format_id', f'format_{hash(str(fmt))}')),
                    'quality': str(fmt.get('quality', 'Unknown')),
                    'filesize': fmt.get('filesize', '~'),
                    'filesize_raw': fmt.get('filesize_raw'),
                    'duration': int(duration or 0),
                    'ext': str(fmt.get('ext', 'mp4')),
                    'url': str(fmt.get('url', url)),
                    'vcodec': str(fmt.get('vcodec', 'avc1')),
                    'acodec': str(fmt.get('acodec', 'mp4a')),
                    'width': int(fmt.get('width', 0)) if fmt.get('width') else 0,
                    'height': int(fmt.get('height', 0)) if fmt.get('height') else 0,
                    'fps': int(fmt.get('fps', 0)) if fmt.get('fps') else 0,
                    'quality_score': int(fmt.get('quality_score', 0)) if fmt.get('quality_score') else 0,
                    'is_m3u8': bool(fmt.get('is_m3u8', False)),
                    'master_url': fmt.get('master_url'),
                    'quality_info': fmt.get('quality_info'),
                    'audio_tracks': fmt.get('audio_tracks', []),
                    'webpage_url': url
                }

                if not format_info['url'] or format_info['url'] == url:
                    format_info['url'] = url

                processed_formats.append(format_info)

            except Exception as e:
                logger.error(f"Ошибка обработки формата {fmt}: {e}")
                continue

        return processed_formats

    def _remove_duplicate_formats(self, formats: List[Dict]) -> List[Dict]:
        """Удаляет дубликаты форматов"""
        if not formats:
            return []

        unique_formats = []
        seen_urls = set()

        for fmt in formats:
            if not isinstance(fmt, dict):
                continue

            url = fmt.get('url')
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_formats.append(fmt)

        return unique_formats


def normalize_audio_tracks(audio_tracks):
    """Дедуплицирует озвучки по отображаемому имени (оставляем только уникальные)."""
    if not isinstance(audio_tracks, list):
        return []

    out = []
    seen_names = set()

    for i, t in enumerate(audio_tracks):
        if not isinstance(t, dict):
            continue
        name = (
            (t.get('name') or '').strip()
            or (t.get('display_name') or '').strip()
            or (t.get('technical_name') or '').strip()
            or f"Аудио {i + 1}"
        )
        if not name:
            continue
        key = name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        new_t = dict(t)
        new_t['name'] = name
        out.append(new_t)

    return out
