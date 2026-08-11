# CLAUDE.md

Instructions for AI agents working in this repo. Pair with `~/projects/notebook/coding/webapp.md`.

## Quick reference

- **Working dir:** `/mnt/projects/webapp/langlearn`
- **Run:** `./start.sh` (boots Flask on `PORT`, default 5056).
- **Run tests:** `.venv_archlinux/bin/python -m pytest`
- **Lint:** none configured; Python is read for type discipline only.

## Architecture

Flask app factory (`backend/app.py`) + blueprints by resource + services for business logic. SQLite via `sqlite3`. Vanilla HTML/CSS/JS frontend, hash routing.

### Key invariants

- Every API endpoint returns `{ok:true, data}` or `{ok:false, error}`.
- Domain tables carry `user_id`; `user_id=1` is the default single user. Don't
  hard-code that constant — import `config.DEFAULT_USER_ID`.
- Migrations are append-only SQL files in `backend/migrations/` applied in
  filename order. New schema changes go in `00X_*.sql`; never edit a
  shipped migration.
- `data/` is gitignored runtime state. Tests use `LANGLEARN_DATA_DIR` to
  point at temp dirs.
- `backend/data/built-in/<language>.json` ships curated seed data (English
  only for v1).
- Atomic JSON writes use `db.atomic_write_json`; never write JSON with
  `Path.write_text` directly.

### File map (when changing X, edit Y)

| Want to change | Edit |
|---|---|
| API shape for X | `backend/blueprints/X.py` |
| Business logic for X | `backend/services/X.py` |
| Schema | new file in `backend/migrations/00X_*.sql` |
| New dictionary provider | `backend/services/dictionaries/X.py` + register in `registry.bootstrap()` |
| New TTS provider | `backend/services/tts/X.py` + register in `backend/services/tts/registry.bootstrap()` |
| LLM schema | `backend/services/llm.py` (top of file) |
| Built-in seed | `backend/data/built-in/<code>.json` |
| Frontend page X | `frontend/static/js/pages/X.js` + add route in `frontend/static/js/main.js` |
| Speaker button binding | `frontend/static/js/components/speak.js` (wire via `bindSpeakButtons`) |
| CSS tokens | `frontend/static/css/tokens.css` |
| CSS components | `frontend/static/css/app.css` |

## Error handling rules

- All blueprints catch exceptions at the route boundary and return
  `{ok:false, error}` with a meaningful message.
- `LLMError` is the only LLM exception type. `LLMSchemaError` is its
  subclass. Blueprints translate them to HTTP 502.
- Validation errors are HTTP 400. Not-found is HTTP 404. Auth (future) is 401.

## Test conventions

- `tests/conftest.py` autouse fixture sets a per-test `LANGLEARN_DATA_DIR`
  and clears in-memory state. New tests should rely on it instead of
  redefining `fresh`.
- LLM tests patch `backend.services.llm.requests.post`; do not call real
  network.
- Use `app.test_client()` for HTTP-level tests of blueprints.

## Style

- Python: type hints on public functions. No one-letter variable names
  outside comprehensions.
- SQL: schema uses `user_id` for tenant-style scoping. Never store
  unmigrated state in tables.
- JS: ES modules, no transpilation. Use `textContent` for untrusted
  strings, never `innerHTML`. The `escapeHtml` helper in pages is a
  safety net, not the default.

## Common pitfalls

- Adding a column to a table requires a new migration file; do not edit
  `001_init.sql` after it's been applied to a real DB.
- Importing `..util` from `backend/services/X.py` works; importing
  `..util` from `backend/services/dictionaries/X.py` does NOT — you need
  `...util` (three dots).
- `OPENAI_API_KEY` is read at request time inside `OpenAICompatClient`;
  tests using `monkeypatch.setenv` work.
- `config.db_path()` is a function, not a constant — re-evaluate per call.
- The chain executor passes `explanation_primary` / `explanation_secondary`
  kwargs to every provider; new providers must accept `**_ignored` or
  named kwargs.

## Things explicitly NOT in v1

- User login / multi-user (tables ready, no auth UI).
- Export / import.
- Audio / TTS.
- Notifications / reminders.
- Mobile packaging.