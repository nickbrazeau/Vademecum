-- 0006 pictures kept beside a source's text, and schematics the learner's
-- assistant draws for a learning point (ADR 0013).
--
-- Purely additive (ADR 0005). Image bytes live under attachments/images/ and
-- schematic files under attachments/schematics/, both content-addressed; the
-- rows say what they are and where they came from.

CREATE TABLE source_images (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    -- The page, slide or section the picture belongs to, as a citation names it.
    unit_index  INTEGER NOT NULL DEFAULT 0,
    locator     TEXT NOT NULL,
    -- `embedded`: an image object inside the file. `rendered`: a whole page
    -- drawn as a bitmap because it had no text layer.
    origin      TEXT NOT NULL CHECK (origin IN ('embedded', 'rendered')),
    media_type  TEXT NOT NULL,
    byte_size   INTEGER NOT NULL,
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_source_images_source ON source_images (source_id, ordinal);
CREATE INDEX idx_source_images_digest ON source_images (sha256);

-- A drawing the assistant made to explain a learning point: SVG only, checked
-- for scripts and external references before it is kept. It carries the
-- point's support label wherever it is shown; nothing drawn is verified.
CREATE TABLE schematics (
    id                TEXT PRIMARY KEY,
    learning_point_id TEXT NOT NULL REFERENCES learning_points (id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    media_type        TEXT NOT NULL DEFAULT 'image/svg+xml',
    byte_size         INTEGER NOT NULL,
    sha256            TEXT NOT NULL,
    stored_name       TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX idx_schematics_point ON schematics (learning_point_id, created_at);

-- When a source's pictures were last gathered. NULL on rows stored before
-- this migration, which is how the one-time backfill finds them.
ALTER TABLE sources ADD COLUMN images_at TEXT;
