from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import json

import asyncpg

logger = logging.getLogger(__name__)


def _ensure_json_obj(v: Any) -> Any:
    """Нормализует поля JSONB."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, (bytes, bytearray)):
        try:
            v = v.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


@dataclass
class QueueJob:
    id: int
    created_by_user_id: int
    created_via: str
    telegram_user_id: Optional[int]
    telegram_chat_id: Optional[int]
    progress_msg_id: Optional[int]
    source_url: str
    title: Optional[str]
    duration_seconds: Optional[int]
    is_short: bool
    requested_quality: Optional[str]
    requested_audio: Optional[str]
    selected_format: Optional[Dict[str, Any]]
    selected_audio: Optional[Dict[str, Any]]
    status: str
    progress: int
    stage: Optional[str]
    attempts: int
    result_path: Optional[str]
    result_size_bytes: Optional[int]
    error_code: Optional[str]
    error_message: Optional[str]


@dataclass
class EnqueueGuardResult:
    status: str
    job_id: Optional[int] = None
    existing_job: Optional[QueueJob] = None
    active_jobs: int = 0


def _row_to_job(row: asyncpg.Record) -> QueueJob:
    return QueueJob(
        id=row["id"],
        created_by_user_id=row["created_by_user_id"],
        created_via=row["created_via"],
        telegram_user_id=row.get("telegram_user_id"),
        telegram_chat_id=row.get("telegram_chat_id"),
        progress_msg_id=row.get("progress_msg_id"),
        source_url=row["source_url"],
        title=row.get("title"),
        duration_seconds=row.get("duration_seconds"),
        is_short=bool(row.get("is_short")),
        requested_quality=row.get("requested_quality"),
        requested_audio=row.get("requested_audio"),
        selected_format=_ensure_json_obj(row.get("selected_format")),
        selected_audio=_ensure_json_obj(row.get("selected_audio")),
        status=row["status"],
        progress=row.get("progress", 0),
        stage=row.get("stage"),
        attempts=row.get("attempts", 0),
        result_path=row.get("result_path"),
        result_size_bytes=row.get("result_size_bytes"),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
    )


async def upsert_telegram_user(
    pool: asyncpg.Pool,
    *,
    telegram_user_id: int,
    chat_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    language_code: Optional[str],
) -> int:
    """Создает или обновляет основного пользователя и Telegram-идентичность."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT user_id FROM telegram_users WHERE telegram_user_id=$1",
                telegram_user_id,
            )
            if row:
                user_id = int(row["user_id"])
                await conn.execute(
    """
                    UPDATE telegram_users
                    SET chat_id=$2, username=$3, first_name=$4, last_name=$5, language_code=$6, last_seen_at=now()
                    WHERE telegram_user_id=$1
    """,
                    telegram_user_id,
                    chat_id,
                    username,
                    first_name,
                    last_name,
                    language_code,
                )
                return user_id

            user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
            await conn.execute(
                """
                INSERT INTO telegram_users(user_id, telegram_user_id, chat_id, username, first_name, last_name, language_code)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                """,
                user_id,
                telegram_user_id,
                chat_id,
                username,
                first_name,
                last_name,
                language_code,
            )
            return int(user_id)


async def get_active_job_for_user(pool: asyncpg.Pool, *, user_id: int) -> Optional[QueueJob]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
    """
            SELECT * FROM download_jobs
            WHERE created_by_user_id=$1 AND status IN ('queued','running')
            ORDER BY created_at DESC
            LIMIT 1
    """,
            user_id,
        )
        return _row_to_job(row) if row else None


async def enqueue_job(
    pool: asyncpg.Pool,
    *,
    user_id: int,
    telegram_user_id: int,
    telegram_chat_id: int,
    progress_msg_id: int,
    source_url: str,
    title: Optional[str],
    duration_seconds: int,
    is_short: bool,
    requested_quality: Optional[str],
    requested_audio: Optional[str],
    selected_format: Dict[str, Any],
    selected_audio: Optional[Dict[str, Any]],
) -> int:
    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
    """
            INSERT INTO download_jobs(
                created_by_user_id, created_via,
                telegram_user_id, telegram_chat_id, progress_msg_id,
                source_url, title, duration_seconds, is_short,
                requested_quality, requested_audio,
                selected_format, selected_audio,
                status, priority, progress, stage
            ) VALUES (
                $1,'telegram',$2,$3,$4,
                $5,$6,$7,$8,
                $9,$10,
                $11,$12,
                'queued',0,0,'queued'
            ) RETURNING id
            """,
            user_id,
            telegram_user_id,
            telegram_chat_id,
            progress_msg_id,
            source_url,
            title,
            duration_seconds,
            is_short,
            requested_quality,
            requested_audio,
            selected_format,
            selected_audio,
        )
        return int(job_id)


