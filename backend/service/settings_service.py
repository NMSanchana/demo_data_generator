"""
Settings service for the demo data generator.

One-time settings, internal-office use only — no tenant_id.

Table shape (module mandatory, screen optional):

    module              | screen         | use_domain | use_subdomain | use_geography
    --------------------+----------------+------------+----------------+---------------
    Skill Management    | ''             | true       | true           | false   <- module-level default (screen = '')
    Skill Management    | Skill Domain   | true       | true           | false   <- screen-specific override

Lookup rule:
    1. Try an exact (module, screen) row.
    2. Fall back to the module-level default row (screen = '').
    3. If neither exists, fall back to all-True defaults (nothing disabled).

Settings change roughly once a month, so there is no cache/TTL here —
every /generate call does one direct SELECT. Saving writes immediately.
"""

import logging
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Sentinel used in the DB for "this row is the module-level default"
# (kept as '' rather than NULL so a plain UNIQUE(module, screen) constraint
# works on any Postgres version, without needing NULLS NOT DISTINCT).
MODULE_DEFAULT_SCREEN = ""

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    module         TEXT NOT NULL,
    screen         TEXT NOT NULL DEFAULT '',
    use_domain     BOOLEAN NOT NULL DEFAULT TRUE,
    use_subdomain  BOOLEAN NOT NULL DEFAULT TRUE,
    use_geography  BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (module, screen)
);
"""

_DEFAULT_SETTINGS = {
    "use_domain":    True,
    "use_subdomain": True,
    "use_geography": True,
    "is_default":    True,   # true whenever nothing was ever saved for this module/screen
}

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    """Create the connection pool and ensure the settings table exists.
    Call once on FastAPI startup."""
    global _pool
    if _pool is not None:
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL must be set in .env (see .env.example).")

    _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)
    logger.info("settings_service: pool ready, settings table ensured")


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("settings_service: pool not initialised — call init_pool() on startup.")
    return _pool


def _normalise_screen(screen: str | None) -> str:
    return screen or MODULE_DEFAULT_SCREEN


async def get_settings(module: str, screen: str | None = None) -> dict:
    """
    Resolve the effective settings for a module (+ optional screen).

    Returns:
        {
            "module": str,
            "screen": str | None,        # None when this is the module-level default
            "use_domain": bool,
            "use_subdomain": bool,
            "use_geography": bool,
            "is_default": bool,          # True if no row was found at all (all-True fallback)
        }
    """
    pool = _get_pool()
    screen_norm = _normalise_screen(screen)

    async with pool.acquire() as conn:
        row = None
        if screen_norm != MODULE_DEFAULT_SCREEN:
            row = await conn.fetchrow(
                "SELECT * FROM settings WHERE module = $1 AND screen = $2",
                module, screen_norm,
            )
        if row is None:
            row = await conn.fetchrow(
                "SELECT * FROM settings WHERE module = $1 AND screen = $2",
                module, MODULE_DEFAULT_SCREEN,
            )

    if row is None:
        logger.info(
            "settings_service: no row for module=%r screen=%r — using all-enabled defaults",
            module, screen,
        )
        return {"module": module, "screen": screen, **_DEFAULT_SETTINGS}

    resolved_screen = row["screen"] or None
    logger.info(
        "settings_service: resolved module=%r screen=%r -> row(screen=%r) domain=%s subdomain=%s geography=%s",
        module, screen, resolved_screen, row["use_domain"], row["use_subdomain"], row["use_geography"],
    )
    return {
        "module":        module,
        "screen":        resolved_screen,
        "use_domain":    row["use_domain"],
        "use_subdomain": row["use_subdomain"],
        "use_geography": row["use_geography"],
        "is_default":    False,
    }


async def save_settings(
    module: str,
    screen: str | None,
    use_domain: bool,
    use_subdomain: bool,
    use_geography: bool,
) -> dict:
    """
    Upsert one settings row. screen=None (or "") saves the module-level default.
    Writes immediately — no cache to invalidate.
    """
    pool = _get_pool()
    screen_norm = _normalise_screen(screen)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO settings (module, screen, use_domain, use_subdomain, use_geography, updated_at)
            VALUES ($1, $2, $3, $4, $5, now())
            ON CONFLICT (module, screen)
            DO UPDATE SET
                use_domain    = EXCLUDED.use_domain,
                use_subdomain = EXCLUDED.use_subdomain,
                use_geography = EXCLUDED.use_geography,
                updated_at    = now()
            """,
            module, screen_norm, use_domain, use_subdomain, use_geography,
        )

    logger.info(
        "settings_service: saved module=%r screen=%r domain=%s subdomain=%s geography=%s",
        module, screen or "(module default)", use_domain, use_subdomain, use_geography,
    )
    return {
        "module":        module,
        "screen":        screen or None,
        "use_domain":    use_domain,
        "use_subdomain": use_subdomain,
        "use_geography": use_geography,
        "is_default":    False,
    }
