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
    structure and phrase in ``payload`` according to the four-case rule
    table in the module docstring.

    ``payload`` may be either a seed-shaped object
    (``{"structures": [...], "phrases": [...]}``) or a single
    fill-shaped object (with ``explanation_primary`` and
    ``explanation_secondary`` at the top level). Mutates in place.
    """
    keep_p = _should_generate_primary(lang, primary)
    keep_s = _should_generate_secondary(primary, secondary)
    if "structures" in payload or "phrases" in payload:
        for s in (payload.get("structures") or []):
            if isinstance(s, dict):
                _strip_explanations(s, keep_primary=keep_p, keep_secondary=keep_s)
        for p in (payload.get("phrases") or []):
            if isinstance(p, dict):
                _strip_explanations(p, keep_primary=keep_p, keep_secondary=keep_s)
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
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pos", "definitions"],
                "properties": {
                    "pos": {"type": "string", "maxLength": 32},
                    "definitions": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["glossary"],
                            "properties": {
                                "glossary": {"type": "string", "minLength": 1, "maxLength": 1000},
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
) -> dict:
    """Send a prompt, get a JSON dict matching `schema`. Retries on schema error.

    ``normalize`` is an optional callable applied to each parsed response
    *before* strict validation. It should repair common, predictable
    field-name variants that non-OpenAI models produce (for example
    ``part_of_speech`` for ``pos``), converting them to the canonical
    schema keys. This keeps strict validation while tolerating the
    schem-agnostic aliases LLMs like to invent. It must not raise; any
    residual mismatches still surface as a validation error.

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
    def chat(self, *, system, user, schema, schema_name, temperature) -> str:
        raise NotImplementedError


class OpenAICompatClient(_BaseClient):
    def chat(self, *, system, user, schema, schema_name, temperature) -> str:
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
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
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
        return _post_json(url, payload, headers)

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


def _post_json(url: str, payload: dict, headers: dict) -> str:
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT_SECONDS)
    except requests.Timeout as e:
        raise LLMTimeout(
            f"request timed out after {config.LLM_TIMEOUT_SECONDS}s "
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
        return data
    for sense in senses:
        if not isinstance(sense, dict):
            continue
        pos = sense.pop("part_of_speech", None)
        if pos is None:
            pos = sense.pop("pos", None)
        if pos is not None:
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
    return data


def lookup_word_via_llm(*, lang: str, word: str, explanation_primary: str | None,
                        explanation_secondary: str | None) -> dict:
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
    )
    user = (
        f"Language: {lang}\n"
        f"Word: {word}\n"
        f"Primary explanation language: {primary}\n"
        f"Secondary explanation language (optional): {secondary or '(none)'}\n"
        "Provide 1-3 senses. Each sense is an object with EXACTLY these "
        "fields, using the EXACT names below:\n"
        "- `pos`: the part of speech (use this exact key, never "
        "`part_of_speech`)\n"
        "- `definitions`: a non-empty array of objects (never call this "
        "`glosses`), each with EXACTLY `glossary` (the gloss/translation) "
        "and optionally `example` (one natural sentence, may be null)\n"
        "- `explanations`: an object with `primary` and `secondary` "
        "(strings or null) in the requested explanation languages\n"
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
) -> list[dict]:
    system = (
        "You generate concise language-learning content. Return ONLY a JSON "
        "object matching the schema. No prose, no code fences."
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
    )
    return payload.get(array_name) or []


def generate_structures_via_llm(
    *, lang: str, n: int,
    primary: str | None = None,
    secondary: str | None = None,
    batch_size: int | None = None,
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
        )
        out.extend(batch)
        remaining -= count
    return out


def generate_phrases_via_llm(
    *, lang: str, n: int,
    primary: str | None = None,
    secondary: str | None = None,
    batch_size: int | None = None,
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
        )
        out.extend(batch)
        remaining -= count
    return out


def generate_seed_payload(lang: str, n_structures: int, n_phrases: int,
                          *, primary: str | None = None,
                          secondary: str | None = None,
                          batch_size: int | None = None) -> dict:
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
        batch_size=batch_size,
    )
    phrases = generate_phrases_via_llm(
        lang=lang, n=n_phrases, primary=primary, secondary=secondary,
        batch_size=batch_size,
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
) -> list[dict]:
    if kind == "structure":
        system = (
            "You complete sentence-structure entries for language learners. "
            "Return ONLY a JSON object matching the schema. Only fill empty fields. "
            "Do not invent values for fields the user already provided."
        )
    else:
        system = (
            "You complete phrase entries for language learners. "
            "Return ONLY a JSON object matching the schema. Only fill empty fields. "
            "Do not invent values for fields the user already provided."
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
            )
        )
    return out


def fill_phrases_via_llm(
    *, lang: str, partials: list[dict],
    primary: str | None = None, secondary: str | None = None,
    batch_size: int | None = None,
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
            )
        )
    return out


def fill_structure_via_llm(*, lang: str, partial: dict,
                            primary: str | None = None,
                            secondary: str | None = None) -> dict:
    """Single-row convenience wrapper around :func:`fill_structures_via_llm`."""
    items = fill_structures_via_llm(
        lang=lang, partials=[partial],
        primary=primary, secondary=secondary, batch_size=1,
    )
    return items[0] if items else {}


def fill_phrase_via_llm(*, lang: str, partial: dict,
                         primary: str | None = None,
                         secondary: str | None = None) -> dict:
    """Single-row convenience wrapper around :func:`fill_phrases_via_llm`."""
    items = fill_phrases_via_llm(
        lang=lang, partials=[partial],
        primary=primary, secondary=secondary, batch_size=1,
    )
    return items[0] if items else {}


# ---- apply_explanations_via_llm ---------------------------------------
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
) -> list[dict]:
    """Run one chunk through the LLM and return the per-row items."""
    system = (
        "You translate and explain existing language-learning rows. "
        "Return ONLY a JSON object matching the schema. Do NOT change "
        "the row's target-language pattern/example_sentence/phrase; "
        "only fill in the explanation columns."
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
                                batch_size: int | None = None) -> dict:
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
            )
        )
    return {"structures": out_structures, "phrases": out_phrases}