# Codex App Server protocol, pinned

Everything in this directory is **generated**. Nothing in it was written or edited by hand, and
nothing in it should be. AGENTS.md treats the installed Codex CLI's own schema as the source of
truth for the App Server protocol, because that protocol evolves with the CLI and a contract
transcribed from documentation is a contract that silently rots.

## Provenance

| | |
| --- | --- |
| Generator | `codex app-server generate-json-schema --out <dir>` |
| CLI | `codex-cli 0.153.4` (recorded in `MANIFEST.json`) |
| Installed at | `/Applications/ChatGPT.app/Contents/Resources/codex` on this Mac |
| Experimental surface | **excluded** — `--experimental` is deliberately not passed |
| Refreshed by | [`scripts/refresh-codex-schemas.py`](../../scripts/refresh-codex-schemas.py) |

```sh
scripts/refresh-codex-schemas.py            # regenerate and rewrite this directory
scripts/refresh-codex-schemas.py --check    # report drift; writes nothing, exits 1 if it differs
```

`--experimental` is excluded on purpose. The bridge sends `capabilities.experimentalApi: false`
during `initialize`, so pinning the experimental contract would pin a surface it must never speak.

## Why this is a subset

The full bundle is about 3.6 MB across 285 files, nearly all of it tool, plugin, realtime and
external-agent surface that the bridge does not touch. Committing all of it would bury the one thing
that matters in review — a change under the surface the bridge actually speaks — in noise.

So the refresh script copies, **byte for byte**, the files the bridge actually depends on, and
derives four sorted method-name lists from the generated top-level unions. `MANIFEST.json` records
the SHA-256 of every committed file, plus the SHA-256 of the four large union documents the method
lists came from, so a reviewer can tell a derived list apart from an edited one.

## Layout

```text
envelope/     transport framing: RequestId, JSONRPCRequest/Response/Notification/Error, and
              the ClientNotification union. Note what is absent from every one of them: a
              `jsonrpc` member. The default stdio transport is newline-delimited JSON with no
              JSON-RPC version header, and the bridge neither sends nor requires one.
v1/           InitializeParams (clientInfo, capabilities) and InitializeResponse.
v2/           account/read, account/login/start, account/login/cancel, account/rateLimits/read
              and the three account notifications; plus thread/start, turn/start, turn/interrupt,
              config/read and the six thread/turn/item notifications a grading turn observes.
server-requests/  the decision vocabularies for the approvals the bridge refuses, in both the
              current (`item/…/requestApproval`) and legacy (`execCommandApproval`,
              `applyPatchApproval`) spellings.
methods/      sorted method names for client requests, client notifications, server requests and
              server notifications, derived from the generated unions.
MANIFEST.json CLI version, generator command, and a SHA-256 for every file.
```

## What the tests do with this

`apps/api/tests/test_appserver_schemas.py` reads these files rather than trusting the bridge's
own constants:

- every method the bridge sends must appear in `methods/client-requests.json`;
- `initialized` must be the client notification the bridge sends;
- every notification the bridge listens for must appear in `methods/server-notifications.json`;
- the bridge's server-request table must **equal** `methods/server-requests.json`, so a Codex
  upgrade that adds a server-initiated request the bridge has not classified fails the suite
  instead of falling into a default;
- the refusal decisions the bridge sends must be members of the pinned decision enums;
- `chatgptDeviceCode` must be a `LoginAccountParams` variant, and the `apiKey` variant must exist
  in the schema and appear nowhere in what the bridge can construct.

That last one is the point of pinning `LoginAccountParams` at all: the schema offers API-key login,
and the test proves Vademecum cannot reach it.

## Why the thread/turn surface is now pinned too

Until the grading slice this directory held the account surface only, because the bridge started no
threads and ran no turns. It now does both, so the same argument that pinned `LoginAccountParams`
applies to `ThreadStartParams` and `TurnStartParams`: every safety property of a grading turn is a
value in one of these documents, and none of them is safe to remember rather than check.

- `ThreadStartParams` carries `sandbox`, `approvalPolicy`, `ephemeral` and the free-form `config`
  the hardening layer uses to disable MCP servers by name. `SandboxMode` is where `read-only` is
  spelled, and `AskForApproval` is where `never` is.
- `TurnStartParams` carries `sandboxPolicy` and `outputSchema`. `SandboxPolicy` is where
  `{"type": "readOnly", "networkAccess": false}` is spelled — a renamed variant or a flipped default
  would otherwise turn network access back on silently.
- `TurnCompletedNotification` carries `TurnStatus` (`completed`/`interrupted`/`failed`/`inProgress`)
  and `TurnError.codexErrorInfo`, which is the closed vocabulary the turn runner maps to its
  rate-limited, signed-out and context-exceeded categories. A value added upstream must become an
  unmapped failure, not a silently mismatched one.
- `ItemCompletedNotification` carries `ThreadItem`, whose `agentMessage` variant is the only item
  the runner reads. If the final message stops arriving under that name, grading must fail rather
  than return nothing.
- `ConfigReadResponse` is what the hardening layer reads the *effective* MCP server names out of at
  runtime. Thread config is deep-merged, so `mcp_servers = {}` does not clear configured servers;
  each one has to be named and disabled, and the names come from here rather than from a constant.

These are the largest documents in the set — `ThreadStartResponse` and the item notifications inline
the whole `ThreadItem` union — which is the cost of the checks above being real.

## Refreshing after a Codex upgrade

1. Run `scripts/refresh-codex-schemas.py`.
2. Read the diff. A changed `methods/server-requests.json` means new server-initiated behaviour to
   classify. A changed `v2/GetAccountResponse.json` or `AccountRateLimitsUpdatedNotification.json`
   means the sanitised status snapshot may need to change with it.
3. Run the backend suite. Failures here are the review prompt, not an inconvenience to route around.
4. Update [ADR 0006](../../docs/adr/0006-codex-app-server-bridge.md) if a decision changed.
