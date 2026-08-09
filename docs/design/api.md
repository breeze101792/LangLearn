# API

All endpoints return `{ok:true, data}` on success or `{ok:false, error}` on
failure, plus an optional `code` and `status` for structured handling. The
JSON response is `Content-Type: application/json` with `JSON_AS_ASCII=False`
so non-ASCII explanations round-trip cleanly.

## Conventions

- Validation errors → HTTP 400.
- Not found → HTTP 404.
- LLM failures → HTTP 502 with code `llm_error`.
- All mutations are auth-gated (future); v1 single-user.

## Endpoints

### Settings

#### `GET /api/settings`

Returns the full settings object for user 1. The first call auto-creates
default settings if missing.

#### `PUT /api/settings`

Partial update. Allowed keys: `active_language`, `auto_add_vocab`,
`page_size`, `explanation_primary`, `explanation_secondary`,
`dict_chain_json`, `theme`, `show_readings`. Unknown keys → 400.
`page_size` (5..50) is shared between Review session size and the
Vocabulary list page size.

### Languages

#### `GET /api/languages`

Returns `[{code, display_name, is_built_in, seeded}]`.

#### `POST /api/languages`

Add a non-built-in language. `{code, display_name}`.

#### `POST /api/languages/<code>/initialize`

Idempotent. With `force=true`, replaces existing seed.

For English (built-in): inserts from `backend/data/built-in/english.json`.

For non-built-in: calls `services.seed.generate_via_llm` which calls the
LLM with a strict JSON schema (50 structures + 100 phrases). If the LLM
is unreachable or schema invalid, returns 502.

#### `GET /api/languages/<code>/seed-status`

Returns `{code, seeded}`.

### Dictionary

#### `POST /api/dictionary/lookup`

`{lang, word}` → walks the user's per-language chain. Returns:

```json
{
  "entry": {"word": "...", "language": "...", "source": "...", "senses": [...]},
  "source": "wordnet" | "llm" | "",
  "auto_added": true | false,
  "providers_in_chain": N
}
```

If `auto_add_vocab` is enabled in settings AND a non-empty result is
returned, the backend inserts one vocab row per sense and reports
`auto_added=true`.

Empty chain + no result → `{entry: empty, source: "", auto_added: false, providers_in_chain: 0}`.

#### `POST /api/dictionary/<provider>`

Manual lookup; e.g. `POST /api/dictionary/llm`. Same body. Skips the chain.

#### `GET /api/dictionary/providers`

Returns `{"providers": ["llm", "wordnet"]}` (sorted).

### Vocab

#### `GET /api/vocab?lang=&limit=&offset=&box=`

List vocab items for one language, newest first. `box` (1..5) optionally
restricts to items at that Leitner level. Response includes `total` (count
of rows matching the filter, for pagination) and `by_box: {1..5}` so the
UI can render level counts without an extra round-trip.

#### `POST /api/vocab`

Add one row. Required: `language, word, source, glossary`. Optional:
`sense_idx, pos, example, explanation_primary, explanation_secondary`.

#### `DELETE /api/vocab/<id>`

Delete + return `{deleted_id, undo_token, ttl_seconds: 5}`.

#### `POST /api/vocab/<id>/restore`

Restore via the `undo_token` returned from DELETE. After 5s the token
expires.

#### `PATCH /api/vocab/<id>`

Update mutable fields. Today only `leitner_box` (1..5) is supported;
used by the Vocabulary page to let the user self-rate "I remember this
at level N". Reschedules `next_due` using the Leitner interval table.

#### `GET /api/vocab/review/status?lang=`

`{due, by_box: {1..5}}`.

#### `GET /api/vocab/review/next?lang=&n=20`

Items where `next_due <= now()`, ordered by due asc.

#### `POST /api/vocab/review/grade`

`{vocab_id, grade: "easy"|"hard"}` → promotes/demotes and reschedules.

### Structures / Phrases

Mirror image of each other:

- `GET /<resource>?lang=` — list
- `POST /<resource>` — create (source defaults to `user`)
- `PUT /<resource>/<id>` — edit; 403 if `source='built-in'`
- `DELETE /<resource>/<id>` — delete; 403 if `source='built-in'`
- `POST /<resource>/fill` — LLM fill-in for empty fields

Field shapes:

```
structures: {language, pattern, example_sentence?, explanation_primary?, explanation_secondary?, source?}
phrases:    {language, phrase, literal_translation?, explanation_primary?, explanation_secondary?, source?}
```

### Auth

#### `GET /api/auth/whoami`

Stub. Returns `{user_id: 1, username: 'me'}` for now.