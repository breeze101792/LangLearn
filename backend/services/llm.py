"""LLM client.

Uses an OpenAI-compatible Chat Completions API endpoint, configured via
`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`. Sends
`response_format={"type":"json_schema", ...}` to enforce schema.

The client returns a parsed dict (validated against the requested schema)
or raises `LLMError`. One retry is attempted on schema failure with the
error appended to the prompt.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

import requests
from jsonschema import Draft202012Validator, ValidationError

from .. import config

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Generic LLM failure (network, schema, provider error)."""


class LLMTimeout(LLMError):
    pass


class LLMSchemaError(LLMError):
    pass


# ---- Explanation-language rules ---------------------------------------
#
# When generating `explanation_primary` / `explanation_secondary` for a row
# whose target language is `lang`, we only ask the model to produce fields
# that are not redundant with the row's own content. The rules:
#
#   L == P, S set:   skip primary, generate secondary in S
#   L == P, S null:  skip primary, skip secondary
#   L != P, S set:   generate primary in P, generate secondary in S
#   L != P, S null:  generate primary in P, skip secondary
#
# where L = target lang, P = settings.explanation_primary,
#       S = settings.explanation_secondary (nullable).
#
# The row's own `pattern` / `phrase` field already shows the target
# language, so an `explanation_primary` in the same language is wasted
# ink. Below helpers translate this rule into prompt text, schema, and
# a final post-processing pass that nulls out any field the prompt told
# the model to skip — that way a chatty model that returns a value
# anyway is corrected on our side.

_LANG_DISPLAY_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "ja": "Japanese",
    "pt": "Portuguese",
    "zh": "Traditional Chinese",
    "fr": "French",
    "de": "German",
}


def _lang_name(code: str | None) -> str:
    if not code:
        return ""
    if code in _LANG_DISPLAY_NAMES:
        return _LANG_DISPLAY_NAMES[code]
    return code


# ---- Level directive ----------------------------------------------------
#
# When the user has set a CEFR level (A1..C2) for the target language,
# we tell the model what that level is so it picks vocabulary and
# grammar appropriate to the learner. ``None`` means "unset" — the
# model is told nothing and behaves as it did before this feature
# existed. The directive is a short, level-specific paragraph appended
# to the system prompt so every target-language generation path
# (Analyze, Refine, Translate, Describe, seed, fill, dictionary lookup,
# apply-explanations) gets the same guidance with no per-call branching.
#
# The guidance is deliberately concrete ("use simple vocabulary",
# "avoid advanced grammar") rather than just naming the level, since
# models vary in how reliably they map a bare "B1" label to concrete
# output choices.

_LEVEL_GUIDANCE: dict[str, str] = {
    "A1": (
        "The learner is a beginner (CEFR A1). Use only the most common, "
        "everyday words and very short, simple sentences. Avoid idioms, "
        "slang, and any grammar beyond the present tense unless the task "
        "explicitly requires it. Prefer high-frequency vocabulary."
    ),
    "A2": (
        "The learner is an elementary learner (CEFR A2). Use common, "
        "everyday vocabulary and short sentences. Basic past and future "
        "tenses are fine; avoid complex subordinate clauses and idiomatic "
        "expressions the learner is unlikely to know."
    ),
    "B1": (
        "The learner is an intermediate learner (CEFR B1). Use everyday, "
        "neutral vocabulary and a range of common tenses. Some idioms and "
        "common collocations are fine; avoid rare literary vocabulary and "
        "very complex sentence structures."
    ),
    "B2": (
        "The learner is an upper-intermediate learner (CEFR B2). Use a "
        "broad range of vocabulary including some less common words and "
        "idioms. Complex sentences and a variety of tenses are welcome; "
        "still avoid highly specialised or literary language unless the "
        "task calls for it."
    ),
    "C1": (
        "The learner is an advanced learner (CEFR C1). Use a wide range "
        "of vocabulary, including idiomatic and nuanced expressions and "
        "less common collocations. Complex grammar and a variety of "
        "registers are appropriate. The learner can handle advanced "
        "material; do not simplify the language."
    ),
    "C2": (
        "The learner is proficient (CEFR C2). Use the full range of the "
        "language, including idioms, colloquialisms, nuanced vocabulary, "
        "and complex grammar, exactly as a fluent native speaker would. "
        "Do not simplify or limit the language in any way."
    ),
}


def _level_directive(level: str | None) -> str:
    """Return a system-prompt fragment describing the user's CEFR level
    for the target language, or "" when no level is set. The caller
    appends this to the system prompt so every generation path gets the
    same guidance."""
    if not level:
        return ""
    guidance = _LEVEL_GUIDANCE.get(level.upper())
    if not guidance:
        return ""
    return " " + guidance


def _should_generate_primary(target_lang: str, primary: str | None) -> bool:
    if not primary:
        return False
    return target_lang != primary


def _should_generate_secondary(primary: str | None, secondary: str | None) -> bool:
    if not secondary:
        return False
    # No point asking for a secondary in the same language as primary;
    # the prompt instructions would just confuse the model.
    if primary and secondary == primary:
        return False
    return True


def _strip_explanations(
    item: dict,
    *,
    keep_primary: bool,
    keep_secondary: bool,
) -> None:
    if not keep_primary:
        item["explanation_primary"] = None
    if not keep_secondary:
        item["explanation_secondary"] = None


def apply_explanation_rules(
    payload: dict,
    *,
    lang: str,
    primary: str | None,
    secondary: str | None,
) -> None:
    """Null out explanation_primary / explanation_secondary on every
    structure, phrase, and word in ``payload`` according to the four-case
    rule table in the module docstring.

    ``payload`` may be one of:

    * a seed-shaped object ``{"structures": [...], "phrases": [...]}``
      (legacy)
    * an analyze-shaped object
      ``{"structures": [...], "phrases": [...], "words": [...]}``
    * a single fill-shaped object with ``explanation_primary`` and
      ``explanation_secondary`` at the top level

    Mutates in place.
    """
    keep_p = _should_generate_primary(lang, primary)
    keep_s = _should_generate_secondary(primary, secondary)
    if "structures" in payload or "phrases" in payload or "words" in payload:
        for s in (payload.get("structures") or []):
            if isinstance(s, dict):
                _strip_explanations(s, keep_primary=keep_p, keep_secondary=keep_s)
        for p in (payload.get("phrases") or []):
            if isinstance(p, dict):
                _strip_explanations(p, keep_primary=keep_p, keep_secondary=keep_s)
        for w in (payload.get("words") or []):
            if isinstance(w, dict):
                _strip_explanations(w, keep_primary=keep_p, keep_secondary=keep_s)
        return
    if "explanation_primary" in payload or "explanation_secondary" in payload:
        _strip_explanations(
            payload, keep_primary=keep_p, keep_secondary=keep_s,
        )


# ---- JSON schemas -------------------------------------------------------

DICT_WORD_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["senses"],
    "properties": {
        "senses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pos", "definitions"],
                "properties": {
                    "pos": {"type": ["string", "null"], "maxLength": 32},
                    "definitions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["glossary"],
                            "properties": {
                                "glossary": {"type": ["string", "null"], "maxLength": 1000},
                                "example": {"type": ["string", "null"], "maxLength": 1000},
                            },
                        },
                    },
                    "explanations": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "primary": {"type": ["string", "null"]},
                            "secondary": {"type": ["string", "null"]},
                        },
                    },
                },
            },
        },
    },
}

SEED_ITEM_BASE: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "explanation": {"type": "string", "minLength": 1, "maxLength": 1500},
        "explanation_primary": {"type": ["string", "null"], "maxLength": 1000},
        "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
    },
}

_STRUCTURE_ITEM_PROPS: dict = {
    "pattern": {"type": "string", "minLength": 1, "maxLength": 500},
    "example_sentence": {"type": "string", "minLength": 1, "maxLength": 1000},
}

# Phrases mirror structures: `phrase` is the expression, and
# `example_sentence` is one concrete sentence showing it in use. We
# used to also store `literal_translation` (a word-for-word rendering
# of the phrase), but for idioms and proverbs that was almost always
# identical to the phrase itself, and the extra column made phrases
# harder to reason about than structures. The single example sentence
# covers the same ground.
_PHRASE_ITEM_PROPS: dict = {
    "phrase": {"type": "string", "minLength": 1, "maxLength": 500},
    "example_sentence": {"type": "string", "minLength": 1, "maxLength": 1000},
}


def seed_schema(*, require_primary: bool) -> dict:
    """Schema for a seed payload.

    `require_primary=True` is the historical "always ask for an English
    explanation" shape. `require_primary=False` is used when the target
    language equals the user's primary native language — we don't ask
    the model to produce a redundant gloss. The caller still post-
    processes the response to null out fields that the rules say to
    skip, so the JSON schema only controls what the LLM is *required*
    to produce, not what ends up in the DB.

    The target-language fields (`pattern` + `example_sentence` for
    structures; `phrase` + `example_sentence` for phrases;
    `explanation` for both) are always required regardless of the
    rules: they're the row's own content, not a gloss of it.
    """
    struct_required = ["pattern", "example_sentence", "explanation"]
    phrase_required = ["phrase", "example_sentence", "explanation"]
    if require_primary:
        struct_required.append("explanation_primary")
        phrase_required.append("explanation_primary")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["structures", "phrases"],
        "properties": {
            "structures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": struct_required,
                    "properties": {**_STRUCTURE_ITEM_PROPS,
                                   **SEED_ITEM_BASE["properties"]},
                },
            },
            "phrases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": phrase_required,
                    "properties": {**_PHRASE_ITEM_PROPS,
                                   **SEED_ITEM_BASE["properties"]},
                },
            },
        },
    }

FILL_STRUCTURE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pattern": {"type": ["string", "null"], "maxLength": 500},
        "example_sentence": {"type": ["string", "null"], "maxLength": 1000},
        "explanation": {"type": ["string", "null"], "maxLength": 1500},
        "explanation_primary": {"type": ["string", "null"], "maxLength": 1000},
        "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
    },
}

FILL_PHRASE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "example_sentence": {"type": ["string", "null"], "maxLength": 1000},
        "explanation": {"type": ["string", "null"], "maxLength": 1500},
        "explanation_primary": {"type": ["string", "null"], "maxLength": 1000},
        "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
    },
}


# ---- Provider plumbing --------------------------------------------------


def _client():
    return OpenAICompatClient()


