# ADR 0008 — An MCP server, so ChatGPT and Claude can use the workspace

- Status: accepted
- Date: 2026-09-28
- Related: [0002](0002-local-first-boundary.md), [0004](0004-supported-platforms.md),
  [0006](0006-codex-app-server-bridge.md), [0007](0007-source-intake-verification-and-public-literature.md)

## Context

The owner asked for Vademecum on an iPhone and on the Mac as something that can be downloaded
from an app store. Three facts decided what that could mean:

1. **A native app cannot run the model path.** Build and Grade go through a spawned
   `codex app-server` child (ADR 0006). iOS apps cannot spawn processes; a sandboxed Mac App
   Store app can only run binaries inside its own bundle, and using OpenAI's ChatGPT sign-in
   from a third-party store app is a terms question for OpenAI and an external-subscription
   question for Apple's review.
2. **OpenAI offers no way for a third-party app to run on a person's ChatGPT plan.** "Sign in
   with ChatGPT" exists only inside OpenAI's own Codex tooling, and where it ships as an identity
   provider it is a login, not model access. Workarounds that reuse Codex tokens are unsupported
   and against its terms. So a native app calling OpenAI directly would need the owner's own API
   key -- the one thing AGENTS.md rules out -- and the owner declined that path.
3. **ChatGPT and Claude both connect to MCP servers, on the phone and on the desktop, on the
   owner's own plan.** ChatGPT through the Apps SDK (and its App Directory), Claude through
   custom connectors and the Connectors Directory. A server they reach uses their plan for the
   conversation; Vademecum's own model turns keep going through Codex as before.

The owner chose the third: one MCP server for both hosts, running on the Mac and reached through
a tunnel, so the data stays where ADR 0002 put it.

## Decisions

### 1. A separate process, and a client of the API

`apps/mcp` is its own package and its own process (`scripts/mcp.sh`), bound to `127.0.0.1:8766`
with the same refusal of any other address the API has. It owns no storage, no SQL, no model
connection and no Codex child. Every tool is one HTTP call to the API at `http://127.0.0.1:8765`,
and `api_client.py` refuses a base URL that is not loopback.

The consequence that matters: **the API's boundary is unchanged.** Every action an assistant can
take is one the web app could take, through the same validation, the same consent hashing and the
same disclosures. The MCP server adds a caller; it does not add a capability.

*Alternative rejected:* importing storage and the bridge in-process. That would be a second
Codex child and a second build service with state the API cannot see. Mounting `/mcp` inside the
API was rejected too: the API checks `Host` on every request precisely so that a name arriving
through a tunnel is refused, and an MCP endpoint needs the opposite.

### 2. The third transmission surface

ADR 0007 names two explicit transmissions of study content (Build, Grade) and one narrow egress
(PubMed). This slice adds nothing to what *Vademecum* sends. It adds something else, which the
documents now have to say plainly:

**Every tool result goes into the host's conversation, and that conversation is processed by the
host's provider** -- OpenAI for ChatGPT, Anthropic for Claude. A learning point read through
`get_learning_point`, a passage read through `read_source`, a flag's text, a Tutor question and
the owner's typed answer: all of it is in that provider's hands the moment the tool returns. This
is a property of using a chat host, not something Vademecum can gate, and the honest framing is
that the owner has *chosen* a host and its provider as a place to read their material.

