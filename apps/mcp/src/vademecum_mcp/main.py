"""Launch entry point: ``python -m vademecum_mcp <command>``.

    serve [--http | --stdio]   run the server (HTTP is the default)
    passphrase                 set or replace the owner's passphrase
    status                     what is configured and who is connected
    revoke-all                 sign every assistant out

In stdio mode stdout is the protocol channel, so every line of diagnostics goes
to stderr; ``configure_logging`` already does that.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys

from vademecum.config import ConfigError
from vademecum.logging_setup import configure_logging

from . import local
from .api_client import ApiClient
from .auth.passphrase import MIN_LENGTH, WeakPassphrase
from .auth.store import AccessStore
from .config import MCP_PATH, McpSettings
from .server import build_http_app, create_server

logger = logging.getLogger("vademecum_mcp")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vademecum_mcp", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="run the MCP server")
    mode = serve.add_mutually_exclusive_group()
    mode.add_argument("--http", action="store_true", help="streamable HTTP on 127.0.0.1 (default)")
    mode.add_argument("--stdio", action="store_true", help="stdio, for a client on this Mac")
    commands.add_parser("passphrase", help="set or replace the owner's passphrase")
    commands.add_parser("status", help="show configuration and connections")
    commands.add_parser("revoke-all", help="sign every connected assistant out")
    commands.add_parser("invite", help="print a one-time invite code for a new learner")
    commands.add_parser("learners", help="list learners")
    disable = commands.add_parser("disable", help="stop a learner signing in and revoke their tokens")
    disable.add_argument("handle")
    reset = commands.add_parser("reset", help="set a new passphrase for a learner who lost theirs")
    reset.add_argument("handle")
    setup = commands.add_parser(
        "setup", help="register with Codex or Claude Desktop, choose the source folder, or start at login"
    )
    setup.add_argument("target", choices=["codex", "claude", "folder", "login"])
    setup.add_argument("path", nargs="?", help="for `folder`: where the source folder should be")
    setup.add_argument("--remove", action="store_true", help="for `login`: stop starting at login")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging()
    logging.getLogger("mcp").setLevel(logging.WARNING)
    settings = McpSettings.load()
    try:
        data_dir = settings.prepare()
    except ConfigError as exc:
        print(f"Vademecum MCP will not start: {exc}", file=sys.stderr)
        return 2

    command = args.command or "serve"
    if command == "passphrase":
        return _set_passphrase(settings)
    if command == "status":
        return _status(settings)
    if command == "revoke-all":
        return _revoke_all(settings)
    if command == "invite":
        return _invite(settings)
    if command == "learners":
        return _learners(settings)
    if command == "disable":
        return _disable(settings, args.handle)
    if command == "reset":
        return _reset(settings, args.handle)
    if command == "setup":
        if args.target == "folder":
            return _setup_folder(args.path)
        if args.target == "login":
            return _setup_login(settings, remove=args.remove)
        return _setup(settings, args.target)
    if getattr(args, "stdio", False):
        return _serve_stdio(settings)
    return _serve_http(settings, data_dir)


def _set_passphrase(settings: McpSettings) -> int:
    if not sys.stdin.isatty():
        print("Set the passphrase from a terminal; it is not read from a pipe.", file=sys.stderr)
        return 2
    first = getpass.getpass(f"New Vademecum passphrase (at least {MIN_LENGTH} characters): ")
    second = getpass.getpass("Again: ")
    if first != second:
        print("They did not match. Nothing changed.", file=sys.stderr)
        return 1
    store = AccessStore(settings.access_db_path)
    try:
        store.set_passphrase(first)
    except WeakPassphrase as exc:
        print(f"Not set: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    print("Passphrase set. Assistants already connected stay connected; run revoke-all to sign them out.")
    return 0


def _status(settings: McpSettings) -> int:
    store = AccessStore(settings.access_db_path)
    try:
        summary = store.summary()
    finally:
        store.close()
    try:
        public = settings.resolve_public_url()
        endpoint = public + MCP_PATH
    except ConfigError:
        endpoint = "(VADEMECUM_MCP_PUBLIC_URL not set; HTTP mode needs it)"
    print(f"MCP endpoint:        {endpoint}")
    print(f"API on this Mac:     {settings.api_base_url}")
    print(f"Tenancy:             {settings.tenancy}")
    if settings.tenancy == "multi":
        print(f"Learners:            {summary['learners']} (open invites: {summary['open_invites']})")
    else:
        print(f"Passphrase set:      {'yes' if summary['passphrase_set'] else 'no — run: scripts/mcp.sh passphrase'}")
    print(f"Registered clients:  {len(summary['clients'])}")
    for name in summary["clients"]:
        print(f"  - {name}")
    print(f"Active connections:  {summary['active_connections']}")
    return 0


def _invite(settings: McpSettings) -> int:
    if settings.tenancy != "multi":
        print("Invites are for multi tenancy (VADEMECUM_TENANCY=multi). Nothing created.", file=sys.stderr)
        return 2
    store = AccessStore(settings.access_db_path)
    try:
        code = store.create_invite()
    finally:
        store.close()
    print(f"Invite code (valid 7 days, single use, shown once): {code}")
    print("The learner enters it on the sign-in page the first time they connect an assistant.")
    return 0


def _learners(settings: McpSettings) -> int:
    store = AccessStore(settings.access_db_path)
    try:
        learners = store.list_learners()
    finally:
        store.close()
    if not learners:
        print("No learners yet. Create an invite with: scripts/mcp.sh invite")
        return 0
    for learner in learners:
        state = "disabled" if learner.disabled else "active"
        seen = learner.last_sign_in or "never signed in"
        print(f"{learner.handle:<32} {state:<9} created {learner.created_at}  last sign-in {seen}")
    return 0


def _disable(settings: McpSettings, handle: str) -> int:
    from .auth.store import InviteError

    store = AccessStore(settings.access_db_path)
    try:
        revoked = store.disable_learner(handle)
    except InviteError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        store.close()
    print(f"Disabled {handle.strip().lower()} and revoked {revoked} token(s). Their workspace is untouched.")
    return 0


def _reset(settings: McpSettings, handle: str) -> int:
    from .auth.store import InviteError

    if not sys.stdin.isatty():
        print("Set the passphrase from a terminal; it is not read from a pipe.", file=sys.stderr)
        return 2
    first = getpass.getpass(f"New passphrase for {handle.strip().lower()} (at least {MIN_LENGTH} characters): ")
    second = getpass.getpass("Again: ")
    if first != second:
        print("They did not match. Nothing changed.", file=sys.stderr)
        return 1
    store = AccessStore(settings.access_db_path)
    try:
        revoked = store.reset_passphrase(handle, first)
    except InviteError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        store.close()
    print(
        f"Passphrase reset for {handle.strip().lower()}; {revoked} token(s) revoked. Every assistant "
        "and desk session has to sign in again. The workspace is untouched."
    )
    return 0


def _revoke_all(settings: McpSettings) -> int:
    store = AccessStore(settings.access_db_path)
    try:
        result = store.revoke_all()
    finally:
        store.close()
    print(
        f"Revoked {result['tokens_revoked']} token(s) and removed {result['clients_removed']} "
        "client registration(s). Every assistant has to connect again. The passphrase is unchanged."
    )
    return 0


def _serve_stdio(settings: McpSettings) -> int:
    """The local product (ADR 0012): the assistant starts this, this starts the API."""
    api = ApiClient(settings.api_base_url, timeout=settings.mcp_api_timeout)
    mcp = create_server(api, settings=settings)
    log_path = settings.resolve_data_dir() / "logs" / "api.log"

    async def main() -> None:
        children: list[object] = []

        async def ensure() -> None:
            child = await local.ensure_api(api, start=lambda: local.start_api(log_path))
            if child is not None:
                children.append(child)
                logger.info("api_started_by_stdio_server")

        await ensure()
        # If the API goes away mid-session (stopped by hand, crashed), the next
        # tool call starts it again instead of failing.
        api.recover = ensure
        try:
            await mcp.run_stdio_async()
        finally:
            api.recover = None
            await api.aclose()
            for child in children:
                local.stop_child(child)

    logger.info("stdio_start api=%s", settings.api_base_url)
    asyncio.run(main())
    return 0


def _setup_folder(chosen: str | None) -> int:
    """Choose the source folder, record it, and lay it out (ADR 0012)."""
    from pathlib import Path

    from vademecum.config import Settings as ApiSettings
    from vademecum.config import write_setting
    from vademecum.ingest import folder as folder_intake

    candidate = Path(chosen).expanduser() if chosen else Path.home() / "Documents" / "Vademecum"
    try:
        folder = ApiSettings(sources_dir=candidate).resolve_sources_dir()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    assert folder is not None
    folder.mkdir(parents=True, exist_ok=True)
    created = folder_intake.scaffold(folder)
    recorded = write_setting("SOURCES_DIR", str(folder))
    print(f"Your source folder: {folder}")
    print(f"Recorded in {recorded}")
    if created:
        print("Created: " + ", ".join(created))
    print("Put material in piles/highconfidence, piles/mediumconfidence or piles/lowconfidence,")
    print("one folder per pile. Vademecum reads it while running; ask your assistant to sync.")
    return 0


def _setup(settings: McpSettings, target: str) -> int:
    """Register this checkout's launcher with Codex or Claude Desktop."""
    try:
        command, args = local.launcher()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if target == "codex":
        backup = local.write_codex_config(local.CODEX_CONFIG, command=command, args=args)
        where = local.CODEX_CONFIG
        restart = "Restart the ChatGPT app (Codex reads its configuration at start)."
    else:
        backup = local.write_claude_config(local.CLAUDE_CONFIG, command=command, args=args)
        where = local.CLAUDE_CONFIG
        restart = "Restart Claude Desktop."
    print(f"Registered Vademecum with {target} in {where}")
    if backup is not None:
        print(f"The previous file is kept at {backup.name}")
    print(f"Launcher: {command} {' '.join(args)}")
    print(restart)
    print("The API starts itself when the assistant starts Vademecum; nothing else to run.")
    return 0