def complete_json(
    *,
    schema: dict,
    schema_name: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_retries: int = 1,
    normalize: Callable[[dict], dict] | None = None,
    timeout: int | None = None,
) -> dict:
    """Send a prompt, get a JSON dict matching `schema`. Retries on schema error.

    ``normalize`` is an optional callable applied to each parsed response
    *before* strict validation. It should repair common, predictable
    field-name variants that non-OpenAI models produce (for example
    ``part_of_speech`` for ``pos``), converting them to the canonical
    schema keys. This keeps strict validation while tolerating the
    schem-agnostic aliases LLMs like to invent. It must not raise; any
    residual mismatches still surface as a validation error.

    ``timeout`` overrides the per-request HTTP timeout (defaults to
    ``config.LLM_TIMEOUT_SECONDS``). Larger prompts that ask for many
    fields (e.g. Analyze) can need a bigger budget than the default.

    On failure, raises ``LLMSchemaError`` whose message includes the
    final validation error and a truncated copy of the last response
    the model produced. This makes it possible to diagnose strict-
    schema mismatches against non-OpenAI proxies without enabling
    DEBUG logging.
    """
    validator = Draft202012Validator(schema)
    client = _client()
    last_error: str | None = None
    last_raw: str | None = None

    for attempt in range(max_retries + 1):
        prompt_user = user if not last_error else (
            user + "\n\nYour previous response failed validation:\n"
            + last_error + "\n\nReturn valid JSON only. No prose."
        )
        raw = client.chat(
            system=system,
            user=prompt_user,
            schema=schema,
            schema_name=schema_name,
            temperature=temperature,
            timeout=timeout,
        )
        last_raw = raw
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            last_error = f"not valid JSON: {e}"
            log.warning("LLM JSON parse error (attempt %d): %s", attempt + 1, e)
            continue
        if normalize is not None:
            try:
                data = normalize(data)
            except Exception as e:  # defensive: normalize must not raise
                log.warning("LLM normalize error (attempt %d): %s", attempt + 1, e)
        try:
            validator.validate(data)
            return data
        except ValidationError as e:
            last_error = e.message
            log.warning("LLM schema validation error (attempt %d): %s",
                        attempt + 1, e.message)

    # Build a helpful error: which schema was being filled, what the
    # last validation error was, and a sample of the last raw response.
    sample = (last_raw or "")[:400]
    raise LLMSchemaError(
        f"LLM did not produce valid JSON for schema '{schema_name}' "
        f"after {max_retries + 1} attempts. Last error: {last_error}. "
        f"Last response (truncated to 400 chars): {sample!r}"
    )


class _BaseClient:
    def chat(self, *, system, user, schema, schema_name, temperature,
             timeout: int | None = None) -> str:
        raise NotImplementedError

    def chat_messages(self, *, messages, schema, schema_name, temperature,
                      timeout: int | None = None) -> str:
        raise NotImplementedError


class OpenAICompatClient(_BaseClient):
    def chat(self, *, system, user, schema, schema_name, temperature,
             timeout: int | None = None) -> str:
        return self.chat_messages(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            schema=schema, schema_name=schema_name,
            temperature=temperature, timeout=timeout,
        )

    def chat_messages(self, *, messages, schema, schema_name, temperature,
                      timeout: int | None = None) -> str:
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "") or config.OPENAI_API_KEY
        url = (os.environ.get("OPENAI_BASE_URL") or config.OPENAI_BASE_URL).rstrip("/") + "/chat/completions"
        model = os.environ.get("OPENAI_MODEL") or config.OPENAI_MODEL
        # Only OpenAI's hosted API hard-requires a key; self-hosted /
        # OpenAI-compatible endpoints (e.g. Ollama) often don't.
        requires_key = "api.openai.com" in url
        if requires_key and not api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return _post_json(url, payload, headers, timeout=timeout)

    def supports_strict_schema(self) -> bool:
        return True


def _strip_markdown_fence(text: str) -> str:
    """Some non-OpenAI proxies wrap responses in ```json ... ``` fences
    even when response_format=json_schema is set. Strip them so the
    JSON parser can read the content."""
    s = text.strip()
    if s.startswith("```"):
        # Drop the opening fence (with optional language tag).
        first_newline = s.find("\n")
        if first_newline == -1:
            return s
        s = s[first_newline + 1:]
        # Drop the closing fence.
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _post_json(url: str, payload: dict, headers: dict,
               *, timeout: int | None = None) -> str:
    effective_timeout = timeout if timeout is not None else config.LLM_TIMEOUT_SECONDS
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=effective_timeout)
    except requests.Timeout as e:
        raise LLMTimeout(
            f"request timed out after {effective_timeout}s "
            f"(set OPENAI_TIMEOUT_SECONDS env var to increase; current "
            f"LLM_TIMEOUT_SECONDS={config.LLM_TIMEOUT_SECONDS}): {e}"
        ) from e
    except requests.RequestException as e:
        raise LLMError(f"network error: {e}") from e
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")
    try:
        body = r.json()
    except ValueError as e:
        raise LLMError(f"non-JSON response: {r.text[:200]}") from e
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content, str):
        raise LLMError(f"unexpected content type: {type(content).__name__}")
    return _strip_markdown_fence(content)


# ---- Domain helpers -----------------------------------------------------


def _normalize_dict_word(data: dict) -> dict:
    """Repair common field-name variants non-OpenAI models produce for
    the ``dict_word`` schema before strict validation. Alias mapping:
    ``part_of_speech`` -> ``pos``; ``glosses`` / ``meanings`` ->
    ``definitions``; ``text`` -> ``glossary``; ``sentence`` /
    ``usage_example`` -> ``example``. Unknown keys are dropped rather
    than passed to the strict validator (which would reject them).
    Must never raise: it runs before validation on every attempt."""
    senses = data.get("senses")
    if not isinstance(senses, list):
        # Some models return a single flat sense object at the top level
        # ({"pos": ..., "definitions": [...], "explanations": {...}})
        # instead of wrapping it in {"senses": [...]}. Promote that shape
        # so strict validation passes.
        if isinstance(data, dict) and any(
            k in data for k in ("pos", "definitions", "glosses", "meanings")
        ):
            return {"senses": [_normalize_dict_sense(data)]}
        return data
    return {"senses": [_normalize_dict_sense(s) for s in senses]}


def _normalize_dict_sense(sense: dict) -> dict:
    """Normalize one sense dict to the canonical ``dict_word`` shape.
    Must never raise: it runs before validation on every attempt."""
    if not isinstance(sense, dict):
        return {}
    pos = sense.pop("part_of_speech", None)
    if pos is None:
        pos = sense.pop("pos", None)
    # Always emit `pos` (null when absent): strict mode requires the
    # key to be present, and the provider defaults a null pos to "—".
    sense["pos"] = pos
    defs = sense.pop("definitions", None)
    if defs is None:
        defs = sense.pop("glosses", None)
    if defs is None:
        defs = sense.pop("meanings", None)
    # Some models emit a flat sense like
    # {"pos": "...", "definition": "...", "example": "..."} instead
    # of nesting the gloss into a `definitions` array. Promote that
    # shape so it matches the strict schema. Any leftover `example`
    # key with no gloss is dropped — the schema requires glossary
    # to be a non-empty string.
    if not defs:
        flat_def = sense.pop("definition", None)
        flat_ex = sense.pop("example", None)
        if flat_def is not None:
            defs = [{"glossary": flat_def}]
            if flat_ex is not None:
                defs[0]["example"] = flat_ex
        else:
            sense.pop("example", None)
    if defs is not None:
        sense["definitions"] = defs
    if isinstance(defs, list):
        for d in defs:
            if not isinstance(d, dict):
                continue
            if "glossary" not in d:
                text = d.pop("text", None)
                if text is None:
                    text = d.pop("translation", None)
                if text is not None:
                    d["glossary"] = text
            # Always emit `glossary` (null when absent): strict mode
            # requires the key, and the provider drops null-glossary
            # definitions during salvage.
            if "glossary" not in d:
                d["glossary"] = None
            if "example" not in d:
                for alias in ("sentence", "usage_example", "usage"):
                    if alias in d:
                        d["example"] = d.pop(alias)
                        break
            for key in list(d):
                if key not in ("glossary", "example"):
                    d.pop(key, None)
    explanations = sense.get("explanations")
    if isinstance(explanations, dict):
        for key in list(explanations):
            if key not in ("primary", "secondary"):
                explanations.pop(key, None)
    sense.pop("glosses", None)
    sense.pop("meanings", None)
    for key in list(sense):
        if key not in ("pos", "definitions", "explanations"):
            sense.pop(key, None)
    return sense


# Keys the seed schemas actually allow on each item. Anything else the
# model emits (e.g. `pattern` on a phrase item, `literal_translation`,
# `items`, `id`) is dropped before strict validation. The post-hoc
# ``apply_explanation_rules`` pass in seed.py decides whether
# explanation_primary/secondary survive, so the normalizer only has to
# keep them around as strings-or-null.
_SEED_STRUCTURE_KEYS = {"pattern", "example_sentence",
                        "explanation", "explanation_primary",
                        "explanation_secondary"}
_SEED_PHRASE_KEYS = {"phrase", "example_sentence",
                     "explanation", "explanation_primary",
                     "explanation_secondary"}


