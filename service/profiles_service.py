"""CRUD for profiles and applicability rules."""
import json
import logging

from service import profile_db
from service.postgres_client import get_pool

logger = logging.getLogger(__name__)


# ── Profiles ──────────────────────────────────────────────────────────────────

async def list_profiles() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM ddpa_profiles ORDER BY created_at")
        return [dict(r) for r in rows]


async def get_profile(profile_id: str) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM ddpa_profiles WHERE id = $1", profile_id)
        return dict(row) if row else None


async def upsert_profile(p: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_profiles (id, name, description, status, selections, overrides, catalog_version, updated_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, NOW())
            ON CONFLICT (id) DO UPDATE SET
                name            = EXCLUDED.name,
                description     = EXCLUDED.description,
                status          = EXCLUDED.status,
                selections      = EXCLUDED.selections,
                overrides       = EXCLUDED.overrides,
                catalog_version = EXCLUDED.catalog_version,
                updated_at      = NOW()
            RETURNING *, (xmax = 0) AS inserted
        """,
        p["id"], p["name"], p.get("description", ""),
        p.get("status", "draft"),
        p.get("selections", {}),
        p.get("overrides", {}),
        p.get("catalogVersion") or p.get("catalog_version"),
        )
        result = dict(row)

    # A brand-new profile gets its own database right away. Later edits never re-create it (the page saves
    # on every change); a failure is recorded on the profile (db_status='error') and never blocks the save.
    if result.pop("inserted", False):
        await profile_db.ensure_profile_db(result["id"])
        refreshed = await get_profile(result["id"])
        if refreshed:
            result = refreshed
    return result


async def delete_profile(profile_id: str) -> bool:
    """Delete the profile AND drop its database with every version in it."""
    existing = await get_profile(profile_id)
    if existing is None:
        return False
    db_name = existing.get("db_name") or profile_db.db_name_for(profile_id)

    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_profiles WHERE id = $1", profile_id)
    if result != "DELETE 1":
        return False

    try:
        await profile_db.drop_database(db_name)
    except Exception:  # noqa: BLE001 -- the profile is already gone; leave the DB and say so in the log
        logger.exception("profile_db: could not drop %s after deleting profile %s -- drop it manually", db_name, profile_id)
    return True


# ── Rules ─────────────────────────────────────────────────────────────────────

async def list_rules() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM ddpa_rules ORDER BY created_at")
        return [dict(r) for r in rows]


async def upsert_rule(r: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_rules (id, feature_id, dimension_id, value_id, applicability)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET
                feature_id   = EXCLUDED.feature_id,
                dimension_id = EXCLUDED.dimension_id,
                value_id     = EXCLUDED.value_id,
                applicability = EXCLUDED.applicability
            RETURNING *
        """,
        r["id"], r["featureId"], r["dimensionId"], r["valueId"], r["applicability"],
        )
        return dict(row)


async def delete_rule(rule_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_rules WHERE id = $1", rule_id)
        return result == "DELETE 1"


async def upsert_rules_bulk(rules: list[dict]) -> int:
    """Insert/update many rules in ONE transaction (used by the bulk rule builder).
    Rule ids are deterministic (rule-<feature>-<dimension>-<value>) so re-applying the same
    scope updates rows instead of duplicating them."""
    if not rules:
        return 0
    rows = [(r["id"], r["featureId"], r["dimensionId"], r["valueId"], r["applicability"]) for r in rules]
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany("""
                INSERT INTO ddpa_rules (id, feature_id, dimension_id, value_id, applicability)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO UPDATE SET
                    feature_id    = EXCLUDED.feature_id,
                    dimension_id  = EXCLUDED.dimension_id,
                    value_id      = EXCLUDED.value_id,
                    applicability = EXCLUDED.applicability
            """, rows)
    return len(rows)


async def delete_rules_bulk(rule_ids: list[str]) -> int:
    if not rule_ids:
        return 0
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_rules WHERE id = ANY($1::text[])", rule_ids)
        return int(result.split()[-1])