async def enqueue_job_guarded(
    pool: asyncpg.Pool,
    *,
    user_id: int,
    telegram_user_id: int,
    telegram_chat_id: int,
    progress_msg_id: int,
    source_url: str,
    title: Optional[str],
    duration_seconds: int,
    is_short: bool,
    requested_quality: Optional[str],
    requested_audio: Optional[str],
    selected_format: Dict[str, Any],
    selected_audio: Optional[Dict[str, Any]],
    max_active_jobs: int,
) -> EnqueueGuardResult:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", int(user_id))

            active_jobs = int(await conn.fetchval(
    """
                SELECT count(*)
                FROM download_jobs
                WHERE created_by_user_id=$1 AND status IN ('queued','running')
                """,
                user_id,
            ) or 0)

            duplicate_row = await conn.fetchrow(
                """
                SELECT *
                FROM download_jobs
                WHERE created_by_user_id=$1
                  AND status IN ('queued','running')
                  AND source_url=$2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
                source_url,
            )
            if duplicate_row:
                return EnqueueGuardResult(
                    status="duplicate",
                    existing_job=_row_to_job(duplicate_row),
                    active_jobs=active_jobs,
                )

            if active_jobs >= max_active_jobs:
                latest_row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM download_jobs
                    WHERE created_by_user_id=$1 AND status IN ('queued','running')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
                return EnqueueGuardResult(
                    status="limit_reached",
                    existing_job=_row_to_job(latest_row) if latest_row else None,
                    active_jobs=active_jobs,
                )

            job_id = await conn.fetchval(
                """
                INSERT INTO download_jobs(
                    created_by_user_id, created_via,
                    telegram_user_id, telegram_chat_id, progress_msg_id,
                    source_url, title, duration_seconds, is_short,
                    requested_quality, requested_audio,
                    selected_format, selected_audio,
                    status, priority, progress, stage
                ) VALUES (
                    $1,'telegram',$2,$3,$4,
                    $5,$6,$7,$8,
                    $9,$10,
                    $11,$12,
                    'queued',0,0,'queued'
                ) RETURNING id
                """,
                user_id,
                telegram_user_id,
                telegram_chat_id,
                progress_msg_id,
                source_url,
                title,
                duration_seconds,
                is_short,
                requested_quality,
                requested_audio,
                selected_format,
                selected_audio,
            )
            return EnqueueGuardResult(
                status="enqueued",
                job_id=int(job_id),
                active_jobs=active_jobs + 1,
            )


async def get_queue_position(pool: asyncpg.Pool, *, job_id: int) -> Optional[int]:
    """Возвращает позицию в очереди с 1 среди queued-задач той же полосы."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
    """
            WITH target AS (
                SELECT id, is_short
                FROM download_jobs
                WHERE id=$1
            ), q AS (
                SELECT j.id,
                       row_number() OVER (ORDER BY j.priority DESC, j.created_at ASC) AS rn
                FROM download_jobs j
                JOIN target t ON t.is_short = j.is_short
                WHERE j.status='queued'
            )
            SELECT rn FROM q WHERE id=$1
    """,
            job_id,
        )
        return int(row["rn"]) if row else None


async def count_running(pool: asyncpg.Pool, *, is_short: bool) -> int:
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT count(*) FROM download_jobs WHERE status='running' AND is_short=$1",
            is_short,
        )
        return int(n or 0)


