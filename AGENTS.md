# Vademecum

## Product intent

Vademecum is a personal clinical-learning workspace that a learner installs on their own Mac
and uses from the assistant they already have -- Codex in the ChatGPT app, or Claude Desktop --
on that assistant's own plan. It holds the material they learn from, the learning points and
questions built from it, the gaps they notice while working, and a plain description of where
those gaps are. It is a product each learner installs, made to be replicated and, in time,
purchased; nobody operates a server for it (ADR 0012).

What it combines:

- an MCP server the assistant starts itself, over stdio, for everything: Today, Tutor, flags,
  the map, builds, grading, syncing the folder, export, backup;
- a **source folder** the learner chooses at install, `piles/highconfidence`,
  `piles/mediumconfidence` and `piles/lowconfidence`, one folder per pile inside a tier: the
  folder is how material comes in and the tier folder is its rating. No browser is needed;
  the web app on loopback remains for browsing and the Improvement Map's graph;
- one records directory on the learner's Mac, with export and backup;
- deterministic verification of everything the model produces: quotes, identifiers, schemas,
  support levels; and
- **the learner's assistant as the model.** It does every model turn inside its own
  conversation. Vademecum never calls a model provider and never holds a key.

An optional hosted mode (ADRs 0009 to 0011) lets one Vademecum serve several learners behind a
public address, which is also the only way ChatGPT chat and its inline cards can reach it. It is
kept and tested; it is not the path the product takes to a learner.

This is an educational product, not a medical device or patient-specific clinical
decision-support system. Never describe its output as a substitute for clinical judgment, current
institutional guidance, or consultation with an appropriate specialist.

## Non-negotiable boundaries

