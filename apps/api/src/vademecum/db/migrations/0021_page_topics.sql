-- 0021 one page per subject (feedback of 11 October). Pages were one per topic string, and
-- topic strings are the model's free wording, so "Antifungal mechanism", "Antifungal
-- mechanisms" and "Antifungal spectrum" were three pages. A topic listed here belongs to
-- the page whose topic is its subject; a page gathers the points of all its topics. The
-- Mac's, like the pages. Additive.

CREATE TABLE page_topics (
    topic      TEXT PRIMARY KEY,
    subject    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_page_topics_subject ON page_topics (subject);

CREATE TRIGGER sync_page_topics_insert AFTER INSERT ON page_topics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('page_topics', json_array(NEW.topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_page_topics_update AFTER UPDATE ON page_topics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('page_topics', json_array(NEW.topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_page_topics_delete AFTER DELETE ON page_topics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('page_topics', json_array(OLD.topic), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
