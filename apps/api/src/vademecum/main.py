"""Local launch entry point: ``python -m vademecum``.

One long-lived process, bound to ``127.0.0.1``. There is no setting that binds
it anywhere else (ADR 0002).
"""

from __future__ import annotations

import logging
import sys
import time

from .app import create_app
from .config import ConfigError, get_settings
from .logging_setup import configure_logging


def run(argv: list[str] | None = None) -> int:
    configure_logging()
    settings = get_settings()
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["sync"]:
        return sync_now(settings)
    if arguments:
        print("usage: python -m vademecum [sync]", file=sys.stderr)
        return 64
    try:
        settings.check_bind()
        data_dir = settings.resolve_data_dir()
    except ConfigError as exc:
        print(f"Vademecum will not start: {exc}", file=sys.stderr)
        return 2

    import uvicorn

    logging.getLogger("vademecum").info(
        "starting host=%s port=%d", settings.host, settings.port
    )
    wait_for_port(settings.host, settings.port)
    # The data directory is printed here, in the terminal, where it is useful.
    # It appears in no HTTP response (ADR 0002, rule 6).
    print(f"Vademecum data directory: {data_dir}")
    print(f"Vademecum: http://{settings.host}:{settings.port}/")
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        # Our own middleware logs requests without free text; uvicorn's access
        # log would repeat the line including the query string.
        access_log=False,
    )
    return 0


def sync_now(settings) -> int:
    """One sync round with the configured peer, from the terminal (ADR 0015)."""
    from .app import prepare_database, sync_with_peer
    from .config import SOURCE_FILES_DIRNAME
    from .sync import SyncError

    if not settings.sync_peer_url or not settings.sync_token:
        print("No peer is configured: set VADEMECUM_SYNC_PEER_URL and VADEMECUM_SYNC_TOKEN.", file=sys.stderr)
        return 2
    try:
        database_path, data_dir = prepare_database(settings)
        result = sync_with_peer(database_path, data_dir / SOURCE_FILES_DIRNAME, settings)
    except (ConfigError, SyncError) as exc:
        print(f"Sync did not complete: {exc}", file=sys.stderr)
        return 1
    print(
        f"Synced: pulled {result['pulled']} (applied {result['applied']}, deferred {result['deferred']}), "
        f"pushed {result['pushed']}."
    )
    return 0


def port_in_use(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def wait_for_port(host: str, port: int, *, pause: float = 5.0, in_use=port_in_use, sleep=time.sleep) -> int:
    """Wait, rather than fail, while another Vademecum holds the port.

    The copy launchd starts at login (ADR 0014) can find an assistant's own
    child already answering; it waits for that child to finish and takes
    over, instead of exiting and being restarted every few seconds. Returns
    how many times it waited.
    """
    waited = 0
    while in_use(host, port):
        if waited == 0:
            logging.getLogger("vademecum").info("port_busy host=%s port=%d; waiting", host, port)
            print(f"Another Vademecum is answering on {host}:{port}; waiting for it to finish.", flush=True)
        waited += 1
        sleep(pause)
    return waited


if __name__ == "__main__":
    raise SystemExit(run())
