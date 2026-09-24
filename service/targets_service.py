"""CRUD for targets and endpoint mappings."""
from service.postgres_client import get_pool


# ── Targets ───────────────────────────────────────────────────────────────────

async def list_targets() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM ddpa_targets ORDER BY created_at")
        return [dict(r) for r in rows]


async def upsert_target(t: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_targets
                (id, name, environment, db_kind, db_url, api_base_url, auth_style,
                 rate_limit_per_min, metadata_schema, mode, is_default, workspace_id, protection)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            ON CONFLICT (id) DO UPDATE SET
                name              = EXCLUDED.name,
                environment       = EXCLUDED.environment,
                db_kind           = EXCLUDED.db_kind,
                db_url            = EXCLUDED.db_url,
                api_base_url      = EXCLUDED.api_base_url,
                auth_style        = EXCLUDED.auth_style,
                rate_limit_per_min = EXCLUDED.rate_limit_per_min,
                metadata_schema   = EXCLUDED.metadata_schema,
                mode              = EXCLUDED.mode,
                is_default        = EXCLUDED.is_default,
                workspace_id      = EXCLUDED.workspace_id,
                protection        = EXCLUDED.protection
            RETURNING *
        """,
        t["id"], t["name"], t.get("environment", "dev"),
        t.get("dbKind", "postgresql"),
        t.get("dbUrl", ""), t.get("apiBaseUrl", ""),
        t.get("authStyle", "none"),
        t.get("rateLimitPerMin", 0),
        t.get("metadataSchema", "ddpa_meta"),
        t.get("mode", "api"),
        t.get("isDefault", False),
        t.get("workspaceId", ""),
        t.get("protection", "open"),
        )
        return dict(row)


async def delete_target(target_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_targets WHERE id = $1", target_id)
        return result == "DELETE 1"


# ── Endpoints ─────────────────────────────────────────────────────────────────

async def list_endpoints(target_id: str | None = None) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        if target_id:
            rows = await conn.fetch("SELECT * FROM ddpa_endpoints WHERE target_id = $1 ORDER BY created_at", target_id)
        else:
            rows = await conn.fetch("SELECT * FROM ddpa_endpoints ORDER BY created_at")
        return [dict(r) for r in rows]


async def upsert_endpoint(e: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_endpoints (id, target_id, data_template, method, path, fallback_to_table)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE SET
                target_id         = EXCLUDED.target_id,
                data_template     = EXCLUDED.data_template,
                method            = EXCLUDED.method,
                path              = EXCLUDED.path,
                fallback_to_table = EXCLUDED.fallback_to_table
            RETURNING *
        """,
        e["id"], e["targetId"], e["dataTemplate"],
        e.get("method", "POST"), e["path"],
        e.get("fallbackToTable", True),
        )
        return dict(row)


async def delete_endpoint(endpoint_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_endpoints WHERE id = $1", endpoint_id)
        return result == "DELETE 1"
