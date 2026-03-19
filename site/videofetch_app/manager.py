from __future__ import annotations

from datetime import timedelta
from typing import Optional, Tuple

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from config import Config
from videofetch_app.models import AnalyseSlot, Job, User


def get_or_create_site_user(request) -> User:
    user_id = request.session.get("user")
    if user_id:
        user = User.objects.filter(id=user_id).first()
        if user is not None:
            return user

    user = User.objects.create()
    request.session["user"] = user.id
    request.session.modified = True
    return user


def select_and_hold(timeout_seconds: int) -> Optional[int]:
    with transaction.atomic():
        slot = (
            AnalyseSlot.objects.select_for_update(skip_locked=True)
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=timezone.now()))
            .order_by("slot_id")
            .first()
        )
        if slot is None:
            return None

        slot.holder = AnalyseSlot.Holder.HOLD
        slot.lease_until = timezone.now() + timedelta(seconds=int(timeout_seconds))
        slot.save(update_fields=["holder", "lease_until"])
        return slot.slot_id


def slot_to_free(slot_id: Optional[int]) -> None:
    if not slot_id:
        return
    AnalyseSlot.objects.filter(slot_id=slot_id).update(
        holder=AnalyseSlot.Holder.FREE,
        lease_until=None,
    )


def load_recent_jobs(user: User, limit: int = 20):
    return list(
        Job.objects.filter(created_by_user=user)
        .order_by("-created_at")[:limit]
    )


def enqueue_web_job_guarded(
    *,
    user: User,
    source_url: str,
    title: str,
    duration_seconds: int,
    is_short: bool,
    requested_quality: Optional[str],
    requested_audio: Optional[str],
    selected_format: dict,
    selected_audio: Optional[dict],
) -> Tuple[str, Optional[Job], int]:
    with transaction.atomic():
        User.objects.select_for_update().get(id=user.id)

        active_qs = Job.objects.filter(
            created_by_user=user,
            status__in=[Job.Status.QUEUED, Job.Status.RUNNING],
        ).order_by("-created_at")
        active_jobs = active_qs.count()

        duplicate = active_qs.filter(source_url=source_url).first()
        if duplicate is not None:
            return "duplicate", duplicate, active_jobs

        max_active_jobs = int(getattr(Config, "USER_MAX_ACTIVE_JOBS", 1))
        if active_jobs >= max_active_jobs:
            latest = active_qs.first()
            return "limit_reached", latest, active_jobs

        job = Job.objects.create(
            created_by_user=user,
            created_via=Job.CreatedBy.WEB,
            source_url=source_url,
            title=title,
            duration_seconds=duration_seconds,
            is_short=is_short,
            requested_quality=requested_quality,
            requested_audio=requested_audio,
            selected_format=selected_format,
            selected_audio=selected_audio,
            status=Job.Status.QUEUED,
            priority=0,
            progress=0,
            stage="queued",
        )
        return "enqueued", job, active_jobs + 1
