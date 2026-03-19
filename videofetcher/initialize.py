from config import Config

from videofetcher import VideoFetcherSettings, VideoService

video_service: VideoService | None = None

def get_videofetcher() -> VideoService:
    global video_service
    if video_service is None:
        settings = VideoFetcherSettings(
            temp_dir=Config.TEMP_DIR,
            proxy_url=Config.PROXY_URL,
            force_proxy_download=Config.FORCE_PROXY_DOWNLOAD,
            extraction_timeout=getattr(Config, "EXTRACTION_TIMEOUT", 30),
            download_timeout=getattr(Config, "DOWNLOAD_TIMEOUT", 300),
            connection_timeout=getattr(Config, "CONNECTION_TIMEOUT", 10),
            max_retries=getattr(Config, "MAX_RETRIES", 3),
            retry_delay=getattr(Config, "RETRY_DELAY", 2),
        )
        video_service = VideoService(settings=settings)
        return video_service
    else:
        return video_service
    

async def init_videofetcher():
    if video_service is None:
        get_videofetcher()
    if not await video_service.initialize():
        raise RuntimeError('VideoService init failed')
    return video_service