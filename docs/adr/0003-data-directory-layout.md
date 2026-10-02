# ADR 0003 — Data-directory layout

- Status: accepted
- Date: 2026-08-29
- Related: [0002](0002-local-first-boundary.md), [0005](0005-sqlite-and-hand-rolled-migrations.md)

## Context

The canonical runtime data must live on the Mac, outside source control, in one configurable place
that is simple to back up and simple to delete. The archive scattered runtime state — a
`notebook-media/` directory inside `apps/api/`, git-ignored, and separately required to be included
in backups. Anything git-ignored but living inside the checkout is one `git clean -xdf` away from
being gone.

## Decision

**One directory, outside the source tree, chosen by configuration.**

`VADEMECUM_DATA_DIR` selects it. When unset, the default is platform-conventional:

| Platform | Default |
| --- | --- |
| macOS | `~/Library/Application Support/Vademecum` |
| Other | `$XDG_DATA_HOME/vademecum`, else `~/.local/share/vademecum` |

Layout:

```text
<data dir>/
  vademecum.sqlite3      canonical database (WAL mode; -wal and -shm siblings)
  attachments/           file bodies addressed by id; no user-supplied filenames
  exports/               human-readable JSON snapshots, one file per export
  backups/               consistent SQLite copies, one file per backup
  logs/                  reserved; redacted diagnostics only, no free text
```

**Startup refuses a data directory inside the source tree.** `resolve_data_dir()` walks up from the
package looking for a repository marker (`AGENTS.md` alongside `apps/`) and raises `ConfigError` if
the resolved data directory is that root or beneath it. The error names the variable to change. This
is checked before the database is opened, so a misconfiguration cannot create files it then has to
be trusted to clean up.

**Directories are created eagerly at `0o700`,** together, on startup — so a missing `backups/` is
found at boot rather than at the moment the owner asks for a backup.

**Everything the owner would miss is under that one root.** Backups copy the database; the JSON
export is a full logical dump of every table. A future attachment store goes in `attachments/`,
inside the same root, and is therefore inside the same backup story by construction.

## Consequences

- "Back up Vademecum" means "copy one directory". "Start over" means "delete one directory".
- The checkout is disposable: deleting it loses no data.
- `.gitignore` still excludes `*.sqlite3`, `data/`, `.env` and the export/backup directory names, as
  defence in depth against a future default or a manual override that points inside the tree.
- Tests point `VADEMECUM_DATA_DIR` at a `tmp_path`, which satisfies the outside-the-tree rule
  naturally and gives every test an empty database.
