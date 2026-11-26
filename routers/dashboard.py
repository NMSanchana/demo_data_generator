from fastapi import APIRouter, Query
from service import dashboard_service

router = APIRouter(prefix="/api/v1/store", tags=["dashboard"])


@router.get("/GetDashboardStats")
async def get_stats():
    return await dashboard_service.get_stats()


@router.get("/GetRecentRuns")
async def get_recent_runs(limit: int = Query(5, ge=1, le=1000)):
    """Stored versions of all profiles, most recently active first."""
    return await dashboard_service.get_recent_runs(limit)