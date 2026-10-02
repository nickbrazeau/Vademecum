# Vademecum

A private clinical-learning workspace that runs on your own Mac. It holds the material you learn
from, the gaps you notice while working, and a plain description of where those gaps are.

It is educational. It is not a clinical-decision-support tool, and nothing it shows is a substitute
for clinical judgment, current institutional guidance, or an appropriate specialist.

See [`AGENTS.md`](AGENTS.md) for the product boundaries and [`docs/adr/`](docs/adr/) for the
decisions behind this implementation. `Vademecum_arxive/` is the read-only predecessor: it is
reference material, never modified, and nothing here reads it at runtime.

## Running it

```sh
./scripts/dev.sh
```

That is the whole thing. The first run creates the Python virtualenv and installs node modules; every
run applies outstanding migrations, starts the API, **waits until `/health` actually answers**, and
only then starts the web app. If a step fails it stops and says which.

- `./scripts/dev.sh --check` — set everything up, prove `/health` answers, exit.
- `./scripts/dev.sh --api-only` — the backend alone.

Then open <http://127.0.0.1:5173>. To use the existing Vademecum browser address:

```sh
VADEMECUM_WEB_PORT=5174 ./scripts/dev.sh
```

Keep that terminal running, then open <http://127.0.0.1:5174>. Closing the terminal or stopping
the process stops the site and its literature checks; this is not a hosted service.

Requirements: macOS 14 or later, Python 3.12+, Node 20+. Supported browsers are desktop Safari 17+,
recent Chrome, and iPhone Safari 17+ are the target browsers
([ADR 0004](docs/adr/0004-supported-platforms.md)). Automated browser checks use installed Chrome
at desktop and narrow-phone widths. They are not a substitute for testing on an actual iPhone or
in Safari; those device checks remain a human acceptance step.

## What it does

- **Today.** The cover sheet: new literature with why it is relevant, the learning points worth a
  look with their sources beneath them, and what is held for review. Nothing counts down, nothing
  is owed.
- **Tutor.** Open questions built from your own sources, in a durable shuffled cycle — no repeats
  until the cycle is exhausted. Type an answer, press **Grade**, get reference-based feedback. No
  quotas, no streaks, no due counts. If the model is unavailable it says so, reveals the reference
  answer, and offers a clearly-labelled self-assessment instead.
- **Sources** (the Source library; `/piles` still works). Piles rated Low/Medium/High **source
  confidence** — your judgment of accuracy and usefulness, not how well you know it. Upload PDFs, PowerPoint
  decks, Word documents, text and Markdown. **Build learning material** shows you exactly which
  excerpts would be sent, then turns them into learning points and questions.
