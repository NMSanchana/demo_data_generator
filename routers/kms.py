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


@router.get("/screens/{screen_id}/fields", summary="Field names KMS holds for one screen (what /generate will use)")
async def kms_screen_fields(screen_id: str):
    try:
        hit = await kms_catalog.lookup_screen(screen_id)
        if hit is None:
            raise HTTPException(status_code=404, detail=f"'{screen_id}' is not a screen in KMS")
        names = await kms_catalog.get_screen_field_names(hit["screen_id"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"KMS unavailable: {e}")
    return {"screen": hit, "fields": names}