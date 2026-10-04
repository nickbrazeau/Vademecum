-- 0012 the literature review on a page, and the dissection's record (ADR 0023,
-- second part). A page keeps the PubMed records reviewed for its topic, with
-- which of them its paragraphs drew on; the dissection agent's state lives in
-- app_state. Purely additive (ADR 0005); the review rows are Domi's.

ALTER TABLE encyclopedia_entries ADD COLUMN literature_checked_at TEXT;
ALTER TABLE encyclopedia_entries ADD COLUMN literature_note TEXT NOT NULL DEFAULT '';

CREATE TABLE encyclopedia_records (
    id         TEXT PRIMARY KEY,
    entry_id   TEXT NOT NULL REFERENCES encyclopedia_entries (id) ON DELETE CASCADE,
    record_id  TEXT NOT NULL REFERENCES literature_records (id) ON DELETE CASCADE,
    ordinal    INTEGER NOT NULL,
    cited      INTEGER NOT NULL DEFAULT 0 CHECK (cited IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (entry_id, record_id)
);

CREATE INDEX idx_encyclopedia_records_entry ON encyclopedia_records (entry_id, ordinal);

CREATE TRIGGER sync_encyclopedia_records_insert AFTER INSERT ON encyclopedia_records
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('encyclopedia_records', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_encyclopedia_records_update AFTER UPDATE ON encyclopedia_records
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('encyclopedia_records', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_encyclopedia_records_delete AFTER DELETE ON encyclopedia_records
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('encyclopedia_records', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
