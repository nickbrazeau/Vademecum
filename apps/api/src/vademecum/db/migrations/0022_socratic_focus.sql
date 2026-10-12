-- 0022 what a Socratic session is about (feedback of 12 October): a case drawn from a page,
-- as before, or a knowledge gap the learner flagged -- its flag, and its words as the tutor
-- was given them. Shared, like the sessions. Additive.

ALTER TABLE socratic_sessions ADD COLUMN focus_flag_id TEXT;
ALTER TABLE socratic_sessions ADD COLUMN focus TEXT NOT NULL DEFAULT '';
