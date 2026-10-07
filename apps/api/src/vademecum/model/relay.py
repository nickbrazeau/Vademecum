"""The Socratic relay (feedback of 6 October): the phone's own tutor, answered by the Mac.

The phone's copy (Foris) has no model connection; the Mac (Domi) does. While the
owner has the Socratic tutor open on the phone, Foris queues each turn here and
the Mac collects it, computes the next question on its own connection with the
page it holds, and posts the reply back. The queue lives in memory: a turn lost
to a restart is asked again by the phone.

The Mac is not watching all the time. It checks a small flag every few seconds
at the Worker in front of Foris -- which does not wake the container -- and only
when the flag is fresh (the owner opened the tutor on the phone) does it start
collecting turns from Foris, until the tutor has been quiet for a while.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

logger = logging.getLogger("vademecum.relay")

LIVE_SECONDS = 45.0  # the Mac collected within this long: it is answering
REOFFER_SECONDS = 120.0  # a turn the Mac took but never answered is offered again
WAKE_EVERY_SECONDS = 30.0  # the flag is rewritten at most this often
WANTED_FRESH_SECONDS = 15 * 60  # the Mac keeps collecting this long after the last sign of the owner
IDLE_SECONDS_FREE = 10 * 60  # with nothing to answer this long, the Mac goes back to checking the flag


class Relay:
    """Foris's side: the queue of turns waiting for the Mac."""

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}
        self._claimed: dict[str, float] = {}
        self._event: asyncio.Event | None = None
        self.heartbeat = 0.0
        self._woke = 0.0
        self.errors: dict[str, str] = {}

    def _signal(self) -> asyncio.Event:
        if self._event is None:
            self._event = asyncio.Event()
        return self._event

    def live(self) -> bool:
        return time.monotonic() - self.heartbeat < LIVE_SECONDS

    def enqueue(self, *, session_id: str, entry_id: str | None, transcript: list[dict[str, str]], exchanges: int) -> str:
        # One turn per session at a time: a newer one replaces any still waiting.
        for request_id, request in list(self._pending.items()):
            if request["session_id"] == session_id:
                self._pending.pop(request_id)
                self._claimed.pop(request_id, None)
        self.errors.pop(session_id, None)
        request_id = uuid.uuid4().hex
        self._pending[request_id] = {
            "id": request_id,
            "session_id": session_id,
            "entry_id": entry_id,
            "transcript": transcript,
            "exchanges": exchanges,
        }
        self._signal().set()
        return request_id

    def waiting(self, session_id: str) -> bool:
        return any(request["session_id"] == session_id for request in self._pending.values())

    def _offer(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        offered = []
        for request_id, request in self._pending.items():
            claimed = self._claimed.get(request_id)
            if claimed is None or now - claimed > REOFFER_SECONDS:
                self._claimed[request_id] = now
                offered.append(request)
        return offered

    async def wait(self, timeout: float) -> list[dict[str, Any]]:
        """The Mac's long poll: turns to answer, or none after `timeout` seconds."""
        self.heartbeat = time.monotonic()
        offered = self._offer()
        if offered:
            return offered
        event = self._signal()
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self.heartbeat = time.monotonic()
        return self._offer()

    def complete(self, request_id: str) -> dict[str, Any] | None:
        self._claimed.pop(request_id, None)
        return self._pending.pop(request_id, None)

    def wake_mac(self) -> None:
        """Tell the Mac, through the Worker's store, that the owner has the tutor open.
        At most every half minute; a failure only means the Mac notices later."""
        now = time.monotonic()
        if now - self._woke < WAKE_EVERY_SECONDS:
            return
        self._woke = now
        put_flag()


def put_flag() -> bool:
    """Write the flag through the seat store (the Worker's bucket): Foris only."""
    import http.client
    from urllib.parse import urlsplit

    base = os.environ.get("SEAT_STORE_URL", "")
    key = os.environ.get("SEAT_KEY", "")
    if not base or not key:
        return False
    parts = urlsplit(base)
    if parts.scheme != "https" or not parts.hostname:
        return False
    body = str(int(time.time())).encode()
    connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=10)
    try:
        connection.request(
            "PUT",
            f"{parts.path.rstrip('/')}/object/relay/wanted",
            body=body,
            headers={"x-seat-key": key, "content-type": "application/octet-stream", "content-length": str(len(body))},
        )
        return connection.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()
