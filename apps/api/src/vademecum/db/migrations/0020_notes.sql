-- 0020 the owner's own notes (ADR 0032, feedback of 10 October): notebooks and notes
-- nested within them, in Markdown, written on the Mac or the phone. Shared, as answers
-- are; mirrored on the Mac to Markdown files in the source folder. A note marked
-- use_as_source is also given to the builder as a low-confidence source. Additive.

CREATE TABLE notes (
    id            TEXT PRIMARY KEY,
    parent_id     TEXT REFERENCES notes (id) ON DELETE CASCADE,
    notebook      INTEGER NOT NULL DEFAULT 0 CHECK (notebook IN (0, 1)),
    title         TEXT NOT NULL,
    body_md       TEXT NOT NULL DEFAULT '',
    position      REAL NOT NULL DEFAULT 0,
    use_as_source INTEGER NOT NULL DEFAULT 0 CHECK (use_as_source IN (0, 1)),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX idx_notes_parent ON notes (parent_id, position);

CREATE TRIGGER sync_notes_insert AFTER INSERT ON notes
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('notes', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_notes_update AFTER UPDATE ON notes
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('notes', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_notes_delete AFTER DELETE ON notes
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('notes', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