1. **No API, ever.** No OpenAI or Anthropic API key, no provider account, no model call from
   Vademecum's own code, and no provider abstraction that could hold one. Every model turn is an
   exchange between the learner's assistant and the tools. A test reads the whole codebase for a
   provider endpoint or a credential and fails on either. (The owner's original Codex-bridge
   mode still exists for the owner's Mac; it, too, holds no key.)
2. **Local first, one folder.** The product binds `127.0.0.1` and nothing else; the learner's
   data is one directory on their Mac; the only egress is PubMed. Personal workspaces only: no
   sharing, publishing, cohorts, leaderboards or author-curated libraries. In the optional hosted
   mode each learner has a directory and a SQLite database of their own and a request's
   connection can open only that one (ADR 0010); the storage layer does not know tenancy exists,
   and the operator has no interface that reads a learner's content.
3. **The model judges; the server checks.** A submission from ChatGPT is accepted only after
   schema validation with `additionalProperties: false`, and a citation only if its quote appears
   verbatim in the stored text it names; an evidence relation only if its quote appears in the
   abstract body the server itself retrieved, and its PMID is one the server retrieved. Support
   levels are computed by the server from those records. No schema has a field in which a model
   can assert support, verification or certainty.
4. **One egress.** The server's only outbound network call is to the single allowlisted PubMed
   host, carrying short public topic phrases and public identifiers. Never a passage, a filename,
   a flag or an answer.
5. **Explicit about the conversation.** Everything a tool returns is part of the learner's ChatGPT
   conversation, under OpenAI's terms for their edition. The documents, the app's instructions and
   the desk say so, and never call that path local or private.
6. **Consent to specific characters.** A build sends nothing until the learner has seen the exact
   excerpts, and `build_start` quotes back the preview's hash; a changed source, rating, exclusion
   or batch invalidates it. Unchanged from ADR 0007, now with the learner reading the preview in
   ChatGPT.
7. **No PHI.** Do not solicit or store names, dates of birth, medical-record numbers or other
   patient identifiers. Show a concise warning near free-text inputs; refuse patient-specific
   requests with the educational boundary and ask for a general restatement. Nothing tries to
   detect identifiers. No Business Associate Agreement is offered.
8. **Free text is never logged.** Diagnostics carry event names, timings, status codes and
   generated correlation ids. No response carries a filesystem path, an account identifier or
   another learner's anything.
9. **Deletion is real, export is readable.** A learner can export everything as JSON and delete
   everything, originals included. Deleting a workspace does not delete a ChatGPT transcript, and
   the desk says so.
10. **Isolation is tested, not asserted.** Learner A with a valid token can read, write or delete
    nothing of learner B's through any route or tool. This test class ships with tenancy, not
    after it.

### What is sent, and by whom

| Action | What leaves where | Who sends it |
| --- | --- | --- |
| Any tool call | The tool's result enters the learner's ChatGPT conversation | ChatGPT, on the learner's license |
| `build_preview` → `submit_learning_points` | Consented excerpts go to ChatGPT in the result; points and questions come back as a submission the server validates | ChatGPT; the server checks |
| `evidence_pending` → `submit_evidence`, `assessment_pending` → `submit_assessment` | Claim, context, passage and retrieved abstract text go out in a result; a judged relation or verdict comes back | Same |
| `tutor_grade` → `record_grade` | Question, reference answer, rubric and the learner's typed answer go out; a structured grade comes back | Same |
| Literature check, build evidence lookup, weekly check | Short public topic phrases and public PMIDs | Vademecum's server, to PubMed only |
| Sync, only when a second Vademecum is configured (ADR 0015) | The learner's own workspace, rows and files, both ways | Home, to the learner's own away node, over HTTPS with the token they set |

Vademecum's server sends nothing to any model provider. It does not have one.

### Tier confidence is not verification

A pile's `low`/`mid`/`high` (shown as Low/Medium/High) is **tier confidence**: the learner's
judgment of the material. Never mastery, priority, difficulty or evidence. Support is decided
separately and mechanically:

- `source_supported` — the material says it at the cited, verified location. Shown, never asked.
- `evidence_supported` — an abstract the server retrieved carries a quote, from its body, that
  ChatGPT matched to the claim and the server found verbatim. The strongest thing that may ever be
  said is *"evidence-supported, machine reviewed"*: never verified, never human-approved, never
  clinically validated. The machine is the learner's ChatGPT, and the copy says so.
- `uncertain` / `conflicting` — held, with the reason shown.

Tutor asks only `evidence_supported` questions whose question, reference answer and rubric were
separately judged `sound` against the cited passage and the retrieved evidence, and whose anchors
still match the stored text. A retraction, correction, expression of concern or excluded source
holds the question immediately; nothing auto-releases.

## Target architecture

The product, on one learner's Mac (ADR 0012):

```text
Codex (ChatGPT app) or Claude Desktop          ~/Documents/Vademecum/piles/<tier>/<pile>/
   |  starts it, stdio; draws the web app          |  scanned at start, every 20 s, on request
   |  in the chat as an MCP App (ADR 0014)         |
   v                                               v
Vademecum MCP server  --starts if absent-->  Vademecum API  -->  records directory
   (tools; no auth needed: the                     |             (database, originals, pictures,
    process is the boundary)                       |              schematics, exports, backups)
                                                   | text-less pages read on the Mac (Vision)
                                                   | one allowlisted host, short public phrases
                                                   v
                                          PubMed (NCBI E-utilities)
```

There is no model box. The model is the learner's assistant, and it is only ever on the other
side of a tool call.

The optional hosted mode (ADRs 0009 to 0011), one Vademecum for several learners:

```text
ChatGPT chat (with cards) / phones           Learner's browser
   |  MCP over HTTPS through a tunnel           |  the desk through the gateway (session cookie)
   v                                            v
Vademecum MCP server + gateway  (issues tokens; accounts by invite; serves the desk)
                 |  X-Vademecum-Token, loopback only
                 v
Vademecum API (opens only that learner's workspace under learners/<id>/)
```

Defaults, unless an ADR records a better choice: Python 3.12 with FastAPI; the MCP reference SDK
for the app's protocol and OAuth 2.1 endpoints; TypeScript, React and Vite for the desk; one
SQLite database per learner with forward-only, checksummed migrations; content-addressed
originals in the learner's own directory; SQLite full-text search before any embedding.
Postgres, object storage and a job queue are deferred until one volume is not enough (ADR 0010).

### The transition from the current checkout

What exists today is the single-owner Mac workspace (ADRs 0001 to 0007) plus the MCP server
(ADR 0008). It keeps working throughout, as the owner's own deployment and the first tenant.

| Stays | Changes | Goes (phase 5) |
| --- | --- | --- |
| Storage rules, consent hashing, quote and evidence checks, support computation, Tutor cycle, export and backup shapes, the whole storage layer | One workspace directory per learner under `learners/` (`tenancy.py`, `VADEMECUM_TENANCY=multi`; landed in phase 2) | The Codex App Server bridge (`appserver/`), ADR 0006 |
| `model/schemas.py` and `model/prompts.py`, as the contract for submissions | Codex turns → pending/submit tools (`model/host.py`, `VADEMECUM_MODEL_PROVIDER=host`; landed in phase 1) | The loopback-only rule, for the hosted service only |
| The MCP server, its tools, its OAuth server | Owner passphrase → learner accounts by invite, tokens naming the learner (landed in phase 2); Claude and stdio become developer conveniences | Any tool or route that returns a device code |
| The React app | Local dev server → hosted desk, with account, export and deletion | |

## The ChatGPT-as-the-model contract

Every model turn is two tools: a *pending* tool that returns exactly what the model may look at,
and a *submit* tool that accepts a structured result. The rules:

1. **The pending result is fenced and labelled.** Material is delimited, carries opaque handles
   (`E1`, `E2`, never an id or a filename path), and is described as data to analyse, never as
   instructions. The server instructions say the same in their first sentences.
2. **The submit tool validates against the same schema Codex was constrained by.** Enums, maximum
   lengths, required fields, `additionalProperties: false`. A refused submission names the field
   and echoes nothing.
3. **Quotes are checked, not trusted.** Citation quotes against stored segment text at the named
   handle; evidence quotes against the abstract body the server retrieved; PMIDs against the
   server's own retrieval. A failed check drops the citation or relation and holds the point.
4. **Nothing in a submission can raise certainty.** `unclear: true` is the only certainty field,
   and it can only lower it. Support is computed afterwards from records the model cannot write.
5. **One turn, one submission.** A pending token identifies the turn and expires; a second
   submission for the same token is refused; a submission for a turn whose material changed since
   the pending call is refused.
6. **Grading is reference-based or it is not grading.** `tutor_grade` returns the reference answer
   and rubric with the grading rules; `record_grade` accepts outcome, feedback, strengths,
   missing-or-unsafe, improved answer and an uncertainty note, or `unable_to_grade`. A learner's
   own judgment is recorded as `self_assessed`, never as a grade.
7. **The learner's answer is stored and never echoed back** in any result.

Never expose schema internals, queue states or provider terms in the learner's conversation.
Translate refusals into plain language while keeping redacted diagnostic detail in server logs.

## Tutor and clinical-integrity rules

Tutor grading must be reference-based. Do not grade a clinical answer from model memory alone when
no reference answer or rubric is available; `unable_to_grade` is the honest outcome.

The structured result minimally contains an outcome (`correct`, `partially_correct`,
`incorrect`, `unable_to_grade`), concise feedback, what the learner did well, what is missing or
unsafe, a suggested improved answer, and an uncertainty or reference-gap flag.

If a prompt appears to request patient-specific diagnosis or treatment, do not continue as though
it were a study question. Show the educational boundary and ask for the request to be restated
without identifiers and as a general learning question.

## Memory and retrieval

The learner's workspace is the durable memory; ChatGPT's conversation memory is not. The model sees
only what a pending tool returned for that turn.

- Stable ids, timestamps, content hashes, source locators on every record.
- Transparent retrieval: full-text search with deterministic ranking first; show which items were
  included; let the learner exclude a source from model use; make deletion real and auditable.
- No embeddings until retrieval quality has been evaluated against a checked dataset, and then
  only computed on the server without any external service, or not at all.

## UX direction

Design for the ChatGPT conversation first, then the desk. The primary workflows must be
understandable without knowledge of MCP, OAuth, queues or verification internals.

- Readable typography and touch targets in widgets; keyboard and screen-reader accessibility on
  the desk.
- Explicit loading, offline, signed-out, held and unavailable states.
- No model call until the learner selects an action such as **Build** or **Grade**; before a
  build, the disclosure and the excerpts are shown and an explicit yes is awaited.
- No streaks, shame, fake urgency, quotas, due counts or manipulative engagement mechanics.
- A visible explanation distinguishing what Vademecum stores from what the ChatGPT conversation
  holds.

## Delivery sequence

Phases 0 to 3 below are done. ADR 0012 then reframed distribution: the product is installed,
not hosted. What remains is packaging (a wheel with the desk built in, then a signed macOS
application bundling Python), the licensing decision, and retiring the owner's Codex bridge
once the owner's own workspace runs in host mode.

Work in vertical, testable slices. Phase 0 precedes everything and can void the plan.

0. **Verify and decide.** Confirm apps are available inside ChatGPT for Clinicians by enrolling one
   account and adding an app in developer mode; check Sign in with ChatGPT for apps; check Apps
   SDK file handling. Developer account, domain, privacy-policy draft. This document and ADR 0009.
1. **Host-driven model contract, locally.** The `ModelProvider` seam with `CodexProvider` (today's
   bridge, temporary) and `HostProvider` (pending/submit tools). Same schemas, same checks, same
   suite with scripted submissions in place of scripted turns. Single-user, SQLite, no hosting.
2. **Tenancy.** A workspace per learner, accounts by invite on the OAuth server, tokens that name
   the learner, deletion, and the isolation test class (landed; ADR 0010).
3. **The two surfaces.** The desk through the gateway (ADR 0011), the Today and Tutor cards
   the ChatGPT app renders inline (`vademecum_mcp/widgets`), and one PubMed throttle per
   process with NCBI's optional courtesy key. Landed.
4. **Pilot and submission.** Terms and privacy policy published; a handful of learners under them;
   reviewer credentials with sample data; App Directory submission.
5. **Retire.** The Codex bridge, `appserver/`, the loopback-only assumptions and ADR 0006, once the
   owner's workspace runs on the product.

Do not begin any migration by deleting or transforming the only copy of existing data.

## Engineering expectations

- Keep protocol, storage, retrieval, verification, tenancy and HTTP boundaries in separate modules.
  The MCP server stays a client of the API; it owns no storage.
- Add dependencies only when they remove meaningful risk or complexity; record the reason.
- Validate all input at the HTTP boundary and every submission at the tool boundary. Parameterised
  queries, versioned migrations, and a connection that can only open the requesting learner's
  database; never a query that takes a learner id as a parameter.
- Put hosts, ports, buckets and keys in configuration; the only key that may exist is NCBI's.
- No production model call in the automated test suite: there is no model to call. Tests feed
  scripted submissions through the same submit tools a learner's ChatGPT would.
- Tests for: schema refusal, quote and PMID checks, pending-token expiry and replay, isolation
  between learners, deletion completeness, export readability, the OAuth flow, and the primary
  conversation and desk flows.
- Verify every change with the smallest relevant tests, then the full suite before handoff.
- Update this file and the README whenever an architectural assumption changes.

## Definition of done for the product

Ready for learners other than the owner only when all of the following are true:

- a verified clinician account can install the app from ChatGPT and sign in without creating a
  provider credential of any kind;
- the server holds no model credential and makes no model call, proven by test;
- two learners cannot see each other's anything, proven by test;
- Build, evidence, assessment and grading run as host-driven exchanges with every mechanical check
  enforced and every refusal in plain language;
- Tutor serves only sound, evidence-supported, anchor-verified questions;
- a learner can export and delete everything from the desk;
- the conversation, the desk and the terms say what ChatGPT holds and what Vademecum holds;
- free text appears in no log and no response carries a path or an identifier;
- terms, privacy policy and support contact are published; and
- unit, integration, isolation and primary flow tests pass.

## Tool flows

- The learner drops lectures, papers and decks into the **source folder**: one folder per pile
  inside `piles/highconfidence`, `piles/mediumconfidence` or `piles/lowconfidence`, the tier
  folder being the pile's **tier confidence**. A scan never deletes; removing a source is an
  explicit, confirmed act. Today and Tutor, in the assistant, are the primary surfaces.
- **Build learning material** shows the exact excerpts in the conversation, waits for a yes, hands
  them to ChatGPT with the schema, and accepts points and questions only after the server's checks.
  Bounded batches with resumable character-level coverage, so a long PDF is walked to its end.
- Learners flag weak topics in one sentence from the conversation; the text is the only required
  field.
- **Today** is the cover sheet: new literature with why it is relevant, what is worth a look, and
  what is held.
- **Tutor** asks open questions from the evidence-supported, sound bank in a durable shuffled cycle:
  no repeats until the cycle is exhausted, no quotas, no streaks, no due counts. The learner
  answers in their own words; ChatGPT grades against the reference; the server records the grade.
- Evidence links have a current basis and preserved history; a successful recheck may replace the
  basis with clean evidence; corrected or retracted records cannot re-enter it.
- The web app is the dashboard, and it is the same app in three places: served by the API in the
  browser, drawn inside the conversation as an MCP App (ADR 0014; `open_vademecum`, with the
  app's own requests going through `app_request` and its fixed route list), and mirrored by the
  named tools. Nothing the in-chat app can do removes, retires, exports, backs up, uploads or
  signs in; those stay where a confirmation exists.
- The learner model (ADR 0031) is arithmetic on the learner's own records, recomputed and never
  stored: what is understood, what is holding, and a suggested next step with its reason. It
  suggests; it never sets a quota, a due count or a score, and the Tutor's shuffled pass stays
  the default.
- Builds may run on a timer (ADR 0018) only in codex mode and only under a standing consent
  the owner gave with the disclosure in view and can withdraw; every run is recorded per pile.
  Never add a scheduled send that is not visible on Today.
- The phone reaches Vademecum through the cloud copy, Foris (ADR 0017), which syncs with the
  Mac and has no model of its own: in ChatGPT or Claude the assistant is the model (host mode);
  the Mac answers the phone's own Socratic tutor through the relay while it is awake (ADR 0029).
  The phone pack (ADR 0016) is gone.
- Two Vademecums may sync (ADR 0015): Domi (the Mac) owns what is made from files;
  shared records merge by later write or by union; Domi initiates; nothing applied from a peer is
  logged again. Never add a sync path that bypasses the change log or the ownership rules.
- Pictures inside sources are kept with their page or slide; a picture file in a pile is a source
  (ADR 0013). The assistant looks at a picture through `view_image`; Vademecum does not interpret
  it. Pages with no text layer are read on the Mac with the system's own recognition and cited
  with an `(OCR)` locator; never present recognised text as a text layer, and never imply reading
  happens anywhere but the device.
- Schematics the assistant draws are SVG only, checked before they are kept, filed against one
  learning point, copied into the source folder, and carry that point's support label. A drawing
  is a study aid, never evidence.
- Never imply diagram understanding by Vademecum, full-text literature access, clinical
  validation, or that anything is private to the conversation.
