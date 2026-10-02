"""A scripted App Server. No process, no network, no model.

AGENTS.md forbids production model calls in the test suite and asks for a
scripted fake transport instead. This is that fake: it satisfies the
``Transport`` protocol, so the real ``AppServerClient`` and ``ModelBridge`` run
against it unchanged -- correlation, initialisation, notifications, refusals,
timeouts, death and restart included.

It is deterministic by construction. Nothing here sleeps, polls or races: a
reply is queued during the same ``send_line`` that delivered the request, so the
client's read loop sees it on its next turn of the event loop.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from vademecum.appserver.errors import BridgeUnavailable


class NoReply:
    """Send nothing back. Used to exercise the request timeout."""


@dataclass(frozen=True)
class Error:
    """Reply with a JSON-RPC error instead of a result."""

    code: int
    message: str = "scripted failure"


@dataclass(frozen=True)
class Raw:
    """Write an exact line, however malformed."""

    line: str


Reply = Any  # a dict result, Error, NoReply, Raw, or a callable returning one


class ScriptedTransport:
    """One 'process'. A new instance per start, like the real thing."""

    def __init__(
        self,
        *,
        responder: Callable[["ScriptedTransport", dict[str, Any]], None] | None = None,
        start_error: BaseException | None = None,
        exit_status: str = "exit:0",
    ) -> None:
        self._stdout: asyncio.Queue[str | None] = asyncio.Queue()
        self._stderr: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.responder = responder
        self.start_error = start_error
        self.write_error: BaseException | None = None
        self.exit_status = exit_status
        self.started = False
        self.stopped = False

    # --- the Transport protocol -----------------------------------------

    async def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    async def send_line(self, line: str) -> None:
        if self.write_error is not None:
            raise self.write_error
        message = json.loads(line)
        self.sent.append(message)
        if self.responder is not None:
            self.responder(self, message)

    async def stdout_lines(self):
        while True:
            item = await self._stdout.get()
            if item is None:
                return
            yield item

    async def stderr_lines(self):
        while True:
            item = await self._stderr.get()
            if item is None:
                return
            yield item

    async def stop(self) -> str:
        if not self.stopped:
            self.stopped = True
            self._stdout.put_nowait(None)
            self._stderr.put_nowait(None)
        return self.exit_status

    # --- what a test drives it with -------------------------------------

    def emit(self, message: dict[str, Any] | str) -> None:
        self._stdout.put_nowait(message if isinstance(message, str) else json.dumps(message))

    def emit_stderr(self, line: bytes) -> None:
        self._stderr.put_nowait(line)

    def die(self, exit_status: str = "exit:1") -> None:
        """The child goes away: stdout reaches EOF."""
        self.exit_status = exit_status
        self._stdout.put_nowait(None)

    def requests(self, method: str) -> list[dict[str, Any]]:
        return [message for message in self.sent if message.get("method") == method]

    def notifications(self, method: str) -> list[dict[str, Any]]:
        return [
            message
            for message in self.sent
            if message.get("method") == method and "id" not in message
        ]

    def replies(self) -> list[dict[str, Any]]:
        """What the client answered to server-initiated requests."""
        return [message for message in self.sent if "method" not in message and "id" in message]


DEFAULT_INITIALIZE_RESULT = {
    # Shaped after v1/InitializeResponse.json. No path from this machine.
    "codexHome": "/tmp/codex-home",
    "platformFamily": "unix",
    "platformOs": "macos",
    "userAgent": "codex-cli/test",
}

SIGNED_OUT_ACCOUNT: dict[str, Any] = {"account": None, "requiresOpenaiAuth": True}


def chatgpt_account(plan: str = "plus", email: str = "owner@example.test") -> dict[str, Any]:
    """A signed-in ChatGPT account, email included.

    The email is present on purpose. It is what the sanitisation tests look for
    in every response body and every log line, and a fake that omitted it would
    make those tests pass for the wrong reason.
    """
    return {
        "account": {"type": "chatgpt", "email": email, "planType": plan},
        "requiresOpenaiAuth": True,
    }


def api_key_account() -> dict[str, Any]:
    """The credential Vademecum refuses to use (AGENTS.md, boundary 4)."""
    return {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True}


def rate_limits(
    *,
    primary_used: int = 12,
    secondary_used: int | None = 40,
    reached: str | None = None,
    resets_at: int | None = 1_800_000_000,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "primary": {
            "usedPercent": primary_used,
            "resetsAt": resets_at,
            "windowDurationMins": 300,
        },
        "limitId": "codex",
        "limitName": "Codex weekly",
        "credits": {"hasCredits": True, "unlimited": False, "balance": "12.34"},
        "rateLimitReachedType": reached,
        "spendControlReached": False,
    }
    if secondary_used is not None:
        snapshot["secondary"] = {
            "usedPercent": secondary_used,
            "resetsAt": resets_at,
            "windowDurationMins": 10080,
        }
    return snapshot


DEVICE_LOGIN_RESULT = {
    "type": "chatgptDeviceCode",
    "loginId": "login-abc123",
    "userCode": "WXYZ-1234",
    "verificationUrl": "https://example.test/device",
}


@dataclass
class AccountScript:
    """A responder that behaves like the account surface of an App Server.

    Every reply is overridable per method, so a test can turn any one of them
    into an error, a silence, or a malformed line without rewriting the rest.
    """

    account: dict[str, Any] = field(default_factory=lambda: dict(SIGNED_OUT_ACCOUNT))
    limits: dict[str, Any] | None = None
    login_result: Any = field(default_factory=lambda: dict(DEVICE_LOGIN_RESULT))
    cancel_result: Any = field(default_factory=lambda: {"status": "canceled"})
    initialize_result: Any = field(default_factory=lambda: dict(DEFAULT_INITIALIZE_RESULT))
    overrides: dict[str, Reply] = field(default_factory=dict)
    # Set to reply with an id of a different type than the one received, to
    # prove string and integer ids are kept apart.
    echo_id_as_string: bool = False
    # How many `account/read` requests arrived, counted before any override can
    # short-circuit. The per-turn preflight is asserted against this.
    reads: int = 0

    def _base(self, method: str) -> Reply:
        if method == "initialize":
            return self.initialize_result
        if method == "account/read":
            return self.account
        if method == "account/rateLimits/read":
            return {"rateLimits": self.limits if self.limits is not None else rate_limits()}
        if method == "account/login/start":
            return self.login_result
        if method == "account/login/cancel":
            return self.cancel_result
        return Error(-32601, "no such method")

    def __call__(self, transport: ScriptedTransport, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None or "id" not in message:
            return  # a notification from the client, or a reply to us
        if method == "account/read":
            self.reads += 1
        reply = self.overrides.get(method, self._base(method))
        if callable(reply) and not isinstance(reply, type):
            reply = reply(transport, message)

        if reply is NoReply or isinstance(reply, NoReply):
            return
        if isinstance(reply, Raw):
            transport.emit(reply.line)
            return

        request_id = message["id"]
        if self.echo_id_as_string and isinstance(request_id, int):
            request_id = str(request_id)
        if isinstance(reply, Error):
            transport.emit(
                {"id": request_id, "error": {"code": reply.code, "message": reply.message}}
            )
        else:
            transport.emit({"id": request_id, "result": reply})


# --- threads and turns -------------------------------------------------------

DEFAULT_MCP_SERVERS = ("computer-use", "node_repl")


def config_read_result(names: tuple[str, ...] | list[str] = DEFAULT_MCP_SERVERS) -> dict[str, Any]:
    """A ConfigReadResponse with an `mcp_servers` table, shaped like the real one.

    The servers are *enabled* here on purpose: that is the state the owner's
    machine is actually in, and a fixture that pre-disabled them would make the
    hardening tests pass without the hardening.
    """
    return {
        "config": {
            "model": "gpt-test",
            "web_search": "disabled",
            "mcp_servers": {
                name: {"command": "/bin/false", "args": [], "enabled": True} for name in names
            },
        },
        "origins": {},
    }


# A `phase` value that is not in MessagePhase. Used to prove an unrecognised
# phase is never read as the answer.
UNKNOWN_PHASE = "someNewPhase"

# What `thread_item` uses to mean "send no `phase` key at all", which is a
# different thing from `phase: null` and both have to be drivable.
ABSENT = object()


def thread_item(
    text: str,
    item_id: str = "item-1",
    phase: Any = ABSENT,
) -> dict[str, Any]:
    """An `agentMessage` ThreadItem, shaped after the pinned schema.

    ``phase`` defaults to absent, which is the legacy shape the schema warns
    about. Pass ``None`` for an explicit JSON ``null``, or any MessagePhase
    string.
    """
    item: dict[str, Any] = {"id": item_id, "type": "agentMessage", "text": text}
    if phase is not ABSENT:
        item["phase"] = phase
    return item


def user_message_item(
    *texts: str, item_id: str = "item-1", content: Any = ABSENT
) -> dict[str, Any]:
    """The `userMessage` echo the lifecycle emits for our own `turn/start` input.

    Shaped after `UserMessageThreadItem` in the pinned union: a `content` array
    of `UserInput`s. ``content`` overrides the text parts entirely, which is how
    a test drives an image part, an empty array, or something that is not an
    array at all.
    """
    parts: Any = (
        [{"type": "text", "text": text} for text in texts] if content is ABSENT else content
    )
    return {"id": item_id, "type": "userMessage", "content": parts, "clientId": None}


def native_item(item_type: str, item_id: str = "item-1", **extra: Any) -> dict[str, Any]:
    """A ThreadItem of any other type, shaped just enough to be dispatched.

    The runner reads the discriminator and nothing else, so a `commandExecution`
    here does not have to carry every required field to be the thing that must
    fail a turn closed.
    """
    return {"id": item_id, "type": item_type, **extra}


def item_completed(thread_id: str, turn_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "item/completed",
        "params": {"threadId": thread_id, "turnId": turn_id, "item": item},
    }


def item_started(thread_id: str, turn_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "item/started",
        "params": {
            "threadId": thread_id,
            "turnId": turn_id,
            "item": item,
            "startedAtMs": 0,
        },
    }


def turn_notification(
    method: str,
    thread_id: str,
    turn_id: str,
    status: str,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    turn: dict[str, Any] = {"id": turn_id, "items": [], "status": status}
    if error is not None:
        turn["error"] = error
    return {"method": method, "params": {"threadId": thread_id, "turn": turn}}


def turn_error(info: str | None, message: str = "upstream text that must never be logged"):
    error: dict[str, Any] = {"message": message}
    if info is not None:
        error["codexErrorInfo"] = info
    return error


@dataclass
class TurnScript:
    """A responder that behaves like the thread and turn surface of an App Server.

    It delegates `initialize` and every account method to an ``AccountScript``,
    so the account bridge keeps working against the same 'process' while a turn
    is in flight -- which is one of the things the tests have to prove.

    Everything a turn can do wrong is a field: a failed status, an oversized
    message, silence, a tool call that should be impossible, and the child dying
    halfway through. Nothing sleeps: the reply and the notifications that follow
    it are queued during the same ``send_line``.
    """

    # Signed in with ChatGPT by default, because every turn now re-reads the
    # account before it starts a thread and a signed-out fake would fail every
    # turn on the preflight instead of on the thing under test. Tests that mean
    # to drive the preflight pass their own `AccountScript`.
    account: AccountScript = field(
        default_factory=lambda: AccountScript(account=chatgpt_account())
    )
    mcp_servers: tuple[str, ...] = DEFAULT_MCP_SERVERS
    config_reply: Any = None
    # What `thread/start` reports as its `modelProvider`. `ABSENT` omits the
    # member, which upstream requires -- so its absence is a contract change and
    # has to be refused too.
    thread_model_provider: Any = "openai"
    messages: tuple[str, ...] = ("{}",)
    # Arbitrary ThreadItems, which replace `messages` when set. This is how a
    # test drives a `commandExecution`, a `reasoning`, or an `agentMessage` with
    # a particular `phase`.
    items: tuple[dict[str, Any], ...] | None = None
    emit_item_started: bool = True
    emit_item_completed: bool = True
    status: str = "completed"
    error: dict[str, Any] | None = None
    # Answer `turn/start` and then say nothing at all.
    silent: bool = False
    # Reach EOF right after answering `turn/start`.
    die_after_turn_start: bool = False
    # Emit `turn/started` -- which names the turn -- and then never answer the
    # `turn/start` request. The turn is running upstream; the RPC times out.
    turn_start_silent: bool = False
    # A server-initiated request that a hardened turn must never produce.
    tool_event: str | None = None
    tool_event_params: dict[str, Any] | None = None
    # Emit the turn's events before the `turn/start` reply rather than after.
    events_before_reply: bool = False
    emit_turn_started: bool = True
    # A burst of `item/completed` agent messages ahead of everything else.
    flood: int = 0
    flood_text: str = "{}"
    # Ids to put on `turn/completed` instead of the turn's own. Either one makes
    # the completion somebody else's.
    completed_thread_id: str | None = None
    completed_turn_id: str | None = None
    interrupt_reply: Any = field(default_factory=dict)
    overrides: dict[str, Reply] = field(default_factory=dict)
    # A hook that replaces the default emission entirely:
    # (script, transport, thread_id, turn_id) -> None.
    turn_behaviour: Callable[["TurnScript", ScriptedTransport, str, str], None] | None = None

    threads: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    config_reads: int = 0
    _server_request_id: int = 0

    # --- ids -------------------------------------------------------------

    def thread_id(self, index: int) -> str:
        return f"thread-{index + 1}"

    def turn_id(self, index: int) -> str:
        return f"turn-{index + 1}"

    # --- the responder ---------------------------------------------------

    def __call__(self, transport: ScriptedTransport, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None or "id" not in message:
            return
        # Recorded before an override can short-circuit: a test that turns
        # `thread/start` into an error still wants to see what was sent.
        if method == "thread/start":
            self.threads.append(message.get("params") or {})
        elif method == "turn/start":
            self.turns.append(message.get("params") or {})
        if method in self.overrides:
            self._reply(transport, message, self.overrides[method])
            return
        if method == "thread/start":
            self._thread_start(transport, message)
        elif method == "turn/start":
            self._turn_start(transport, message)
        elif method == "turn/interrupt":
            self.interrupts.append(message.get("params") or {})
            self._reply(transport, message, self.interrupt_reply)
        elif method == "config/read":
            self.config_reads += 1
            reply = self.config_reply
            if reply is None:
                reply = config_read_result(self.mcp_servers)
            self._reply(transport, message, reply)
        else:
            self.account(transport, message)

    def _reply(self, transport: ScriptedTransport, message: dict[str, Any], reply: Reply) -> None:
        if callable(reply) and not isinstance(reply, type):
            reply = reply(transport, message)
        if reply is NoReply or isinstance(reply, NoReply):
            return
        if isinstance(reply, Raw):
            transport.emit(reply.line)
            return
        if isinstance(reply, Error):
            transport.emit(
                {"id": message["id"], "error": {"code": reply.code, "message": reply.message}}
            )
            return
        transport.emit({"id": message["id"], "result": reply})

    def _thread_start(self, transport: ScriptedTransport, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        thread_id = self.thread_id(len(self.threads) - 1)
        thread = {"id": thread_id, "ephemeral": params.get("ephemeral"), "cwd": params.get("cwd")}
        result: dict[str, Any] = {
            "thread": thread,
            "cwd": params.get("cwd"),
            "sandbox": params.get("sandbox"),
            "approvalPolicy": params.get("approvalPolicy"),
            "approvalsReviewer": None,
            "model": "gpt-test",
        }
        if self.thread_model_provider is not ABSENT:
            result["modelProvider"] = self.thread_model_provider
        self._reply(transport, message, result)
        transport.emit({"method": "thread/started", "params": {"thread": thread}})

    def _turn_start(self, transport: ScriptedTransport, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        index = len(self.turns) - 1
        thread_id = params.get("threadId") or self.thread_id(index)
        turn_id = self.turn_id(index)
        reply = {"turn": {"id": turn_id, "items": [], "status": "inProgress"}}
        if self.turn_start_silent:
            transport.emit(turn_notification("turn/started", thread_id, turn_id, "inProgress"))
            return
        if self.events_before_reply:
            # The events land before the reply that names the turn, which is why
            # the runner subscribes before sending the request.
            self.drive(transport, thread_id, turn_id)
            self._reply(transport, message, reply)
            return

        self._reply(transport, message, reply)
        self.drive(transport, thread_id, turn_id)

    # --- what the turn does ----------------------------------------------

    def emitted_items(self) -> tuple[dict[str, Any], ...]:
        """The ThreadItems this turn streams, from whichever field was set."""
        if self.items is not None:
            return tuple(
                {"id": f"item-{position + 1}", **item}
                for position, item in enumerate(self.items)
            )
        return tuple(
            thread_item(text, item_id=f"item-{position + 1}")
            for position, text in enumerate(self.messages)
        )

    def drive(self, transport: ScriptedTransport, thread_id: str, turn_id: str) -> None:
        if self.turn_behaviour is not None:
            self.turn_behaviour(self, transport, thread_id, turn_id)
            return
        if self.die_after_turn_start:
            transport.die()
            return
        if self.silent:
            return
        if self.emit_turn_started:
            transport.emit(turn_notification("turn/started", thread_id, turn_id, "inProgress"))
        if self.tool_event is not None:
            self.emit_tool_event(transport, thread_id, turn_id)
            return
        for position in range(self.flood):
            flooded = thread_item(self.flood_text, item_id=f"flood-{position + 1}")
            transport.emit(item_completed(thread_id, turn_id, flooded))
        for item in self.emitted_items():
            if self.emit_item_started:
                transport.emit(item_started(thread_id, turn_id, item))
            if self.emit_item_completed:
                transport.emit(item_completed(thread_id, turn_id, item))
        transport.emit(
            turn_notification(
                "turn/completed",
                self.completed_thread_id or thread_id,
                self.completed_turn_id or turn_id,
                self.status,
                self.error,
            )
        )

    def emit_tool_event(
        self, transport: ScriptedTransport, thread_id: str | None, turn_id: str | None
    ) -> None:
        """A server-initiated request a hardened turn must never provoke."""
        self._server_request_id += 1
        params = self.tool_event_params
        if params is None:
            params = {"itemId": "i", "reason": "because"}
            if thread_id is not None:
                params = {**params, "threadId": thread_id, "turnId": turn_id}
        transport.emit(
            {
                "id": f"server-{self._server_request_id}",
                "method": self.tool_event or "item/tool/call",
                "params": params,
            }
        )


def deliver(client: Any, generation: int, message: dict[str, Any]) -> None:
    """Hand one notification to a client's subscribers *as if* it arrived in
    ``generation``.

    The only way to simulate an event that crossed a restart. A real restart
    cannot be used for this: the client publishes no session while it is
    replacing one, so a runner's liveness poll ends the turn before the event
    from the new child could ever be observed. This drives the same fan-out the
    read loop drives, with the generation as the one dial the test turns.
    """
    method = message["method"]
    client._dispatch(generation, method, message.get("params"), method)


def factory(
    *transports: ScriptedTransport,
) -> Callable[[], ScriptedTransport]:
    """A transport factory that hands out the given transports in order.

    Running past the end is an error rather than a silent extra process: a test
    that starts a third child when it expected two should say so.
    """
    queue = list(transports)

    def make() -> ScriptedTransport:
        if not queue:
            raise BridgeUnavailable("no_more_scripted_transports")
        return queue.pop(0)

    return make


def scripted(script: AccountScript | None = None) -> ScriptedTransport:
    return ScriptedTransport(responder=script or AccountScript())
