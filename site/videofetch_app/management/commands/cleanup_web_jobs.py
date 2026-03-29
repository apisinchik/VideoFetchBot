from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from config import Config
from videofetch_app.cleanup import (
    build_managed_roots,
    build_reclaim_candidates,
    current_free_bytes,
    resolve_managed_result_path,
    resolve_project_path,
    resolve_storage_relative_path,
)
from videofetch_app.models import Broadcast, BroadcastAttachment, Job, TelegramAccount, User


class Command(BaseCommand):
    help = "Удаляет устаревшие web jobs, web users без активной сессии, вложения завершенных рассылок и старые медиа при нехватке места."

    def add_arguments(self, parser):
        parser.add_argument("--job-max-age-hours", type=int, default=int(os.getenv("CLEANUP_WEB_JOB_MAX_AGE_HOURS", "24")))
        parser.add_argument("--completed-broadcast-max-age-hours", type=int, default=int(os.getenv("CLEANUP_COMPLETED_BROADCAST_MAX_AGE_HOURS", "1")))
        parser.add_argument("--min-media-age-minutes", type=int, default=int(os.getenv("CLEANUP_MEDIA_MIN_AGE_MINUTES", "30")))
        parser.add_argument("--min-free-mb", type=int, default=int(os.getenv("CLEANUP_MIN_FREE_MB", "1024")))
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        project_root = Path(settings.PROJECT_ROOT)
        temp_root = build_managed_roots(
            project_root=project_root,
            temp_dir=Config.TEMP_DIR,
            media_root=getattr(settings, "MEDIA_ROOT", None),
        )[0]
        managed_roots = build_managed_roots(
            project_root=project_root,
            temp_dir=Config.TEMP_DIR,
            media_root=getattr(settings, "MEDIA_ROOT", None),
        )

        active_session_user_ids = self._load_active_session_user_ids(now)
        expired_web_user_ids = self._load_expired_web_user_ids(active_session_user_ids)

        deleted_expired_sessions = 0
        if options["dry_run"]:
            deleted_expired_sessions = Session.objects.filter(expire_date__lte=now).count()
        else:
            deleted_expired_sessions, _ = Session.objects.filter(expire_date__lte=now).delete()

        stale_cutoff = now - timedelta(hours=options["job_max_age_hours"])
        stale_jobs = list(
            Job.objects.filter(
                created_via=Job.CreatedBy.WEB,
                status__in=[Job.Status.DONE, Job.Status.FAILED, Job.Status.CANCELED],
            )
            .filter(
                Q(updated_at__lte=stale_cutoff)
                | Q(created_by_user_id__in=expired_web_user_ids)
            )
            .order_by("updated_at", "id")
        )

        deleted_job_ids: list[int] = []
        deleted_file_paths: set[str] = set()
        reclaimed_bytes = 0

        for job in stale_jobs:
            file_path = resolve_managed_result_path(
                job.result_path,
                project_root=project_root,
                managed_roots=managed_roots,
            )
            if file_path and file_path.exists() and file_path.is_file():
                reclaimed_bytes += file_path.stat().st_size
                deleted_file_paths.add(str(file_path))
                if not options["dry_run"]:
                    file_path.unlink(missing_ok=True)
            deleted_job_ids.append(job.id)

        if deleted_job_ids and not options["dry_run"]:
            Job.objects.filter(id__in=deleted_job_ids).delete()

        deleted_web_users = 0
        stale_web_users = User.objects.filter(id__in=expired_web_user_ids).exclude(
            id__in=TelegramAccount.objects.values("user_id")
        )
        stale_web_users = stale_web_users.exclude(
            id__in=Job.objects.values_list("created_by_user_id", flat=True)
        )
        if options["dry_run"]:
            deleted_web_users = stale_web_users.count()
        else:
            deleted_web_users, _ = stale_web_users.delete()

        cleaned_broadcast_attachments, deleted_broadcast_files, cleaned_broadcast_bytes = self._cleanup_completed_broadcast_attachments(
            now=now,
            project_root=project_root,
            max_age_hours=options["completed_broadcast_max_age_hours"],
            dry_run=options["dry_run"],
        )
        reclaimed_bytes += cleaned_broadcast_bytes

        reclaimed_files, reclaimed_file_bytes = self._reclaim_oldest_media_files(
            temp_root=temp_root,
            project_root=project_root,
            managed_roots=managed_roots,
            min_age_minutes=options["min_media_age_minutes"],
            min_free_mb=options["min_free_mb"],
            dry_run=options["dry_run"],
        )
        reclaimed_bytes += reclaimed_file_bytes

        self.stdout.write(
            self.style.SUCCESS(
                "cleanup_web_jobs: "
                f"expired_sessions={deleted_expired_sessions} "
                f"deleted_jobs={len(deleted_job_ids)} "
                f"deleted_job_files={len(deleted_file_paths)} "
                f"deleted_web_users={deleted_web_users} "
                f"cleaned_broadcast_attachments={cleaned_broadcast_attachments} "
                f"deleted_broadcast_files={deleted_broadcast_files} "
                f"reclaimed_oldest_files={reclaimed_files} "
                f"reclaimed_bytes={reclaimed_bytes}"
            )
        )

    def _load_active_session_user_ids(self, now):
        user_ids: set[int] = set()
        for session in Session.objects.filter(expire_date__gt=now).iterator():
            try:
                payload = session.get_decoded()
            except Exception:
                continue
            user_id = payload.get("user")
            try:
                if user_id is not None:
                    user_ids.add(int(user_id))
            except (TypeError, ValueError):
                continue
        return user_ids

    def _load_expired_web_user_ids(self, active_session_user_ids: set[int]):
        web_user_ids = set(
            Job.objects.filter(created_via=Job.CreatedBy.WEB)
            .values_list("created_by_user_id", flat=True)
            .distinct()
        )
        return web_user_ids - active_session_user_ids

    def _cleanup_completed_broadcast_attachments(
        self,
        *,
        now,
        project_root: Path,
        max_age_hours: int,
        dry_run: bool,
    ) -> tuple[int, int, int]:
        media_root_raw = getattr(settings, "MEDIA_ROOT", None)
        if not media_root_raw:
            return 0, 0, 0

        media_root = resolve_project_path(media_root_raw, project_root)
        cutoff = now - timedelta(hours=max_age_hours)
        broadcasts = (
            Broadcast.objects.filter(
                status=Broadcast.Status.COMPLETED,
                finished_at__isnull=False,
                finished_at__lte=cutoff,
            )
            .prefetch_related("attachments")
            .order_by("finished_at", "id")
        )

        attachment_ids_to_clear: list[int] = []
        deleted_paths: set[Path] = set()
        reclaimed_bytes = 0

        for broadcast in broadcasts:
            for attachment in broadcast.attachments.all():
                raw_file = getattr(getattr(attachment, "file", None), "name", "") or str(getattr(attachment, "file", "") or "")
                if not raw_file:
                    continue

                path = resolve_storage_relative_path(
                    raw_file,
                    storage_root=media_root,
                )
                if path is None:
                    continue

                attachment_ids_to_clear.append(int(attachment.id))
                if path in deleted_paths or not path.exists() or not path.is_file():
                    continue

                reclaimed_bytes += path.stat().st_size
                deleted_paths.add(path)
                if not dry_run:
                    path.unlink(missing_ok=True)

        if attachment_ids_to_clear and not dry_run:
            BroadcastAttachment.objects.filter(id__in=attachment_ids_to_clear).update(file="")

        return len(attachment_ids_to_clear), len(deleted_paths), reclaimed_bytes

    def _reclaim_oldest_media_files(
        self,
        *,
        temp_root: Path,
        project_root: Path,
        managed_roots,
        min_age_minutes: int,
        min_free_mb: int,
        dry_run: bool,
    ) -> tuple[int, int]:
        if not temp_root.exists():
            return 0, 0

        min_free_bytes = int(min_free_mb) * 1024 * 1024
        free_bytes = current_free_bytes(temp_root)
        if free_bytes >= min_free_bytes:
            return 0, 0

        now_ts = timezone.now().timestamp()
        min_age_seconds = int(min_age_minutes) * 60

        terminal_jobs = list(
            Job.objects.filter(
                status__in=[Job.Status.DONE, Job.Status.FAILED, Job.Status.CANCELED]
            ).exclude(result_path__isnull=True).exclude(result_path="")
        )

        job_paths: dict[Path, int] = {}
        for job in terminal_jobs:
            path = resolve_managed_result_path(
                job.result_path,
                project_root=project_root,
                managed_roots=managed_roots,
            )
            if path is not None:
                job_paths[path] = job.id

        orphan_paths: list[Path] = []
        for root, _dirs, files in os.walk(temp_root):
            for filename in files:
                candidate = Path(root) / filename
                if candidate not in job_paths:
                    orphan_paths.append(candidate)

        reclaim_candidates = build_reclaim_candidates(
            [*job_paths.keys(), *orphan_paths],
            now_ts=now_ts,
            min_age_seconds=min_age_seconds,
        )

        deleted_files = 0
        reclaimed_bytes = 0

        for candidate in reclaim_candidates:
            if free_bytes >= min_free_bytes:
                break
            deleted_files += 1
            reclaimed_bytes += candidate.size_bytes
            free_bytes += candidate.size_bytes
            if dry_run:
                continue

            candidate.path.unlink(missing_ok=True)
            job_id = job_paths.get(candidate.path)
            if job_id:
                with transaction.atomic():
                    job = Job.objects.select_for_update().filter(id=job_id).first()
                    if job and job.result_path:
                        resolved = resolve_managed_result_path(
                            job.result_path,
                            project_root=project_root,
                            managed_roots=managed_roots,
                        )
                        if resolved == candidate.path:
                            result_meta = dict(job.result_meta or {})
                            result_meta["evicted_for_space"] = True
                            result_meta["evicted_at"] = timezone.now().isoformat()
                            job.result_path = None
                            job.result_size_bytes = None
                            job.result_meta = result_meta
                            job.save(update_fields=["result_path", "result_size_bytes", "result_meta"])

        return deleted_files, reclaimed_bytes
