from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class CachedDownload:
    """Готовый файл, который можно повторно отправить пользователю."""

    user_id: int
    file_path: str
    caption: str
    media_kind: str
    created_at: float

    @property
    def exists(self) -> bool:
        return os.path.exists(self.file_path)

    @property
    def size_bytes(self) -> int:
        try:
            return os.path.getsize(self.file_path)
        except OSError:
            return 0

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


class DownloadRegistry:
    """In-memory registry for last downloaded file per user."""

    def __init__(self, retention_seconds: int = 6 * 3600) -> None:
        self.retention_seconds = retention_seconds
        self._by_user: Dict[int, CachedDownload] = {}

    def set(self, user_id: int, file_path: str, caption: str, media_kind: str = "video") -> CachedDownload:
        rec = CachedDownload(
            user_id=user_id,
            file_path=file_path,
            caption=caption,
            media_kind=media_kind,
            created_at=time.time(),
        )
        self._by_user[user_id] = rec
        return rec

    def get(self, user_id: int) -> Optional[CachedDownload]:
        rec = self._by_user.get(user_id)
        if not rec:
            return None

        if not rec.exists:
            self._by_user.pop(user_id, None)
            return None

        if self.is_expired(rec):
            self._safe_remove(rec.file_path)
            self._by_user.pop(user_id, None)
            return None

        return rec

    def pop(self, user_id: int) -> Optional[CachedDownload]:
        return self._by_user.pop(user_id, None)

    def cleanup(self) -> int:
        removed = 0
        for user_id, rec in list(self._by_user.items()):
            if (not rec.exists) or self.is_expired(rec):
                self._safe_remove(rec.file_path)
                self._by_user.pop(user_id, None)
                removed += 1
        return removed

    def is_expired(self, rec: CachedDownload) -> bool:
        return (time.time() - rec.created_at) > self.retention_seconds

    @staticmethod
    def _safe_remove(path: str) -> None:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
