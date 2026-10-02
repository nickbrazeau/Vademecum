# ADR 0012 — A local product each learner installs

- Status: accepted
- Date: 2026-09-30
- Amends: [0009](0009-hosted-personal-workspaces-chatgpt-is-the-model.md),
  [0010](0010-tenancy-by-workspace-per-learner.md), [0011](0011-the-desk-through-the-gateway.md)
- Restores, as the primary path: [0002](0002-local-first-boundary.md)

## Context

The pilot of the hosted product worked: accounts, isolation, the gateway, the tunnel. Using it
showed what it costs. A public address that changes, a sign-in page, an operator, and the
learner's material passing through a machine that is not theirs -- all so that ChatGPT's
servers can reach a Mac. Meanwhile the assistants a clinician actually has, Codex inside the
ChatGPT app and Claude Desktop, run on the Mac and can start an MCP server themselves, over
stdio, from one line in a configuration file they own.

The owner consolidated on that: **each learner runs Vademecum on their own Mac, in one folder,
with the assistant they already have, and nobody operates a server.** The product is a thing
you install, not a service you sign in to. It should still be replicable by others and, in
time, purchasable.

## Decisions

### 1. The unit of distribution is a local install

One checkout (later, one package), one data folder, one launcher. `scripts/install.sh` finds
or installs Python, creates the virtualenv, installs the API and the MCP server, builds the desk
when Node is present, and registers the launcher with whichever assistants are installed.
`scripts/mcp.sh setup codex` and `setup claude` do the registration alone: an entry in
`~/.codex/config.toml` or Claude Desktop's configuration, replacing any earlier Vademecum entry,
keeping everything else, and leaving a backup beside the file.

### 2. The assistant starts Vademecum, and Vademecum starts the API

In stdio mode the MCP server checks for the API on loopback and, if nothing answers, starts it
as a child process with its output in `<data dir>/logs/api.log`, waits for it, and stops it
when the assistant stops the server. A learner runs no command and leaves no terminal open.
The child runs in host mode unless told otherwise: the assistant on the other end of stdio is
the model, on the learner's own plan, and the machine holds no key (ADR 0009). The desk is the
API's own static serving on loopback, opened in a browser when files need adding.

### 2a. The source folder is how material comes in

Added the same day, after using the local mode: with no browser in the loop, a file needs a
way in. The installer asks for a **source folder** (default `~/Documents/Vademecum`) and lays
it out as `piles/highconfidence`, `piles/mediumconfidence` and `piles/lowconfidence`. A folder
inside a tier is a pile at that confidence; the tier folder *is* the rating, so moving the
folder re-rates the pile and nothing else has to be kept in step. The API scans the folder at
start, every twenty seconds while it runs, and on request through `sync_sources`, using the
same detection, extraction and content-addressed storage as an upload. A scan never deletes;
`remove_source` does, with a confirmation word. Export and backup became tools as well, since
they write into the learner's own records directory and return only a file name. The folder is
recorded in the one settings file both processes read. The web app remains for browsing and
the Improvement Map's graph, and is no longer required for anything.

### 3. Single tenancy is the product; the hosted mode is kept, optional

ADR 0010's tenancy, ADR 0011's gateway and ADR 0008's tunnel remain in the codebase, tested,
behind `VADEMECUM_TENANCY=multi` and the HTTP transport. They are how a group could run one
Vademecum for several learners, and how ChatGPT chat with its inline cards could reach one. They
are no longer the path the product takes to a learner, and the documents say which is which.

### 4. What replication means, and what purchase would need

*Replicate:* the checkout, `install.sh`, and the README's local section are enough for a
technically comfortable colleague today. A packaged release -- a wheel with the desk built in,
or a signed macOS application that bundles Python -- is the next step toward a clinician who
has never opened a terminal, and is deferred until the workflow itself has been used enough to
settle.

*Purchase:* nothing in this ADR decides pricing, licensing or a business entity; those are the
owner's decisions. What the design permits is stated so they can be made: a license is a
signed file the product checks offline, with no phone-home, because ADR 0002's egress rule
(PubMed only) holds for the local product; the free-versus-paid line, if any, is drawn in
features, not in data or in the model, since the learner's ChatGPT does the model work either
way.

## Consequences

- The primary boundary document is ADR 0002 again: loopback only, one egress, no key, no PHI,
  free text never logged. The additions of 0009 to 0011 apply to the hosted mode.
- AGENTS.md's product intent changes from "a hosted product" to "a product each learner
  installs", with the hosted mode as an optional deployment.
- The ChatGPT app's inline cards are only rendered by ChatGPT chat, which needs the hosted
  mode; Codex and Claude show tool results as text. That is accepted.
- The next engineering step toward a purchasable product is packaging, not features.
