"""Configuration and data-directory resolution (ADR 0002, ADR 0003).

Two rules are enforced here rather than in prose:

1. The server binds ``127.0.0.1`` and nothing else. AGENTS.md states this as a
   non-negotiable boundary, so there is no opt-out to configure -- not even for
   another loopback address.
2. The runtime data directory is never inside the source tree.

Both failures are refusals to start, not warnings.
"""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTNAMES = frozenset({"localhost", "localhost.localdomain"})

# The only address Vademecum will bind. AGENTS.md, "Private network only".
BIND_HOST = "127.0.0.1"

# Sub-directories created eagerly at startup so a missing one is found at boot
# rather than at the moment the owner asks for a backup (ADR 0003).
#
# ``attachments/sources`` holds uploaded originals, named by their own digest.
# ``model-workspace`` is the isolated, permanently empty directory that grading
# and synthesis threads are given as their working directory -- see
# ``MODEL_WORKSPACE_DIRNAME`` below for why it is not the data root.
DATA_SUBDIRECTORIES = (
    "attachments",
    "attachments/sources",
    "attachments/images",
    "attachments/schematics",
    "attachments/reports",
    "exports",
    "backups",
    "logs",
    "model-workspace",
)

DATABASE_FILENAME = "vademecum.sqlite3"

# Where uploaded originals live, relative to the data directory.
SOURCE_FILES_DIRNAME = "attachments/sources"

# The working directory handed to every model thread.
#
# It is a dedicated empty directory, not the data root, and the difference is
# not cosmetic. The App Server resolves project context -- AGENTS.md and other
# instruction files -- from its thread's cwd, and it can read files inside it
# under a read-only sandbox. Pointing a grading thread at the data directory
# would put the database, the exports and every uploaded original inside the
# one directory the model is allowed to look at. This one is created empty and
# nothing in Vademecum ever writes into it.
#
# Verified against the installed CLI rather than assumed: `thread/start` with
# this cwd reports `instructionSources: []`, and the same call with the
# checkout as cwd reports this repository's AGENTS.md.
MODEL_WORKSPACE_DIRNAME = "model-workspace"

# A checkout is identified by AGENTS.md sitting alongside apps/.
REPO_MARKERS = ("AGENTS.md", "apps")

# Where the ChatGPT desktop app keeps the Codex CLI on macOS. Tried first
# because that is the installation an owner already has when they have a
# ChatGPT plan -- which is the only credential Vademecum uses (ADR 0006).
CHATGPT_APP_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")

# Candidates, in order, when VADEMECUM_CODEX_PATH is unset.
# The ChatGPT app has moved its copy once already; both places are tried.
CHATGPT_APP_CODEX_CLI = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")

CODEX_CANDIDATES = (
    CHATGPT_APP_CODEX_CLI,
    CHATGPT_APP_CODEX,
    Path("/usr/local/bin/codex"),
    Path("/opt/homebrew/bin/codex"),
    Path.home() / ".local" / "bin" / "codex",
)


def default_codex_path() -> Path:
    """The first Codex CLI that exists, or the app's copy as the thing to name.

    Returning a path that may not exist is deliberate: the bridge reports
    ``codex_not_found`` as an interface state, which is more useful than a
    configuration error at startup for a component that is optional until the
    owner opens the Model page.
    """
    for candidate in CODEX_CANDIDATES:
        if candidate.is_file():
            return candidate
    return CHATGPT_APP_CODEX


class ConfigError(RuntimeError):
    """Configuration that would violate a documented boundary."""


