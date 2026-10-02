"""Redacted diagnostics for the App Server bridge.

AGENTS.md: diagnostics are event names, timings, status codes and generated
correlation ids. Not payloads, not tokens, not device or user codes, not an
account email, not a prompt, not an answer, not retrieved note content, and not
a line of the child's stderr.

The rule is enforced structurally rather than by care at each call site. This
module is the only way the bridge logs, it accepts a fixed set of field names,
and it accepts only scalars. Passing ``params=`` or ``message=`` to it is a
``ValueError`` in the test suite's own terms -- the field is not in the set, so
the value never reaches a formatter.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

logger = logging.getLogger("vademecum.appserver")

# The complete vocabulary of diagnostic fields. Adding one is a deliberate act
# and should be weighed against what it could carry.
ALLOWED_FIELDS = frozenset(
    {
        "cid",  # generated correlation id, ours
        "method",  # a protocol method name, shape-checked below
        "category",  # a closed-set failure category
        "code",  # a JSON-RPC error code (integer)
        "status",  # ok | failed | refused | ...
        "duration_ms",
        "generation",  # which managed process this concerned
        "exit",  # exit:N or signal:N
        "lines",  # counted, never contents
        "bytes",  # counted, never contents
        "count",
        "state",  # sanitised interface state name
        "pending",  # number of in-flight requests
        "attempt",
    }
)

# A method name is server-controlled text when it arrives inbound. Protocol
# names are short and drawn from a narrow alphabet, so anything else is
# replaced rather than logged.
SAFE_METHOD = re.compile(r"\A[A-Za-z0-9/_.:-]{1,64}\Z")


def new_correlation_id() -> str:
    """A local id, unrelated to any account, login or request identifier."""
    return uuid.uuid4().hex[:12]


def safe_method(method: Any) -> str:
    """A method name that is safe to log, or ``"unrecognised"``."""
    if isinstance(method, str) and SAFE_METHOD.match(method):
        return method
    return "unrecognised"


def _render(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "none"
    if isinstance(value, (int, str)):
        return str(value)
    # Unreachable through `event`, which rejects non-scalars; belt and braces
    # so a future caller cannot make this the leak.
    return type(value).__name__


def event(name: str, **fields: Any) -> None:
    """Log one structured event.

    Unknown field names are dropped rather than formatted, and a non-scalar
    value is dropped too. A diagnostic that silently loses a field is a much
    smaller problem than one that prints a payload.
    """
    parts = [name]
    for key in sorted(fields):
        if key not in ALLOWED_FIELDS:
            continue
        value = fields[key]
        if value is not None and not isinstance(value, (str, int, float, bool)):
            continue
        parts.append(f"{key}={_render(value)}")
    logger.info(" ".join(parts))
