"""The wire contract, exercised as pure functions over strings."""

from __future__ import annotations

import json

import pytest

from vademecum.appserver import protocol


class TestFraming:
    def test_a_message_is_one_line_and_carries_no_version_header(self) -> None:
        line = protocol.encode(protocol.request(1, protocol.ACCOUNT_READ, {"refreshToken": False}))
        assert line.endswith("\n")
        assert line.count("\n") == 1
        decoded = json.loads(line)
        # The default stdio transport is newline-delimited JSON. A `jsonrpc`
        # member is not part of it, and sending one would be inventing contract.
        assert "jsonrpc" not in decoded
        assert decoded == {"id": 1, "method": "account/read", "params": {"refreshToken": False}}

    def test_a_notification_has_no_id(self) -> None:
        assert protocol.notification(protocol.INITIALIZED) == {"method": "initialized"}

    def test_params_are_omitted_rather_than_sent_as_null(self) -> None:
        """`account/rateLimits/read` declares `params` as null and not required."""
        assert protocol.request(7, protocol.ACCOUNT_RATE_LIMITS_READ) == {
            "id": 7,
            "method": "account/rateLimits/read",
        }


class TestIdentifiers:
    def test_a_string_id_and_an_integer_id_are_different_requests(self) -> None:
        assert protocol.id_key("7") != protocol.id_key(7)

    def test_a_boolean_is_not_an_error_code(self) -> None:
        assert not protocol.is_error_code(True)
        assert not protocol.is_error_code(False)
        assert protocol.is_error_code(-32601)
        assert protocol.is_error_code(0)
        assert not protocol.is_error_code("1")
        assert not protocol.is_error_code(None)

    def test_a_boolean_is_not_a_request_id(self) -> None:
        # `isinstance(True, int)` is true in Python, so without this a `true`
        # id would be filed under the integer 1 and steal a real reply.
        assert not protocol.is_request_id(True)
        assert protocol.is_request_id(0)
        assert protocol.is_request_id("abc")
        assert not protocol.is_request_id(None)
        assert not protocol.is_request_id(1.5)


class TestDecoding:
    def test_a_result_is_matched_by_key_presence_not_truthiness(self) -> None:
        message = protocol.decode_line('{"id": 3, "result": null}')
        assert isinstance(message, protocol.Result)
        assert message.result is None

    def test_a_server_request_keeps_its_id_type(self) -> None:
        message = protocol.decode_line('{"id": "srv-1", "method": "item/tool/call", "params": {}}')
        assert isinstance(message, protocol.ServerRequest)
        assert message.id == "srv-1"

    def test_a_notification_has_no_id(self) -> None:
        message = protocol.decode_line('{"method": "account/updated", "params": {"planType": "pro"}}')
        assert isinstance(message, protocol.ServerNotification)
        assert message.params == {"planType": "pro"}

    def test_an_error_reply_keeps_the_code_and_drops_the_message(self) -> None:
        message = protocol.decode_line(
            '{"id": 4, "error": {"code": -32601, "message": "secret detail"}}'
        )
        assert isinstance(message, protocol.ErrorReply)
        assert message.code == -32601
        # There is no attribute holding the server's text, so no later code has
        # the option of logging it.
        assert not hasattr(message, "message")

    def test_an_unknown_top_level_member_is_ignored(self) -> None:
        message = protocol.decode_line('{"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}')
        assert isinstance(message, protocol.Result)

    @pytest.mark.parametrize(
        ("line", "reason"),
        [
            ("not json at all", "invalid_json"),
            ("[1, 2, 3]", "not_an_object"),
            ('{"method": 5, "id": 1}', "invalid_method"),
            ('{"method": "", "id": 1}', "invalid_method"),
            ('{"method": "x", "id": 1.5}', "invalid_id"),
            ('{"id": true, "result": 1}', "invalid_id"),
            ('{"result": 1}', "neither_request_nor_reply"),
            ('{"id": 1}', "reply_without_result_or_error"),
            ('{"id": 1, "error": "boom"}', "invalid_error"),
            ('{"id": 1, "error": {"message": "no code"}}', "invalid_error"),
            # `isinstance(True, int)` again: a boolean code would otherwise be
            # read as the code 1 and categorised as an ordinary refusal.
            ('{"id": 1, "error": {"code": true, "message": "boom"}}', "invalid_error"),
            ('{"id": 1, "error": {"code": false}}', "invalid_error"),
            ('{"id": 1, "error": {"code": "-32601"}}', "invalid_error"),
            ('{"id": 1, "error": {"code": 1.5}}', "invalid_error"),
        ],
    )
    def test_malformed_lines_carry_a_category_not_their_content(
        self, line: str, reason: str
    ) -> None:
        with pytest.raises(protocol.MalformedMessage) as caught:
            protocol.decode_line(line)
        assert caught.value.reason == reason


