"""Where connections and accounts are recorded: a small SQLite file of its own.

Separate from the study database on purpose. Tokens and client records are
not study data; they belong in no export and no backup bundle, and revoking
every connection must not touch a single learning record.

What is stored, and what is not:

* Registered clients, as the SDK handed them over. A client secret, when one
  was minted, is kept as issued: the SDK compares it on every token request,
  so it cannot be a hash. The file is created ``0600``.
* Authorization codes and tokens are stored as SHA-256 digests. The plaintext
  is handed to the client once and never seen here again. A token issued in
  multi tenancy carries the learner it belongs to; the API resolves a token
  to a learner by reading this same table (ADR 0010).
* Passphrases as scrypt records (``passphrase.py``): the owner's in single
  tenancy, one per learner in multi.
* Invites, as digests, single use, with an expiry. An invite is how a learner
  comes to exist; there is no open registration.
* Failed sign-in attempts, per subject, so a guessing loop meets a lockout.

Everything here is synchronous SQLite behind one lock. Each call is a handful
of indexed rows; a thread pool would be more machinery than the work.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from . import passphrase as pp

PENDING_TTL_SECONDS = 600
CODE_TTL_SECONDS = 300
INVITE_TTL_SECONDS = 7 * 86400

# Five wrong passphrases in fifteen minutes lock a subject out for the rest
# of that window. The subject is the owner in single tenancy and a handle in
# multi, so one learner's mistakes lock nobody else out.
LOCKOUT_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 900

MAX_CLIENTS = 50
OWNER_SUBJECT = "owner"

# A handle is typed by a person and shown back to them; it is never a path.
HANDLE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id  TEXT PRIMARY KEY,
    info       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_authorizations (
    id         TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    params     TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS authorization_codes (
    code_hash  TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    params     TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    learner_id TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    client_id  TEXT NOT NULL,
    family_id  TEXT NOT NULL,
    scopes     TEXT NOT NULL,
    resource   TEXT,
    expires_at INTEGER NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    learner_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_family ON tokens (family_id);
CREATE INDEX IF NOT EXISTS idx_tokens_learner ON tokens (learner_id);
CREATE TABLE IF NOT EXISTS consent_attempts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL NOT NULL,
    succeeded INTEGER NOT NULL,
    subject   TEXT NOT NULL DEFAULT 'owner'
);
CREATE TABLE IF NOT EXISTS learners (
    id           TEXT PRIMARY KEY,
    handle       TEXT NOT NULL UNIQUE,
    passphrase   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_sign_in TEXT,
    disabled     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invites (
    code_hash  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used_by    TEXT,
    used_at    TEXT
);
"""

# Columns added after the first release. ``CREATE TABLE IF NOT EXISTS`` does
# not add a column to a table that already exists, so these are checked.
ADDED_COLUMNS = (
    ("tokens", "learner_id", "TEXT"),
    ("authorization_codes", "learner_id", "TEXT"),
    ("consent_attempts", "subject", "TEXT NOT NULL DEFAULT 'owner'"),
)


@dataclass(frozen=True)
class TokenRecord:
    kind: str
    client_id: str
    family_id: str
    scopes: list[str]
    resource: str | None
    expires_at: int
    revoked: bool
    learner_id: str | None


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    family_id: str
    expires_in: int


@dataclass(frozen=True)
class Pending:
    client_id: str
    params: AuthorizationParams


@dataclass(frozen=True)
class LearnerRecord:
    id: str
    handle: str
    created_at: str
    last_sign_in: str | None
    disabled: bool


