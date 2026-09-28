from fastapi import APIRouter, HTTPException
from service import masking_service

router = APIRouter(prefix="/api/v1/store", tags=["masking"])


@router.get("/GetMaskingRules")
async def get_masking_rules():
    return await masking_service.list_masking_rules()


@router.post("/SaveMaskingRule")
async def save_masking_rule(body: dict):
    return await masking_service.upsert_masking_rule(body)


@router.delete("/DeleteMaskingRule/{rule_id}")
async def remove_masking_rule(rule_id: str):
    ok = await masking_service.delete_masking_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Masking rule not found")
    return {"ok": True}