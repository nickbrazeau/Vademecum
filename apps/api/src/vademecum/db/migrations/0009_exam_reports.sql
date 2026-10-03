-- 0009 exam reports (ADR 0020): a score report the learner uploads (an ITE,
-- a Step report, a board feedback letter) and the content areas a model
-- read out of it, each with a standing and a quote the server checked
-- against the report's own text. The Improvement Map draws them. Purely
-- additive (ADR 0005); both tables are synced (triggers below, as in 0007).

CREATE TABLE exam_reports (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    byte_size     INTEGER NOT NULL,
    stored_name   TEXT NOT NULL,
    text          TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('uploaded', 'parsed', 'failed')),
    status_detail TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    parsed_at     TEXT
);

CREATE TABLE exam_areas (
    id           TEXT PRIMARY KEY,
    report_id    TEXT NOT NULL REFERENCES exam_reports (id) ON DELETE CASCADE,
    ordinal      INTEGER NOT NULL,
    topic        TEXT NOT NULL,
    specialty_id TEXT,
    standing     TEXT NOT NULL CHECK (standing IN ('below', 'at', 'above')),
    quote        TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_exam_areas_report ON exam_areas (report_id, ordinal);
CREATE INDEX idx_exam_areas_topic ON exam_areas (topic);

CREATE TRIGGER sync_exam_reports_insert AFTER INSERT ON exam_reports
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('exam_reports', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_exam_reports_update AFTER UPDATE ON exam_reports
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('exam_reports', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_exam_reports_delete AFTER DELETE ON exam_reports
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('exam_reports', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_exam_areas_insert AFTER INSERT ON exam_areas
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('exam_areas', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_exam_areas_update AFTER UPDATE ON exam_areas
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('exam_areas', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_exam_areas_delete AFTER DELETE ON exam_areas
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('exam_areas', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
