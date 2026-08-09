-- 001_init.sql — initial schema for LangLearn.

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS languages (
    code          TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    is_built_in   INTEGER NOT NULL DEFAULT 0,
    seeded_at     TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    user_id                INTEGER PRIMARY KEY,
    active_language        TEXT NOT NULL DEFAULT 'en',
    auto_add_vocab         INTEGER NOT NULL DEFAULT 1,
    review_session_size    INTEGER NOT NULL DEFAULT 20,
    explanation_primary    TEXT NOT NULL DEFAULT 'en',
    explanation_secondary  TEXT,
    dict_chain_json        TEXT NOT NULL DEFAULT '{}',
    theme                  TEXT NOT NULL DEFAULT 'auto',
    show_readings          INTEGER NOT NULL DEFAULT 1,
    updated_at             TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (active_language) REFERENCES languages(code),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS vocab_items (
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
    UNIQUE(user_id, language, word, sense_idx),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_vocab_due ON vocab_items(user_id, language, next_due);
CREATE INDEX IF NOT EXISTS idx_vocab_lang_word ON vocab_items(user_id, language, word);

CREATE TABLE IF NOT EXISTS structures (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER NOT NULL DEFAULT 1,
    language               TEXT NOT NULL,
    pattern                TEXT NOT NULL,
    example_sentence       TEXT,
    explanation_primary    TEXT,
    explanation_secondary  TEXT,
    source                 TEXT NOT NULL DEFAULT 'built-in',
    added_at               TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_structures_lang ON structures(user_id, language);

CREATE TABLE IF NOT EXISTS phrases (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER NOT NULL DEFAULT 1,
    language               TEXT NOT NULL,
    phrase                 TEXT NOT NULL,
    literal_translation    TEXT,
    explanation_primary    TEXT,
    explanation_secondary  TEXT,
    source                 TEXT NOT NULL DEFAULT 'built-in',
    added_at               TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_phrases_lang ON phrases(user_id, language);

CREATE TABLE IF NOT EXISTS seed_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    language     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,
    total        INTEGER,
    done         INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_seed_jobs_lang ON seed_jobs(user_id, language, kind, status);

INSERT OR IGNORE INTO users (id, username) VALUES (1, 'me');