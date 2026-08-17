# Test Plan

LangLearn's test suite. Run everything with:

    .venv_$(hostname)/bin/python -m pytest

On this host that is `.venv_nixlab/bin/python -m pytest`. The JS tests are
wrapped by Python test modules (see below) so a single `pytest` invocation
runs the whole suite.

## Frameworks

- **Python**: `pytest` (config in `pytest.ini`, `testpaths = tests`).
  `pytest-cov` is available for coverage reports.
- **JS**: Node's built-in test harness is not used; instead each pure-JS
  module has a hand-rolled `.mjs` runner (exits 0 on pass, 1 on first
  failure) that a Python test module shells out to. This keeps the JS tests
  inside the single `pytest` entry point.

## Fixtures

`tests/conftest.py` provides an autouse `clean_state` fixture that, before
every test:

- points `LANGLEARN_DATA_DIR` at a fresh `tmp_path`;
- isolates LLM config (`OPENAI_API_KEY=""`, `OPENAI_BASE_URL` at OpenAI's
  endpoint, `OPENAI_MODEL="gpt-test"`) so lookups fail fast and
  deterministically;
- clears `LANGLEARN_PASSWORD` so auth-gated tests don't inherit a developer
  `.env` (auth tests re-enable it via their own fixture);
- runs `db.init_schema()` and resets module-level in-memory state
  (`vocab._undo_tokens`, `auth_gate._login_attempts`).

New tests should rely on this fixture instead of redefining a `fresh`
fixture. LLM tests patch `backend.services.llm.requests.post`; never call
real network.

## Coverage matrix

| Module | Coverage | Notes |
|---|---|---|
| `backend/app.py` | 94% | error handlers, index/manifest/sw routes |
| `backend/blueprints/analyze.py` | 100% | |
| `backend/blueprints/auth.py` | 100% | |
| `backend/blueprints/describe.py` | 96% | raw-body upload, invalid lang, ValueError→400 |
| `backend/blueprints/dictionary.py` | 98% | |
| `backend/blueprints/languages.py` | 100% | |
| `backend/blueprints/phrases.py` | 100% | |
| `backend/blueprints/refine.py` | 100% | |
| `backend/blueprints/settings.py` | 100% | |
| `backend/blueprints/structures.py` | 100% | |
| `backend/blueprints/transfer.py` | 95% | |
| `backend/blueprints/translate.py` | 100% | |
| `backend/blueprints/tts.py` | 96% | |
| `backend/blueprints/vocab.py` | 93% | |
| `backend/config.py` | 89% | `_data_dir` is dead code (never called) |
| `backend/db.py` | 95% | |
| `backend/services/auth_gate.py` | 97% | |
| `backend/services/dictionaries/*` | 97-100% | |
| `backend/services/leitner.py` | 100% | |
| `backend/services/llm.py` | 95% | large module; edge branches uncovered |
| `backend/services/seed.py` | 98% | |
| `backend/services/settings.py` | 100% | |
| `backend/services/transfer.py` | 99% | |
| `backend/services/tts/*` | 100% | |
| `backend/services/vocab.py` | 99% | |
| `backend/util.py` | 100% | |

Overall: ~97% statement coverage.

## Test layers

- **Unit**: pure logic and helpers — `test_util.py`, `test_leitner.py`,
  `test_llm_helpers.py`, `test_llm_normalizers.py`, the `.mjs` runners.
- **Integration**: service boundaries and data flow — `test_db*.py`,
  `test_seed*.py`, `test_transfer*.py`, `test_settings*.py`.
- **HTTP/API**: blueprint routes via `app.test_client()` — `test_*_api.py`,
  `test_*_full.py`, `test_describe.py`, `test_analyze.py`, etc.
- **E2E-ish**: `test_pages_load.py` shells out to a Node script that loads
  the SPA shell and asserts the page renders.

## Conventions

- Every API endpoint returns `{ok:true, data}` or `{ok:false, error}`;
  tests assert the `ok`/`code` shape.
- Validation errors are HTTP 400, not-found 404, LLM errors 502.
- Use `app.test_client()` for HTTP-level tests of blueprints.
- Match existing style: type hints on public functions, no one-letter
  variable names outside comprehensions.

## Known thin areas

- `backend/blueprints/describe.py` lines 62/72 (multipart/raw oversized
  re-checks) are defensive dead code — the `content_length` check at the
  top of the route catches oversized requests first, so they are not
  reachable through the HTTP layer.
- `backend/config.py` `_data_dir()` is dead code (never called; the public
  `data_dir()` is fully covered).
- `backend/services/llm.py` has ~47 uncovered statements across its 2500+
  lines — mostly defensive branches and rarely-hit error paths.
