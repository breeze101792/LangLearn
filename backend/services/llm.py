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
from typing import Any

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

SEED_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["structures", "phrases"],
    "properties": {
        "structures": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pattern", "explanation_primary"],
                "properties": {
                    "pattern": {"type": "string", "minLength": 1, "maxLength": 500},
                    "example_sentence": {"type": ["string", "null"], "maxLength": 1000},
                    "explanation_primary": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
                },
            },
        },
        "phrases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["phrase", "explanation_primary"],
                "properties": {
                    "phrase": {"type": "string", "minLength": 1, "maxLength": 500},
                    "literal_translation": {"type": ["string", "null"], "maxLength": 500},
                    "explanation_primary": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
                },
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
        "explanation_primary": {"type": ["string", "null"], "maxLength": 1000},
        "explanation_secondary": {"type": ["string", "null"], "maxLength": 1000},
    },
}

FILL_PHRASE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "literal_translation": {"type": ["string", "null"], "maxLength": 500},
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
) -> dict:
    """Send a prompt, get a JSON dict matching `schema`. Retries on schema error."""
    validator = Draft202012Validator(schema)
    client = _client()
    last_error: str | None = None

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
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            last_error = f"not valid JSON: {e}"
            log.warning("LLM JSON parse error (attempt %d): %s", attempt + 1, e)
            continue
        try:
            validator.validate(data)
            return data
        except ValidationError as e:
            last_error = e.message
            log.warning("LLM schema validation error (attempt %d): %s", attempt + 1, e.message)

    raise LLMSchemaError(f"LLM did not produce valid JSON after {max_retries + 1} attempts")


class _BaseClient:
    def chat(self, *, system, user, schema, schema_name, temperature) -> str:
        raise NotImplementedError


class OpenAICompatClient(_BaseClient):
    def chat(self, *, system, user, schema, schema_name, temperature) -> str:
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "") or config.OPENAI_API_KEY
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        url = (os.environ.get("OPENAI_BASE_URL") or config.OPENAI_BASE_URL).rstrip("/") + "/chat/completions"
        model = os.environ.get("OPENAI_MODEL") or config.OPENAI_MODEL
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
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        return _post_json(url, payload, headers)

    def supports_strict_schema(self) -> bool:
        return True


def _post_json(url: str, payload: dict, headers: dict) -> str:
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT_SECONDS)
    except requests.Timeout as e:
        raise LLMTimeout(str(e)) from e
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
    return content


# ---- Domain helpers -----------------------------------------------------


def lookup_word_via_llm(*, lang: str, word: str, explanation_primary: str | None,
                        explanation_secondary: str | None) -> dict:
    system = (
        "You are a bilingual dictionary. Return ONLY a JSON object matching the "
        "provided schema. Do not include prose, code fences, or commentary. "
        "Provide concise, accurate glosses and one natural example per sense."
    )
    user = (
        f"Language: {lang}\n"
        f"Word: {word}\n"
        f"Primary explanation language: {explanation_primary or lang}\n"
        f"Secondary explanation language (optional): {explanation_secondary or '(none)'}\n"
        "Provide 1-3 senses. Each sense has part-of-speech, definitions, "
        "and explanations in the requested languages."
    )
    return complete_json(
        schema=DICT_WORD_SCHEMA,
        schema_name="dict_word",
        system=system,
        user=user,
        temperature=0.2,
    )


def generate_seed_payload(lang: str, n_structures: int, n_phrases: int) -> dict:
    system = (
        "You generate concise language-learning content. Return ONLY a JSON "
        "object matching the schema. No prose, no code fences."
    )
    user = (
        f"Generate a starter set for learners of {lang}.\n"
        f"- {n_structures} common sentence structures (clause patterns with examples).\n"
        f"- {n_phrases} common phrases or idioms with literal translation and explanation.\n"
        "Each item must include explanation_primary in English and explanation_secondary in Simplified Chinese."
    )
    return complete_json(
        schema=SEED_SCHEMA,
        schema_name="seed",
        system=system,
        user=user,
        temperature=0.3,
        max_retries=1,
    )


def fill_structure_via_llm(*, lang: str, partial: dict) -> dict:
    system = (
        "You complete a sentence-structure entry for language learners. "
        "Return ONLY a JSON object matching the schema. Only fill empty fields. "
        "Do not invent values for fields the user already provided."
    )
    user = (
        f"Language: {lang}\n"
        f"Partial input (already-filled fields are non-null and must not be changed):\n"
        f"{json.dumps(partial, ensure_ascii=False, indent=2)}\n"
        "Fill any null fields. explanation_primary must be English; "
        "explanation_secondary should be Simplified Chinese when present."
    )
    return complete_json(
        schema=FILL_STRUCTURE_SCHEMA,
        schema_name="fill_structure",
        system=system,
        user=user,
        temperature=0.2,
    )


def fill_phrase_via_llm(*, lang: str, partial: dict) -> dict:
    system = (
        "You complete a phrase entry for language learners. "
        "Return ONLY a JSON object matching the schema. Only fill empty fields. "
        "Do not invent values for fields the user already provided."
    )
    user = (
        f"Language: {lang}\n"
        f"Partial input (already-filled fields are non-null and must not be changed):\n"
        f"{json.dumps(partial, ensure_ascii=False, indent=2)}\n"
        "Fill any null fields. explanation_primary must be English; "
        "explanation_secondary should be Simplified Chinese when present."
    )
    return complete_json(
        schema=FILL_PHRASE_SCHEMA,
        schema_name="fill_phrase",
        system=system,
        user=user,
        temperature=0.2,
    )