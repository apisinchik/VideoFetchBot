from __future__ import annotations

import logging
import pathlib
import json
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Настраивает кодеки типов соединения."""
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    """Создает пул asyncpg с кодеками JSON и JSONB."""
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size, init=_init_connection)


async def init_schema(pool: asyncpg.Pool, schema_path: str | pathlib.Path) -> None:
    """Идемпотентно применяет schema.sql."""
    path = pathlib.Path(schema_path)
    sql = path.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(sql)
        logger.info("PostgreSQL schema initialized")
