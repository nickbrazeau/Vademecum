# ADR 0011 — The desk through the gateway

- Status: accepted; superseded by ADR 0012 (a local install each learner owns), kept for hosted mode
- Date: 2026-09-28
- Amends: [0009](0009-hosted-personal-workspaces-chatgpt-is-the-model.md) (phase 3),
  [0008](0008-mcp-server-for-chat-hosts.md), [0010](0010-tenancy-by-workspace-per-learner.md)

## Context

ADR 0009 gave the product two surfaces: the ChatGPT app for the daily loop and the web app as
the desk, for the work only a browser can do -- uploading files, managing sources, exporting,
deleting. In multi tenancy the desk needs a sign-in and a way to reach the API as one learner.
Two shapes were possible: teach the React app to run the OAuth flow itself and hold a token in
JavaScript, or let the process that is already the public host and already the authority on
tokens serve the desk as well. The owner chose the second.

## Decisions

### 1. One public host, one sign-in, one place that knows a token

The MCP server serves the desk in multi tenancy: `GET /login` is the same sign-in the consent
page uses, rendered by the same code (`auth/pages.py`), with the same invite flow the first
time. It becomes the gateway for the desk's API calls and the origin the browser sees. The API
stays private on loopback, reached by two callers only: the tools, with the assistant's token,
and the gateway, with the desk session's token.

### 2. A desk session is a token in a cookie, not a token in JavaScript

A successful sign-in mints an access token in the same access store the API reads -- client
`desk`, the learner attached, no refresh token, no resource -- and sets it in a cookie marked
`HttpOnly`, `SameSite=Strict`, `Secure` on HTTPS, path `/`, with a fourteen-day lifetime. The
API resolves it like any other token. The MCP endpoint refuses it, because it names no resource
and the SDK's bearer check binds tokens to the resource. Nothing in the browser can read it, and
nothing the browser stores is more than the shell and unsent drafts, as ADR 0002 requires.

Sign-out revokes the token and clears the cookie. Deleting the workspace from the desk revokes
every token the learner holds, the session's included, and the next request is a 401.

### 3. The API is proxied, streamed, and checked for origin

`/api/*` on the gateway forwards to `/api/*` on the API with the cookie's token in
`X-Vademecum-Token`. Request and response bodies are streamed, so a forty-megabyte upload passes
through without landing in the gateway's memory, and the API's own limits still apply. Only the
headers that describe the message travel in either direction; the browser's cookies never reach
the API and the API's `Host` is its own.

Mutating requests must come from this origin. The cookie's `SameSite=Strict` is the first line;
`Origin`, when a browser sends it, must match the public origin, and `Sec-Fetch-Site` must not
say cross-site. A read from a foreign origin succeeds only in the sense that the browser will
not attach the cookie; there is no CORS anywhere, so the reply is unreadable to it.

### 4. The built web app at the root, behind the session

The gateway serves `apps/web/dist` (or `VADEMECUM_MCP_DESK_DIST`) at the public root: hashed
assets immutable, the worker and manifest revalidated, the shell `no-store`, client routes
falling back to the shell, and a browser with no session redirected to `/login`. The service
worker's allowlist (ADR 0002) is unchanged because the paths are unchanged. Paths are resolved
inside the build directory and nothing outside it is served.

The web app changed in two small ways: a 401 from the API is a navigation to `/login`, and
the header shows the learner's sign-out and hides the owner's Model page when the health check
says the workspace is behind the gateway.

### 5. Single tenancy has no desk here

On the owner's Mac the desk is the Vite dev server or the API's own static serving, on loopback,
as before. The gateway's `/login`, `/logout`, `/api/*` and root serving exist only in multi
tenancy; a single-tenancy gateway keeps the plain notice at its root.

## Consequences

- A learner has one credential and two clients: their ChatGPT, through OAuth, and their browser,
  through the cookie. Both resolve to the same workspace through the same store.
- The gateway is now on the request path for uploads, so its process must be sized for
  concurrent streams; it holds nothing per request beyond the buffers in flight.
- Widgets for the ChatGPT app remain the second half of phase 3 and do not touch the desk.
