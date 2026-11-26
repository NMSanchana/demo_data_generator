"""
One Postgres database per demo profile.

  - The platform DB (PG_DB, e.g. "demo-data") stays the control plane: ddpa_profiles records, for each
    profile, its db_name / db_status / db_error / current_version.
  - Each profile gets its OWN database on the same server with the same credentials, named
    ddp_<profile-slug>_<hash>. It is created automatically when the profile is first saved and dropped
    when the profile is deleted.
  - Inside a profile database there are 4 fixed tables (see PROFILE_DB_DDL). Generated rows are stored
    as JSONB, so a new screen or a changed field list never needs a table change.

Safe to call repeatedly: creation, migration and drop are all idempotent.
"""

import asyncio
import hashlib
import logging
import re
from collections import OrderedDict

import asyncpg

from service.postgres_client import connection_kwargs, get_pool, _init_connection

logger = logging.getLogger(__name__)

NAME_PREFIX = "ddp_"
# CREATE/DROP DATABASE cannot take parameters, so the identifier is validated hard before it is quoted.
_NAME_RE = re.compile(r"^ddp_[a-z0-9_]{1,58}$")
SCHEMA_VERSION = 1

# Open at most this many profile pools at once; the least recently used one is closed.
MAX_OPEN_POOLS = 6
POOL_MAX_SIZE = 2

PROFILE_DB_DDL = """
CREATE TABLE IF NOT EXISTS dd_schema_version (
    version     INT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per version of this profile's data (v1, v2, ...)
CREATE TABLE IF NOT EXISTS dd_runs (
    id                  BIGSERIAL PRIMARY KEY,
    version_no          INT NOT NULL UNIQUE,
    status              TEXT NOT NULL DEFAULT 'completed',
    is_current          BOOLEAN NOT NULL DEFAULT FALSE,
    top_up              BOOLEAN NOT NULL DEFAULT FALSE,
    domain              TEXT,
    subdomain           TEXT,
    geography           TEXT,
    resolved_geography  TEXT,
    catalog_version     TEXT,
    mode                TEXT,
    seed                TEXT,
    target_id           TEXT,
    target_name         TEXT,
    row_count_requested INT,
    note                TEXT,
    params              JSONB NOT NULL DEFAULT '{}',
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ
);

-- The screens generated inside a version
CREATE TABLE IF NOT EXISTS dd_run_screens (
    id             BIGSERIAL PRIMARY KEY,
    run_id         BIGINT NOT NULL REFERENCES dd_runs(id) ON DELETE CASCADE,
    feature_id     TEXT,
    feature_name   TEXT,
    data_template  TEXT,
    screen         TEXT NOT NULL,
    screen_id      TEXT,
    module         TEXT,
    kms_module     TEXT,
    resolved_via   TEXT,
    fields_source  TEXT,
    fields         JSONB NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL,
    message        TEXT,
    apm_ready      BOOLEAN NOT NULL DEFAULT FALSE,
    row_count      INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_dd_run_screens_run ON dd_run_screens(run_id);

-- Every generated row, tagged with its screen and version; the values are one JSONB block
CREATE TABLE IF NOT EXISTS dd_rows (
    id             BIGSERIAL PRIMARY KEY,
    run_screen_id  BIGINT NOT NULL REFERENCES dd_run_screens(id) ON DELETE CASCADE,
    run_id         BIGINT NOT NULL REFERENCES dd_runs(id) ON DELETE CASCADE,
    screen         TEXT NOT NULL,
    row_index      INT NOT NULL,
    data           JSONB NOT NULL,
    edited         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_dd_rows_screen ON dd_rows(screen, run_id);
CREATE INDEX IF NOT EXISTS ix_dd_rows_data ON dd_rows USING GIN (data);

INSERT INTO dd_schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
"""


# ── naming ────────────────────────────────────────────────────────────────────

def db_name_for(profile_id: str) -> str:
    """Deterministic from the profile ID (not its name, so renaming never breaks it).
    <= 4 + 40 + 1 + 8 = 53 chars, inside Postgres's 63-char identifier limit."""
    slug = re.sub(r"[^a-z0-9]+", "_", profile_id.lower()).strip("_")[:40] or "profile"
    digest = hashlib.sha1(profile_id.encode("utf-8")).hexdigest()[:8]
    return f"{NAME_PREFIX}{slug}_{digest}"


def _checked(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"Refusing unsafe profile database name: {name!r}")
    return name


def _quote(name: str) -> str:
    return '"' + _checked(name) + '"'


# ── create / migrate / drop ───────────────────────────────────────────────────

_locks: dict[str, asyncio.Lock] = {}


def _lock_for(name: str) -> asyncio.Lock:
    return _locks.setdefault(name, asyncio.Lock())


async def _admin_connection() -> asyncpg.Connection:
    """CREATE/DROP DATABASE must run outside a transaction; a plain connection to the platform DB does."""
    return await asyncpg.connect(**connection_kwargs(), timeout=15)


