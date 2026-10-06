"""The model connection: status, sign-in, cancel, restart.

Five routes, and none of them sends a prompt anywhere; one only reads the summary
the Mac kept of what it last saw, for the phone's copy. This slice reads account
and rate-limit state and performs ChatGPT device-code sign-in; there is no turn,
no thread and no tutor call behind any of it.

Every response here is already sanitised by ``appserver.account``. What is
absent is the point: no account email, no account or workspace identifier, no
App Server error text, and -- outside the single live response to
``POST /model/login`` -- no verification URL and no one-time code.
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import Request, APIRouter, Depends, Response, status

from ..appserver import ModelBridge
from ..appserver.account import DeviceLogin, ModelStatus
from . import schemas
from .deps import get_connection, get_model_bridge

router = APIRouter(prefix="/model", tags=["model"])


def _status_out(value: ModelStatus) -> schemas.ModelStatusOut:
    limits = value.rate_limits
    return schemas.ModelStatusOut(
        state=value.state,
        signed_in=value.signed_in,
        plan=value.plan,
        rate_limits=(
            None
            if limits is None
            else schemas.RateLimitsOut(
                primary=(
                    None
                    if limits.primary is None
                    else schemas.UsageWindowOut(**vars(limits.primary))
                ),
                secondary=(
                    None
                    if limits.secondary is None
                    else schemas.UsageWindowOut(**vars(limits.secondary))
                ),
                limited=limits.limited,
                limit_reason=limits.limit_reason,
            )
        ),
        login_pending=value.login_pending,
        detail=value.detail,
        reason=value.reason,
        checked_at=value.checked_at,
    )


@router.get("/last-seen")
def last_seen(connection: sqlite3.Connection = Depends(get_connection)) -> dict:
    """The Mac's connection as it last saw it, for a copy with none of its own (the
    phone's). A summary only: which connection, signed in, plan, allowance used."""
    from ..storage import model_seen

    return {"seen": model_seen.read(connection)}


@router.get("/status", response_model=schemas.ModelStatusOut)
async def model_status(
    request: Request,
    bridge: ModelBridge = Depends(get_model_bridge),
) -> schemas.ModelStatusOut:
    """The sanitised snapshot.

    This is the call that starts the managed process, the first time it is
    made. It never raises: a bridge that cannot start is an ``unavailable``
    state with a plain-language reason, which is what the interface can act on.
    """
    value = await bridge.status()
    await asyncio.to_thread(_remember, request, value)
    return _status_out(value)


def _remember(request: Request, value: ModelStatus) -> None:
    """Kept for the phone's copy, which shows it as last seen (feedback of 5 October)."""
    from ..db import connect
    from ..storage import model_seen

    try:
        workspace = getattr(request.state, "workspace", None)
        path = workspace.database_path if workspace is not None else request.app.state.database_path
        connection = connect(path)
    except Exception:  # noqa: BLE001 - remembering is a courtesy, never a failure
        return
    try:
        model_seen.record(connection, request.app.state.settings.model_provider, value)
    except Exception:  # noqa: BLE001
        pass
    finally:
        connection.close()


@router.post("/login", response_model=schemas.DeviceLoginOut, status_code=status.HTTP_201_CREATED)
async def start_login(
    response: Response,
    bridge: ModelBridge = Depends(get_model_bridge),
) -> schemas.DeviceLoginOut:
    """Begin ChatGPT device-code sign-in.

    The reply carries the verification URL and the one-time code so the owner
    can complete sign-in in a browser they opened themselves. Vademecum does
    not open one for them: an application that launches an external browser at
    a URL it received over a pipe is doing something the owner did not ask for.

    ``no-store`` is set by the application middleware for everything under
    ``/api``; it is repeated here because this particular body is the one that
    must never be replayed from any cache.
    """
    login: DeviceLogin = await bridge.start_device_login()
    response.headers["Cache-Control"] = "no-store"
    return schemas.DeviceLoginOut(
        verification_url=login.verification_url,
        user_code=login.user_code,
        login_id=login.login_id,
    )


@router.post("/login/cancel", response_model=schemas.LoginCancelOut)
async def cancel_login(
    bridge: ModelBridge = Depends(get_model_bridge),
) -> schemas.LoginCancelOut:
    """Cancel the pending sign-in.

    It takes no body. The pending login id lives in this process, so the
    browser never has to hold one in order to cancel -- which is what lets the
    interface keep the whole device-code exchange out of storage.
    """
    return schemas.LoginCancelOut(status=await bridge.cancel_login())


@router.post("/restart", response_model=schemas.ModelStatusOut)
async def restart(
    bridge: ModelBridge = Depends(get_model_bridge),
) -> schemas.ModelStatusOut:
    """Replace the managed process and report what the new one says.

    The explicit path, for when the automatic single retry was not enough. It
    drops cached rate-limit and plan state rather than carrying it across, and
    it cancels any pending sign-in with it.
    """
    return _status_out(await bridge.restart())
