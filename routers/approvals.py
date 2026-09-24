from fastapi import APIRouter, HTTPException
from service import approvals_service

router = APIRouter(prefix="/store/approvals", tags=["approvals"])


@router.get("")
async def get_approvals(status: str | None = None):
    return await approvals_service.list_approvals(status)


@router.post("")
async def save_approval(body: dict):
    return await approvals_service.upsert_approval(body)


@router.delete("/{approval_id}")
async def remove_approval(approval_id: str):
    ok = await approvals_service.delete_approval(approval_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Approval not found")
    return {"ok": True}