def _normalize_seed_batch(data: Any, *, array_name: str) -> dict:
    """Repair common shape deviations non-OpenAI models produce for the
    per-batch seed schema (``{"structures": [...]}`` or ``{"phrases":
    [...]}``) before strict validation. Specifically:

    * The model returns a bare JSON array ``[...]`` instead of the
      expected wrapper object — wrap it as ``{array_name: [...]}``.
    * The model nests one level too deep (``{"items": [...]}`` /
      ``{"data": [...]}``) — unwrap into ``{array_name: [...]}``.
    * Items are not dicts — drop them.
    * Items carry extra keys the strict schema forbids (e.g.
      ``pattern`` on a phrase item, ``literal_translation``) — drop
      unknown keys, keeping only the schema-allowed set.
    * ``explanation_primary`` / ``explanation_secondary`` come back as
      empty strings or non-strings — coerce to null so the
      string-or-null schema accepts them.

    Must never raise: it runs before validation on every attempt.
    """
    allowed = (_SEED_STRUCTURE_KEYS if array_name == "structures"
               else _SEED_PHRASE_KEYS)
    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Prefer the canonical key; fall back to common wrapper names
        # the model sometimes uses instead of the requested array_name.
        for key in (array_name, "items", "data", "results"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
    if items is None:
        # Leave data untouched so the validator reports the real error.
        return data if isinstance(data, dict) else {}
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kept = {}
        for key in allowed:
            if key not in item:
                continue
            value = item[key]
            if key in ("explanation_primary", "explanation_secondary"):
                if not isinstance(value, str) or not value:
                    value = None
            kept[key] = value
        cleaned.append(kept)
    return {array_name: cleaned}


def lookup_word_via_llm(*, lang: str, word: str, explanation_primary: str | None,
                        explanation_secondary: str | None,
                        level: str | None = None) -> dict:
    primary = explanation_primary or lang
    secondary = explanation_secondary
    # When any of the three languages is Chinese, steer the model toward
    # Traditional characters. Naming "Traditional Chinese" alone is not
    # enough for some models — they default to Simplified — so we add a
    # quiet, script-agnostic rule to the system prompt.
    script_note = ""
    if "zh" in (lang, primary, secondary):
        script_note = " For Chinese content, use Traditional Chinese characters."
    system = (
        "You are a bilingual dictionary. Return ONLY a JSON object matching the "
        "provided schema. Do not include prose, code fences, or commentary. "
        "Provide concise, accurate glosses and one natural example per sense."
        + script_note
        + _level_directive(level)
    )
    user = (
        f"Target language (the dictionary's language): {_lang_name(lang)} ({lang})\n"
        f"Word to look up: {word}\n"
        f"Primary explanation language: {primary}\n"
        f"Secondary explanation language (optional): {secondary or '(none)'}\n"
        f"The word `{word}` is a word in {_lang_name(lang)}. Look it up as a "
        f"{_lang_name(lang)} word: the `glossary` and `example` must be in "
        f"{_lang_name(lang)}. Do NOT translate the word into another language "
        f"or treat it as a foreign word — it belongs to {_lang_name(lang)}.\n"
        "Return a JSON object with a single top-level key `senses` holding "
        "an array of 1-3 sense objects. Each sense is an object with EXACTLY "
        "these fields, using the EXACT names below:\n"
        "- `pos`: the part of speech (use this exact key, never "
        "`part_of_speech`)\n"
        "- `definitions`: a non-empty array of objects (never call this "
        "`glosses`), each with EXACTLY `glossary` (a concise definition "
        f"OF THE WORD, written in the target language ({_lang_name(lang)}) "
        "— not a translation into another language) and optionally "
        f"`example` (one natural sentence USING the word, written in "
        f"the target language ({_lang_name(lang)}); may be null)\n"
        "- `explanations`: an object with `primary` and `secondary` "
        "(strings or null) in the requested explanation languages — these "
        "are the translations/explanations for a reader who doesn't "
        f"speak {_lang_name(lang)}\n"
        "Do not invent any other keys."
    )
    return complete_json(
        schema=DICT_WORD_SCHEMA,
        schema_name="dict_word",
        system=system,
        user=user,
        temperature=0.2,
        normalize=_normalize_dict_word,
    )


SEED_BATCH_SIZE: int = 20


def _seed_array_schema(array_name: str, *, require_primary: bool) -> dict:
    """Schema for a single seed batch (only structures or only phrases,
    with at most SEED_BATCH_SIZE items)."""
    struct_required = ["pattern", "example_sentence", "explanation"]
    phrase_required = ["phrase", "example_sentence", "explanation"]
    if require_primary:
        struct_required.append("explanation_primary")
        phrase_required.append("explanation_primary")
    required = struct_required if array_name == "structures" else phrase_required
    base_props = (
        {**_STRUCTURE_ITEM_PROPS, **SEED_ITEM_BASE["properties"]}
        if array_name == "structures"
        else {**_PHRASE_ITEM_PROPS, **SEED_ITEM_BASE["properties"]}
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [array_name],
        "properties": {
            array_name: {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": required,
                    "properties": base_props,
                },
            },
        },
    }


def _build_seed_user_prompt(
    *,
    lang: str,
    n: int,
    kind: str,
    primary: str | None,
    secondary: str | None,
    target_name: str,
    primary_name: str,
    secondary_name: str,
) -> str:
    parts = [
        f"Target language (the one being learned): {target_name} ({lang}).",
        f"Generate {n} {kind} for learners of {lang}.",
    ]
    if kind == "structures":
        parts.append(
            f"{n} common sentence structures (clause patterns with examples)."
        )
    else:
        parts.append(
            f"{n} common phrases or idioms, each with a single example "
            f"sentence showing the phrase in use."
        )
    if primary:
        parts.append(f"For each item, include explanation_primary in {primary_name}.")
    else:
        parts.append(
            f"Do NOT include explanation_primary: the target language "
            f"({target_name}) already shows the item itself."
        )
    if secondary:
        parts.append(f"For each item, include explanation_secondary in {secondary_name}.")
    else:
        parts.append("Do NOT include explanation_secondary.")
    parts.append(
        f"All structure items: pattern and example_sentence must be in "
        f"{target_name}. phrase and example_sentence must be in {target_name}."
    )
    if kind == "phrases":
        parts.append(
            f"Each phrase's example_sentence is one natural sentence in "
            f"{target_name} showing the phrase used in context — not a "
            f"translation, just a real usage example."
        )
    parts.append(
        f"Each item must also include an `explanation` (REQUIRED, in "
        f"{target_name}): a paragraph-length usage note describing when "
        f"and why to use this {kind[:-1]}, register (formal/informal), "
        f"common context, and any alternatives. 2-4 sentences."
    )
    return "\n".join(parts)


def _seed_one_batch(
    *,
    array_name: str,
    n: int,
    kind: str,
    lang: str,
    primary: str | None,
    secondary: str | None,
    require_primary: bool,
    target_name: str,
    primary_name: str,
    secondary_name: str,
    level: str | None = None,
) -> list[dict]:
    system = (
        "You generate concise language-learning content. Return ONLY a JSON "
        "object matching the schema. No prose, no code fences."
        + _level_directive(level)
    )
    user = _build_seed_user_prompt(
        lang=lang, n=n, kind=kind, primary=primary, secondary=secondary,
        target_name=target_name,
        primary_name=primary_name, secondary_name=secondary_name,
    )
    payload = complete_json(
        schema=_seed_array_schema(array_name, require_primary=require_primary),
        schema_name=f"seed_{array_name}",
        system=system,
        user=user,
        temperature=0.3,
        max_retries=1,
        normalize=lambda d: _normalize_seed_batch(d, array_name=array_name),
    )
    return payload.get(array_name) or []


def generate_structures_via_llm(
    *, lang: str, n: int,
    primary: str | None = None,
    secondary: str | None = None,
    batch_size: int | None = None,
    level: str | None = None,
) -> list[dict]:
    """Generate `n` structures for `lang` in batches of `batch_size`
    (default ``SEED_BATCH_SIZE``). Returns a list of structure dicts."""
    if batch_size is None:
        batch_size = SEED_BATCH_SIZE
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    if n == 0:
        return []

    keep_primary = _should_generate_primary(lang, primary)
    target_name = _lang_name(lang)
    primary_name = _lang_name(primary) if primary else ""
    secondary_name = _lang_name(secondary) if secondary else ""

    out: list[dict] = []
    remaining = n
    while remaining > 0:
        count = min(batch_size, remaining)
        batch = _seed_one_batch(
            array_name="structures", n=count, kind="structures",
            lang=lang, primary=primary, secondary=secondary,
            require_primary=keep_primary,
            target_name=target_name,
            primary_name=primary_name, secondary_name=secondary_name,
            level=level,
        )
        out.extend(batch)
        remaining -= count
    return out


def generate_phrases_via_llm(
    *, lang: str, n: int,
    primary: str | None = None,
    secondary: str | None = None,
    batch_size: int | None = None,
    level: str | None = None,
) -> list[dict]:
    """Generate `n` phrases for `lang` in batches of `batch_size`
    (default ``SEED_BATCH_SIZE``). Returns a list of phrase dicts."""
    if batch_size is None:
        batch_size = SEED_BATCH_SIZE
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    if n == 0:
        return []

    keep_primary = _should_generate_primary(lang, primary)
    target_name = _lang_name(lang)
    primary_name = _lang_name(primary) if primary else ""
    secondary_name = _lang_name(secondary) if secondary else ""

    out: list[dict] = []
    remaining = n
    while remaining > 0:
        count = min(batch_size, remaining)
        batch = _seed_one_batch(
            array_name="phrases", n=count, kind="phrases",
            lang=lang, primary=primary, secondary=secondary,
            require_primary=keep_primary,
            target_name=target_name,
            primary_name=primary_name, secondary_name=secondary_name,
            level=level,
        )
        out.extend(batch)
        remaining -= count
    return out


def generate_seed_payload(lang: str, n_structures: int, n_phrases: int,
                          *, primary: str | None = None,
                          secondary: str | None = None,
                          batch_size: int | None = None,
                          level: str | None = None) -> dict:
    """Generate a starter set of structures and phrases for `lang`.

    `primary` / `secondary` are the user's native explanation languages
    from settings. They control which explanation fields the LLM is
    asked to fill, per the rules in the module docstring. The function
    always post-processes the response to null out fields that the
    rules say to skip, so a chatty model that returns a value anyway
    is corrected on our side.

    Structures and phrases are generated in separate LLM calls
    (batched internally by ``SEED_BATCH_SIZE``). The two payloads are
    merged into the seed-shaped dict the caller expects.
    """
    structures = generate_structures_via_llm(
        lang=lang, n=n_structures, primary=primary, secondary=secondary,
        batch_size=batch_size, level=level,
    )
    phrases = generate_phrases_via_llm(
        lang=lang, n=n_phrases, primary=primary, secondary=secondary,
        batch_size=batch_size, level=level,
    )
    payload = {"structures": structures, "phrases": phrases}
    apply_explanation_rules(
        payload, lang=lang, primary=primary, secondary=secondary,
    )
    return payload


FILL_BATCH_SIZE: int = 20


def _fill_array_schema(kind: str) -> dict:
    """Schema for one batch of fill responses."""
    if kind == "structure":
        item = FILL_STRUCTURE_SCHEMA
    else:
        item = FILL_PHRASE_SCHEMA
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [kind + "s"],
        "properties": {
            kind + "s": {
                "type": "array",
                "items": item,
            },
        },
    }


def _build_fill_user_prompt(
    *, kind: str, lang: str, partials: list[dict],
    target_name: str, primary_name: str, secondary_name: str,
    keep_primary: bool, keep_secondary: bool,
) -> str:
    rules: list[str] = []
    if keep_primary:
        rules.append(f"Fill explanation_primary in {primary_name}.")
    else:
        rules.append(
            f"Do NOT include explanation_primary: the target language "
            f"({target_name}) already shows the {kind}."
        )
    if keep_secondary:
        rules.append(f"Fill explanation_secondary in {secondary_name}.")
    else:
        rules.append("Do NOT include explanation_secondary.")
    extra = (
        f" example_sentence must be in {target_name}."
        if kind == "structure"
        else f" example_sentence must be in {target_name}: one natural sentence showing the phrase in use."
    )
    extra += f" `explanation` (if null) is a paragraph-length usage note in {target_name}."
    return (
        f"Language: {lang}\n"
        f"Target language name: {target_name}\n"
        f"Partial input (already-filled fields are non-null and must not be changed):\n"
        f"{json.dumps(partials, ensure_ascii=False, indent=2)}\n"
        f"Fill any null fields. " + " ".join(rules) + extra
    )


def _fill_one_batch(
    *, kind: str, partials: list[dict],
    lang: str, primary: str | None, secondary: str | None,
    keep_primary: bool, keep_secondary: bool,
    target_name: str, primary_name: str, secondary_name: str,
    level: str | None = None,
) -> list[dict]:
    if kind == "structure":
        system = (
            "You complete sentence-structure entries for language learners. "
            "Return ONLY a JSON object matching the schema. Only fill empty fields. "
            "Do not invent values for fields the user already provided."
            + _level_directive(level)
        )
    else:
        system = (
            "You complete phrase entries for language learners. "
            "Return ONLY a JSON object matching the schema. Only fill empty fields. "
            "Do not invent values for fields the user already provided."
            + _level_directive(level)
        )
    user = _build_fill_user_prompt(
        kind=kind, lang=lang, partials=partials,
        target_name=target_name,
        primary_name=primary_name, secondary_name=secondary_name,
        keep_primary=keep_primary, keep_secondary=keep_secondary,
    )
    payload = complete_json(
        schema=_fill_array_schema(kind),
        schema_name=f"fill_{kind}s",
        system=system,
        user=user,
        temperature=0.2,
    )
    items = payload.get(kind + "s") or []
    apply_explanation_rules(
        payload, lang=lang, primary=primary, secondary=secondary,
    )
    return items


