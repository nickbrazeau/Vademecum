"""One schema-constrained turn, run to a validated payload or to a category.

This is the reusable half of AGENTS.md's Tutor requirement: start an ephemeral,
read-only, approval-free thread in an isolated directory, run exactly one turn
with a strict ``outputSchema``, collect the final agent message from streamed
item events, wait for ``turn/completed``, and validate before anything else in
the application is allowed to see the result. What the prompt says and what the
schema means are the caller's business; this module has no clinical knowledge.

Eight rules shape it:

0. **Whose plan is this?** Before a thread is started, every turn re-reads
   ``account/read`` and requires a ``chatgpt`` account, and every ``thread/start``
   both sends and verifies ``modelProvider: "openai"``. AGENTS.md boundary 4
   forbids API-key billing and forbids falling back to it silently; a status
   snapshot taken minutes ago is not a fact about the account this turn will be
   billed to. The check is read-only: it never logs in, never sets a login
   method, and never touches a credential.
1. **Fail closed on anything unexpected.** Not only an approval request or a
   client-tool call: any ``item/started`` or ``item/completed`` carrying a
   ThreadItem type outside ``protocol.SAFE_ITEM_TYPES``. That set is an
   allowlist of three, so a Codex release that adds an item variant -- or
   ignores our config and runs a command -- fails the turn instead of streaming
   past it. The third member, ``userMessage``, is the lifecycle echoing our own
   prompt back: inert, bounded, correlated against what we sent, counted
   against the retained budget, and never read as output.
2. **Only the final answer is an answer.** ``AgentMessageThreadItem.phase``
   distinguishes interim commentary from terminal answer text. Commentary is
   never the result, whatever JSON it happens to contain.
3. **Every event is matched three ways** -- thread id, turn id, and the client's
   child generation. A ``turn/completed`` naming a different turn does not
   complete ours, and an event from a process that has since been replaced does
   not make a dead turn look alive. Mismatches are counted and dropped. The
   generation is checked in *both* directions: an event must carry the
   generation the thread started in, and that generation must still be the
   client's current one -- while waiting and again before a payload is
   returned. A delayed answer from a child that has been replaced is discarded,
   not returned.
4. **Bounded everything.** A deadline, a maximum retained size *and count*, and
   a bounded interrupt on every exit that is not a clean completion -- timeout,
   cancellation, overflow, unsafe event, invalid output. A turn abandoned
   without an interrupt keeps running upstream and keeps spending the owner's
   plan. An interrupt never starts a child: if the generation is gone there is
   no turn of ours left in it.
5. **Demultiplex here, not in the client.** ``AppServerClient`` fans every
   observed notification out to every subscriber and knows nothing about
   threads.
6. **Categories only.** ``TurnError.message`` is upstream text that can quote
   the prompt back. It is read for nothing, logged nowhere, and never reaches a
   raised exception. Only ``codexErrorInfo``, a closed enum, survives -- as one
   of this module's own category strings.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import hardening, protocol
from .client import UNEXPECTED_TOOL_EVENTS, AppServerClient
from .diagnostics import event, new_correlation_id, safe_method
from .errors import BridgeError, BridgeProtocolError, BridgeTimeout, BridgeUnavailable


class TurnRefused(BridgeError):
    """The turn ran and ended in ``failed``."""

    category = "refused"


class TurnUnsafe(BridgeError):
    """An event a turn that must never produce one nonetheless produced.

    A tool call, an approval request, an MCP elicitation -- or a ThreadItem of a
    type outside the allowlist on our own thread and turn.
    """

    category = "unsafe_tool_event"


class UnenforceableSchema(BridgeProtocolError):
    """An ``outputSchema`` carrying a constraint the local validator ignores."""

    category = "schema_unenforceable"


class CredentialUnusable(BridgeError):
    """The account a turn would be billed to is not a ChatGPT sign-in.

    Raised by the per-turn preflight, in one of three categories -- see
    ``ACCOUNT_CATEGORIES``. Every one of them means no content turn ran.
    """

    category = "signed_out"


class ProviderUnsupported(BridgeError):
    """``thread/start`` came back naming a model provider other than OpenAI.

    A custom provider is a custom endpoint and a custom key: AGENTS.md boundary
    4 forbids that outright, and forbids falling back to it silently. The thread
    is abandoned with no turn started.
    """

    category = "unsupported_provider"


# CodexErrorInfo values worth telling apart, because each maps to a different
# thing the owner can do about it. Everything else is a plain refusal.
ERROR_CATEGORIES = {
    "usageLimitExceeded": "rate_limited",
    "rateLimitExceeded": "rate_limited",
    "unauthorized": "signed_out",
    "contextWindowExceeded": "context_exceeded",
}

# What the per-turn account preflight can conclude, as the closed set of
# categories ``CredentialUnusable`` is raised with. Each is a different thing
# the owner can do about it and none of them is "carry on".
ACCOUNT_SIGNED_OUT = "signed_out"  # no account: sign in with ChatGPT
ACCOUNT_UNSUPPORTED = "unsupported_credential"  # apiKey, or anything not chatgpt
ACCOUNT_UNREADABLE = "account_unreadable"  # the read failed or made no sense
ACCOUNT_CATEGORIES = frozenset({ACCOUNT_SIGNED_OUT, ACCOUNT_UNSUPPORTED, ACCOUNT_UNREADABLE})

# How often the wait for completion looks up to check the child is still there.
# A dead process is not an event the client emits, and a turn that outlives its
# process would otherwise sit until the deadline.
LIVENESS_POLL_SECONDS = 0.05

# How long a cancelled run waits for its own interrupt to reach the child before
# giving up on it. Bounded: a shutdown must not be held open by a dead pipe.
INTERRUPT_GRACE_SECONDS = 5.0

# The most agent messages one turn may produce before it is treated as runaway
# output. The character budget alone does not bound this: ten thousand
# one-character messages cost nothing in `_chars` and everything in a list that
# grows while the `turn/start` reply is still outstanding. Generous enough that
# a chatty-but-finite model still finishes.
MAX_AGENT_MESSAGES = 64

# The two notifications that carry a ThreadItem.
_ITEM_METHODS = (protocol.ITEM_STARTED, protocol.ITEM_COMPLETED)


@dataclass(frozen=True)
class TurnResult:
    payload: dict[str, Any]
    raw_chars: int
    turn_id: str
    duration_ms: float


# --- outcomes the read-loop handler can post ---------------------------------


@dataclass(frozen=True)
class _Completed:
    status: str
    error_category: str | None


@dataclass(frozen=True)
class _Unsafe:
    pass


@dataclass(frozen=True)
class _Overflow:
    pass


_Outcome = _Completed | _Unsafe | _Overflow


class TurnRunner:
    """One turn at a time, on a shared client.

    ``workspace`` must be an existing, isolated directory. It is the turn's
    ``cwd``, which is what a read-only sandbox is read-only *relative to* and
    what project context would be resolved from. Nothing here writes to it.
    """

    def __init__(
        self,
        client: AppServerClient,
        *,
        workspace: Path,
        turn_timeout: float = 180.0,
    ) -> None:
        self._client = client
        self._workspace = workspace
        self._turn_timeout = turn_timeout

        self._thread_id: str | None = None
        self._turn_id: str | None = None
        # The child generation the thread was started in. Every event has to
        # come from it, and the interrupt is only sent while it is still current.
        self._generation: int | None = None
        # The prompt this turn sent, kept only to correlate the lifecycle's
        # `userMessage` echo of it. Never logged, never returned.
        self._prompt: str | None = None
        self._outcome: asyncio.Future[_Outcome] | None = None
        self._final_answer: str | None = None
        self._unphased: str | None = None
        self._messages = 0
        self._chars = 0
        self._max_chars = 0
        self._settled = False
        self._ignored = 0
        self._interrupted = False

    # --- the public call -------------------------------------------------

    async def run(
        self,
        *,
        instructions: str,
        developer_instructions: str,
        prompt: str,
        output_schema: dict[str, Any],
        max_output_chars: int = 200_000,
    ) -> TurnResult:
        if not self._workspace.is_dir():
            # Not created here on purpose. An isolated empty directory is the
            # caller's guarantee, and a runner that made one would be deciding
            # where a model is allowed to look.
            raise BridgeProtocolError("workspace_missing")

        # Before anything is sent, and before a child is even started: a schema
        # this module cannot check on the way back is not a schema it will ask
        # a model to honour on the way out.
        assert_enforceable(output_schema)

        cid = new_correlation_id()
        started = time.perf_counter()
        self._reset(max_output_chars, prompt)

        # Whose plan this turn spends, asked now rather than remembered. Before
        # the config read, before the thread, before anything that could bill.
        await self._require_chatgpt_account(cid)

        # Read per thread, not per client: the owner's MCP config can change
        # between two grading turns (hardening.py).
        config = await hardening.thread_config(self._client)
        thread = await self._client.call(
            protocol.THREAD_START,
            protocol.grading_thread_params(
                str(self._workspace),
                base_instructions=instructions,
                developer_instructions=developer_instructions,
                config=config,
            ),
        )
        self._thread_id = _thread_id_of(thread)
        if self._thread_id is None:
            raise BridgeProtocolError("no_thread_id")
        # Captured with the thread, not with the turn: everything this run will
        # ever accept has to have come out of this child.
        self._generation = self._client.generation

        # The provider we asked for, read back off the reply, before a turn is
        # started. There is nothing to interrupt on this path -- no turn exists
        # yet, so `turn/interrupt` has no id to name -- and the thread is
        # ephemeral, unnamed by any turn, and costs nothing while it sits there
        # unused. The pinned method list has no `thread/close`, so nothing else
        # is sent either.
        provider = protocol.thread_model_provider(thread)
        if provider != protocol.MODEL_PROVIDER_OPENAI:
            event(
                "appserver_turn_provider",
                cid=cid,
                method=protocol.THREAD_START,
                status="failed",
                category=ProviderUnsupported.category,
            )
            raise ProviderUnsupported()

        # Subscribed before `turn/start` is sent, so nothing can arrive between
        # the request and the subscription. The first events of a turn -- and an
        # unexpected tool call in particular -- can precede the reply.
        self._outcome = asyncio.get_running_loop().create_future()
        unsubscribe = self._client.subscribe(self._observe)
        try:
            turn = await self._client.call(
                protocol.TURN_START,
                protocol.turn_params(self._thread_id, prompt, output_schema),
            )
            self._adopt_turn_id(_turn_id_of(turn))
            outcome = await self._await_outcome(cid)
            return await self._finish(outcome, output_schema, cid, started)
        except asyncio.CancelledError:
            await self._interrupt_detached("cancelled")
            raise
        except BaseException:
            # The single place every unsuccessful exit passes through. A
            # `turn/start` whose RPC timed out after `turn/started` already
            # named the turn is the case that matters: the turn is running
            # upstream and only the notification told us its id. `_interrupt`
            # is idempotent and does nothing when there is no turn id or when
            # a more specific reason already interrupted.
            await self._interrupt("failed")
            raise
        finally:
            unsubscribe()

    async def cancel(self) -> None:
        """Interrupt the turn in flight, if there is one. Safe to call twice."""
        await self._interrupt("cancelled")

    # --- the preflight ---------------------------------------------------

    async def _require_chatgpt_account(self, cid: str) -> None:
        """A fresh ``account/read`` before every turn. Read-only, fail closed.

        AGENTS.md boundary 4: no API-key billing, ever, and no silent fallback.
        That is a property of the credential a turn is *about to* run against,
        so it is asked here rather than inherited from a status snapshot the
        Model page took minutes ago -- the owner can log out, or log in with
        something else, between two grading turns, and the account facade's
        cached view would still say what it said then.

        ``refreshToken: false``: reading state must not perform a refresh. And
        nothing on this path writes: no ``account/login/start``, no forced login
        method, no logout, no token, no credential file. The account's email is
        never read, so it cannot be logged; only a category ever is.

        Anything other than a ``chatgpt`` account raises, which means no
        ``thread/start`` and therefore no turn.
        """
        try:
            reply = await self._client.call(
                protocol.ACCOUNT_READ, protocol.account_read_params()
            )
        except asyncio.CancelledError:
            raise
        except BridgeError as exc:
            # A refusal, a timeout, a dead child: the credential is unknown, and
            # unknown is not something a turn may be started on top of. `exc` is
            # chained but not logged: its category is upstream-shaped.
            self._account_failed(cid, ACCOUNT_UNREADABLE)
            raise CredentialUnusable(ACCOUNT_UNREADABLE) from exc
        except Exception as exc:
            self._account_failed(cid, ACCOUNT_UNREADABLE)
            raise CredentialUnusable(ACCOUNT_UNREADABLE) from exc

        kind = protocol.account_type(reply)
        if kind == protocol.ACCOUNT_TYPE_CHATGPT:
            event("appserver_turn_account", cid=cid, method=protocol.ACCOUNT_READ, status="ok")
            return
        if kind == protocol.ACCOUNT_ABSENT:
            category = ACCOUNT_SIGNED_OUT
        elif kind == protocol.ACCOUNT_UNREADABLE:
            category = ACCOUNT_UNREADABLE
        else:
            # `apiKey`, `amazonBedrock`, or a variant added upstream. One rule
            # refuses all three, so a new one needs no code to be refused.
            category = ACCOUNT_UNSUPPORTED
        self._account_failed(cid, category)
        raise CredentialUnusable(category)

    @staticmethod
    def _account_failed(cid: str, category: str) -> None:
        event(
            "appserver_turn_account",
            cid=cid,
            method=protocol.ACCOUNT_READ,
            status="failed",
            category=category,
        )

    # --- internals -------------------------------------------------------

    def _reset(self, max_output_chars: int, prompt: str) -> None:
        self._thread_id = None
        self._turn_id = None
        self._generation = None
        self._prompt = prompt
        self._outcome = None
        self._final_answer = None
        self._unphased = None
        self._messages = 0
        self._chars = 0
        self._max_chars = max_output_chars
        self._settled = False
        self._ignored = 0
        self._interrupted = False

    def _adopt_turn_id(self, turn_id: str | None) -> None:
        """Learn the turn id, from an authoritative source only.

        Exactly two things may name our turn: the `turn/start` reply, and a
        `turn/started` notification that already matched our thread and our
        generation. Nothing else, or a foreign `turn/completed` could introduce
        the very id it is then matched against.
        """
        if turn_id is not None and self._turn_id is None:
            self._turn_id = turn_id

    def _post(self, outcome: _Outcome) -> None:
        # `_settled` is set even if the future was already resolved: it is what
        # stops the buffer growing after the answer stopped mattering.
        self._settled = True
        future = self._outcome
        if future is not None and not future.done():
            future.set_result(outcome)

    async def _await_outcome(self, cid: str) -> _Outcome:
        """Wait for the turn to end, the deadline to pass, or the child to die.

        ``asyncio.wait`` rather than ``wait_for``: the latter cancels what it is
        waiting on, and this future is also written to from the read loop.
        """
        future = self._outcome
        assert future is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._turn_timeout
        self._require_current_generation(cid)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                await self._interrupt("timeout")
                event("appserver_turn", cid=cid, method=protocol.TURN_START, status="timeout")
                raise BridgeTimeout()
            await asyncio.wait({future}, timeout=min(remaining, LIVENESS_POLL_SECONDS))
            # Before the future is read, not after: a resolved outcome from a
            # child that has since been replaced is somebody else's turn.
            self._require_current_generation(cid)
            if future.done():
                return future.result()
            if not self._client.running:
                event(
                    "appserver_turn",
                    cid=cid,
                    method=protocol.TURN_START,
                    status="failed",
                    category="process_exited",
                )
                raise BridgeUnavailable("process_exited")

    def _stale(self) -> bool:
        """True once the child this thread was started in has been replaced.

        ``_ours`` compares an *event* against the generation captured at thread
        start; this compares that capture against the client's generation
        *now*. Both are needed. A delayed notification carrying our own
        generation number still passes the first check, and if the child it came
        from has since been replaced, the turn it describes died with that
        process -- so its answer is not an answer.
        """
        return self._generation is not None and self._client.generation != self._generation

    def _require_current_generation(self, cid: str) -> None:
        if not self._stale():
            return
        # No interrupt: the child that held this turn is gone, and the one
        # holding the generation now is a different process running somebody
        # else's work. `_interrupt` would refuse anyway; not calling it is the
        # statement of intent.
        event(
            "appserver_turn",
            cid=cid,
            status="failed",
            category="generation_changed",
        )
        raise BridgeUnavailable("generation_changed")

    async def _finish(
        self,
        outcome: _Outcome,
        output_schema: dict[str, Any],
        cid: str,
        started: float,
    ) -> TurnResult:
        duration_ms = (time.perf_counter() - started) * 1000

        if isinstance(outcome, _Unsafe):
            await self._interrupt("unsafe")
            event(
                "appserver_turn",
                cid=cid,
                status="failed",
                category=TurnUnsafe.category,
                duration_ms=duration_ms,
            )
            raise TurnUnsafe()

        if isinstance(outcome, _Overflow):
            await self._interrupt("output_too_large")
            event(
                "appserver_turn",
                cid=cid,
                status="failed",
                category="output_too_large",
                bytes=self._chars,
                count=self._messages,
                duration_ms=duration_ms,
            )
            raise BridgeProtocolError("output_too_large")

        if outcome.status == protocol.TURN_STATUS_FAILED:
            category = outcome.error_category or TurnRefused.category
            event(
                "appserver_turn", cid=cid, status="failed", category=category, duration_ms=duration_ms
            )
            raise TurnRefused(category)
        if outcome.status == protocol.TURN_STATUS_INTERRUPTED:
            event(
                "appserver_turn",
                cid=cid,
                status="failed",
                category="interrupted",
                duration_ms=duration_ms,
            )
            raise BridgeError("interrupted")
        if outcome.status != protocol.TURN_STATUS_COMPLETED:
            # `inProgress` on a completion notification, or a status this build
            # does not know. Neither is a result.
            raise BridgeProtocolError("invalid_turn_status")

        text = self._answer()
        if text is None:
            # No final answer. A turn that produced only commentary lands here,
            # which is the point: commentary is not a result even when it parses.
            event("appserver_turn", cid=cid, status="failed", category="no_final_message")
            raise BridgeProtocolError("no_final_message")

        try:
            payload = json.loads(text)
        except ValueError as exc:
            event("appserver_turn", cid=cid, status="failed", category="invalid_output")
            raise BridgeProtocolError("invalid_output") from exc
        if not isinstance(payload, dict) or not validate_against(payload, output_schema):
            event("appserver_turn", cid=cid, status="failed", category="invalid_output")
            raise BridgeProtocolError("invalid_output")

        # The last thing before a payload leaves this module: the answer has to
        # have come from a child that is still the current one.
        self._require_current_generation(cid)

        event(
            "appserver_turn",
            cid=cid,
            status="ok",
            count=self._messages,
            bytes=len(text),
            duration_ms=duration_ms,
        )
        return TurnResult(
            payload=payload,
            raw_chars=len(text),
            turn_id=self._turn_id or "",
            duration_ms=duration_ms,
        )

    def _answer(self) -> str | None:
        """The one message that may become the result.

        The rule, pinned:

        * the last ``agentMessage`` whose ``phase`` is ``final_answer`` wins;
        * if no message carried that phase, the last message whose ``phase`` was
          **absent or JSON null** is used instead. That is the single documented
          rule for an unknown phase, and it follows the schema's own note that
          providers do not emit ``phase`` consistently and that ``None`` means
          "phase unknown". Refusing those would refuse every legacy model;
        * ``commentary`` never wins, and neither does a ``phase`` value this
          build does not recognise. Both are dropped where they arrive.
        """
        return self._final_answer if self._final_answer is not None else self._unphased

    async def _interrupt(self, reason: str) -> None:
        """Stop the turn upstream. Best effort, bounded, never the reported failure.

        A turn that has already finished answers ``turn/interrupt`` with an
        error, and a dead process answers nothing. Neither is worth replacing
        the reason the caller is actually being told about.

        What this must never do is *start a process*. ``client.call`` spawns a
        child when there is not one, so a turn whose generation died or was
        replaced would otherwise resurrect the bridge in order to interrupt a
        turn that no longer exists anywhere.
        """
        thread_id, turn_id = self._thread_id, self._turn_id
        if thread_id is None or turn_id is None or self._interrupted:
            return
        self._interrupted = True
        if not self._client.running or self._client.generation != self._generation:
            event("appserver_turn_interrupt", category="generation_gone", status=reason)
            return
        try:
            await self._client.call(
                protocol.TURN_INTERRUPT,
                protocol.interrupt_params(thread_id, turn_id),
                timeout=INTERRUPT_GRACE_SECONDS,
            )
        except BridgeError as exc:
            event("appserver_turn_interrupt", category=exc.category, status=reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            event("appserver_turn_interrupt", category="failed", status=reason)
        else:
            event("appserver_turn_interrupt", status=reason)

    async def _interrupt_detached(self, reason: str) -> None:
        """Interrupt from inside a cancellation, without being cancelled again.

        The interrupt runs as its own task so a second cancellation lands on the
        wait rather than on the write. It is not left dangling either way: if the
        grace passes, the task is cancelled before this returns.
        """
        task = asyncio.get_running_loop().create_task(self._interrupt(reason))
        try:
            await asyncio.wait({task}, timeout=INTERRUPT_GRACE_SECONDS)
        except asyncio.CancelledError:
            pass
        if not task.done():
            task.cancel()

    # --- the read-loop handler -------------------------------------------

    def _observe(self, generation: int, method: str, params: Any) -> None:
        """Runs on the client's read loop: synchronous, cheap, and silent.

        Anything raised here is caught by the client and logged as a category,
        so this must not be where a failure is decided. It records, and posts an
        outcome for ``run`` to act on.
        """
        if method in UNEXPECTED_TOOL_EVENTS:
            # Server *requests*: approvals, client-tool calls, MCP elicitations.
            # The legacy approvals carry `conversationId` and no thread id at
            # all, so an unattributed one is treated as ours -- failing two
            # turns closed is the cheaper mistake.
            if self._ours(generation, params, match_turn=False, allow_unattributed=True):
                self._unsafe(method, "tool_request")
            return

        if method == protocol.TURN_STARTED:
            # The only notification allowed to name our turn, because it is the
            # only one that arrives before the turn has a name.
            if self._ours(generation, params, match_turn=False):
                self._adopt_turn_id(_turn_id_of(params))
            else:
                self._ignore(method)
            return

        # Item events are screened on thread and generation only. An unsafe item
        # on our thread is unsafe whoever it is billed to -- our thread runs one
        # turn at a time -- and requiring a turn id that `turn/started` has not
        # supplied yet would let a `commandExecution` arriving early be dropped
        # as unattributable instead of failing the turn. Being *read* still
        # takes all three, below.
        if not self._ours(generation, params, match_turn=method not in _ITEM_METHODS):
            self._ignore(method)
            return

        if method in _ITEM_METHODS:
            item = params.get("item")
            kind = protocol.item_type(item)
            if kind in protocol.INERT_ITEM_TYPES:
                # The lifecycle handing our own prompt back as a `userMessage`.
                # It may appear; it may never be output. Correlated against what
                # we sent, or it is a "user" message we did not write.
                if not self._is_our_own_input(item):
                    self._unsafe(method, "uncorrelated_input_echo")
                elif method == protocol.ITEM_COMPLETED and self._ours(generation, params):
                    self._retain_echo()
                return
            if kind not in protocol.SAFE_ITEM_TYPES:
                # A command execution, a file change, an MCP tool call, a web
                # search, or a variant that did not exist when this was written.
                self._unsafe(method, "unsafe_item")
                return
            if method == protocol.ITEM_COMPLETED and self._ours(generation, params):
                self._collect(params)
        elif method == protocol.TURN_COMPLETED:
            self._post(
                _Completed(
                    status=protocol.turn_status(params) or "",
                    error_category=_error_category(_turn_error_of(params)),
                )
            )
        elif method == protocol.SERVER_ERROR:
            if isinstance(params, dict) and params.get("willRetry") is True:
                return
            self._post(
                _Completed(
                    status=protocol.TURN_STATUS_FAILED,
                    error_category=_error_category(
                        params.get("error") if isinstance(params, dict) else None
                    ),
                )
            )

    def _ours(
        self,
        generation: int,
        params: Any,
        *,
        match_turn: bool = True,
        allow_unattributed: bool = False,
    ) -> bool:
        """Thread, turn and generation, all three.

        The generation is checked first and is never waived: an event from a
        replaced child concerns a turn that died with it, and accepting one
        would let a rapid restart make a dead turn look alive.
        """
        if self._thread_id is None or self._generation is None:
            return False
        if generation != self._generation or self._stale():
            return False
        if not isinstance(params, dict):
            return allow_unattributed
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str):
            return allow_unattributed
        if thread_id != self._thread_id:
            return False
        if not match_turn:
            return True
        if self._turn_id is None:
            # Our thread, but nothing has named our turn yet, so this event
            # cannot be attributed to it. Dropped rather than guessed at.
            return False
        seen = _event_turn_id(params)
        return isinstance(seen, str) and seen == self._turn_id

    def _ignore(self, method: str) -> None:
        self._ignored += 1
        event(
            "appserver_turn_event_ignored",
            method=safe_method(method),
            category="not_ours",
            count=self._ignored,
        )

    def _unsafe(self, method: str, category: str) -> None:
        event(
            "appserver_turn_unsafe_event",
            method=safe_method(method),
            category=category,
        )
        self._post(_Unsafe())

    def _is_our_own_input(self, item: Any) -> bool:
        """Is this `userMessage` the prompt this turn sent, and nothing else?

        Bounded first: ``user_message_texts`` refuses an echo whose parts total
        more than the prompt does before it assembles any of them, so a
        server-authored "user" message cannot cost memory on its way to being
        rejected. Correlated second: the parts have to *be* the prompt. An echo
        that is merely prompt-shaped, or one carrying an image, a skill or a
        mention part, is not something this bridge can have sent.
        """
        if self._prompt is None:
            return False
        parts = protocol.user_message_texts(item, max_chars=len(self._prompt))
        return parts is not None and "".join(parts) == self._prompt

    def _retain_echo(self) -> None:
        """Charge the echo to the budget without keeping a character of it.

        The text is not stored anywhere, which is what makes "it can never
        become the answer" structural rather than a rule someone has to
        remember. What is kept is its cost, so a stream of echoes overflows the
        turn exactly as a stream of agent messages would.
        """
        if self._settled or self._prompt is None:
            return
        self._messages += 1
        if self._messages > MAX_AGENT_MESSAGES:
            self._post(_Overflow())
            return
        self._chars += len(self._prompt)
        if self._chars > self._max_chars:
            self._post(_Overflow())

    def _collect(self, params: dict[str, Any]) -> None:
        # Nothing is retained once an outcome has been decided: a failed or
        # overflowing turn must stop accumulating, not keep buffering until the
        # deadline.
        if self._settled:
            return
        item = params.get("item")
        text = protocol.agent_message_text(item)
        if text is None:
            return

        # Counted before the phase is read, so commentary is bounded too.
        self._messages += 1
        if self._messages > MAX_AGENT_MESSAGES:
            self._post(_Overflow())
            return

        phase = protocol.agent_message_phase(item)
        if phase == protocol.MESSAGE_PHASE_FINAL_ANSWER:
            self._final_answer = text
        elif phase is None:
            self._unphased = text
        else:
            # `commentary`, or a MessagePhase this build does not know. Never an
            # answer, and not retained, so a preamble cannot become the result.
            event("appserver_turn_message_dropped", category="non_final_phase")
            return

        self._chars += len(text)
        if self._chars > self._max_chars:
            self._post(_Overflow())


def _thread_id_of(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    thread = response.get("thread")
    if not isinstance(thread, dict):
        return None
    value = thread.get("id")
    return value if isinstance(value, str) else None


def _turn_id_of(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    turn = payload.get("turn")
    if not isinstance(turn, dict):
        return None
    value = turn.get("id")
    return value if isinstance(value, str) else None


def _event_turn_id(params: dict[str, Any]) -> str | None:
    """The turn a notification names, in either of the two spellings.

    Item and error notifications carry a flat ``turnId``; the turn lifecycle
    notifications carry the ``Turn`` object and its ``id``.
    """
    flat = params.get("turnId")
    if isinstance(flat, str):
        return flat
    return _turn_id_of(params)


def _turn_error_of(params: Any) -> Any:
    if not isinstance(params, dict):
        return None
    turn = params.get("turn")
    return turn.get("error") if isinstance(turn, dict) else None


def _error_category(error: Any) -> str | None:
    """Read only `codexErrorInfo`. `message` is never touched.

    The enum has object variants (`httpConnectionFailed` and friends) as well as
    string ones; only the strings carry something worth telling the owner.
    """
    if not isinstance(error, dict):
        return None
    info = error.get("codexErrorInfo")
    if isinstance(info, str):
        return ERROR_CATEGORIES.get(info)
    return None


# --- the validator -----------------------------------------------------------

# The complete set of JSON Schema keywords ``validate_against`` actually
# enforces, plus the two annotations that constrain nothing. Small and explicit
# on purpose: this is the list ``assert_enforceable`` checks a schema against,
# so adding a name here without implementing it is the exact bug that check
# exists to prevent.
SUPPORTED_KEYWORDS = frozenset(
    {
        "type",
        "enum",
        "required",
        "properties",
        "items",
        "additionalProperties",
        "maxLength",
        "minItems",
        "maxItems",
        # Annotations. They describe; they do not constrain.
        "title",
        "description",
    }
)

SUPPORTED_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)


def assert_enforceable(schema: Any) -> None:
    """Reject an ``outputSchema`` whose constraints would not be checked.

    ``validate_against`` ignores keywords it does not implement, which is the
    right behaviour for a validator and the wrong one for a *contract*: a
    schema carrying ``pattern``, ``minimum``, ``oneOf`` or ``$ref`` would be
    sent to the model as though it were binding and then read back as though it
    had been verified, when in fact nothing checked it. So the schema is walked
    before the turn is sent, and anything outside the implemented subset raises
    here -- loudly, locally, and before a model is asked to honour it.

    Raises ``UnenforceableSchema``, whose category is safe to log.
    """
    if not isinstance(schema, dict):
        raise UnenforceableSchema("schema_not_an_object")

    for keyword in schema:
        if keyword not in SUPPORTED_KEYWORDS:
            raise UnenforceableSchema("unsupported_keyword")

    if "type" in schema:
        declared = schema["type"]
        # A list of types is a union, and this validator reads a single string.
        if not isinstance(declared, str) or declared not in SUPPORTED_TYPES:
            raise UnenforceableSchema("unsupported_type")

    if "enum" in schema:
        options = schema["enum"]
        if not isinstance(options, list) or not options:
            raise UnenforceableSchema("unsupported_enum")

    if "additionalProperties" in schema and schema["additionalProperties"] is not False:
        # `true` is permissive and `{...}` is a subschema this does not apply.
        # Only the spelling that is enforced is accepted.
        raise UnenforceableSchema("unsupported_additional_properties")

    if "required" in schema:
        names = schema["required"]
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise UnenforceableSchema("unsupported_required")

    for bound in ("maxLength", "minItems", "maxItems"):
        if bound in schema:
            value = schema[bound]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise UnenforceableSchema("unsupported_bound")

    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict):
            raise UnenforceableSchema("unsupported_properties")
        for subschema in properties.values():
            assert_enforceable(subschema)

    if "items" in schema:
        items = schema["items"]
        # The tuple form (`items: [...]`) is positional and is not implemented.
        if not isinstance(items, dict):
            raise UnenforceableSchema("unsupported_items")
        assert_enforceable(items)


def validate_against(value: Any, schema: Any) -> bool:
    """Check a parsed payload against the same schema the turn was given.

    A deliberately small subset of JSON Schema: ``type`` (object, array, string,
    integer, number, boolean, null), ``required``, ``properties``, ``items``,
    ``enum``, ``additionalProperties: false``, ``maxLength``, ``minItems`` and
    ``maxItems``. That is exactly what a grading rubric needs -- AGENTS.md asks
    for enums, maximum lengths, required fields and ``additionalProperties:
    false`` -- and stopping there keeps a validator inside a trust boundary
    readable in one sitting instead of pulling in a dependency to interpret the
    other forty keywords.

    A keyword this does not implement is *ignored*, so a schema is never made
    stricter by accident. What stops that being a silent hole is
    ``assert_enforceable``, which refuses to send such a schema in the first
    place. ``True``/``False`` are not integers here, whatever Python thinks.
    """
    if not isinstance(schema, dict):
        return True

    if "enum" in schema:
        options = schema["enum"]
        if not isinstance(options, list) or not any(_same(value, option) for option in options):
            return False

    declared = schema.get("type")
    if isinstance(declared, str) and not _is_type(value, declared):
        return False

    if isinstance(value, dict):
        return _validate_object(value, schema)
    if isinstance(value, list):
        return _validate_array(value, schema)
    if isinstance(value, str):
        maximum = schema.get("maxLength")
        if isinstance(maximum, int) and len(value) > maximum:
            return False
    return True


def _validate_object(value: dict[str, Any], schema: dict[str, Any]) -> bool:
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}

    required = schema.get("required")
    if isinstance(required, list):
        for name in required:
            if name not in value:
                return False

    if schema.get("additionalProperties") is False:
        for name in value:
            if name not in properties:
                return False

    for name, subschema in properties.items():
        if name in value and not validate_against(value[name], subschema):
            return False
    return True


def _validate_array(value: list[Any], schema: dict[str, Any]) -> bool:
    minimum = schema.get("minItems")
    if isinstance(minimum, int) and len(value) < minimum:
        return False
    maximum = schema.get("maxItems")
    if isinstance(maximum, int) and len(value) > maximum:
        return False
    items = schema.get("items")
    if isinstance(items, dict):
        return all(validate_against(item, items) for item in value)
    return True


def _is_type(value: Any, declared: str) -> bool:
    if declared == "object":
        return isinstance(value, dict)
    if declared == "array":
        return isinstance(value, list)
    if declared == "string":
        return isinstance(value, str)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "integer":
        # `isinstance(True, int)` is true in Python and false in JSON Schema.
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared == "null":
        return value is None
    # A type this subset does not implement constrains nothing. `assert_
    # enforceable` is what stops one being sent.
    return True


def _same(value: Any, option: Any) -> bool:
    """Enum membership without Python's ``True == 1``."""
    if isinstance(value, bool) != isinstance(option, bool):
        return False
    return value == option
