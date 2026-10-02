-- 0003 separate a point's ACTIVE evidence basis from its retained audit history.
--
-- The defect this fixes: an accepted claim whose supporting paper is later
-- corrected is held, correctly. The owner then reopens coverage and reruns the
-- build, and the same claim is re-verified against a NEW, clean paper. The run
-- succeeds -- and the point stays held, because `settle_point` and the
-- eligibility query looked at *every* evidence link ever recorded, including the
-- corrected one. There was no way back. Recovery was impossible by construction.
--
-- The fix is to stop treating "every link ever written" as "the basis for this
-- claim". A verification pass establishes a BASIS: a numbered set of links
-- produced by one run. Only the current basis decides support and eligibility.
-- Superseded links are kept -- they are the audit trail showing what the claim
-- once rested on and why it was withdrawn -- but they no longer hold the point
-- hostage.
--
-- Forward-only and additive: 0002 may already be applied, so nothing here alters
-- or rewrites it. Existing rows are backfilled into basis 1, which is exactly
-- what they are: the current basis, established by whichever run wrote them.

ALTER TABLE evidence_links ADD COLUMN basis INTEGER NOT NULL DEFAULT 1;

-- NULL means "current". A timestamp means this link was the basis once and has
-- since been replaced; it is history, not evidence.
ALTER TABLE evidence_links ADD COLUMN superseded_at TEXT;

-- Which run established this link, so an audit row can be traced to the build
-- that produced it. Deliberately not a foreign key: a run may be pruned long
-- before the evidence it produced stops being interesting.
ALTER TABLE evidence_links ADD COLUMN generation_id TEXT;

-- Why it stopped being the basis, in the owner's terms.
ALTER TABLE evidence_links ADD COLUMN superseded_reason TEXT NOT NULL DEFAULT '';

CREATE INDEX idx_evidence_links_active
    ON evidence_links (learning_point_id, superseded_at);

-- The 0002 unique index was (learning_point_id, record_id), which cannot hold
-- once a point may carry the same record in an old basis and a new one. It is
-- replaced by a *partial* unique index covering only the current basis: one live
-- link per (point, record), any number of superseded ones.
DROP INDEX IF EXISTS idx_evidence_links_unique;
CREATE UNIQUE INDEX idx_evidence_links_current
    ON evidence_links (learning_point_id, record_id) WHERE superseded_at IS NULL;

-- The basis number a point is currently on. Bumped by each verification pass
-- that successfully establishes new evidence; unchanged by a pass that failed,
-- which is what makes a failed rerun preserve the prior basis.
ALTER TABLE learning_points ADD COLUMN evidence_basis INTEGER NOT NULL DEFAULT 1;
