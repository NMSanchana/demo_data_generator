"""CRUD for profiles and applicability rules."""
import json
from service.postgres_client import get_pool


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
            RETURNING *
        """,
        p["id"], p["name"], p.get("description", ""),
        p.get("status", "draft"),
        p.get("selections", {}),
        p.get("overrides", {}),
        p.get("catalogVersion") or p.get("catalog_version"),
        )
        return dict(row)


async def delete_profile(profile_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_profiles WHERE id = $1", profile_id)
        return result == "DELETE 1"


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