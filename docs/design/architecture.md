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
   │     ├── llm.py                 Pluggable client (openai_compat | ollama)
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
| Explanations: target → primary → optional secondary | Primary native (default English) + optional secondary (default Chinese). |
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

## Out of Scope for v1

- User accounts / login (tables ready, UI not).
- Export / import.
- Audio pronunciation.
- PWA install flow (manifest is placeholder only).
- Mobile app packaging.