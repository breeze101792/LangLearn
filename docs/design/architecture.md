# Architecture Overview

## Goal

Single-user (v1) multi-language learning web app. Lets the user look up
words via a configurable chain of dictionaries, memorize them through a
Leitner-style review loop, and learn common sentence structures & phrases
seeded per language. LLM is used to generate content (definitions,
structures, phrases) in a strict structured format.

## High-Level Component Diagram

```
Browser (vanilla HTML/CSS/JS, hash routing)
   │  fetch /api/*   (JSON, credentials: same-origin)
   ▼
Flask app factory (backend/app.py)
   │
   ├── blueprints/                  HTTP shape only
   │     ├── auth.py                stub for v1 (multi-user-ready)
   │     ├── dictionary.py          POST /api/dictionary/lookup
   │     ├── vocab.py               /api/vocab, /api/vocab/review/*
   │     ├── structures.py          /api/structures, /api/structures/fill
   │     ├── phrases.py             /api/phrases, /api/phrases/fill
   │     ├── languages.py           /api/languages, /api/languages/<code>/initialize
   │     └── settings.py            /api/settings
   │
   ├── services/                    Business logic, no HTTP knowledge
    │     ├── llm.py                 OpenAI-compatible client
   │     ├── dictionaries/
   │     │     ├── base.py          WordEntry, Sense dataclasses
   │     │     ├── wordnet.py       NLTK WordNet provider (English only)
   │     │     ├── llm.py           LLM provider
   │     │     └── registry.py      Chain executor
   │     ├── leitner.py             5-box scheduler
   │     ├── seed.py                Built-in loader + LLM seed gen
   │     ├── vocab.py               CRUD + auto-add from lookup
   │     └── settings.py            Settings load/save
   │
   ├── db.py                        SQLite layer + migration runner
   ├── util.py                      ok(), err(), safe_path(), validators
   └── config.py                    paths, defaults, env vars

data/langlearn.sqlite              # runtime DB (gitignored)
backend/data/built-in/english.json # static English seed (committed)
browser localStorage               # LLM dict cache (one entry per lang+word)
```

## Execution Flow

1. User opens `/` → served a single-page layout with hash-based routing
   (`#/dictionary`, `#/review`, `#/structures`, `#/phrases`, `#/settings`).
2. Frontend hits `GET /api/settings` and `GET /api/languages` to render nav.
3. First time a language is opened (no seed rows for it):
   `POST /api/languages/{code}/initialize` runs automatically; English uses
   built-in JSON, others call `services.seed.seed_via_llm`. Manual "Re-seed"
   button in Settings triggers the same endpoint with `force=true`.
4. Dictionary lookup: `POST /api/dictionary/lookup {lang, word}` walks the
   user's per-language dictionary chain. First non-empty result wins. LLM
   provider writes to browser localStorage via response so the client caches.
5. Vocab auto-add: when the lookup response includes `auto_added=true`, the
   frontend shows an "Added to vocab — undo" toast (3s).
6. Review: `GET /api/vocab/review/next?n=20` returns due words; user grades
   easy/hard; `POST /api/vocab/review/grade` updates Leitner box and
   next-due timestamp.

## Module Boundaries

- **Blueprints** do HTTP shape only: validate input, call a service, return
  `{ok, data}` or `{ok:false, error}`.
- **Services** do business logic and own no HTTP knowledge.
- **db.py** exposes a single `get_conn()` context manager and the migration
  runner. No ORM.
- **llm.py** abstracts OpenAI-compatible and Ollama. Caller passes a
  `response_format` JSON schema; the client returns parsed dict or raises
  `LLMError`. One retry on schema failure.
- **Dictionaries** are provider classes implementing
  `lookup(word, lang, **kwargs) -> WordEntry`. The chain is just a list of
  `{name, enabled}` rows; first non-empty wins.

## Single-User v1, Multi-User-Ready

Every domain table has `user_id` (default 1). A single seeded user row
exists from migration time. Adding login later = add a `users.password_hash`
column + `sessions` table, swap `user_id=1` for current session, add an
auth blueprint. No data migration needed.

## Key Design Decisions

