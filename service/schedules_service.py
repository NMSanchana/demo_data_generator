"""CRUD for schedules and occurrences."""
from service.postgres_client import get_pool


# ── Schedules ─────────────────────────────────────────────────────────────────

async def list_schedules() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM ddpa_schedules ORDER BY created_at")
        return [dict(r) for r in rows]


async def upsert_schedule(s: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_schedules (id, workspace_id, profile_id, target_id, cron, enabled, config)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                profile_id   = EXCLUDED.profile_id,
                target_id    = EXCLUDED.target_id,
                cron         = EXCLUDED.cron,
                enabled      = EXCLUDED.enabled,
                config       = EXCLUDED.config
            RETURNING *
        """,
        s["id"], s.get("workspaceId"), s.get("profileId"),
        s.get("targetId"), s.get("cron"),
        s.get("enabled", True), s.get("config", {}),
        )
        return dict(row)


async def delete_schedule(schedule_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_schedules WHERE id = $1", schedule_id)
        return result == "DELETE 1"


# ── Occurrences ───────────────────────────────────────────────────────────────

async def list_occurrences(schedule_id: str | None = None) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        if schedule_id:
            rows = await conn.fetch(
                "SELECT * FROM ddpa_occurrences WHERE schedule_id = $1 ORDER BY created_at DESC",
                schedule_id
            )
        else:
            rows = await conn.fetch("SELECT * FROM ddpa_occurrences ORDER BY created_at DESC")
        return [dict(r) for r in rows]


async def upsert_occurrence(o: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_occurrences (id, schedule_id, status, started_at, finished_at, result)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                status      = EXCLUDED.status,
                started_at  = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                result      = EXCLUDED.result
            RETURNING *
        """,
        o["id"], o["scheduleId"], o.get("status", "pending"),
        o.get("startedAt"), o.get("finishedAt"), o.get("result", {}),
        )
        return dict(row)
