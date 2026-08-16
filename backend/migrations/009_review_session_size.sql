-- 009_review_session_size.sql — split review session size from page size.
--
-- page_size used to cover both the Review session length and the page
-- size for list views (Vocabulary, Structures, Phrases). They are
-- different concerns: a review session is a focused study batch and
-- tends to be larger, while list pages are browse-sized. Splitting lets
-- the user tune them independently.
--
-- The new column review_session_size defaults to 30, the new default
-- review session length. page_size keeps its existing default (20) and
-- remains the list-page size. Bounds (5..50) are enforced by the
-- settings service for both columns.

ALTER TABLE settings ADD COLUMN review_session_size INTEGER NOT NULL DEFAULT 30;

-- Backfill existing rows so a user who already raised page_size to use
-- it as a review size keeps a similar session length on first load. If
-- page_size is still the default 20, leave review_session_size at the
-- new default 30 so the user gets the larger default.
UPDATE settings
   SET review_session_size = page_size
 WHERE page_size > 20;