class TestInitializeParams:
    def test_the_client_identifies_itself_as_vademecum(self) -> None:
        params = protocol.initialize_params("Vademecum", "Vademecum", "0.1.0")
        assert params["clientInfo"] == {
            "name": "Vademecum",
            "title": "Vademecum",
            "version": "0.1.0",
        }

    def test_every_capability_is_explicitly_refused(self) -> None:
        """Stated, not omitted: a changed default must not opt this client in."""
        capabilities = protocol.initialize_params("Vademecum", "Vademecum", "0.1.0")["capabilities"]
        assert capabilities == {
            "experimentalApi": False,
            "mcpServerOpenaiFormElicitation": False,
            "requestAttestation": False,
            # A table of MCP extension settings rather than a toggle, so "none"
            # is an empty one rather than `False`.
            "extensions": {},
        }
        assert all(
            value is False for name, value in capabilities.items() if name != "extensions"
        )


class TestAccountParams:
    def test_reading_account_state_does_not_refresh_a_token(self) -> None:
        assert protocol.account_read_params() == {"refreshToken": False}

    def test_the_only_login_this_module_can_build_is_chatgpt_device_code(self) -> None:
        assert protocol.device_login_params() == {"type": "chatgptDeviceCode"}


class TestThreadItems:
    """The allowlist and the phase, as pure functions over dictionaries."""

    def test_the_item_type_is_read_from_the_discriminator(self) -> None:
        assert protocol.item_type({"type": "agentMessage"}) == "agentMessage"
        assert protocol.item_type({"type": "commandExecution"}) == "commandExecution"

    @pytest.mark.parametrize("item", [None, "text", {}, {"type": 7}, {"type": None}, []])
    def test_an_item_with_no_readable_type_is_not_a_safe_one(self, item: object) -> None:
        assert protocol.item_type(item) not in protocol.SAFE_ITEM_TYPES

    def test_the_allowlist_holds_exactly_the_three_harmless_variants(self) -> None:
        assert protocol.SAFE_ITEM_TYPES == {"agentMessage", "reasoning", "userMessage"}
        # And the third is inert: an echo of our own input, never model output.
        assert protocol.INERT_ITEM_TYPES == {"userMessage"}
        assert protocol.ITEM_AGENT_MESSAGE not in protocol.INERT_ITEM_TYPES

    def test_an_inert_item_carries_no_readable_agent_text(self) -> None:
        """Whatever a `userMessage` says, it is not an answer to anything."""
        echo = {"type": "userMessage", "content": [{"type": "text", "text": '{"ok": true}'}]}
        assert protocol.agent_message_text(echo) is None

    @pytest.mark.parametrize(
        "phase,expected",
        [
            ("final_answer", protocol.MESSAGE_PHASE_FINAL_ANSWER),
            ("commentary", protocol.MESSAGE_PHASE_COMMENTARY),
            (None, None),
            ("someNewPhase", protocol.MESSAGE_PHASE_UNRECOGNISED),
            (7, protocol.MESSAGE_PHASE_UNRECOGNISED),
            ({}, protocol.MESSAGE_PHASE_UNRECOGNISED),
        ],
    )
    def test_a_present_phase_is_read_or_reported_as_unrecognised(
        self, phase: object, expected: object
    ) -> None:
        item = {"type": "agentMessage", "text": "x", "phase": phase}
        assert protocol.agent_message_phase(item) == expected

    def test_an_absent_phase_is_the_same_answer_as_an_explicit_null(self) -> None:
        assert protocol.agent_message_phase({"type": "agentMessage", "text": "x"}) is None
        assert (
            protocol.agent_message_phase({"type": "agentMessage", "text": "x", "phase": None})
            is None
        )

    def test_the_unrecognised_sentinel_is_not_a_value_the_schema_can_carry(self) -> None:
        assert protocol.MESSAGE_PHASE_UNRECOGNISED not in protocol.MESSAGE_PHASES
        assert not protocol.MESSAGE_PHASE_UNRECOGNISED.isalnum()


class TestTheInputEcho:
    """`user_message_texts`: bounded and shape-checked, as a pure function."""

    def test_a_text_echo_within_the_bound_is_readable(self) -> None:
        item = {"type": "userMessage", "content": [{"type": "text", "text": "hello"}]}
        assert protocol.user_message_texts(item, max_chars=5) == ("hello",)

    def test_the_bound_is_the_total_of_every_part(self) -> None:
        item = {
            "type": "userMessage",
            "content": [{"type": "text", "text": "abc"}, {"type": "text", "text": "de"}],
        }
        assert protocol.user_message_texts(item, max_chars=5) == ("abc", "de")
        assert protocol.user_message_texts(item, max_chars=4) is None

    @pytest.mark.parametrize(
        "item",
        [
            None,
            "userMessage",
            {"type": "agentMessage", "text": "x"},
            {"type": "userMessage"},  # no content at all
            {"type": "userMessage", "content": []},  # empty
            {"type": "userMessage", "content": "hello"},  # not an array
            {"type": "userMessage", "content": [{"type": "image", "url": "x"}]},
            {"type": "userMessage", "content": [{"type": "skill", "name": "n", "path": "p"}]},
            {"type": "userMessage", "content": [{"type": "text", "text": 7}]},
            {"type": "userMessage", "content": ["hello"]},
        ],
    )
    def test_anything_this_bridge_could_not_have_sent_reads_as_nothing(
        self, item: object
    ) -> None:
        assert protocol.user_message_texts(item, max_chars=1000) is None
