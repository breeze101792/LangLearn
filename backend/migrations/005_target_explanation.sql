-- 005_target_explanation.sql — per-row target-language usage note for
-- structures + phrases.
--
-- Distinct from `example_sentence` (structures) / `literal_translation`
-- (phrases), which are short, single-sentence target-language fields.
-- `explanation` is a paragraph-length usage note in the target language
-- describing when and why to use the structure/phrase, register,
-- common context, alternatives. The AI is the source of this column.
--
-- Per the [explanation-language rules](../design/architecture.md#explanation-language-rules),
-- the explanation_primary / explanation_secondary columns are skipped
-- when the user is learning their primary native (L == P), or when no
-- secondary is set. The `explanation` column is NOT subject to those
-- rules: it's always in the target language, always required, and is
-- the row's main "explanation" (as opposed to glosses into the user's
-- native languages).
--
-- NOT NULL is the desired shape for v1+, but existing user-added rows
-- might pre-date this column; we backfill with an empty string and
-- the application layer treats empty as "needs regeneration".

ALTER TABLE structures ADD COLUMN explanation TEXT NOT NULL DEFAULT '';
ALTER TABLE phrases    ADD COLUMN explanation TEXT NOT NULL DEFAULT '';
