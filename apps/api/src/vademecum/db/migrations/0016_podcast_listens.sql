-- 0016 podcasts listened to (ADR 0026). An episode the owner has finished drops
-- to the archive. Kept apart from the episode, which is Domi's, so finishing it
-- on the phone archives it on the Mac too; shared, as answers are. Additive.

CREATE TABLE podcast_listens (
    episode_id  TEXT PRIMARY KEY REFERENCES podcast_episodes (id) ON DELETE CASCADE,
    listened_at TEXT NOT NULL
);

CREATE TRIGGER sync_podcast_listens_insert AFTER INSERT ON podcast_listens
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('podcast_listens', json_array(NEW.episode_id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_podcast_listens_update AFTER UPDATE ON podcast_listens
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('podcast_listens', json_array(NEW.episode_id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_podcast_listens_delete AFTER DELETE ON podcast_listens
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('podcast_listens', json_array(OLD.episode_id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
