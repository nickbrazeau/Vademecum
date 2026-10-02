"""One managed App Server process, and the protocol spoken over it.

Responsibilities, in the order they matter:

1. **One process, started lazily, reused.** Nothing spawns at import or at
   startup. The first call that needs the bridge starts it, under a lock, so
   two concurrent first calls produce one process and one ``initialize``.
2. **Never leave anything hanging.** Every outbound request is either resolved,
   timed out, or failed when the process dies. Every inbound server request is
   answered exactly once, including the ones this client refuses.
3. **Fail closed.** Every failure path ends in a category and an unavailable
   state. There is no path here that changes provider or billing mode, because
   there is no other provider in the code to change to.
4. **Say nothing.** Diagnostics go through ``diagnostics.event``, which accepts
   a fixed set of scalar fields. No payload, no server message text, no stderr.

The transport is injected, so the entire layer runs against a scripted fake in
tests with no child process in sight.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import protocol
from .diagnostics import event, new_correlation_id, safe_method
from .errors import BridgeError, BridgeProtocolError, BridgeRefused, BridgeTimeout, BridgeUnavailable
from .transport import Transport


@dataclass(frozen=True)
class Decline:
    """Answer an approval with a decision that both denies and stops."""

    decision: str


@dataclass(frozen=True)
class Refuse:
    """Answer with a JSON-RPC error. The message is fixed, local text."""

    message: str


ServerRequestPolicy = Decline | Refuse

# ``(generation, method, params)``. The generation is what lets a subscriber
# discard an event from a process that has since been replaced.
Subscriber = Callable[[int, str, Any], None]

# Refusal copy. Fixed strings, chosen here; nothing derived from the request.
_NO_TOOLS = "Vademecum runs no client tools and grants no permissions over this connection."
_NO_TOKENS = "Vademecum uses Codex-managed ChatGPT authentication and supplies no tokens."
_NO_ATTESTATION = "Vademecum did not negotiate attestation and cannot produce a token."

# Every server-initiated request this client can receive, and what it answers.
#
# The keys must equal schemas/codex-app-server/methods/server-requests.json --
# a test asserts equality in both directions, so a Codex upgrade that adds a
# server-initiated request fails the suite rather than falling into the
# unknown-method default below. That default is safe either way; the test is
# there so a new capability gets read by a person.
#
# The approvals are answered "cancel"/"abort" rather than "decline": both deny,
# but these also stop the turn.
#
# Turns now exist, which changes what one of these *means* without changing what
# it gets answered. It is no longer "something is running that should not be":
# a grading turn is running, and it has just asked to do something that
# `hardening.py` disabled at two layers. That is a hardening failure -- a Codex
# release ignoring our config, or a config key that moved -- and the turn must
# not be allowed to finish and return a result as though nothing happened. So
# the refusal below is still the answer on the wire, and `UNEXPECTED_TOOL_EVENTS`
# is additionally reported to `on_unexpected_tool_event` and to every subscriber,
# which is what lets `turns.py` fail the turn closed.
SERVER_REQUEST_POLICY: dict[str, ServerRequestPolicy] = {
    "item/commandExecution/requestApproval": Decline("cancel"),
    "item/fileChange/requestApproval": Decline("cancel"),
    "execCommandApproval": Decline("abort"),
    "applyPatchApproval": Decline("abort"),
    "item/permissions/requestApproval": Refuse(_NO_TOOLS),
    "item/tool/requestUserInput": Refuse(_NO_TOOLS),
    "item/tool/call": Refuse(_NO_TOOLS),
    "mcpServer/elicitation/request": Refuse(_NO_TOOLS),
    "account/chatgptAuthTokens/refresh": Refuse(_NO_TOKENS),
    "attestation/generate": Refuse(_NO_ATTESTATION),
}

UNKNOWN_REQUEST = Refuse("Vademecum does not implement this method.")

# The subset whose arrival is a hardening failure rather than merely something
# to refuse. Everything a running turn could use to reach out, run something, or
# ask a person for permission it should never have needed.
UNEXPECTED_TOOL_EVENTS = frozenset(
    {
        "item/tool/call",
        "item/permissions/requestApproval",
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "execCommandApproval",
        "applyPatchApproval",
        "mcpServer/elicitation/request",
    }
)


@dataclass
class _Session:
    """One process's worth of state. Replaced wholesale on restart."""

    generation: int
    transport: Transport
    pending: dict[protocol.IdKey, asyncio.Future[Any]] = field(default_factory=dict)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    reader: asyncio.Task[None] | None = None
    stderr: asyncio.Task[None] | None = None
    initialized: bool = False
    alive: bool = True
    closing: bool = False
    exit_status: str | None = None
    stderr_line_count: int = 0
    stderr_byte_count: int = 0
    malformed_count: int = 0
    orphan_reply_count: int = 0


