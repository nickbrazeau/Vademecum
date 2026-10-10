-- 0018 page changes asked for away from the Mac (feedback of 10 October). Pages are
-- Domi's, so an edit or a deletion made on the cloud copy cannot overwrite them
-- directly; it is recorded here, shared, and the Mac applies it at its next sync and
-- marks it applied. Additive.

CREATE TABLE page_changes (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('edit', 'revert', 'delete')),
    body_md     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    applied_at  TEXT
);

CREATE INDEX idx_page_changes_waiting ON page_changes (applied_at, created_at);

CREATE TRIGGER sync_page_changes_insert AFTER INSERT ON page_changes
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('page_changes', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_page_changes_update AFTER UPDATE ON page_changes
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('page_changes', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_page_changes_delete AFTER DELETE ON page_changes
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('page_changes', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
