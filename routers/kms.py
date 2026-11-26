from fastapi import APIRouter, HTTPException

from service import kms_catalog

router = APIRouter(prefix="/kms", tags=["kms"])


@router.get("/health", summary="Is the KMS (Qdrant) collection reachable?")
async def kms_health():
    return await kms_catalog.health()


@router.get("/catalog", summary="Product > Module > Capability > Feature tree, straight from KMS")
async def kms_catalog_tree(refresh: bool = False):
    try:
        snap = await kms_catalog.get_snapshot(force=refresh)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KMS unavailable: {e}")
    return snap["catalog"]


@router.get("/relationships", summary="Entities + relationships derived from KMS field data")
async def kms_relationships(module: str | None = None, min_confidence: float = 0.0, refresh: bool = False):
    try:
        snap = await kms_catalog.get_snapshot(force=refresh)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KMS unavailable: {e}")
    return kms_catalog.filter_relationships(snap["relationships"], module, min_confidence)


@router.get("/screens/{screen_id}/fields", summary="Fields KMS holds for one screen (what /generate will use)")
async def kms_screen_fields(screen_id: str, detail: bool = False):
    """detail=true also returns what was dropped (audit columns, grid/joined-table columns) and why,
    plus each kept field's table/column/type -- use it to check KMS field data before a run."""
    try:
        hit = await kms_catalog.lookup_screen(screen_id)
        if hit is None:
            raise HTTPException(status_code=404, detail=f"'{screen_id}' is not a screen in KMS")
        info = await kms_catalog.get_screen_fields(hit["screen_id"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KMS unavailable: {e}")
    names = [f["name"] for f in info["fields"]]
    if not detail:
        return {"screen": hit, "fields": names}
    return {"screen": hit, "fields": names, "basis": info.get("basis"), "mainTable": info["mainTable"],
            "totalInKms": info["totalInKms"], "kept": info["fields"], "dropped": info["dropped"]}


@router.get("/modules", summary="Every KMS module code and the APM module it resolves to")
async def kms_modules():
    """matchedBy = alias | exact | fuzzy | none. For 'none', /generate relies on the LLM fallback;
    add the code to localedata/module_aliases.json to make it deterministic."""
    try:
        return {"modules": await kms_catalog.module_report()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KMS unavailable: {e}")


@router.get("/screens-with-fields", summary="Which screens have field data in KMS (these generate without GitLab)")
async def kms_screens_with_fields():
    """{screens: {SCREEN_ID: number of field records}}. A screen missing here has no KMS field data."""
    try:
        counts = await kms_catalog.screens_with_fields()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KMS unavailable: {e}")
    return {"count": len(counts), "screens": counts}