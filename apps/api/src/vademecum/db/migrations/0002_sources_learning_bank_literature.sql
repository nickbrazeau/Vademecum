-- 0002 source intake, the generated learning bank, the Tutor cycle, and the
-- public-literature watch.
--
-- Forward-only and purely additive (ADR 0005). Nothing here alters, drops or
-- rewrites a 0001 table: `piles` keeps its `low`/`mid`/`high` tier values and
-- `learning_items` keeps every row it had. A pile's tier is now read as *source
-- confidence* -- how much the owner trusts the material they dropped in -- and
-- the interface says "Medium" where the stored value is `mid`. That is a label
-- change, not a data change, so there is no remap to get wrong.
--
-- The distinction this schema exists to keep is between two things that are
-- easy to collapse:
--
--   * `sources.confidence`  -- how much the owner trusts the material.
--   * `learning_points.support` -- whether a claim is actually supported, and
--     by what.
--
-- No value of the first may ever produce `evidence_supported` in the second.
-- Nothing in this file can enforce that on its own; `storage/learning.py` and
-- `model/verification.py` do, and they are tested against it.

-- --- source intake -----------------------------------------------------------

-- One uploaded original. The bytes live under `attachments/sources/` named by
-- their own digest; this row is the record of what they are and how they were
-- read. A row exists even when extraction failed, because the *file* is the
-- thing that must survive -- losing an upload because a parser choked on it is
-- the failure this table is shaped to prevent.
CREATE TABLE sources (
    id            TEXT PRIMARY KEY,
    -- RESTRICT, not CASCADE: deleting a pile must not silently take the
    -- owner's uploaded originals with it (`storage/piles.delete_pile`).
    pile_id       TEXT NOT NULL REFERENCES piles (id) ON DELETE RESTRICT,
    display_name  TEXT NOT NULL,
    stored_name   TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    byte_size     INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    -- Mirrors piles.tier so a source can be trusted differently from the pile
    -- it sits in. Same vocabulary on purpose: `mid` reads as "Medium".
    confidence    TEXT NOT NULL CHECK (confidence IN ('low', 'mid', 'high')),
    -- `stored`   the file is kept, nothing has been read out of it yet
    -- `extracted` text was located and segmented
    -- `needs_ocr` a PDF or deck with pages but no extractable text layer
    -- `encrypted` password-protected; the file is kept, unread
    -- `unreadable` the format was right and the bytes were not
    status        TEXT NOT NULL CHECK (
                      status IN ('stored', 'extracted', 'needs_ocr', 'encrypted', 'unreadable')
                  ),
    status_detail TEXT NOT NULL DEFAULT '',
    unit_kind     TEXT NOT NULL DEFAULT '',
    unit_count    INTEGER NOT NULL DEFAULT 0,
    char_count    INTEGER NOT NULL DEFAULT 0,
    -- Digest of the extracted text, not of the file. Re-uploading identical
    -- bytes must be a true no-op that PRESERVES segment ids, because questions
    -- and citations are anchored to them. A changed extraction (a better
    -- parser, a repaired file) is a different value here, and that is what
    -- triggers revalidation of everything downstream.
    extraction_hash TEXT NOT NULL DEFAULT '',
    -- Honest coverage of the read, as JSON: how many pages had text, how many
    -- were image-only, how many failed, whether the document was truncated.
    coverage_json TEXT NOT NULL DEFAULT '{}',
    -- The owner's own exclusion control (AGENTS.md, "allow an item to be
    -- excluded from model use"). An excluded source is still stored and still
    -- readable; it is simply never selected for an excerpt.
    excluded      INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    extracted_at  TEXT
);

-- Dedupe, and the property that makes upload idempotent: the same bytes dropped
-- into the same pile twice is one row and one file on disk. The same document
-- filed in two piles is two rows, because it may deserve two confidences.
CREATE UNIQUE INDEX idx_sources_pile_digest ON sources (pile_id, sha256);
CREATE INDEX idx_sources_pile ON sources (pile_id);
CREATE INDEX idx_sources_digest ON sources (sha256);

