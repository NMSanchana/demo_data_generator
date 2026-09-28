from fastapi import APIRouter, HTTPException
from service import targets_service

router = APIRouter(prefix="/api/v1/store", tags=["targets"])


@router.get("/GetTargets")
async def get_targets():
    return await targets_service.list_targets()


@router.post("/SaveTarget")
async def save_target(body: dict):
    return await targets_service.upsert_target(body)


@router.delete("/DeleteTarget/{target_id}")
async def remove_target(target_id: str):
    ok = await targets_service.delete_target(target_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Target not found")
    return {"ok": True}


@router.get("/GetEndpoints")
async def get_endpoints(target_id: str | None = None):
    return await targets_service.list_endpoints(target_id)


@router.post("/SaveEndpoint")
async def save_endpoint(body: dict):
    return await targets_service.upsert_endpoint(body)


@router.delete("/DeleteEndpoint/{endpoint_id}")
async def remove_endpoint(endpoint_id: str):
    ok = await targets_service.delete_endpoint(endpoint_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return {"ok": True}