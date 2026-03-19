"""Общий слой базы данных для Telegram-бота и веб-приложения."""

from .postgres_db import create_pool, init_schema
from .postgres_queue import (
    QueueJob,
    upsert_telegram_user,
    get_active_job_for_user,
    enqueue_job,
    get_queue_position,
    count_running,
    claim_next,
    update_progress,
    mark_done,
    mark_failed,
    mark_canceled,
    cancel_latest_queued,
    get_job,
)

__all__ = [
    "create_pool",
    "init_schema",
    "QueueJob",
    "upsert_telegram_user",
    "get_active_job_for_user",
    "enqueue_job",
    "get_queue_position",
    "count_running",
    "claim_next",
    "update_progress",
    "mark_done",
    "mark_failed",
    "mark_canceled",
    "cancel_latest_queued",
    "get_job",
]
