"""Error categories are not proof that a request was or was not dispatched."""

import pytest

from vademecum.appserver.errors import BridgeError
from vademecum.model import grading
from vademecum.storage.jobs import FAILURE_DETAIL


@pytest.mark.parametrize("category", [
    "signed_out", "timeout", "process_exited", "write_failed", "unavailable",
    "rate_limited", "protocol", "unexpected_remote_category",
])
def test_ambiguous_failures_never_promise_no_transmission(category):
    message = grading.unavailable_reason(BridgeError(category)).lower()
    assert "nothing was sent" not in message
    assert "did not send" not in message
    assert "had already been sent" not in message
    assert not grading.promised_no_transmission(category)
    assert "pile" not in message and "batch" not in message


def test_unknown_error_uses_safe_copy_without_raw_exception_text():
    error = BridgeError("unknown")
    error.args = ("sensitive upstream text",)
    message = grading.unavailable_reason(error)
    assert "sensitive upstream text" not in message
    assert "nothing was graded or recorded" in message


@pytest.mark.parametrize("category", ["signed_out", "timeout"])
def test_build_error_copy_does_not_infer_whether_earlier_stages_sent_content(category):
    message = FAILURE_DETAIL[category].lower()
    assert "no model action could run" not in message
    assert "had already been sent" not in message
    assert "may" in message
    assert "unchanged" in message or "no learning material" in message
