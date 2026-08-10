-- 006_phrases_example_sentence.sql — phrases now have a single
-- `example_sentence` column (in the target language), mirroring
-- structures. The `literal_translation` column is dropped; it
-- carried a word-for-word rendering that, for idioms and proverbs,
-- was almost always identical to the phrase itself and added noise.
--
-- Migration steps:
--  1. Add `example_sentence` as NOT NULL with default '' (so existing
--     rows pass the constraint). Backfill from `literal_translation`
--     so the column carries something useful immediately.
--  2. Drop the `literal_translation` column.
--
-- SQLite (>= 3.35) supports ALTER TABLE DROP COLUMN. Phrases has no
-- foreign key constraints referencing literal_translation.

ALTER TABLE phrases ADD COLUMN example_sentence TEXT NOT NULL DEFAULT '';

UPDATE phrases SET example_sentence = COALESCE(literal_translation, '');

ALTER TABLE phrases DROP COLUMN literal_translation;
