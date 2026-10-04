-- 0015 the owner's feedback of 4 October (ADR 0026). A page can carry the
-- owner's own edit, in Markdown, beside the compiled text, and is mirrored to a
-- Markdown file in the source folder; a case can be acknowledged once seen on
-- Today; an episode records the sources it was written from; and review
-- events count what the owner reviewed for the dashboard on Today. Purely
-- additive (ADR 0005). Review events are shared, as answers are.

ALTER TABLE encyclopedia_entries ADD COLUMN body_md TEXT NOT NULL DEFAULT '';
ALTER TABLE encyclopedia_entries ADD COLUMN edited_at TEXT;
ALTER TABLE case_entries ADD COLUMN acknowledged_at TEXT;
ALTER TABLE podcast_episodes ADD COLUMN sources TEXT NOT NULL DEFAULT '[]';

CREATE TABLE review_events (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('page')),
    ref_id     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_review_events_created ON review_events (created_at);

CREATE TRIGGER sync_review_events_insert AFTER INSERT ON review_events
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('review_events', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_review_events_delete AFTER DELETE ON review_events
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('review_events', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
