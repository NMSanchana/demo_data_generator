"""CRUD for approval requests."""
import json
from service.postgres_client import get_pool


async def list_approvals(status: str | None = None) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM ddpa_approvals WHERE status = $1 ORDER BY created_at DESC", status
            )
        else:
            rows = await conn.fetch("SELECT * FROM ddpa_approvals ORDER BY created_at DESC")
        return [dict(r) for r in rows]


async def upsert_approval(a: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_approvals
                (id, run_id, feature_id, screen, row_data, status, requested_by, reviewed_by, reviewed_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
            ON CONFLICT (id) DO UPDATE SET
                status      = EXCLUDED.status,
                reviewed_by = EXCLUDED.reviewed_by,
                reviewed_at = EXCLUDED.reviewed_at
            RETURNING *
        """,
        a["id"], a.get("runId"), a.get("featureId"),
        a.get("screen"), a.get("rowData", {}),
        a.get("status", "pending"),
        a.get("requestedBy"), a.get("reviewedBy"), a.get("reviewedAt"),
        )
        return dict(row)


async def delete_approval(approval_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_approvals WHERE id = $1", approval_id)
        return result == "DELETE 1"