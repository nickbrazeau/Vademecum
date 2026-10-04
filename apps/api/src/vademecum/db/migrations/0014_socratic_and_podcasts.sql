-- 0014 the Socratic tutor and the podcast generator (ADR 0025). A Socratic
-- session is a dialogue about one page, run by the assistant in a chat host
-- or by the Mac's own model connection, ending in an assessment that feeds
-- the map. A podcast episode is a script written from pages and, on the Mac,
-- rendered to audio on-device. Purely additive (ADR 0005). Sessions are
-- shared (either node may run one); episodes are Domi's.

CREATE TABLE socratic_sessions (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT REFERENCES encyclopedia_entries (id) ON DELETE SET NULL,
    topic       TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    mode        TEXT NOT NULL CHECK (mode IN ('host', 'codex', 'claude')),
    status      TEXT NOT NULL CHECK (status IN ('open', 'done', 'abandoned')),
    transcript  TEXT NOT NULL DEFAULT '[]',
    assessment  TEXT NOT NULL DEFAULT '{}',
    exchanges   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX idx_socratic_sessions_status ON socratic_sessions (status, updated_at);

CREATE TABLE podcast_episodes (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL CHECK (status IN ('draft', 'scripted', 'rendered', 'failed')),
    status_detail    TEXT NOT NULL DEFAULT '',
    entry_ids        TEXT NOT NULL DEFAULT '[]',
    script           TEXT NOT NULL DEFAULT '[]',
    takeaways        TEXT NOT NULL DEFAULT '[]',
    voices           TEXT NOT NULL DEFAULT '{}',
    audio_name       TEXT NOT NULL DEFAULT '',
    audio_bytes      INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX idx_podcast_episodes_created ON podcast_episodes (created_at DESC);

CREATE TRIGGER sync_socratic_sessions_insert AFTER INSERT ON socratic_sessions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('socratic_sessions', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_socratic_sessions_update AFTER UPDATE ON socratic_sessions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('socratic_sessions', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_socratic_sessions_delete AFTER DELETE ON socratic_sessions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('socratic_sessions', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_podcast_episodes_insert AFTER INSERT ON podcast_episodes
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('podcast_episodes', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_podcast_episodes_update AFTER UPDATE ON podcast_episodes
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('podcast_episodes', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_podcast_episodes_delete AFTER DELETE ON podcast_episodes
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('podcast_episodes', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
