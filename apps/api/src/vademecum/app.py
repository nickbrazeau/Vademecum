"""FastAPI application factory.

Boundaries kept apart on purpose: this module wires HTTP to storage, to the
Codex App Server bridge and to the literature watcher, and owns no SQL, no
product rules and no protocol knowledge of its own.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response

from . import __version__
from .api import errors, origin
from .api import (
    routes_flags,
    routes_health,
    routes_literature,
    routes_maintenance,
    routes_media,
    routes_model,
    routes_overview,
    routes_piles,
    routes_sources,
    routes_tutor,
    routes_workspace,
)
from .appserver import ModelBridge
from .appserver.transport import Transport
from .appserver.turns import TurnRunner
from .config import DATABASE_FILENAME, Settings, find_repo_root, get_settings, settings_file_path
from .db import connect
from .ingest import folder as folder_intake
from .literature import ALLOWED_HOSTS, HttpsFetcher, LiteratureWatcher, PubMedProvider
from .logging_setup import log_requests
from .model import BuildService
from .model.host import HostTurns
from .storage import learning, literature as literature_store
from .tenancy import (
    OWNER_ID,
    SharedStoreResolver,
    TokenResolver,
    Workspace,
    Workspaces,
    prepare_workspace_database,
)

logger = logging.getLogger("vademecum")

API_PREFIX = "/api"

DESCRIPTION = (
    "Local-first clinical-learning workspace, bound to loopback. Your material is stored "
    "on this Mac. Two explicit actions transmit content, each after showing exactly what "
    "would be sent: Build learning material sends the source excerpts you previewed, and "
    "Grade sends one question, its reference answer and your typed answer. Both go to "
    "OpenAI through the local Codex process using ChatGPT sign-in; no API key is used. "
    "The literature watch sends short public topic words to PubMed and no learning content."
)


def static_root() -> Path | None:
    """The built frontend, when there is one. Absent in development."""
    repo_root = find_repo_root()
    if repo_root is None:
        return None
    candidate = repo_root / "apps" / "web" / "dist"
    return candidate if (candidate / "index.html").exists() else None


def prepare_database(settings: Settings) -> tuple[Path, Path]:
    """Prepare the single-owner data directory and bring its schema up to date.

    The owner's workspace is the data directory itself. Per-learner workspaces
    go through ``tenancy.Workspaces``, which calls the same preparation.
    """
    data_dir = settings.prepare()
    database_path = data_dir / DATABASE_FILENAME
    prepare_workspace_database(database_path, settings)
    return database_path, data_dir


def build_fetcher(settings: Settings) -> HttpsFetcher | None:
    """The one HTTPS client to the one allowlisted host, or None when off.

    One per process, shared by every workspace's provider: the throttle is
    the fetcher's, and NCBI's limit is per caller, not per learner.
    """
    if not settings.literature_enabled:
        return None
    host = next(iter(sorted(ALLOWED_HOSTS)))
    return HttpsFetcher(
        host,
        timeout=settings.literature_request_timeout,
        contact_email=settings.literature_contact_email,
        ncbi_key=settings.literature_ncbi_key,
    )


def build_provider(settings: Settings, fetcher: HttpsFetcher | None = None) -> PubMedProvider | None:
    """The literature provider, or None when the watch is switched off."""
    if not settings.literature_enabled:
        return None
    shared = fetcher or build_fetcher(settings)
    assert shared is not None
    return PubMedProvider(shared, max_results=settings.literature_max_results)


def current_sources_dir(settings: Settings) -> Path | None:
    """The source folder as of now, not as of startup.

    `setup folder` records a new location in the settings file while the API
    may well be running; every scan re-reads the file so the change takes
    effect without a restart. Without a settings file, the settings this
    process was built with stand.
    """
    path = settings_file_path()
    if path.is_file():
        fresh = Settings(_env_file=str(path), _env_file_encoding="utf-8")  # type: ignore[call-arg]
        fresh = fresh.model_copy(update={"tenancy": settings.tenancy, "model_provider": settings.model_provider})
        return fresh.resolve_sources_dir()
    return settings.resolve_sources_dir()


def backfill_pictures(database_path: Path, source_dir: Path) -> dict:
    """Pictures for sources stored before they were kept (ADR 0013), own connection."""
    from .ingest.backfill import backfill_pictures as run

    connection = connect(database_path)
    try:
        return run(connection, source_dir=source_dir)
    finally:
        connection.close()


def scan_sources_folder(database_path: Path, source_dir: Path, folder: Path | None) -> dict:
    """One scan of the learner's folder, on its own connection (ADR 0012)."""
    if folder is None:
        return {"folder_present": False, "stored": [], "piles_created": [], "rejected": []}
    folder.mkdir(parents=True, exist_ok=True)
    folder_intake.scaffold(folder)
    connection = connect(database_path)
    try:
        report = folder_intake.scan_folder(connection, source_dir=source_dir, folder=folder)
    finally:
        connection.close()
    if report["stored"] or report["piles_created"]:
        # Counts only. Filenames and pile names are the learner's words.
        logger.info(
            "folder_scan stored=%d piles_created=%d rejected=%d",
            len(report["stored"]),
            len(report["piles_created"]),
            len(report["rejected"]),
        )
    return report


