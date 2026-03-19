from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from django.urls import reverse

from bot.media_utils import estimate_voice_size_bytes
from videofetcher.service import normalize_audio_tracks


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def human_filesize(size_bytes: Any) -> str:
    try:
        size = float(size_bytes)
    except Exception:
        return ""
    if size <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_duration(duration_seconds: Any) -> str:
    try:
        total = int(duration_seconds or 0)
    except Exception:
        total = 0
    if total <= 0:
        return "Неизвестно"

    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours > 0:
        return f"{hours}ч {minutes}мин {seconds}сек"
    return f"{minutes}мин {seconds}сек"


def _audio_score(fmt: dict) -> int:
    for key in ("abr", "tbr", "filesize_raw"):
        try:
            value = int(fmt.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return 0


def _get_quality_info(fmt: dict) -> dict:
    quality_info = fmt.get("quality_info")
    return quality_info if isinstance(quality_info, dict) else {}


def _get_width(fmt: dict, label: str) -> int:
    for candidate in (fmt.get("width"), _get_quality_info(fmt).get("width")):
        try:
            width = int(candidate or 0)
        except Exception:
            width = 0
        if width > 0:
            return width

    resolution = _get_quality_info(fmt).get("resolution") or fmt.get("resolution")
    if isinstance(resolution, (tuple, list)) and len(resolution) == 2:
        try:
            return int(resolution[0] or 0)
        except Exception:
            return 0
    if isinstance(resolution, str):
        match = re.search(r"(\d+)\s*[xX]", resolution)
        if match:
            return int(match.group(1))

    match = re.fullmatch(r"\s*(\d{3,4})\s*", str(label))
    return int(match.group(1)) if match else 0


def _get_height(fmt: dict, label: str) -> int:
    for candidate in (fmt.get("height"), _get_quality_info(fmt).get("height")):
        try:
            height = int(candidate or 0)
        except Exception:
            height = 0
        if height > 0:
            return height

    resolution = _get_quality_info(fmt).get("resolution") or fmt.get("resolution")
    if isinstance(resolution, (tuple, list)) and len(resolution) == 2:
        try:
            return int(resolution[1] or 0)
        except Exception:
            return 0
    if isinstance(resolution, str):
        match = re.search(r"[xX]\s*(\d+)", resolution)
        if match:
            return int(match.group(1))

    match = re.search(r"(\d{3,4})\s*p", str(label).lower())
    return int(match.group(1)) if match else 0


def _format_score(fmt: dict, height: int) -> int:
    try:
        score = int(fmt.get("quality_score") or 0)
    except Exception:
        score = 0
    if score <= 0:
        score = height or 0

    acodec = fmt.get("acodec", "none")
    if acodec and acodec != "none":
        score += 10_000

    try:
        score += int(fmt.get("fps") or 0)
    except Exception:
        pass

    return score


def _estimate_format_size(fmt: dict, duration: Optional[int]) -> str:
    filesize = fmt.get("filesize")
    if filesize and filesize != "~":
        return str(filesize)

    raw = human_filesize(fmt.get("filesize_raw"))
    if raw:
        return raw

    quality_info = _get_quality_info(fmt)
    bandwidth = quality_info.get("bandwidth")
    duration_value = fmt.get("duration") or quality_info.get("duration") or duration
    try:
        bandwidth_i = int(bandwidth or 0)
    except Exception:
        bandwidth_i = 0
    try:
        duration_i = int(duration_value or 0)
    except Exception:
        duration_i = 0

    if bandwidth_i > 0 and duration_i > 0:
        return human_filesize(int((bandwidth_i * duration_i) / 8))
    return ""


def _clean_quality_label(label: str, fmt: dict) -> str:
    cleaned = (label or "").strip()
    if cleaned:
        cleaned = re.sub(r"\b(со\s+звуком|без\s+звука)\b", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if cleaned:
        return cleaned

    height = _get_height(fmt, "")
    if height > 0:
        return f"{height}p"
    width = _get_width(fmt, "")
    if width > 0:
        return str(width)
    return "Видео"


def build_audio_only_choice_format(formats: list[dict], video_info: dict, original_url: str) -> Optional[dict]:
    if not isinstance(formats, list):
        return None

    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        audio_tracks = normalize_audio_tracks(fmt.get("audio_tracks", []))
        if not audio_tracks:
            continue

        master_url = (fmt.get("master_url") or fmt.get("url") or "").strip()
        page_url = (
            (video_info.get("webpage_url") if isinstance(video_info, dict) else None)
            or original_url
            or fmt.get("webpage_url")
            or master_url
        )
        if not master_url:
            continue

        duration = int(fmt.get("duration") or (video_info.get("duration") if isinstance(video_info, dict) else 0) or 0)
        return {
            "format_id": f"{fmt.get('format_id') or 'hls'}_audio_only",
            "quality": "Аудио",
            "filesize": "~",
            "filesize_raw": estimate_voice_size_bytes(duration),
            "ext": "m4a",
            "url": master_url,
            "master_url": master_url,
            "webpage_url": page_url,
            "duration": duration,
            "is_audio": True,
            "is_audio_choice_button": True,
            "is_hls_audio_only": True,
            "audio_tracks": audio_tracks,
        }
    return None


def build_visible_format_choices(formats: list[dict], *, duration: Optional[int] = None) -> List[dict]:
    best_video: dict[tuple, tuple[int, dict, int, int]] = {}
    best_audio: dict[tuple, tuple[int, dict, int]] = {}
    audio_choice_buttons: list[tuple[int, dict]] = []

    for index, fmt in enumerate(formats or []):
        if not isinstance(fmt, dict):
            continue

        if fmt.get("is_audio", False):
            if fmt.get("is_audio_choice_button"):
                audio_choice_buttons.append((index, fmt))
                continue
            quality = str(fmt.get("quality") or fmt.get("format_note") or "audio").strip().lower()
            key = ("audio", quality)
            score = _audio_score(fmt)
            previous = best_audio.get(key)
            if previous is None or score > previous[2]:
                best_audio[key] = (index, fmt, score)
            continue

        label = str(fmt.get("quality") or fmt.get("format_note") or fmt.get("format") or "video")
        height = _get_height(fmt, label)
        width = _get_width(fmt, label) if height <= 0 else 0
        if height > 0:
            key = ("h", height)
            sort_value = height
        elif width > 0:
            key = ("w", width)
            sort_value = width
        else:
            key = ("q", label.strip().lower())
            sort_value = 0

        score = _format_score(fmt, height)
        previous = best_video.get(key)
        if previous is None or score > previous[2]:
            best_video[key] = (index, fmt, score, sort_value)

    def sort_key(item: tuple[tuple, tuple[int, dict, int, int]]) -> tuple[int, int]:
        key, (_, _, score, sort_value) = item
        if key[0] in {"h", "w"} and isinstance(sort_value, int):
            return (-int(sort_value), -int(score))
        return (0, -int(score))

    visible: list[dict] = []

    for _, (index, fmt, _, _) in sorted(best_video.items(), key=sort_key):
        quality = _clean_quality_label(
            str(fmt.get("quality") or fmt.get("format_note") or fmt.get("format") or "Видео"),
            fmt,
        )
        size_text = _estimate_format_size(fmt, duration)
        visible.append(
            {
                "format_index": index,
                "kind": "video",
                "quality": quality,
                "label": f"🎬 {quality}{f' ({size_text})' if size_text else ''}",
                "audio_tracks": [
                    {"index": track_index, "name": track.get("name", f"Аудио {track_index + 1}")}
                    for track_index, track in enumerate(normalize_audio_tracks(fmt.get("audio_tracks", [])))
                ],
                "requires_audio_choice": bool(fmt.get("audio_tracks")),
            }
        )

    if audio_choice_buttons:
        audio_choices = audio_choice_buttons
    else:
        audio_choices = [(index, fmt) for index, fmt, _ in best_audio.values()]

    for index, fmt in audio_choices:
        quality = str(fmt.get("quality") or "Аудио")
        if fmt.get("is_audio_choice_button") or "audio" in quality.lower() or "аудио" in quality.lower():
            quality = "Аудио"
        size_text = _estimate_format_size(fmt, duration)
        visible.append(
            {
                "format_index": index,
                "kind": "audio",
                "quality": quality,
                "label": f"🎵 {quality}{f' ({size_text})' if size_text else ''}",
                "audio_tracks": [
                    {"index": track_index, "name": track.get("name", f"Аудио {track_index + 1}")}
                    for track_index, track in enumerate(normalize_audio_tracks(fmt.get("audio_tracks", [])))
                ],
                "requires_audio_choice": bool(fmt.get("audio_tracks")),
            }
        )

    return visible


def build_analysis_payload(video_info: dict, formats: list[dict], original_url: str) -> dict:
    duration = int(video_info.get("duration") or 0)
    title = video_info.get("title") or ("Фильм" if video_info.get("is_movie") else "Видео")

    audio_tracks: list[dict] = []
    for fmt in formats:
        if isinstance(fmt, dict) and fmt.get("audio_tracks"):
            audio_tracks = normalize_audio_tracks(fmt.get("audio_tracks", []))
            break

    visible_formats = build_visible_format_choices(formats, duration=duration)
    video_option_count = sum(1 for item in visible_formats if item.get("kind") == "video")

    return {
        "title": title,
        "source_url": original_url,
        "duration_seconds": duration,
        "duration_text": format_duration(duration),
        "is_movie": bool(video_info.get("is_movie")),
        "total_audio_tracks": len(audio_tracks),
        "video_option_count": video_option_count,
        "formats": visible_formats,
    }


def _stage_label(stage: Optional[str], status: str) -> str:
    value = (stage or status or "").strip().lower()
    mapping = {
        "queued": "В очереди",
        "starting": "Подготовка",
        "downloading": "Скачивание",
        "finalizing": "Сборка файла",
        "done": "Готово",
        "failed": "Ошибка",
        "canceled": "Отменено",
    }
    return mapping.get(value, value or "Ожидание")


def _status_label(status: str) -> str:
    mapping = {
        "queued": "Ожидает",
        "running": "В работе",
        "done": "Готово",
        "failed": "Ошибка",
        "canceled": "Отменено",
    }
    return mapping.get((status or "").lower(), status or "Неизвестно")


def _public_error_message(job) -> Optional[str]:
    raw = (getattr(job, "error_message", None) or "").strip()
    if not raw:
        return None

    lowered = raw.lower()
    if "youtube_auth_required" in lowered or ("sign in to confirm" in lowered and "not a bot" in lowered):
        return "YouTube запросил авторизацию или подтверждение." 
    if "failed to download hls stream" in lowered:
        return "Не удалось скачать HLS-поток." 
    if "download_video returned empty path" in lowered:
        return "Сервер не сохранил итоговый файл." 
    return "Не удалось обработать загрузку. Попробуйте снова позже."


def serialize_job(job) -> dict:
    selected_format = job.selected_format or {}
    selected_audio = job.selected_audio or {}
    media_kind = "audio" if selected_format.get("is_audio") else "video"
    quality = job.requested_quality or selected_format.get("quality") or selected_format.get("format_id") or "Видео"
    audio_name = job.requested_audio or selected_audio.get("name")
    result_size_text = human_filesize(getattr(job, "result_size_bytes", None))
    download_ready = bool(job.status == "done" and job.result_path)

    return {
        "id": job.id,
        "source_url": job.source_url,
        "title": job.title or selected_format.get("title") or "Видео",
        "duration_text": format_duration(job.duration_seconds),
        "quality": str(quality),
        "audio_name": audio_name,
        "status": job.status,
        "status_label": _status_label(job.status),
        "stage": job.stage or job.status,
        "stage_label": _stage_label(job.stage, job.status),
        "progress": int(job.progress or 0),
        "media_kind": media_kind,
        "result_size_text": result_size_text,
        "download_ready": download_ready,
        "download_url": reverse("job_download", args=[job.id]) if download_ready else "",
        "error_message": _public_error_message(job),
        "can_poll": job.status in {"queued", "running"},
    }
