-- 002_unique_word.sql — one vocab row per (user_id, language, word).
--
-- The previous schema allowed multiple rows per word (one per sense). That
-- caused the review session to show the same word N times in a row, which
-- defeats the purpose of spaced repetition. We collapse multi-sense entries
-- into a single row that stores the first sense's data and on re-lookup
-- overwrites the row in place.
--
-- Steps:
--   1. Find rows that share (user_id, language, word) and keep the lowest id.
--   2. Move any sense_idx > 0 data into sense_idx = 0 (we keep the first).
--   3. Delete the duplicates.
--   4. Replace the UNIQUE constraint with UNIQUE(user_id, language, word).

-- Collapse duplicates by promoting the oldest row's data into a single row.
-- For each (user_id, language, word) cluster, keep the row with the lowest id
-- and delete the others.
DELETE FROM vocab_items
WHERE id NOT IN (
    SELECT MIN(id) FROM vocab_items GROUP BY user_id, language, word
);

-- Recreate the table with the simpler UNIQUE constraint.
CREATE TABLE vocab_items_new (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER NOT NULL DEFAULT 1,
    language               TEXT NOT NULL,
    word                   TEXT NOT NULL,
    source                 TEXT NOT NULL,
    sense_idx              INTEGER NOT NULL DEFAULT 0,
    pos                    TEXT,
    glossary               TEXT NOT NULL,
    example                TEXT,
    explanation_primary    TEXT,
    explanation_secondary  TEXT,
    leitner_box            INTEGER NOT NULL DEFAULT 1,
    next_due               TEXT NOT NULL DEFAULT (datetime('now')),
    added_at               TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, language, word),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
INSERT INTO vocab_items_new
    (id, user_id, language, word, source, sense_idx, pos, glossary, example,
     explanation_primary, explanation_secondary, leitner_box, next_due, added_at)
SELECT
    id, user_id, language, word, source, sense_idx, pos, glossary, example,
    explanation_primary, explanation_secondary, leitner_box, next_due, added_at
FROM vocab_items;
DROP TABLE vocab_items;
ALTER TABLE vocab_items_new RENAME TO vocab_items;

CREATE INDEX IF NOT EXISTS idx_vocab_due ON vocab_items(user_id, language, next_due);
CREATE INDEX IF NOT EXISTS idx_vocab_lang_word ON vocab_items(user_id, language, word);