def fill_structures_via_llm(
    *, lang: str, partials: list[dict],
    primary: str | None = None, secondary: str | None = None,
    batch_size: int | None = None,
    level: str | None = None,
) -> list[dict]:
    """Fill in null fields for a batch of partial structure dicts.
    Each input partial becomes one filled output dict. Batched."""
    if batch_size is None:
        batch_size = FILL_BATCH_SIZE
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if not partials:
        return []
    keep_primary = _should_generate_primary(lang, primary)
    keep_secondary = _should_generate_secondary(primary, secondary)
    target_name = _lang_name(lang)
    primary_name = _lang_name(primary) if primary else ""
    secondary_name = _lang_name(secondary) if secondary else ""
    out: list[dict] = []
    for chunk in _chunk(partials, batch_size):
        out.extend(
            _fill_one_batch(
                kind="structure", partials=chunk,
                lang=lang, primary=primary, secondary=secondary,
                keep_primary=keep_primary, keep_secondary=keep_secondary,
                target_name=target_name,
                primary_name=primary_name, secondary_name=secondary_name,
                level=level,
            )
        )
    return out


def fill_phrases_via_llm(
    *, lang: str, partials: list[dict],
    primary: str | None = None, secondary: str | None = None,
    batch_size: int | None = None,
    level: str | None = None,
) -> list[dict]:
    """Fill in null fields for a batch of partial phrase dicts.
    Each input partial becomes one filled output dict. Batched."""
    if batch_size is None:
        batch_size = FILL_BATCH_SIZE
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if not partials:
        return []
    keep_primary = _should_generate_primary(lang, primary)
    keep_secondary = _should_generate_secondary(primary, secondary)
    target_name = _lang_name(lang)
    primary_name = _lang_name(primary) if primary else ""
    secondary_name = _lang_name(secondary) if secondary else ""
    out: list[dict] = []
    for chunk in _chunk(partials, batch_size):
        out.extend(
            _fill_one_batch(
                kind="phrase", partials=chunk,
                lang=lang, primary=primary, secondary=secondary,
                keep_primary=keep_primary, keep_secondary=keep_secondary,
                target_name=target_name,
                primary_name=primary_name, secondary_name=secondary_name,
                level=level,
            )
        )
    return out


def fill_structure_via_llm(*, lang: str, partial: dict,
                            primary: str | None = None,
                            secondary: str | None = None,
                            level: str | None = None) -> dict:
    """Single-row convenience wrapper around :func:`fill_structures_via_llm`."""
    items = fill_structures_via_llm(
        lang=lang, partials=[partial],
        primary=primary, secondary=secondary, batch_size=1, level=level,
    )
    return items[0] if items else {}


def fill_phrase_via_llm(*, lang: str, partial: dict,
                         primary: str | None = None,
                         secondary: str | None = None,
                         level: str | None = None) -> dict:
    """Single-row convenience wrapper around :func:`fill_phrases_via_llm`."""
    items = fill_phrases_via_llm(
        lang=lang, partials=[partial],
        primary=primary, secondary=secondary, batch_size=1, level=level,
    )
    return items[0] if items else {}


# ---- analyze_text_via_llm ----------------------------------------------
#
# "Analyze" takes a free-form sentence or paragraph in the target language
# and returns three lists extracted from it: sentence structures (clause
# patterns worth learning), phrases/idioms (multi-word expressions), and
# difficult words (terms the learner is likely to need to look up). Each
# item carries the same explanation fields the rest of the app uses
# (`explanation` in the target language, plus `explanation_primary` and
# `explanation_secondary` in the user's natives), so the frontend can
# "Add" any item to the existing structures/phrases/vocab tables with a
# single click — no second LLM call.
#
# The response is a single object; the model is not asked to return
# anything the existing schemas already ask for, but the explanation
# rules still apply (skip `explanation_primary` when the target language
# equals the user's primary native, skip `explanation_secondary` when it
# would be redundant with primary or unset).

ANALYZE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["structures", "phrases", "words"],
    "properties": {
        "structures": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pattern", "example_sentence", "explanation"],
                "properties": {
                    "pattern": {"type": "string", "minLength": 1, "maxLength": 500},
                    "example_sentence": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "explanation": {"type": "string", "minLength": 1, "maxLength": 1500},
                    "explanation_primary": {"type": ["string", "null"], "maxLength": 1000},
                    "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
                },
            },
        },
        "phrases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["phrase", "example_sentence", "explanation"],
                "properties": {
                    "phrase": {"type": "string", "minLength": 1, "maxLength": 500},
                    "example_sentence": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "explanation": {"type": "string", "minLength": 1, "maxLength": 1500},
                    "explanation_primary": {"type": ["string", "null"], "maxLength": 1000},
                    "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
                },
            },
        },
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["word", "pos", "glossary"],
                "properties": {
                    "word": {"type": "string", "minLength": 1, "maxLength": 200},
                    "pos": {"type": "string", "maxLength": 32},
                    "glossary": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "example": {"type": ["string", "null"], "maxLength": 1000},
                    "explanation_primary": {"type": ["string", "null"], "maxLength": 1000},
                    "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
                },
            },
        },
    },
}


def _build_analyze_user_prompt(
    *, lang: str, text: str,
    target_name: str, primary_name: str, secondary_name: str,
    keep_primary: bool, keep_secondary: bool,
) -> str:
    parts: list[str] = [
        f"Target language (the one being learned): {target_name} ({lang}).",
        f"User input (a sentence or short paragraph in {target_name}):",
        text,
        "",
        "Extract up to 5 sentence structures (clause patterns), up to 5 phrases/idioms,",
        "and up to 8 difficult words that a learner of {lang} would benefit from studying.".format(lang=target_name),
        "",
        "Definitions:",
        f"- 'structures': clause patterns worth learning, with one example sentence",
        f"  from the input showing the pattern. `pattern` is the skeleton, e.g. ",
        f"  'X makes Y Z' or 'A rather than B'. `example_sentence` is a verbatim",
        f"  (or lightly cleaned-up) sentence from the input. `explanation` (in",
        f"  {target_name}) is a short usage note.",
        f"- 'phrases': multi-word expressions worth memorizing (collocations,",
        f"  idioms, set phrases). `phrase` is the expression itself. `example_sentence`",
        f"  is a verbatim sentence from the input using the phrase. `explanation`",
        f"  (in {target_name}) is a short usage note.",
        f"- 'words': single words from the input that are uncommon or carry",
        f"  meaning that's not obvious from context. `word` is the lemma form,",
        f"  `pos` is its part of speech, `glossary` is a short definition in",
        f"  {target_name}, and `example` is an optional short phrase showing",
        f"  it used in context (in {target_name}).",
        "",
        f"All example_sentence / phrase / example values must be in {target_name}.",
    ]
    if keep_primary:
        parts.append(
            f"For each item, include explanation_primary in {primary_name} (a short"
            f" translation or note for a reader who doesn't speak {target_name})."
        )
    else:
        parts.append(
            f"Do NOT include explanation_primary: the target language "
            f"({target_name}) already shows the item itself."
        )
    if keep_secondary:
        parts.append(
            f"For each item, include explanation_secondary in {secondary_name}."
        )
    else:
        parts.append("Do NOT include explanation_secondary.")
    parts.append(
        "If the input is too short or too simple to extract useful items,"
        " return empty arrays (not invented content)."
    )
    return "\n".join(parts)


def _normalize_analyze(data: dict) -> dict:
    """Repair common field-name variants non-OpenAI models produce for the
    analyze schema. Like the dict_word normalizer, must never raise: runs
    before strict validation on every attempt.

    - `word`/lemma case-folded + truncated to 200 chars.
    - drop `definitions` / `meanings` from word items (the schema only
      allows `glossary`).
    - structures/phrases: drop any unknown keys so strict validation passes.
    """
    for kind in ("structures", "phrases"):
        for item in (data.get(kind) or []):
            if not isinstance(item, dict):
                continue
            for key in list(item):
                if key not in (
                    "pattern", "phrase",
                    "example_sentence",
                    "explanation",
                    "explanation_primary",
                    "explanation_secondary",
                ):
                    item.pop(key, None)
    for w in (data.get("words") or []):
        if not isinstance(w, dict):
            continue
        if isinstance(w.get("word"), str):
            w["word"] = w["word"].strip()[:200]
        # Promote `definition` -> `glossary` if the model conflated them.
        if not w.get("glossary") and isinstance(w.get("definition"), str):
            w["glossary"] = w["definition"]
        if not w.get("example") and isinstance(w.get("sentence"), str):
            w["example"] = w["sentence"]
        for key in list(w):
            if key not in (
                "word", "pos", "glossary", "example",
                "explanation_primary", "explanation_secondary",
            ):
                w.pop(key, None)
    return data


