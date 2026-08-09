-- 003_rename_page_size.sql — generalize review_session_size to page_size.
--
-- The setting started life as the size of a Review session, but the same
-- value is now used as the page size for list views (e.g. Vocabulary), so
-- the column gets a more general name. Bounds (5..50) and default (20)
-- are unchanged; only the column name changes.
--
-- SQLite supports RENAME COLUMN since 3.25 (2018). Python 3.13 ships a
-- newer SQLite, so this is portable across all supported runtimes.

ALTER TABLE settings RENAME COLUMN review_session_size TO page_size;
