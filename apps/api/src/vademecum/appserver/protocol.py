"""The wire contract, as pure functions over strings and dictionaries.

No I/O, no process, no asyncio. Everything here can be tested by handing it a
line of text.

The shape is not invented: it is read off the schema bundle the installed Codex
CLI generates for itself, pinned under ``schemas/codex-app-server/`` and checked
against this module by ``tests/test_appserver_schemas.py``.

Three properties of that contract are easy to get wrong and are therefore
stated here rather than assumed:

* **There is no ``jsonrpc`` member.** The default stdio transport is
  newline-delimited JSON. ``JSONRPCRequest`` in the generated schema requires
  ``id`` and ``method``; a version header is neither sent nor expected. An
  incoming one is ignored rather than rejected.
* **An id is a string or a 64-bit integer**, and the two are distinct. A server
  that answers ``"7"`` has not answered ``7``.
* **A result may legitimately be ``null``.** Presence of the ``result`` key,
  not its truthiness, is what makes a message a success reply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Union

# --- methods -----------------------------------------------------------------
#
# Every name here appears in schemas/codex-app-server/methods/*.json, and a test
# proves it. Nothing outside this block may spell a method inline.

INITIALIZE = "initialize"
INITIALIZED = "initialized"

ACCOUNT_READ = "account/read"
ACCOUNT_LOGIN_START = "account/login/start"
ACCOUNT_LOGIN_CANCEL = "account/login/cancel"
ACCOUNT_RATE_LIMITS_READ = "account/rateLimits/read"

THREAD_START = "thread/start"
TURN_START = "turn/start"
TURN_INTERRUPT = "turn/interrupt"
CONFIG_READ = "config/read"

CLIENT_REQUEST_METHODS = frozenset(
    {
        INITIALIZE,
        ACCOUNT_READ,
        ACCOUNT_LOGIN_START,
        ACCOUNT_LOGIN_CANCEL,
        ACCOUNT_RATE_LIMITS_READ,
        THREAD_START,
        TURN_START,
        TURN_INTERRUPT,
        CONFIG_READ,
    }
)

ACCOUNT_UPDATED = "account/updated"
ACCOUNT_RATE_LIMITS_UPDATED = "account/rateLimits/updated"
ACCOUNT_LOGIN_COMPLETED = "account/login/completed"

THREAD_STARTED = "thread/started"
TURN_STARTED = "turn/started"
TURN_COMPLETED = "turn/completed"
ITEM_STARTED = "item/started"
ITEM_COMPLETED = "item/completed"
SERVER_ERROR = "error"

# Notifications the bridge acts on. Everything else the server sends is counted
# and dropped.
OBSERVED_NOTIFICATIONS = frozenset(
    {
        ACCOUNT_UPDATED,
        ACCOUNT_RATE_LIMITS_UPDATED,
        ACCOUNT_LOGIN_COMPLETED,
        THREAD_STARTED,
        TURN_STARTED,
        TURN_COMPLETED,
        ITEM_STARTED,
        ITEM_COMPLETED,
        SERVER_ERROR,
    }
)

# SandboxMode / AskForApproval / SandboxPolicy vocabulary, spelled once. Every
# one of these is an enum member in the pinned v2 schemas and a test proves it.
SANDBOX_READ_ONLY = "read-only"
APPROVAL_NEVER = "never"
SANDBOX_POLICY_READ_ONLY = "readOnly"

# TurnStatus.
TURN_STATUS_COMPLETED = "completed"
TURN_STATUS_INTERRUPTED = "interrupted"
TURN_STATUS_FAILED = "failed"
TURN_STATUS_IN_PROGRESS = "inProgress"
TURN_STATUSES = frozenset(
    {TURN_STATUS_COMPLETED, TURN_STATUS_INTERRUPTED, TURN_STATUS_FAILED, TURN_STATUS_IN_PROGRESS}
)

# --- ThreadItem variants -----------------------------------------------------
#
# `type` is the discriminator of the `ThreadItem` oneOf inlined in the pinned
# ItemStartedNotification/ItemCompletedNotification documents.

# The one variant a grading turn reads.
ITEM_AGENT_MESSAGE = "agentMessage"
# Read by nothing. Named only so it can be allowed below.
ITEM_REASONING = "reasoning"
# The lifecycle's echo of the input we ourselves sent. Never model output.
ITEM_USER_MESSAGE = "userMessage"

# Item types that may appear but are not model output and are never read as an
# answer. `userMessage` is the turn lifecycle handing our own `turn/start`
# input back: `UserMessageThreadItem.content` is the same `UserInput` array we
# sent. A normal turn emits one, so refusing it outright failed every turn
# closed for the wrong reason -- but accepting it blindly would let a
# server-authored "user" message of any size arrive unbounded. So it is inert
# *and* checked: `user_message_texts` refuses anything that is not a bounded
# array of `text` parts, and `turns.py` additionally requires the parts to be
# exactly the prompt it sent. Its text is never retained, only its cost.
INERT_ITEM_TYPES = frozenset({ITEM_USER_MESSAGE})

# The ONLY item types a hardened grading turn may produce, on `item/started` and
# on `item/completed` alike. An allowlist rather than a denylist, because the
# union upstream has nineteen variants today and will have more tomorrow: a
# `ThreadItem` this build has never heard of is unknown, and unknown must read
# as unsafe at a trust boundary. A future Codex item type therefore fails the
# turn closed by default instead of arriving unnoticed.
#
# `agentMessage` is the answer itself. `reasoning` is the model's own summary
# text -- no command, no file, no MCP server, no network reach, no side effect
# of any kind -- and it cannot be suppressed from config, so allowing it is the
# difference between a hardened turn and a turn that always fails. Its content
# is never read. `userMessage` is the correlated input echo above. Nothing else
# is on this list; every other variant in the union (commandExecution,
# fileChange, mcpToolCall, dynamicToolCall, webSearch, imageView,
# imageGeneration, hookPrompt, functionCallOutput, subAgentActivity,
# collabAgentToolCall, sleep, plan, enteredReviewMode, exitedReviewMode,
# contextCompaction) means something ran that `hardening.py` disabled at two
# layers.
SAFE_ITEM_TYPES = frozenset({ITEM_AGENT_MESSAGE, ITEM_REASONING}) | INERT_ITEM_TYPES

# --- MessagePhase ------------------------------------------------------------
#
# `AgentMessageThreadItem.phase` classifies assistant text as interim commentary
# or as the terminal answer. Only the second may become a result.
MESSAGE_PHASE_COMMENTARY = "commentary"
MESSAGE_PHASE_FINAL_ANSWER = "final_answer"
MESSAGE_PHASES = frozenset({MESSAGE_PHASE_COMMENTARY, MESSAGE_PHASE_FINAL_ANSWER})

# Not an upstream value, and deliberately unspellable as one. What
# `agent_message_phase` reports for a `phase` that is present but is not a
# MessagePhase this build knows -- a variant added upstream, or a non-string.
# It is never treated as an answer.
MESSAGE_PHASE_UNRECOGNISED = "__unrecognised__"

# The only login Vademecum performs (AGENTS.md, boundary 4). The schema's
# LoginAccountParams union also offers `apiKey`; that variant must not be
# constructible anywhere in this codebase.
LOGIN_TYPE_DEVICE_CODE = "chatgptDeviceCode"

# Account types the schema can report. Only the first is usable here. The union
# also has `amazonBedrock`, which is neither named nor reachable: anything that
# is not `chatgpt` is refused by one rule, so a fourth variant added upstream
# needs no code change to be refused.
ACCOUNT_TYPE_CHATGPT = "chatgpt"
ACCOUNT_TYPE_API_KEY = "apiKey"

# Not upstream values, and deliberately unspellable as ones (an Account `type`
# is a lowerCamelCase identifier). What `account_type` reports for the two
# shapes that carry no type at all.
ACCOUNT_ABSENT = "__absent__"
ACCOUNT_UNREADABLE = "__unreadable__"

# The only model provider Vademecum will run a turn against. Pinned in three
# places: the `-c model_provider` layer every child starts with
# (`hardening.py`), the `modelProvider` member of every `thread/start`, and the
# `modelProvider` of the `ThreadStartResponse`, which `turns.py` checks before a
# turn is started. A custom provider is an API key with extra steps.
MODEL_PROVIDER_OPENAI = "openai"

# The `type` of the one `UserInput` variant this bridge ever sends or accepts.
# The union also offers image, localImage, audio, localAudio, skill and mention;
# none is an input this code can produce, so none may appear in an echo of it.
USER_INPUT_TEXT = "text"

# --- JSON-RPC error codes ----------------------------------------------------

METHOD_NOT_FOUND = -32601

# Categories for the codes the App Server may return, so a code never has to be
# interpreted twice. Unlisted codes fall back to "refused".
ERROR_CATEGORIES = {
    -32700: "protocol",
    -32600: "protocol",
    -32601: "method_not_found",
    -32602: "protocol",
    -32603: "server_error",
}


def error_category(code: int) -> str:
    return ERROR_CATEGORIES.get(code, "refused")


# --- identifiers -------------------------------------------------------------

RequestId = Union[str, int]
IdKey = tuple[str, RequestId]


def is_request_id(value: Any) -> bool:
    """A request id is a string or an integer -- and ``True`` is neither.

    ``isinstance(True, int)`` is true in Python, so a bool would otherwise be
    accepted as the id ``1`` and collide with a real one.
    """
    if isinstance(value, bool):
        return False
    return isinstance(value, (str, int))


def id_key(value: RequestId) -> IdKey:
    """A hashable key that keeps ``"7"`` and ``7`` apart."""
    return ("s", value) if isinstance(value, str) else ("i", value)


def is_error_code(value: Any) -> bool:
    """A JSON-RPC error code is an integer -- and ``True`` is not one.

    Same Python wrinkle as ``is_request_id``: ``isinstance(True, int)`` is
    true, so ``{"code": true}`` would otherwise be read as the code ``1`` and
    categorised as a refusal. A boolean where a code belongs is a malformed
    message, and is counted as one.
    """
    return isinstance(value, int) and not isinstance(value, bool)


# --- inbound messages --------------------------------------------------------


class MalformedMessage(Exception):
    """A line that could not be understood. Carries a category, not content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ServerRequest:
    """A request the server expects this client to answer."""

    id: RequestId
    method: str
    params: Any