-- Text with a location attached. A learning point cites a segment, so every
-- claim can be walked back to "page 4 of this deck" rather than to a document.
CREATE TABLE source_segments (
    id         TEXT PRIMARY KEY,
    source_id  TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    ordinal    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    locator    TEXT NOT NULL,
    text       TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    -- Content digest, so a re-extraction can keep the id of a segment whose
    -- text is unchanged and only disturb the ones that actually moved.
    text_hash  TEXT NOT NULL DEFAULT '',
    -- Resumable coverage, as a CHARACTER CURSOR, not a boolean.
    --
    -- A segment can be longer than one batch may transmit, so "this segment was
    -- in a batch" is not the same as "all of its text has been processed".
    -- `covered_upto` is how many characters of `text` have been sent in a batch
    -- that COMMITTED validated output; the next batch resumes from exactly
    -- there. A failed or cancelled batch advances nothing, so the same range is
    -- offered again, and a long document walks forward instead of its opening
    -- being resent forever.
    covered_upto INTEGER NOT NULL DEFAULT 0,
    covered_at   TEXT,
    covered_by   TEXT
);

CREATE UNIQUE INDEX idx_source_segments_ordinal ON source_segments (source_id, ordinal);
CREATE INDEX idx_source_segments_uncovered ON source_segments (source_id, covered_upto);

-- --- generation --------------------------------------------------------------

-- One "Build learning material" run. Kept whatever happens, including the
-- crashes: a run that died mid-flight has to be visible as a failure rather
-- than as nothing, and a rerun that fails must leave the previous run's output
-- alone (`storage/jobs.py`).
CREATE TABLE generation_runs (
    id               TEXT PRIMARY KEY,
    pile_id          TEXT NOT NULL REFERENCES piles (id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN ('synthesis')),
    status           TEXT NOT NULL CHECK (
                         status IN ('running', 'succeeded', 'failed', 'cancelled')
                     ),
    stage            TEXT NOT NULL DEFAULT '',
    failure_category TEXT NOT NULL DEFAULT '',
    source_count     INTEGER NOT NULL DEFAULT 0,
    excerpt_count    INTEGER NOT NULL DEFAULT 0,
    excerpt_chars    INTEGER NOT NULL DEFAULT 0,
    point_count      INTEGER NOT NULL DEFAULT 0,
    question_count   INTEGER NOT NULL DEFAULT 0,
    held_count       INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT NOT NULL,
    heartbeat_at     TEXT NOT NULL,
    finished_at      TEXT
);

CREATE INDEX idx_generation_runs_pile ON generation_runs (pile_id, started_at DESC);

-- The consent record for one batch: exactly which TEXT RANGES the owner was
-- shown and agreed to send. `ranges` is a JSON array of
-- {segment_id, start, end}, and `selection_hash` is over those ranges and a
-- digest of the exact substring each names -- so consent is to specific
-- characters, not to a document or to "a build". If anything changes between
-- the preview and the send (a source excluded, a file re-extracted, another
-- batch committed, the text edited), the hash differs and the send is refused
-- in favour of a fresh preview.
CREATE TABLE build_batches (
    id             TEXT PRIMARY KEY,
    pile_id        TEXT NOT NULL REFERENCES piles (id) ON DELETE CASCADE,
    run_id         TEXT REFERENCES generation_runs (id) ON DELETE SET NULL,
    selection_hash TEXT NOT NULL,
    ranges         TEXT NOT NULL,
    excerpt_count  INTEGER NOT NULL DEFAULT 0,
    excerpt_chars  INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL CHECK (
                       status IN ('previewed', 'running', 'committed', 'failed', 'cancelled')
                   ),
    created_at     TEXT NOT NULL,
    committed_at   TEXT
);

CREATE INDEX idx_build_batches_pile ON build_batches (pile_id, created_at DESC);

-- --- the learning bank -------------------------------------------------------

