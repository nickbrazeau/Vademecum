"""One model turn's runner, scoped to the thing it is for (a page, a session, an episode).

A turn factory that knows scopes (host mode, where the assistant in the conversation is
the model) gets the kind and id so the turn can be matched up; any other factory just
makes a runner.
"""

from __future__ import annotations

from typing import Any


def runner_for(turn_factory: Any, kind: str, scope_id: str) -> Any:
    scoped = getattr(turn_factory, "scoped", None)
    if callable(scoped):
        return scoped(kind, scope_id)()
    return turn_factory()
