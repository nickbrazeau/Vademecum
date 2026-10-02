"""Account state, sign-in and rate limits, sanitised.

This is the only part of the bridge the HTTP layer talks to, and the point at
which App Server payloads stop being App Server payloads. Nothing above this
module ever sees a raw protocol object.

What is deliberately dropped on the way through:

* **The account email.** ``GetAccountResponse`` carries it. Nothing here reads
  it, so no route can return it and no log line can contain it.
* **Any account or workspace identifier.** Same reason.
* **The server's error text.** Categories in, plain-language copy out.
* **The device code and verification URL**, everywhere except the single live
  response to the request that created them. They are held in this process only
  as long as the reply takes to build.

What is deliberately refused:

* **Anything but ChatGPT sign-in.** ``LoginAccountParams`` in the schema offers
  an ``apiKey`` variant. This module can only construct the device-code one,
  and if the server ever answers a login with a different variant, that is an
  error rather than a fallback (AGENTS.md, boundary 4).

What this module does *not* claim: that nothing leaves the Mac. Reading account
state, reading rate limits and performing device-code sign-in are handed to the
local ``codex app-server`` child, and Codex contacts OpenAI to carry them out.
What is true in this slice is narrower and is what the copy below says: no
learning content -- no note, question, answer or retrieved context -- is
transmitted, because there is no action here that would send one.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from . import protocol
from .client import AppServerClient
from .diagnostics import event, new_correlation_id
from .errors import (
    RETRYABLE_CATEGORIES,
    BridgeError,
    BridgeProtocolError,
    LoginNotSupported,
)
from .transport import SubprocessTransport, Transport

ModelState = Literal["connecting", "signed_out", "signed_in", "rate_limited", "unavailable"]

# Plausible bounds for a reset timestamp, used to decide whether `resetsAt` can
# be shown as a time at all. The field is an int64 in the schema with no stated
# unit; treating it as Unix seconds is an interpretation, so it is one that
# checks itself rather than one that renders 1970 or 33658 with confidence.
EPOCH_FLOOR = 1_577_836_800  # 2020-01-01T00:00:00Z
EPOCH_CEILING = 4_102_444_800  # 2100-01-01T00:00:00Z

# How many uncorrelated login completions are remembered. One is enough for the
# race this exists for -- a completion arriving before the reply that names it
# -- and a handful covers a user who taps sign-in more than once.
RECENT_COMPLETIONS = 8

# Plain language for every category the bridge can produce. The interface shows
# these; the category itself stays in the response for tests and for the log.
DETAIL: dict[str, str] = {
    "codex_not_found": (
        "Codex is not installed where Vademecum expects it. Install the ChatGPT app or the "
        "Codex CLI, then try again."
    ),
    "spawn_failed": (
        "Codex would not start on this Mac. No note, question or answer was sent anywhere."
    ),
    "startup_failed": (
        "The Codex connection could not be set up. No note, question or answer was sent "
        "anywhere. Try again to reconnect."
    ),
    "not_started": "The Codex connection is not running.",
    "process_exited": "The Codex connection stopped. Try again to reconnect.",
    "write_failed": "The Codex connection stopped mid-request. Try again to reconnect.",
    "timeout": (
        "Codex did not answer in time. No note, question or answer was sent to a model."
    ),
    "protocol": "Codex answered with something this version of Vademecum could not read.",
    "method_not_found": (
        "This version of Codex does not offer what Vademecum asked for. Updating Codex may fix it."
    ),
    "server_error": "Codex reported a problem on its side.",
    "refused": "Codex refused the request.",
    "login_not_supported": (
        "Only ChatGPT sign-in is supported. Vademecum does not use an API key and will not "
        "fall back to one."
    ),
    "credential_unsupported": (
        "Codex is signed in with a credential Vademecum does not use. Sign in with ChatGPT to "
        "use model features; no API key is used."
    ),
    "closed": "Vademecum is shutting down.",
    "unavailable": "The Codex connection is not available.",
}

SIGNED_OUT_DETAIL = (
    "Not signed in to ChatGPT. Sign in to build learning material and grade Tutor answers "
    "using your Codex plan. "
    "No API key is involved."
)
SIGNED_IN_DETAIL = (
    "Signed in to ChatGPT through Codex. Reading this state goes through Codex, which contacts "
    "OpenAI to answer it. This status check sends no study content. Build and Grade send "
    "only the content described in their previews when you explicitly approve those actions."
)
RATE_LIMITED_DETAIL = (
    "Your Codex usage limit has been reached. Everything stored on this Mac is unaffected."
)


def _utc_now() -> str:
    """UTC, ISO 8601, seconds. Local to this layer; storage is not imported."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_from_epoch(value: Any) -> str | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if not EPOCH_FLOOR <= value <= EPOCH_CEILING:
        return None
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class UsageWindow:
    """One rate-limit window, as much of it as is safe and present."""

    used_percent: int
    resets_at: str | None
    window_minutes: int | None