-- A claim, with its support recorded separately from the confidence of the
-- material it came from.
--
--   source_supported    the material says it; nothing external was checked
--   evidence_supported  an allowlisted provider returned text that a verifier
--                       matched to the claim, quote and identifier included
--   uncertain           the material is unclear, or verification could not run
--   conflicting         two sources, or a source and the literature, disagree
--
-- `evidence_grade` keeps "an abstract mentions this" apart from "a guideline or
-- full text says it". `review_state` goes to `needs_re_review` when linked
-- evidence is later corrected or retracted.
CREATE TABLE learning_points (
    id             TEXT PRIMARY KEY,
    pile_id        TEXT NOT NULL REFERENCES piles (id) ON DELETE RESTRICT,
    generation_id  TEXT REFERENCES generation_runs (id) ON DELETE SET NULL,
    claim          TEXT NOT NULL,
    detail         TEXT NOT NULL DEFAULT '',
    support        TEXT NOT NULL CHECK (
                       support IN ('source_supported', 'evidence_supported',
                                   'uncertain', 'conflicting')
                   ),
    evidence_grade TEXT NOT NULL DEFAULT 'none' CHECK (
                       evidence_grade IN ('none', 'abstract_only', 'guideline_or_full_text')
                   ),
    review_state   TEXT NOT NULL DEFAULT 'machine_reviewed' CHECK (
                       review_state IN ('machine_reviewed', 'needs_re_review')
                   ),
    held           INTEGER NOT NULL DEFAULT 0 CHECK (held IN (0, 1)),
    hold_reason    TEXT NOT NULL DEFAULT '',
    content_hash   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

-- Deduplication with provenance kept: a claim repeated across three lectures is
-- one point with three rows in `learning_point_sources`, not three points.
CREATE UNIQUE INDEX idx_learning_points_hash ON learning_points (pile_id, content_hash);
CREATE INDEX idx_learning_points_pile ON learning_points (pile_id);
CREATE INDEX idx_learning_points_support ON learning_points (support);

CREATE TABLE learning_point_sources (
    id                TEXT PRIMARY KEY,
    learning_point_id TEXT NOT NULL REFERENCES learning_points (id) ON DELETE CASCADE,
    source_id         TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    segment_id        TEXT REFERENCES source_segments (id) ON DELETE SET NULL,
    locator           TEXT NOT NULL,
    quote             TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_learning_point_sources_unique
    ON learning_point_sources (learning_point_id, source_id, locator, quote);
CREATE INDEX idx_learning_point_sources_point ON learning_point_sources (learning_point_id);
CREATE INDEX idx_learning_point_sources_source ON learning_point_sources (source_id);

CREATE TABLE learning_point_topics (
    learning_point_id TEXT NOT NULL REFERENCES learning_points (id) ON DELETE CASCADE,
    topic             TEXT NOT NULL,
    PRIMARY KEY (learning_point_id, topic)
);

CREATE INDEX idx_learning_point_topics_topic ON learning_point_topics (topic);

-- A question is only ever asked with its own reference answer beside it
-- (AGENTS.md: grading must be reference-based). `status` is what Tutor reads:
-- only `eligible` is ever served.
--
-- `assessment` is the second, independent gate. It is NOT "the quote matched".
-- A separate verification pass judges the QUESTION and its REFERENCE ANSWER and
-- RUBRIC against the cited source text and the returned evidence text, and has
-- to come back `sound`. A question can quote a real passage accurately and
-- still be unanswerable, ambiguous, or have a reference answer the passage does
-- not actually support -- and that question must not be asked.
CREATE TABLE tutor_questions (
    id                TEXT PRIMARY KEY,
    learning_point_id TEXT NOT NULL REFERENCES learning_points (id) ON DELETE CASCADE,
    pile_id           TEXT NOT NULL REFERENCES piles (id) ON DELETE RESTRICT,
    generation_id     TEXT REFERENCES generation_runs (id) ON DELETE SET NULL,
    prompt            TEXT NOT NULL,
    reference_answer  TEXT NOT NULL,
    rubric            TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL CHECK (status IN ('eligible', 'held', 'retired')),
    hold_reason       TEXT NOT NULL DEFAULT '',
    assessment        TEXT NOT NULL DEFAULT 'unassessed' CHECK (
                          assessment IN ('unassessed', 'sound', 'unsound')
                      ),
    assessment_notes  TEXT NOT NULL DEFAULT '',
    assessed_at       TEXT,
    -- Bumped whenever the prompt or reference answer is rewritten by a later
    -- run. Answer history keeps its own copy of what was asked (see
    -- tutor_attempts), so an old attempt stays interpretable after a rewrite.
    version           INTEGER NOT NULL DEFAULT 1,
    retired_at        TEXT,
    retired_reason    TEXT NOT NULL DEFAULT '',
    content_hash      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_tutor_questions_hash ON tutor_questions (pile_id, content_hash);
CREATE INDEX idx_tutor_questions_status ON tutor_questions (status);
CREATE INDEX idx_tutor_questions_point ON tutor_questions (learning_point_id);

-- Where the reference answer comes from, exactly. A question whose anchors do
-- not match the stored segment text never becomes `eligible`.
CREATE TABLE tutor_question_anchors (
    id          TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES tutor_questions (id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    segment_id  TEXT REFERENCES source_segments (id) ON DELETE SET NULL,
    locator     TEXT NOT NULL,
    quote       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_tutor_question_anchors_question ON tutor_question_anchors (question_id);

-- --- Tutor -------------------------------------------------------------------

-- The shuffled cycle, on disk. A question is served once per cycle; when the
-- cycle is exhausted a new one is drawn. This is what "no repeats until a full
-- cycle" is made of, and it survives a restart because it is a table rather
-- than a variable. There is no due date here, no interval, and no count owed.
CREATE TABLE tutor_cycle_entries (
    id           TEXT PRIMARY KEY,
    cycle_number INTEGER NOT NULL,
    position     INTEGER NOT NULL,
    question_id  TEXT NOT NULL REFERENCES tutor_questions (id) ON DELETE CASCADE,
    served_at    TEXT,
    created_at   TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_tutor_cycle_unique ON tutor_cycle_entries (cycle_number, question_id);
CREATE INDEX idx_tutor_cycle_order ON tutor_cycle_entries (cycle_number, position);

-- One graded (or self-assessed) answer. Local, and never logged: the request
-- logger records a path and a status, and the App Server diagnostics accept a
-- closed set of scalar fields with no place for any of this.
--
-- The question is SNAPSHOTTED here. An attempt that only referenced a question
-- id would become uninterpretable the moment a later run rewrote the reference
-- answer, and would vanish entirely if the question were deleted. Questions are
-- retired rather than deleted, and the snapshot means history reads correctly
-- even across a rewrite. `ON DELETE SET NULL`, not CASCADE, for the same reason.
CREATE TABLE tutor_attempts (
    id                TEXT PRIMARY KEY,
    question_id       TEXT REFERENCES tutor_questions (id) ON DELETE SET NULL,
    question_version  INTEGER NOT NULL DEFAULT 1,
    asked_prompt      TEXT NOT NULL DEFAULT '',
    asked_reference   TEXT NOT NULL DEFAULT '',
    asked_rubric      TEXT NOT NULL DEFAULT '',
    outcome           TEXT NOT NULL CHECK (
                          outcome IN ('correct', 'partially_correct', 'incorrect',
                                      'unable_to_grade', 'self_assessed')
                      ),
    graded_by         TEXT NOT NULL CHECK (graded_by IN ('model', 'self')),
    answer            TEXT NOT NULL DEFAULT '',
    feedback          TEXT NOT NULL DEFAULT '',
    strengths         TEXT NOT NULL DEFAULT '',
    missing_or_unsafe TEXT NOT NULL DEFAULT '',
    improved_answer   TEXT NOT NULL DEFAULT '',
    uncertainty       TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);

CREATE INDEX idx_tutor_attempts_question ON tutor_attempts (question_id, created_at DESC);

-- --- public-literature watch -------------------------------------------------

-- What the owner has asked to be watched. `query` is a short public topic
-- string and is the *only* thing that reaches a provider: no source passage, no
-- note, no learning point and no answer is ever put into a search.
CREATE TABLE literature_topics (
    id                   TEXT PRIMARY KEY,
    label                TEXT NOT NULL,
    query                TEXT NOT NULL,
    enabled              INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    last_checked_at      TEXT,
    last_status          TEXT NOT NULL DEFAULT '',
    last_failure         TEXT NOT NULL DEFAULT '',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_due_at          TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE INDEX idx_literature_topics_due ON literature_topics (enabled, next_due_at);

CREATE TABLE literature_checks (
    id               TEXT PRIMARY KEY,
    topic_id         TEXT NOT NULL REFERENCES literature_topics (id) ON DELETE CASCADE,
    trigger          TEXT NOT NULL CHECK (trigger IN ('manual', 'scheduled', 'catch_up')),
    status           TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    failure_category TEXT NOT NULL DEFAULT '',
    result_count     INTEGER NOT NULL DEFAULT 0,
    new_count        INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT NOT NULL,
    finished_at      TEXT
);

CREATE INDEX idx_literature_checks_topic ON literature_checks (topic_id, started_at DESC);

-- Provider metadata, deduplicated by identifier. Note `abstract` may be empty:
-- when a provider returns none, that is what is stored and what is shown. A
-- clinical summary is never written in its place.
CREATE TABLE literature_records (
    id                TEXT PRIMARY KEY,
    pmid              TEXT,
    doi               TEXT,
    title             TEXT NOT NULL,
    journal           TEXT NOT NULL DEFAULT '',
    abstract          TEXT NOT NULL DEFAULT '',
    published_on      TEXT,
    provider_date     TEXT,
    publication_types TEXT NOT NULL DEFAULT '[]',
    retracted         INTEGER NOT NULL DEFAULT 0 CHECK (retracted IN (0, 1)),
    corrected         INTEGER NOT NULL DEFAULT 0 CHECK (corrected IN (0, 1)),
    -- This record is itself a retraction/erratum/expression-of-concern NOTICE
    -- about some other paper. A notice is never supporting evidence, and is
    -- kept distinct from `retracted` (which is about THIS paper).
    is_notice         INTEGER NOT NULL DEFAULT 0 CHECK (is_notice IN (0, 1)),
    correction_notes  TEXT NOT NULL DEFAULT '[]',
    url               TEXT NOT NULL DEFAULT '',
    -- guideline | trial | major_journal | other. A ranking hint for ordering,
    -- never a claim that a result is practice-changing.
    priority          TEXT NOT NULL DEFAULT 'other',
    first_seen_at     TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_literature_records_pmid
    ON literature_records (pmid) WHERE pmid IS NOT NULL;
CREATE UNIQUE INDEX idx_literature_records_doi
    ON literature_records (doi) WHERE doi IS NOT NULL;
CREATE INDEX idx_literature_records_published ON literature_records (published_on DESC);

-- The join is where "new to your library" lives, which is a different fact from
-- "recently published" and is kept in a different column for that reason.
CREATE TABLE literature_topic_records (
    id            TEXT PRIMARY KEY,
    topic_id      TEXT NOT NULL REFERENCES literature_topics (id) ON DELETE CASCADE,
    record_id     TEXT NOT NULL REFERENCES literature_records (id) ON DELETE CASCADE,
    check_id      TEXT REFERENCES literature_checks (id) ON DELETE SET NULL,
    state         TEXT NOT NULL DEFAULT 'unread' CHECK (
                      state IN ('unread', 'acknowledged', 'dismissed')
                  ),
    why_relevant  TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_literature_topic_records_unique
    ON literature_topic_records (topic_id, record_id);
CREATE INDEX idx_literature_topic_records_state ON literature_topic_records (state, first_seen_at DESC);

-- A verified link between a claim and a returned record. Written only by the
-- verifier, and only when a quote it supplied was found in the text the
-- provider actually returned.
CREATE TABLE evidence_links (
    id                TEXT PRIMARY KEY,
    learning_point_id TEXT NOT NULL REFERENCES learning_points (id) ON DELETE CASCADE,
    record_id         TEXT NOT NULL REFERENCES literature_records (id) ON DELETE CASCADE,
    relation          TEXT NOT NULL CHECK (
                          relation IN ('supports', 'contradicts', 'unclear')
                      ),
    quote             TEXT NOT NULL DEFAULT '',
    evidence_grade    TEXT NOT NULL DEFAULT 'abstract_only' CHECK (
                          evidence_grade IN ('abstract_only', 'guideline_or_full_text')
                      ),
    verified_at       TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_evidence_links_unique ON evidence_links (learning_point_id, record_id);
CREATE INDEX idx_evidence_links_record ON evidence_links (record_id);

-- --- small durable settings --------------------------------------------------

-- Scheduler state and the weekly-check preference. A table rather than a file
-- so it is inside the backup and inside the export like everything else.
CREATE TABLE app_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
