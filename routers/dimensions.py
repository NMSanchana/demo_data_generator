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


@router.get("/GetDimensions")
async def get_dimensions():
    return await dimensions_service.list_dimensions()


@router.post("/SaveDimension")
async def save_dimension(body: dict):
    if not str(body.get("name", "")).strip():
        raise HTTPException(status_code=422, detail="A dimension needs a name")
    return await dimensions_service.upsert_dimension(body)


@router.delete("/DeleteDimension/{dimension_id}")
async def remove_dimension(dimension_id: str):
    ok = await dimensions_service.delete_dimension(dimension_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Dimension not found")
    return {"ok": True}


@router.get("/GetDimensionOrder")
async def get_dimension_order():
    return await dimensions_service.list_dimension_order()


@router.post("/SaveDimensionOrder")
async def save_dimension_order(body: dict):
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=422, detail="items must be a non-empty list")
    return await dimensions_service.save_dimension_order(items)