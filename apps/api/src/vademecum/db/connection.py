"""SQLite connection factories (ADR 0005).

``PRAGMA foreign_keys`` is per-connection in SQLite and off by default. Setting
it once at open is the whole reason this module exists.

Connections are cheap and are never shared. Each HTTP request opens one and
closes it; a backup opens its own reader. A ``sqlite3.Connection`` carries
transaction state, so one shared across request threads would interleave two
owners' transactions into one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import count
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    """Open *path* with the pragmas every Vademecum connection needs."""
    connection = sqlite3.connect(
        str(path),
        # Manual transaction control; see transaction() below. This also keeps
        # executescript() predictable for the migration runner.
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def connect_reader(path: str | Path) -> sqlite3.Connection:
    """A connection that only ever reads committed data.

    Two differences from :func:`connect` matter here. It sets no journal mode:
    the mode is a property of the database file, and a would-be reader that
    tries to change it can collide with a writer that is mid-transaction. And
    it is a *separate* connection, so reading through it sees the last
    committed state rather than a caller's open transaction.
    """
    connection = sqlite3.connect(str(path), isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


# Savepoint names are generated rather than derived from a nesting depth:
# sqlite3.Connection does not accept attributes, and a name that is unique per
# invocation needs no per-connection bookkeeping to stay correct.
_savepoints = count()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in one transaction, rolling back on any exception.

    Nests: an inner ``transaction()`` inside an outer one becomes a SAVEPOINT.
    That lets a caller wrap several storage helpers -- each of which opens its
    own transaction -- so the whole group commits or rolls back together, which
    is what makes a build's points, questions, evidence and coverage one unit
    instead of a sequence a later exception can leave half-applied.
    """
    if connection.in_transaction:
        name = f"vdm_sp_{next(_savepoints)}"
        connection.execute(f"SAVEPOINT {name}")
        try:
            yield connection
        except BaseException:
            connection.execute(f"ROLLBACK TO {name}")
            connection.execute(f"RELEASE {name}")
            raise
        connection.execute(f"RELEASE {name}")
        return

    connection.execute("BEGIN")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
