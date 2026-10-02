"""Who may use the tools (ADR 0008).

OAuth 2.1 as the MCP specification requires -- PKCE, dynamic client
registration, protected-resource metadata -- with one deliberate simplification:
there is exactly one resource owner. Approving a connection means the owner
typing a passphrase, set once on the Mac, into a page this server renders. No
account, no identity provider, no third party.

The SDK serves the protocol endpoints; ``provider.py`` is what they call into,
``store.py`` is where the records live, and ``consent.py`` is the one page.
"""
