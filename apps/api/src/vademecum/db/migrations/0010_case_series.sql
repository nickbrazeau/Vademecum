-- 0010 the Case Series hub (ADR 0022): other people's teaching cases, kept
-- by series, title and link, with the publisher's public show notes bounded
-- beside them so the teaching points a model writes can be checked against
-- the text they rest on. Purely additive (ADR 0005); synced from Domi
-- (triggers below, as in 0007).

CREATE TABLE case_entries (
    id             TEXT PRIMARY KEY,
    series         TEXT NOT NULL,
    subseries      TEXT NOT NULL DEFAULT '',
    external_id    TEXT NOT NULL,
    title          TEXT NOT NULL,
    url            TEXT NOT NULL,
    credit         TEXT NOT NULL DEFAULT '',
    published_on   TEXT,
    text           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL CHECK (status IN ('new', 'synthesised', 'failed')),
    status_detail  TEXT NOT NULL DEFAULT '',
    attempts       INTEGER NOT NULL DEFAULT 0,
    one_liner      TEXT NOT NULL DEFAULT '',
    points         TEXT NOT NULL DEFAULT '[]',
    think_first    TEXT NOT NULL DEFAULT '[]',
    specialty_id   TEXT,
    synthesised_at TEXT,
    first_seen_at  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (series, external_id)
);

CREATE INDEX idx_case_entries_series_date ON case_entries (series, published_on DESC);
CREATE INDEX idx_case_entries_status ON case_entries (status);

CREATE TRIGGER sync_case_entries_insert AFTER INSERT ON case_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('case_entries', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_case_entries_update AFTER UPDATE ON case_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('case_entries', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_case_entries_delete AFTER DELETE ON case_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('case_entries', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
