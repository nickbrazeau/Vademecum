# ADR 0005 — `sqlite3` from the standard library, with numbered SQL migrations

- Status: accepted
- Date: 2026-08-29
- Related: [0001](0001-rewrite-preserving-product-patterns.md), [0003](0003-data-directory-layout.md)

## Context

AGENTS.md calls for "SQLite with migrations" and for dependencies to be added only when they remove
meaningful risk or complexity. The obvious候 candidates are SQLAlchemy + Alembic, which is what the
archive used against Postgres.

The archive's own history argues against carrying that weight down to SQLite: `pip install -e` baked
absolute interpreter paths into console scripts, so `alembic` broke when the checkout moved and
`scripts/dev.sh` shipped a workaround for it. That is a large amount of machinery for a
single-writer, single-user, ~5-table database.

## Decision

Use `sqlite3` from the standard library, with a hand-rolled migration runner.

- Migrations are `.sql` files in `apps/api/src/vademecum/db/migrations/`, named
  `NNNN_slug.sql`, applied in numeric order inside one transaction each.
- `schema_migrations(version, name, checksum, applied_at)` records what ran. The checksum is a
  SHA-256 of the file; the runner **refuses to start** if an already-applied migration's bytes have
  changed, which is the failure this table exists to catch.
- Applying is forward-only. There is no `downgrade`. Recovery is "restore a backup", which is a
  workflow that exists and is tested.
- Every query is parameterised. No string-built SQL anywhere.
- Connections set `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`.
  `foreign_keys` is per-connection in SQLite and off by default — setting it once at open is the
  whole reason the connection factory exists.
- **Connections are never shared.** Migrations run once at startup on a connection that is closed
  again; what the app keeps is the database *path*. Each HTTP request opens its own connection and
  closes it however the request ends. A `sqlite3.Connection` carries the current transaction, and
  FastAPI runs these synchronous endpoints on a thread pool, so one process-wide connection would
  put two requests' transactions in the same place — one request's `BEGIN` sitting inside another's
  work. WAL and `busy_timeout` are what make the alternative cheap enough to be obvious.
- **A backup reads through its own connection.** `write_backup` takes a path, not a connection.
  `Connection.backup` reads through whichever connection it is given, so driving it from a
  connection that is mid-transaction would block on that caller's own transaction — which only the
  caller can end — and would otherwise copy rows it has not committed. Its own reader sees the last
  committed state under WAL, without blocking the writer or being blocked by it.

Storage access lives in `vademecum/storage/`, takes a connection, and returns domain objects. HTTP
handlers own no SQL. That seam is what lets a later slice add retrieval, or swap the engine, without
touching routes.

## Consequences

- Runtime dependencies are FastAPI, Uvicorn, Pydantic and `pydantic-settings`. Nothing for the
  database.
- Writing a migration means writing SQL, which for SQLite's limited `ALTER TABLE` is the honest
  interface anyway.
- No autogenerate. Schema drift is caught by `tests/test_migrations.py`, which applies every
  migration to an empty database and asserts the resulting table and column set.
- Concurrent *readers* are ordinary and are served without contention; concurrent writers are
  serialised by SQLite and wait out `busy_timeout`. A second writing process is still an assumption
  worth revisiting, not an accident.
