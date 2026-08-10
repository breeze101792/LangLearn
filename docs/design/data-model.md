# Data Model

SQLite DDL lives in `backend/migrations/001_init.sql`. All domain tables carry
`user_id` (default 1) so multi-user is a future add without schema change.

## Tables

### users

```sql
id          INTEGER PRIMARY KEY
username    TEXT NOT NULL UNIQUE
created_at  TEXT NOT NULL DEFAULT (datetime('now'))
```

Seeded with `id=1, username='me'` by the migration.

### languages

```sql
code          TEXT PRIMARY KEY         -- 'en', 'es', 'ja', 'pt', 'zh', 'fr', 'de'
display_name  TEXT NOT NULL
is_built_in   INTEGER NOT NULL DEFAULT 0
seeded_at     TEXT                     -- ISO timestamp, null = not seeded yet
created_at    TEXT NOT NULL DEFAULT (datetime('now'))
```

The catalog (which codes we support) lives in `backend/config.py`
`LANGUAGE_CATALOG`. The table is populated lazily as the user opens each
language.

### settings

```sql
user_id                INTEGER PRIMARY KEY
active_language        TEXT NOT NULL DEFAULT 'en'
auto_add_vocab         INTEGER NOT NULL DEFAULT 1
page_size              INTEGER NOT NULL DEFAULT 20  -- review session size AND vocab list page size (5..50)
explanation_primary    TEXT NOT NULL DEFAULT 'en'
explanation_secondary  TEXT
dict_chain_json        TEXT NOT NULL DEFAULT '{}'   -- {lang: [{name, enabled}]}
theme                  TEXT NOT NULL DEFAULT 'auto' -- 'auto' | 'light' | 'dark'
show_readings          INTEGER NOT NULL DEFAULT 1   -- show romaji/kana on review cards
updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
```

`dict_chain_json` is the ordered provider list per language. Both `wordnet`
and `llm` are valid `name` values. The settings row for user 1 is created
on first boot.

### vocab_items

```sql
id                     INTEGER PRIMARY KEY AUTOINCREMENT
user_id                INTEGER NOT NULL DEFAULT 1
language               TEXT NOT NULL
word                   TEXT NOT NULL
source                 TEXT NOT NULL          -- 'wordnet' | 'llm' | 'user'
sense_idx              INTEGER NOT NULL DEFAULT 0
pos                    TEXT
glossary               TEXT NOT NULL
example                TEXT
explanation_primary    TEXT
explanation_secondary  TEXT
leitner_box            INTEGER NOT NULL DEFAULT 1   -- 1..5
next_due               TEXT NOT NULL DEFAULT (datetime('now'))
added_at               TEXT NOT NULL DEFAULT (datetime('now'))
UNIQUE(user_id, language, word, sense_idx)
```

`leitner_box` schedules next review:

| Box | Interval |
|---|---|
| 1 | 1 day  |
| 2 | 3 days |
| 3 | 7 days |
| 4 | 14 days |
| 5 | 30 days |

`'easy'` promotes (clamped at 5), `'hard'` resets to 1.

### structures

```sql
id                     INTEGER PRIMARY KEY AUTOINCREMENT
user_id                INTEGER NOT NULL DEFAULT 1
language               TEXT NOT NULL
pattern                TEXT NOT NULL
example_sentence       TEXT NOT NULL          -- target language, required
explanation            TEXT NOT NULL          -- target language, paragraph, required
explanation_primary    TEXT
explanation_secondary  TEXT
source                 TEXT NOT NULL DEFAULT 'built-in'   -- 'built-in' | 'user' | 'llm'
added_at               TEXT NOT NULL DEFAULT (datetime('now'))
```

`source='built-in'` rows are read-only. PUT/DELETE returns 403.

`pattern`, `example_sentence`, and `explanation` are always in the
target language. The two `explanation_*` columns are filled by the LLM,
but only when they add a language different from `language`. See the
[Explanation-language rules](architecture.md#explanation-language-rules)
section for the full table and the rules around when each
`explanation_*` is required vs skipped.

### phrases

```sql
id                     INTEGER PRIMARY KEY AUTOINCREMENT
user_id                INTEGER NOT NULL DEFAULT 1
language               TEXT NOT NULL
phrase                 TEXT NOT NULL
example_sentence       TEXT NOT NULL          -- target language, required (one natural sentence)
explanation            TEXT NOT NULL          -- target language, paragraph, required
explanation_primary    TEXT
explanation_secondary  TEXT
source                 TEXT NOT NULL DEFAULT 'built-in'
added_at               TEXT NOT NULL DEFAULT (datetime('now'))
```

Same read-only rule as structures, same explanation-language rules.
`phrase`, `example_sentence`, and `explanation` are all in the target
language. `example_sentence` is one natural sentence showing the
phrase in context (not a translation). `explanation` is a
paragraph-length usage note. Phrases no longer have a
`literal_translation` column — that was removed by migration 006
because for idioms and proverbs the literal rendering was almost
always identical to the phrase itself.

### seed_jobs

Tracks LLM-based seeding runs for resumption and progress reporting.
Not surfaced in v1 UI but the table exists for future streaming.

```sql
id           INTEGER PRIMARY KEY AUTOINCREMENT
user_id      INTEGER NOT NULL
language     TEXT NOT NULL
kind         TEXT NOT NULL            -- 'structures' | 'phrases'
status       TEXT NOT NULL            -- 'pending' | 'running' | 'done' | 'failed'
total        INTEGER
done         INTEGER NOT NULL DEFAULT 0
error        TEXT
started_at   TEXT NOT NULL DEFAULT (datetime('now'))
finished_at  TEXT
```

### schema_migrations

```sql
filename    TEXT PRIMARY KEY
applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
```

## Built-in JSON (`backend/data/built-in/english.json`)

```json
{
  "structures": [
    {"pattern": "Subject + be + going to + verb",
     "example_sentence": "I am going to travel next month.",
     "explanation": "Used to express future plans or intentions. Common in everyday speech and informal writing."}
  ],
  "phrases": [
    {"phrase": "How do you do?",
     "example_sentence": "How do you do? I don't think we've met.",
     "explanation": "A formal British greeting used when meeting someone for the first time. The response is conventionally 'How do you do?' rather than an answer."}
  ]
}
```

The built-in seed carries only **target-language content** including
the `explanation` paragraph and the `example_sentence`. The
`explanation_primary` and `explanation_secondary` columns are
intentionally absent — they're filled in by the AI per the user's
settings (see
[Explanation-language rules](architecture.md#explanation-language-rules)).
Loading the seed leaves those columns NULL; the user re-seeds (LLM
path) or clicks "Apply explanations" to populate them for their
specific primary/secondary natives.

The English built-in is generated via
`scripts/generate_explanations.py` (requires `OPENAI_API_KEY`).
After running, the script patches `backend/data/built-in/english.json`
in place.

Counts: 51 structures, 103 phrases. Adding another built-in language is
"drop a JSON file with the same shape; mark `is_built_in=1` in `config.py`".