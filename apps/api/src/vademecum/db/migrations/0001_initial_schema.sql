-- 0001 initial schema: tiered piles, learning items, knowledge gap flags,
-- and the curated-article table the cover sheet reads.
--
-- Every table carries a stable local id and timestamps so export, backup and a
-- later retrieval slice have something durable to key on (AGENTS.md, ADR 0003).

CREATE TABLE piles (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    tier        TEXT NOT NULL CHECK (tier IN ('low', 'mid', 'high')),
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX idx_piles_tier ON piles (tier);

CREATE TABLE learning_items (
    id           TEXT PRIMARY KEY,
    pile_id      TEXT NOT NULL REFERENCES piles (id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX idx_learning_items_pile ON learning_items (pile_id);

-- A Knowledge Gap Flag is a moment of self-identified uncertainty. Only `text`
-- is required: filing it against a topic is the system's job, not the owner's
-- (CONTEXT.md, ADR 0001).
CREATE TABLE knowledge_gap_flags (
    id          TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    topic       TEXT,
    pile_id     TEXT REFERENCES piles (id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'addressed')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    addressed_at TEXT
);

CREATE INDEX idx_flags_status_created ON knowledge_gap_flags (status, created_at DESC);
CREATE INDEX idx_flags_topic ON knowledge_gap_flags (topic);

-- Curated articles for the Today cover sheet. Nothing in this slice writes to
-- this table: no process fetches articles, so the cover sheet is empty and says
-- so rather than inventing entries (ADR 0002).
CREATE TABLE curated_articles (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    source_name  TEXT NOT NULL DEFAULT '',
    source_url   TEXT NOT NULL DEFAULT '',
    published_on TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_curated_articles_published ON curated_articles (published_on DESC);
