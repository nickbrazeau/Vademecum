"""The bridge, checked against the schema the installed Codex CLI generated.

AGENTS.md treats the installed App Server schema as the source of truth, so
these tests read ``schemas/codex-app-server/`` rather than trusting the module
constants next to them. A Codex upgrade that moves the contract fails here --
which is the point. Refresh with ``scripts/refresh-codex-schemas.py`` and read
the diff before changing anything to match it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vademecum.appserver import protocol
from vademecum.appserver.client import SERVER_REQUEST_POLICY, Decline
from vademecum.config import find_repo_root

REPO_ROOT = find_repo_root()
assert REPO_ROOT is not None
SCHEMAS = REPO_ROOT / "schemas" / "codex-app-server"


def load(relative: str) -> dict | list:
    return json.loads((SCHEMAS / relative).read_text(encoding="utf-8"))


def definitions(document: dict) -> dict:
    return document.get("definitions", {})


def variant_titles(document: dict) -> dict[str, dict]:
    return {variant.get("title", ""): variant for variant in document.get("oneOf", [])}


class TestProvenance:
    def test_the_manifest_pins_a_cli_version_and_excludes_experimental(self) -> None:
        manifest = load("MANIFEST.json")
        assert manifest["codex_version"].startswith("codex-cli ")
        assert manifest["experimental"] is False
        assert manifest["generator"].startswith("codex app-server generate-json-schema")

    def test_every_committed_file_matches_its_recorded_digest(self) -> None:
        """A hand-edited fixture is a hand-invented contract."""
        import hashlib

        manifest = load("MANIFEST.json")
        recorded = {**manifest["verbatim_files"], **manifest["derived_files"]}
        for name, digest in recorded.items():
            actual = hashlib.sha256((SCHEMAS / name).read_bytes()).hexdigest()
            assert actual == digest, f"{name} does not match MANIFEST.json"

    def test_the_fixture_set_covers_what_this_slice_speaks(self) -> None:
        for required in (
            "v1/InitializeParams.json",
            "v1/InitializeResponse.json",
            "v2/GetAccountParams.json",
            "v2/GetAccountResponse.json",
            "v2/LoginAccountParams.json",
            "v2/LoginAccountResponse.json",
            "v2/CancelLoginAccountParams.json",
            "v2/CancelLoginAccountResponse.json",
            "v2/GetAccountRateLimitsResponse.json",
            "v2/AccountLoginCompletedNotification.json",
            "v2/AccountUpdatedNotification.json",
            "v2/AccountRateLimitsUpdatedNotification.json",
            "envelope/JSONRPCRequest.json",
            "envelope/JSONRPCResponse.json",
            "envelope/JSONRPCNotification.json",
            "envelope/JSONRPCError.json",
            "envelope/RequestId.json",
            "v2/ThreadStartParams.json",
            "v2/ThreadStartResponse.json",
            "v2/TurnStartParams.json",
            "v2/TurnStartResponse.json",
            "v2/TurnStartedNotification.json",
            "v2/TurnCompletedNotification.json",
            "v2/TurnInterruptParams.json",
            "v2/TurnInterruptResponse.json",
            "v2/ItemStartedNotification.json",
            "v2/ItemCompletedNotification.json",
            "v2/ErrorNotification.json",
            "v2/ThreadStartedNotification.json",
            "v2/ConfigReadParams.json",
            "v2/ConfigReadResponse.json",
        ):
            assert (SCHEMAS / required).is_file(), required


class TestEnvelope:
    @pytest.mark.parametrize(
        "name",
        ["JSONRPCRequest", "JSONRPCResponse", "JSONRPCNotification", "JSONRPCError"],
    )
    def test_no_envelope_carries_a_version_header(self, name: str) -> None:
        """The default stdio framing has no `jsonrpc` member, and neither do we."""
        document = load(f"envelope/{name}.json")
        assert "jsonrpc" not in document.get("properties", {})

    def test_a_request_requires_an_id_and_a_method(self) -> None:
        document = load("envelope/JSONRPCRequest.json")
        assert set(document["required"]) == {"id", "method"}

    def test_a_notification_requires_a_method_and_declares_no_id(self) -> None:
        document = load("envelope/JSONRPCNotification.json")
        assert document["required"] == ["method"]
        assert "id" not in document["properties"]

    def test_a_request_id_is_a_string_or_an_integer(self) -> None:
        document = load("envelope/RequestId.json")
        kinds = {branch.get("type") for branch in document["anyOf"]}
        assert kinds == {"string", "integer"}
        # Which is exactly the pair `id_key` is built to keep apart.
        assert protocol.id_key("1") != protocol.id_key(1)

    def test_a_success_reply_is_identified_by_the_result_member(self) -> None:
        document = load("envelope/JSONRPCResponse.json")
        assert set(document["required"]) == {"id", "result"}

    def test_initialized_is_the_client_notification(self) -> None:
        names = load("methods/client-notifications.json")
        assert names == [protocol.INITIALIZED]


class TestMethodNames:
    def test_every_method_the_bridge_sends_exists_upstream(self) -> None:
        upstream = set(load("methods/client-requests.json"))
        missing = protocol.CLIENT_REQUEST_METHODS - upstream
        assert missing == set(), missing

    def test_every_notification_the_bridge_listens_for_exists_upstream(self) -> None:
        upstream = set(load("methods/server-notifications.json"))
        missing = protocol.OBSERVED_NOTIFICATIONS - upstream
        assert missing == set(), missing

    def test_the_server_request_table_matches_the_schema_exactly(self) -> None:
        """Equality in both directions, on purpose.

        A method upstream that the table has not classified would fall into the
        generic refusal -- safe, but unread. A method in the table that upstream
        no longer has is dead code pretending to be a decision. Either way a
        person should look, so either way this fails.
        """
        upstream = set(load("methods/server-requests.json"))
        assert set(SERVER_REQUEST_POLICY) == upstream


class TestInitialize:
    def test_client_info_carries_the_fields_the_bridge_sends(self) -> None:
        document = load("v1/InitializeParams.json")
        client_info = definitions(document)["ClientInfo"]
        assert set(client_info["properties"]) >= {"name", "title", "version"}
        sent = protocol.initialize_params("Vademecum", "Vademecum", "0.1.0")["clientInfo"]
        assert set(sent) <= set(client_info["properties"])
        assert set(client_info["required"]) <= set(sent)

    def test_every_capability_the_schema_offers_is_declined_by_name(self) -> None:
        """Not omitted: a default that flips upstream must not opt this client in."""
        document = load("v1/InitializeParams.json")
        offered = set(definitions(document)["InitializeCapabilities"]["properties"])
        declared = protocol.initialize_params("Vademecum", "Vademecum", "0.1.0")["capabilities"]
        # `optOutNotificationMethods` is a list, not a capability to enable.
        toggles = offered - {"optOutNotificationMethods"}
        assert set(declared) == toggles
        # `extensions` is a table of MCP extension settings rather than a
        # toggle, so "none" is an empty one. Everything else is a boolean, and
        # every boolean is off.
        assert declared["extensions"] == {}
        assert all(value is False for name, value in declared.items() if name != "extensions")


class TestAccountContract:
    def test_reading_an_account_takes_a_refresh_token_flag(self) -> None:
        document = load("v2/GetAccountParams.json")
        assert "refreshToken" in document["properties"]
        assert protocol.account_read_params() == {"refreshToken": False}

    def test_the_chatgpt_account_variant_carries_the_plan_the_bridge_shows(self) -> None:
        document = load("v2/GetAccountResponse.json")
        chatgpt = variant_titles(definitions(document)["Account"])["ChatgptAccount"]
        assert set(chatgpt["properties"]) == {"email", "planType", "type"}
        # The email exists upstream. It has no field anywhere downstream.
        from vademecum.appserver.account import ModelStatus

        assert "email" not in ModelStatus.__dataclass_fields__

    def test_the_api_key_variant_exists_upstream_and_is_unreachable_here(self) -> None:
        document = load("v2/LoginAccountParams.json")
        titles = variant_titles(document)
        api_key = next(name for name in titles if name.startswith("ApiKey"))
        assert api_key, "the schema still offers API-key login"
        # And the only login the bridge can build is the device-code one.
        assert protocol.device_login_params()["type"] == protocol.LOGIN_TYPE_DEVICE_CODE

    def test_device_code_login_is_a_variant_with_the_fields_the_bridge_reads(self) -> None:
        params = variant_titles(load("v2/LoginAccountParams.json"))
        device = params["ChatgptDeviceCodev2::LoginAccountParams"]
        assert device["properties"]["type"]["enum"] == [protocol.LOGIN_TYPE_DEVICE_CODE]
        assert set(device["required"]) == {"type"}
        assert set(protocol.device_login_params()) == {"type"}

        responses = variant_titles(load("v2/LoginAccountResponse.json"))
        reply = responses["ChatgptDeviceCodev2::LoginAccountResponse"]
        assert set(reply["required"]) == {"loginId", "type", "userCode", "verificationUrl"}

    def test_cancelling_a_login_takes_the_login_id(self) -> None:
        document = load("v2/CancelLoginAccountParams.json")
        assert document["required"] == ["loginId"]
        assert set(protocol.cancel_login_params("x")) == {"loginId"}

    def test_the_cancel_statuses_are_the_ones_the_facade_reports(self) -> None:
        document = load("v2/CancelLoginAccountResponse.json")
        upstream = set(definitions(document)["CancelLoginAccountStatus"]["enum"])
        assert upstream == {"canceled", "notFound"}

    def test_the_rate_limit_fields_the_bridge_reads_exist_upstream(self) -> None:
        document = load("v2/GetAccountRateLimitsResponse.json")
        snapshot = definitions(document)["RateLimitSnapshot"]["properties"]
        assert {"primary", "secondary", "rateLimitReachedType", "spendControlReached"} <= set(
            snapshot
        )
        window = definitions(document)["RateLimitWindow"]["properties"]
        assert {"usedPercent", "resetsAt", "windowDurationMins"} <= set(window)

    def test_a_rolling_update_is_documented_as_sparse(self) -> None:
        """The merge behaviour is not an invention; the schema says so."""
        document = load("v2/AccountRateLimitsUpdatedNotification.json")
        assert "Sparse" in document["description"]
        assert "does not clear a previously observed value" in document["description"]

    def test_a_login_completed_notification_needs_only_success(self) -> None:
        document = load("v2/AccountLoginCompletedNotification.json")
        assert document["required"] == ["success"]
        # `error` is a string upstream and is never read into anything here.
        assert "error" in document["properties"]


class TestThreadAndTurnContract:
    """Every safety property of a grading turn, checked against the schema."""

    def test_the_thread_and_turn_methods_exist_upstream(self) -> None:
        upstream = set(load("methods/client-requests.json"))
        assert {
            protocol.THREAD_START,
            protocol.TURN_START,
            protocol.TURN_INTERRUPT,
            protocol.CONFIG_READ,
        } <= upstream
        # And each is spelled once, in CLIENT_REQUEST_METHODS, or `call` refuses.
        assert {
            protocol.THREAD_START,
            protocol.TURN_START,
            protocol.TURN_INTERRUPT,
            protocol.CONFIG_READ,
        } <= protocol.CLIENT_REQUEST_METHODS

    def test_the_thread_and_turn_notifications_exist_upstream(self) -> None:
        upstream = set(load("methods/server-notifications.json"))
        assert {
            protocol.THREAD_STARTED,
            protocol.TURN_STARTED,
            protocol.TURN_COMPLETED,
            protocol.ITEM_STARTED,
            protocol.ITEM_COMPLETED,
            protocol.SERVER_ERROR,
        } <= upstream
        assert protocol.OBSERVED_NOTIFICATIONS <= upstream

    def test_the_account_notifications_are_still_observed(self) -> None:
        """Adding the turn surface must not have dropped the account surface."""
        assert {
            protocol.ACCOUNT_UPDATED,
            protocol.ACCOUNT_RATE_LIMITS_UPDATED,
            protocol.ACCOUNT_LOGIN_COMPLETED,
        } <= protocol.OBSERVED_NOTIFICATIONS

    def test_every_key_a_grading_thread_sends_is_a_property_upstream(self) -> None:
        """No invented member -- `tools` and `tool_choice` in particular."""
        document = load("v2/ThreadStartParams.json")
        offered = set(document["properties"])
        sent = protocol.grading_thread_params("/tmp/x", base_instructions="b",
                                              developer_instructions="d", config={})
        assert set(sent) <= offered
        assert "tools" not in sent
        assert "tool_choice" not in sent

    def test_the_grading_thread_is_read_only_never_approving_and_ephemeral(self) -> None:
        document = load("v2/ThreadStartParams.json")
        sandbox_modes = set(definitions(document)["SandboxMode"]["enum"])
        assert protocol.SANDBOX_READ_ONLY in sandbox_modes
        approvals = definitions(document)["AskForApproval"]
        plain = next(branch for branch in approvals["oneOf"] if "enum" in branch)
        assert protocol.APPROVAL_NEVER in set(plain["enum"])

        sent = protocol.grading_thread_params("/tmp/x", base_instructions="b",
                                              developer_instructions="d", config={"k": 1})
        assert sent["sandbox"] == protocol.SANDBOX_READ_ONLY
        assert sent["approvalPolicy"] == protocol.APPROVAL_NEVER
        assert sent["ephemeral"] is True
        assert sent["cwd"] == "/tmp/x"
        assert sent["config"] == {"k": 1}

    def test_thread_config_is_a_free_form_object_upstream(self) -> None:
        """Which is what lets the hardening layer name MCP servers in it."""
        document = load("v2/ThreadStartParams.json")
        config = document["properties"]["config"]
        assert config["additionalProperties"] is True
        assert "object" in config["type"]

    def test_the_turn_sends_a_read_only_policy_with_network_access_off(self) -> None:
        document = load("v2/TurnStartParams.json")
        offered = set(document["properties"])
        sent = protocol.turn_params("t", "hello", {"type": "object"})
        assert set(sent) <= offered
        assert set(document["required"]) <= set(sent)

        policy = variant_titles(definitions(document)["SandboxPolicy"])["ReadOnlySandboxPolicy"]
        assert policy["properties"]["type"]["enum"] == [protocol.SANDBOX_POLICY_READ_ONLY]
        assert policy["properties"]["networkAccess"]["type"] == "boolean"
        assert sent["sandboxPolicy"] == {"type": protocol.SANDBOX_POLICY_READ_ONLY,
                                         "networkAccess": False}
        assert sent["approvalPolicy"] == protocol.APPROVAL_NEVER

    def test_the_turn_input_is_the_text_variant(self) -> None:
        document = load("v2/TurnStartParams.json")
        text = variant_titles(definitions(document)["UserInput"])["TextUserInput"]
        assert set(text["required"]) == {"text", "type"}
        assert text["properties"]["type"]["enum"] == ["text"]
        assert protocol.turn_params("t", "hello", {})["input"] == [
            {"type": "text", "text": "hello"}
        ]

    def test_the_output_schema_is_the_documented_constraint(self) -> None:
        document = load("v2/TurnStartParams.json")
        assert "constrain the final assistant message" in (
            document["properties"]["outputSchema"]["description"]
        )

    def test_interrupting_names_both_the_thread_and_the_turn(self) -> None:
        document = load("v2/TurnInterruptParams.json")
        assert set(document["required"]) == {"threadId", "turnId"}
        assert protocol.interrupt_params("t", "u") == {"threadId": "t", "turnId": "u"}

    def test_the_turn_statuses_are_the_ones_the_runner_branches_on(self) -> None:
        document = load("v2/TurnCompletedNotification.json")
        upstream = set(definitions(document)["TurnStatus"]["enum"])
        assert upstream == protocol.TURN_STATUSES
        for status in upstream:
            assert protocol.turn_status({"turn": {"status": status}}) == status
        # A status this build does not know is not read as one.
        assert protocol.turn_status({"turn": {"status": "somethingNew"}}) is None
        assert protocol.turn_status({}) is None

    def test_the_error_categories_map_values_that_exist_upstream(self) -> None:
        from vademecum.appserver.turns import ERROR_CATEGORIES

        document = load("v2/TurnCompletedNotification.json")
        info = definitions(document)["CodexErrorInfo"]
        plain = set(next(branch for branch in info["oneOf"] if "enum" in branch)["enum"])
        assert set(ERROR_CATEGORIES) <= plain
        assert set(ERROR_CATEGORIES.values()) == {"rate_limited", "signed_out", "context_exceeded"}

    def test_the_turn_error_message_is_not_something_the_bridge_reads(self) -> None:
        """It exists upstream, and no code path here has a use for it."""
        document = load("v2/TurnCompletedNotification.json")
        assert "message" in definitions(document)["TurnError"]["properties"]

        from vademecum.appserver import turns

        error = {"message": "the prompt was: secret", "codexErrorInfo": "unauthorized"}
        assert turns._error_category(error) == "signed_out"
        assert turns._error_category({"message": "secret"}) is None

    def test_the_agent_message_item_is_the_one_the_runner_reads(self) -> None:
        document = load("v2/ItemCompletedNotification.json")
        item = variant_titles(definitions(document)["ThreadItem"])["AgentMessageThreadItem"]
        assert item["properties"]["type"]["enum"] == [protocol.ITEM_AGENT_MESSAGE]
        assert {"id", "text", "type"} == set(item["required"])
        assert protocol.agent_message_text({"type": "agentMessage", "text": "x"}) == "x"
        assert protocol.agent_message_text({"type": "reasoning", "text": "x"}) is None

    def test_the_item_allowlist_is_a_subset_of_the_pinned_union(self) -> None:
        """Defect 1's fixed point: the allowlist is driven by the schema.

        Every name on it must still be a ThreadItem variant upstream, so a
        renamed variant fails here rather than becoming an allowlist entry that
        matches nothing -- and a Codex release that adds a variant leaves it
        outside the allowlist, which is the direction this must fail in.
        """
        document = load("v2/ItemCompletedNotification.json")
        upstream = {
            variant["properties"]["type"]["enum"][0]
            for variant in definitions(document)["ThreadItem"]["oneOf"]
        }
        assert protocol.SAFE_ITEM_TYPES <= upstream
        assert protocol.SAFE_ITEM_TYPES == {"agentMessage", "reasoning", "userMessage"}
        assert protocol.INERT_ITEM_TYPES <= upstream
        # Which leaves everything that can run, write, fetch or delegate outside
        # it. Named here so a rename upstream is noticed.
        for unsafe in (
            "commandExecution",
            "fileChange",
            "mcpToolCall",
            "dynamicToolCall",
            "webSearch",
            "imageView",
            "imageGeneration",
            "hookPrompt",
            "functionCallOutput",
            "subAgentActivity",
            "collabAgentToolCall",
            "sleep",
            "plan",
            "enteredReviewMode",
            "contextCompaction",
        ):
            assert unsafe in upstream, unsafe
            assert unsafe not in protocol.SAFE_ITEM_TYPES

    def test_the_input_echo_variant_is_the_name_and_shape_the_runner_allows(self) -> None:
        """Defect 9(a): the echo is a real lifecycle item, verified against the union.

        A normal turn emits one, so refusing `userMessage` outright failed every
        turn closed for the wrong reason. What makes allowing it safe is that
        the variant carries `content` -- the same `UserInput` array the turn
        sent -- and no text member a reader could mistake for model output.
        """
        document = load("v2/ItemCompletedNotification.json")
        item = variant_titles(definitions(document)["ThreadItem"])["UserMessageThreadItem"]
        assert item["properties"]["type"]["enum"] == [protocol.ITEM_USER_MESSAGE]
        assert protocol.ITEM_USER_MESSAGE in protocol.INERT_ITEM_TYPES
        assert set(item["required"]) == {"content", "id", "type"}
        # No `text` and no `phase`: nothing on it is reachable by the two
        # readers that produce an answer.
        assert "text" not in item["properties"]
        assert "phase" not in item["properties"]

        # And the only `UserInput` variant the echo may be made of is the one
        # `turn_params` sends.
        text = variant_titles(definitions(document)["UserInput"])["TextUserInput"]
        assert text["properties"]["type"]["enum"] == [protocol.USER_INPUT_TEXT]
        assert protocol.turn_params("t", "hi", {})["input"][0]["type"] == protocol.USER_INPUT_TEXT

    def test_the_model_provider_is_pinned_on_the_way_out_and_on_the_way_back(self) -> None:
        """Defect 9: boundary 4 is a property of the provider, not just the login."""
        params = load("v2/ThreadStartParams.json")
        assert "modelProvider" in params["properties"]
        sent = protocol.grading_thread_params(
            "/tmp/x", base_instructions="b", developer_instructions="d", config={}
        )
        assert sent["modelProvider"] == protocol.MODEL_PROVIDER_OPENAI == "openai"

        response = load("v2/ThreadStartResponse.json")
        # Required upstream, which is why an absent one is a contract change
        # rather than a default -- and is read as "not the provider we pinned".
        assert "modelProvider" in response["required"]
        assert response["properties"]["modelProvider"]["type"] == "string"
        assert protocol.thread_model_provider({"modelProvider": "openai"}) == "openai"
        assert protocol.thread_model_provider({"modelProvider": None}) is None
        assert protocol.thread_model_provider({}) is None
        assert protocol.thread_model_provider("openai") is None

    def test_the_account_types_the_preflight_branches_on_are_the_pinned_union(self) -> None:
        """Every Account variant upstream, and only one of them may run a turn."""
        document = load("v2/GetAccountResponse.json")
        variants = definitions(document)["Account"]["oneOf"]
        upstream = {variant["properties"]["type"]["enum"][0] for variant in variants}
        assert protocol.ACCOUNT_TYPE_CHATGPT in upstream
        assert protocol.ACCOUNT_TYPE_API_KEY in upstream
        # `account` is optional and nullable upstream: that is the shape of
        # "signed out", and it is not a shape a turn may run on.
        assert "account" not in document["required"]
        for kind in upstream:
            assert protocol.account_type({"account": {"type": kind}}) == kind
        # The two sentinels cannot collide with any of them.
        assert protocol.ACCOUNT_ABSENT not in upstream
        assert protocol.ACCOUNT_UNREADABLE not in upstream

    def test_both_item_notifications_carry_the_same_thread_item_union(self) -> None:
        """Which is why the allowlist is applied to `item/started` as well."""
        started = definitions(load("v2/ItemStartedNotification.json"))["ThreadItem"]
        completed = definitions(load("v2/ItemCompletedNotification.json"))["ThreadItem"]
        names = lambda union: {  # noqa: E731
            variant["properties"]["type"]["enum"][0] for variant in union["oneOf"]
        }
        assert names(started) == names(completed)

    def test_the_message_phase_values_are_the_ones_the_runner_branches_on(self) -> None:
        """Defect 2: only the terminal phase may become a result."""
        document = load("v2/ItemCompletedNotification.json")
        phase = definitions(document)["MessagePhase"]
        upstream = {branch["enum"][0] for branch in phase["oneOf"]}
        assert upstream == protocol.MESSAGE_PHASES
        assert protocol.MESSAGE_PHASE_FINAL_ANSWER == "final_answer"
        assert protocol.MESSAGE_PHASE_COMMENTARY == "commentary"
        # And the sentinel this build uses for anything else is not spellable
        # upstream, so it can never collide with a real value.
        assert protocol.MESSAGE_PHASE_UNRECOGNISED not in upstream

    def test_the_absent_phase_rule_follows_the_schemas_own_note(self) -> None:
        """`None` means "phase unknown" upstream, so it is read as legacy text.

        That is the single documented rule for an absent or null `phase`: the
        last such message is the answer when no `final_answer` ever arrived.
        """
        document = load("v2/ItemCompletedNotification.json")
        description = definitions(document)["MessagePhase"]["description"]
        assert "do not emit this consistently" in description
        assert "phase unknown" in description

    def test_the_agent_message_phase_is_optional_upstream(self) -> None:
        document = load("v2/ItemCompletedNotification.json")
        item = variant_titles(definitions(document)["ThreadItem"])["AgentMessageThreadItem"]
        assert "phase" in item["properties"]
        assert "phase" not in item["required"]

    def test_an_item_notification_names_its_thread_and_turn(self) -> None:
        """Which is what makes demultiplexing on them possible at all."""
        for name in ("ItemStartedNotification", "ItemCompletedNotification"):
            document = load(f"v2/{name}.json")
            assert {"threadId", "turnId"} <= set(document["required"])
        completed = load("v2/TurnCompletedNotification.json")
        assert "threadId" in completed["required"]

    def test_an_error_notification_carries_a_turn_error_and_a_retry_flag(self) -> None:
        document = load("v2/ErrorNotification.json")
        assert set(document["required"]) == {"error", "threadId", "turnId", "willRetry"}

    def test_config_read_returns_the_effective_config(self) -> None:
        params = load("v2/ConfigReadParams.json")
        assert set(protocol.config_read_params()) <= set(params["properties"])
        assert protocol.config_read_params() == {"includeLayers": False}
        response = load("v2/ConfigReadResponse.json")
        assert "config" in response["required"]

    def test_web_search_is_a_string_enum_and_not_a_boolean(self) -> None:
        """Why the startup flag is `web_search="disabled"` and not `=false`."""
        response = load("v2/ConfigReadResponse.json")
        modes = set(definitions(response)["WebSearchMode"]["enum"])
        assert "disabled" in modes
        assert True not in modes and False not in modes

        from vademecum.appserver import hardening

        assert 'web_search="disabled"' in hardening.STARTUP_CONFIG_FLAGS
        assert "web_search=false" not in hardening.STARTUP_CONFIG_FLAGS


class TestRefusalVocabulary:
    def test_the_decisions_the_bridge_sends_are_members_of_the_pinned_enums(self) -> None:
        current = definitions(load("server-requests/CommandExecutionRequestApprovalResponse.json"))
        current_values = {
            branch["enum"][0]
            for branch in current["CommandExecutionApprovalDecision"]["oneOf"]
            if "enum" in branch
        }
        legacy = definitions(load("server-requests/ExecCommandApprovalResponse.json"))
        legacy_values = {
            branch["enum"][0]
            for branch in legacy["ReviewDecision"]["oneOf"]
            if "enum" in branch
        }

        assert SERVER_REQUEST_POLICY["item/commandExecution/requestApproval"] == Decline("cancel")
        assert "cancel" in current_values
        assert SERVER_REQUEST_POLICY["execCommandApproval"] == Decline("abort")
        assert "abort" in legacy_values
        # And neither of the two is an approval.
        assert "accept" not in {"cancel", "abort"}
        assert "approved" not in {"cancel", "abort"}

    def test_no_approval_is_answered_with_an_accepting_decision(self) -> None:
        for method, policy in SERVER_REQUEST_POLICY.items():
            if isinstance(policy, Decline):
                assert not policy.decision.startswith("accept"), method
                assert not policy.decision.startswith("approve"), method


def test_the_pinned_set_is_small_enough_to_read() -> None:
    """Reviewability is the reason this is a subset rather than the whole bundle.

    The bound moved once, when the thread/turn surface was pinned: those
    documents inline the whole ``ThreadItem`` union, so six of them account for
    most of what is here. It is still under a fifth of the 3.6 MB bundle, and
    still a set a person can list.
    """
    files = [path for path in SCHEMAS.rglob("*.json")]
    assert len(files) <= 45
    assert sum(path.stat().st_size for path in files) < 768 * 1024
