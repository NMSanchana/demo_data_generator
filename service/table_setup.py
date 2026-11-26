"""
Creates all DEMO DATA platform store tables on startup (IF NOT EXISTS).
Safe to run every time the server starts -- idempotent.
Called from main.py on_startup after the PG pool is initialised.
"""

import logging
from service.postgres_client import get_pool

logger = logging.getLogger(__name__)

CREATE_TABLES = """
-- Profiles (with feature overrides stored as JSONB)
CREATE TABLE IF NOT EXISTS ddpa_profiles (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'draft',
    selections    JSONB NOT NULL DEFAULT '{}',
    overrides     JSONB NOT NULL DEFAULT '{}',
    catalog_version TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Per-profile database bookkeeping (see service/profile_db.py). Idempotent for databases created before this existed.
ALTER TABLE ddpa_profiles ADD COLUMN IF NOT EXISTS db_name         TEXT;
ALTER TABLE ddpa_profiles ADD COLUMN IF NOT EXISTS db_status       TEXT NOT NULL DEFAULT 'none';
ALTER TABLE ddpa_profiles ADD COLUMN IF NOT EXISTS db_error        TEXT;
ALTER TABLE ddpa_profiles ADD COLUMN IF NOT EXISTS db_created_at   TIMESTAMPTZ;
ALTER TABLE ddpa_profiles ADD COLUMN IF NOT EXISTS current_version INT;

-- Applicability rules
CREATE TABLE IF NOT EXISTS ddpa_rules (
    id            TEXT PRIMARY KEY,
    feature_id    TEXT NOT NULL,
    dimension_id  TEXT NOT NULL,
    value_id      TEXT NOT NULL,
    applicability TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Targets: a named destination (name + environment) recorded with each run
CREATE TABLE IF NOT EXISTS ddpa_targets (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    environment       TEXT NOT NULL DEFAULT 'dev',
    is_default        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Dimension values (dimension structure stays in seed; values are user-managed)
CREATE TABLE IF NOT EXISTS ddpa_dimension_values (
    id            TEXT PRIMARY KEY,
    dimension_id  TEXT NOT NULL,
    name          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(dimension_id, name)
);
"""


async def run_migrations() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLES)
    logger.info("DB migrations complete -- all DEMO DATA platform tables ready.")