# LLM model evaluation

Central doc for evaluating candidate LLM models for LangLearn. There
is one probe script that targets any model. Verdict per model is
recorded in the **Verdict index** below; no dated report files are
kept.

## How to evaluate a new model

1. Make sure `OPENAI_BASE_URL` points at the endpoint that serves the
   model.
2. Run the probe (use `--timeout 90` for slow / reasoning models):

   ```
   .venv_nixlab/bin/python scripts/eval-llm/probe.py --model <model-id>
   ```

3. Read `scripts/eval-llm/runs/<model-slug>/<timestamp>/summary.md` and
   `report.json`. The script returns exit code 0 iff every case
   passes (schema-valid AND, for fill_* cases, data-preserving).
4. Add a row to the **Verdict index** below. Old per-run data lives
   under `scripts/eval-llm/runs/` and is gitignored — feel free to
   delete it.

The probe reuses the real schemas and helpers in
`backend/services/llm.py` so it exercises the same path the app uses,
just pointed at a different model.

## Test surface

The probe runs 5 cases against `response_format: json_schema strict: True`:

| Case | Schema | What it checks |
|---|---|---|
| dict_word "bank" (en) | `DICT_WORD_SCHEMA` | Polysemy, common word |
| dict_word "set" (en) | `DICT_WORD_SCHEMA` | Polysemy, mixed POS |
| dict_word "desafortunadamente" (es) | `DICT_WORD_SCHEMA` | Non-English, long word |
| seed 3 structures + 5 phrases (es) | `seed_schema(require_primary=True)` | Bilingual (en + zh) generation, count adherence |
| fill_structure "be used to doing" (en) | `FILL_STRUCTURE_SCHEMA` | Preservation of non-null fields |

Each case measures: schema-validity, latency, and (where relevant) field
preservation / item counts.

## Probe grading rules (do not relax)

A case passes iff **both**:

1. `Draft202012Validator` accepts the JSON.
2. For `fill_*` cases only, the model preserved every non-null field
   the user supplied (no silent data loss).

The preservation check exists because schema-valid JSON can still
erase user input. It caught a real bug during the qwen3.5:4b probe
where the model returned `null` for the user-supplied `pattern` and
`explanation_primary` fields. Don't remove this check.

## Verdict index

| Model | Schema-valid | Verdict | Required changes to ship |
|---|---|---|---|
| gemma4:e2b | 5/5 | Usable. Slow. | Bump `LLM_TIMEOUT_SECONDS` 20 → 60 in `backend/config.py:92`. |
| qwen3.5:4b | 3/5 | Slow + fragile. 2/5 timeout at 90s. | Not recommended. |
| qwen3.5:0.8b | 0/5 | Reasoning model, all budget on thinking. | Not recommended. |

**Project default:** `OPENAI_MODEL=gemma4:e2b` via OpenAI-compat with
`LLM_TIMEOUT_SECONDS=60`. Set in `.env`.

## Notes per model (one line is enough)

- **gemma4:e2b:** 5/5, all cases 12–25s, fill_structure preserves
  fields. Polysemy is reasonable. Bilingual explanations clean.
- **qwen3.5:4b:** 3/5. The 3 short cases (88–128s) pass and produce
  bilingual EN+zh output. The 2 long cases (Spanish + seed) hit
  `finish_reason: length` with `content=""` because the model is a
  reasoning model and the shim doesn't expose a way to disable
  thinking. If we ever serve a `*-nothink` variant of this model, it
  becomes competitive.
- **qwen3.5:0.8b:** 0/5. Same reasoning-model failure mode as 4b but
  every case is too long. Unusable at this endpoint.