| Decision | Why |
|---|---|
| Single-page app with hash routing | Matches "no framework" rule; one HTML file, JS modules under `static/`. |
| Per-language dict chain in DB | User can reorder/disable providers per language without code change. |
| WordNet + LLM as sibling providers, not "fallback" | Both are first-class. Chain order controls preference. |
| LLM cache in localStorage only | Avoids backend storage; one cache entry per `(lang, word)`. |
| LLM-assisted fill for user-added items | Reduces typing; LLM still respects the JSON schema. |
| Leitner 5 boxes, default 20/session | User picked Leitner; session size configurable. |
| Explanations: target → primary → optional secondary | Primary native (default English) + optional secondary (default unset). Generation skips fields that would duplicate the row's own target-language content; see [Explanation-language rules](#explanation-language-rules) below. |
| Built-in static seed for English only | Spanish/Japanese/Portuguese go through LLM. |
| Seed: auto on first open + manual button | User picked both. |
| Auto-add to vocab by default, global toggle | User picked global default; configurable. |
| Export/import skipped for v1 | User deferred. |
| `user_id` columns now, no auth UI | Single-user today; multi-user tomorrow without schema change. |
| OpenAI-compatible + Ollama, env-switched | User picked "both configurable". |

## Risks

- **LLM schema drift** — small models can miss schema fields. Mitigation:
  `jsonschema` validation server-side; on failure, retry once with the
  error appended to the prompt; if still failing, return
  `{ok:false, error:"llm_invalid_schema"}` and let frontend offer
  "Try again" or "Edit manually".
- **WordNet coverage gap for non-English** — NLTK WordNet is English-only.
  The `wordnet` provider reports "no result" cleanly; chain naturally moves
  to the LLM provider if enabled.
- **Seed cost** — LLM seed for 50 structures + 100 phrases takes a while.
  Mitigation: `seed_jobs` table exists for resumable runs; in v1 the
  initialize endpoint is synchronous.
- **Browser cache unbounded** — localStorage ~5MB cap. Mitigation: LRU
  eviction at 1000 entries per language in `cache.js`.
- **Built-in items getting edited** — backend rejects `PUT/DELETE` when
  `source='built-in'`; frontend hides edit/delete buttons for those rows.

## Explanation-language rules

Structures and phrases carry two `explanation_*` columns that hold
native-language glosses generated by the LLM. The point of those
columns is to translate the row's content (a `pattern` / `phrase` in
the target language) into the user's native languages. We don't
generate a field whose language equals the target language, because it
would duplicate the row's own content.

