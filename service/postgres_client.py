"""
Shared asyncpg connection pool for the DEMO DATA platform store.
All service modules import get_pool() from here -- never create their own connection.

Environment variables (set in .env):
    PG_HOST      default: 217.217.249.121
    PG_PORT      default: 5432
    PG_DB        default: DEMO_DATA
    PG_USER      default: gbuser
    PG_PASSWORD  default: aidev123
"""

import asyncpg
import json
import logging
import os

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn):
    """Register JSONB codec so asyncpg handles dict<->JSONB automatically."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        host=os.getenv("PG_HOST", "217.217.249.121"),
        port=int(os.getenv("PG_PORT", "5432")),
        database=os.getenv("PG_DB", "DEMO_DATA"),
        user=os.getenv("PG_USER", "gbuser"),
        password=os.getenv("PG_PASSWORD", "aidev123"),
        min_size=2,
        max_size=10,
        init=_init_connection,
    )
    logger.info("PostgreSQL pool initialised → %s/%s", os.getenv("PG_HOST", "217.217.249.121"), os.getenv("PG_DB", "DEMO_DATA"))


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed.")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("PostgreSQL pool not initialised. Call init_pool() on startup.")
    return _pool