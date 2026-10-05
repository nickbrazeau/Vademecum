-- 0017 an episode asked for in the owner's own words (ADR 0027): what was asked,
-- and that it is voiced as soon as its script is written. Additive.

ALTER TABLE podcast_episodes ADD COLUMN request TEXT NOT NULL DEFAULT '';
ALTER TABLE podcast_episodes ADD COLUMN auto_render INTEGER NOT NULL DEFAULT 0;