Let `L` = target language (row's `language`), `P` =
`settings.explanation_primary` (user's first native), `S` =
`settings.explanation_secondary` (user's second native, or `NULL`).
The rule table:

| Case | `explanation_primary` | `explanation_secondary` |
|---|---|---|
| `L == P`, `S` set   | skip (redundant with target) | generate in `S` |
| `L == P`, `S` null  | skip | skip — row's target content is the only "explanation" |
| `L != P`, `S` set   | generate in `P` | generate in `S` |
| `L != P`, `S` null  | generate in `P` | skip |

Guarantee: at minimum the user sees the target-language content (the
`pattern` / `phrase` field). Native-language glosses are only
generated when they add a language different from `L`.

#### Phrases now mirror structures

Phrases used to carry a separate `literal_translation` column (a
word-for-word rendering of the phrase). For idioms and proverbs this
was almost always identical to the phrase itself, which added noise
without adding meaning. Phrases now have a single `example_sentence`
column — one natural sentence in the target language showing the
phrase in use. This makes phrases and structures parallel:

| Field | Structures | Phrases |
|---|---|---|
| Template / expression | `pattern` | `phrase` |
| Sample sentence | `example_sentence` | `example_sentence` |
| Usage note | `explanation` | `explanation` |
| Native glosses | `explanation_primary`, `explanation_secondary` | same |

The `literal_translation` column was removed by migration `006`. The
`example_sentence` column is `NOT NULL` and the schema requires it on
add. The LLM service now asks the model to write a single example
sentence per phrase, instead of a word-for-word rendering.

## What the built-in seed carries

The built-in seed (e.g. `backend/data/built-in/english.json`) is
**target-language content only**. The JSON does not carry
`explanation_primary` or `explanation_secondary` — those columns are
always NULL for built-in rows. The reasoning:

- The seed is shared across users. A seed row whose `explanation_primary`
  is in some specific language would be useless (or wrong) for a user
  whose primary native is different.
- The AI is the source of translations. The seed is content; the
  user-specific explanations are generated on demand, per the user's
  settings at the time of viewing or re-seeding.

### Two ways to populate explanations

There are two distinct operations, kept separate because they answer
different questions:

1. **Initialize / Re-seed** (`POST /api/languages/<code>/initialize`,
   `force=true` to re-seed). Generates target-language content
   (structures & phrases) and saves the row. For built-in languages
   this re-reads the JSON file; for others it goes through the LLM
   (`seed_via_llm`). The explanation columns are still filled per
   the current settings at the time of the call.
2. **Apply explanations** (`POST /api/languages/<code>/apply-explanations`).
   A per-language *translation* pass. Loads existing
   target-language content, asks the LLM to fill in
   `explanation_primary` / `explanation_secondary` per the user's
   current primary/secondary settings, and overwrites those
   columns. The target-language content is never changed. This is
   what the user does after switching primary/secondary in Settings
   so their existing rows pick up the new translations.

Both operations apply the same explanation-language rules. The
"Apply explanations" path is implemented in
`backend/services/seed.py:190` and the LLM schema in
`APPLY_EXPLANATIONS_SCHEMA` (`backend/services/llm.py`).

The "Re-seed" button in Settings is the existing
`/api/languages/<code>/initialize` flow. A new
"Apply explanations" button sits next to it for already-seeded
languages.

### Target-language fields are required

`literal_translation` (phrases), `example_sentence` (structures), and
`explanation` (both) are target-language fields. They are
**always** required regardless of the rules above.

- `literal_translation` / `example_sentence` are short, single-
  sentence target-language fields.
- `explanation` is a paragraph-length (2-4 sentences) usage note in
  the target language, describing when and why to use the structure/
  phrase, register (formal/informal), common context, and any
  alternatives. It is the row's main "explanation" — distinct from
  `explanation_primary` / `explanation_secondary`, which are native-
  language glosses.

The schema requires all three; the LLM prompt and post-process enforce
that. The Add UI shows the field as required and validates before save.
The row renderer always shows the `Explanation:` line; for English
idioms the `Literal:` line shows the same as the phrase with a
"(same as phrase — typical for English idioms)" note.

### How it's enforced

Three places have to agree:

1. **Prompt** (`backend/services/llm.py`): the user prompt tells the
   LLM which fields to include and which to skip, with the human-
   readable language name (e.g. "English" / "Traditional Chinese") so
   the model doesn't have to guess from the code.
2. **JSON schema** (`llm.py`): `seed_schema(require_primary=…)` drops
   `explanation_primary` from the per-item `required` list when the
   target equals primary. Fill schemas leave both fields optional —
   the post-process is what actually nulls them.
3. **Post-process** (`llm.apply_explanation_rules`): called at every
   persistence boundary — `seed_via_llm` in
   `backend/services/seed.py`, the two `/api/structures/fill` and
   `/api/phrases/fill` blueprints. It is the single source of truth
   for what ends up in the DB; the prompt and schema are best-effort
   hints to the model, but the post-process wins even if the model
   ignores them. This is why a chatty model can't sneak a redundant
   `explanation_primary` past the L == P guard.

Edge cases:

- `P == S` (user set both natives to the same code): we ask for two
  identical-language explanations. Wasteful but harmless; not worth a
  branch.
- `P` cleared to empty in settings: treated as `"en"` (the DB default)
  to avoid an "all null" explosion.
- `S == L` (e.g. learning Chinese, secondary native Chinese): the
  frontend hides the secondary field in the Add form so the user
  doesn't fill in something redundant. The backend also nulls a
  user-typed `explanation_secondary` whose language would equal
  target, as defense in depth.
- The Add form hides `explanation_primary` when `L == P` and
  `explanation_secondary` when `S` is null / `S == P` / `S == L`.
  This is a UX shortcut — the backend would strip a redundant value
  anyway, but hiding the field up front is friendlier.

### User-input rules

The rules above were originally a property of the LLM-generation
path. We extend them to user input too: if a user manually adds a
structure/phrase and types an `explanation_primary` in the target
language, the backend strips it before saving. The Add form hides
the field in that case to prevent the confusion, but the backend
still enforces the rule for any client (e.g. a future importer or
the LLM-fill path when it falls through).

## Out of Scope for v1

- User accounts / login (tables ready, UI not).
- Export / import.
- Audio pronunciation.
- PWA install flow (manifest is placeholder only).
- Mobile app packaging.