async def database_exists(name: str) -> bool:
    conn = await _admin_connection()
    try:
        return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", _checked(name)))
    finally:
        await conn.close()


async def _migrate(name: str) -> None:
    conn = await asyncpg.connect(**connection_kwargs(name), timeout=15)
    try:
        await conn.execute(PROFILE_DB_DDL)
    finally:
        await conn.close()


async def create_database(name: str) -> bool:
    """Create the database if missing and make sure its tables exist. Returns True if it was newly created."""
    async with _lock_for(name):
        created = False
        conn = await _admin_connection()
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", _checked(name))
            if not exists:
                try:
                    await conn.execute(f"CREATE DATABASE {_quote(name)}")
                    created = True
                    logger.info("profile_db: created database %s", name)
                except asyncpg.DuplicateDatabaseError:
                    pass  # another worker won the race -- fine
        finally:
            await conn.close()
        await _migrate(name)
        return created


async def drop_database(name: str) -> bool:
    """Drop the profile database. Returns True if it is gone afterwards."""
    async with _lock_for(name):
        await close_profile_pool(name)
        conn = await _admin_connection()
        try:
            try:
                await conn.execute(f"DROP DATABASE IF EXISTS {_quote(name)} WITH (FORCE)")  # Postgres 13+
            except asyncpg.PostgresSyntaxError:
                # older server: kick the other sessions out ourselves, then drop
                await conn.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
                    _checked(name),
                )
                await conn.execute(f"DROP DATABASE IF EXISTS {_quote(name)}")
            logger.info("profile_db: dropped database %s", name)
            return True
        finally:
            await conn.close()


async def list_profile_databases() -> list[str]:
    conn = await _admin_connection()
    try:
        rows = await conn.fetch("SELECT datname FROM pg_database WHERE datname LIKE 'ddp\\_%' ORDER BY datname")
        return [r["datname"] for r in rows]
    finally:
        await conn.close()


# ── per-profile connection pools (small, least-recently-used) ─────────────────

_pools: "OrderedDict[str, asyncpg.Pool]" = OrderedDict()


async def get_profile_pool(name: str) -> asyncpg.Pool:
    _checked(name)
    pool = _pools.get(name)
    if pool is not None:
        _pools.move_to_end(name)
        return pool
    pool = await asyncpg.create_pool(
        **connection_kwargs(name),
        min_size=0, max_size=POOL_MAX_SIZE,
        max_inactive_connection_lifetime=60,
        init=_init_connection,
    )
    _pools[name] = pool
    while len(_pools) > MAX_OPEN_POOLS:
        old_name, old_pool = _pools.popitem(last=False)
        await old_pool.close()
        logger.info("profile_db: closed idle pool for %s", old_name)
    return pool


async def close_profile_pool(name: str) -> None:
    pool = _pools.pop(name, None)
    if pool is not None:
        await pool.close()


async def close_all_pools() -> None:
    for name in list(_pools):
        await close_profile_pool(name)


# ── control-plane bookkeeping (ddpa_profiles) ─────────────────────────────────

async def _set_status(profile_id: str, status: str, error: str | None = None, db_name: str | None = None, created: bool = False) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ddpa_profiles SET
                db_status = $2,
                db_error = $3,
                db_name = COALESCE($4, db_name),
                db_created_at = CASE WHEN $5 THEN NOW() ELSE db_created_at END
            WHERE id = $1
            """,
            profile_id, status, error, db_name, created,
        )


async def ensure_profile_db(profile_id: str) -> dict:
    """Make sure this profile's database exists and is migrated. Never raises: a failure is recorded on the
    profile (db_status='error' + db_error) so the UI can show it and offer Retry.
    Returns {"db_name", "db_status", "db_error"}."""
    name = db_name_for(profile_id)
    await _set_status(profile_id, "creating", None, name)
    try:
        created = await create_database(name)
        await _set_status(profile_id, "ready", None, name, created)
        return {"db_name": name, "db_status": "ready", "db_error": None}
    except Exception as e:  # noqa: BLE001 -- report, don't crash the profile save
        logger.exception("profile_db: could not prepare database %s for profile %s", name, profile_id)
        msg = f"{type(e).__name__}: {e}"
        await _set_status(profile_id, "error", msg[:500], name)
        return {"db_name": name, "db_status": "error", "db_error": msg[:500]}


async def backfill_profile_dbs() -> None:
    """On startup: give every profile that has no ready database one, and report leftover databases."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, db_name, db_status FROM ddpa_profiles")
        known = {r["db_name"] or db_name_for(r["id"]) for r in rows}
        for r in rows:
            if r["db_status"] != "ready":
                logger.info("profile_db: backfill for profile %s (status=%s)", r["id"], r["db_status"])
                await ensure_profile_db(r["id"])
        orphans = [d for d in await list_profile_databases() if d not in known]
        if orphans:
            logger.warning("profile_db: databases with no profile (not dropped automatically): %s", orphans)
    except Exception:  # noqa: BLE001
        logger.exception("profile_db: startup backfill failed")