Where this is stated: the consent page ("Anything it reads becomes part of that assistant's
conversation with its provider"), the server instructions the host model reads, the README, and
here. Where it is not softened: nowhere says "local", "private" or "nothing is sent" about the
MCP path.

What the tool descriptions add on top: `build_start` and `tutor_grade` say what *Vademecum* then
sends onward through Codex, `literature_check` says what reaches PubMed, and the three carry the
`openWorldHint` annotation so a host presents them differently from a read.

### 3. OAuth 2.1 with exactly one resource owner

The MCP specification requires OAuth 2.1 for HTTP transports, and both hosts require it to
connect: protected-resource metadata (RFC 9728), authorization-server metadata (RFC 8414),
dynamic client registration (RFC 7591), the authorization-code grant with PKCE `S256`, and the
`resource` indicator (RFC 8707). The SDK serves all of those endpoints; `auth/provider.py` is the
provider behind them and makes the decisions that are Vademecum's:

- **Approval is a passphrase, set once on the Mac.** There is no account and no identity
  provider. `/authorize` lands on a consent page that names the client and where it will be sent
  back to, says what a connection can do, and asks for the passphrase. scrypt from the standard
  library; five wrong attempts in fifteen minutes lock the page for the rest of the window. No
  passphrase set means nothing can be approved, and the page says so.
- **Tokens are opaque random strings, stored as SHA-256 digests** in a small SQLite file of their
  own (`<data dir>/mcp/access.sqlite3`, mode `0600`). Not in the study database, so they are in
  no export and no backup, and revoking every connection touches no learning record.
- **Access tokens live an hour; refresh tokens rotate.** Presenting a rotated refresh token again
  revokes the whole family. Every token is bound to the one resource URL and the SDK refuses any
  other.
- **One scope**, `vademecum`, because there is one workspace and one owner. Scoping tools finer
  than that would be a permission design nobody asked for.
- **Dynamic registration only.** Both hosts prefer Client ID Metadata Documents, which would
  require this server to fetch a document from a URL the client chose -- an outbound request to
  a client-controlled address, from the one process in this system that is otherwise a client
  of loopback only. Not advertised, not implemented; both hosts fall back to registration.
  Redirect URIs must be HTTPS or loopback; at most fifty clients may register.
- **No API key, still.** The tokens here are issued by Vademecum to a host; they are not
  provider credentials, and nothing in this package names a provider endpoint or a key. A test
  reads the source for both.

Stdio mode has no authorization, as the specification says it should not: the process was
started by the owner on the Mac, and the boundary is the process.

### 4. A dependency inside the trust boundary, and why

`mcp` (the reference Python SDK) is the first runtime dependency added for protocol reasons since
the bridge was written by hand (ADR 0006). The difference: the App Server is one peer with a
pinned, generated schema; MCP is two independently implemented hosts that both expect streamable
HTTP session handling, `WWW-Authenticate` discovery, the OAuth endpoints above and tool schema
conventions to interoperate exactly. That is the kind of risk a dependency is allowed to remove.
It is pinned below its next major version. `httpx` becomes a runtime dependency for the one
loopback client.

### 5. What the tool surface leaves out

Thirty-one tools, pinned by a test. Excluded on purpose:

- **ChatGPT sign-in.** The device code is shown once on the Mac and stored nowhere (ADR 0006).
  Returning it through a tool would put it in a chat transcript.
- **Deletion and retirement.** Piles, sources, flags and generated material are deleted from the
  web app, where the owner sees what they are deleting.
- **Export and backup.** They write files on the Mac; they belong beside the files.
- **File uploads.** `add_text_source` takes pasted text as a Markdown source. PDFs, decks and
  Word documents are dropped in on the Mac.

### 6. The tunnel is the owner's

Nothing here opens a port to the world. `scripts/mcp.sh --tunnel` runs `tailscale funnel` in the
foreground in front of the loopback port and removes it on exit; any other tunnel works by setting
`VADEMECUM_MCP_PUBLIC_URL`. That URL must be HTTPS, has no path, and is the OAuth issuer -- taken
from configuration, never from a `Host` header. The SDK's DNS-rebinding protection is configured
with exactly the public name and loopback, so any other `Host` is a `421`.

### 7. Tested against the real API, with no socket

The suite builds the actual API application in-process, with the scripted turn runner and fake
PubMed the API's own end-to-end test uses, and drives the MCP server through the SDK's in-memory
client and the HTTP application through httpx's ASGI transport. A note becomes a build, a build
becomes a question, a question is graded, and the OAuth flow runs end to end -- with no process,
no network and no model call. The autouse guard that stops any test spawning a real Codex is
copied from the API's suite.

## Consequences

- The owner can use Vademecum from ChatGPT or Claude on a phone while the Mac runs
  `./scripts/dev.sh` and `./scripts/mcp.sh --tunnel`. When the Mac sleeps, so does this.
- The owner's ChatGPT and Claude conversation histories now hold whatever the tools returned.
  Deleting a flag in Vademecum does not delete it from a transcript.
- Listing in the ChatGPT App Directory or the Claude Connectors Directory is a separate,
  human step: a verified developer account, a domain the owner controls, a privacy policy and a
  review. This slice supports the personal path -- ChatGPT's developer mode and Claude's custom
  connectors -- and does not attempt a listing.
- Two processes now write to the terminal. The data directory is printed by both and appears in
  no tool result; a test checks every read tool for a path.
- AGENTS.md's architecture diagram gains one box, and its boundary list gains the sentence that
  tool results reach the host's provider.
