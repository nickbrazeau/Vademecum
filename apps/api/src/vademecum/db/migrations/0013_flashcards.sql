-- 0013 flashcards (ADR 0024): a front and a back written from an encyclopedia
-- page, citing its points, drawn with the Improvement Map's weight behind them.
-- Cards are Domi's; reviews are given on either node. Purely additive (ADR 0005).

CREATE TABLE flashcards (
    id            TEXT PRIMARY KEY,
    entry_id      TEXT NOT NULL REFERENCES encyclopedia_entries (id) ON DELETE CASCADE,
    topic         TEXT NOT NULL,
    front         TEXT NOT NULL,
    back          TEXT NOT NULL,
    point_ids     TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL CHECK (status IN ('eligible', 'held', 'retired')),
    hold_reason   TEXT NOT NULL DEFAULT '',
    entry_version INTEGER NOT NULL DEFAULT 1,
    content_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX idx_flashcards_entry ON flashcards (entry_id, status);
CREATE UNIQUE INDEX idx_flashcards_hash ON flashcards (entry_id, content_hash);

CREATE TABLE flashcard_reviews (
    id         TEXT PRIMARY KEY,
    card_id    TEXT REFERENCES flashcards (id) ON DELETE SET NULL,
    rating     TEXT NOT NULL CHECK (rating IN ('again', 'good')),
    created_at TEXT NOT NULL
);

CREATE INDEX idx_flashcard_reviews_card ON flashcard_reviews (card_id, created_at);

CREATE TRIGGER sync_flashcards_insert AFTER INSERT ON flashcards
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('flashcards', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_flashcards_update AFTER UPDATE ON flashcards
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('flashcards', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_flashcards_delete AFTER DELETE ON flashcards
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('flashcards', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_flashcard_reviews_insert AFTER INSERT ON flashcard_reviews
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('flashcard_reviews', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_flashcard_reviews_delete AFTER DELETE ON flashcard_reviews
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('flashcard_reviews', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
