"""Dashboard summary stats and recent runs from the DB."""
from service.postgres_client import get_pool


async def get_stats() -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        total_runs = await conn.fetchval("SELECT COUNT(*) FROM ddpa_occurrences") or 0
        completed = await conn.fetchval("SELECT COUNT(*) FROM ddpa_occurrences WHERE status = 'completed'") or 0
        profile_count = await conn.fetchval("SELECT COUNT(*) FROM ddpa_profiles") or 0

        avg_seconds = await conn.fetchval("""
            SELECT AVG(EXTRACT(EPOCH FROM (finished_at - started_at)))
            FROM ddpa_occurrences
            WHERE status = 'completed' AND started_at IS NOT NULL AND finished_at IS NOT NULL
        """)

        return {
            "total_runs": total_runs,
            "completed_runs": completed,
            "active_profiles": profile_count,
            "avg_generation_seconds": round(float(avg_seconds), 1) if avg_seconds else None,
        }


async def get_recent_runs(limit: int = 5) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT o.id, o.status, o.started_at, o.finished_at, o.result,
                   s.profile_id
            FROM ddpa_occurrences o
            LEFT JOIN ddpa_schedules s ON s.id = o.schedule_id
            ORDER BY o.created_at DESC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]