# How often a running stderr count is reported. The child is normally silent;
# a burst is worth knowing about, its content is not.
STDERR_REPORT_EVERY = 100


class AppServerClient:
    def __init__(
        self,
        transport_factory: Callable[[], Transport],
        *,
        client_name: str,
        client_title: str,
        client_version: str,
        request_timeout: float = 30.0,
        startup_timeout: float = 20.0,
        on_notification: Callable[[str, Any], None] | None = None,
        on_unexpected_tool_event: Callable[[str, Any], None] | None = None,
    ) -> None:
        self._factory = transport_factory
        self._client_name = client_name
        self._client_title = client_title
        self._client_version = client_version
        self._request_timeout = request_timeout
        self._startup_timeout = startup_timeout
        self._on_notification = on_notification
        self._on_unexpected_tool_event = on_unexpected_tool_event
        # Subscribers, in subscription order. A list rather than a set: order is
        # stable, and a handler is not required to be hashable.
        self._subscribers: list[Subscriber] = []

        self._lock = asyncio.Lock()
        self._session: _Session | None = None
        self._generation = 0
        self._next_id = 0
        self._closed = False
        self.initialize_count = 0

    # --- event fan-out --------------------------------------------------

    def subscribe(self, handler: Subscriber) -> Callable[[], None]:
        """Watch every observed notification and every unexpected tool event.

        Returns the unsubscribe callable, which is idempotent. The handler is
        called as ``(generation, method, params)``. Notifications carry
        ``threadId`` and ``turnId``; demultiplexing on those *and on the
        generation* is the subscriber's job, so this class stays generic and
        knows nothing about threads.

        The generation is not decoration. A restart replaces the child, and a
        notification that was in flight across one belongs to a process whose
        turns are already gone -- a subscriber that could not tell would let a
        dead turn look alive.
        """
        self._subscribers.append(handler)
        removed = False

        def unsubscribe() -> None:
            nonlocal removed
            if removed:
                return
            removed = True
            try:
                self._subscribers.remove(handler)
            except ValueError:
                pass

        return unsubscribe

    def _dispatch(self, generation: int, method: str, params: Any, name: str) -> None:
        """Hand one event to every handler.

        Each is isolated: one that raises is logged by name and category only --
        it was holding the payload -- and neither takes down the read loop nor
        stops the handlers after it.
        """

        def failed() -> None:
            event(
                "appserver_notification_failed",
                generation=generation,
                method=name,
                category="handler_error",
            )

        if self._on_notification is not None:
            # The account facade's handler. Generation-free by design: it reads
            # account state, which is a property of the login rather than of
            # this particular child.
            try:
                self._on_notification(method, params)
            except Exception:
                failed()
        for handler in list(self._subscribers):
            try:
                handler(generation, method, params)
            except Exception:
                failed()

    # --- lifecycle ------------------------------------------------------

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def running(self) -> bool:
        session = self._session
        return session is not None and session.alive

    async def ensure_started(self) -> _Session:
        """Start the process if it is not running, and return the session.

        The lock is what makes "exactly once" true. Two requests arriving
        together both wait here; the second finds a live, already-initialised
        session and sends nothing.
        """
        if self._closed:
            raise BridgeUnavailable("closed")
        session = self._session
        if session is not None and session.alive:
            return session
        async with self._lock:
            session = self._session
            if session is not None and session.alive:
                return session
            if session is not None:
                # Cleared first: a failed restart must not leave a dead session
                # published, or the next caller reaps a corpse twice.
                self._session = None
                await self._teardown(session, reason="replacing")
            return await self._start()

    async def _start(self) -> _Session:
        self._generation += 1
        correlation_id = new_correlation_id()
        started = time.perf_counter()
        transport = self._factory()
        session = _Session(generation=self._generation, transport=transport)

        try:
            await transport.start()
        except BridgeError as exc:
            event(
                "appserver_start_failed",
                cid=correlation_id,
                generation=session.generation,
                category=exc.category,
            )
            raise
        except asyncio.CancelledError:
            # The caller went away while the child was being spawned. Whatever
            # got as far as existing is stopped before this unwinds.
            event(
                "appserver_start_failed",
                cid=correlation_id,
                generation=session.generation,
                category="cancelled",
            )
            await self._stop_quietly(transport)
            raise
        except Exception as exc:  # a transport that fails in its own way
            event(
                "appserver_start_failed",
                cid=correlation_id,
                generation=session.generation,
                category="spawn_failed",
            )
            await self._stop_quietly(transport)
            raise BridgeUnavailable("spawn_failed") from exc

        loop = asyncio.get_running_loop()
        session.reader = loop.create_task(self._read_loop(session))
        session.stderr = loop.create_task(self._drain_stderr(session))

        # From here on the child is running and two tasks are reading it, while
        # nothing has published the session yet. Every way out of this block --
        # including the two that are not protocol failures -- has to take all
        # three down, or a cancelled Model-page request leaves an orphaned
        # `codex app-server` that no later caller can find in order to reap.
        try:
            await self._request(
                session,
                protocol.INITIALIZE,
                protocol.initialize_params(
                    self._client_name, self._client_title, self._client_version
                ),
                timeout=self._startup_timeout,
                correlation_id=correlation_id,
            )
            await self._send(session, protocol.notification(protocol.INITIALIZED))
        except BridgeError as exc:
            await self._teardown(session, reason="initialize_failed")
            event(
                "appserver_initialize_failed",
                cid=correlation_id,
                generation=session.generation,
                category=exc.category,
            )
            raise
        except asyncio.CancelledError:
            # A closed tab, a shutdown, a request that was abandoned mid-start.
            # Torn down first, then re-raised: cancellation is the caller's
            # answer, not a reason to leak a process.
            await self._teardown(session, reason="startup_cancelled")
            event(
                "appserver_initialize_failed",
                cid=correlation_id,
                generation=session.generation,
                category="cancelled",
            )
            raise
        except Exception as exc:
            # Anything a transport or this client can raise that is not already
            # a category. Wrapped, so a caller still sees the closed set, and
            # the exception itself is not logged: it can quote what it choked
            # on.
            await self._teardown(session, reason="startup_failed")
            event(
                "appserver_initialize_failed",
                cid=correlation_id,
                generation=session.generation,
                category="startup_failed",
            )
            raise BridgeUnavailable("startup_failed") from exc

        session.initialized = True
        self.initialize_count += 1
        self._session = session
        event(
            "appserver_started",
            cid=correlation_id,
            generation=session.generation,
            duration_ms=(time.perf_counter() - started) * 1000,
            status="ok",
        )
        return session

    async def restart(self) -> None:
        """Stop the current process and start a fresh, re-initialised one."""
        if self._closed:
            raise BridgeUnavailable("closed")
        async with self._lock:
            session = self._session
            self._session = None
            if session is not None:
                await self._teardown(session, reason="restart")
            await self._start()

    async def aclose(self) -> None:
        """Shut down for good. Safe when nothing was ever started."""
        self._closed = True
        async with self._lock:
            session = self._session
            self._session = None
            if session is not None:
                await self._teardown(session, reason="closed")

    @staticmethod
    async def _stop_quietly(transport: Transport) -> None:
        """Stop a transport that never got as far as having a session.

        Failures are swallowed: this only ever runs while another exception is
        on its way out, and that one is the useful one.
        """
        try:
            await transport.stop()
        except Exception:
            pass

    async def _teardown(self, session: _Session, *, reason: str) -> None:
        """Stop one session and leave nothing running behind it.

        Order matters. In-flight server-request replies are cancelled first, so
        none of them tries to write to a pipe that is about to close. The
        reader is cancelled before the transport stops, so its exit is not
        mistaken for the process dying on its own.
        """
        if session.closing:
            return
        session.closing = True
        session.alive = False

        tasks = [task for task in (session.reader, session.stderr) if task is not None]
        tasks.extend(session.tasks)
        for task in tasks:
            task.cancel()

        try:
            status = await session.transport.stop()
        except Exception:
            status = "stop_failed"
        session.exit_status = session.exit_status or status

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        session.tasks.clear()

        self._fail_pending(session, "process_exited")
        event(
            "appserver_stopped",
            generation=session.generation,
            category=reason,
            exit=session.exit_status,
            lines=session.stderr_line_count,
        )

    def _fail_pending(self, session: _Session, category: str) -> None:
        pending, session.pending = session.pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(BridgeUnavailable(category))

    # --- reading --------------------------------------------------------

    async def _read_loop(self, session: _Session) -> None:
        try:
            async for line in session.transport.stdout_lines():
                self._handle_line(session, line)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The exception *type* is not even logged: a decoder error can
            # carry the fragment it choked on in its message, and there is no
            # version of that which belongs in a log.
            event("appserver_read_failed", generation=session.generation, category="read_error")
        finally:
            if not session.closing:
                self._on_disconnect(session)
                # Reap here rather than waiting for the next caller. A child
                # that exits while nobody is looking would otherwise sit as a
                # zombie until the application shuts down, and "nobody is
                # looking" is the normal state of this page.
                try:
                    session.exit_status = await session.transport.stop()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    session.exit_status = session.exit_status or "stop_failed"

    def _on_disconnect(self, session: _Session) -> None:
        """stdout reached EOF: the child is gone or going.

        Called from inside the reader task, so it must not await the reader.
        It marks the session dead and fails everything waiting; the next
        ``ensure_started`` reaps it properly.
        """
        session.alive = False
        waiting = len(session.pending)
        self._fail_pending(session, "process_exited")
        event(
            "appserver_disconnected",
            generation=session.generation,
            category="stdout_closed",
            pending=waiting,
        )

    def _handle_line(self, session: _Session, line: str) -> None:
        if not line.strip():
            return
        try:
            message = protocol.decode_line(line)
        except protocol.MalformedMessage as exc:
            session.malformed_count += 1
            event(
                "appserver_malformed_message",
                generation=session.generation,
                category=exc.reason,
                count=session.malformed_count,
            )
            return

        if isinstance(message, protocol.ServerRequest):
            self._spawn_server_reply(session, message)
        elif isinstance(message, protocol.ServerNotification):
            self._handle_notification(session, message)
        else:
            self._resolve(session, message)

    def _resolve(self, session: _Session, message: protocol.Result | protocol.ErrorReply) -> None:
        future = session.pending.pop(protocol.id_key(message.id), None)
        if future is None or future.done():
            # A reply to a request that already timed out, was cancelled, or
            # was never sent. Counted; the payload is dropped unread.
            session.orphan_reply_count += 1
            event(
                "appserver_unmatched_reply",
                generation=session.generation,
                count=session.orphan_reply_count,
            )
            return
        if isinstance(message, protocol.ErrorReply):
            future.set_exception(
                BridgeRefused(message.code, protocol.error_category(message.code))
            )
        else:
            future.set_result(message.result)

    def _handle_notification(self, session: _Session, message: protocol.ServerNotification) -> None:
        name = safe_method(message.method)
        if message.method not in protocol.OBSERVED_NOTIFICATIONS:
            event("appserver_notification_ignored", generation=session.generation, method=name)
            return
        event("appserver_notification", generation=session.generation, method=name)
        self._dispatch(session.generation, message.method, message.params, name)

    # --- server-initiated requests --------------------------------------

    def _spawn_server_reply(self, session: _Session, message: protocol.ServerRequest) -> None:
        if message.method in UNEXPECTED_TOOL_EVENTS:
            # Reported from inside the read loop, before the refusal is even
            # scheduled, so a turn runner learns of it in arrival order and can
            # fail closed while the turn is still interruptible.
            name = safe_method(message.method)
            event(
                "appserver_unexpected_tool_event",
                generation=session.generation,
                method=name,
                category="hardening_bypassed",
            )
            if self._on_unexpected_tool_event is not None:
                try:
                    self._on_unexpected_tool_event(message.method, message.params)
                except Exception:
                    event(
                        "appserver_notification_failed",
                        generation=session.generation,
                        method=name,
                        category="handler_error",
                    )
            self._dispatch(session.generation, message.method, message.params, name)
        task = asyncio.get_running_loop().create_task(self._answer_server_request(session, message))
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)

    async def _answer_server_request(
        self, session: _Session, message: protocol.ServerRequest
    ) -> None:
        """Answer exactly once, whatever was asked.

        There is no branch here that leaves a request unanswered, and none that
        performs work on the server's behalf. This slice has no model turns and
        no client tools, so every one of these is either an approval to stop or
        a capability to refuse.
        """
        policy = SERVER_REQUEST_POLICY.get(message.method, UNKNOWN_REQUEST)
        name = safe_method(message.method)
        if isinstance(policy, Decline):
            reply = protocol.result(message.id, {"decision": policy.decision})
            status = "declined"
        else:
            reply = protocol.error(message.id, protocol.METHOD_NOT_FOUND, policy.message)
            status = "refused"

        try:
            await self._send(session, reply)
        except BridgeError as exc:
            event(
                "appserver_server_request_reply_failed",
                generation=session.generation,
                method=name,
                category=exc.category,
            )
            return
        except asyncio.CancelledError:
            raise
        event(
            "appserver_server_request",
            generation=session.generation,
            method=name,
            status=status,
        )

    # --- writing --------------------------------------------------------

    async def _send(self, session: _Session, message: dict[str, Any]) -> None:
        if not session.alive:
            raise BridgeUnavailable("process_exited")
        await session.transport.send_line(protocol.encode(message))

    def _allocate_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _request(
        self,
        session: _Session,
        method: str,
        params: Any = None,
        *,
        timeout: float | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        request_id = self._allocate_id()
        key = protocol.id_key(request_id)
        cid = correlation_id or new_correlation_id()
        deadline = self._request_timeout if timeout is None else timeout

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        session.pending[key] = future
        started = time.perf_counter()
        try:
            await self._send(session, protocol.request(request_id, method, params))
            value = await asyncio.wait_for(future, timeout=deadline)
        except asyncio.TimeoutError as exc:
            event(
                "appserver_request",
                cid=cid,
                generation=session.generation,
                method=method,
                status="timeout",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            raise BridgeTimeout() from exc
        except asyncio.CancelledError:
            # The caller went away -- a closed browser tab, a shutdown. The
            # entry is removed in `finally`, so a late reply is counted as
            # unmatched rather than resolving a future nobody holds.
            event(
                "appserver_request",
                cid=cid,
                generation=session.generation,
                method=method,
                status="cancelled",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        except BridgeRefused as exc:
            event(
                "appserver_request",
                cid=cid,
                generation=session.generation,
                method=method,
                status="refused",
                code=exc.code,
                category=exc.category,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        except BridgeError as exc:
            event(
                "appserver_request",
                cid=cid,
                generation=session.generation,
                method=method,
                status="failed",
                category=exc.category,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        finally:
            session.pending.pop(key, None)

        event(
            "appserver_request",
            cid=cid,
            generation=session.generation,
            method=method,
            status="ok",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return value

    async def call(self, method: str, params: Any = None, *, timeout: float | None = None) -> Any:
        """Send a request, lazily starting the process, and await the reply."""
        if method not in protocol.CLIENT_REQUEST_METHODS:
            # An unknown method would be a programming error, and sending one
            # would be the client inventing contract. Refuse locally.
            raise BridgeProtocolError("unknown_client_method")
        session = await self.ensure_started()
        return await self._request(session, method, params, timeout=timeout)

    # --- stderr ---------------------------------------------------------

    async def _drain_stderr(self, session: _Session) -> None:
        """Drain continuously; count only.

        Draining is not optional: a full stderr pipe blocks the child, and a
        blocked child looks exactly like a hung protocol. What is optional is
        keeping any of it, and this keeps none. Not the text, not a sample, not
        the first line "for debugging".
        """
        try:
            async for line in session.transport.stderr_lines():
                session.stderr_line_count += 1
                session.stderr_byte_count += len(line)
                if session.stderr_line_count % STDERR_REPORT_EVERY == 0:
                    event(
                        "appserver_stderr",
                        generation=session.generation,
                        lines=session.stderr_line_count,
                        bytes=session.stderr_byte_count,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            event("appserver_stderr_failed", generation=session.generation, category="read_error")

    # --- introspection for diagnostics and tests ------------------------

    def counters(self) -> dict[str, int]:
        session = self._session
        if session is None:
            return {"stderr_lines": 0, "malformed": 0, "unmatched_replies": 0, "pending": 0}
        return {
            "stderr_lines": session.stderr_line_count,
            "malformed": session.malformed_count,
            "unmatched_replies": session.orphan_reply_count,
            "pending": len(session.pending),
        }

    def exit_status(self) -> str | None:
        session = self._session
        return session.exit_status if session is not None else None
