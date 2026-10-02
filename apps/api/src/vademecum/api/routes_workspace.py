"""The learner's whole workspace, as one thing: what it is, and deleting it.

Deletion is real (AGENTS.md): the learner's database, originals, exports,
backups and pending turns go, and every token their assistant holds is
revoked. It is refused in single tenancy, where the owner's workspace is the
data directory itself and is removed by the owner, not by a request. Nothing
here returns a learner identifier or a path.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request

from ..storage.sources import ConflictError
from ..tenancy import Workspace
from . import schemas
from .deps import get_workspace

router = APIRouter(prefix="/workspace", tags=["workspace"])

CONFIRMATION = "delete everything"


class WorkspaceDeletion(schemas.Strict):
    confirm: Literal["delete everything"]


@router.get("")
def describe(request: Request, workspace: Workspace = Depends(get_workspace)) -> dict:
    return {
        "tenancy": request.app.state.settings.tenancy,
        "model_mode": request.app.state.model_mode,
        "deletable": request.app.state.settings.tenancy == "multi",
        "note": (
            "Deleting removes your material, learning points, questions, answers and "
            "flags from Vademecum, and signs your assistant out. It does not remove "
            "anything from your ChatGPT conversation history."
        ),
    }


@router.delete("")
async def delete_workspace(
    payload: WorkspaceDeletion,
    request: Request,
    workspace: Workspace = Depends(get_workspace),
) -> dict:
    settings = request.app.state.settings
    if settings.tenancy != "multi":
        raise ConflictError(
            "not_in_this_mode",
            "This workspace is the data directory on this Mac; remove it there.",
        )
    revoked = request.app.state.token_resolver.revoke_all_for(workspace.learner_id)
    removed = await request.app.state.workspaces.delete(workspace.learner_id)
    return {"deleted": True, "files_removed": removed, "connections_revoked": revoked}
