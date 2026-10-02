"""The authorization server behind the SDK's OAuth endpoints.

The SDK owns the wire format and the checks that belong to it -- redirect URI
matching, PKCE, expiry, client authentication. This provider owns the
decisions that are Vademecum's: one owner, one resource, tokens that are opaque
random strings stored as digests, refresh rotation with reuse detection, and
the consent page as the only way to turn a request into a code.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ..config import CONSENT_PATH, SCOPE
from .store import MAX_CLIENTS, AccessStore

REUSED_REFRESH = (
    "That refresh token was already used. Every connection from this client has "
    "been signed out; connect again from the assistant."
)


def canonical(url: str) -> str:
    """Lowercase scheme and host, no trailing slash: the RFC 8707 comparison."""
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{netloc}{path}"


class OwnerAuthorizationServer:
    """Implements ``mcp.server.auth.provider.OAuthAuthorizationServerProvider``."""

    def __init__(
        self,
        store: AccessStore,
        *,
        public_url: str,
        resource_url: str,
        access_ttl: int,
        refresh_ttl: int,
    ) -> None:
        self.store = store
        self.public_url = public_url.rstrip("/")
        self.resource_url = canonical(resource_url)
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl

    # --- clients --------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.store.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            parts = urlsplit(str(uri))
            host = (parts.hostname or "").lower()
            secure = parts.scheme == "https"
            local = parts.scheme == "http" and host in {"localhost", "127.0.0.1", "[::1]", "::1"}
            if not (secure or local):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    "Redirect URIs must be HTTPS, or HTTP to localhost.",
                )
        if self.store.count_clients() >= MAX_CLIENTS:
            raise RegistrationError(
                "invalid_client_metadata",
                "Too many clients are registered with this Vademecum. Run "
                "`scripts/mcp.sh revoke-all` on the Mac to clear them.",
            )
        self.store.save_client(client_info)

    # --- authorization --------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource is not None and canonical(params.resource) != self.resource_url:
            raise AuthorizeError(
                "invalid_target", "This authorization server issues tokens for one resource only."
            )
        pending = self.store.create_pending(client.client_id, params)
        return f"{self.public_url}{CONSENT_PATH}?request={pending}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self.store.load_code(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not self.store.mark_code_used(authorization_code.code):
            raise TokenError("invalid_grant", "authorization code already used")
        resource = self.resource_url
        if authorization_code.resource is not None and canonical(authorization_code.resource) != resource:
            raise TokenError("invalid_target", "token requested for a different resource")
        # The learner the consent page signed in, carried by the code. None in
        # single tenancy, where the token is the owner's and needs no name.
        return self._issue(client.client_id, family_id=None, learner_id=authorization_code.subject)

    # --- refresh --------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        # Revoked tokens are loaded on purpose: presenting one is the signal
        # that a rotated token leaked, and the exchange below acts on it.
        record = self.store.load_token(refresh_token, "refresh", include_revoked=True)
        if record is None or record.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record.client_id,
            scopes=record.scopes,
            expires_at=record.expires_at,
            resource=record.resource,
            subject=record.learner_id,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        record = self.store.load_token(refresh_token.token, "refresh", include_revoked=True)
        if record is None:
            raise TokenError("invalid_grant", "refresh token does not exist")
        if record.revoked:
            self.store.revoke_family(record.family_id)
            raise TokenError("invalid_grant", REUSED_REFRESH)
        self.store.revoke_token(refresh_token.token)
        return self._issue(client.client_id, family_id=record.family_id, learner_id=record.learner_id)

    # --- verification ---------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        record = self.store.load_token(token, "access")
        if record is None:
            return None
        return AccessToken(
            token=token,
            client_id=record.client_id,
            scopes=record.scopes,
            expires_at=record.expires_at,
            resource=record.resource,
            subject=record.learner_id,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        kind = "refresh" if isinstance(token, RefreshToken) else "access"
        record = self.store.load_token(token.token, kind, include_revoked=True)
        self.store.revoke_token(token.token)
        if record is not None and kind == "refresh":
            self.store.revoke_family(record.family_id)

    async def exchange_identity_assertion(self, client, params):  # pragma: no cover
        raise TokenError("unsupported_grant_type", "The JWT bearer grant is not supported here.")

    # --- helpers --------------------------------------------------------

    def _issue(
        self, client_id: str, *, family_id: str | None, learner_id: str | None = None
    ) -> OAuthToken:
        issued = self.store.issue_tokens(
            client_id=client_id,
            scopes=[SCOPE],
            resource=self.resource_url,
            access_ttl=self.access_ttl,
            refresh_ttl=self.refresh_ttl,
            family_id=family_id,
            learner_id=learner_id,
        )
        return OAuthToken(
            access_token=issued.access_token,
            token_type="Bearer",
            expires_in=issued.expires_in,
            scope=SCOPE,
            refresh_token=issued.refresh_token,
        )