@dataclass(frozen=True)
class ServerNotification:
    method: str
    params: Any


@dataclass(frozen=True)
class Result:
    id: RequestId
    result: Any


@dataclass(frozen=True)
class ErrorReply:
    id: RequestId
    code: int


Inbound = Union[ServerRequest, ServerNotification, Result, ErrorReply]


def decode_line(line: str) -> Inbound:
    """Parse one line of the stdout stream.

    Raises ``MalformedMessage`` with a category. The caller counts it and reads
    the next line: one unparseable line is not a reason to tear down a process
    that is otherwise answering.
    """
    try:
        payload = json.loads(line)
    except ValueError as exc:
        raise MalformedMessage("invalid_json") from exc

    if not isinstance(payload, dict):
        raise MalformedMessage("not_an_object")

    has_id = "id" in payload
    method = payload.get("method")

    if method is not None:
        if not isinstance(method, str) or not method:
            raise MalformedMessage("invalid_method")
        if has_id:
            if not is_request_id(payload["id"]):
                raise MalformedMessage("invalid_id")
            return ServerRequest(id=payload["id"], method=method, params=payload.get("params"))
        return ServerNotification(method=method, params=payload.get("params"))

    if not has_id:
        raise MalformedMessage("neither_request_nor_reply")
    if not is_request_id(payload["id"]):
        raise MalformedMessage("invalid_id")

    if "error" in payload:
        error = payload["error"]
        if not isinstance(error, dict) or not is_error_code(error.get("code")):
            raise MalformedMessage("invalid_error")
        # `message` is deliberately dropped here, at the edge, so no later code
        # has the option of logging it.
        return ErrorReply(id=payload["id"], code=int(error["code"]))

    if "result" in payload:
        # Presence, not truthiness: `"result": null` is a valid success reply.
        return Result(id=payload["id"], result=payload["result"])

    raise MalformedMessage("reply_without_result_or_error")


