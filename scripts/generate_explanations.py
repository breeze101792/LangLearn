"""Generate `explanation` and (for phrases) `example_sentence` values
for the English built-in seed.

Both fields are target-language content. The English built-in seed
needs:

  * structures: pattern + example_sentence + explanation
  * phrases:    phrase + example_sentence + explanation

This script calls the LLM (using `OPENAI_API_KEY` / `OPENAI_BASE_URL` /
`OPENAI_MODEL` from your environment) to generate them in batches, then
patches `backend/data/built-in/english.json`.

Usage:
    .venv_nixlab/bin/python scripts/generate_explanations.py [--lang en] [--yes]

    --lang <code>   Which built-in language to patch. Default: en.
    --yes           Skip the interactive confirmation prompt.

Requires OPENAI_API_KEY to be set (or a self-hosted OpenAI-compatible
endpoint that doesn't require a key, configured via OPENAI_BASE_URL).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the project root importable when this script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL from the project
# .env (if present) so the user can keep their key out of the shell.
# backend.config does this too on import, but doing it here makes
# the script self-contained and obvious.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from backend.services import llm  # noqa: E402
from backend.services.llm import _lang_name  # noqa: E402

SEED_BATCH_SIZE: int = 25


def generate_for_items(lang: str, items: list[dict], kind: str) -> dict:
    """Call the LLM once per batch of items, asking it to fill
    target-language fields. Returns ``{item_key: {field: value}}``.

    For structures: fills ``explanation`` (only).
    For phrases: fills ``example_sentence`` AND ``explanation``.

    Batching is necessary because the LLM response for hundreds of
    rows exceeds the per-request timeout on slow proxies.
    """
    if not items:
        return {}

    if kind == "structure":
        array_name = "structures"
        field_a = "pattern"
        field_b = "example_sentence"
        required = ["_id", "explanation"]
        prompt_intro = (
            "Write a target-language usage note for each structure. "
            "You are given the pattern and an example sentence; do not "
            "change them. The `explanation` must be in the same language "
            "as the pattern."
        )
    else:
        array_name = "phrases"
        field_a = "phrase"
        field_b = "example_sentence"
        required = ["_id", "example_sentence", "explanation"]
        prompt_intro = (
            "For each phrase, write ONE natural example sentence in "
            "the target language showing the phrase used in context, "
            "AND a paragraph-length usage note (also in the target "
            "language) describing when and why to use this phrase, "
            "register, common context, and any alternatives. The "
            "`example_sentence` is a real usage, not a translation. "
            "The `explanation` is 2-4 sentences."
        )

    target = _lang_name(lang)
    user = (
        f"Target language: {target} ({lang}).\n"
        f"{prompt_intro}\n"
        f"Return a JSON object with a top-level `{array_name}` array. "
        f"Do NOT return a bare array.\n"
        f"For each item, return the EXACT same `{field_a}` and "
        f"`{field_b}` you were given (do not rename them), plus the "
        f"new fields. The `_id` you return MUST match the input `_id`.\n"
        f"Items:\n"
        f"{json.dumps([{'_id': i, field_a: it.get(field_a), field_b: it.get(field_b)} for i, it in enumerate(items)], ensure_ascii=False, indent=2)}"
    )
    system = (
        "You write target-language content for language-learning rows. "
        "Return ONLY a JSON object matching the schema. No prose, no code fences."
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [array_name],
        "properties": {
            array_name: {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,  # tolerate renamed fields
                    "required": required,
                    "properties": {
                        "_id": {"type": "integer"},
                        "example_sentence": {"type": ["string", "null"], "maxLength": 1000},
                        "explanation": {"type": ["string", "null"], "maxLength": 1500},
                    },
                },
            },
        },
    }
    out: dict = {}
    # Build an index from each item's identifying field to the
    # original item, so we can match LLM output even when the model
    # returns `_id` indexes that don't line up with the input batch.
    by_key: dict = {it.get(field_a): it for it in items}
    for i in range(0, len(items), SEED_BATCH_SIZE):
        batch = items[i : i + SEED_BATCH_SIZE]
        batch_user = user + f"\n(This batch: items {i}..{i+len(batch)-1}.)"
        print(f"  -> {kind} batch {i}..{i+len(batch)-1} ({len(batch)} items)", flush=True)
        response = llm.complete_json(
            schema=schema,
            schema_name=f"generate_{kind}_fields",
            system=system,
            user=batch_user,
            temperature=0.3,
        )
        # Be tolerant: the model may return either a wrapper object
        # `{structures: [...]}` (per the schema) or a bare array
        # (a common short-form). If it's a list, wrap it.
        if isinstance(response, list):
            response = {array_name: response}
        # Some models echo back the input items unchanged. Build a
        # map of `_id` -> content to recover the field_a key.
        id_to_key: dict[int, str] = {}
        for idx_in_batch, it in enumerate(batch):
            id_to_key[idx_in_batch] = it.get(field_a)
        for entry in response.get(array_name, []):
            # Find the original item: try _id first, then the
            # content field. The model may rename `_id` or reset the
            # counter — content is the reliable signal.
            original_key = None
            entry_id = entry.get("_id")
            if isinstance(entry_id, int) and entry_id in id_to_key:
                original_key = id_to_key[entry_id]
            else:
                content_val = entry.get(field_a)
                if isinstance(content_val, str) and content_val in by_key:
                    original_key = content_val
            if original_key is None or original_key not in by_key:
                continue
            out[original_key] = {
                k: v for k, v in entry.items() if k not in ("_id", field_a)
            }
            # The model may name the explanation field `usage_note`
            # or `note` (more natural names). Fall back to those.
            if not out[original_key].get("explanation"):
                for alt in ("usage_note", "note", "description"):
                    if out[original_key].get(alt):
                        out[original_key]["explanation"] = out[original_key].pop(alt)
                        break
                else:
                    out[original_key].pop("explanation", None)
        print(f"     batch produced {sum(1 for k in out if k in by_key)} unique keys so far", flush=True)
    return out


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", default="en",
                        help="Which built-in language to patch. Default: en.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation prompt.")
    parsed = parser.parse_args(args)

    # Match the production seed loader's filename search order: first
    # the code-named file (e.g. en.json), then the display name
    # (e.g. english.json). The default language is "en" but the only
    # shipped file today is english.json.
    candidates = [
        Path("backend/data/built-in") / f"{parsed.lang}.json",
        Path("backend/data/built-in") / f"{_lang_name(parsed.lang).lower()}.json",
    ]
    seed_path = None
    for p in candidates:
        if p.exists():
            seed_path = p
            break
    if seed_path is None:
        print(f"no built-in seed at "
              f"{candidates[0]} or {candidates[1]}", file=sys.stderr)
        return 1
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    structures = data.get("structures", [])
    phrases = data.get("phrases", [])

    target = _lang_name(parsed.lang)
    print(f"target DB: {seed_path}")
    print(f"target language: {target} ({parsed.lang})")
    print(f"items: {len(structures)} structures, {len(phrases)} phrases")
    print()

    if not parsed.yes:
        sys.stdout.write("Continue? [y/N] ")
        sys.stdout.flush()
        answer = sys.stdin.readline().strip().lower()
        if answer not in ("y", "yes"):
            print("aborted.")
            return 1

    struct_fields = generate_for_items(parsed.lang, structures, "structure")
    phrase_fields = generate_for_items(parsed.lang, phrases, "phrase")

    # Drop legacy fields that are no longer part of the schema.
    for s in structures:
        s.pop("literal_translation", None)  # in case any structure had it
    for p in phrases:
        p.pop("literal_translation", None)

    for s in structures:
        if s.get("pattern") in struct_fields:
            f = struct_fields[s["pattern"]]
            s["explanation"] = f.get("explanation") or f.get("usage_note", "")
    for p in phrases:
        if p.get("phrase") in phrase_fields:
            f = phrase_fields[p["phrase"]]
            p["example_sentence"] = f.get("example_sentence", "")
            p["explanation"] = f.get("explanation") or f.get("usage_note", "")

    seed_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"patched {seed_path}")
    print(f"  structures updated: {len(struct_fields)}")
    print(f"  phrases updated:    {len(phrase_fields)}")
    return 0


def main_with_args(args: list[str]) -> int:
    """Test-friendly entry point: run with explicit argv."""
    return main(args)


if __name__ == "__main__":
    sys.exit(main())
