from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import logging
import re
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


def _human_filesize(size_bytes) -> str:
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


def _audio_score(fmt: dict) -> int:
    try:
        abr = int(fmt.get('abr') or 0)
    except Exception:
        abr = 0
    try:
        tbr = int(fmt.get('tbr') or 0)
    except Exception:
        tbr = 0
    try:
        raw = int(fmt.get('filesize_raw') or 0)
    except Exception:
        raw = 0
    return abr or tbr or raw


def create_quality_keyboard(formats, duration: Optional[int] = None):
    """Клавиатура выбора качества."""
    builder = InlineKeyboardBuilder()

    video_formats = []
    audio_formats = []
    audio_choice_buttons = []

    def _get_width_from_fmt(fmt: dict, q_label: str) -> int:
        import re

        try:
            w = int(fmt.get('width') or 0)
            if w > 0:
                return w
        except Exception:
            pass

        qi = fmt.get('quality_info') if isinstance(fmt.get('quality_info'), dict) else {}
        try:
            w = int(qi.get('width') or 0)
            if w > 0:
                return w
        except Exception:
            pass

        res = qi.get('resolution') or fmt.get('resolution')
        if isinstance(res, (tuple, list)) and len(res) == 2:
            try:
                w = int(res[0] or 0)
                if w > 0:
                    return w
            except Exception:
                pass
        if isinstance(res, str):
            m = re.search(r"(\d+)\s*[xX]", res)
            if m:
                try:
                    w = int(m.group(1))
                    if w > 0:
                        return w
                except Exception:
                    pass

        m = re.fullmatch(r"\s*(\d{3,4})\s*", str(q_label))
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return 0

        return 0

    def _get_height_from_fmt(fmt: dict, q_label: str) -> int:
        import re

        try:
            h = int(fmt.get('height') or 0)
            if h > 0:
                return h
        except Exception:
            pass

        qi = fmt.get('quality_info') if isinstance(fmt.get('quality_info'), dict) else {}
        try:
            h = int(qi.get('height') or 0)
            if h > 0:
                return h
        except Exception:
            pass

        res = qi.get('resolution') or fmt.get('resolution')
        if isinstance(res, (tuple, list)) and len(res) == 2:
            try:
                h = int(res[1] or 0)
                if h > 0:
                    return h
            except Exception:
                pass
        if isinstance(res, str):
            m = re.search(r"[xX]\s*(\d+)", res)
            if m:
                try:
                    h = int(m.group(1))
                    if h > 0:
                        return h
                except Exception:
                    pass

        m = re.search(r"(\d{3,4})\s*p", str(q_label).lower())
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return 0

        return 0

    def _format_score(fmt: dict, height: int) -> int:
        score = 0
        try:
            score = int(fmt.get('quality_score') or 0)
        except Exception:
            score = 0
        if score <= 0:
            score = height or 0

        acodec = fmt.get('acodec', 'none')
        if acodec and acodec != 'none':
            score += 10000

        try:
            score += int(fmt.get('fps') or 0)
        except Exception:
            pass

        return score

    best_video: dict[tuple, tuple[int, dict, int, int]] = {}
    best_audio: dict[tuple, tuple[int, dict, int]] = {}

    for i, fmt in enumerate(formats or []):
        if not isinstance(fmt, dict):
            continue

        if fmt.get('is_audio', False):
            if fmt.get('is_audio_choice_button'):
                audio_choice_buttons.append((i, fmt))
                continue
            q = str(fmt.get('quality') or fmt.get('format_note') or 'audio')
            key = ('audio', q.strip().lower())
            score = _audio_score(fmt)
            prev = best_audio.get(key)
            if prev is None or score > prev[2]:
                best_audio[key] = (i, fmt, score)
            continue

        q_label = str(fmt.get('quality') or fmt.get('format_note') or fmt.get('format') or 'video')
        height = _get_height_from_fmt(fmt, q_label)
        width = _get_width_from_fmt(fmt, q_label) if height <= 0 else 0
        if height > 0:
            key = ('h', height)
            sort_value = height
        elif width > 0:
            key = ('w', width)
            sort_value = width
        else:
            key = ('q', q_label.strip().lower())
            sort_value = 0

        score = _format_score(fmt, height)
        prev = best_video.get(key)
        if prev is None or score > prev[2]:
            best_video[key] = (i, fmt, score, sort_value)

    def _sort_key(item):
        (k, (idx, fmt, score, sort_value)) = item
        if k[0] in ('h', 'w') and isinstance(sort_value, int):
            return (-int(sort_value), -int(score))
        return (0, -int(score))

    for _, (i, fmt, _, _) in sorted(best_video.items(), key=_sort_key):
        video_formats.append((fmt, f"quality:{i}"))

    if audio_choice_buttons:
        for i, fmt in audio_choice_buttons:
            audio_formats.append((fmt, f"quality:{i}"))
    else:
        for _, (i, fmt, _) in best_audio.items():
            audio_formats.append((fmt, f"quality:{i}"))

    def _filesize_suffix(fmt: dict) -> str:
        fs = fmt.get('filesize')
        if fs and fs != '~':
            return f" ({fs})"

        raw = fmt.get('filesize_raw')
        human = _human_filesize(raw)
        if human:
            return f" ({human})"

        qi = fmt.get('quality_info') if isinstance(fmt.get('quality_info'), dict) else {}
        bw = qi.get('bandwidth')
        duration_s = fmt.get('duration') or qi.get('duration') or duration
        try:
            bw_i = int(bw) if bw is not None else 0
        except Exception:
            bw_i = 0
        try:
            dur_i = int(duration_s) if duration_s is not None else 0
        except Exception:
            dur_i = 0
        if bw_i > 0 and dur_i > 0:
            est = int((bw_i * dur_i) / 8)
            human = _human_filesize(est)
            if human:
                return f" ({human})"
        return ''

    def _clean_quality_label(label: str, fmt: dict) -> str:
        q = (label or "").strip()
        if q:
            q = re.sub(r"\b(со\s+звуком|без\s+звука)\b", "", q, flags=re.IGNORECASE).strip()
            q = re.sub(r"\s{2,}", " ", q)
        if not q:
            h = _get_height_from_fmt(fmt, "")
            if h > 0:
                return f"{h}p"
            w = _get_width_from_fmt(fmt, "")
            if w > 0:
                return str(w)
            return "Видео"
        return q

    for fmt, callback_data in video_formats:
        quality = fmt.get('quality') or fmt.get('format_note') or fmt.get('format') or 'Видео'
        quality = _clean_quality_label(str(quality), fmt)

        button_text = f"🎬 {quality}{_filesize_suffix(fmt)}"

        builder.button(text=button_text, callback_data=callback_data)

    for fmt, callback_data in audio_formats:
        quality = fmt.get('quality') or 'Аудио'
        quality = str(quality)

        if fmt.get('is_audio_choice_button'):
            quality = 'Аудио'
        elif 'audio' in quality.lower() or 'аудио' in quality.lower():
            quality = 'Аудио'

        button_text = f"🎵 {quality}{_filesize_suffix(fmt)}"

        builder.button(text=button_text, callback_data=callback_data)

    builder.button(text="❌ Отмена", callback_data="cancel")

    total = len(video_formats) + len(audio_formats)
    if total > 0:
        rows_for_formats = (total + 1) // 2
        builder.adjust(*([2] * rows_for_formats + [1]))
    else:
        builder.adjust(1)

    return builder.as_markup()




def create_audio_keyboard(audio_tracks: List[Dict]) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора аудиодорожек"""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = []

    for i, track in enumerate(audio_tracks):
        track_name = track.get('name', f'Аудио {i + 1}')
        buttons.append([
            InlineKeyboardButton(
                text=f"🎵 {track_name}",
                callback_data=f"audio:{i}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="cancel"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
