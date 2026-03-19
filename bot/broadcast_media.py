from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


BROADCAST_CAPTION_LIMIT = 1024
BROADCAST_MEDIA_GROUP_LIMIT = 10

PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm', '.mkv'}


@dataclass(frozen=True)
class BroadcastSendPlan:
    mode: str
    caption: str
    attachment_kinds: tuple[str, ...]


def classify_broadcast_attachment(attachment: Any) -> str:
    content_type = (_attachment_value(attachment, 'content_type') or '').strip().lower()
    original_name = (
        _attachment_value(attachment, 'original_name')
        or _attachment_value(attachment, 'file')
        or ''
    ).strip()
    ext = Path(original_name).suffix.lower()

    if content_type.startswith('image/') and ext in PHOTO_EXTENSIONS:
        return 'photo'
    if ext in PHOTO_EXTENSIONS:
        return 'photo'

    if content_type.startswith('video/'):
        return 'video'
    if ext in VIDEO_EXTENSIONS:
        return 'video'

    return 'document'


def build_broadcast_send_plan(text: str, attachments: Sequence[Any]) -> BroadcastSendPlan:
    caption = (text or '').strip()
    attachment_list = list(attachments or [])

    if not attachment_list:
        return BroadcastSendPlan(mode='text', caption=caption, attachment_kinds=())

    if len(attachment_list) > BROADCAST_MEDIA_GROUP_LIMIT:
        raise ValueError(
            f'Telegram allows at most {BROADCAST_MEDIA_GROUP_LIMIT} attachments in one broadcast message.'
        )

    if len(caption) > BROADCAST_CAPTION_LIMIT:
        raise ValueError(
            f'Text is too long for a Telegram media caption. Limit: {BROADCAST_CAPTION_LIMIT} characters.'
        )

    kinds = tuple(classify_broadcast_attachment(item) for item in attachment_list)

    if len(attachment_list) == 1:
        return BroadcastSendPlan(
            mode=f'single_{kinds[0]}',
            caption=caption,
            attachment_kinds=kinds,
        )

    unique_kinds = set(kinds)
    if unique_kinds == {'document'}:
        return BroadcastSendPlan(
            mode='group_document',
            caption=caption,
            attachment_kinds=kinds,
        )

    if unique_kinds.issubset({'photo', 'video'}):
        return BroadcastSendPlan(
            mode='group_media',
            caption=caption,
            attachment_kinds=kinds,
        )

    raise ValueError(
        'Telegram cannot send documents together with photo/video media as one broadcast message.'
    )


def _attachment_value(attachment: Any, key: str) -> Any:
    if isinstance(attachment, dict):
        return attachment.get(key)
    return getattr(attachment, key, None)