def analyze_text_via_llm(
    *, lang: str, text: str,
    primary: str | None = None,
    secondary: str | None = None,
    level: str | None = None,
) -> dict:
    """Ask the LLM to extract structures, phrases, and hard words from
    ``text`` (in ``lang``). Returns the parsed dict matching
    :data:`ANALYZE_SCHEMA`. The same explanation-language rules used
    elsewhere apply (see :data:`_should_generate_primary` and friends):
    ``explanation_primary`` is nulled out when the target language equals
    the user's primary native, and ``explanation_secondary`` is nulled
    out when it would be redundant with primary or unset.

    Raises :class:`LLMError` on network or schema failures.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text required")
    text = text.strip()
    if len(text) > 4000:
        # Cap input so the prompt + per-item explanations stays inside a
        # single LLM call. 4000 is well above a paragraph but well below
        # a chapter — long enough for the page's use case, short enough
        # to keep the schema-strict response inside most proxies' timeouts.
        text = text[:4000]
    keep_primary = _should_generate_primary(lang, primary)
    keep_secondary = _should_generate_secondary(primary, secondary)
    target_name = _lang_name(lang)
    primary_name = _lang_name(primary) if primary else ""
    secondary_name = _lang_name(secondary) if secondary else ""

    script_note = ""
    if "zh" in (lang, primary, secondary):
        script_note = " For Chinese content, use Traditional Chinese characters."

    system = (
        "You extract language-learning content from a short text. Return ONLY "
        "a JSON object matching the schema. No prose, no code fences."
        + script_note
        + _level_directive(level)
    )
    user = _build_analyze_user_prompt(
        lang=lang, text=text,
        target_name=target_name,
        primary_name=primary_name,
        secondary_name=secondary_name,
        keep_primary=keep_primary,
        keep_secondary=keep_secondary,
    )
    data = complete_json(
        schema=ANALYZE_SCHEMA,
        schema_name="analyze_text",
        system=system,
        user=user,
        temperature=0.2,
        max_retries=0,
        normalize=_normalize_analyze,
        timeout=ANALYZE_TIMEOUT_SECONDS,
    )
    # Post-process the same way the seed/fill paths do, so a chatty
    # model that returns a redundant `explanation_primary` for a
    # target language that equals the user's primary native still ends
    # up with that field nulled out in the DB / UI.
    apply_explanation_rules(
        data, lang=lang, primary=primary, secondary=secondary,
    )
    return data


# Analyze asks for up to 5 + 5 + 8 items each with up to three
# explanation fields, so the schema-strict response can easily hit
# 8-15k characters. On slow local proxies the default global LLM
# timeout (180s) is too tight even for a single attempt, and a retry
# on a near-miss is wasted user-facing time — better to fail fast and
# let the user click again than to block the page for six minutes.
ANALYZE_TIMEOUT_SECONDS: int = int(
    os.environ.get("LLM_ANALYZE_TIMEOUT_SECONDS", "600")
)


# ---- refine_text_via_llm -----------------------------------------------
#
# "Refine" takes a free-form sentence or short paragraph in the target
# language and returns:
#
#   * `corrected` — the same text with grammar / spelling / word-choice
#     fixes. Stays in the target language.
#   * `native` — a more idiomatic, native-speaker version of the same
#     meaning, in the target language. Often a different sentence, not
#     just a different word order.
#   * `edits` — a list of small, in-place changes, each with the
#     original span, the suggested span, and a one-line reason. The UI
#     uses this to underline the bad bits and show what to change.
#   * `explanation` — a short paragraph in the target language
#     summarizing the main issues and patterns to watch for.
#
# `corrected` and `native` are always in the target language. The
# `edits` reasons are also in the target language (a learner reading
# "use past tense, not present" learns the grammar term in context).
# The model is also asked to provide `explanation_primary` and
# `explanation_secondary` so non-target readers can see the same
# feedback in their native language — the explanation-language rules
# in :func:`apply_explanation_rules` still apply, so the redundant
# fields get nulled out.
#
# Like Analyze, this is a single LLM call that produces a sizable
# response (potentially several thousand characters), and a slow
# near-miss is worse for the user than a quick fail. We use the same
# dedicated timeout and `max_retries=0` policy as Analyze.

REFINE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["corrected", "native", "edits", "explanation"],
    "properties": {
        "corrected": {"type": "string", "minLength": 1, "maxLength": 4000},
        "native":    {"type": "string", "minLength": 1, "maxLength": 4000},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["original", "suggested", "reason"],
                "properties": {
                    "original":  {"type": "string", "minLength": 1, "maxLength": 500},
                    "suggested": {"type": "string", "minLength": 1, "maxLength": 500},
                    "reason":    {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
        },
        "explanation":          {"type": "string", "minLength": 1, "maxLength": 1500},
        "explanation_primary":  {"type": ["string", "null"], "maxLength": 1000},
        "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
    },
}


REFINE_TIMEOUT_SECONDS: int = int(
    os.environ.get("LLM_REFINE_TIMEOUT_SECONDS", "600")
)


def _build_refine_user_prompt(
    *, lang: str, text: str,
    target_name: str, primary_name: str, secondary_name: str,
    keep_primary: bool, keep_secondary: bool,
) -> str:
    parts: list[str] = [
        f"Target language (the one being learned): {target_name} ({lang}).",
        f"User input (a sentence or short paragraph in {target_name}):",
        text,
        "",
        "Produce four fields:",
        f"- `corrected`: the input rewritten so it is grammatical and natural,",
        f"  in {target_name}. Make minimal changes — fix errors, don't rewrite the style.",
        f"- `native`: a more idiomatic version a fluent speaker would actually say,",
        f"  in {target_name}. This may be a noticeably different sentence.",
        f"- `edits`: an ordered list of small, in-place changes from `corrected`",
        f"  (or from `native` if the change is the rewrite itself), each with",
        f"  `original` (the bad span, copied verbatim from the input), `suggested`",
        f"  (the fixed span, copied verbatim from `corrected` or `native`), and",
        f"  `reason` (a short note in {target_name} explaining the change). Order",
        f"  most-important changes first. If the input is already correct, return",
        f"  an empty `edits` list.",
        f"- `explanation`: a short paragraph in {target_name} summarizing the main",
        f"  issues and the patterns to watch for next time. 2-4 sentences.",
        "",
        f"All values inside `corrected`, `native`, `edits[i].original`,",
        f"`edits[i].suggested`, and `explanation` must be in {target_name}.",
    ]
    if keep_primary:
        parts.append(
            f"Also include `explanation_primary`: a short translation of "
            f"`explanation` for a reader who doesn't speak {target_name}, "
            f"written in {primary_name}."
        )
    else:
        parts.append(
            f"Do NOT include `explanation_primary`: {target_name} already shows "
            f"the explanation."
        )
    if keep_secondary:
        parts.append(
            f"Also include `explanation_secondary` in {secondary_name}."
        )
    else:
        parts.append("Do NOT include `explanation_secondary`.")
    parts.append(
        "Return ONLY a JSON object matching the schema. No prose, no code fences."
    )
    return "\n".join(parts)


def _normalize_refine(data: dict) -> dict:
    """Repair common field-name variants non-OpenAI models produce for
    the refine schema. Must never raise: runs before strict validation
    on every attempt.

    - `native` / `native_version` -> `native`
    - `rewrite` / `improved` -> `corrected`
    - `changes` / `fixes` / `suggestions` -> `edits`
    - per-edit: `from` / `before` -> `original`; `to` / `after` ->
      `suggested`; `note` / `why` -> `reason`
    - any non-string `reason` is stringified; unknown keys are dropped.
    """
    if "native_version" in data and "native" not in data:
        data["native"] = data.pop("native_version")
    if "rewrite" in data and "corrected" not in data:
        data["corrected"] = data.pop("rewrite")
    if "improved" in data and "corrected" not in data:
        data["corrected"] = data.pop("improved")
    if "native" not in data and "more_native" in data:
        data["native"] = data.pop("more_native")
    for alias in ("changes", "fixes", "suggestions"):
        if alias in data and "edits" not in data:
            data["edits"] = data.pop(alias)
            break
    for key in list(data):
        if key not in (
            "corrected", "native", "edits", "explanation",
            "explanation_primary", "explanation_secondary",
        ):
            data.pop(key, None)
    edits = data.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if not isinstance(e, dict):
                continue
            if "original" not in e:
                for alias in ("from", "before", "bad", "input"):
                    if alias in e:
                        e["original"] = e.pop(alias)
                        break
            if "suggested" not in e:
                for alias in ("to", "after", "fixed", "replacement", "good"):
                    if alias in e:
                        e["suggested"] = e.pop(alias)
                        break
            if "reason" not in e:
                for alias in ("note", "why", "explanation", "message"):
                    if alias in e:
                        e["reason"] = e.pop(alias)
                        break
            for key in list(e):
                if key not in ("original", "suggested", "reason"):
                    e.pop(key, None)
            if not isinstance(e.get("reason"), str):
                e["reason"] = "" if e.get("reason") is None else str(e["reason"])
            if not isinstance(e.get("original"), str):
                e["original"] = "" if e.get("original") is None else str(e["original"])
            if not isinstance(e.get("suggested"), str):
                e["suggested"] = "" if e.get("suggested") is None else str(e["suggested"])
    return data


def refine_text_via_llm(
    *, lang: str, text: str,
    primary: str | None = None,
    secondary: str | None = None,
    level: str | None = None,
) -> dict:
    """Ask the LLM to correct and rewrite a sentence or short paragraph
    in ``lang``. Returns the parsed dict matching :data:`REFINE_SCHEMA`:
    ``{corrected, native, edits, explanation, explanation_primary?,
    explanation_secondary?}``. The same explanation-language rules used
    elsewhere apply (see :data:`_should_generate_primary` and friends).

    Raises :class:`LLMError` on network or schema failures.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text required")
    text = text.strip()
    if len(text) > 4000:
        text = text[:4000]
    keep_primary = _should_generate_primary(lang, primary)
    keep_secondary = _should_generate_secondary(primary, secondary)
    target_name = _lang_name(lang)
    primary_name = _lang_name(primary) if primary else ""
    secondary_name = _lang_name(secondary) if secondary else ""

    script_note = ""
    if "zh" in (lang, primary, secondary):
        script_note = " For Chinese content, use Traditional Chinese characters."

    system = (
        "You are a careful language tutor. Correct the user's text, "
        "offer a more idiomatic rewrite, and explain the changes. "
        "Return ONLY a JSON object matching the schema. No prose, "
        "no code fences."
        + script_note
        + _level_directive(level)
    )
    user = _build_refine_user_prompt(
        lang=lang, text=text,
        target_name=target_name,
        primary_name=primary_name,
        secondary_name=secondary_name,
        keep_primary=keep_primary,
        keep_secondary=keep_secondary,
    )
    data = complete_json(
        schema=REFINE_SCHEMA,
        schema_name="refine_text",
        system=system,
        user=user,
        temperature=0.2,
        max_retries=0,
        normalize=_normalize_refine,
        timeout=REFINE_TIMEOUT_SECONDS,
    )
    # Post-process the same way the seed/fill/analyze paths do.
    apply_explanation_rules(
        data, lang=lang, primary=primary, secondary=secondary,
    )
    return data


# ---- translate_text_via_llm --------------------------------------------
#
# "Translate" takes a free-form sentence or short paragraph in any
# language and produces a translation into the target language (the one
# the user is learning, i.e. their active language), plus a short
# teaching breakdown so the page doubles as a study aid rather than a
# plain translator:
#
#   * `translation` — the rendered target-language sentence(s). This is
#     the headline output.
#   * `alternatives` — 2-3 other natural ways to say the same thing in
#     the target language, varying register or word choice.
#   * `breakdown` — a word-by-word / phrase-by-phrase gloss mapping a
#     target-language span to the source-language meaning. The source
#     language is auto-detected by the model; the caller does not pass
#     it. The UI shows this as a small table under the translation.
#   * `notes` — a short grammar / usage note (in the target language)
#     explaining a construction or word choice the learner should
#     notice. Optional `notes_primary` / `notes_secondary` follow the
#     same explanation-language rules as the rest of the app.
#
# The explanation-language rules still apply to `notes_primary` /
# `notes_secondary` — primary is skipped when the target language
# equals it.

