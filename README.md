# LangLearn

A multi-language vocabulary and phrase trainer. Look up words, learn sentence
structures and common phrases, and review them with a Leitner 5-box scheduler.

**Status:** v0.1 — single user. Tables carry `user_id` so multi-user is a
future add without schema change.

## Stack

- **Backend:** Python 3.10+ Flask (app factory + blueprints), raw SQLite via
  `sqlite3`, NLTK WordNet for English, OpenAI-compatible LLM client.
- **Frontend:** Vanilla HTML/CSS/JS, hash routing, no build step, no CDN.
- **Storage:** SQLite at `./data/langlearn.sqlite`. Built-in English seed at
  `backend/data/built-in/english.json`.

## Quick start

```bash
./start.sh                # http://127.0.0.1:5000
```

The first launch creates a per-host virtualenv (`.venv_<hostname>`), installs
deps from `backend/requirements.txt`, runs the migration on first boot, and
seeds the language catalog. The first time you open a language, its
structures + phrases get seeded (English uses built-in JSON; others go
through the LLM).

Override the data directory:

```bash
LANGLEARN_DATA_DIR=/var/lib/langlearn ./start.sh
```

## LLM configuration

The app uses a single OpenAI-compatible Chat Completions endpoint. Copy
`.env.example` to `.env` and set the values, or export the env vars directly
(shell exports override `.env`):

```bash
cp .env.example .env   # then edit OPENAI_API_KEY

# or export directly
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1   # default
export OPENAI_MODEL=gpt-4o-mini                    # default
```

The client uses strict JSON schema via `response_format={"type":"json_schema", ...}`
with one retry on schema validation failure. Schema files live in
`backend/services/llm.py`.

## Features

- **Dictionary lookup** with a per-language ordered chain of dictionary
  providers. Built-in chain for English is `WordNet → LLM`; for other
  languages it's `LLM` only. Reorder or disable providers per language in
  Settings → Dictionary chain.
- **Auto-add to vocab** when a lookup returns a result (toggle in Settings).
  Auto-add is one vocab row per sense. The undo window is a 5s toast.
- **Leitner 5-box review** — box intervals are 1d, 3d, 7d, 14d, 30d.
  Default session size 20, configurable.
- **Sentence structures + phrases** pages — built-in read-only items carry
  a `Built-in` badge; user-added items are editable. Both kinds can be AI
  filled in place.
- **First-run initialization** is automatic when you open a language, and
  a manual button in Settings → Initialize data replaces the seed.
- **Browser-local cache** for dictionary lookups (`langlearn:dict:v1:*` in
  localStorage, LRU-capped at 1000 entries per language).

## Project layout

```
langlearn/
├── backend/
│   ├── app.py                    Flask app factory
│   ├── config.py                 paths, defaults, env vars
│   ├── db.py                     SQLite layer + migration runner
│   ├── util.py                   ok()/err()/validators
│   ├── requirements.txt
│   ├── blueprints/               HTTP endpoints, one resource each
│   │   ├── auth.py               (stub for future multi-user)
│   │   ├── settings.py
│   │   ├── languages.py
│   │   ├── dictionary.py
│   │   ├── vocab.py
│   │   ├── structures.py
│   │   └── phrases.py
│   ├── services/                 business logic, no HTTP
│   │   ├── settings.py
│   │   ├── llm.py                OpenAI-compatible client + schemas
│   │   ├── seed.py               built-in + LLM seeding
│   │   ├── leitner.py            5-box scheduler
│   │   ├── vocab.py
│   │   └── dictionaries/
│   │       ├── base.py           WordEntry / Sense / Definition
│   │       ├── wordnet.py
│   │       ├── llm.py
│   │       └── registry.py       chain executor
│   ├── migrations/
│   │   └── 001_init.sql
│   └── data/built-in/english.json
├── frontend/
│   ├── templates/index.html
│   └── static/
│       ├── css/{tokens,app}.css
│       ├── js/{main,api,cache,state}.js
│       ├── js/components/{nav-links,lang-switcher,toast}.js
│       ├── js/pages/{dictionary,review,structures,phrases,settings}.js
│       ├── manifest.json
│       └── sw.js
├── data/                          runtime SQLite (gitignored)
├── docs/design/                   architecture / data model / api / frontend
├── tests/                         pytest, isolated tmp data dir per test
├── start.sh
├── pyproject.toml
├── pytest.ini
├── .gitignore
├── README.md
└── CLAUDE.md
```

## API surface (all return `{ok:true, data}` or `{ok:false, error}`)

| Method | Path | Notes |
|---|---|---|
| GET    | `/api/settings` | full settings object |
| PUT    | `/api/settings` | partial update; rejects unknown keys |
| GET    | `/api/languages` | catalog with `seeded` flag |
| POST   | `/api/languages` | `{code, display_name}` — adds a non-built-in language |
| POST   | `/api/languages/<code>/initialize` | `{force?:bool}` — idempotent seed |
| POST   | `/api/dictionary/lookup` | `{lang, word}` — walks per-language chain |
| POST   | `/api/dictionary/<provider>` | manual lookup (e.g. `/api/dictionary/llm`) |
| GET    | `/api/dictionary/providers` | list available provider names |
| GET    | `/api/vocab?lang=&limit=&offset=` | list vocab items |
| POST   | `/api/vocab` | add one row |
| DELETE | `/api/vocab/<id>` | delete (returns undo_token) |
| POST   | `/api/vocab/<id>/restore` | restore via undo token |
| GET    | `/api/vocab/review/status?lang=` | due counts per box |
| GET    | `/api/vocab/review/next?lang=&n=20` | due items |
| POST   | `/api/vocab/review/grade` | `{vocab_id, grade:"easy|hard"}` |
| GET    | `/api/structures?lang=` | list structures |
| POST   | `/api/structures` | add user-added structure |
| PUT    | `/api/structures/<id>` | edit (rejects built-in) |
| DELETE | `/api/structures/<id>` | delete (rejects built-in) |
| POST   | `/api/structures/fill` | LLM-assisted fill-in |
| GET    | `/api/phrases?lang=` | list phrases |
| POST   | `/api/phrases` | add user-added phrase |
| PUT    | `/api/phrases/<id>` | edit (rejects built-in) |
| DELETE | `/api/phrases/<id>` | delete (rejects built-in) |
| POST   | `/api/phrases/fill` | LLM-assisted fill-in |

## Tests

```bash
.venv_archlinux/bin/python -m pytest
```

All tests run with isolated temp data dirs (set via `LANGLEARN_DATA_DIR`).
Mock HTTP for LLM tests; no network required.

## Conventions

See `CLAUDE.md` for agent-facing conventions and `docs/design/` for the
design artifacts. The webapp rule we follow is
`~/projects/notebook/coding/webapp.md`.

## Out of scope for v1

- User accounts / login (tables ready, UI not).
- Export / import.
- Audio pronunciation.
- PWA install flow (manifest is placeholder only).
- Mobile app.