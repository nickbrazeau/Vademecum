"""The tool surface, grouped the way the product is.

Every tool is a thin call to the API. What is deliberately *not* here is as
much the design as what is (ADR 0008):

* no ChatGPT sign-in -- the device code must not pass through a chat provider;
* no deletion of piles or flags and no retiring of material; a source can be
  removed, with a confirmation word, because the folder is how material comes
  in and a folder needs a way out;
* no file upload beyond pasted text -- files go into the source folder;
* export and backup, which write into the records directory on this Mac and
  return a file name, never a path;
* open_dashboard, which opens the web app served from this Mac in the owner's
  own browser, and open_vademecum, which has the host draw the same web app
  inside the conversation; the app's own requests go through app_request,
  which allows a fixed list of routes (ADR 0014).
"""

from __future__ import annotations

from mcp.server import MCPServer

from ..api_client import ApiClient
from ..config import McpSettings
from . import app, build, dashboard, encyclopedia, folder, gaps, library, literature, media, socratic, tutor


def register(mcp: MCPServer, api: ApiClient, settings: McpSettings | None = None) -> None:
    # The Socratic tutor first: a client that reads only the head of a long tool
    # list still finds it (feedback of 6 October).
    socratic.register(mcp, api)
    library.register(mcp, api)
    media.register(mcp, api)
    folder.register(mcp, api, settings)
    dashboard.register(mcp, api, settings)
    app.register(mcp, api)
    gaps.register(mcp, api)
    build.register(mcp, api)
    tutor.register(mcp, api)
    encyclopedia.register(mcp, api)
    literature.register(mcp, api)
