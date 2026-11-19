from fastapi import APIRouter, HTTPException
from service import profiles_service

router = APIRouter(prefix="/api/v1/store", tags=["profiles"])


@router.get("/GetProfiles")
async def get_profiles():
    return await profiles_service.list_profiles()


@router.post("/SaveProfile")
async def save_profile(body: dict):
    return await profiles_service.upsert_profile(body)


@router.delete("/DeleteProfile/{profile_id}")
async def remove_profile(profile_id: str):
    ok = await profiles_service.delete_profile(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"ok": True}


@router.get("/GetRules")
async def get_rules():
    return await profiles_service.list_rules()


@router.post("/SaveRule")
async def save_rule(body: dict):
    return await profiles_service.upsert_rule(body)


@router.delete("/DeleteRule/{rule_id}")
async def remove_rule(rule_id: str):
    ok = await profiles_service.delete_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"ok": True}


MAX_BULK = 5000


@router.post("/SaveRulesBulk")
async def save_rules_bulk(body: dict):
    rules = body.get("rules") or []
    if len(rules) > MAX_BULK:
        raise HTTPException(status_code=400, detail=f"At most {MAX_BULK} rules per request")
    for r in rules:
        if r.get("applicability") not in ("critical", "optional", "not_applicable"):
            raise HTTPException(status_code=400, detail=f"Invalid applicability: {r.get('applicability')!r}")
    return {"saved": await profiles_service.upsert_rules_bulk(rules)}


@router.post("/DeleteRulesBulk")
async def delete_rules_bulk(body: dict):
    ids = body.get("ids") or []
    if len(ids) > MAX_BULK:
        raise HTTPException(status_code=400, detail=f"At most {MAX_BULK} ids per request")
    return {"deleted": await profiles_service.delete_rules_bulk(ids)}