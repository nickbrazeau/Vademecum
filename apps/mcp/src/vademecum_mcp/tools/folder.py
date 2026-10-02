"""The source folder, syncing it, and the records that leave as files.

The one place a tool result names a path on purpose: the learner's own
source folder, so the assistant can tell them where to put a lecture. The
API never returns a path (ADR 0002); this server knows the folder from the
same settings file the API reads, and says so itself.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from ..api_client import ApiClient
from ..config import McpSettings
from ._shared import READ, WRITE, call

LAYOUT = (
    "One folder per pile inside a tier folder: piles/highconfidence/<pile>/, "
    "piles/mediumconfidence/<pile>/, piles/lowconfidence/<pile>/. The tier folder is the "
    "pile's source-confidence rating; move a pile's folder to re-rate it. PDF, .pptx, "
    ".docx, text and Markdown are read. Removing a file from the folder does not remove "
    "it from Vademecum; remove_source does."
)


def register(mcp: MCPServer, api: ApiClient, settings: McpSettings | None) -> None:
    @mcp.tool(annotations=READ)
    async def sources_folder() -> dict[str, Any]:
        """Where the owner puts material: the path of their Vademecum source
        folder on this Mac and how it is laid out. Tell them this path when
        they ask how to add a lecture, a deck or a document. In a hosted
        Vademecum there is no folder and files come in through the web app."""
        folder = settings.resolve_sources_dir() if settings is not None else None
        if folder is None:
            return {
                "folder": None,
                "note": "This Vademecum has no source folder; files come in through the web app.",
            }
        return {
            "folder": str(folder),
            "piles": str(folder / "piles"),
            "layout": LAYOUT,
            "note": (
                "Vademecum reads the folder every few seconds while running and whenever "
                "sync_sources is called. Never put patient identifiers in it."
            ),
        }

    @mcp.tool(annotations=WRITE)
    async def sync_sources() -> dict[str, Any]:
        """Read the source folder now: new files become sources in their piles,
        new pile folders become piles, moved pile folders are re-rated. Returns
        what was stored, what was already present, what is still being written
        and what was rejected with a reason. Call this after the owner says
        they added something, before previewing a build."""
        return await call(api.post("/api/sources/scan"))

    @mcp.tool(annotations=WRITE)
    async def export_workspace() -> dict[str, Any]:
        """Write a readable JSON export of everything in the workspace into the
        exports directory beside the records. Returns the file name, never a
        path."""
        return await call(api.post("/api/export"))

    @mcp.tool(annotations=WRITE)
    async def backup_workspace() -> dict[str, Any]:
        """Write a restorable backup bundle, the database plus every stored
        original, into the backups directory beside the records. Returns the
        file name, never a path."""
        return await call(api.post("/api/backup"))
