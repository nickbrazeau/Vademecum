-- 0004 the Improvement Map's specialty taxonomy and remembered layout.
--
-- Two things the map needs that nothing stored before:
--
-- 1. A SPECIALTY per topic, so the graph can colour by the clinical field a
--    gap belongs to rather than by which pile happened to produce its points.
--    The fourteen specialties are the set the predecessor settled on
--    (Vademecum_arxive, its ADR 0007): the Infographic Atlas's eleven plus
--    Hematology, General Internal Medicine and Psychiatry, which the historical
--    gap log needed. Re-entered here as seed data; nothing is read from the
--    archive at runtime (ADR 0001). The list is a starting taxonomy, not a
--    frozen enum: rows may be added.
--
-- 2. Where each topic SAT last time. A force layout is recomputed on every
--    open; without remembered positions the picture rearranges itself as
--    topics are added and the owner's spatial memory is worthless. Positions
--    are in the graph's own units, one row per topic, and are pruned when a
--    topic disappears from the map.
--
-- A topic's specialty is the owner's call (`assigned_by = 'owner'`). The map
-- also offers a name match -- a topic literally called "Nephrology" is
-- nephrology -- but that inference is computed on read and never written,
-- so a guess can never masquerade as a decision.

CREATE TABLE specialties (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL
);

INSERT INTO specialties (id, name, sort_order) VALUES
    ('general-internal-medicine', 'General Internal Medicine', 1),
    ('cardiology',                'Cardiology',                2),
    ('pulmonology',               'Pulmonology',               3),
    ('gastroenterology',          'Gastroenterology',          4),
    ('nephrology',                'Nephrology',                5),
    ('endocrinology',             'Endocrinology',             6),
    ('rheumatology',              'Rheumatology',              7),
    ('allergy-immunology',        'Allergy & Immunology',      8),
    ('infectious-disease',        'Infectious Disease',        9),
    ('neurology',                 'Neurology',                 10),
    ('hematology',                'Hematology',                11),
    ('oncology',                  'Oncology',                  12),
    ('dermatology',               'Dermatology',               13),
    ('psychiatry',                'Psychiatry',                14);

CREATE TABLE topic_specialties (
    topic        TEXT PRIMARY KEY,
    specialty_id TEXT NOT NULL REFERENCES specialties (id) ON DELETE CASCADE,
    assigned_by  TEXT NOT NULL DEFAULT 'owner' CHECK (assigned_by IN ('owner')),
    updated_at   TEXT NOT NULL
);

CREATE INDEX idx_topic_specialties_specialty ON topic_specialties (specialty_id);

CREATE TABLE map_positions (
    topic      TEXT PRIMARY KEY,
    x          REAL NOT NULL,
    y          REAL NOT NULL,
    updated_at TEXT NOT NULL
);
