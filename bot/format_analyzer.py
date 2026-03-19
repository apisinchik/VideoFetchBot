import json
import yt_dlp
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def analyze_video_formats(url: str) -> Tuple[Dict, List[Dict]]:
    """Анализируем доступные форматы видео"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': False,
        'extract_flat': False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        sanitized_info = ydl.sanitize_info(info)

        logger.debug("Available formats analysis:")
        for fmt in info.get('formats', []):
            logger.debug(f"Format: {fmt.get('format_id')} - "f"vcodec: {fmt.get('vcodec')}, "
                         f"acodec: {fmt.get('acodec')}, "
                         f"height: {fmt.get('height')}, "
                         f"filesize: {fmt.get('filesize')}")

        return sanitized_info, info.get('formats', [])