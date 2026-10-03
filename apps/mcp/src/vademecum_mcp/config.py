"""Settings for the MCP server (ADR 0008).

Three boundaries are enforced here rather than described:

1. The server binds ``127.0.0.1`` and nothing else, exactly like the API.
   Reaching it from a phone is the job of a tunnel the owner runs deliberately.
2. It speaks to the API at a loopback address only; ``api_client.py`` refuses
   anything else.
3. In HTTP mode the public URL must be HTTPS. OAuth 2.1 requires it, and a
   bearer token over plain HTTP is a token on the wire.

The variables the API already reads (``VADEMECUM_PORT``, ``VADEMECUM_DATA_DIR``)
are read here under the same names, so the two processes agree on where the
API is and where the data lives by construction rather than by configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from vademecum.config import BIND_HOST, ConfigError
from vademecum.config import Settings as ApiSettings
from vademecum.config import is_loopback

ACCESS_DIRNAME = "mcp"
ACCESS_DB_FILENAME = "access.sqlite3"

# Where the MCP endpoint lives under the public URL, and the one scope every
# token carries. One scope, because there is one owner and one workspace.
MCP_PATH = "/mcp"
SCOPE = "vademecum"

CONSENT_PATH = "/consent"


class McpSettings(BaseSettings):
    """Runtime settings, all overridable through ``VADEMECUM_*`` variables."""

    model_config = SettingsConfigDict(env_prefix="VADEMECUM_", extra="ignore")

    # --- the API this server speaks to (same variables the API reads) ---
    host: str = BIND_HOST
    port: int = Field(default=8765, ge=1, le=65535)
    data_dir: Path | None = None
    # The source folder, as the API reads it (ADR 0012); this server only
    # tells the learner where it is.
    sources_dir: Path | None = None
    sources_folder_enabled: bool = True

    # `single`: one owner, approved by one passphrase. `multi`: learners with
    # accounts of their own, created by invitation (ADR 0010). The same
    # variable the API reads, so the two agree by construction.
    tenancy: Literal["single", "multi"] = "single"

    # --- this server ---
    mcp_port: int = Field(default=8766, ge=1, le=65535)
    # The HTTPS origin a tunnel presents to the world, e.g.
    # https://my-mac.example.ts.net. Required in HTTP mode: OAuth metadata has
    # to name absolute URLs, and taking them from the Host header would let a
    # request choose its own issuer.
    mcp_public_url: str = ""
    # A grading turn may take up to the API's turn timeout (180 s by default),
    # so the client's budget is larger than that, not smaller.
    mcp_api_timeout: float = Field(default=240.0, gt=0, le=900)
    mcp_access_token_ttl: int = Field(default=3600, ge=60, le=86400)
    mcp_refresh_token_ttl: int = Field(default=30 * 86400, ge=3600, le=365 * 86400)

    # --- the desk, through this gateway (ADR 0011; multi tenancy only) ---
    # The built web app to serve at the public root. Unset: the checkout's
    # apps/web/dist when it exists, else no desk.
    mcp_desk_dist: Path | None = None
    # The owner's passphrase, seeded at start when none is set yet: how foris
    # (ADR 0017) gets one without a terminal. Read once, never logged.
    mcp_passphrase: str = ""
    # Listen on every interface instead of loopback. Only for a container
    # whose sole network is the private one its Worker reaches it on (ADR
    # 0017); on a Mac this stays off and the gateway is loopback like the API.
    mcp_listen_all: bool = False
    mcp_desk_session_ttl: int = Field(default=14 * 86400, ge=600, le=90 * 86400)

    def resolve_desk_dist(self) -> Path | None:
        candidate = self.mcp_desk_dist
        if candidate is None:
            from vademecum.config import find_repo_root

            root = find_repo_root()
            candidate = None if root is None else root / "apps" / "web" / "dist"
        if candidate is None:
            return None
        candidate = Path(candidate).expanduser()
        return candidate if (candidate / "index.html").is_file() else None

    @classmethod
    def load(cls) -> "McpSettings":
        """Settings as the running product reads them: the settings file, then
        the environment on top (the same file the API reads, ADR 0012)."""
        from vademecum.config import settings_file_path

        path = settings_file_path()
        if path.is_file():
            return cls(_env_file=str(path), _env_file_encoding="utf-8")  # type: ignore[call-arg]
        return cls()

    def resolve_sources_dir(self) -> Path | None:
        """The learner's source folder, by the API's own rules."""
        return ApiSettings(
            data_dir=self.data_dir,
            sources_dir=self.sources_dir,
            sources_folder_enabled=self.sources_folder_enabled,
            tenancy=self.tenancy,
            model_provider="host",
        ).resolve_sources_dir()

    def check_bind(self) -> None:
        """Refuse any bind address other than ``127.0.0.1``. No override."""
        if self.host != BIND_HOST:
            raise ConfigError(
                f"VADEMECUM_HOST={self.host!r} is refused: the MCP server binds "
                f"{BIND_HOST} and nothing else. Reach it from another device "
                "through a tunnel that forwards to it."
            )

    @property
    def api_base_url(self) -> str:
        return f"http://{BIND_HOST}:{self.port}"

    def resolve_data_dir(self) -> Path:
        """The API's data directory, by the API's own rules."""
        return ApiSettings(data_dir=self.data_dir).resolve_data_dir()

    @property
    def access_db_path(self) -> Path:
        return self.resolve_data_dir() / ACCESS_DIRNAME / ACCESS_DB_FILENAME

    def resolve_public_url(self) -> str:
        """The public origin, validated, without a trailing slash.

        HTTPS is required unless the host is loopback, which is what tests and
        a purely local check use. No path, query or fragment: the origin is the
        OAuth issuer, and an issuer with a path is a different issuer.
        """
        raw = self.mcp_public_url.strip().rstrip("/")
        if not raw:
            raise ConfigError(
                "VADEMECUM_MCP_PUBLIC_URL is not set. In HTTP mode it must be the "
                "HTTPS origin your tunnel presents, e.g. https://my-mac.tailnet.ts.net."
            )
        parts = urlsplit(raw)
        host = parts.hostname or ""
        if parts.scheme not in {"http", "https"} or not host:
            raise ConfigError(f"VADEMECUM_MCP_PUBLIC_URL={raw!r} is not an http(s) origin.")
        if parts.scheme != "https" and not is_loopback(host):
            raise ConfigError(
                f"VADEMECUM_MCP_PUBLIC_URL={raw!r} is refused: it must be HTTPS. A "
                "bearer token over plain HTTP is a token on the wire."
            )
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ConfigError(
                f"VADEMECUM_MCP_PUBLIC_URL={raw!r} is refused: give the origin only, "
                "with no path, query or fragment."
            )
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def resource_url(self) -> str:
        return self.resolve_public_url() + MCP_PATH

    def prepare(self) -> Path:
        """Validate the boundaries and create the access directory."""
        self.check_bind()
        root = self.resolve_data_dir()
        (root / ACCESS_DIRNAME).mkdir(parents=True, exist_ok=True, mode=0o700)
        return root
