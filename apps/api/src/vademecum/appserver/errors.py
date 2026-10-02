"""What can go wrong at the App Server boundary, as categories.

Two rules shape this module:

1. **A category, never a message.** Every failure carries a short machine
   category (``process_exited``, ``timeout``, ``protocol``) that is safe to log
   and safe to map to interface copy. The App Server's own error text is
   discarded at the boundary: it is remote, unbounded, and can quote whatever
   was sent to it.
2. **Fail closed.** There is no error here that means "carry on with a
   different provider". Every one of them ends in an honest unavailable state.
"""

from __future__ import annotations


class BridgeError(RuntimeError):
    """A failure at the App Server boundary.

    ``category`` is the only part that may be logged or returned. It comes from
    a closed set defined by the raising code, never from the server.
    """

    category = "unavailable"

    def __init__(self, category: str | None = None) -> None:
        # The exception message is the category and nothing else, so an
        # accidental ``str(exc)`` in a log line cannot leak a payload.
        resolved = category or type(self).category
        super().__init__(resolved)
        self.category = resolved


class BridgeUnavailable(BridgeError):
    """The managed process could not be started, or is no longer answering."""

    category = "unavailable"


class BridgeTimeout(BridgeError):
    """A request was sent and no reply arrived within the deadline."""

    category = "timeout"


class BridgeProtocolError(BridgeError):
    """The server answered, but not with something this client can use."""

    category = "protocol"


class BridgeRefused(BridgeError):
    """The server refused the request.

    The App Server's JSON-RPC error code survives as ``code`` because it is an
    integer from a closed set. Its ``message`` does not survive at all.
    """

    category = "refused"

    def __init__(self, code: int, category: str | None = None) -> None:
        super().__init__(category)
        self.code = code


class LoginNotSupported(BridgeError):
    """A login shape Vademecum will not perform was requested or reported.

    Raised when anything but ChatGPT device-code login appears at this
    boundary. AGENTS.md forbids API-key billing outright, and forbids falling
    back to it silently; this is what "not silently" is made of.
    """

    category = "login_not_supported"


# Categories the account facade may retry once, after restarting the process.
# A timeout is absent on purpose: the request may have been received and acted
# on, and a login started twice is worse than a login reported as unavailable.
RETRYABLE_CATEGORIES = frozenset({"unavailable", "process_exited", "write_failed"})