# ---- translate_text_via_llm --------------------------------------------
#
# "Translate" takes a free-form sentence or short paragraph in any
# language and produces a translation into the target language (the one
# the user is learning, i.e. their active language), plus a short
# teaching breakdown so the page doubles as a study aid rather than a
# plain translator.
#
# The input is split into sentences by the model. Each sentence is its
# own teaching block so several sentences share one response cleanly:
#
#   * `sentences` — one entry per sentence in the input, in order:
#       - `source`: the sentence lifted verbatim from the input.
#       - `translation`: the rendered target-language sentence.
#       - `alternatives`: 2-3 other natural ways to say the *same*
#         sentence in the target language. Each is an object with
#         `text` (the alternative) and `nuance` (a short note in the
#         target language on how the register / word choice / tone
#         differs from the headline translation).
#       - `breakdown`: a word-by-word / phrase-by-phrase gloss for
#         this sentence. `target` is a span lifted from `translation`,
#         `source` is the matching phrase in the input's original
#         language, `note` is an optional short grammar/usage gloss in
#         the target language.
#       - `notes`: a short paragraph (in the target language) pointing
#         out one or two constructions a learner should notice for this
#         sentence.
#   * `notes` — a short overall summary (in the target language) of the
#     patterns worth remembering across the whole input.
#   * `notes_primary` / `notes_secondary` — translations of the
#     top-level `notes` for the user's native languages, following the
#     same explanation-language rules as the rest of the app.
#
# The source language is auto-detected by the model. The explanation-
# language rules apply to `notes_primary` / `notes_secondary` — primary
# is skipped when the target language equals it.

TRANSLATE_SENTENCE_ITEM: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "translation", "alternatives", "breakdown", "notes"],
    "properties": {
        "source": {"type": "string", "minLength": 1, "maxLength": 1000},
        "translation": {"type": "string", "minLength": 1, "maxLength": 1000},
        "alternatives": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "nuance"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "nuance": {"type": ["string", "null"], "maxLength": 500},
                },
            },
        },
        "breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target", "source"],
                "properties": {
                    "target": {"type": "string", "minLength": 1, "maxLength": 500},
                    "source": {"type": "string", "minLength": 1, "maxLength": 500},
                    "note": {"type": ["string", "null"], "maxLength": 500},
                },
            },
        },
        "notes": {"type": "string", "minLength": 1, "maxLength": 1000},
    },
}

TRANSLATE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sentences", "notes"],
    "properties": {
        "sentences": {
            "type": "array",
            "minItems": 1,
            "items": TRANSLATE_SENTENCE_ITEM,
        },
        "notes": {"type": "string", "minLength": 1, "maxLength": 1500},
        "notes_primary": {"type": ["string", "null"], "maxLength": 1000},
        "notes_secondary": {"type": ["string", "null"], "maxLength": 1000},
    },
}


TRANSLATE_TIMEOUT_SECONDS: int = int(
    os.environ.get("LLM_TRANSLATE_TIMEOUT_SECONDS", "600")
)


def _build_translate_user_prompt(
    *, target_lang: str, text: str,
    target_name: str,
    primary_name: str, secondary_name: str,
    keep_primary: bool, keep_secondary: bool,
) -> str:
    parts: list[str] = [
        f"Target language (the one being learned): {target_name} ({target_lang}).",
        "The user's input may be in any language; detect it yourself.",
        "User input:",
        text,
        "",
        "Split the input into sentences. Produce one entry per sentence in",
        "`sentences`, in input order. Each entry has:",
        f"- `source`: the sentence lifted verbatim from the input.",
        f"- `translation`: a natural, grammatical rendering of that sentence",
        f"  in {target_name}. Translate the meaning, not word-for-word.",
        f"- `alternatives`: 2-3 other natural ways to say the SAME sentence",
        f"  in {target_name}. Each is an object with `text` (the alternative",
        f"  sentence) and `nuance` (a short note in {target_name} on how the",
        f"  register, word choice, or tone differs from `translation`). If",
        f"  there's only one good way, return a single item with a null",
        f"  `nuance`. Order most natural first.",
        f"- `breakdown`: a word-by-word / phrase-by-phrase gloss for THIS",
        f"  sentence. `target` is a phrase or word lifted from this",
        f"  sentence's `translation`; `source` is the matching phrase in the",
        f"  original language; `note` is an optional short grammar/usage",
        f"  gloss in {target_name} (may be null). Cover the whole sentence;",
        f"  order matches the translation's left-to-right flow.",
        f"- `notes`: a short paragraph in {target_name} pointing out one or",
        f"  two constructions, particles, or word choices a learner of",
        f"  {target_name} should notice in THIS sentence. 1-3 sentences.",
        "",
        f"Top-level `notes`: a short paragraph in {target_name} summarizing",
        f"the patterns worth remembering across the whole input. 2-4",
        f"sentences. If the input is a single sentence, this can overlap",
        f"with that sentence's `notes`.",
        "",
        f"Every `translation`, `alternatives[].text`, `breakdown[].target`,",
        f"per-sentence `notes`, and the top-level `notes` must be in",
        f"{target_name}. `source` and `breakdown[].source` must be in the",
        f"original language of the input.",
    ]
    if keep_primary:
        parts.append(
            f"Also include `notes_primary`: a short translation of the"
            f" top-level `notes` for a reader who doesn't speak"
            f" {target_name}, written in {primary_name}."
        )
    else:
        parts.append(
            f"Do NOT include `notes_primary`: {target_name} already shows"
            f" the notes."
        )
    if keep_secondary:
        parts.append(
            f"Also include `notes_secondary` in {secondary_name}."
        )
    else:
        parts.append("Do NOT include `notes_secondary`.")
    parts.append(
        "Return ONLY a JSON object matching the schema. No prose, no code fences."
    )
    return "\n".join(parts)


def _normalize_translate_alt(item: dict) -> None:
    """Repair one alternative object. Aliases: `alternative` / `phrase` /
    `sentence` -> `text`; `why` / `note` / `comment` / `difference` ->
    `nuance`. Unknown keys dropped. `nuance` defaults to null."""
    if not isinstance(item, dict):
        return
    if "text" not in item:
        for alias in ("alternative", "phrase", "sentence", "option", "value"):
            if alias in item:
                item["text"] = item.pop(alias)
                break
    if "nuance" not in item:
        for alias in ("why", "note", "comment", "difference", "reason", "gloss"):
            if alias in item:
                item["nuance"] = item.pop(alias)
                break
    for key in list(item):
        if key not in ("text", "nuance"):
            item.pop(key, None)
    if not isinstance(item.get("text"), str):
        item["text"] = "" if item.get("text") is None else str(item["text"])
    if not isinstance(item.get("nuance"), str):
        item["nuance"] = None if item.get("nuance") is None else str(item["nuance"])


def _normalize_translate_breakdown_item(item: dict) -> None:
    """Repair one breakdown object. Aliases: `to` / `target_phrase` ->
    `target`; `from` / `source_phrase` -> `source`; `comment` / `gloss`
    -> `note`. Unknown keys dropped."""
    if not isinstance(item, dict):
        return
    if "target" not in item:
        for alias in ("to", "target_phrase", "target_text", "phrase"):
            if alias in item:
                item["target"] = item.pop(alias)
                break
    if "source" not in item:
        for alias in ("from", "source_phrase", "source_text", "meaning"):
            if alias in item:
                item["source"] = item.pop(alias)
                break
    if "note" not in item:
        for alias in ("comment", "gloss", "explanation", "usage"):
            if alias in item:
                item["note"] = item.pop(alias)
                break
    for key in list(item):
        if key not in ("target", "source", "note"):
            item.pop(key, None)
    if not isinstance(item.get("note"), str):
        item["note"] = None if item.get("note") is None else str(item["note"])


def _normalize_translate_sentence(item: dict) -> None:
    """Repair one sentence object in place. Coerce `alternatives` from a
    list of strings into objects with null `nuance`, and run the per-item
    normalizers. Unknown keys dropped."""
    if not isinstance(item, dict):
        return
    if "source" not in item:
        for alias in ("original", "input", "input_sentence"):
            if alias in item:
                item["source"] = item.pop(alias)
                break
    if "translation" not in item:
        for alias in ("translated", "result", "target_sentence"):
            if alias in item:
                item["translation"] = item.pop(alias)
                break
    alts = item.get("alternatives")
    if alts is None:
        for alias in ("options", "variants", "alternative", "alt"):
            if alias in item:
                alts = item.pop(alias)
                break
    if isinstance(alts, str):
        alts = [alts]
    if isinstance(alts, list):
        fixed = []
        for a in alts:
            if isinstance(a, str):
                fixed.append({"text": a, "nuance": None})
            elif isinstance(a, dict):
                _normalize_translate_alt(a)
                fixed.append(a)
        item["alternatives"] = fixed
    else:
        item["alternatives"] = []
    breakdown = item.get("breakdown")
    if breakdown is None:
        for alias in ("gloss", "segments", "tokens"):
            if alias in item:
                breakdown = item.pop(alias)
                break
    if isinstance(breakdown, list):
        for b in breakdown:
            _normalize_translate_breakdown_item(b)
        item["breakdown"] = breakdown
    else:
        item["breakdown"] = []
    if "notes" not in item or not isinstance(item.get("notes"), str):
        for alias in ("note", "explanation", "comment"):
            if alias in item:
                item["notes"] = item.pop(alias)
                break
    for key in list(item):
        if key not in ("source", "translation", "alternatives",
                       "breakdown", "notes"):
            item.pop(key, None)


def _normalize_translate(data: dict) -> dict:
    """Repair common field-name variants non-OpenAI models produce for
    the translate schema. Must never raise: runs before strict validation
    on every attempt.

    - `sentences` may arrive as `sentence` / `items` / `results`.
    - legacy flat shape (top-level `translation`/`alternatives`/`breakdown`)
      is promoted into a single-element `sentences` array. The sentence's
      `source` is filled from ``_TRANSLATE_SOURCE_TEXT`` (set per call by
      :func:`translate_text_via_llm`) since the flat shape has no source.
    - per-sentence, per-alternative, and per-breakdown aliases handled
      by the helper normalizers above.
    - unknown top-level keys dropped.
    """
    if "sentences" not in data:
        for alias in ("sentence", "items", "results", "blocks"):
            if alias in data and isinstance(data[alias], list):
                data["sentences"] = data.pop(alias)
                break
    # Promote a legacy flat response into a single sentence block.
    if "sentences" not in data and isinstance(data.get("translation"), str):
        source_text = _TRANSLATE_SOURCE_TEXT or ""
        block = {
            "source": data.get("source") or data.get("original") or source_text,
            "translation": data.pop("translation"),
            "alternatives": data.get("alternatives") or [],
            "breakdown": data.get("breakdown") or [],
            "notes": data.get("notes") or "",
        }
        data["sentences"] = [block]
        data.pop("alternatives", None)
        data.pop("breakdown", None)
        # Keep top-level notes if present; otherwise reuse the block's.
        if not data.get("notes"):
            data["notes"] = block["notes"]
    for key in list(data):
        if key not in ("sentences", "notes",
                       "notes_primary", "notes_secondary"):
            data.pop(key, None)
    sentences = data.get("sentences")
    if isinstance(sentences, list):
        for s in sentences:
            _normalize_translate_sentence(s)
    return data


# Per-call stash of the user's input text. ``_normalize_translate`` reads
# this to backfill `source` when a model returns the legacy flat shape
# (which has no source field). Set by ``translate_text_via_llm`` via a
# closure-built wrapper; defaults to empty outside a translate call.
_TRANSLATE_SOURCE_TEXT: str = ""


