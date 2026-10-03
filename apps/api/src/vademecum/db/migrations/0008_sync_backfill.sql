-- 0008 every row that existed before the change log did, entered into it
-- (ADR 0015). Migration 0007 added the log and its triggers, which record
-- changes from then on; a workspace with records already in it would never
-- have pushed them to a peer. One entry per existing row, as an upsert, so
-- the first sync after this carries the whole workspace. Purely additive.

INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'piles', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM piles;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'learning_items', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM learning_items;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'knowledge_gap_flags', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM knowledge_gap_flags;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'curated_articles', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM curated_articles;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'sources', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM sources;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'source_segments', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM source_segments;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'source_images', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM source_images;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'generation_runs', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM generation_runs;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'build_batches', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM build_batches;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'learning_points', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM learning_points;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'learning_point_sources', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM learning_point_sources;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'learning_point_topics', json_array(learning_point_id, topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM learning_point_topics;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'schematics', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM schematics;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'tutor_questions', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM tutor_questions;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'tutor_question_anchors', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM tutor_question_anchors;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'tutor_cycle_entries', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM tutor_cycle_entries;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'tutor_attempts', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM tutor_attempts;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'literature_topics', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM literature_topics;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'literature_checks', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM literature_checks;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'literature_records', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM literature_records;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'literature_topic_records', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM literature_topic_records;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'evidence_links', json_array(id), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM evidence_links;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'app_state', json_array(key), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM app_state;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'topic_specialties', json_array(topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM topic_specialties;
INSERT INTO sync_log (table_name, row_key, op, at) SELECT 'map_positions', json_array(topic), 'upsert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM map_positions;
