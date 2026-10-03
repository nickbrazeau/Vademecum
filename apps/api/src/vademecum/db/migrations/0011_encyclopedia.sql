-- 0011 the encyclopedia and the board bank (ADR 0023). A pile's learning
-- points are compiled, topic by topic, into an encyclopedia page whose every
-- paragraph names the points it rests on; board-style questions are written
-- from a page and cite its points. Purely additive (ADR 0005). Pages and
-- questions are Domi's (triggers below, as in 0007); the cycle and the
-- answers are shared, as the Tutor's are.

CREATE TABLE encyclopedia_entries (
    id            TEXT PRIMARY KEY,
    topic         TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    specialty_id  TEXT,
    summary       TEXT NOT NULL DEFAULT '',
    sections      TEXT NOT NULL DEFAULT '[]',
    point_ids     TEXT NOT NULL DEFAULT '[]',
    points_hash   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL CHECK (status IN ('current', 'failed')),
    status_detail TEXT NOT NULL DEFAULT '',
    version       INTEGER NOT NULL DEFAULT 1,
    compiled_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE board_questions (
    id            TEXT PRIMARY KEY,
    entry_id      TEXT NOT NULL REFERENCES encyclopedia_entries (id) ON DELETE CASCADE,
    topic         TEXT NOT NULL,
    stem          TEXT NOT NULL,
    options       TEXT NOT NULL,
    answer_index  INTEGER NOT NULL CHECK (answer_index BETWEEN 0 AND 4),
    explanation   TEXT NOT NULL,
    objective     TEXT NOT NULL DEFAULT '',
    point_ids     TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL CHECK (status IN ('eligible', 'held', 'retired')),
    hold_reason   TEXT NOT NULL DEFAULT '',
    entry_version INTEGER NOT NULL DEFAULT 1,
    content_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX idx_board_questions_entry ON board_questions (entry_id, status);
CREATE UNIQUE INDEX idx_board_questions_hash ON board_questions (entry_id, content_hash);

CREATE TABLE board_cycle_entries (
    id           TEXT PRIMARY KEY,
    cycle_number INTEGER NOT NULL,
    position     INTEGER NOT NULL,
    question_id  TEXT NOT NULL REFERENCES board_questions (id) ON DELETE CASCADE,
    served_at    TEXT,
    created_at   TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_board_cycle_unique ON board_cycle_entries (cycle_number, question_id);
CREATE INDEX idx_board_cycle_order ON board_cycle_entries (cycle_number, position);

CREATE TABLE board_attempts (
    id           TEXT PRIMARY KEY,
    question_id  TEXT REFERENCES board_questions (id) ON DELETE SET NULL,
    asked_stem   TEXT NOT NULL DEFAULT '',
    chosen_index INTEGER NOT NULL,
    correct      INTEGER NOT NULL CHECK (correct IN (0, 1)),
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_board_attempts_question ON board_attempts (question_id, created_at);

CREATE TRIGGER sync_encyclopedia_entries_insert AFTER INSERT ON encyclopedia_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('encyclopedia_entries', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_encyclopedia_entries_update AFTER UPDATE ON encyclopedia_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('encyclopedia_entries', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_encyclopedia_entries_delete AFTER DELETE ON encyclopedia_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('encyclopedia_entries', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_board_questions_insert AFTER INSERT ON board_questions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('board_questions', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_board_questions_update AFTER UPDATE ON board_questions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('board_questions', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_board_questions_delete AFTER DELETE ON board_questions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('board_questions', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_board_cycle_entries_insert AFTER INSERT ON board_cycle_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('board_cycle_entries', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_board_cycle_entries_update AFTER UPDATE ON board_cycle_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('board_cycle_entries', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_board_cycle_entries_delete AFTER DELETE ON board_cycle_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('board_cycle_entries', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_board_attempts_insert AFTER INSERT ON board_attempts
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('board_attempts', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_board_attempts_delete AFTER DELETE ON board_attempts
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('board_attempts', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
