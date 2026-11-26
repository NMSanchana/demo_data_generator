"""
Shared asyncpg connection pool for the DEMO DATA platform store.
All service modules import get_pool() from here -- never create their own connection.

Environment variables (set in .env -- see .env.example). There are no built-in credentials:
    PG_HOST      required
    PG_USER      required
    PG_PASSWORD  required
    PG_PORT      default: 5432
    PG_DB        default: DEMO_DATA
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


def connection_kwargs(database: str | None = None) -> dict:
    """Connection settings for the shared PG server. database=None -> the main platform DB (PG_DB).
    Per-profile databases live on the same server with the same credentials (see service/profile_db.py)."""
    missing = [k for k in ("PG_HOST", "PG_USER", "PG_PASSWORD") if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} must be set in .env (see .env.example).")
    return dict(
        host=os.getenv("PG_HOST"),
        port=int(os.getenv("PG_PORT", "5432")),
        database=database or os.getenv("PG_DB", "DEMO_DATA"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
    )


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        **connection_kwargs(),
        min_size=2,
        max_size=10,
        init=_init_connection,
    )
    logger.info("PostgreSQL pool initialised → %s/%s", os.getenv("PG_HOST"), os.getenv("PG_DB", "DEMO_DATA"))


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