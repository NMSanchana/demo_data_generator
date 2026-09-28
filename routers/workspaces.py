from fastapi import APIRouter, HTTPException
from service import workspaces_service

router = APIRouter(prefix="/api/v1/store", tags=["workspaces"])


@router.get("/GetWorkspaces")
async def get_workspaces():
    return await workspaces_service.list_workspaces()


@router.post("/SaveWorkspace")
async def save_workspace(body: dict):
    return await workspaces_service.upsert_workspace(body)


@router.delete("/DeleteWorkspace/{workspace_id}")
async def remove_workspace(workspace_id: str):
    ok = await workspaces_service.delete_workspace(workspace_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {"ok": True}