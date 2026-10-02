# ADR 0009 — A hosted product for other learners: ChatGPT is the model, Vademecum is the memory

- Status: accepted, with one precondition (see *Verification before building*)
- Date: 2026-09-28
- Amends: [0002](0002-local-first-boundary.md), [0007](0007-source-intake-verification-and-public-literature.md),
  [0008](0008-mcp-server-for-chat-hosts.md)
- Supersedes, once phase 5 lands: [0006](0006-codex-app-server-bridge.md)

## Context

Every ADR before this one describes a workspace for one owner on one Mac. The goal has changed:
Vademecum is to be disseminated to other learners. Two facts decide how.

1. **The channel is ChatGPT, and only ChatGPT.** OpenAI's *ChatGPT for Clinicians* (launched
   23 April 2026) gives verified US physicians, nurse practitioners, physician assistants and
   pharmacists a free ChatGPT. Learners already have the model; the product has to meet them where
   it is. Claude and every other host stop being product targets.
2. **There is no API.** No OpenAI API key, no provider account, no server-side model call, ever.
   The learner's own ChatGPT does every piece of model work, inside their conversation, on their
   free license. Vademecum's server never contacts a model provider. Its one external call
   remains PubMed.

Two more constraints the owner has set:

- **Personal workspaces only.** Each learner's material, points, questions, flags and history are
  theirs alone. There is no shared bank, no publishing, no author-curated library, no
  cross-learner reads of any kind. This is a product decision and a data-protection one at once.
- **Educational, still.** Not a medical device, not clinical decision support, no patient
  identifiers. Now those are terms of service as well as design rules.

The current build is not wasted. The API's storage rules, consent hashing, quote checks, evidence
gates, Tutor cycle and the MCP server from ADR 0008 are the foundation. What changes is who runs
the model, where the data lives, and who the owner is.

## Decisions

### 1. ChatGPT is the model. Vademecum is memory, structure and verification.

Today Build and Grade run through a Codex child on one owner's sign-in (ADR 0006). That cannot
serve other learners and would need a key to replace. Instead, every model turn becomes an
exchange between the ChatGPT app and the server, in the learner's own conversation:

| Turn today (Codex) | Turn in the product (ChatGPT, through tools) |
| --- | --- |
| Synthesis: excerpts in, points and questions out, schema-constrained | `build_preview` returns the consented excerpts with opaque handles and the exact output schema; ChatGPT writes the points and questions; `submit_learning_points` accepts them |
| Evidence: one claim against one retrieved abstract | `evidence_pending` returns claim, context and the abstract text; ChatGPT judges the relation and quotes; `submit_evidence` accepts it |
| Assessment: question, reference and rubric against passage and evidence | `assessment_pending` returns them; ChatGPT judges sound/unsound; `submit_assessment` accepts it |
| Grading: reference-based feedback on the learner's answer | `tutor_grade` returns question, reference and rubric with the grading rules; ChatGPT grades; `record_grade` accepts the structured result |

**What stays on the server, unchanged in kind, is everything that can be checked rather than
judged:**

- Every submission is validated against the same JSON schemas that constrain Codex today
  (`model/schemas.py`): `additionalProperties: false`, enums, maximum lengths. A submission that
  does not validate is refused, with the field named and nothing echoed.
- A cited quote must appear verbatim in the stored segment text at the handle it names, or the
  citation is dropped and the point is held.
- An evidence quote must appear in the abstract body the server itself retrieved from PubMed,
  never in a title, or the relation is discarded.
- A PMID must be one the server retrieved. A fabricated identifier maps to nothing.
- Support levels (`source_supported`, `evidence_supported`, `uncertain`, `conflicting`) are
  computed by the server from those records. No submission has a field that can assert support,
  verification or certainty; the only certainty field a model may set is `unclear: true`.
- Tutor serves only `evidence_supported` questions whose assessment came back `sound` *and*
  whose anchors still match the stored text. Retractions, corrections and exclusions hold
  questions exactly as today.

**What is given up, and said plainly.** The isolated, tool-less, network-less Codex turn is gone,
and the "second, independent gate" is now the same model in the same conversation as the first.
Independence was never the strongest defence; the mechanical checks above were, and they are
untouched. Prompt-injection defences change shape too: the material is data in a tool result,
fenced and labelled as such in the server instructions and in every submission tool's
description, and the structural rule holds that nothing ChatGPT says can mark a claim supported.

