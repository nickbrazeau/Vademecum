"""Local launch entry point: ``python -m vademecum``.

One long-lived process, bound to ``127.0.0.1``. There is no setting that binds
it anywhere else (ADR 0002).
"""

from __future__ import annotations

import logging
import sys

from .app import create_app
from .config import ConfigError, get_settings
from .logging_setup import configure_logging


def run() -> int:
    configure_logging()
    settings = get_settings()
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


if __name__ == "__main__":
    raise SystemExit(run())
