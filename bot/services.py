import yt_dlp
import os
import logging
import asyncio
import random
from typing import Tuple, List, Dict, Optional
from config import Config
from proxy_checker import ProxyChecker

logger = logging.getLogger(__name__)


class VideoService:
    def __init__(self):
        self.temp_dir = Config.TEMP_DIR
        self.proxy_url = Config.PROXY_URL
        self.proxy_checker = ProxyChecker()
        self.use_proxy = True
        os.makedirs(self.temp_dir, exist_ok=True)

        # Базовые опции yt-dlp
        self.ydl_opts = {
            'quiet': False,
            'no_warnings': False,
            'extract_flat': False,
        }

    async def initialize(self):
        """Инициализация сервиса с проверкой прокси"""
        logger.info("Initializing VideoService...")

        if self.proxy_url:
            is_working, message = await self.proxy_checker.check_proxy(self.proxy_url)
            if is_working:
                logger.info(f"✅ Proxy is working: {message}")
                self.use_proxy = True
            else:
                logger.warning(f"❌ Proxy not working: {message}")
                if await self.proxy_checker.test_direct_connection():
                    logger.info("✅ Direct connection is available, disabling proxy")
                    self.use_proxy = False
                else:
                    logger.error("❌ No network connection available")
                    return False
        else:
            logger.info("No proxy configured, using direct connection")
            self.use_proxy = False

        return True

    def _get_ydl_opts(self, download: bool = False, format_id: str = None) -> Dict:
        """Получаем опции для yt-dlp"""
        opts = self.ydl_opts.copy()

        if self.use_proxy and self.proxy_url:
            opts['proxy'] = self.proxy_url
            logger.info("Using SOCKS proxy for download")
        else:
            logger.info("Using direct connection (no proxy)")

        opts.update({
            'socket_timeout': Config.CONNECTION_TIMEOUT,
            'extractor_retries': Config.MAX_RETRIES,
            'retries': Config.MAX_RETRIES,
            'fragment_retries': Config.MAX_RETRIES,
            'ignoreerrors': True,
            'no_check_certificate': True,
            'prefer_insecure': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
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

        return opts

    async def extract_video_info(self, url: str) -> Tuple[Optional[Dict], Optional[List], str]:
        """Извлекаем информацию о видео с повторными попытками"""
        for attempt in range(Config.MAX_RETRIES):
            try:
                logger.info(f"Extracting video info (attempt {attempt + 1})")

                ydl_opts = self._get_ydl_opts(download=False)
                ydl_opts.update({
                    'socket_timeout': Config.EXTRACTION_TIMEOUT,
                })

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await asyncio.get_event_loop().run_in_executor(
                        None, ydl.extract_info, url, False
                    )

                    if not info:
                        if attempt < Config.MAX_RETRIES - 1:
                            await asyncio.sleep(Config.RETRY_DELAY * (attempt + 1))
                            continue
                        return None, None, "video_info_error"

                    # Обрабатываем форматы
                    formats = self._process_formats(info.get('formats', []), url, info.get('duration', 0))

                    if not formats:
                        if attempt < Config.MAX_RETRIES - 1:
                            await asyncio.sleep(Config.RETRY_DELAY * (attempt + 1))
                            continue
                        return None, None, "no_formats_found"

                    logger.info(f"Successfully extracted info with {len(formats)} formats")
                    return info, formats, "success"

            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                logger.warning(f"DownloadError (attempt {attempt + 1}): {error_msg}")

                if any(err in error_msg for err in ['SSL', 'timeout', 'socket', 'connection']):
                    if self.use_proxy and attempt == 1:
                        logger.info("Network error detected, disabling proxy for next attempt")
                        self.use_proxy = False

                if attempt < Config.MAX_RETRIES - 1:
                    delay = Config.RETRY_DELAY * (attempt + 1) + random.uniform(0, 1)
                    await asyncio.sleep(delay)
                    continue

                return None, None, f"download_error: {error_msg}"

            except Exception as e:
                logger.error(f"Unexpected error (attempt {attempt + 1}): {str(e)}")
                if attempt < Config.MAX_RETRIES - 1:
                    await asyncio.sleep(Config.RETRY_DELAY * (attempt + 1))
                    continue
                return None, None, f"error: {str(e)}"

        return None, None, "max_retries_exceeded"

    def _process_formats(self, formats: List[Dict], url: str, duration: int) -> List[Dict]:
        """Обрабатываем и фильтруем форматы (без ограничений по размеру)"""
        video_audio_formats = []
        audio_formats = []

        logger.info(f"Processing {len(formats)} total formats")

        for fmt in formats:
            format_id = fmt.get('format_id', '')
            vcodec = fmt.get('vcodec', 'none')
            acodec = fmt.get('acodec', 'none')

            # Пропускаем форматы без видео и аудио
            if vcodec == 'none' and acodec == 'none':
                continue

            # Рассчитываем размер файла
            filesize = fmt.get('filesize') or fmt.get('filesize_approx')
            if not filesize and duration > 0:
                # Расчет примерного размера на основе битрейта
                tbr = fmt.get('tbr', 0)  # общий битрейт
                if tbr:
                    filesize = (tbr * 1000 * duration) / 8  # в байтах
                else:
                    # Для аудио используем abr
                    abr = fmt.get('abr', 0)
                    if abr:
                        filesize = (abr * 1000 * duration) / 8

            # УБРАЛ ОГРАНИЧЕНИЕ ПО РАЗМЕРУ ФАЙЛА - теперь скачиваем файлы любого размера
            format_info = {
                'format_id': format_id,
                'quality': self._format_quality(fmt),
                'filesize': self._format_filesize(filesize),
                'ext': fmt.get('ext', 'mp4'),
                'url': url,
                'vcodec': vcodec,
                'acodec': acodec,
                'filesize_raw': filesize,
                'height': fmt.get('height', 0) or 0,
                'fps': fmt.get('fps', 0) or 0,
                'quality_score': self._get_quality_score(fmt)
            }

            # Разделяем видео+аудио и только аудио
            if vcodec != 'none' and acodec != 'none':
                video_audio_formats.append(format_info)
            elif vcodec == 'none' and acodec != 'none':
                audio_formats.append(format_info)

        logger.info(f"Found {len(video_audio_formats)} video+audio formats and {len(audio_formats)} audio formats")

        # Сортируем видео+аудио форматы по качеству (от наилучшего к худшему)
        video_audio_formats.sort(key=lambda x: x['quality_score'], reverse=True)

        # Сортируем аудио форматы по качеству и берем лучший
        if audio_formats:
            audio_formats.sort(key=lambda x: self._get_audio_quality_score(x), reverse=True)
            best_audio = audio_formats[0]
            best_audio['quality'] = '🎵 Аудио'
            # Добавляем лучший аудио формат в конец списка
            video_audio_formats.append(best_audio)

        logger.info(f"Final processed formats: {len(video_audio_formats)}")

        return video_audio_formats

    def _format_quality(self, fmt: Dict) -> str:
        """Форматируем информацию о качестве"""
        quality_parts = []

        # Добавляем только разрешение
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

        # Разрешение (самый важный фактор)
        height = fmt.get('height') or 0
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

        # FPS (учитываем в сортировке, но не показываем)
        fps = fmt.get('fps') or 0
        if fps >= 60:
            score += 200
        elif fps >= 50:
            score += 150
        elif fps >= 30:
            score += 100
        elif fps > 0:
            score += 50

        # Наличие аудио
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

    async def download_video(self, format_info: Dict, user_id: int) -> str:
        """Скачиваем видео с обработкой ошибок (увеличил таймауты для больших файлов)"""
        for attempt in range(Config.MAX_RETRIES):
            try:
                logger.info(f"Downloading video (attempt {attempt + 1})")

                ydl_opts = self._get_ydl_opts(
                    download=True,
                    format_id=format_info['format_id']
                )

                # Увеличиваем таймауты для больших файлов
                ydl_opts.update({
                    'socket_timeout': Config.DOWNLOAD_TIMEOUT * 2,  # Удвоили таймаут
                    'retries': 5,
                    'fragment_retries': 5,
                })

                # Для аудио добавляем постобработку
                if 'Аудио' in format_info['quality']:
                    ydl_opts.update({
                        'format': 'bestaudio/best',
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }],
                    })

                output_template = os.path.join(self.temp_dir, f"{user_id}_%(id)s.%(ext)s")
                ydl_opts['outtmpl'] = output_template

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    error_code = await asyncio.get_event_loop().run_in_executor(
                        None, ydl.download, [format_info['url']]
                    )

                    if error_code:
                        logger.warning(f"Download completed with error code: {error_code}")

                # Ищем скачанный файл
                downloaded_file = self._find_downloaded_file(user_id, format_info['url'])
                if downloaded_file:
                    logger.info(f"Successfully downloaded: {downloaded_file}")
                    return downloaded_file
                else:
                    raise Exception("Downloaded file not found")

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Download error (attempt {attempt + 1}): {error_msg}")

                if any(err in error_msg for err in ['SSL', 'timeout', 'socket']):
                    if self.use_proxy and attempt == 1:
                        logger.info("Network error during download, disabling proxy")
                        self.use_proxy = False

                if attempt < Config.MAX_RETRIES - 1:
                    delay = Config.RETRY_DELAY * (attempt + 1) + random.uniform(0, 1)
                    await asyncio.sleep(delay)
                    continue

                raise e

        raise Exception("All download attempts failed")

    def _find_downloaded_file(self, user_id: int, url: str) -> Optional[str]:
        """Находим скачанный файл"""
        try:
            # Извлекаем ID видео из URL
            video_id = None
            if 'youtube.com/watch?v=' in url:
                video_id = url.split('youtube.com/watch?v=')[1].split('&')[0]
            elif 'youtu.be/' in url:
                video_id = url.split('youtu.be/')[1].split('?')[0]

            # Ищем файлы пользователя
            for file in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, file)
                if (file.startswith(f"{user_id}_") and
                        os.path.isfile(file_path) and
                        not file.endswith('.part') and
                        not file.endswith('.ytdl') and
                        not file.endswith('.tmp')):

                    if video_id and video_id not in file:
                        continue

                    return file_path

        except Exception as e:
            logger.error(f"Error finding downloaded file: {str(e)}")

        return None

    def _format_duration(self, duration_seconds: int) -> str:
        """Форматируем длительность"""
        if not duration_seconds:
            return "Unknown"

        hours = duration_seconds // 3600
        minutes = (duration_seconds % 3600) // 60
        seconds = duration_seconds % 60

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"

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