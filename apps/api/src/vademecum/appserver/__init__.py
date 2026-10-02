"""The Codex App Server bridge.

Four layers, deliberately separable (ADR 0006):

``protocol``     the wire contract as pure functions; no I/O
``transport``    one managed child process; no protocol knowledge
``hardening``    the tool and MCP lockdown, at startup and per thread
``client``       correlation, lifecycle, supervision, refusals, event fan-out
``turns``        one schema-constrained turn, run to a validated payload
``account``      the sanitised account facade the HTTP layer talks to

Nothing above ``account`` and ``turns`` sees an App Server payload, and nothing
in here reaches into storage. Tests substitute a scripted transport, so the
whole stack runs without a child process and without a model call.
"""

from .account import DeviceLogin, ModelBridge, ModelStatus, RateLimits, UsageWindow
from .errors import (
    BridgeError,
    BridgeProtocolError,
    BridgeRefused,
    BridgeTimeout,
    BridgeUnavailable,
    LoginNotSupported,
)
from .turns import (
    CredentialUnusable,
    ProviderUnsupported,
    TurnRefused,
    TurnResult,
    TurnRunner,
    TurnUnsafe,
    UnenforceableSchema,
    assert_enforceable,
    validate_against,
)

__all__ = [
    "BridgeError",
    "BridgeProtocolError",
    "BridgeRefused",
    "BridgeTimeout",
    "BridgeUnavailable",
    "CredentialUnusable",
    "DeviceLogin",
    "LoginNotSupported",
    "ModelBridge",
    "ModelStatus",
    "ProviderUnsupported",
    "RateLimits",
    "TurnRefused",
    "TurnResult",
    "TurnRunner",
    "TurnUnsafe",
    "UnenforceableSchema",
    "UsageWindow",
    "assert_enforceable",
    "validate_against",
]
