import hashlib
import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from service import profiles_service, profile_db, run_store

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


@router.get("/GetProfileDb/{profile_id}")
async def get_profile_db(profile_id: str):
    p = await profiles_service.get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {k: p.get(k) for k in ("id", "db_name", "db_status", "db_error", "db_created_at", "current_version")}


@router.post("/CreateProfileDb/{profile_id}")
async def create_profile_db(profile_id: str):
    """Retry button: (re)create and migrate the profile's database. Safe to call on a healthy one."""
    if not await profiles_service.get_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return await profile_db.ensure_profile_db(profile_id)


@router.get("/GetProfileRuns/{profile_id}")
async def get_profile_runs(profile_id: str):
    """The stored versions (v1, v2, ...) of this profile's data, newest first."""
    return await run_store.list_runs(profile_id)


@router.get("/GetRunBundle/{profile_id}/{run_id}")
async def get_run_bundle(profile_id: str, run_id: int):
    """Download one stored version (details, screens and rows) as a JSON file."""
    bundle = await run_store.get_bundle(profile_id, run_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Version not found")
    body = json.dumps(jsonable_encoder(bundle), ensure_ascii=False, indent=2).encode("utf-8")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", profile_id).strip("_") or "profile"
    filename = f"{slug}-v{bundle['version']['version_no']}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Bundle-SHA256": hashlib.sha256(body).hexdigest(),
            "Access-Control-Expose-Headers": "Content-Disposition, X-Bundle-SHA256",
        },
    )


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