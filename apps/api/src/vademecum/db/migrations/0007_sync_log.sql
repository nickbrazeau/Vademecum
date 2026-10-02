-- 0007 the change log that sync starts from (ADR 0015).
--
-- Every synced table gets three triggers that record which row changed and
-- how. The payload is not copied here: at push time the row is read as it
-- is then, so ten edits to one flag travel as one change. `sync_state` is one
-- row: this node's identity, where it stands with its peer, and the flag the
-- triggers consult so that changes *applied from the peer* are not logged
-- again and bounced back. Purely additive (ADR 0005).

CREATE TABLE sync_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    node_id         TEXT NOT NULL,
    applying        INTEGER NOT NULL DEFAULT 0,
    peer_node_id    TEXT,
    pulled_through  INTEGER NOT NULL DEFAULT 0,
    pushed_through  INTEGER NOT NULL DEFAULT 0,
    last_sync_at    TEXT,
    last_sync_note  TEXT
);

INSERT INTO sync_state (id, node_id) VALUES (1, 'node_' || lower(hex(randomblob(12))));

CREATE TABLE sync_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name  TEXT NOT NULL,
    row_key     TEXT NOT NULL,
    op          TEXT NOT NULL CHECK (op IN ('upsert', 'delete')),
    at          TEXT NOT NULL
);

CREATE INDEX idx_sync_log_row ON sync_log (table_name, row_key, seq);