**Consequence for the boundary documents.** ADR 0007's "two explicit transmissions" collapse to
zero transmissions *by Vademecum* to any model provider. The learner reads their material in
ChatGPT, and everything a tool returns is part of that conversation, under OpenAI's terms for
their edition of ChatGPT. Vademecum's own egress is PubMed, and only PubMed.

*Alternative rejected:* an operator API key funding an isolated server-side pass for the parts
that matter most, such as question assessment. It would restore independence and cost money per
learner per build; the owner has ruled it out. The provider abstraction in phase 1 keeps the door
closed rather than open: there is no `ApiKeyProvider`, and the privacy tests keep failing any
file that names a provider endpoint or a key.

### 2. Personal workspaces, isolated by construction

- Every table carries a `learner_id`; every query is scoped by it at the storage layer, not at the
  route. There is no query path without a learner.
- There is no sharing surface: no publish, no invite, no shared pile, no leaderboard, no
  cohort view. Anything of that kind is a new ADR, not a feature flag.
- The operator has no interface that reads a learner's content. Support works from redacted
  diagnostics (event names, timings, status codes, correlation ids) exactly as ADR 0002 requires
  of logs. Backups are encrypted at rest; restoring one is an operational act, not a reading one.
- Deletion is real: deleting a workspace removes rows, originals, and derived material, and the
  learner can export everything first, in the same readable JSON as today.
- The isolation test class is the most important new test in the suite: learner A, holding a
  valid token, can read, write or delete nothing of learner B's through any route or tool.

### 3. A hosted service, because the data cannot stay on one Mac

- Postgres replaces SQLite (ADR 0005's hand-rolled forward-only migrations remain the pattern;
  the dialect changes). Uploaded originals go to object storage, still content-addressed by
  digest. Builds and literature checks run on a job queue rather than as tasks in the web
  process, because a build now waits on a learner's conversation rather than on a child process.
- The service binds a public HTTPS name behind TLS, a rate limiter and structured audit logs.
  The loopback-only rule of ADR 0002 is retired for the hosted service. It survives, unchanged,
  for the owner's own Mac deployment, which keeps working throughout the transition.
- PubMed is a shared resource: one NCBI API key, one throttle for the whole service, cached
  records, and the same allowlisted single host in `literature/http.py`. Only short public topic
  phrases and PMIDs are ever sent, as before.
- Everything in `apps/api` that is not SQLite-specific carries over. The MCP server from ADR 0008
  carries over as the ChatGPT app's backend, with Claude and stdio kept only as developer
  conveniences.

### 4. Identity replaces the passphrase

The OAuth 2.1 server built in ADR 0008 stays; the consent page becomes a sign-in page for real
accounts. Sign in with ChatGPT as an identity provider is preferred if OpenAI opens it to apps,
because the learner then never creates a second account; otherwise email magic links. Clinician
verification is ChatGPT's job, not Vademecum's: the product does not verify licenses, collect
NPIs, or store anything about a learner beyond an email or an identity-provider subject.

### 5. Two surfaces, one backend

- **The ChatGPT app is the pocket.** Today, Tutor, flags, the Improvement Map, previews and
  submissions, as tools with Apps SDK widgets for the Tutor card and the cover sheet. Files
  cannot reliably be attached to an MCP tool from ChatGPT, so uploads do not happen here.
- **The web app is the desk.** The existing React app, hosted: uploads, source management,
  exclusion, export, deletion, account. No model work of any kind happens on the desk.

### 6. A product with obligations

Listing in the ChatGPT App Directory needs a verified developer account, a registrable domain
the owner controls, a published privacy policy, a support contact, reviewer test credentials and
review. Before any learner who is not the owner uses it: terms of service that state the
educational boundary, forbid patient identifiers, and place responsibility for uploaded
institutional material with the uploader. No Business Associate Agreement is offered; the product
is not for PHI, and the free-text warnings and the patient-specific refusal are what make that
true in practice.

## Verification before building