@dataclass(frozen=True)
class RateLimits:
    primary: UsageWindow | None
    secondary: UsageWindow | None
    limited: bool
    limit_reason: str | None


@dataclass(frozen=True)
class ModelStatus:
    state: ModelState
    signed_in: bool
    plan: str | None
    rate_limits: RateLimits | None
    login_pending: bool
    detail: str
    reason: str | None
    checked_at: str


@dataclass(frozen=True)
class DeviceLogin:
    """The one payload that may leave this process and be shown once.

    It is returned to a live, ``no-store`` HTTP response and to nothing else:
    not to a log, not to the database, not to browser storage. The bridge keeps
    only ``login_id``, and only so the sign-in can be cancelled.
    """

    verification_url: str
    user_code: str
    login_id: str


def _window(raw: Any) -> UsageWindow | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("usedPercent")
    if not isinstance(used, int) or isinstance(used, bool):
        return None
    minutes = raw.get("windowDurationMins")
    return UsageWindow(
        used_percent=max(0, min(100, used)),
        resets_at=_iso_from_epoch(raw.get("resetsAt")),
        window_minutes=minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else None,
    )


def sanitise_rate_limits(snapshot: Any) -> RateLimits | None:
    """A ``RateLimitSnapshot`` reduced to what the interface may show.

    Dropped along the way: ``limitId``, ``limitName``, ``credits`` (a balance is
    billing detail), ``individualLimit`` (spend amounts) and ``planType``
    (already carried separately). Kept: how full each window is, when it
    resets, and whether a limit has actually been reached.
    """
    if not isinstance(snapshot, dict):
        return None
    primary = _window(snapshot.get("primary"))
    secondary = _window(snapshot.get("secondary"))
    reached = snapshot.get("rateLimitReachedType")
    limit_reason = reached if isinstance(reached, str) and reached else None
    limited = bool(limit_reason) or snapshot.get("spendControlReached") is True
    if not limited:
        limited = any(
            window is not None and window.used_percent >= 100 for window in (primary, secondary)
        )
    if primary is None and secondary is None and not limited:
        return None
    return RateLimits(
        primary=primary, secondary=secondary, limited=limited, limit_reason=limit_reason
    )


def merge_snapshot(previous: dict[str, Any] | None, update: Any) -> dict[str, Any] | None:
    """Apply a sparse ``account/rateLimits/updated`` payload.

    The schema is explicit that a rolling update is sparse and that a null does
    not clear a previously observed value, so a null is skipped rather than
    written.
    """
    if not isinstance(update, dict):
        return previous
    merged = dict(previous or {})
    for key, value in update.items():
        if value is None:
            continue
        merged[key] = value
    return merged


