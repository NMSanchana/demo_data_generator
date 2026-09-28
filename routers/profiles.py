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