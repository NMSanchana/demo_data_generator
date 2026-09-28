from fastapi import APIRouter, HTTPException
from service import dimensions_service

router = APIRouter(prefix="/api/v1/store", tags=["dimensions"])


@router.get("/GetDimensionValues")
async def get_dimension_values(dimension_id: str | None = None):
    return await dimensions_service.list_dimension_values(dimension_id)


@router.post("/SaveDimensionValue")
async def save_dimension_value(body: dict):
    return await dimensions_service.upsert_dimension_value(body)


@router.delete("/DeleteDimensionValue/{value_id}")
async def remove_dimension_value(value_id: str):
    ok = await dimensions_service.delete_dimension_value(value_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Dimension value not found")
    return {"ok": True}