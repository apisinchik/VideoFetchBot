import asyncio
import datetime
import logging
import os
import shutil
from typing import Dict, List, Optional

import yt_dlp

from .hls_downloader import HLSVideoDownloader

logger = logging.getLogger(__name__)


class VideoServiceDownloadMixin:
    async def download_video(
        self,
        format_info: Dict,
        user_id: int,
        progress_callback=None,
        audio_track: Dict = None,
        source_url: str | None = None,
    ) -> str:
        """Скачивает видео."""
        if not isinstance(format_info, dict):
            raise Exception("Invalid format_info: expected dict, got " + str(type(format_info)))

        raw_url = (format_info.get('url') or '').strip()
        page_url = (source_url or format_info.get('webpage_url') or '').strip()
        if not raw_url and not page_url:
            raise Exception("No URL found in format_info")

        is_hls = bool(format_info.get('is_m3u8', False))
        is_audio = bool(format_info.get('is_audio', False))
        is_hls_audio_only = bool(format_info.get('is_hls_audio_only', False))

        logger.info(f"Скачивание: HLS={is_hls}, Audio={is_audio}, HLSAudioOnly={is_hls_audio_only}")

        proxy_available = bool(self.proxy_url)
        force_proxy = bool(getattr(self.settings, 'force_proxy_download', False))

        if proxy_available and not force_proxy:
            proxy_plan = [False, True]
        elif proxy_available and force_proxy:
            proxy_plan = [True]
        else:
            proxy_plan = [False]

        last_err: Exception | None = None

        for use_proxy in proxy_plan:
            try:
                fmt = dict(format_info)
                if page_url:
                    fmt['webpage_url'] = page_url

                if is_hls_audio_only:
                    result = await self._download_m3u8_audio_only(
                        fmt,
                        user_id,
                        progress_callback,
                        audio_track,
                        use_proxy=use_proxy,
                    )
                elif is_hls:
                    result = await self._download_m3u8_video_improved(
                        fmt,
                        user_id,
                        progress_callback,
                        audio_track,
                        use_proxy=use_proxy,
                    )
                elif is_audio:
                    result = await self._download_audio_with_ytdlp(
                        fmt,
                        user_id,
                        progress_callback,
                        use_proxy=use_proxy,
                        source_url=page_url,
                    )
                else:
                    result = await self._download_with_ytdlp_direct(
                        fmt,
                        user_id,
                        progress_callback,
                        audio_track=audio_track,
                        use_proxy=use_proxy,
                        source_url=page_url,
                    )

                if result and os.path.exists(result):
                    logger.info("Успешно скачано")
                    return result

                raise Exception("Не удалось скачать видео")

            except Exception as e:
                last_err = e
                logger.error(f"Ошибка скачивания (proxy={use_proxy}): {e}")
                continue

        if last_err:
            raise last_err
        raise Exception("Не удалось скачать видео")

    async def _download_m3u8_audio_only(
        self,
        format_info: Dict,
        user_id: int,
        progress_callback=None,
        audio_track: Dict = None,
        use_proxy: bool = False,
    ) -> str:
        master_url = format_info.get('master_url') or format_info.get('url')
        if not master_url:
            raise Exception("No M3U8 URL provided for audio-only download")

            logger.info(f"Downloading HLS audio-only from: {master_url} (proxy: {use_proxy})")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ffmpeg_available = bool(shutil.which("ffmpeg"))
        output_ext = "m4a" if ffmpeg_available else "ts"
        output_path = os.path.join(self.temp_dir, f"{user_id}_{timestamp}.{output_ext}")
        proxy_url = self.proxy_url if use_proxy else None

        audio_tracks = format_info.get('audio_tracks') if isinstance(format_info.get('audio_tracks'), list) else []
        selected_audio = audio_track if isinstance(audio_track, dict) else None
        if not selected_audio and audio_tracks:
            selected_audio = audio_tracks[0]
        if not selected_audio:
            raise Exception("No audio track selected for audio-only download")

        async with HLSVideoDownloader(proxy_url=proxy_url) as downloader:
            success = await downloader.download_audio_only_stream(
                master_url=master_url,
                output_filename=output_path,
                selected_audio=selected_audio,
                progress_callback=progress_callback,
                referer=format_info.get('webpage_url', ''),
                duration_s=int(format_info.get('duration') or 0),
                available_audio_tracks=audio_tracks,
            )

        if success and os.path.exists(output_path):
            logger.info(f"Successfully downloaded audio-only to: {output_path}")
            return output_path
        raise Exception("Failed to download HLS audio-only stream")

    async def _download_m3u8_video_improved(self, format_info: Dict, user_id: int, progress_callback=None,
                                            audio_track: Dict = None, use_proxy: bool = False) -> str:
        """Улучшенное скачивание M3U8 видео через HLSVideoDownloader"""
        master_url = format_info.get('master_url') or format_info.get('url')
        if not master_url:
            raise Exception("No M3U8 URL provided")

            logger.info(f"Downloading M3U8 video from: {master_url} (proxy: {use_proxy})")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(self.temp_dir, f"{user_id}_{timestamp}.mp4")

        proxy_url = self.proxy_url if use_proxy else None

        async with HLSVideoDownloader(proxy_url=proxy_url) as downloader:
            selected_quality = format_info.get('quality_info') if isinstance(format_info.get('quality_info'), dict) else None
            if not selected_quality:
                selected_quality = {
                    'url': master_url,
                    'resolution': format_info.get('quality') or f"{format_info.get('height', 'Unknown')}p"
                }
            else:
                selected_quality = dict(selected_quality)
                selected_quality.setdefault('url', master_url)

            audio_tracks = format_info.get('audio_tracks') if isinstance(format_info.get('audio_tracks'), list) else []
            downloader_audio_tracks = audio_tracks or None
            selected_audio = None
            if audio_track and isinstance(audio_track, dict):
                selected_audio = audio_track
            elif audio_tracks:
                selected_audio = audio_tracks[0]
            elif downloader_audio_tracks is None:
                logger.info("No HLS audio tracks in selected format; downloader will inspect master playlist")

            quality_name = selected_quality.get('resolution', 'Unknown')
            audio_name = selected_audio.get('name', 'no') if selected_audio else 'no'
            logger.info(f"Downloading: {quality_name} with {audio_name} audio")

            use_external_audio = bool(selected_audio and selected_audio.get('download_via_ytdlp'))
            if use_external_audio and not shutil.which("ffmpeg"):
                raise Exception("ffmpeg is required to merge external audio tracks")

            if use_external_audio:
                video_only_path = os.path.splitext(output_path)[0] + ".video_only.mp4"
                success = await downloader.download_complete_stream(
                    master_url=master_url,
                    output_filename=video_only_path,
                    selected_quality=selected_quality,
                    selected_audio=None,
                    progress_callback=progress_callback,
                    referer=format_info.get('webpage_url', ''),
                    duration_s=int(format_info.get('duration') or 0),
                    available_audio_tracks=downloader_audio_tracks,
                )
                if not success or not os.path.exists(video_only_path):
                    raise Exception("Failed to download video-only HLS stream")

                audio_fmt = dict(selected_audio)
                audio_fmt.setdefault('webpage_url', format_info.get('webpage_url'))
                audio_path = await self._download_audio_with_ytdlp(
                    audio_fmt,
                    user_id,
                    progress_callback,
                    use_proxy=use_proxy,
                    source_url=format_info.get('webpage_url', ''),
                )
                if not audio_path or not os.path.exists(audio_path):
                    raise Exception("Failed to download external audio track")

                merged = self._merge_with_ffmpeg(video_only_path, audio_path, output_path)
                try:
                    if os.path.exists(video_only_path):
                        os.remove(video_only_path)
                except Exception:
                    pass
                try:
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                except Exception:
                    pass
                if not merged:
                    raise Exception("Failed to merge video and audio with ffmpeg")
                success = True
            else:
                success = await downloader.download_complete_stream(
                    master_url=master_url,
                    output_filename=output_path,
                    selected_quality=selected_quality,
                    selected_audio=selected_audio,
                    progress_callback=progress_callback,
                    referer=format_info.get('webpage_url', ''),
                    duration_s=int(format_info.get('duration') or 0),
                    available_audio_tracks=downloader_audio_tracks,
                )

            if success and os.path.exists(output_path):
                logger.info(f"Successfully downloaded to: {output_path}")

                return output_path
            else:
                raise Exception("Failed to download HLS stream")

    async def _download_audio_with_ytdlp(
        self,
        format_info: Dict,
        user_id: int,
        progress_callback=None,
        use_proxy: bool = False,
        source_url: str | None = None,
    ) -> str:
        """Скачивает аудио через yt-dlp."""
        try:
            retry_on_auth = bool(getattr(self.settings, "ytdlp_retry_on_auth_error", True))
            retry_on_forbidden = bool(getattr(self.settings, "ytdlp_retry_on_forbidden_error", True))

            url = (source_url or format_info.get('webpage_url') or format_info.get('url') or '').strip()
            if not url:
                raise Exception("No URL provided for audio download")

                logger.info(f"Скачивание аудио: {url} (proxy: {use_proxy})")

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_template = os.path.join(self.temp_dir, f"{user_id}_{timestamp}.%(ext)s")

            ydl_opts = {
                'quiet': False,
                'no_warnings': False,
                'socket_timeout': 60,
                'retries': 3,
                'fragment_retries': 10,
                'ignoreerrors': False,
                'noplaylist': True,
                'noprogress': True,
                'skip_unavailable_fragments': False,
                'abort_on_unavailable_fragments': True,
                'format': format_info.get('format_id') or 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': output_template,
                'http_headers': self._build_ydl_http_headers(),
            }

            self._apply_referer_headers(ydl_opts, source_url or format_info.get('webpage_url'))

            if use_proxy and self.proxy_url:
                ydl_opts['proxy'] = self.proxy_url
                logger.info("Using proxy for yt-dlp audio download")
            else:
                logger.info("Using direct connection for yt-dlp audio download")

            cookie_retry_done = False
            await self._ensure_cookiefile()

            for attempt in range(3):
                try:
                    self._apply_ytdlp_js_challenge_opts(ydl_opts)
                    self._apply_ytdlp_cookie_source(ydl_opts)

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        await asyncio.to_thread(ydl.download, [url])

                    downloaded_file = self._find_downloaded_file(user_id, url, timestamp)
                    if downloaded_file:
                        logger.info(f"Аудио скачано: {downloaded_file}")
                        return downloaded_file
                    raise Exception("Downloaded audio file not found")

                except Exception as e:
                    err = str(e)
                    if self._is_youtube_auth_error(err):
                        if not cookie_retry_done and retry_on_auth:
                            cookie_retry_done = True
                            refreshed = await self._refresh_cookiefile()
                            if not refreshed:
                                await self._wait_for_cookiefile_update()
                            continue
                        raise RuntimeError("youtube_auth_required") from e

                    if self._is_forbidden_fragment_error(err) and retry_on_forbidden and not cookie_retry_done:
                        cookie_retry_done = True
                        refreshed = await self._refresh_cookiefile()
                        if not refreshed:
                            await self._wait_for_cookiefile_update()
                        continue

                    raise

            raise Exception("Audio download failed after retries")

        except Exception as e:
            logger.error(f"Ошибка скачивания аудио: {e}")
            raise

    async def _download_with_ytdlp_direct(
        self,
        format_info: Dict,
        user_id: int,
        progress_callback=None,
        audio_track: Dict = None,
        use_proxy: bool = True,
        source_url: str | None = None,
    ) -> str:
        """Скачивает через yt-dlp."""
        try:
            retry_on_auth = bool(getattr(self.settings, "ytdlp_retry_on_auth_error", True))
            retry_on_forbidden = bool(getattr(self.settings, "ytdlp_retry_on_forbidden_error", True))
            fallback_mp4 = bool(getattr(self.settings, "ytdlp_fallback_to_mp4_on_hls_403", True))

            page_url = (source_url or format_info.get('webpage_url') or '').strip()
            direct_url = (format_info.get('url') or '').strip()

            url = page_url or direct_url
            if not url:
                raise Exception("No URL provided for download")

                logger.info(f"yt-dlp download: {url} (use_proxy={use_proxy})")

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_template = os.path.join(self.temp_dir, f"{user_id}_{timestamp}.%(ext)s")

            selected_height = 0
            try:
                selected_height = int(format_info.get('height') or 0)
            except Exception:
                selected_height = 0
            is_youtube = self._is_youtube_url(page_url or direct_url)
            selected_external_audio = self._get_external_audio_track(format_info, audio_track)
            generic_non_hls_selector = self._build_non_hls_format_selector(selected_height)
            youtube_non_hls_selector = self._build_non_hls_format_selector(selected_height, prefer_mp4=True)
            if selected_external_audio and format_info.get('format_id'):
                audio_format_id = (selected_external_audio.get('format_id') or '').strip()
                if audio_format_id:
                    requested_format = f"{format_info['format_id']}+{audio_format_id}"
                    non_hls_fallback = requested_format
                else:
                    requested_format = format_info['format_id']
                    non_hls_fallback = requested_format
            elif is_youtube:
                requested_format = youtube_non_hls_selector
                non_hls_fallback = generic_non_hls_selector
            else:
                requested_format = format_info.get('format_id') or generic_non_hls_selector
                non_hls_fallback = generic_non_hls_selector

            ydl_opts = {
                'quiet': False,
                'no_warnings': False,
                'socket_timeout': 60,
                'retries': 3,
                'fragment_retries': 10,
                'ignoreerrors': False,
                'noplaylist': True,
                'noprogress': True,
                'outtmpl': output_template,
                'format': requested_format,
                'skip_unavailable_fragments': False,
                'abort_on_unavailable_fragments': True,
                'http_headers': self._build_ydl_http_headers(),
            }
            if self._should_force_mp4_merge(format_info, selected_external_audio, is_youtube):
                ydl_opts['merge_output_format'] = 'mp4'

            self._apply_referer_headers(ydl_opts, page_url or format_info.get('webpage_url'))

            if use_proxy and self.proxy_url:
                ydl_opts['proxy'] = self.proxy_url
                logger.info("Using proxy for yt-dlp download")
            else:
                logger.info("Using direct connection for yt-dlp download")

            cookie_retry_done = False
            mp4_fallback_used = False
            await self._ensure_cookiefile()

            for _ in range(3):
                try:
                    self._apply_ytdlp_js_challenge_opts(ydl_opts)
                    self._apply_ytdlp_cookie_source(ydl_opts)

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        await asyncio.to_thread(ydl.download, [url])

                    downloaded_file = self._find_downloaded_file(user_id, url, timestamp)
                    if downloaded_file:
                        logger.info(f"Видео скачано: {downloaded_file}")
                        return downloaded_file
                    raise Exception("Downloaded file not found")

                except Exception as e:
                    err = str(e)

                    if self._is_youtube_auth_error(err):
                        if not cookie_retry_done and retry_on_auth:
                            cookie_retry_done = True
                            refreshed = await self._refresh_cookiefile()
                            if not refreshed:
                                await self._wait_for_cookiefile_update()
                            continue
                        raise RuntimeError("youtube_auth_required") from e

                    if self._is_forbidden_fragment_error(err):
                        if retry_on_forbidden and not cookie_retry_done:
                            cookie_retry_done = True
                            refreshed = await self._refresh_cookiefile()
                            if not refreshed:
                                await self._wait_for_cookiefile_update()
                            continue

                        if fallback_mp4 and not mp4_fallback_used:
                            mp4_fallback_used = True
                            ydl_opts['format'] = non_hls_fallback
                            logger.warning("HLS fragments forbidden/empty. Retrying with non-HLS fallback format..."
                            )
                            continue

                    raise

            raise Exception("Download failed after retries")

        except Exception as e:
            logger.error(f"Ошибка прямого скачивания: {e}")
            raise

    def _get_external_audio_track(self, format_info: Dict, selected_audio: Optional[Dict]) -> Optional[Dict]:
        if isinstance(selected_audio, dict) and selected_audio.get("download_via_ytdlp"):
            return selected_audio

        default_audio = format_info.get("default_audio_track")
        if isinstance(default_audio, dict) and default_audio.get("download_via_ytdlp"):
            return default_audio

        return None

    def _should_force_mp4_merge(
        self,
        format_info: Dict,
        external_audio: Optional[Dict],
        is_youtube: bool,
    ) -> bool:
        if not external_audio:
            return is_youtube

        video_ext = (format_info.get("ext") or "").lower()
        audio_ext = (external_audio.get("ext") or "").lower()
        return video_ext == "mp4" and audio_ext in {"m4a", "mp4"}

    def _build_non_hls_format_selector(self, max_height: int = 0, prefer_mp4: bool = False) -> str:
        """Собирает универсальный yt-dlp selector без HLS-потоков."""
        height_filter = f"[height<={max_height}]" if max_height > 0 else ""
        selectors: List[str] = []

        if prefer_mp4:
            selectors.extend([
                f"bestvideo[ext=mp4][vcodec^=avc1]{height_filter}[protocol!*=m3u8]+bestaudio[ext=m4a][protocol!*=m3u8]",
                f"bestvideo[ext=webm]{height_filter}[protocol!*=m3u8]+bestaudio[ext=webm][protocol!*=m3u8]",
            ])

        selectors.extend([
            f"bestvideo{height_filter}[protocol!*=m3u8]+bestaudio[protocol!*=m3u8]",
            f"best{height_filter}[protocol!*=m3u8]",
            "best",
        ])
        return "/".join(selectors)

    def _find_downloaded_file(self, user_id: int, url: str, timestamp: str = None) -> Optional[str]:
        """Находим скачанный файл"""
        try:
            logger.info(f"Поиск файла для user_id: {user_id}, timestamp: {timestamp}")

            if timestamp:
                for file in os.listdir(self.temp_dir):
                    file_path = os.path.join(self.temp_dir, file)
                    if (file.startswith(f"{user_id}_{timestamp}") and
                            os.path.isfile(file_path) and
                            not file.endswith('.part') and
                            not file.endswith('.ytdl') and
                            not file.endswith('.tmp')):
                        logger.info(f"Найден файл по timestamp: {file_path}")
                        return file_path

            user_files = []
            for file in os.listdir(self.temp_dir):
                if file.startswith(f"{user_id}_"):
                    file_path = os.path.join(self.temp_dir, file)
                    if (os.path.isfile(file_path) and
                            not file.endswith('.part') and
                            not file.endswith('.ytdl') and
                            not file.endswith('.tmp')):
                        user_files.append((file_path, os.path.getmtime(file_path)))

            user_files.sort(key=lambda x: x[1], reverse=True)

            if user_files:
                logger.info(f"Найден файл по user_id: {user_files[0][0]}")
                return user_files[0][0]

            files_in_temp = os.listdir(self.temp_dir)
            logger.info(f"Содержимое temp_dir: {files_in_temp}")

            logger.warning(f"Не найдено подходящих файлов для user_id: {user_id}")

        except Exception as e:
            logger.error(f"Error finding downloaded file: {str(e)}")

        return None

    async def cleanup_user_files(self, user_id: int):
        """Очищаем временные файлы пользователя"""
        try:
            if os.path.exists(self.temp_dir):
                for file in os.listdir(self.temp_dir):
                    if file.startswith(f"{user_id}_") and os.path.isfile(os.path.join(self.temp_dir, file)):
                        os.remove(os.path.join(self.temp_dir, file))
        except Exception as e:
            logger.error(f"Error cleaning up user files: {str(e)}")

    async def close(self):
        """Закрываем сервис"""
        pass

    def get_user_files(self, user_id: int) -> List[str]:
        """Возвращает список файлов пользователя во временной директории"""
        try:
            user_files = []
            if os.path.exists(self.temp_dir):
                for file in os.listdir(self.temp_dir):
                    if file.startswith(f"{user_id}_") and os.path.isfile(os.path.join(self.temp_dir, file)):
                        file_path = os.path.join(self.temp_dir, file)
                        user_files.append(file_path)

            user_files.sort(key=lambda x: os.path.getctime(x), reverse=True)
            return user_files
        except Exception as e:
            logger.error(f"Error getting user files: {str(e)}")
            return []

    def get_user_files_info(self, user_id: int) -> List[Dict]:
        """Возвращает подробную информацию о файлах пользователя"""
        try:
            files_info = []
            user_files = self.get_user_files(user_id)

            for file_path in user_files:
                try:
                    file_size = os.path.getsize(file_path)
                    file_size_mb = file_size / (1024 * 1024)
                    created_time = os.path.getctime(file_path)
                    created_date = datetime.datetime.fromtimestamp(created_time).strftime("%Y-%m-%d %H:%M:%S")

                    file_info = {
                        'path': file_path,
                        'filename': os.path.basename(file_path),
                        'size_bytes': file_size,
                        'size_mb': round(file_size_mb, 2),
                        'created': created_date,
                        'extension': os.path.splitext(file_path)[1].lower()
                    }
                    files_info.append(file_info)
                except Exception as e:
                    logger.error(f"Error getting info for file {file_path}: {e}")
                    continue

            return files_info
        except Exception as e:
            logger.error(f"Error getting user files info: {str(e)}")
            return []

    def get_latest_user_file(self, user_id: int) -> Optional[str]:
        """Возвращает самый новый файл пользователя"""
        try:
            user_files = self.get_user_files(user_id)
            if user_files:
                return user_files[0]
            return None
        except Exception as e:
            logger.error(f"Error getting latest user file: {str(e)}")
            return None
