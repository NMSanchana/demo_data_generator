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

-- Applicability rules
CREATE TABLE IF NOT EXISTS ddpa_rules (
    id            TEXT PRIMARY KEY,
    feature_id    TEXT NOT NULL,
    dimension_id  TEXT NOT NULL,
    value_id      TEXT NOT NULL,
    applicability TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Targets (APM connection configs)
CREATE TABLE IF NOT EXISTS ddpa_targets (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    environment       TEXT NOT NULL DEFAULT 'dev',
    db_kind           TEXT NOT NULL DEFAULT 'postgresql',
    db_url            TEXT,
    api_base_url      TEXT,
    auth_style        TEXT NOT NULL DEFAULT 'none',
    rate_limit_per_min INT NOT NULL DEFAULT 0,
    metadata_schema   TEXT,
    mode              TEXT NOT NULL DEFAULT 'api',
    is_default        BOOLEAN NOT NULL DEFAULT FALSE,
    workspace_id      TEXT,
    protection        TEXT NOT NULL DEFAULT 'open',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Endpoint mappings (dataTemplate -> API path per target)
CREATE TABLE IF NOT EXISTS ddpa_endpoints (
    id                TEXT PRIMARY KEY,
    target_id         TEXT NOT NULL REFERENCES ddpa_targets(id) ON DELETE CASCADE,
    data_template     TEXT NOT NULL,
    method            TEXT NOT NULL DEFAULT 'POST',
    path              TEXT NOT NULL,
    fallback_to_table BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Workspaces
CREATE TABLE IF NOT EXISTS ddpa_workspaces (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    owner_id      TEXT,
    settings      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Schedules
CREATE TABLE IF NOT EXISTS ddpa_schedules (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT,
    profile_id    TEXT,
    target_id     TEXT,
    cron          TEXT,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    config        JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Schedule occurrences (run history)
CREATE TABLE IF NOT EXISTS ddpa_occurrences (
    id            TEXT PRIMARY KEY,
    schedule_id   TEXT NOT NULL REFERENCES ddpa_schedules(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'pending',
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    result        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Approvals
CREATE TABLE IF NOT EXISTS ddpa_approvals (
    id            TEXT PRIMARY KEY,
    run_id        TEXT,
    feature_id    TEXT,
    screen        TEXT,
    row_data      JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',
    requested_by  TEXT,
    reviewed_by   TEXT,
    reviewed_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Masking rules
CREATE TABLE IF NOT EXISTS ddpa_masking_rules (
    id            TEXT PRIMARY KEY,
    field_pattern TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    config        JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
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