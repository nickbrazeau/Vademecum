"""One TLS client context for the two places this process speaks HTTPS.

The system's own trust store when it can be read (macOS keychain, through
`truststore`), so a Mac behind a workplace proxy that re-signs TLS still
reaches PubMed and the learner's own seat; Python's bundled roots otherwise.
Nothing here changes what is sent or where.
"""

from __future__ import annotations

import ssl


def client_context() -> ssl.SSLContext:
    try:
        import truststore
    except ImportError:
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