- **Knowledge Gap Flags.** `⌘K` from anywhere. One textarea, one required field.
- **Improvement Map.** Where the gaps are, drawn as a graph: a node per topic, sized by open
  flags, coloured by specialty (your assignment, or a match on the topic's own name), and linked
  where one learning point was filed under both topics. Drag, tap, pinch. Where things sit is
  remembered between opens. The full flag list stays underneath; the graph is a view, not the
  record.
- **Model connection.** Whether Codex is signed in to ChatGPT, the plan, and usage.
- **Export and backup.** Readable JSON, and a restorable bundle containing the database *and* every
  uploaded original, with a manifest and a `verify` route that checks it.

### First learning session

1. Open **Sources**, create a pile, choose its source confidence, and add files.
2. Review extraction warnings. Preview **Build learning material**, inspect the selected text and
   transmission disclosure, and confirm the batch. Continue with further batches for long documents.
3. Read the generated points and their citations. Only evidence-supported questions that pass the
   separate reference-answer assessment enter **Tutor**; drafts and unresolved conflicts remain held.
4. Open **Tutor** to answer, reveal the reference, grade or self-assess, and choose **Next question**.
5. Review suggested public topics in Sources, edit as needed, and add the ones you want to watch.
   **Check now** is manual; weekly checking is a separate opt-in and works only while the app runs.

### What is sent, and when

Model content is sent only after an explicit Build or Grade action. Public literature checks run
when requested, during a Build, or on the weekly schedule after you enable it. Account/usage checks
also contact OpenAI through Codex; they do not send your study material.

| You press | What is sent | Where it goes |
| --- | --- | --- |
| **Build learning material** | Selected excerpts, filenames, confidence labels and locators. Follow-up checks send generated claims and their context with retrieved abstracts, then questions, reference answers and rubrics with selected passages and abstracts. | OpenAI, through the Codex process on this Mac, on your ChatGPT sign-in |
| **Grade** | The question on screen, its reference answer and rubric, and the answer you typed. Nothing else. | Same |
| **Check literature**, a Build's evidence lookup, or an enabled weekly check | Short public topic phrases (derived during Build; reviewable/editable for watched topics) and public PubMed identifiers for retrieval/status checks. Never source excerpts, filenames or learner answers. | PubMed (NCBI E-utilities) |

No API key is used, ever. Consent is to *specific characters*: if a source is excluded, renamed,
re-rated or re-read between the preview and the send, the send is refused and you get a fresh
preview.

### What "verified" means here — and does not

A pile's Low/Medium/High is **source confidence**. It never makes a claim true.

- **Draft (source-supported)** — your material says it, at the cited location. Kept and shown.
  Tutor never asks it.
- **Evidence-supported, machine reviewed** — published literature was retrieved and a quote from
  its *abstract* was matched to the claim, and the question and its reference answer separately
  passed an assessment against that text. This is the strongest thing Vademecum will ever say. It
  is **not** verified, **not** human-approved, and **not** clinically validated.
- **Uncertain / conflicting** — held, with the reason shown.

A retraction, correction or expression of concern holds affected questions when a lookup discovers
it; excluding a source holds its questions immediately. Recovery requires an explicit successful
recheck against clean evidence. Prior evidence and attempts remain in the audit history. A failed
lookup or model check does not replace accepted material or advance processed-text coverage.
A newly discovered correction or retraction is still retained as a safety signal and can hold
affected questions even if a later stage of that build fails; the accepted reference text is kept.

### Intake and evidence limits

This version reads **extractable text**, plus, on a Mac, text it can **recognise on the device**
([ADR 0013](docs/adr/0013-pictures-on-device-reading-and-schematics.md)): a PDF page with no text
layer is drawn with Quartz and read with the Vision framework, a picture slide is read from its
pictures, and a `.png` or `.jpg` dropped into a pile is read directly. Recognised text is cited
with an `(OCR)` locator so you always know which quotes came off an image. Pictures inside PDFs,
decks and Word files are kept beside the text with the page or slide they were on, and the
assistant can look at them; Vademecum itself does not interpret a figure. PPTX slide text and
speaker notes are read with their locations. Legacy `.ppt`/`.doc` files must first be exported as
`.pptx`/`.docx` or PDF. Coverage measures extracted and recognised text.

PubMed verification uses available **abstracts**, not paywalled full text or an exhaustive guideline
review. Missing or insufficient evidence leaves material out of Tutor. New-publication alerts are
relevance candidates, not a declaration that practice has changed. No generated material is
clinically validated by these automated checks. Historical content from the old Vademecum is not
automatically imported into this rewrite.

## Where your data lives

One directory, outside this checkout, chosen by `VADEMECUM_DATA_DIR`
([ADR 0003](docs/adr/0003-data-directory-layout.md)). By default:

| Platform | Default |
| --- | --- |
| macOS | `~/Library/Application Support/Vademecum` |
| Other | `$XDG_DATA_HOME/vademecum`, else `~/.local/share/vademecum` |

```text
<data dir>/
  vademecum.sqlite3      canonical database (WAL mode)
  attachments/sources/   stored originals, named by content digest
  exports/               human-readable JSON snapshots
  backups/               database + originals + manifest ZIP bundles
  logs/                  reserved; redacted diagnostics only
```

Use the in-app backup action for a consistent database plus originals, and verify the resulting
bundle before relying on it. A plain copy of a live SQLite file can miss WAL transactions. The
data directory contains the durable study records; keep it and its backups separate from code.
Startup refuses a data directory inside the source tree, before it opens the database.

## The privacy boundary

Stated in [ADR 0002](docs/adr/0002-local-first-boundary.md) and enforced by tests, not by prose:

- **Loopback, with no opt-out.** `VADEMECUM_HOST` is `127.0.0.1` and nothing else is accepted, not
  even another loopback address. Anything else is a refusal to start, not a warning in a log. There
  is no setting that changes this: AGENTS.md lists it among the non-negotiable boundaries, and a
  boundary with an off switch is not a boundary.
- **One narrow, deliberate egress.** `apps/api/src/vademecum/literature/http.py` is the whole of
  Vademecum's own network code: `http.client` over TLS to **one allowlisted host**
  (`eutils.ncbi.nlm.nih.gov`), GET only, redirects never followed, bounded response body, throttled
  and narrowly retried, failures reported as categories rather than provider text. Only a short
  public topic phrase is sent, derived during Build or read/editable as a watched topic. The rest of the backend imports no HTTP client,
  and the frontend calls only same-origin `/api`.
- **Two explicit transmissions, each with the payload shown first.** Build and Grade send what the
  table above says, through the local Codex child.
- **Loopback is not the same as same-origin.** `Host` is checked on **every** request, because DNS
  rebinding reaches reads too, and `Origin` on every write, with an explicit `null` origin refused
  rather than waved through. Malformed headers are a 403, never a 500. There is no CORS middleware
  anywhere.
- **Tools are disabled explicitly, not assumed off.** A read-only sandbox does not disable tools.
  The Codex child is started with the shell, exec, browser, computer-use, apps, plugins, hooks,
  multi-agent, code-mode, image, memory and skill features off, `web_search="disabled"`, telemetry
  exporters off, and every configured MCP server disabled *by name* — `mcp_servers={}` does not
  clear a deep-merged config. Turns run in a dedicated empty working directory, not the data
  directory and not this checkout. Any unexpected item on the thread fails the turn closed.
- **Every content turn re-checks the credential.** `account/read` is re-read per turn and anything
  that is not a ChatGPT account is refused before a thread is started.
- **No API key, ever, and no silent fallback to one.** Codex holds a ChatGPT-managed credential and
  makes the upstream request. Vademecum never sees a token. The App Server's schema also offers
  API-key login; the bridge cannot construct it, an account signed in that way reads as *signed
  out, with a reason*, and a test restricts the string `apiKey` to the two files that exist to
  refuse it.
- **No production model call in the test suite, and no real Codex either.** Every bridge test runs
  against a scripted fake transport, and an autouse fixture makes `create_subprocess_exec` raise so
  no test can spawn `codex app-server` by accident.
- **The device code is shown once and stored nowhere.** The verification URL, the one-time code and
  the login id come back in one `no-store` response, live in the page for as long as you are
  looking at them, and go into no log, no database and no browser storage. Cancelling needs no
  body: the pending sign-in is held by the backend.
- **The service worker caches the static shell and nothing else.** The policy is a pure function
  (`apps/web/src/sw/cachePolicy.ts`) so it can be asked directly. It is an *allowlist*: `/`,
  `/index.html`, the manifest, the named icons, and hashed `/assets/` build output. Every other
  path — `/api`, `/attachments/…`, `/exports/…`, a client route — is refused, along with non-`GET`,
  cross-origin, opaque, non-200, query strings, and anything marked `no-store`. The server marks
  every `/api` response `no-store` as well.
- **No PHI.** Free-text inputs carry a short warning. Nothing tries to detect identifiers: a
  detector that is wrong in either direction is worse than a clear rule.
- **Free text is never logged.** Request logs carry a method, path, status, duration and a
  correlation id. Responses carry no filesystem paths.
- **Browser storage holds only unsaved drafts.** Safari evicts origin storage; the database on the
  Mac is the canonical copy, always.

Reaching the web app from an iPhone needs a private tunnel you set up deliberately, forwarding to
`127.0.0.1` on this Mac. That friction is intended; it is not configured here. Using Vademecum
*through ChatGPT or Claude* on a phone is a different path, with its own boundary, described next.

## Using it from Codex or Claude on this Mac

This is the product ([ADR 0012](docs/adr/0012-a-local-product-each-learner-installs.md)): one
install, one folder, and the assistant you already have doing the model work on its own plan.

```sh
./scripts/install.sh
```

That finds or installs Python, installs Vademecum, asks where your **source folder** should be
(default `~/Documents/Vademecum`) and lays it out, and registers the launcher with Codex (the
ChatGPT app) and Claude Desktop, whichever are installed. Restart the assistant and ask it for
your Vademecum cover sheet. The assistant starts the MCP server over stdio, the MCP server starts
the API beside it if nothing is listening, and both stop when the assistant does. Nothing listens
anywhere but `127.0.0.1`, no address is public, and the API runs in host mode: the assistant is
the model, and this Mac holds no key.

**The source folder is how material comes in.** No browser is needed:

```text
~/Documents/Vademecum/
  README.txt
  piles/
    highconfidence/    Sepsis guideline/  guideline.pdf
    mediumconfidence/  ICU lectures/      week-1.pptx  week-2.pptx
    lowconfidence/     Old notes/         scribbles.md
```

Each tier folder is a source-confidence rating; each folder inside it is a pile. Vademecum reads
the folder at start, every twenty seconds while it runs, and whenever you ask the assistant to
sync. Move a pile's folder to another tier to re-rate it. Removing a file from the folder does
not remove it from Vademecum; ask the assistant to remove the source, and it will ask you to
confirm. Ask the assistant where the folder is and it tells you. Export and backup are tools too;
they write into the records directory and return a file name.

**Pictures and schematics.** Pictures found inside your files are kept, and a picture file in a
pile is a source of its own. Ask the assistant to list a source's images and to look at one: it
sees the figure and discusses it with you in your learning terms. Pages with no text layer are
read on this Mac, never elsewhere. When a learning point would be clearer as a drawing, ask the
assistant to draw it: the schematic is checked, filed against that point, and copied into your
source folder under `schematics/<pile>/` as an SVG you can open anywhere. A schematic carries its
point's support label and is never itself "verified".

To change or repeat any step by hand:

```sh
./scripts/mcp.sh setup folder ~/Documents/Vademecum   # choose and lay out the source folder
./scripts/mcp.sh setup codex     # writes [mcp_servers.vademecum] in ~/.codex/config.toml
./scripts/mcp.sh setup claude    # writes mcpServers.vademecum in Claude Desktop's configuration
```

The folder choice is recorded in `~/Library/Application Support/Vademecum/settings.env`, which
both processes read; a variable in the environment still wins. The registrations keep everything
else in the assistant's file and leave a backup beside it.

**The dashboard.** The web app is the dashboard: Today, the source library, the Tutor, the
Improvement Map as a graph, and the model status. It is available two ways from the assistant
([ADR 0014](docs/adr/0014-the-web-app-inside-the-conversation.md)):

- **Inside the conversation, by default.** Where the assistant can draw MCP Apps (Codex does;
  Claude Desktop does; ChatGPT does), opening Vademecum means seeing it: the cover sheet and the
  `open_vademecum` tool are both drawn as the web app, right there in the chat, and the
  assistant is instructed to open it first. Ask for the Tutor, your sources or the map and it
  opens on that page. Open your source folder as a Codex project and its `AGENTS.md` says the
  same. It is the same app built into one document; everything it shows it asks the host for,
  and the host asks Vademecum on this Mac. Files still come in through the source folder, and
  removing, exporting, backing up and signing in stay in the browser dashboard or with the
  assistant's own tools, each of which confirms with you.
- **In your browser.** The API serves the web app at <http://127.0.0.1:8765/> whenever Vademecum
  is running and the app has been built (`install.sh` builds it when Node is present;
  `cd apps/web && npm run build` does it by hand, and also rebuilds the in-chat document). Ask
  the assistant to "open the dashboard" and the `open_dashboard` tool opens that page. Nothing is
  served anywhere but this Mac.

In development, `./scripts/dev.sh` runs the web app on <http://127.0.0.1:5173> instead, and a
running API is simply reused by the assistant.

## Optional: the hosted mode, for ChatGPT chat, phones, or several learners

Everything below is kept and tested but is not the path the product takes to a learner. It is
how ChatGPT chat (with the inline cards), a phone, or a group sharing one Vademecum can reach it:
the same MCP server over HTTPS behind a tunnel you run
([ADR 0008](docs/adr/0008-mcp-server-for-chat-hosts.md)).

**Read this first.** Everything a tool returns -- a learning point, a passage from your notes, a
flag, a Tutor question and the answer you typed -- becomes part of that assistant's conversation
and is processed by its provider (OpenAI for ChatGPT, Anthropic for Claude). Vademecum still sends
your material onward only through Build and Grade, exactly as the table above says; but the chat
host has already read whatever you asked it to fetch. Nothing on this path is "local".

### Setup

```sh
./scripts/dev.sh                 # the API, as always, in one terminal
./scripts/mcp.sh passphrase      # once: the passphrase that approves a connection
./scripts/mcp.sh --tunnel        # the MCP server behind a Tailscale Funnel, in another terminal
```

`--tunnel` works out the public HTTPS name from your Tailscale identity, prints the endpoint
(`https://<your-mac>.<tailnet>.ts.net/mcp`), and removes the funnel when you stop it. Funnel has
to be enabled for your tailnet; Tailscale's admin console says how. Any other tunnel works too:
set `VADEMECUM_MCP_PUBLIC_URL` to the HTTPS origin it presents and run `./scripts/mcp.sh` alone.

Then connect an assistant:

- **ChatGPT.** Settings → Apps & Connectors → Advanced → enable developer mode → Create. Enter the
  endpoint, choose OAuth, and approve on the page that opens with your passphrase.
- **Claude** (claude.ai, the desktop app, the iPhone app). Customize → Connectors → Add custom
  connector. Enter the endpoint, leave the OAuth client on "Register automatically", connect, and
  approve with your passphrase.
- **Claude Code** in this checkout picks the server up from `.mcp.json` over stdio, with no tunnel
  and no passphrase: the process is started by you, on this Mac.
- **Claude Desktop, locally.** Add to its MCP configuration:

  ```json
  {"mcpServers": {"vademecum": {"command": "/absolute/path/to/Vademecum/scripts/mcp.sh",
                                "args": ["--stdio"]}}}
  ```

`./scripts/mcp.sh status` shows the endpoint, whether a passphrase is set, and which assistants
are registered. `./scripts/mcp.sh revoke-all` signs every one of them out; the passphrase stays.

### The cards

In ChatGPT, Today and every Tutor result are drawn as inline cards as well as described in
words: the cover sheet with what is worth a look and where things stand, and the question with
its cycle position, then the reference and the feedback once graded. The cards are complete as
served, with no external asset and no network of their own; they can ask for the reference and
move to the next question, and nothing else. Grading stays in the conversation.

### What a connected assistant can do

Show the dashboard inside the conversation, or open it in the browser; read Today and the
Improvement Map; list piles, sources and learning points; read a source's
extracted text; list and look at the pictures kept from a source; add a pasted note as a source;
keep a schematic it drew for a learning point and read it back; file and update Knowledge Gap
Flags; preview and start a build, check it, cancel it; ask the next Tutor question, reveal the
reference, grade an answer, or record a self-assessment; watch and check literature topics; read
the model status.

It cannot sign in to ChatGPT (the device code stays on this Mac), delete anything, retire
material, or add a file itself: files come in through the source folder on the Mac, or through
the web app in the hosted mode.

Before a build, the assistant is instructed to show you the same disclosure and excerpt list the
web app shows and to wait for your yes; the API refuses a send whose preview no longer matches the
material, exactly as it does for the web app. Both hosts also ask you before running a tool that
is not read-only.

### Host mode: ChatGPT does the model work

Set `VADEMECUM_MODEL_PROVIDER=host` and this process never calls a model
([ADR 0009](docs/adr/0009-hosted-personal-workspaces-chatgpt-is-the-model.md)). Every turn a
build or a grade needs -- synthesis, evidence, assessment, grading -- becomes a *pending turn*:
exactly what the model may look at (the same instructions, rules, fenced material and output
schema Codex is given in the default mode) and a deadline. The assistant reads it from a tool
reply, does the work in the conversation, and submits one JSON object. Vademecum validates it
against the schema, checks every quote against the material and every evidence quote against the
abstract it retrieved, and only then continues. Nothing the assistant submits can mark a claim
supported; support is computed afterwards from records it cannot write.

In this mode Build and Grade in the web app say where the work happens, and a build started
there waits until an assistant continues it. Pending turns live in `pending_turns`, expire after
thirty minutes (`VADEMECUM_HOST_TURN_TTL`), and are abandoned by a restart, which fails the run
honestly. The default mode, `codex`, is unchanged: the owner's Mac deployment keeps grading and
building through the local Codex child.

### Multi tenancy: other learners, each in a workspace of their own

`VADEMECUM_TENANCY=multi` (which requires host mode) turns this into the hosted product of
[ADR 0010](docs/adr/0010-tenancy-by-workspace-per-learner.md). Each learner gets a directory
under `learners/` with their own database, originals, exports and backups; a request can only
ever open the workspace its token belongs to, and there is no query anywhere that takes a
learner as a parameter. Accounts are by invitation:

```sh
VADEMECUM_TENANCY=multi VADEMECUM_MODEL_PROVIDER=host ./scripts/dev.sh --api-only
VADEMECUM_TENANCY=multi ./scripts/mcp.sh --tunnel
./scripts/mcp.sh invite          # a one-time code, valid seven days, shown once
./scripts/mcp.sh learners        # handles only, never material
./scripts/mcp.sh disable HANDLE  # stops sign-in, revokes tokens, keeps the workspace
```

The first time a learner connects an assistant, the sign-in page asks for the invite code, a
handle and a passphrase of their choosing; afterwards, handle and passphrase. Five wrong
attempts lock that handle for fifteen minutes. A learner deletes their whole workspace with one
confirmed request, which also signs their assistant out; nothing is removed from their ChatGPT
conversation history, and the reply says so. The owner's Codex connection is unreachable in this
mode, and a health check opens nobody's database.

**The desk.** In multi tenancy the same public host serves the web app
([ADR 0011](docs/adr/0011-the-desk-through-the-gateway.md)): a learner signs in at `/login`
with the same handle and passphrase (or invite code, the first time) and gets an `HttpOnly`,
`SameSite=Strict` session cookie; the web app's API calls are proxied to the API on loopback with
that session's token, file transfers streamed through. Build `apps/web` first (`npm run build`), or
point `VADEMECUM_MCP_DESK_DIST` at a build. Sign-out and workspace deletion both end the
session. The owner's Model page is hidden there, because there is no model connection to show.

### The boundary, on this path

- The MCP server binds `127.0.0.1` only, like the API, and accepts requests only for the public
  name you configured and loopback; any other `Host` is refused.
- Connections are OAuth 2.1 as the MCP specification requires: PKCE, dynamic registration, one
  scope, tokens bound to this one endpoint. Approval is your passphrase, hashed with scrypt; five
  wrong tries lock the page for fifteen minutes. Tokens are random, stored as digests in
  `<data dir>/mcp/access.sqlite3` (mode `0600`), never in the study database and never in a
  backup. Access tokens last an hour; refresh tokens rotate, and a replayed one signs the whole
  connection out.
- No API key, still. These tokens are issued by Vademecum to an assistant; they are not provider
  credentials. A test reads the package for a provider endpoint or a key and fails if it finds one.
- Free text is never logged by this process either, and no tool result carries a filesystem path.

Listing Vademecum in the ChatGPT App Directory or the Claude Connectors Directory is a separate
step: a verified developer account, a domain you control, a privacy policy and a review. This
checkout supports the personal path and does not attempt a listing.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `VADEMECUM_HOST` | `127.0.0.1` | Bind address. `127.0.0.1` is the only accepted value. |
| `VADEMECUM_PORT` | `8765` | API port. |
| `VADEMECUM_DATA_DIR` | platform default | Where everything durable lives. |
| `VADEMECUM_WEB_PORT` | `5173` | Development web-app port (`scripts/dev.sh` only). |
| `VADEMECUM_CODEX_PATH` | first Codex found | The Codex CLI the bridge manages. Tried in order: the ChatGPT app's copy, `/usr/local/bin`, Homebrew, `~/.local/bin`. |
| `VADEMECUM_APPSERVER_REQUEST_TIMEOUT` | `30` | Seconds to wait for an App Server reply. |
| `VADEMECUM_APPSERVER_STARTUP_TIMEOUT` | `20` | Seconds to wait for `initialize`. |
| `VADEMECUM_SOURCES_DIR` | `~/Documents/Vademecum` | The source folder (single tenancy): `piles/<tier>/<pile>/` becomes piles and sources. Recorded by `setup folder`. |
| `VADEMECUM_SOURCES_FOLDER_ENABLED` | `true` | Set `false` to stop scanning any folder. |
| `VADEMECUM_SOURCES_SCAN_INTERVAL` | `20` | Seconds between scans while the API runs. |
| `VADEMECUM_TENANCY` | `single` | `single`: one owner, one workspace, no identity. `multi`: a workspace per learner under `learners/`, every request identified by its token; requires host mode. |
| `VADEMECUM_MODEL_PROVIDER` | `codex` | Who does the model work: `codex` (the local Codex child) or `host` (the assistant, through pending turns; this process never calls a model). |
| `VADEMECUM_HOST_TURN_TTL` | `1800` | Seconds a pending host turn may wait for the assistant before the run fails. |
| `VADEMECUM_LITERATURE_NCBI_KEY` | unset | NCBI's E-utilities courtesy key: a public database's rate-limit identifier (ten requests a second instead of three), shared by every workspace in the process. Not a model credential. |
| `VADEMECUM_MCP_PORT` | `8766` | MCP server port, on `127.0.0.1` (`scripts/mcp.sh`). |
| `VADEMECUM_MCP_PUBLIC_URL` | unset | The HTTPS origin the tunnel presents; required in HTTP mode. `--tunnel` sets it. |
| `VADEMECUM_MCP_API_TIMEOUT` | `240` | Seconds the MCP server waits for the API; longer than a grading turn. |
| `VADEMECUM_MCP_ACCESS_TOKEN_TTL` | `3600` | Seconds an assistant's access token lives. |
| `VADEMECUM_MCP_DESK_DIST` | `apps/web/dist` if built | The built web app the gateway serves at its root in multi tenancy. |
| `VADEMECUM_MCP_DESK_SESSION_TTL` | `1209600` | Seconds a desk session lasts (14 days). |
| `VADEMECUM_MCP_REFRESH_TOKEN_TTL` | `2592000` | Seconds a refresh token lives (30 days). |

Codex is optional. If it is not installed the Model page says so in one sentence; nothing else in
Vademecum changes, and startup is unaffected.

## Tests

```sh
cd apps/web
npm test
npm run typecheck
npm run build
cd ../api
.venv/bin/python -m pytest
```

Install the API development dependencies (`.venv/bin/python -m pip install -e '.[dev]'` from
`apps/api`) after updating an existing checkout. The real-browser tests require Playwright from
that development extra and installed Google Chrome. They use an isolated temporary database,
fake model/provider replies, and a fresh frontend build; missing prerequisites fail explicitly.

The backend suite also runs from this directory, which is what a tool that starts at the repository
root will do:

```sh
apps/api/.venv/bin/python -m pytest              # same suite, from the rewrite root
apps/api/.venv/bin/python -m pytest --ignore=apps/api/tests/test_browser_flows.py  # backend-only
```

The MCP server's suite (`apps/mcp/tests`) is collected by the same root-level run. It builds the
real API application in-process and drives the tools and the OAuth flow through it with no
socket, no process and no model call.

`pytest.ini` here is what makes that safe: it scopes collection to the two test trees and keeps
`Vademecum_arxive/` — the read-only predecessor — out of it entirely. Without it, a root-level run
walks into the archive and tries to import it.

`python -m pytest`, not the bare console script: `pip install` bakes an absolute interpreter path
into `.venv/bin/*` at install time, so those break when the checkout moves.

What the suites are actually for:

- `apps/api/tests/test_privacy.py` — posts a marker string through the API and asserts it appears in
  no log line or error body; asserts no response carries a filesystem path; restricts external
  network code to the allowlisted literature provider; and asserts these documents describe the bridge as
  reaching OpenAI through Codex rather than claiming there is no network traffic at all.
- `apps/api/tests/test_migrations.py` — pins tables and columns, proves upgrades preserve older
  notes and flags, and asserts an edited migration refuses to start.
- `apps/api/tests/test_export_backup.py` — asserts an export is readable JSON of every table, and
  that a backup taken mid-write contains committed rows only and passes `integrity_check`.
- `apps/api/tests/test_dev_script.py` — reads `scripts/dev.sh` and runs its supervision loop
  under the system Bash: fails if the script uses anything newer than the Bash 3.2 macOS ships
  (`wait -n`, `mapfile`, associative arrays, …), or if the loop stops watching either child.
- `apps/web/tests/cachePolicy.test.ts` and `serviceWorker.test.ts` — the cache rules directly, and
  then the worker itself: an `/api` request is not even responded to, let alone stored.
- `apps/web/tests/sources.test.ts` — fails the build if the vocabulary the product refuses to use
  ("streak", "review queue", "due count", …) appears anywhere in the interface, if any external
  origin, provider endpoint or key appears outside the explicit citation/disclosure allowlists,
  or if `fetch` is called outside the API client.
- `apps/api/tests/test_appserver_client.py` — the bridge against a scripted transport: initialise
  exactly once under concurrency, ids correlated by type, every server-initiated request answered
  exactly once, malformed lines survived, timeouts and cancellations releasing their slot, a dead
  child failing everything waiting, restart re-initialising, stderr counted and never logged, and
  no task left running after shutdown.
- `apps/api/tests/test_appserver_schemas.py` — the bridge against the *generated* schema in
  `schemas/codex-app-server/`, including an equality between its server-request table and the
  schema's own list, so a Codex upgrade that adds one fails here.
- `apps/api/tests/test_model_routes.py` and `apps/web/tests/model.test.tsx` — the four routes and
  every interface state, including that a signed-in response contains no `@` anywhere and that the
  one-time code reaches neither storage nor the logs.

## Deliberately absent

No containers, cloud hosting, embeddings, analytics, separate API keys, or model/provider fallback.
Historical-data migration is not part of this slice; text recognition runs only on the device, and
interpreting a figure is the assistant's work, not Vademecum's. The web app does not yet show
pictures or schematics. The web app is not reachable from a phone; ChatGPT and Claude are, through the MCP server and a tunnel you
run ([ADR 0008](docs/adr/0008-mcp-server-for-chat-hosts.md)). Nothing here reads `Vademecum_arxive/` at runtime. Tests use fake model/provider replies;
a live model-content session is a separate owner-approved acceptance check.

## Layout

```text
apps/api/src/vademecum/
  config.py          settings, data-directory resolution, the loopback refusal
  db/                connection factory and the migration runner
  db/migrations/     NNNN_slug.sql, forward-only, checksummed
  storage/           all SQL; returns domain objects
  appserver/         the Codex bridge: protocol, transport, client, account facade
  api/               all HTTP; owns no SQL and no protocol
apps/web/src/
  lib/api.ts         the only thing that talks to anything
  sw/cachePolicy.ts  what may be cached, as a pure function
  components/, pages/
apps/mcp/src/vademecum_mcp/
  api_client.py      the one HTTP client, to 127.0.0.1 and nowhere else
  tools/             every tool, one API call each
  auth/              the OAuth provider, the token store, the consent page
  server.py          instructions, tool registration, the HTTP application
schemas/codex-app-server/   generated, version-pinned App Server contract
docs/adr/            the decisions, and why
scripts/dev.sh       the one command
scripts/mcp.sh       the MCP server, for ChatGPT and Claude
scripts/refresh-codex-schemas.py   regenerate the pinned contract
```

Storage, HTTP and the App Server bridge are kept apart on purpose. The bridge is wired in at the
HTTP layer and does not reach into storage; storage does not know it exists.

## The Codex contract

`schemas/codex-app-server/` is generated by the installed Codex CLI and never edited by hand
([its README](schemas/codex-app-server/README.md) records the provenance). After a Codex upgrade:

```sh
scripts/refresh-codex-schemas.py --check    # report drift, write nothing
scripts/refresh-codex-schemas.py            # regenerate, then read the diff
```

The backend suite reads those files. A change under the account or envelope surface — or a new
server-initiated request the bridge has not classified — fails a test, which is the point.
