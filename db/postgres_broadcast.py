from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import asyncpg


@dataclass
class BroadcastAttachmentRef:
    id: int
    file: str
    original_name: str
    content_type: str
    size_bytes: int


@dataclass
class BroadcastDeliveryJob:
    delivery_id: int
    broadcast_id: int
    chat_id: int
    telegram_user_id: int
    attempts: int
    text: str
    attachments: list[BroadcastAttachmentRef]


async def requeue_running_broadcast_deliveries(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                UPDATE broadcast_deliveries
                SET status='pending',
                    locked_by=NULL,
                    locked_at=NULL,
                    run_after=now()
                WHERE status='running'
                RETURNING broadcast_id
                """
            )
            if rows:
                broadcast_ids = sorted({int(row['broadcast_id']) for row in rows})
                await conn.execute(
                    """
                    UPDATE broadcasts
                    SET status='queued',
                        finished_at=NULL
                    WHERE id = ANY($1::bigint[])
                      AND status='running'
                    """,
                    broadcast_ids,
                )
            return len(rows)


async def claim_next_broadcast_delivery(pool: asyncpg.Pool, *, worker_id: str) -> Optional[BroadcastDeliveryJob]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                WITH picked AS (
                    SELECT bd.id
                    FROM broadcast_deliveries bd
                    JOIN broadcasts b ON b.id = bd.broadcast_id
                    WHERE bd.status='pending'
                      AND bd.run_after <= now()
                      AND b.status IN ('queued','running')
                    ORDER BY bd.run_after ASC, bd.id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE broadcast_deliveries bd
                SET status='running',
                    locked_by=$1,
                    locked_at=now(),
                    attempts=attempts+1
                FROM picked
                WHERE bd.id = picked.id
                RETURNING
                    bd.id AS delivery_id,
                    bd.broadcast_id AS broadcast_id,
                    bd.chat_id AS chat_id,
                    bd.telegram_user_id AS telegram_user_id,
                    bd.attempts AS attempts
                """,
                worker_id,
            )
            if not row:
                return None

            await conn.execute(
                """
                UPDATE broadcasts
                SET status='running',
                    started_at=COALESCE(started_at, now()),
                    finished_at=NULL
                WHERE id=$1
                """,
                int(row['broadcast_id']),
            )
            text = await conn.fetchval(
                "SELECT text FROM broadcasts WHERE id=$1",
                int(row['broadcast_id']),
            )

            attachments = await conn.fetch(
                """
                SELECT id, file, original_name, content_type, size_bytes
                FROM broadcast_attachments
                WHERE broadcast_id=$1
                ORDER BY id ASC
                """,
                int(row['broadcast_id']),
            )

            return BroadcastDeliveryJob(
                delivery_id=int(row['delivery_id']),
                broadcast_id=int(row['broadcast_id']),
                chat_id=int(row['chat_id']),
                telegram_user_id=int(row['telegram_user_id']),
                attempts=int(row['attempts']),
                text=(text or '').strip(),
                attachments=[
                    BroadcastAttachmentRef(
                        id=int(item['id']),
                        file=item['file'],
                        original_name=item['original_name'] or '',
                        content_type=item['content_type'] or '',
                        size_bytes=int(item['size_bytes'] or 0),
                    )
                    for item in attachments
                ],
            )


async def mark_broadcast_delivery_sent(pool: asyncpg.Pool, *, delivery_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            broadcast_id = await conn.fetchval(
                """
                UPDATE broadcast_deliveries
                SET status='sent',
                    sent_at=now(),
                    locked_by=NULL,
                    locked_at=NULL,
                    last_error=''
                WHERE id=$1
                RETURNING broadcast_id
                """,
                delivery_id,
            )
            if broadcast_id:
                await _refresh_broadcast_state(conn, int(broadcast_id))


async def mark_broadcast_delivery_failed(pool: asyncpg.Pool, *, delivery_id: int, error_message: str) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            broadcast_id = await conn.fetchval(
                """
                UPDATE broadcast_deliveries
                SET status='failed',
                    locked_by=NULL,
                    locked_at=NULL,
                    last_error=$2
                WHERE id=$1
                RETURNING broadcast_id
                """,
                delivery_id,
                error_message[:1500],
            )
            if broadcast_id:
                await _refresh_broadcast_state(conn, int(broadcast_id), last_error=error_message)


async def reschedule_broadcast_delivery(
    pool: asyncpg.Pool,
    *,
    delivery_id: int,
    delay_seconds: float,
    error_message: str,
) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            broadcast_id = await conn.fetchval(
                """
                UPDATE broadcast_deliveries
                SET status='pending',
                    run_after=now() + ($2 * interval '1 second'),
                    locked_by=NULL,
                    locked_at=NULL,
                    last_error=$3
                WHERE id=$1
                RETURNING broadcast_id
                """,
                delivery_id,
                float(delay_seconds),
                error_message[:1500],
            )
            if broadcast_id:
                await conn.execute(
                    """
                    UPDATE broadcasts
                    SET last_error=$2
                    WHERE id=$1
                    """,
                    int(broadcast_id),
                    error_message[:1500],
                )


async def _refresh_broadcast_state(conn: asyncpg.Connection, broadcast_id: int, last_error: str | None = None) -> None:
    stats = await conn.fetchrow(
        """
        SELECT
            count(*)::int AS total_count,
            count(*) FILTER (WHERE status='sent')::int AS sent_count,
            count(*) FILTER (WHERE status='failed')::int AS failed_count,
            count(*) FILTER (WHERE status IN ('pending','running'))::int AS active_count
        FROM broadcast_deliveries
        WHERE broadcast_id=$1
        """,
        broadcast_id,
    )
    if not stats:
        return

    total_count = int(stats['total_count'] or 0)
    sent_count = int(stats['sent_count'] or 0)
    failed_count = int(stats['failed_count'] or 0)
    active_count = int(stats['active_count'] or 0)

    status = 'running'
    if active_count == 0:
        status = 'completed' if sent_count > 0 else 'failed'

    if active_count == 0:
        await conn.execute(
            """
            UPDATE broadcasts
            SET total_recipients=$2,
                sent_count=$3,
                failed_count=$4,
                status=$5,
                finished_at=now(),
                last_error=COALESCE($6, last_error)
            WHERE id=$1
            """,
            broadcast_id,
            total_count,
            sent_count,
            failed_count,
            status,
            (last_error or '')[:1500] or None,
        )
    else:
        await conn.execute(
            """
            UPDATE broadcasts
            SET total_recipients=$2,
                sent_count=$3,
                failed_count=$4,
                status=$5,
                last_error=COALESCE($6, last_error)
            WHERE id=$1
            """,
            broadcast_id,
            total_count,
            sent_count,
            failed_count,
            status,
            (last_error or '')[:1500] or None,
        )