async def _watch_folder(app: FastAPI, database_path: Path, source_dir: Path, interval: float) -> None:
    """Scan the folder every `interval` seconds; a failed scan is reported, not fatal.

    The folder is re-resolved each time, so a `setup folder` while this runs
    is honoured at the next scan and `app.state.sources_folder` follows it.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            folder = current_sources_dir(app.state.settings)
            app.state.sources_folder = folder
            await asyncio.to_thread(scan_sources_folder, database_path, source_dir, folder)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the watch reports and continues
            logger.error("folder_scan_failed error=%s", type(exc).__name__)


async def _watch_parent(parent_pid: int, interval: float = 2.0) -> None:
    """Stop this process when the one that started it is gone (ADR 0012).

    The MCP server starts the API as a child and stops it on exit, but an
    assistant that kills the server outright gives it no chance to. The child
    notices its parent has changed (to launchd, on macOS) and asks itself to
    shut down the way a Ctrl-C would, so the next server start finds the port
    free and starts current code.
    """
    import os
    import signal

    while True:
        await asyncio.sleep(interval)
        if os.getppid() != parent_pid:
            logger.info("parent_gone pid=%d; stopping", parent_pid)
            signal.raise_signal(signal.SIGTERM)
            return


def _status_change_handler(database_path: Path):
    """Hold dependent material when a watched paper is retracted or corrected.

    The watcher discovers the change; the learning bank has to react to it, or a
    question whose only evidence has been retracted keeps being asked. A
    correction is not a retraction -- both hold, neither auto-releases, and only
    a fresh verification pass can put a question back.
    """

    def handle(changes: list[dict[str, object]]) -> None:
        if not changes:
            return
        connection = connect(database_path)
        try:
            for change in changes:
                record_id = str(change.get("record_id") or "")
                if not record_id:
                    continue
                newly = str(change.get("newly") or "")
                reason = {
                    "retracted": (
                        "A paper this point was linked to has been retracted. It "
                        "is held until you re-check it."
                    ),
                    "corrected": (
                        "A paper this point was linked to has been corrected. "
                        "That is not a retraction, but it is held until you "
                        "re-check it."
                    ),
                }.get(
                    newly,
                    "A paper this point was linked to has changed status. It is "
                    "held until you re-check it.",
                )
                learning.flag_points_for_record(connection, record_id, reason=reason)
        except Exception:
            # The watcher must not die because the bank could not be updated.
            logger.exception("status_change_handler_failed")
        finally:
            connection.close()

    return handle


def create_app(
    settings: Settings | None = None,
    *,
    transport_factory: Callable[[], Transport] | None = None,
    provider_factory: Callable[[], object | None] | None = None,
    token_resolver: TokenResolver | None = None,
) -> FastAPI:
    """Build the application.

    ``transport_factory``, ``provider_factory`` and ``token_resolver`` are the
    seams the test suite uses: a scripted transport substitutes for the managed
    ``codex app-server`` child, a fake provider substitutes for PubMed, and a
    static resolver substitutes for the MCP server's access store, so the whole
    stack runs with no process, no socket and no model call anywhere.
    """
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        root = resolved.prepare()
        app.state.settings = resolved
        app.state.model_mode = resolved.model_provider
        # Multi tenancy: who a token belongs to. The production resolver reads
        # the MCP server's access store beside this data directory (ADR 0010);
        # the path is the MCP server's convention, pinned by a test over there.
        app.state.token_resolver = token_resolver or SharedStoreResolver(
            root / "mcp" / "access.sqlite3"
        )

        # The owner's Codex bridge. Constructed, not started: nothing spawns
        # until a request needs it, and in multi tenancy no request may.
        app.state.model_bridge = ModelBridge(
            codex_path=resolved.resolve_codex_path(),
            working_directory=root,
            client_version=__version__,
            request_timeout=resolved.appserver_request_timeout,
            startup_timeout=resolved.appserver_startup_timeout,
            transport_factory=transport_factory,
        )
        # One PubMed client for the whole process (ADR 0010): every workspace's
        # provider shares its throttle, so the process as a whole stays inside
        # NCBI's allowance however many learners are checking.
        app.state.literature_fetcher = None if provider_factory else build_fetcher(resolved)
        make_provider = provider_factory or (
            lambda: build_provider(resolved, app.state.literature_fetcher)
        )

        async def open_services(workspace: Workspace) -> None:
            """Per-workspace services: who does the model work, and the watch."""
            # Who does the model work (ADR 0009). In host mode this process has
            # no turn runner at all: the pipeline files pending turns for the
            # learner's ChatGPT.
            if resolved.model_provider == "host":
                host_turns: HostTurns | None = HostTurns(
                    workspace.database_path, ttl_seconds=resolved.host_turn_ttl
                )
                abandoned = host_turns.sweep()
                if abandoned:
                    logger.info("pending_turns_abandoned count=%d", abandoned)
                pipeline_turns: object = host_turns
            else:
                host_turns = None

                def codex_turn_factory() -> TurnRunner:
                    # A turn's cwd is the isolated empty workspace directory,
                    # which is what keeps project instructions and the owner's
                    # files out of a grading thread.
                    return TurnRunner(
                        app.state.model_bridge.client,
                        workspace=workspace.model_workspace,
                        turn_timeout=resolved.appserver_turn_timeout,
                    )

                pipeline_turns = codex_turn_factory
            workspace.host_turns = host_turns
            workspace.turn_factory = pipeline_turns
            workspace.build_service = BuildService(
                database_path=workspace.database_path,
                turn_factory=pipeline_turns,
                provider_factory=make_provider,
            )
            watcher: LiteratureWatcher | None = None
            if resolved.literature_enabled:
                connection = connect(workspace.database_path)
                try:
                    preferences = literature_store.get_settings(connection)
                finally:
                    connection.close()
                watcher = LiteratureWatcher(
                    database_path=workspace.database_path,
                    provider=make_provider(),
                    interval_hours=preferences.get(
                        "interval_hours", resolved.literature_interval_hours
                    ),
                    # Opt-in: the periodic sweep runs only if the learner
                    # switched it on. A manual "Check now" works regardless.
                    enabled=bool(preferences.get("weekly_enabled")),
                    on_status_change=_status_change_handler(workspace.database_path),
                )
                await watcher.start()
            workspace.watcher = watcher

        app.state.workspaces = Workspaces(resolved, open_services=open_services)

        def refusing_turn_factory() -> TurnRunner:
            raise RuntimeError("this process does not run model turns")

        app.state.turn_factory = refusing_turn_factory
        app.state.sources_folder = None
        folder_task: asyncio.Task[None] | None = None
        backfill_task: asyncio.Task[dict] | None = None
        parent_task: asyncio.Task[None] | None = None
        if resolved.parent_pid is not None:
            parent_task = asyncio.create_task(_watch_parent(resolved.parent_pid))
        if resolved.tenancy == "single":
            # The owner's workspace, opened at startup so migrations run before
            # the first request, as they always have. The handles below are
            # what the single-owner deployment and its tests reach for.
            owner = await app.state.workspaces.get(OWNER_ID)
            app.state.database_path = owner.database_path
            app.state.data_dir = owner.data_dir
            app.state.source_dir = owner.source_dir
            app.state.build_service = owner.build_service
            app.state.host_turns = owner.host_turns
            app.state.literature_watcher = owner.watcher
            if resolved.model_provider == "codex":
                app.state.turn_factory = owner.turn_factory

            # The learner's folder (ADR 0012): scanned once before the first
            # request, then in the background while this process runs.
            folder = current_sources_dir(resolved)
            if folder is not None:
                app.state.sources_folder = folder
                await asyncio.to_thread(scan_sources_folder, owner.database_path, owner.source_dir, folder)
                folder_task = asyncio.create_task(
                    _watch_folder(app, owner.database_path, owner.source_dir, resolved.sources_scan_interval)
                )
            # Sources stored before pictures were kept get them now, in the
            # background: a long scan is read on-device page by page.
            backfill_task = asyncio.create_task(
                asyncio.to_thread(backfill_pictures, owner.database_path, owner.source_dir)
            )
            app.state.backfill_task = backfill_task

        try:
            yield
        finally:
            for task in (folder_task, backfill_task, parent_task):
                if task is None:
                    continue
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            await app.state.workspaces.aclose()
            await app.state.model_bridge.aclose()

    app = FastAPI(
        title="Vademecum",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.middleware("http")(log_requests)

    @app.middleware("http")
    async def no_store_for_api(request: Request, call_next) -> Response:
        """Private content is never cacheable, by any layer."""
        response = await call_next(request)
        if request.url.path.startswith(API_PREFIX):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    # Registered after the loggers so a refused request is still logged, and
    # before the routers so nothing mutating runs without it.
    origin.install(
        app,
        authorities=origin.allowed_authorities(resolved.port, resolved.web_port),
    )

    errors.install(app)

    for router in (
        routes_health.router,
        routes_piles.router,
        routes_sources.router,
        routes_tutor.router,
        routes_literature.router,
        routes_flags.router,
        routes_overview.router,
        routes_maintenance.router,
        routes_model.router,
        routes_workspace.router,
        routes_media.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    dist = static_root()
    if dist is not None:
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Response:
            """Serve built files; fall back to the shell for client routes."""
            candidate = (dist / path).resolve()
            if path and dist in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
