"""Vademecum as an MCP server (ADR 0008).

A thin, authenticated adapter: it exposes the local API's product actions as
MCP tools so ChatGPT and Claude can use the owner's workspace from a phone or
another Mac. It owns no storage, no SQL and no model connection. Everything it
does, it does by calling the API on ``127.0.0.1``.
"""

__version__ = "0.1.0"