def translate_text_via_llm(
    *, target_lang: str, text: str,
    primary: str | None = None,
    secondary: str | None = None,
    level: str | None = None,
) -> dict:
    """Ask the LLM to translate ``text`` (in any source language, auto-
    detected by the model) into ``target_lang`` (the language being
    learned) and produce a per-sentence teaching breakdown. Returns the
    parsed dict matching :data:`TRANSLATE_SCHEMA`: ``{sentences, notes,
    notes_primary?, notes_secondary?}`` where each sentence carries its
    own ``translation``, ``alternatives`` (with nuance), ``breakdown``,
    and ``notes``.

    The explanation-language rules apply to ``notes_primary`` /
    ``notes_secondary`` (see :data:`_should_generate_primary`): primary
    is skipped when the target language equals it, secondary is skipped
    when it would be redundant with primary or is unset.

    Raises :class:`LLMError` on network or schema failures.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text required")
    text = text.strip()
    if len(text) > 4000:
        text = text[:4000]
    keep_primary = _should_generate_primary(target_lang, primary)
    keep_secondary = _should_generate_secondary(primary, secondary)
    target_name = _lang_name(target_lang)
    primary_name = _lang_name(primary) if primary else ""
    secondary_name = _lang_name(secondary) if secondary else ""

    script_note = ""
    if "zh" in (target_lang, primary, secondary):
        script_note = " For Chinese content, use Traditional Chinese characters."

    system = (
        "You are a language tutor. Translate the user's text into the "
        "target language and break it down so a learner can follow. "
        "Return ONLY a JSON object matching the schema. No prose, "
        "no code fences."
        + script_note
        + _level_directive(level)
    )
    user = _build_translate_user_prompt(
        target_lang=target_lang, text=text,
        target_name=target_name,
        primary_name=primary_name, secondary_name=secondary_name,
        keep_primary=keep_primary, keep_secondary=keep_secondary,
    )
    # Stash the source text so the normalizer can backfill `source` on
    # a legacy flat response. Set/restore around the LLM call so a
    # concurrent call (none in v1 — single user, blocking) still sees a
    # sensible value.
    global _TRANSLATE_SOURCE_TEXT
    prev_source = _TRANSLATE_SOURCE_TEXT
    _TRANSLATE_SOURCE_TEXT = text
    try:
        data = complete_json(
            schema=TRANSLATE_SCHEMA,
            schema_name="translate_text",
            system=system,
            user=user,
            temperature=0.3,
            max_retries=0,
            normalize=_normalize_translate,
            timeout=TRANSLATE_TIMEOUT_SECONDS,
        )
    finally:
        _TRANSLATE_SOURCE_TEXT = prev_source
    # Post-process notes_primary/notes_secondary with the shared rules.
    # apply_explanation_rules handles a single fill-shaped object that
    # carries explanation_primary / explanation_secondary at the top
    # level, so temporarily rename our notes_* fields to the expected
    # keys, run the pass, then rename back. This keeps one rules engine
    # instead of a parallel one for translate.
    if "notes_primary" in data:
        data["explanation_primary"] = data.pop("notes_primary")
    if "notes_secondary" in data:
        data["explanation_secondary"] = data.pop("notes_secondary")
    apply_explanation_rules(
        data, lang=target_lang, primary=primary, secondary=secondary,
    )
    if "explanation_primary" in data:
        data["notes_primary"] = data.pop("explanation_primary")
    if "explanation_secondary" in data:
        data["notes_secondary"] = data.pop("explanation_secondary")
    return data


#
# "Apply explanations" is the per-language translation pass: load
# existing target-language structures and phrases, ask the LLM to fill
# in explanation_primary / explanation_secondary in the user's
# primary/secondary native languages, and return the new values keyed
# by row id. Target-language content is never returned by the model —
# the caller updates only the explanation columns.
#
# Batching: the LLM is called once per chunk of `APPLY_BATCH_SIZE` rows
# (structures and phrases are batched independently). Smaller payloads
# are easier on slow proxies that time out on huge responses, and a
# bad batch only fails that batch (other rows still get updated).


APPLY_BATCH_SIZE: int = 20


_APPLY_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id"],
    "properties": {
        "id": {"type": "integer"},
        "explanation": {"type": ["string", "null"], "maxLength": 1500},
        "explanation_primary": {"type": ["string", "null"], "maxLength": 1000},
        "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
    },
}


def _apply_batch_schema(array_name: str) -> dict:
    """Schema for a single-chunk apply response. The array contains
    only one kind (structures or phrases), and only `APPLY_BATCH_SIZE`
    items at most."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [array_name],
        "properties": {
            array_name: {
                "type": "array",
                "items": _APPLY_ITEM_SCHEMA,
            },
        },
    }


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _build_apply_rules(lang: str, primary: str | None, secondary: str | None,
                       target_name: str, primary_name: str,
                       secondary_name: str) -> list[str]:
    """Return the per-language rule lines for the apply prompt. The
    `explanation` paragraph rule is always included (it's a target-
    language field and always required)."""
    rules: list[str] = []
    if primary:
        rules.append(f"Fill explanation_primary in {primary_name}.")
    else:
        rules.append(
            f"Set explanation_primary to null: the target language "
            f"({target_name}) already shows the item."
        )
    if secondary:
        rules.append(f"Fill explanation_secondary in {secondary_name}.")
    else:
        rules.append("Set explanation_secondary to null.")
    rules.append(
        f"For each row, fill or refine `explanation` in {target_name} "
        f"(a paragraph-length usage note). If the row already has one "
        f"and it's adequate, return it unchanged; otherwise replace it "
        f"with a better one."
    )
    return rules


def _apply_explanations_one_batch(
    *,
    array_name: str,
    items: list[dict],
    lang: str,
    primary: str | None,
    secondary: str | None,
    target_name: str,
    primary_name: str,
    secondary_name: str,
    field_a: str,
    field_b: str,
    level: str | None = None,
) -> list[dict]:
    """Run one chunk through the LLM and return the per-row items."""
    system = (
        "You translate and explain existing language-learning rows. "
        "Return ONLY a JSON object matching the schema. Do NOT change "
        "the row's target-language pattern/example_sentence/phrase; "
        "only fill in the explanation columns."
        + _level_directive(level)
    )
    rules = _build_apply_rules(
        lang, primary, secondary, target_name, primary_name, secondary_name,
    )
    user = (
        f"Target language (the one being learned): {target_name} ({lang}).\n"
        f"For each row, return the same `id` and only the explanation fields.\n"
        + " ".join(rules) + "\n"
        + f"Rows (id, {field_a}, {field_b}, current explanation):\n"
        + json.dumps(
            [{"id": r.get("id"), field_a: r.get(field_a),
              field_b: r.get(field_b),
              "explanation": r.get("explanation")}
             for r in items],
            ensure_ascii=False, indent=2,
          )
    )
    payload = complete_json(
        schema=_apply_batch_schema(array_name),
        schema_name=f"apply_explanations_{array_name}",
        system=system,
        user=user,
        temperature=0.2,
    )
    # Apply the post-process to this batch's slice of the payload. The
    # helper handles both the seed-shaped ({"structures": [...]}) and
    # single-item shapes — we have a seed-shaped wrapper here.
    apply_explanation_rules(
        payload, lang=lang, primary=primary, secondary=secondary,
    )
    return payload.get(array_name) or []


def apply_explanations_via_llm(*, lang: str,
                                structures: list[dict],
                                phrases: list[dict],
                                primary: str | None = None,
                                secondary: str | None = None,
                                batch_size: int | None = None,
                                level: str | None = None) -> dict:
    """Translate existing target-language content into the user's
    primary/secondary natives. Returns a dict
    ``{"structures": [{id, explanation?, explanation_primary?, explanation_secondary?}, ...],
    "phrases":    [{...}, ...]}``. The caller looks up each row by `id`
    and updates only the explanation columns.

    The LLM is called once per `batch_size` rows (default
    ``APPLY_BATCH_SIZE``). Structures and phrases are batched
    independently. Each call fits comfortably inside the LLM timeout
    even on slow proxies.

    Target-language content (pattern, example_sentence, phrase) is
    never returned by the model — the prompt forbids it and the schema
    disallows it. The `explanation` column is target-language but is
    returned here: the apply path can fill it in if it's empty (or
    refine it if the user wants a fresh take).
    The rules in the module docstring determine which `explanation_*`
    fields the model is asked to fill. Post-processes via
    :func:`apply_explanation_rules` so a chatty model can't sneak a
    redundant gloss past the L == P guard.
    """
    if batch_size is None:
        batch_size = APPLY_BATCH_SIZE
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    target_name = _lang_name(lang)
    primary_name = _lang_name(primary) if primary else ""
    secondary_name = _lang_name(secondary) if secondary else ""

    out_structures: list[dict] = []
    for chunk in _chunk(structures, batch_size):
        out_structures.extend(
            _apply_explanations_one_batch(
                array_name="structures",
                items=chunk,
                lang=lang,
                primary=primary,
                secondary=secondary,
                target_name=target_name,
                primary_name=primary_name,
                secondary_name=secondary_name,
                field_a="pattern",
                field_b="example_sentence",
                level=level,
            )
        )
    out_phrases: list[dict] = []
    for chunk in _chunk(phrases, batch_size):
        out_phrases.extend(
            _apply_explanations_one_batch(
                array_name="phrases",
                items=chunk,
                lang=lang,
                primary=primary,
                secondary=secondary,
                target_name=target_name,
                primary_name=primary_name,
                secondary_name=secondary_name,
                field_a="phrase",
                field_b="example_sentence",
                level=level,
            )
        )
    return {"structures": out_structures, "phrases": out_phrases}


# ---- describe_image_via_llm -------------------------------------------
#
# "Describe" takes an uploaded image and the user's target language and
# produces:
#
#   * `description` — a short paragraph in the target language describing
#     what's in the picture. This is the headline output and the main
#     teaching payload: a learner reads it to learn how to describe a
#     scene in the language they're studying.
#   * `description_primary` / `description_secondary` — translations of
#     `description` into the user's native languages, following the same
#     explanation-language rules as the rest of the app (primary is
#     skipped when the target language equals it).
#   * `words` — a list of concrete vocabulary items visible in the image
#     (objects, animals, people, actions, settings). Each item is shaped
#     to fit the existing "Add to Vocab" one-click save flow:
#     `word`, `pos`, `glossary` (in the target language), `example` (a
#     short phrase using the word in the target language), plus
#     `explanation_primary` / `explanation_secondary` for non-target
#     readers.
#
# The image is sent to the model as a base64 data URL inside a
# multimodal Chat Completions message. Schema-strict JSON is still
# requested; the same `complete_json` retry/validate path is used, but
# the user message carries an `image_url` content part alongside the
# text instructions.

