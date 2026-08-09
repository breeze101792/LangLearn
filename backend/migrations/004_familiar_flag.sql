-- 004_familiar_flag.sql — per-row "I remember this" flag for structures + phrases.
--
-- Mirrors the leitner_box model on vocab_items: 0 = unfamiliar (default),
-- 1 = familiar. The Structures and Phrases pages split into two subtabs
-- (Unfamiliar / Familiar) and let the user toggle a row's flag in place.
-- The flag is intentionally independent of `source` so built-in seed rows
-- are also markable — the user can decide they've outgrown a starter row.

ALTER TABLE structures ADD COLUMN familiar INTEGER NOT NULL DEFAULT 0;
ALTER TABLE phrases    ADD COLUMN familiar INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_structures_familiar ON structures(user_id, language, familiar);
CREATE INDEX IF NOT EXISTS idx_phrases_familiar    ON phrases(user_id, language, familiar);
