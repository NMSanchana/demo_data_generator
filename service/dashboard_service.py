"""Dashboard summary stats and recent runs.

Every Generate is stored as a version in the profile's own database (service/run_store.py), so that is where the
numbers come from. (The old ddpa_occurrences table is only written by scheduled runs and stays empty otherwise.)
"""
from service import profiles_service, run_store


async def get_stats() -> dict:
    profiles = await profiles_service.list_profiles()
    runs = await run_store.list_all_runs()
    durations = [r["duration_seconds"] for r in runs if r.get("duration_seconds") is not None]
    return {
        "total_runs": len(runs),
        "completed_runs": sum(1 for r in runs if r["status"] == "completed"),
        "partial_runs": sum(1 for r in runs if r["status"] == "partial"),
        "failed_runs": sum(1 for r in runs if r["status"] == "failed"),
        "total_rows": sum(r["row_count"] for r in runs),
        "total_profiles": len(profiles),
        "profiles_with_data": len({r["profile_id"] for r in runs}),
        # only versions stored after the timing was added have a duration; older ones are left out of the average
        "avg_generation_seconds": round(sum(durations) / len(durations), 1) if durations else None,
    }


async def get_recent_runs(limit: int = 5) -> list[dict]:
    return await run_store.list_all_runs(limit)