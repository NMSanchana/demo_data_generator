from fastapi import APIRouter
from service import dashboard_service

router = APIRouter(prefix="/api/v1/store", tags=["dashboard"])


@router.get("/GetDashboardStats")
async def get_stats():
    return await dashboard_service.get_stats()


@router.get("/GetRecentRuns")
async def get_recent_runs(limit: int = 5):
    return await dashboard_service.get_recent_runs(limit)