"""CRUD for workspaces."""
from service.postgres_client import get_pool


async def list_workspaces() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM ddpa_workspaces ORDER BY created_at")
        return [dict(r) for r in rows]


async def upsert_workspace(w: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_workspaces (id, name, description, owner_id, settings)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                name        = EXCLUDED.name,
                description = EXCLUDED.description,
                owner_id    = EXCLUDED.owner_id,
                settings    = EXCLUDED.settings
            RETURNING *
        """,
        w["id"], w["name"], w.get("description", ""),
        w.get("ownerId", ""), w.get("settings", {}),
        )
        return dict(row)


async def delete_workspace(workspace_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_workspaces WHERE id = $1", workspace_id)
        return result == "DELETE 1"
