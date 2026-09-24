from fastapi import APIRouter, HTTPException
from service import schedules_service

router = APIRouter(prefix="/store/schedules", tags=["schedules"])


@router.get("")
async def get_schedules():
    return await schedules_service.list_schedules()


@router.post("")
async def save_schedule(body: dict):
    return await schedules_service.upsert_schedule(body)


@router.delete("/{schedule_id}")
async def remove_schedule(schedule_id: str):
    ok = await schedules_service.delete_schedule(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"ok": True}


@router.get("/occurrences")
async def get_occurrences(schedule_id: str | None = None):
    return await schedules_service.list_occurrences(schedule_id)


@router.post("/occurrences")
async def save_occurrence(body: dict):
    return await schedules_service.upsert_occurrence(body)