CREATE TRIGGER sync_piles_insert AFTER INSERT ON piles
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('piles', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_piles_update AFTER UPDATE ON piles
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('piles', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_piles_delete AFTER DELETE ON piles
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('piles', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_items_insert AFTER INSERT ON learning_items
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_items', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_items_update AFTER UPDATE ON learning_items
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_items', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_items_delete AFTER DELETE ON learning_items
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_items', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_knowledge_gap_flags_insert AFTER INSERT ON knowledge_gap_flags
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('knowledge_gap_flags', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_knowledge_gap_flags_update AFTER UPDATE ON knowledge_gap_flags
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('knowledge_gap_flags', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_knowledge_gap_flags_delete AFTER DELETE ON knowledge_gap_flags
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('knowledge_gap_flags', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_curated_articles_insert AFTER INSERT ON curated_articles
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('curated_articles', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_curated_articles_update AFTER UPDATE ON curated_articles
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('curated_articles', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_curated_articles_delete AFTER DELETE ON curated_articles
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('curated_articles', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_sources_insert AFTER INSERT ON sources
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('sources', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_sources_update AFTER UPDATE ON sources
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('sources', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_sources_delete AFTER DELETE ON sources
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('sources', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_source_segments_insert AFTER INSERT ON source_segments
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('source_segments', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_source_segments_update AFTER UPDATE ON source_segments
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('source_segments', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_source_segments_delete AFTER DELETE ON source_segments
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('source_segments', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_source_images_insert AFTER INSERT ON source_images
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('source_images', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_source_images_update AFTER UPDATE ON source_images
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('source_images', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_source_images_delete AFTER DELETE ON source_images
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('source_images', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_generation_runs_insert AFTER INSERT ON generation_runs
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('generation_runs', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_generation_runs_update AFTER UPDATE ON generation_runs
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('generation_runs', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_generation_runs_delete AFTER DELETE ON generation_runs
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('generation_runs', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_build_batches_insert AFTER INSERT ON build_batches
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('build_batches', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_build_batches_update AFTER UPDATE ON build_batches
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('build_batches', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_build_batches_delete AFTER DELETE ON build_batches
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('build_batches', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_points_insert AFTER INSERT ON learning_points
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_points', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_points_update AFTER UPDATE ON learning_points
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_points', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_points_delete AFTER DELETE ON learning_points
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_points', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_point_sources_insert AFTER INSERT ON learning_point_sources
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_point_sources', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_point_sources_update AFTER UPDATE ON learning_point_sources
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_point_sources', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_point_sources_delete AFTER DELETE ON learning_point_sources
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_point_sources', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_point_topics_insert AFTER INSERT ON learning_point_topics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_point_topics', json_array(NEW.learning_point_id, NEW.topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_point_topics_update AFTER UPDATE ON learning_point_topics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_point_topics', json_array(NEW.learning_point_id, NEW.topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_learning_point_topics_delete AFTER DELETE ON learning_point_topics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('learning_point_topics', json_array(OLD.learning_point_id, OLD.topic), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_schematics_insert AFTER INSERT ON schematics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('schematics', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_schematics_update AFTER UPDATE ON schematics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('schematics', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_schematics_delete AFTER DELETE ON schematics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('schematics', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_questions_insert AFTER INSERT ON tutor_questions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_questions', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_questions_update AFTER UPDATE ON tutor_questions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_questions', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_questions_delete AFTER DELETE ON tutor_questions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_questions', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_question_anchors_insert AFTER INSERT ON tutor_question_anchors
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_question_anchors', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_question_anchors_update AFTER UPDATE ON tutor_question_anchors
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_question_anchors', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_question_anchors_delete AFTER DELETE ON tutor_question_anchors
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_question_anchors', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_cycle_entries_insert AFTER INSERT ON tutor_cycle_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_cycle_entries', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_cycle_entries_update AFTER UPDATE ON tutor_cycle_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_cycle_entries', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_cycle_entries_delete AFTER DELETE ON tutor_cycle_entries
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_cycle_entries', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_attempts_insert AFTER INSERT ON tutor_attempts
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_attempts', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_attempts_update AFTER UPDATE ON tutor_attempts
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_attempts', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_tutor_attempts_delete AFTER DELETE ON tutor_attempts
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('tutor_attempts', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_topics_insert AFTER INSERT ON literature_topics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_topics', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_topics_update AFTER UPDATE ON literature_topics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_topics', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_topics_delete AFTER DELETE ON literature_topics
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_topics', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_checks_insert AFTER INSERT ON literature_checks
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_checks', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_checks_update AFTER UPDATE ON literature_checks
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_checks', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_checks_delete AFTER DELETE ON literature_checks
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_checks', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_records_insert AFTER INSERT ON literature_records
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_records', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_records_update AFTER UPDATE ON literature_records
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_records', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_records_delete AFTER DELETE ON literature_records
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_records', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_topic_records_insert AFTER INSERT ON literature_topic_records
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_topic_records', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_topic_records_update AFTER UPDATE ON literature_topic_records
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_topic_records', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_literature_topic_records_delete AFTER DELETE ON literature_topic_records
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('literature_topic_records', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_evidence_links_insert AFTER INSERT ON evidence_links
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('evidence_links', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_evidence_links_update AFTER UPDATE ON evidence_links
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('evidence_links', json_array(NEW.id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_evidence_links_delete AFTER DELETE ON evidence_links
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('evidence_links', json_array(OLD.id), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_app_state_insert AFTER INSERT ON app_state
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('app_state', json_array(NEW.key), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_app_state_update AFTER UPDATE ON app_state
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('app_state', json_array(NEW.key), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_app_state_delete AFTER DELETE ON app_state
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('app_state', json_array(OLD.key), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_topic_specialties_insert AFTER INSERT ON topic_specialties
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('topic_specialties', json_array(NEW.topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_topic_specialties_update AFTER UPDATE ON topic_specialties
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('topic_specialties', json_array(NEW.topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_topic_specialties_delete AFTER DELETE ON topic_specialties
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('topic_specialties', json_array(OLD.topic), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_map_positions_insert AFTER INSERT ON map_positions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('map_positions', json_array(NEW.topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_map_positions_update AFTER UPDATE ON map_positions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('map_positions', json_array(NEW.topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
CREATE TRIGGER sync_map_positions_delete AFTER DELETE ON map_positions
WHEN (SELECT applying FROM sync_state WHERE id = 1) = 0
BEGIN
    INSERT INTO sync_log (table_name, row_key, op, at) VALUES ('map_positions', json_array(OLD.topic), 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;
