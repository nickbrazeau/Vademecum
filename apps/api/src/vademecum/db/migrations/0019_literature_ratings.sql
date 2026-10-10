-- 0019 the owner's thumbs on papers (feedback of 10 October). Up or down, one per
-- paper, used to rank what Today shows next. Shared, so a rating made on the phone
-- reaches the Mac; kept apart from the records, which are Domi's. Additive.

CREATE TABLE literature_ratings (
    record_id  TEXT PRIMARY KEY REFERENCES literature_records (id) ON DELETE CASCADE,
    rating     INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    updated_at TEXT NOT NULL
);

CREATE TRIGGER sync_literature_ratings_insert AFTER INSERT ON literature_ratings
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_ratings', json_array(NEW.record_id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_ratings_update AFTER UPDATE ON literature_ratings
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_ratings', json_array(NEW.record_id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_ratings_delete AFTER DELETE ON literature_ratings
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_ratings', json_array(OLD.record_id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