1. **Apps inside ChatGPT for Clinicians.** Business, Enterprise and Healthcare workspaces gate
   apps behind admin settings; the Clinicians edition may be its own case. Enroll one clinician
   account and add an app in developer mode. If apps are not available there, the channel does
   not exist and this ADR is void. Nothing else is worth starting first.
2. **Sign in with ChatGPT for apps.** If unavailable, email magic links, without ceremony.
3. **Apps SDK file handling.** If attachments do reach tools reliably by then, uploads may move
   to the pocket; the desk still exists for management and deletion.

## Delivery sequence

0. **Decide and verify.** This ADR; AGENTS.md rewritten; the three checks above; developer
   account, domain, privacy policy draft.
1. **Host-driven model contract, locally.** A `ModelProvider` seam with two implementations:
   `CodexProvider` (today's bridge, kept for the owner's Mac through the transition) and
   `HostProvider` (the pending/submit tools). Same schemas, same mechanical checks, same suite,
   with scripted submissions replacing scripted turns. Single-user, on SQLite, no hosting.
2. **Tenancy.** `learner_id` everywhere, Postgres, object storage, the job queue, real accounts on
   the OAuth server, and the isolation tests.
3. **The two surfaces.** Hosted desk; ChatGPT app with widgets; shared PubMed throttle.
4. **Pilot and submission.** Terms and privacy policy published; a handful of learners under
   them; reviewer credentials with sample data; submission.
5. **Retire.** The Codex bridge, `appserver/`, the loopback-only assumptions and ADR 0006, once
   the owner's own workspace runs on the product too.

## Phase 1, as landed (2026-09-28)

- `model/host.py`: the pending-turn registry (`HostTurns`) and the runner the pipeline sees
  (`HostTurnRunner`), behind `VADEMECUM_MODEL_PROVIDER=host`. The build pipeline is unchanged;
  `BuildService` hands it a per-run turn factory. Grading is split into a snapshot half and a
  recording half, used by both modes.
- Migration 0005 (`pending_turns`); routes `build/pending`, `build/submit`, `tutor/grade/submit`;
  `build_start` answers with the first pending turn, `tutor/grade` with a pending grading turn
  shaped as a refusal so the desk keeps the typed answer and says where grading happens.
- Tools `build_pending`, `build_submit`, `tutor_record_grade`; the server instructions carry the
  exchange's rules.
- Tests drive the real application in host mode with a test playing ChatGPT, through the API
  and through the tools: schema refusal leaves the turn pending, one result per turn, quotes
  still checked, cancellation and restart abandon turns, expiry, stale grades not recorded.
- Not yet: the ordering that lets the host answer several evidence turns in one submission (the
  pipeline asks one at a time), and the widgets. Both are phase 3.

## Phases 2 and 3, as landed (2026-09-28 and 2026-09-29)

- Phase 2: [ADR 0010](0010-tenancy-by-workspace-per-learner.md) -- a workspace per learner,
  accounts by invite, tokens naming the learner, deletion, isolation tests.
- Phase 3: [ADR 0011](0011-the-desk-through-the-gateway.md) -- the desk through the gateway;
  two cards the ChatGPT app renders inline (`vademecum_mcp/widgets`: the cover sheet on
  `get_today`, the Tutor card on every Tutor tool, with reveal and next callable from the card
  and grading never), self-contained HTML with no external asset and everything drawn escaped;
  one PubMed client per process so every learner shares the throttle, with NCBI's optional
  courtesy key (`VADEMECUM_LITERATURE_NCBI_KEY`), the only key the product may hold.
- Still one evidence turn at a time in a build; batching is a later refinement.

## Consequences

- The strongest claim about a learning point is unchanged: *evidence-supported, machine
  reviewed*. The "machine" is now the learner's ChatGPT, and the documents say so.
- Vademecum's server never holds a model credential and never calls a model. A test reads the
  whole codebase for a provider endpoint or a key, as today.
- Every learner's material is in their ChatGPT conversation history as well as in Vademecum;
  deleting a workspace does not delete a transcript. The terms and the desk say so.
- Two ADRs are partly retired: 0002's loopback rule (for the hosted service) and 0007's two
  transmissions (there are none by the server). 0006 goes entirely in phase 5.
- What the owner has today keeps working: the Mac deployment, the Codex path and the MCP server
  are the first tenant, not a throwaway.