class ModelBridge:
    """The account-facing facade over one managed App Server process."""

    def __init__(
        self,
        *,
        codex_path: Path,
        working_directory: Path,
        client_version: str,
        request_timeout: float = 30.0,
        startup_timeout: float = 20.0,
        transport_factory: Callable[[], Transport] | None = None,
    ) -> None:
        factory = transport_factory or (
            lambda: SubprocessTransport(codex_path, working_directory)
        )
        self._client = AppServerClient(
            factory,
            # AGENTS.md requires the client identify itself as Vademecum.
            client_name="Vademecum",
            client_title="Vademecum",
            client_version=client_version,
            request_timeout=request_timeout,
            startup_timeout=startup_timeout,
            on_notification=self._on_notification,
        )
        self._rate_limits: dict[str, Any] | None = None
        self._plan_hint: str | None = None
        self._auth_mode_hint: str | None = None
        self._pending_login_id: str | None = None
        self._login_failed = False
        # Completions that arrived with no pending sign-in to match: either
        # stale, or ahead of the reply that would have named them. Bounded,
        # because an unbounded one would be a place for ids to accumulate.
        self._completed_logins: deque[tuple[str, bool]] = deque(maxlen=RECENT_COMPLETIONS)
        self._lock = asyncio.Lock()

    @property
    def client(self) -> AppServerClient:
        return self._client

    @property
    def login_pending(self) -> bool:
        return self._pending_login_id is not None

    # --- notifications --------------------------------------------------

    def _on_notification(self, method: str, params: Any) -> None:
        """Runs on the read loop. Cheap, synchronous, and silent.

        Nothing here logs a payload; ``AppServerClient`` has already logged the
        method name, which is all that is safe about a notification.
        """
        if method == protocol.ACCOUNT_RATE_LIMITS_UPDATED:
            if isinstance(params, dict):
                self._rate_limits = merge_snapshot(self._rate_limits, params.get("rateLimits"))
        elif method == protocol.ACCOUNT_UPDATED:
            if isinstance(params, dict):
                plan = params.get("planType")
                if isinstance(plan, str):
                    self._plan_hint = plan
                mode = params.get("authMode")
                if isinstance(mode, str):
                    self._auth_mode_hint = mode
        elif method == protocol.ACCOUNT_LOGIN_COMPLETED:
            self._on_login_completed(params)

    def _on_login_completed(self, params: Any) -> None:
        """Apply a completion to the sign-in it names, and to no other.

        Two orderings have to survive this, and neither may clear the wrong
        sign-in:

        * **A stale completion.** A sign-in was cancelled and another started.
          The old one completing afterwards names the old ``loginId``, so it is
          not allowed to clear the one the owner is currently looking at.
        * **A completion that overtakes its own start reply.** The read loop
          sees it while ``start_device_login`` is still awaiting the response
          that would have named the id. There is nothing to match yet, so it is
          remembered by id and applied when the reply arrives -- otherwise
          ``login_pending`` stays true forever for a sign-in that has finished.

        ``error`` is a string on the wire and is not read: a failed login
        reduces to a boolean here, so there is nothing to leak later. Nothing
        in this method logs an id either; only which of the three cases it was.
        """
        if not isinstance(params, dict):
            return
        login_id = params.get("loginId")
        succeeded = params.get("success") is True
        if not isinstance(login_id, str) or not login_id:
            # The schema makes `loginId` optional, so a completion can arrive
            # with nothing to correlate it to. Guessing which sign-in it meant
            # is the bug this method exists to prevent, so the pending one
            # stays pending; the owner can still cancel it.
            event("model_login_completed", status="uncorrelated")
            return
        if login_id == self._pending_login_id:
            self._pending_login_id = None
            self._login_failed = not succeeded
            event("model_login_completed", status="ok" if succeeded else "failed")
            return
        self._completed_logins.append((login_id, succeeded))
        event("model_login_completed", status="unmatched")

    def _take_completion(self, login_id: str) -> bool | None:
        """Whether this login already completed, removing the record if so."""
        for index, (candidate, succeeded) in enumerate(self._completed_logins):
            if candidate == login_id:
                del self._completed_logins[index]
                return succeeded
        return None

    # --- reads ----------------------------------------------------------

    async def _read_with_retry(self, method: str, params: Any = None) -> Any:
        """The single retry path.

        One retry, for read-only methods, and only for the categories that mean
        "the pipe went away" rather than "the server said no". The retry does
        not restart anything explicitly: a dead process is reaped and replaced
        by ``ensure_started`` on the way back in, which avoids two callers
        racing to restart and one of them killing the other's fresh process.

        A timeout is not retried. The request may have been received and acted
        on, and this path must stay safe to repeat.
        """
        try:
            return await self._client.call(method, params)
        except BridgeError as exc:
            if exc.category not in RETRYABLE_CATEGORIES:
                raise
            event("appserver_retry", method=method, category=exc.category, attempt=1)
            return await self._client.call(method, params)

    async def status(self) -> ModelStatus:
        """The sanitised snapshot. Never raises; failure is a state."""
        correlation_id = new_correlation_id()
        try:
            account = await self._read_with_retry(
                protocol.ACCOUNT_READ, protocol.account_read_params()
            )
        except BridgeError as exc:
            return self._unavailable(exc.category, correlation_id)

        if not isinstance(account, dict):
            return self._unavailable("protocol", correlation_id)

        details = account.get("account")
        account_type = details.get("type") if isinstance(details, dict) else None

        if account_type is None:
            return self._signed_out(None, correlation_id)
        if account_type != protocol.ACCOUNT_TYPE_CHATGPT:
            # Signed in, but not in a way Vademecum will use. Reported as
            # signed out with a reason rather than treated as usable: this is
            # the boundary that keeps a stored key from silently becoming the
            # billing path.
            return self._signed_out("credential_unsupported", correlation_id)

        plan = details.get("planType") if isinstance(details, dict) else None
        if isinstance(plan, str):
            self._plan_hint = plan

        try:
            limits_reply = await self._read_with_retry(protocol.ACCOUNT_RATE_LIMITS_READ)
        except BridgeError as exc:
            # Signed in is still true and still worth showing; the usage panel
            # is simply absent.
            event("model_rate_limits_unavailable", cid=correlation_id, category=exc.category)
            limits_reply = None

        if isinstance(limits_reply, dict):
            self._rate_limits = merge_snapshot(self._rate_limits, limits_reply.get("rateLimits"))

        limits = sanitise_rate_limits(self._rate_limits)
        state: ModelState = "rate_limited" if limits is not None and limits.limited else "signed_in"
        status = ModelStatus(
            state=state,
            signed_in=True,
            plan=self._plan_hint,
            rate_limits=limits,
            login_pending=self.login_pending,
            detail=RATE_LIMITED_DETAIL if state == "rate_limited" else SIGNED_IN_DETAIL,
            reason=None,
            checked_at=_utc_now(),
        )
        event("model_status", cid=correlation_id, state=status.state, status="ok")
        return status

    def _signed_out(self, reason: str | None, correlation_id: str) -> ModelStatus:
        status = ModelStatus(
            state="signed_out",
            signed_in=False,
            plan=None,
            rate_limits=None,
            login_pending=self.login_pending,
            detail=DETAIL[reason] if reason else SIGNED_OUT_DETAIL,
            reason=reason,
            checked_at=_utc_now(),
        )
        event("model_status", cid=correlation_id, state=status.state, category=reason or "none")
        return status

    def _unavailable(self, category: str, correlation_id: str) -> ModelStatus:
        status = ModelStatus(
            state="unavailable",
            signed_in=False,
            plan=None,
            rate_limits=None,
            login_pending=self.login_pending,
            detail=DETAIL.get(category, DETAIL["unavailable"]),
            reason=category,
            checked_at=_utc_now(),
        )
        event("model_status", cid=correlation_id, state=status.state, category=category)
        return status

    # --- sign-in --------------------------------------------------------

    async def start_device_login(self) -> DeviceLogin:
        """Begin ChatGPT device-code sign-in.

        Serialised, so two taps on the sign-in button cannot leave two device
        codes outstanding with only one of them cancellable.
        """
        async with self._lock:
            reply = await self._client.call(
                protocol.ACCOUNT_LOGIN_START, protocol.device_login_params()
            )
            if not isinstance(reply, dict):
                raise BridgeProtocolError("protocol")
            if reply.get("type") != protocol.LOGIN_TYPE_DEVICE_CODE:
                # The server answered a device-code request with a different
                # login variant. That is a refusal, not something to adapt to.
                raise LoginNotSupported()
            url = reply.get("verificationUrl")
            code = reply.get("userCode")
            login_id = reply.get("loginId")
            if not (isinstance(url, str) and isinstance(code, str) and isinstance(login_id, str)):
                raise BridgeProtocolError("protocol")

            completed = self._take_completion(login_id)
            if completed is None:
                self._pending_login_id = login_id
                self._login_failed = False
                # Note what this event does not carry: the url, the code, or
                # the login id. Only that a sign-in started.
                event("model_login_started", status="pending")
            else:
                # The completion notification overtook this reply. Recording
                # the login as pending now would leave the interface offering
                # to cancel a sign-in that has already finished.
                self._pending_login_id = None
                self._login_failed = not completed
                event("model_login_started", status="already_completed")
            return DeviceLogin(verification_url=url, user_code=code, login_id=login_id)

    async def cancel_login(self) -> str:
        """Cancel the pending sign-in. Returns a status, never raises for 'none'."""
        async with self._lock:
            login_id = self._pending_login_id
            if login_id is None:
                event("model_login_cancel", status="nothing_pending")
                return "nothing_pending"
            try:
                reply = await self._client.call(
                    protocol.ACCOUNT_LOGIN_CANCEL, protocol.cancel_login_params(login_id)
                )
            finally:
                # Cleared either way. A cancel that failed to reach the server
                # still means this process must stop treating the code as live.
                self._pending_login_id = None
                # And a completion for it, if one is still remembered, is now
                # about a sign-in nobody is waiting on.
                self._take_completion(login_id)
            status = reply.get("status") if isinstance(reply, dict) else None
            resolved = status if status in {"canceled", "notFound"} else "canceled"
            event("model_login_cancel", status=resolved)
            return resolved

    # --- lifecycle ------------------------------------------------------

    async def restart(self) -> ModelStatus:
        """Explicitly replace the managed process, then report state.

        Cached state is dropped rather than carried across: a rate-limit
        snapshot from a process that is gone is a claim this cannot support.
        """
        self._rate_limits = None
        self._plan_hint = None
        self._auth_mode_hint = None
        self._pending_login_id = None
        self._login_failed = False
        self._completed_logins.clear()
        correlation_id = new_correlation_id()
        try:
            await self._client.restart()
        except BridgeError as exc:
            return self._unavailable(exc.category, correlation_id)
        event("model_restarted", cid=correlation_id, generation=self._client.generation)
        return await self.status()

    async def aclose(self) -> None:
        await self._client.aclose()