# --- outbound messages -------------------------------------------------------


def encode(message: dict[str, Any]) -> str:
    """One message, one line. Compact, and newline-terminated."""
    return json.dumps(message, separators=(",", ":")) + "\n"


def request(request_id: RequestId, method: str, params: Any = None) -> dict[str, Any]:
    message: dict[str, Any] = {"id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: Any = None) -> dict[str, Any]:
    message: dict[str, Any] = {"method": method}
    if params is not None:
        message["params"] = params
    return message


def result(request_id: RequestId, value: Any) -> dict[str, Any]:
    return {"id": request_id, "result": value}


def error(request_id: RequestId, code: int, message: str) -> dict[str, Any]:
    """An error reply.

    ``message`` is a fixed string chosen by this client -- never anything
    derived from what the server sent, and never anything derived from local
    content.
    """
    return {"id": request_id, "error": {"code": code, "message": message}}


def initialize_params(name: str, title: str, version: str) -> dict[str, Any]:
    """`initialize` params, with every capability explicitly refused.

    The capabilities are stated rather than omitted. Omission means "whatever
    the default is", and a default that changes in a later Codex release would
    quietly opt this client into a surface it has no code to handle. All four
    are surfaces this bridge must not have: experimental methods, MCP form
    elicitation, upstream attestation, and MCP extensions.

    ``extensions`` is the successor to ``mcpServerOpenaiFormElicitation`` and is
    a table rather than a toggle, so "none" is spelled as an empty one. Both are
    sent: the legacy flag still exists upstream, and a client that dropped it
    would be relying on a default.
    """
    return {
        "clientInfo": {"name": name, "title": title, "version": version},
        "capabilities": {
            "experimentalApi": False,
            "mcpServerOpenaiFormElicitation": False,
            "requestAttestation": False,
            "extensions": {},
        },
    }


def account_read_params() -> dict[str, Any]:
    """`refreshToken: false`: reading state must not perform a token refresh."""
    return {"refreshToken": False}


def device_login_params() -> dict[str, Any]:
    return {"type": LOGIN_TYPE_DEVICE_CODE}


def cancel_login_params(login_id: str) -> dict[str, Any]:
    return {"loginId": login_id}


# --- threads and turns -------------------------------------------------------


def grading_thread_params(
    cwd: str,
    *,
    base_instructions: str,
    developer_instructions: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """`thread/start` params for a grading thread.

    Every key here is a property of `ThreadStartParams` in the pinned schema,
    and nothing else is sent. In particular there is no `tools` or
    `tool_choice` member: those are not in the contract, and inventing one would
    be a safety property that exists only in this file. Tool suppression is
    ``hardening.py``'s job and travels in ``config``.

    ``ephemeral`` keeps the thread out of Codex's persisted history: a grading
    turn carries a learner's answer, and AGENTS.md's durable memory is the local
    database rather than a rollout file.

    ``modelProvider`` is stated rather than inherited. Omitting it means "the
    provider the owner's config.toml selects", and a `model_provider` pointing
    at a custom OpenAI-compatible endpoint is exactly the API-key billing
    boundary 4 forbids. The reply's own `modelProvider` is checked against this
    before a turn is started.
    """
    return {
        "cwd": cwd,
        "sandbox": SANDBOX_READ_ONLY,
        "approvalPolicy": APPROVAL_NEVER,
        "ephemeral": True,
        "modelProvider": MODEL_PROVIDER_OPENAI,
        "baseInstructions": base_instructions,
        "developerInstructions": developer_instructions,
        "config": config,
    }


def turn_params(thread_id: str, text: str, output_schema: dict[str, Any]) -> dict[str, Any]:
    """`turn/start` params: one text input, a strict output schema, no network.

    The sandbox and approval policy are restated per turn rather than inherited.
    Both are documented upstream as overrides "for this turn and subsequent
    turns", so stating them is what makes the turn's own guarantee independent
    of what the thread was started with.
    """
    return {
        "threadId": thread_id,
        "input": [{"type": USER_INPUT_TEXT, "text": text}],
        "outputSchema": output_schema,
        "approvalPolicy": APPROVAL_NEVER,
        "sandboxPolicy": {"type": SANDBOX_POLICY_READ_ONLY, "networkAccess": False},
    }


def interrupt_params(thread_id: str, turn_id: str) -> dict[str, Any]:
    return {"threadId": thread_id, "turnId": turn_id}


def config_read_params() -> dict[str, Any]:
    """The effective config, merged. Layers are not asked for: the merged view
    is what a thread would inherit, and it is the smaller answer."""
    return {"includeLayers": False}


def turn_status(params: Any) -> str | None:
    """The status of a `turn/completed` (or `turn/started`) notification.

    ``None`` when the notification is not shaped like one, or carries a status
    outside ``TurnStatus``. A caller must treat that as a failure rather than as
    success: a status this code does not know is not a status it can act on.
    """
    if not isinstance(params, dict):
        return None
    turn = params.get("turn")
    if not isinstance(turn, dict):
        return None
    status = turn.get("status")
    if isinstance(status, str) and status in TURN_STATUSES:
        return status
    return None


def account_type(reply: Any) -> str:
    """The `type` of the Account in a `GetAccountResponse`, or a sentinel.

    Three answers, and the caller must act on all three:

    * an Account `type` string -- which may be one this build has never heard
      of, and is returned as-is so the caller refuses it by name rather than by
      guessing;
    * ``ACCOUNT_ABSENT`` -- `account` is missing or JSON ``null``. The schema
      makes `account` optional and `requiresOpenaiAuth` mandatory, so this is
      the documented shape of "signed out";
    * ``ACCOUNT_UNREADABLE`` -- the reply is not a `GetAccountResponse` at all,
      or `account` is not an object, or its `type` is not a string. Not a
      credential this code can classify, so not one it may run a turn against.
    """
    if not isinstance(reply, dict):
        return ACCOUNT_UNREADABLE
    if "account" not in reply or reply["account"] is None:
        return ACCOUNT_ABSENT
    account = reply["account"]
    if not isinstance(account, dict):
        return ACCOUNT_UNREADABLE
    kind = account.get("type")
    return kind if isinstance(kind, str) and kind else ACCOUNT_UNREADABLE


def thread_model_provider(response: Any) -> str | None:
    """The `modelProvider` of a `ThreadStartResponse`.

    ``None`` when the reply is not shaped like one or does not carry the member
    as a string. It is a required property upstream, so its absence is a
    contract change rather than a default -- and the caller reads ``None`` as
    "not the provider we pinned".
    """
    if not isinstance(response, dict):
        return None
    provider = response.get("modelProvider")
    return provider if isinstance(provider, str) else None


def user_message_texts(item: Any, *, max_chars: int) -> tuple[str, ...] | None:
    """The `text` parts of a `userMessage` ThreadItem's `content`, bounded.

    ``None`` -- meaning "not an echo of anything this bridge sent" -- when the
    item is not a `userMessage`, when `content` is not a non-empty array, when
    any element is not a `text` `UserInput` (an image, audio, skill or mention
    part is not an input this code can produce), or when the parts total more
    than ``max_chars``. The bound is applied while walking, so an unbounded
    echo is refused without ever being assembled.
    """
    if not isinstance(item, dict) or item.get("type") != ITEM_USER_MESSAGE:
        return None
    content = item.get("content")
    if not isinstance(content, list) or not content:
        return None
    parts: list[str] = []
    total = 0
    for element in content:
        if not isinstance(element, dict) or element.get("type") != USER_INPUT_TEXT:
            return None
        text = element.get("text")
        if not isinstance(text, str):
            return None
        total += len(text)
        if total > max_chars:
            return None
        parts.append(text)
    return tuple(parts)


def item_type(item: Any) -> str | None:
    """The `type` discriminator of a ThreadItem, or ``None`` if there is not one.

    ``None`` is not a safe item type: a payload with no readable discriminator
    cannot be matched against ``SAFE_ITEM_TYPES`` and must fail closed.
    """
    if not isinstance(item, dict):
        return None
    value = item.get("type")
    return value if isinstance(value, str) else None


def agent_message_text(item: Any) -> str | None:
    """The `text` of an `agentMessage` ThreadItem, or ``None`` for anything else."""
    if not isinstance(item, dict) or item.get("type") != ITEM_AGENT_MESSAGE:
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None


def agent_message_phase(item: Any) -> str | None:
    """The `phase` of an `agentMessage`, as one of three answers.

    * a member of ``MESSAGE_PHASES`` -- the provider said which it is;
    * ``None`` -- ``phase`` is absent or JSON ``null``. The schema's own note is
      that providers do not emit it consistently and that ``None`` means "phase
      unknown", so this is the legacy case rather than a refusal;
    * ``MESSAGE_PHASE_UNRECOGNISED`` -- present, and something else entirely.
    """
    if not isinstance(item, dict):
        return MESSAGE_PHASE_UNRECOGNISED
    if "phase" not in item:
        return None
    phase = item["phase"]
    if phase is None:
        return None
    if isinstance(phase, str) and phase in MESSAGE_PHASES:
        return phase
    return MESSAGE_PHASE_UNRECOGNISED