def _setup_login(settings: McpSettings, *, remove: bool = False) -> int:
    """Keep the API running from login, so the Dock app is always ready."""
    from vademecum.config import find_repo_root

    if sys.platform != "darwin":
        print("Starting at login is set up with launchd and needs macOS.", file=sys.stderr)
        return 2
    if remove:
        if local.remove_login_agent(local.LOGIN_AGENT):
            print("Vademecum no longer starts at login. An assistant still starts it when needed.")
        else:
            print("Vademecum was not set to start at login.")
        return 0
    root = find_repo_root()
    if root is None:
        print("cannot find the checkout; run setup from inside it", file=sys.stderr)
        return 2
    log_path = settings.resolve_data_dir() / "logs" / "api.log"
    local.write_login_agent(
        local.LOGIN_AGENT, python=sys.executable, working_directory=root, log_path=log_path
    )
    if not local.load_login_agent(local.LOGIN_AGENT):
        print(f"Wrote {local.LOGIN_AGENT} but launchd did not accept it; see `launchctl` output.", file=sys.stderr)
        return 1
    print(f"Vademecum starts at login and stays running ({local.LOGIN_AGENT.name}).")
    print(f"The dashboard is always at {settings.api_base_url} ; in Safari, File > Add to Dock puts it in the Dock.")
    print("If an assistant had already started Vademecum, the login copy waits for it to finish, then takes over.")
    print("After updating Vademecum, run this again to restart it on the new code.")
    return 0


def _serve_http(settings: McpSettings, data_dir) -> int:
    try:
        public_url = settings.resolve_public_url()
    except ConfigError as exc:
        print(f"Vademecum MCP will not start: {exc}", file=sys.stderr)
        return 2
    store = AccessStore(settings.access_db_path)
    if settings.tenancy == "single" and not store.has_passphrase():
        print(
            "No passphrase is set yet: assistants can find this server but nothing can be "
            "approved until you run: ./scripts/mcp.sh passphrase",
            file=sys.stderr,
        )
    api = ApiClient(settings.api_base_url, timeout=settings.mcp_api_timeout)
    app = build_http_app(settings, api, store)

    import uvicorn

    print(f"Vademecum MCP data directory: {data_dir}")
    print(f"Vademecum MCP: http://{settings.host}:{settings.mcp_port}{MCP_PATH} (loopback)")
    print(f"Public endpoint for assistants: {public_url}{MCP_PATH}")
    logger.info("http_start port=%d", settings.mcp_port)
    try:
        uvicorn.run(app, host=settings.host, port=settings.mcp_port, access_log=False)
    finally:
        store.close()
    return 0
