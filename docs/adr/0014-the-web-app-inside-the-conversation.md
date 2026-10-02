# ADR 0014 — The web app inside the conversation

- Status: accepted
- Date: 2026-10-01
- Extends: [0008](0008-mcp-server-for-chat-hosts.md) (the cards), [0012](0012-a-local-product-each-learner-installs.md)
- Keeps: [0002](0002-local-first-boundary.md), [0009](0009-hosted-personal-workspaces-chatgpt-is-the-model.md)

## Context

The owner likes the web app and wants it to be what they see in Codex and Claude Desktop, not
a separate window. Two cards already exist (Today, Tutor) as MCP Apps: self-contained HTML the
host draws in a sandboxed frame and feeds with a tool's result. They are hand-written views of
one result each. The web app is five pages of React with its own data layer, built for a
browser tab where `fetch` reaches the API on loopback.

Inside a host's frame there is no network. Everything the app wants has to go through the host
as a tool call, and whatever tool it calls is, in some hosts, also visible to the model. The
MCP Apps specification lets a tool declare itself callable from the app only; ChatGPT's bridge
has no such distinction. So the shape of the one tool the app may call is the whole boundary.

## Decisions

### 1. One build, two places

The same React app is built twice: once as the site the API serves at `127.0.0.1:8765`, and
once as a single HTML document with its script and stylesheet inlined
(`apps/web/vite.app.config.ts`, `scripts/inline-app.mjs`), written to
`apps/mcp/src/vademecum_mcp/widgets/app.html` and committed, so an install without Node still
has it. The MCP server serves it as the resource `ui://vademecum/app.html` with the MCP Apps
media type and a content policy that allows nothing to be loaded from anywhere.

The in-chat entry (`src/app-main.tsx`) sets one switch, `markInChat()`. With it set: no service
worker, the route lives in component state rather than the frame's history, the Model page is
not offered, the upload panel says the source folder is the way in, and every request goes
through the host.

### 2. The host bridge speaks both dialects

`src/lib/host.ts` detects which host it is in. Under ChatGPT it uses `window.openai.callTool`
and the bridge's theme. Under an MCP Apps host it speaks JSON-RPC over `postMessage` to the
parent frame: `ui/initialize`, `ui/notifications/initialized`, `tools/call`, and
`ui/notifications/size-changed`, and it follows `ui/notifications/host-context-changed` for the
theme. The stylesheet's dark variables are repeated under `:root[data-theme="dark"]` at build
time so the host's choice, not the frame's media query, decides. In a browser tab neither bridge
is present and the app uses `fetch` exactly as before.

### 3. The app calls one tool, and the server decides what it may reach

`app_request(method, path, body)` forwards to the API on loopback for an explicit list of
routes mirroring `src/lib/api.ts`: reads of Today, piles, sources, points, Tutor, literature,
flags, model status; writes that the dashboard makes with the owner present (piles, flags,
specialty and map positions, Tutor advance/grade/reveal/self-assess, literature topics and
settings, build start/cancel/recheck, a folder sync). Not on the list, and refused with a
message that says where they are done instead: every `DELETE`, retiring material, export,
backup, file upload, ChatGPT sign-in and restart, and the workspace itself. Those remain the
browser dashboard's, the source folder's, or the assistant's own tools', each with its own
confirmation (ADR 0008).

The tool returns the API's status and body, or its refusal, as data, so the app shows the same
message the browser would. It is declared `visibility: ["app"]` for hosts that honour it and
`openai/widgetAccessible` for ChatGPT; its description tells a model that sees it anyway to use
the named tools. A model that calls it reaches nothing its own tools do not already reach.

### 4. The dashboard is the default: `open_vademecum` and the cover sheet both draw it

`open_vademecum(view)` is a read-only tool whose result is drawn by the app resource; it returns
the view to open on (today, tutor, sources, map) and nothing else, and the app loads what it
shows through `app_request`. The cover sheet, `get_today`, is drawn by the same resource, so
the hand-written Today card is retired; the Tutor card stays, because reveal and advance from
inside the card are its point. The server's instructions say to open the dashboard first
whenever the owner opens Vademecum or asks what is new; a prompt named `open_vademecum` does
the same for hosts that offer prompts as quick actions; and the source folder gets an
`AGENTS.md` saying so, for a learner who opens that folder as a Codex project. If the host
cannot draw it, `open_dashboard` opens the same web app in the browser.

## Consequences

- The dashboard is available three ways: in the browser at `127.0.0.1:8765`, in the chat where
  the host draws MCP Apps, and (as before) through the named tools. All three read the same
  records on the Mac.
- The in-chat build is a 314 KB document committed to the repository and regenerated by
  `npm run build`. A change to the web app is not in the chat until that runs.
- A host that does not render MCP Apps shows `open_vademecum`'s one-line result; the
  instructions tell the assistant to offer `open_dashboard` then.
- Building and grading started from inside the chat run in host mode exactly as from the
  browser: the API records a pending turn and the dashboard says it is waiting for the
  assistant, which does the turn through `build_pending` or `tutor_record_grade` when asked.
