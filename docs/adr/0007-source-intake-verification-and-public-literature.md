# ADR 0007: Source intake, verification, and the public-literature watch

Status: accepted. Supersedes nothing; extends ADR 0002 (local-first boundary) and
ADR 0006 (App Server bridge).

## Context

The first slices stored piles, plain-text items and flags, and read Codex account
state. Nothing generated material, nothing was asked, and nothing left the
machine except account operations. Making the product actually work means
uploading real documents, sending some of them to a model, and looking things up
in the published literature. Each of those is a new way to be wrong about what
the owner's data is doing.

## Decisions

### 1. Source confidence is not evidence

`piles.tier` (`low`/`mid`/`high`, labelled Low/Medium/High) is how much the owner
trusts the *material*. It is never mastery, priority, difficulty, or support.
`storage.learning.support_for` takes no confidence argument at all — not as
discipline, but so there is nothing to be careless with. `evidence_supported` is
reachable only through `apply_evidence`, which needs a record a provider actually
returned and a quote actually found in its abstract body.

The ceiling phrase is **"evidence-supported, machine reviewed"**. Never verified,
never human-approved, never clinically validated.

### 2. Tutor asks only externally-checked, independently-assessed material

`ELIGIBLE_SUPPORTS` is `{evidence_supported}` — one frozen name in one place,
with a test asserting its contents. Four independent gates, re-read from the
database on every call (never a cached flag):

1. the point is `evidence_supported`;
2. nothing in its active evidence basis is retracted, corrected, a notice, or contradicting, and
   it is not awaiting re-review;
3. every anchor quote is present in the stored segment it names, in an included,
   readable source; and
4. a **separate** assessment pass judged the question, reference answer and
   rubric against both the cited passage and the retrieved evidence.

Gate 4 is not gate 3 restated. A question can quote a real passage perfectly and
still be ambiguous, unanswerable, or carry a reference answer the passage does
not support.

`source_supported` material is genuinely useful, so it is kept and shown — as
draft, visibly held, never asked.

### 3. Coverage is a character cursor, not a boolean

A stored segment can be longer than one batch may transmit. Counting segments
would report a 12,000-character page as processed after 2,400 of it had been
sent, and the next batch would find nothing to do. `source_segments.covered_upto`
records how many characters have been through a **committed** batch; the next
batch resumes from exactly there. A failed or cancelled batch advances nothing.
`reopen_coverage` (and `POST /api/piles/{id}/recheck`) rewinds it, because
"already processed" must not be a permanent dead end — it releases nothing.

### 4. Consent is to specific characters, plus everything sent with them

A preview persists the ordered `(segment, start, end)` ranges and hashes them
together with the text digest, the filename, the confidence label and the
locator — every field that ends up in the prompt. Sending quotes the hash back
and the ranges are re-derived from the live database. A source excluded,
renamed, re-rated or re-extracted in between changes the hash, and the send is
refused in favour of a fresh preview.

### 5. A build is staged, then committed once

Claim → synthesise → stage → verify → assess → commit. Nothing is written until
every stage has answered. Model failures **propagate**: swallowing an evidence
timeout let a rerun report "succeeded" with an empty bank, silently demoting
material that had already been accepted. The commit is one transaction
(`db.transaction` nests via SAVEPOINT), so coverage and the material it produced
land together or not at all. Required provider outages also fail the build: an
unsuccessful lookup is not a successful search with no matching evidence. Accepted
references, evidence basis and coverage survive the failure unchanged. A successful
search with no usable evidence can produce a clearly held draft.
Newly observed provider correction/retraction signals are a separate safety fact: they may hold
affected questions even when a later build stage fails, without rewriting references or advancing
coverage. An ordinary outage alone must not demote previously accepted content.

Migration 0003 separates current evidence from superseded history. A successful
explicit recheck may establish a clean new basis without deleting earlier links or
attempts. Corrected papers remain in history but cannot automatically become support
again when returned alongside clean replacement evidence.

### 6. Uploads are content-addressed and idempotent

Files are stored under their own SHA-256, so the uploaded filename never becomes
a path. Identical bytes with an identical extraction is a true no-op that
**preserves segment ids** — questions and citations are anchored to them. A
changed extraction reconciles in place, keeping the row of every segment whose
text is unchanged, and holds everything that cited the source. The original is
written before extraction is attempted: a parser that cannot read a file is not a
reason to lose it.

### 7. One narrow egress, one allowlisted host

`literature/http.py` is the whole of Vademecum's own network egress: `http.client`
over TLS, one frozen host, GET only, no redirects followed, bounded body,
throttled, retried narrowly, errors as categories rather than provider text. Only
a short public topic string is ever sent: derived during Build, or reviewed and
editable as a watched topic. XML is
parsed with entity declarations and expansion refused, while the DOCTYPE real
NLM responses carry is tolerated.

### 8. Loopback is not same-origin

`Host` is validated on **every** request — DNS rebinding reaches reads, and a
rebound Today payload is a disclosure. `Origin` is validated on every mutation,
with an explicit `null` refused rather than treated as absent, and `Referer`
consulted only when `Origin` is absent. Malformed headers fail closed with a 403,
never a 500. There is no CORS middleware anywhere.

### 9. A read-only sandbox does not disable tools

Verified against the installed CLI rather than assumed. Features are disabled at
child-process startup (`-c features.*=false`, `web_search="disabled"`,
`project_doc_max_bytes=0`, cleared `notify`, otel/analytics exporters off), MCP
servers are discovered via `config/read` and disabled **by name** because
`mcp_servers={}` does not clear a deep-merged config, and the thread's working
directory is a dedicated empty one — an empty cwd reports
`instructionSources: []` where the checkout reports this repository's AGENTS.md.
Any unexpected item type on our thread fails the turn closed.

### 10. Every content turn re-checks the credential

`account/read` with `refreshToken: false`, requiring `account.type == "chatgpt"`,
fresh per turn. A cached status snapshot is not a credential check.

## Consequences

- The README's "no network egress from Vademecum's own code" claim is no longer
  true and has been rewritten rather than quietly narrowed.
- Builds derive bounded public search topics from the selected material. Missing
  substantive evidence produces held material; failed provider requests are reported
  as failures. Watched topics are a separate opt-in for ongoing checks.
- Verification costs model turns. Batches are small on purpose.
- Backups are now bundles (database + originals + manifest) because a backup
  containing only the database restores a library whose every source file is
  missing.
