"""CRUD for targets.

A target is just a name and an environment (dev / demo / uat) that is stored with each run, so History can
show where a version was meant for. It holds no connection settings: the backend saves to the one APM host
set in .env.

Databases created before this change still have the old extra columns (db_url, api_base_url, auth_style,
rate_limit_per_min, metadata_schema, mode, workspace_id, protection, db_kind). They all have defaults or are
nullable, so they are simply left alone and never read.
"""
from service.postgres_client import get_pool


async def list_targets() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, environment, is_default, created_at FROM ddpa_targets ORDER BY created_at"
        )
        return [dict(r) for r in rows]


async def upsert_target(t: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ddpa_targets (id, name, environment, is_default)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE SET
                name        = EXCLUDED.name,
                environment = EXCLUDED.environment,
                is_default  = EXCLUDED.is_default
            RETURNING id, name, environment, is_default, created_at
            """,
            t["id"], t["name"], t.get("environment", "dev"), bool(t.get("isDefault", False)),
        )
        return dict(row)


async def delete_target(target_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_targets WHERE id = $1", target_id)
        return result == "DELETE 1"