class InviteError(ValueError):
    """An invite or a registration that cannot be honoured; ``message`` is shown."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AccessStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existed = path.exists()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        for table, column, declaration in ADDED_COLUMNS:
            present = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in present:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        self._conn.commit()
        if not existed:
            path.chmod(0o600)
        self.path = path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- the owner's passphrase (single tenancy) -------------------------

    def set_passphrase(self, passphrase: str) -> None:
        record = pp.hash_passphrase(passphrase)
        with self._lock:
            self._conn.execute(
                "INSERT INTO app_state (key, value) VALUES ('passphrase', ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (record,),
            )
            self._conn.commit()

    def has_passphrase(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM app_state WHERE key = 'passphrase'"
            ).fetchone()
        return row is not None

    def verify_passphrase(self, passphrase: str) -> bool:
        """Check the owner's passphrase and record the attempt either way."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_state WHERE key = 'passphrase'"
            ).fetchone()
        ok = row is not None and pp.verify_passphrase(passphrase, row["value"])
        self._record_attempt(OWNER_SUBJECT, ok)
        return ok

    # --- lockout, per subject ------------------------------------------------

    def lockout_remaining(self, subject: str = OWNER_SUBJECT) -> int:
        """Seconds until the consent page accepts another attempt, or 0."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT at FROM consent_attempts WHERE succeeded = 0 AND subject = ? AND at > ?"
                " ORDER BY at DESC LIMIT ?",
                (subject, time.time() - LOCKOUT_WINDOW_SECONDS, LOCKOUT_ATTEMPTS),
            ).fetchall()
        if len(rows) < LOCKOUT_ATTEMPTS:
            return 0
        oldest = rows[-1]["at"]
        return max(0, int(oldest + LOCKOUT_WINDOW_SECONDS - time.time()))

    def _record_attempt(self, subject: str, ok: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO consent_attempts (at, succeeded, subject) VALUES (?, ?, ?)",
                (time.time(), 1 if ok else 0, subject),
            )
            self._conn.execute(
                "DELETE FROM consent_attempts WHERE at < ?",
                (time.time() - LOCKOUT_WINDOW_SECONDS,),
            )
            self._conn.commit()

    # --- learners (multi tenancy) --------------------------------------------

    def create_invite(self, *, ttl_seconds: int = INVITE_TTL_SECONDS) -> str:
        """A one-time code, shown once. Only its digest is kept."""
        code = "-".join(secrets.token_hex(2) for _ in range(4))
        with self._lock:
            self._conn.execute(
                "INSERT INTO invites (code_hash, created_at, expires_at) VALUES (?, ?, ?)",
                (_hash(code), _now_iso(), time.time() + ttl_seconds),
            )
            self._conn.execute(
                "DELETE FROM invites WHERE used_by IS NULL AND expires_at < ?", (time.time(),)
            )
            self._conn.commit()
        return code

    def redeem_invite(self, *, code: str, handle: str, passphrase: str) -> str:
        """Turn a live invite into a learner. Returns the new learner id."""
        handle = handle.strip().lower()
        if not HANDLE.match(handle):
            raise InviteError(
                "Choose a handle of 3 to 32 characters: lowercase letters, digits, "
                "dots, dashes or underscores, starting with a letter or digit."
            )
        try:
            record = pp.hash_passphrase(passphrase)
        except pp.WeakPassphrase as exc:
            raise InviteError(str(exc)) from None
        learner_id = "lrn_" + secrets.token_hex(8)
        with self._lock:
            row = self._conn.execute(
                "SELECT used_by, expires_at FROM invites WHERE code_hash = ?",
                (_hash(code.strip().lower()),),
            ).fetchone()
            if row is None or row["used_by"] is not None or row["expires_at"] < time.time():
                raise InviteError("That invite code is not valid, has been used, or has expired.")
            taken = self._conn.execute(
                "SELECT 1 FROM learners WHERE handle = ?", (handle,)
            ).fetchone()
            if taken is not None:
                raise InviteError("That handle is already taken. Choose another.")
            # Registering is a sign-in: the learner proved the invite and chose
            # the passphrase they will sign in with from now on.
            self._conn.execute(
                "INSERT INTO learners (id, handle, passphrase, created_at, last_sign_in)"
                " VALUES (?, ?, ?, ?, ?)",
                (learner_id, handle, record, _now_iso(), _now_iso()),
            )
            self._conn.execute(
                "UPDATE invites SET used_by = ?, used_at = ? WHERE code_hash = ?",
                (learner_id, _now_iso(), _hash(code.strip().lower())),
            )
            self._conn.commit()
        return learner_id

    def authenticate(self, *, handle: str, passphrase: str) -> str | None:
        """The learner id for a handle and passphrase, or None. Records the attempt."""
        handle = handle.strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, passphrase, disabled FROM learners WHERE handle = ?", (handle,)
            ).fetchone()
        ok = (
            row is not None
            and not row["disabled"]
            and pp.verify_passphrase(passphrase, row["passphrase"])
        )
        self._record_attempt(f"handle:{handle}", ok)
        if not ok:
            return None
        with self._lock:
            self._conn.execute(
                "UPDATE learners SET last_sign_in = ? WHERE id = ?", (_now_iso(), row["id"])
            )
            self._conn.commit()
        return str(row["id"])

    def list_learners(self) -> list[LearnerRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, handle, created_at, last_sign_in, disabled FROM learners ORDER BY created_at"
            ).fetchall()
        return [
            LearnerRecord(row["id"], row["handle"], row["created_at"], row["last_sign_in"], bool(row["disabled"]))
            for row in rows
        ]

    def reset_passphrase(self, handle: str, passphrase: str) -> int:
        """Replace a learner's passphrase and revoke every token they hold.

        An operator's act, from the Mac, for a learner who has lost theirs.
        Their workspace is untouched; every assistant and desk session has to
        sign in again with the new passphrase.
        """
        handle = handle.strip().lower()
        try:
            record = pp.hash_passphrase(passphrase)
        except pp.WeakPassphrase as exc:
            raise InviteError(str(exc)) from None
        with self._lock:
            row = self._conn.execute("SELECT id FROM learners WHERE handle = ?", (handle,)).fetchone()
            if row is None:
                raise InviteError("No learner has that handle.")
            self._conn.execute(
                "UPDATE learners SET passphrase = ?, disabled = 0 WHERE id = ?", (record, row["id"])
            )
            cursor = self._conn.execute(
                "UPDATE tokens SET revoked = 1 WHERE learner_id = ? AND revoked = 0", (row["id"],)
            )
            self._conn.execute("DELETE FROM consent_attempts WHERE subject = ?", (f"handle:{handle}",))
            self._conn.commit()
            return int(cursor.rowcount)

    def disable_learner(self, handle: str) -> int:
        """Stop a learner signing in and revoke every token they hold.

        Their workspace is not touched: deletion is the learner's act, from
        the API, with the confirmation phrase.
        """
        handle = handle.strip().lower()
        with self._lock:
            row = self._conn.execute("SELECT id FROM learners WHERE handle = ?", (handle,)).fetchone()
            if row is None:
                raise InviteError("No learner has that handle.")
            self._conn.execute("UPDATE learners SET disabled = 1 WHERE id = ?", (row["id"],))
            cursor = self._conn.execute(
                "UPDATE tokens SET revoked = 1 WHERE learner_id = ? AND revoked = 0", (row["id"],)
            )
            self._conn.commit()
            return int(cursor.rowcount)

    def revoke_all_for(self, learner_id: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE tokens SET revoked = 1 WHERE learner_id = ? AND revoked = 0", (learner_id,)
            )
            self._conn.commit()
            return int(cursor.rowcount)

    # --- clients --------------------------------------------------------

    def save_client(self, info: OAuthClientInformationFull) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO oauth_clients (client_id, info, created_at)"
                " VALUES (?, ?, ?)",
                (info.client_id, info.model_dump_json(exclude_none=True), _now_iso()),
            )
            self._conn.commit()

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT info FROM oauth_clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row["info"])

    def count_clients(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0])

    def client_names(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT info FROM oauth_clients ORDER BY created_at").fetchall()
        names = []
        for row in rows:
            info = json.loads(row["info"])
            names.append(str(info.get("client_name") or "(unnamed client)"))
        return names

    # --- pending consent ------------------------------------------------

    def create_pending(self, client_id: str, params: AuthorizationParams) -> str:
        pending_id = secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute(
                "INSERT INTO pending_authorizations (id, client_id, params, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (pending_id, client_id, params.model_dump_json(), time.time() + PENDING_TTL_SECONDS),
            )
            self._conn.execute(
                "DELETE FROM pending_authorizations WHERE expires_at < ?", (time.time(),)
            )
            self._conn.commit()
        return pending_id

    def get_pending(self, pending_id: str) -> Pending | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT client_id, params FROM pending_authorizations"
                " WHERE id = ? AND consumed = 0 AND expires_at > ?",
                (pending_id, time.time()),
            ).fetchone()
        if row is None:
            return None
        return Pending(row["client_id"], AuthorizationParams.model_validate_json(row["params"]))

    def consume_pending(self, pending_id: str) -> Pending | None:
        """Take the pending request out of play, returning it once."""
        with self._lock:
            row = self._conn.execute(
                "SELECT client_id, params FROM pending_authorizations"
                " WHERE id = ? AND consumed = 0 AND expires_at > ?",
                (pending_id, time.time()),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE pending_authorizations SET consumed = 1 WHERE id = ?", (pending_id,)
            )
            self._conn.commit()
        return Pending(row["client_id"], AuthorizationParams.model_validate_json(row["params"]))

    # --- authorization codes --------------------------------------------

    def create_code(
        self, client_id: str, params: AuthorizationParams, *, learner_id: str | None = None
    ) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute(
                "INSERT INTO authorization_codes (code_hash, client_id, params, expires_at, learner_id)"
                " VALUES (?, ?, ?, ?, ?)",
                (_hash(code), client_id, params.model_dump_json(), time.time() + CODE_TTL_SECONDS, learner_id),
            )
            self._conn.execute(
                "DELETE FROM authorization_codes WHERE expires_at < ?", (time.time() - 3600,)
            )
            self._conn.commit()
        return code

    def load_code(self, code: str) -> AuthorizationCode | None:
        """The code's record, used or not: the exchange decides what to do."""
        with self._lock:
            row = self._conn.execute(
                "SELECT client_id, params, expires_at, used, learner_id FROM authorization_codes"
                " WHERE code_hash = ?",
                (_hash(code),),
            ).fetchone()
        if row is None:
            return None
        params = AuthorizationParams.model_validate_json(row["params"])
        return AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=float(row["expires_at"]) if not row["used"] else 0.0,
            client_id=row["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=row["learner_id"],
        )

    def mark_code_used(self, code: str) -> bool:
        """True the first time; False for a replay."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE authorization_codes SET used = 1 WHERE code_hash = ? AND used = 0",
                (_hash(code),),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    # --- tokens ---------------------------------------------------------

    def issue_tokens(
        self,
        *,
        client_id: str,
        scopes: list[str],
        resource: str | None,
        access_ttl: int,
        refresh_ttl: int,
        family_id: str | None = None,
        learner_id: str | None = None,
        with_refresh: bool = True,
    ) -> IssuedTokens:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        family = family_id or secrets.token_urlsafe(16)
        now = int(time.time())
        rows = [
            (_hash(access), "access", client_id, family, " ".join(scopes), resource, now + access_ttl, _now_iso(), learner_id),
        ]
        if with_refresh:
            rows.append(
                (_hash(refresh), "refresh", client_id, family, " ".join(scopes), resource, now + refresh_ttl, _now_iso(), learner_id)
            )
        else:
            refresh = ""
        with self._lock:
            self._conn.executemany(
                "INSERT INTO tokens (token_hash, kind, client_id, family_id, scopes, resource,"
                " expires_at, created_at, learner_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.execute("DELETE FROM tokens WHERE expires_at < ?", (now - 86400,))
            self._conn.commit()
        return IssuedTokens(access, refresh, family, access_ttl)

    def load_token(self, token: str, kind: str, *, include_revoked: bool = False) -> TokenRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT kind, client_id, family_id, scopes, resource, expires_at, revoked, learner_id"
                " FROM tokens WHERE token_hash = ? AND kind = ?",
                (_hash(token), kind),
            ).fetchone()
        if row is None:
            return None
        if row["revoked"] and not include_revoked:
            return None
        if row["expires_at"] < int(time.time()):
            return None
        return TokenRecord(
            kind=row["kind"],
            client_id=row["client_id"],
            family_id=row["family_id"],
            scopes=row["scopes"].split(),
            resource=row["resource"],
            expires_at=int(row["expires_at"]),
            revoked=bool(row["revoked"]),
            learner_id=row["learner_id"],
        )

    def revoke_token(self, token: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tokens SET revoked = 1 WHERE token_hash = ?", (_hash(token),)
            )
            self._conn.commit()

    def revoke_family(self, family_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE tokens SET revoked = 1 WHERE family_id = ?", (family_id,))
            self._conn.commit()

    def revoke_all(self) -> dict[str, int]:
        """Every token gone, every client forgotten. Passphrases and learners stay."""
        with self._lock:
            tokens = self._conn.execute("UPDATE tokens SET revoked = 1 WHERE revoked = 0").rowcount
            clients = self._conn.execute("DELETE FROM oauth_clients").rowcount
            self._conn.execute("DELETE FROM pending_authorizations")
            self._conn.execute("DELETE FROM authorization_codes")
            self._conn.commit()
        return {"tokens_revoked": int(tokens), "clients_removed": int(clients)}

    def summary(self) -> dict[str, object]:
        now = int(time.time())
        with self._lock:
            active = self._conn.execute(
                "SELECT COUNT(*) FROM tokens WHERE kind = 'refresh' AND revoked = 0 AND expires_at > ?",
                (now,),
            ).fetchone()[0]
            learners = self._conn.execute("SELECT COUNT(*) FROM learners WHERE disabled = 0").fetchone()[0]
            invites = self._conn.execute(
                "SELECT COUNT(*) FROM invites WHERE used_by IS NULL AND expires_at > ?", (time.time(),)
            ).fetchone()[0]
        return {
            "passphrase_set": self.has_passphrase(),
            "clients": self.client_names(),
            "active_connections": int(active),
            "learners": int(learners),
            "open_invites": int(invites),
        }