async def requeue_running_jobs(pool: asyncpg.Pool) -> int:
    """Возвращает все running-задачи в queued при перезапуске бота."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
    """
            UPDATE download_jobs
            SET status='queued',
                stage='queued',
                progress=0,
                locked_by=NULL,
                locked_at=NULL,
                run_after=now()
            WHERE status='running'
            RETURNING id
    """
        )
        return len(rows)


async def claim_next(pool: asyncpg.Pool, *, is_short: bool, worker_id: str) -> Optional[QueueJob]:
    """Атомарно забирает следующую queued-задачу в указанной полосе."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
    """
                WITH picked AS (
                    SELECT id
                    FROM download_jobs
                    WHERE status='queued'
                      AND run_after <= now()
                      AND is_short=$1
                    ORDER BY priority DESC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE download_jobs
                SET status='running', locked_by=$2, locked_at=now(), attempts=attempts+1, stage='starting'
                WHERE id IN (SELECT id FROM picked)
                RETURNING *
    """,
                is_short,
                worker_id,
            )
            return _row_to_job(row) if row else None


async def update_progress(pool: asyncpg.Pool, *, job_id: int, progress: int, stage: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE download_jobs SET progress=$2, stage=$3 WHERE id=$1",
            job_id,
            int(progress),
            stage,
        )


async def mark_done(
    pool: asyncpg.Pool,
    *,
    job_id: int,
    result_path: str,
    result_size_bytes: int,
    result_meta: Optional[Dict[str, Any]] = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
    """
            UPDATE download_jobs
            SET status='done', progress=100, stage='done', result_path=$2, result_size_bytes=$3, result_meta=$4
            WHERE id=$1
    """,
            job_id,
            result_path,
            int(result_size_bytes),
            result_meta,
        )


async def mark_failed(pool: asyncpg.Pool, *, job_id: int, error_message: str, error_code: str = "failed") -> None:
    async with pool.acquire() as conn:
        await conn.execute(
    """
            UPDATE download_jobs
            SET status='failed', stage='failed', error_code=$2, error_message=$3
            WHERE id=$1
    """,
            job_id,
            error_code,
            error_message[:1500],
        )


async def cancel_latest_queued(pool: asyncpg.Pool, *, user_id: int) -> Optional[int]:
    """Отменяет последнюю queued-задачу пользователя. Возвращает id задачи или None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
    """
            UPDATE download_jobs
            SET status='canceled', stage='canceled'
            WHERE id = (
                SELECT id FROM download_jobs
                WHERE created_by_user_id=$1 AND status='queued'
                ORDER BY created_at DESC
                LIMIT 1
            )
            RETURNING id
    """,
            user_id,
        )
        return int(row["id"]) if row else None


async def mark_canceled(pool: asyncpg.Pool, *, job_id: int, reason: str = "canceled") -> None:
    async with pool.acquire() as conn:
        await conn.execute(
    """
            UPDATE download_jobs
            SET status='canceled', stage='canceled', error_code='canceled', error_message=$2
            WHERE id=$1
    """,
            job_id,
            reason[:1500],
        )


async def get_job(pool: asyncpg.Pool, *, job_id: int) -> Optional[QueueJob]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM download_jobs WHERE id=$1", job_id)
        return _row_to_job(row) if row else None


async def start_slots(pool: asyncpg.Pool, slots: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute('''INSERT INTO analysis_slots(holder, lease_until)
                     SELECT \'free\', NULL
                     FROM generate_series(1, $1);''', slots)


async def clear_slots(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM analysis_slots")
        

async def select_and_hold(pool: asyncpg.Pool, timeout: int) -> Optional[int]:
    async with pool.acquire() as conn:
        id = await conn.fetchval('''WITH picked AS (
                           SELECT slot_id
                           FROM analysis_slots
                           WHERE lease_until IS NULL OR lease_until < now()
                           ORDER BY slot_id
                           LIMIT 1
                           )
                           UPDATE analysis_slots s
                           SET holder = \'hold\',
                           lease_until = now() + make_interval(secs => $1)
                           FROM picked
                           WHERE s.slot_id = picked.slot_id 
                           RETURNING s.slot_id;''', timeout)
        return id


async def slot_to_free(pool: asyncpg.Pool, id) -> None:
    async with pool.acquire() as conn:
        await conn.execute('''UPDATE analysis_slots
                     SET holder = \'free\', lease_until = NULL
                     WHERE slot_id = $1;''', id)