def is_loopback(host: str) -> bool:
    """True when *host* can only be reached from this machine."""
    candidate = host.strip().strip("[]").lower()
    if not candidate:
        return False
    if candidate in LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for the checkout root, or None."""
    here = (start or Path(__file__)).resolve()
    for directory in (here, *here.parents):
        if all((directory / marker).exists() for marker in REPO_MARKERS):
            return directory
    return None


def default_data_dir() -> Path:
    """The platform-conventional data directory (ADR 0003)."""
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Vademecum"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "vademecum"


class Settings(BaseSettings):
    """Runtime settings, all overridable through ``VADEMECUM_*`` variables."""

    model_config = SettingsConfigDict(env_prefix="VADEMECUM_", extra="ignore")

    host: str = BIND_HOST
    port: int = Field(default=8765, ge=1, le=65535)
    # The development web app's port. Not bound by this process: it is here so
    # the same-origin guard knows which local origin the browser will present.
    web_port: int = Field(default=5173, ge=1, le=65535)
    data_dir: Path | None = None

    # --- Codex App Server bridge (ADR 0006) ---
    # No enable/disable switch: the bridge starts lazily on first use and
    # reports an honest unavailable state when Codex is absent, so a flag would
    # only add a second way to be off.
    codex_path: Path | None = None
    # The Claude Code CLI, for `claude` mode (ADR 0019): the owner's own
    # Claude sign-in, made in a terminal; this process holds no key.
    claude_path: Path | None = None
    appserver_request_timeout: float = Field(default=30.0, gt=0, le=300)
    appserver_startup_timeout: float = Field(default=20.0, gt=0, le=300)
    # A model turn is a whole reasoning pass, not a status read, so it gets its
    # own budget. Bounded on both sides: a turn that is allowed to run forever
    # is a turn nobody can cancel.
    appserver_turn_timeout: float = Field(default=180.0, gt=0, le=900)

    # --- the folder (ADR 0012) ---
    # Where a learner puts files: one visible folder, one subfolder per pile.
    # Scanned at start, every `sources_scan_interval` seconds, and on request.
    # Single tenancy only; the hosted mode takes files through the web app.
    sources_dir: Path | None = None
    sources_folder_enabled: bool = True
    sources_scan_interval: float = Field(default=20.0, ge=2, le=3600)

    # --- started by the MCP server (ADR 0012) ---
    # The pid of the process that started this one, when there is one. The
    # API watches it and stops itself when it goes, so no assistant restart
    # leaves an API running yesterday's code on the port.
    parent_pid: int | None = None

    # --- a second Vademecum to sync with (ADR 0015) ---
    # `domi` (at home: the Mac) keeps the files and does the reading; `foris`
    # (abroad: the always-awake copy) is what a phone reaches while the Mac
    # sleeps. Only domi initiates. `sync_peer_url` and `sync_token` are domi's
    # view of foris; `sync_accept_token` is what foris requires of domi. All
    # empty means no sync at all. (harbour/sea and home/away are older spellings.)
    sync_role: Literal["domi", "foris", "harbour", "sea", "home", "away"] = "domi"

    @property
    def sync_role_name(self) -> Literal["domi", "foris"]:
        """The role in the current vocabulary, whichever spelling was set."""
        return "foris" if self.sync_role in ("foris", "sea", "away") else "domi"
    sync_peer_url: str = ""
    sync_token: str = ""
    sync_accept_token: str = ""
    sync_interval: float = Field(default=300.0, ge=10, le=86400)
    # `lean` (the default, for foris): records only, cited passages only, no
    # files. `full`: everything, for a second machine that should hold it all.
    sync_scope: Literal["full", "lean"] = "lean"

    # --- how many learners (ADR 0010) ---
    # `single`: the owner's Mac, one workspace, no identity. `multi`: the
    # hosted product, a workspace per learner under `learners/`, every request
    # identified by the token the MCP server issued. Multi requires host mode:
    # there is no Codex for anyone but the owner, and no key for anyone at all.
    tenancy: Literal["single", "multi"] = "single"

    # --- who does the model work (ADR 0009) ---
    # `codex`: the owner's Mac deployment, through the local Codex child.
    # `host`: the learner's ChatGPT, through pending/submit tools; this process
    # never calls a model. There is no third value and no key-based one.
    model_provider: Literal["codex", "host", "claude"] = "codex"
    # How long a pending host turn may wait for ChatGPT before the run fails.
    host_turn_ttl: float = Field(default=1800.0, ge=60, le=86400)

    # --- public-literature watch (ADR 0007) ---
    # The provider is fixed in code, not configured: an "endpoint" setting is a
    # way to make the allowlist meaningless. What is configurable is whether the
    # watch runs at all, how often, and how hard it tries.
    literature_enabled: bool = True
    literature_request_timeout: float = Field(default=20.0, gt=0, le=120)
    literature_interval_hours: float = Field(default=168.0, ge=1, le=8760)
    literature_max_results: int = Field(default=25, ge=1, le=100)
    # NCBI asks that unattended clients identify themselves and, optionally,
    # supply a contact address. Empty by default: nothing about the owner is
    # sent unless they choose to.
    literature_contact_email: str = ""
    # NCBI's courtesy key for E-utilities: a public database's rate-limit
    # identifier (ten requests a second instead of three), shared by every
    # workspace in this process. Not a model credential; the only key this
    # product may hold (AGENTS.md). Empty by default.
    literature_ncbi_key: str = ""

    # --- the Case Series hub (ADR 0022) ---
    # Fixed public requests to three publishers of teaching cases, on a timer
    # the owner switches on in the dashboard. Off here means no hub at all.
    cases_enabled: bool = True
    cases_interval_hours: float = Field(default=6.0, ge=1, le=168)
    cases_max_results: int = Field(default=60, ge=10, le=100)

    def resolve_claude_path(self) -> Path:
        from .model.claude_cli import default_claude_path

        chosen = self.claude_path or default_claude_path()
        return Path(os.path.abspath(chosen.expanduser()))

    def resolve_codex_path(self) -> Path:
        chosen = self.codex_path or default_codex_path()
        return Path(os.path.abspath(chosen.expanduser()))

    def resolve_data_dir(self) -> Path:
        """Absolute data directory, refusing anything inside the source tree."""
        chosen = (self.data_dir or default_data_dir()).expanduser()
        # resolve() without strict=True: the directory may not exist yet.
        resolved = Path(os.path.abspath(chosen))
        repo_root = find_repo_root()
        if repo_root is not None and (
            resolved == repo_root or repo_root in resolved.parents
        ):
            raise ConfigError(
                "VADEMECUM_DATA_DIR points inside the source tree "
                f"({resolved}). Runtime data must live outside the checkout; "
                "set VADEMECUM_DATA_DIR to a directory elsewhere."
            )
        return resolved

    def check_bind(self) -> None:
        """Refuse any bind address other than ``127.0.0.1``.

        There is deliberately no override. AGENTS.md lists binding
        ``127.0.0.1`` among its non-negotiable boundaries, and a setting that
        can turn a boundary off is not a boundary. Reaching Vademecum from
        another device is the job of a private tunnel the owner sets up, which
        still arrives here over loopback. The comparison is exact: a host with
        surrounding whitespace is a typo, and a typo in a bind address is not
        something to guess at.
        """
        if self.host != BIND_HOST:
            raise ConfigError(
                f"VADEMECUM_HOST={self.host!r} is refused: Vademecum binds "
                f"{BIND_HOST} and nothing else, including other loopback "
                "addresses. There is no setting that changes this; reach it "
                "from another device through a private tunnel that forwards to "
                f"{BIND_HOST}."
            )

    def resolve_sources_dir(self) -> Path | None:
        """The learner's folder, or None when there is none to scan.

        Defaults to ``~/Documents/Vademecum``: somewhere a person sees in
        Finder, unlike the data directory under Library. Never inside the
        checkout, for the same reason the data directory is not.
        """
        if self.tenancy != "single" or not self.sources_folder_enabled:
            return None
        chosen = (self.sources_dir or Path.home() / "Documents" / "Vademecum").expanduser()
        resolved = Path(os.path.abspath(chosen))
        repo_root = find_repo_root()
        if repo_root is not None and (resolved == repo_root or repo_root in resolved.parents):
            raise ConfigError(
                f"VADEMECUM_SOURCES_DIR points inside the source tree ({resolved}). "
                "Choose a folder elsewhere."
            )
        return resolved

    def check_tenancy(self) -> None:
        """Multi-learner tenancy without host mode would mean the operator's
        Codex grading other people's answers. Refused at startup."""
        if self.tenancy == "multi" and self.model_provider != "host":
            raise ConfigError(
                "VADEMECUM_TENANCY=multi requires VADEMECUM_MODEL_PROVIDER=host: a "
                "hosted Vademecum runs no model of its own (ADR 0009, ADR 0010)."
            )

    def prepare(self) -> Path:
        """Validate the boundaries and create the data directory tree."""
        self.check_bind()
        self.check_tenancy()
        root = self.resolve_data_dir()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in DATA_SUBDIRECTORIES:
            (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        return root

    @property
    def database_path(self) -> Path:
        return self.resolve_data_dir() / DATABASE_FILENAME

    @property
    def source_files_dir(self) -> Path:
        return self.resolve_data_dir() / SOURCE_FILES_DIRNAME

    @property
    def model_workspace_dir(self) -> Path:
        return self.resolve_data_dir() / MODEL_WORKSPACE_DIRNAME


# The one settings file of the local product (ADR 0012): what the installer
# records, such as where the source folder is. Read by the API and by the MCP
# server, so the two agree; a variable in the environment still wins.
SETTINGS_FILE = Path.home() / "Library" / "Application Support" / "Vademecum" / "settings.env"


def settings_file_path() -> Path:
    override = os.environ.get("VADEMECUM_SETTINGS_FILE")
    return Path(override).expanduser() if override else SETTINGS_FILE


def write_setting(name: str, value: str, *, path: Path | None = None) -> Path:
    """Record one ``VADEMECUM_*`` setting in the settings file, keeping the rest."""
    target = path or settings_file_path()
    key = name if name.startswith("VADEMECUM_") else f"VADEMECUM_{name.upper()}"
    lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    kept = [line for line in lines if not line.strip().startswith(f"{key}=")]
    quoted = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    kept.append(f"{key}={quoted}")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return target


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    path = settings_file_path()
    if path.is_file():
        return Settings(_env_file=str(path), _env_file_encoding="utf-8")  # type: ignore[call-arg]
    return Settings()
