-- 0005 pending turns: the model work that ChatGPT does (ADR 0009, phase 1).
--
-- In host mode Vademecum never calls a model. Each turn the pipeline needs --
-- synthesis, evidence, assessment, grading -- becomes a row here: exactly
-- what the model may look at, the schema its answer must match, and a
-- deadline. The learner's ChatGPT reads the row through a tool and submits a
-- result through another; the submission is validated against the schema,
-- the row is marked submitted, and the pipeline continues.
--
-- Purely additive (ADR 0005). Rows are transient: a startup sweep abandons
-- whatever was pending when the process stopped, because the waiting task is
-- gone with it, and a run whose turn was abandoned fails honestly.
CREATE TABLE pending_turns (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (
                      kind IN ('synthesis', 'evidence', 'assessment', 'grading')
                  ),
    -- What the turn belongs to: a generation run, or a Tutor question.
    scope_kind    TEXT NOT NULL CHECK (scope_kind IN ('run', 'question')),
    scope_id      TEXT NOT NULL,
    -- The exchange, as JSON: instructions, rules, material, output_schema.
    payload       TEXT NOT NULL,
    -- What the server needs to finish the turn once the answer arrives, as
    -- JSON. For grading: the snapshot of what was asked and the typed answer.
    context       TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL CHECK (
                      status IN ('pending', 'submitted', 'expired', 'abandoned')
                  ),
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    submitted_at  TEXT
);

CREATE INDEX idx_pending_turns_scope ON pending_turns (scope_kind, scope_id, status);
