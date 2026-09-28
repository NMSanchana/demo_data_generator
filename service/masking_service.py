"""CRUD for masking rules."""
import json
from service.postgres_client import get_pool


async def list_masking_rules() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM ddpa_masking_rules ORDER BY created_at")
        return [dict(r) for r in rows]


async def upsert_masking_rule(r: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_masking_rules (id, field_pattern, strategy, config)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                field_pattern = EXCLUDED.field_pattern,
                strategy      = EXCLUDED.strategy,
                config        = EXCLUDED.config
            RETURNING *
        """,
        r["id"], r["fieldPattern"], r["strategy"], r.get("config", {}),
        )
        return dict(row)


async def delete_masking_rule(rule_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_masking_rules WHERE id = $1", rule_id)
        return result == "DELETE 1"