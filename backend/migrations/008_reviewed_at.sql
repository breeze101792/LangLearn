-- 008_reviewed_at.sql — track when each vocab item was last reviewed.
--
-- The Leitner box system already knows *which* box a word is in and *when* it
-- is next due (via next_due), but it never records *when* the last review
-- happened. That timestamp is needed to answer "which words did I review
-- today" for the Review page's "Reviewed words" subpage.
--
-- reviewed_at is NULL until the first grade. It is set to the review time on
-- every grade (easy or hard).

ALTER TABLE vocab_items ADD COLUMN reviewed_at TEXT;
