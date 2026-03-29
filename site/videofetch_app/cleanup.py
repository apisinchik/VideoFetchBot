from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


CRITICAL_TEMP_FILENAMES = {"ytdlp_cookies.txt"}


@dataclass(slots=True)
class FileReclaimCandidate:
    path: Path
    age_seconds: float
    size_bytes: int


def resolve_project_path(raw_path: str | os.PathLike[str], project_root: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)


def build_managed_roots(
    *,
    project_root: Path,
    temp_dir: str | os.PathLike[str],
    media_root: str | os.PathLike[str] | None = None,
) -> list[Path]:
    roots = [resolve_project_path(temp_dir, project_root)]
    if media_root:
        media_path = resolve_project_path(media_root, project_root)
        if media_path not in roots:
            roots.append(media_path)
    return roots


def resolve_managed_result_path(
    raw_path: str | os.PathLike[str] | None,
    *,
    project_root: Path,
    managed_roots: Sequence[Path],
) -> Path | None:
    if not raw_path:
        return None
    path = resolve_project_path(raw_path, project_root)
    if any(root == path or root in path.parents for root in managed_roots):
        return path
    return None


def resolve_storage_relative_path(
    raw_path: str | os.PathLike[str] | None,
    *,
    storage_root: Path,
) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = storage_root / path
    path = path.resolve(strict=False)
    if storage_root == path or storage_root in path.parents:
        return path
    return None


def build_reclaim_candidates(
    paths: Iterable[Path],
    *,
    now_ts: float,
    min_age_seconds: int,
) -> list[FileReclaimCandidate]:
    candidates: list[FileReclaimCandidate] = []
    for path in paths:
        if path.name in CRITICAL_TEMP_FILENAMES:
            continue
        if not path.exists() or not path.is_file():
            continue
        stat = path.stat()
        age_seconds = now_ts - stat.st_mtime
        if age_seconds < min_age_seconds:
            continue
        candidates.append(
            FileReclaimCandidate(
                path=path,
                age_seconds=age_seconds,
                size_bytes=stat.st_size,
            )
        )
    candidates.sort(key=lambda item: item.path.stat().st_mtime)
    return candidates


def current_free_bytes(reference_path: Path) -> int:
    return shutil.disk_usage(reference_path).free
