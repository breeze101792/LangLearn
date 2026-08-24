-- 011_installed_dictionaries.sql — per-user install state for offline dictionaries.
--
-- A "dictionary" in this app is two things: a provider function (in
-- `backend/services/dictionaries/`) and an install marker (this table).
-- Providers are only registered with the chain executor when they're
-- marked installed here. WordNet for English is auto-installed on first
-- run; everything else starts uninstalled and the user installs it
-- through the Settings UI when they're ready.
--
-- Schema notes:
--   * `provider` matches a registry key (e.g. "wordnet"). Adding a new
--     offline dictionary is a code change that introduces the provider;
--     this table just records whether the user opted in.
--   * `language` is the catalog language code (`config.LANGUAGE_CATALOG`).
--     WordNet only ships English; a hypothetical "FreeDict spa-deu" would
--     be installed once for `es` with metadata noting it covers more.
--   * composite PK prevents duplicate install rows for the same pair.
--   * no user_id column in v1; single-user. The chain executor reads
--     `DEFAULT_USER_ID`'s install set; multi-user support would add a
--     user_id column and a composite PK.

CREATE TABLE IF NOT EXISTS installed_dictionaries (
    provider      TEXT NOT NULL,
    language      TEXT NOT NULL,
    installed_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (provider, language)
);

CREATE INDEX IF NOT EXISTS idx_installed_dict_lang
    ON installed_dictionaries(language);
