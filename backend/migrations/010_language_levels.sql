-- 010_language_levels.sql — per-language CEFR proficiency level.
--
-- The user can set their proficiency level (A1..C2, or unset) for each
-- target language. The level is sent to the LLM on every target-
-- language generation call (Analyze, Refine, Translate, Describe, seed,
-- fill, dictionary lookup, apply-explanations) so the model picks
-- vocabulary and grammar appropriate to the learner. Unset means
-- "don't tell the model anything" — the legacy behavior before this
-- column existed.
--
-- Stored as a JSON object {lang_code: "B1", ...} so adding a language
-- or a level never needs another migration. Validation lives in the
-- settings service (`_coerce` + `_clean_language_levels`).

ALTER TABLE settings ADD COLUMN language_levels_json TEXT NOT NULL DEFAULT '{}';