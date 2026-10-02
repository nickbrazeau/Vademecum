"""Background build supervision: one run per pile, cancellable, never orphaned."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..db import connect
from ..storage import jobs, sources
from .build import BuildRunner

logger = logging.getLogger("vademecum.model")


@dataclass
class _Active:
    task: asyncio.Task[Any]
    runner: BuildRunner
    run_id: str
    batch_id: str


class BuildService:
    """Starts, tracks and stops build runs.

    One run per pile at a time. Concurrency here would race two batches onto the
    same coverage and produce two sets of near-duplicate points, so a second
    start is a conflict rather than a queue.
    """

    def __init__(
        self,
        *,
        database_path: Path,
        turn_factory: Callable[[], Any],
        provider_factory: Callable[[], Any | None],
    ) -> None:
        self._database_path = database_path
        self._turn_factory = turn_factory
        self._provider_factory = provider_factory
        self._active: dict[str, _Active] = {}
        self._lock = asyncio.Lock()

    def is_running(self, pile_id: str) -> bool:
        active = self._active.get(pile_id)
        return active is not None and not active.task.done()

    async def start(self, *, pile_id: str, run_id: str, batch_id: str) -> None:
        async with self._lock:
            if self.is_running(pile_id):
                raise sources.ConflictError(
                    "build_running", "A build is already running for this pile."
                )
            runner = BuildRunner(
                database_path=self._database_path,
                turn_factory=_scoped(self._turn_factory, run_id),
                provider=self._provider_factory(),
            )
            task = asyncio.get_running_loop().create_task(
                runner.run(pile_id=pile_id, run_id=run_id, batch_id=batch_id)
            )
            self._active[pile_id] = _Active(task, runner, run_id, batch_id)
            task.add_done_callback(lambda _: self._active.pop(pile_id, None))

    async def cancel(self, pile_id: str) -> bool:
        active = self._active.get(pile_id)
        if active is None or active.task.done():
            return False
        active.runner.cancel()
        active.task.cancel()
        try:
            await active.task
        except (asyncio.CancelledError, Exception):
            pass
        return True

    async def aclose(self) -> None:
        """Stop everything. A run left behind on shutdown becomes 'interrupted'.

        The sweep at next startup is what turns it into an honest failed row;
        this only makes sure no task outlives the process.
        """
        for pile_id in list(self._active):
            await self.cancel(pile_id)

    def sweep(self) -> int:
        connection = connect(self._database_path)
        try:
            return jobs.sweep_interrupted(connection)
        finally:
            connection.close()


def _scoped(turn_factory: Any, run_id: str) -> Callable[[], Any]:
    """The pipeline's turn factory for one run.

    The Codex factory takes no arguments and knows nothing about runs. The host
    factory (ADR 0009) has to file each pending turn under the run it belongs
    to, so it offers ``scoped``; when it does, that is what the pipeline gets.
    """
    scoped = getattr(turn_factory, "scoped", None)
    if callable(scoped):
        return scoped("run", run_id)
    return turn_factory