DESCRIBE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["description", "words"],
    "properties": {
        "description": {"type": "string", "minLength": 1, "maxLength": 2000},
        "description_primary": {"type": ["string", "null"], "maxLength": 2000},
        "description_secondary": {"type": ["string", "null"], "maxLength": 2000},
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["word", "pos", "glossary"],
                "properties": {
                    "word": {"type": "string", "minLength": 1, "maxLength": 200},
                    "pos": {"type": "string", "maxLength": 32},
                    "glossary": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "example": {"type": ["string", "null"], "maxLength": 1000},
                    "explanation_primary": {"type": ["string", "null"], "maxLength": 1000},
                    "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
                },
            },
        },
    },
}


DESCRIBE_TIMEOUT_SECONDS: int = int(
    os.environ.get("LLM_DESCRIBE_TIMEOUT_SECONDS", "600")
)


def _build_describe_user_prompt(
    *, target_name: str, primary_name: str, secondary_name: str,
    keep_primary: bool, keep_secondary: bool,
) -> str:
    parts: list[str] = [
        f"Target language (the one being learned): {target_name}.",
        "Describe the image in the target language and extract concrete",
        "vocabulary items visible in it. Return ONLY a JSON object matching",
        "the schema. No prose, no code fences.",
        "",
        "Fields:",
        f"- `description`: a short paragraph (3-6 sentences) in {target_name}",
        f"  describing what's in the picture. Use natural, everyday",
        f"  phrasing a learner can imitate. Mention the main subjects, the",
        f"  setting, and any notable action.",
        f"- `words`: up to 12 concrete vocabulary items visible in the image.",
        f"  Each item is an object with:",
        f"    * `word`: the lemma form, written in {target_name}.",
        f"    * `pos`: the part of speech (use this exact key).",
        f"    * `glossary`: a short definition OF THE WORD, written in",
        f"      {target_name} (not a translation).",
        f"    * `example`: an optional short phrase showing the word used in",
        f"      the context of the picture, written in {target_name}. May be",
        f"      null.",
        "",
        f"All `word`, `glossary`, and `example` values must be in {target_name}.",
    ]
    if keep_primary:
        parts.append(
            f"Also include `description_primary`: a translation of "
            f"`description` for a reader who doesn't speak {target_name}, "
            f"written in {primary_name}."
        )
    else:
        parts.append(
            f"Do NOT include `description_primary`: {target_name} already "
            f"shows the description."
        )
    if keep_secondary:
        parts.append(
            f"Also include `description_secondary` in {secondary_name}."
        )
    else:
        parts.append("Do NOT include `description_secondary`.")
    parts.append(
        "If the image has no clear subject or is too abstract to describe, "
        "return a single-sentence `description` and an empty `words` array."
    )
    return "\n".join(parts)


def _normalize_describe(data: dict) -> dict:
    """Repair common field-name variants non-OpenAI models produce for the
    describe schema. Must never raise: runs before strict validation on
    every attempt.

    - `caption` / `summary` / `text` -> `description`.
    - `description_native` / `description_translation` -> `description_primary`.
    - `vocabulary` / `items` / `vocab` -> `words`.
    - per-word: `lemma` / `term` / `name` -> `word`; `definition` / `meaning`
      -> `glossary`; `sentence` / `usage` -> `example`.
    - unknown keys dropped from both the top level and word items.
    """
    if not isinstance(data, dict):
        return data
    if "description" not in data:
        for alias in ("caption", "summary", "text", "scene"):
            if alias in data and isinstance(data[alias], str):
                data["description"] = data.pop(alias)
                break
    if "description_primary" not in data:
        for alias in ("description_native", "description_translation", "primary"):
            if alias in data:
                data["description_primary"] = data.pop(alias)
                break
    if "words" not in data:
        for alias in ("vocabulary", "items", "vocab", "terms"):
            if alias in data and isinstance(data[alias], list):
                data["words"] = data.pop(alias)
                break
    for key in list(data):
        if key not in ("description", "description_primary", "description_secondary", "words"):
            data.pop(key, None)
    words = data.get("words")
    if isinstance(words, list):
        cleaned = []
        for w in words:
            if not isinstance(w, dict):
                continue
            if "word" not in w:
                for alias in ("lemma", "term", "name", "label"):
                    if alias in w:
                        w["word"] = w.pop(alias)
                        break
            if "pos" not in w:
                for alias in ("part_of_speech", "part"):
                    if alias in w:
                        w["pos"] = w.pop(alias)
                        break
            if not w.get("glossary"):
                for alias in ("definition", "meaning", "gloss", "glossary_text"):
                    if alias in w:
                        w["glossary"] = w.pop(alias)
                        break
            if not w.get("example"):
                for alias in ("sentence", "usage", "usage_example"):
                    if alias in w:
                        w["example"] = w.pop(alias)
                        break
            for key in list(w):
                if key not in ("word", "pos", "glossary", "example",
                               "explanation_primary", "explanation_secondary"):
                    w.pop(key, None)
            if not isinstance(w.get("word"), str) or not w["word"].strip():
                continue
            if not isinstance(w.get("glossary"), str) or not w["glossary"].strip():
                continue
            if not isinstance(w.get("pos"), str):
                w["pos"] = ""
            w["word"] = w["word"].strip()[:200]
            w["glossary"] = w["glossary"].strip()[:1000]
            w["pos"] = w["pos"][:32]
            cleaned.append(w)
        data["words"] = cleaned
    return data


def _build_describe_messages(
    *, system: str, user_text: str, image_data_url: str,
) -> list[dict]:
    """Build the Chat Completions message array with a multimodal user
    turn. The image is attached as an `image_url` content part pointing at
    a base64 data URL. Text instructions live alongside it as a `text`
    part so the model knows the schema and language rules."""
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        },
    ]


def describe_image_via_llm(
    *, target_lang: str, image_bytes: bytes, mime_type: str = "image/jpeg",
    primary: str | None = None,
    secondary: str | None = None,
    level: str | None = None,
) -> dict:
    """Ask a vision-capable LLM to describe ``image_bytes`` in ``target_lang``
    and extract concrete vocabulary items visible in it. Returns the
    parsed dict matching :data:`DESCRIBE_SCHEMA`: ``{description,
    description_primary?, description_secondary?, words}``.

    The image is sent as a base64 data URL inside a multimodal Chat
    Completions message. The explanation-language rules apply to
    ``description_primary`` / ``description_secondary`` (primary is
    skipped when the target language equals it, secondary is skipped
    when it would be redundant with primary or unset).

    Raises :class:`LLMError` on network or schema failures, and
    :class:`ValueError` if ``image_bytes`` is empty or ``mime_type`` is
    not a recognized image type.
    """
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise ValueError("image_bytes required")
    if mime_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        raise ValueError(f"unsupported image mime type: {mime_type}")

    import base64
    encoded = base64.b64encode(bytes(image_bytes)).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"

    keep_primary = _should_generate_primary(target_lang, primary)
    keep_secondary = _should_generate_secondary(primary, secondary)
    target_name = _lang_name(target_lang)
    primary_name = _lang_name(primary) if primary else ""
    secondary_name = _lang_name(secondary) if secondary else ""

    script_note = ""
    if "zh" in (target_lang, primary, secondary):
        script_note = " For Chinese content, use Traditional Chinese characters."

    system = (
        "You are a language tutor. Describe pictures in the target language "
        "and pull out concrete vocabulary items a learner would want to "
        "study. Return ONLY a JSON object matching the schema. No prose, "
        "no code fences."
        + script_note
        + _level_directive(level)
    )
    user_text = _build_describe_user_prompt(
        target_name=target_name,
        primary_name=primary_name, secondary_name=secondary_name,
        keep_primary=keep_primary, keep_secondary=keep_secondary,
    )
    messages = _build_describe_messages(
        system=system, user_text=user_text, image_data_url=data_url,
    )
    raw = _describe_complete_json(
        schema=DESCRIBE_SCHEMA,
        schema_name="describe_image",
        messages=messages,
        temperature=0.3,
        max_retries=0,
        normalize=_normalize_describe,
        timeout=DESCRIBE_TIMEOUT_SECONDS,
    )
    # Reuse the shared explanation-rules engine for the words array by
    # treating the top-level `description_primary` / `description_secondary`
    # fields as if they were `explanation_primary` / `explanation_secondary`.
    # The shared helper walks `structures` / `phrases` / `words` arrays and
    # also handles a single fill-shaped object at the top level — but our
    # payload has BOTH (words array AND description_* at the top), so we
    # split: apply the array pass to a wrapper that only carries `words`,
    # then apply the single-object pass to the top-level description_*.
    data = json.loads(raw)
    words_payload = {"words": data.get("words") or []}
    apply_explanation_rules(
        words_payload, lang=target_lang, primary=primary, secondary=secondary,
    )
    data["words"] = words_payload.get("words") or []

    top_payload: dict = {}
    if "description_primary" in data:
        top_payload["explanation_primary"] = data.pop("description_primary")
    if "description_secondary" in data:
        top_payload["explanation_secondary"] = data.pop("description_secondary")
    apply_explanation_rules(
        top_payload, lang=target_lang, primary=primary, secondary=secondary,
    )
    if "explanation_primary" in top_payload:
        data["description_primary"] = top_payload.pop("explanation_primary")
    if "explanation_secondary" in top_payload:
        data["description_secondary"] = top_payload.pop("explanation_secondary")
    return data


def _describe_complete_json(
    *, schema: dict, schema_name: str, messages: list[dict],
    temperature: float = 0.2, max_retries: int = 1,
    normalize: Callable[[dict], dict] | None = None,
    timeout: int | None = None,
) -> str:
    """Sibling of :func:`complete_json` that takes a pre-built ``messages``
    array (so the describe path can attach an image content part) and
    returns the raw JSON string the model produced. Validates against
    ``schema`` with the same retry-on-schema-error policy as the text
    path."""
    validator = Draft202012Validator(schema)
    client = _client()
    last_error: str | None = None
    last_raw: str | None = None

    for attempt in range(max_retries + 1):
        if not last_error:
            attempt_messages = messages
        else:
            # Append a follow-up user text turn explaining the validation
            # failure. The original image is still visible from the
            # prior user turn.
            attempt_messages = messages + [
                {"role": "user", "content": (
                    "Your previous response failed validation:\n"
                    + last_error
                    + "\n\nReturn valid JSON only. No prose."
                )},
            ]
        raw = client.chat_messages(
            messages=attempt_messages,
            schema=schema, schema_name=schema_name,
            temperature=temperature, timeout=timeout,
        )
        last_raw = raw
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            last_error = f"not valid JSON: {e}"
            log.warning("LLM JSON parse error (attempt %d): %s", attempt + 1, e)
            continue
        if normalize is not None:
            try:
                data = normalize(data)
            except Exception as e:
                log.warning("LLM normalize error (attempt %d): %s", attempt + 1, e)
        try:
            validator.validate(data)
            return json.dumps(data)
        except ValidationError as e:
            last_error = e.message
            log.warning("LLM schema validation error (attempt %d): %s",
                        attempt + 1, e.message)
    sample = (last_raw or "")[:400]
    raise LLMSchemaError(
        f"LLM did not produce valid JSON for schema '{schema_name}' "
        f"after {max_retries + 1} attempts. Last error: {last_error}. "
        f"Last response (truncated to 400 chars): {sample!r}"
    )