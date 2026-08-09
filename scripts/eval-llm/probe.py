"""Probe an LLM against the project's real JSON schemas.

Reuses `backend.services.llm` (schemas, validator, timeout config) so the
probe exercises the same path the app uses, just pointed at a different
model. Always uses `OPENAI_BASE_URL` from the environment.

Usage:
  # default: model from $OPENAI_MODEL
  .venv_nixlab/bin/python scripts/eval-llm/probe.py

  # explicit model
  .venv_nixlab/bin/python scripts/eval-llm/probe.py --model gemma4:e2b

  # custom timeout (seconds) for slow models
  .venv_nixlab/bin/python scripts/eval-llm/probe.py --model gemma4:e2b --timeout 90

Results land in scripts/eval-llm/runs/<model-slug>/<timestamp>/report.json
plus a per-run markdown summary at summary.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

import requests  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402

from backend.services import llm as llm_mod  # noqa: E402


# ---- CLI ---------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default=os.environ.get("OPENAI_MODEL"),
                   help="Model id to test (default: $OPENAI_MODEL)")
    p.add_argument("--timeout", type=int, default=60,
                   help="HTTP timeout in seconds (default: 60)")
    p.add_argument("--runs-dir", default=None,
                   help="Override the runs/ output directory")
    return p.parse_args()


# ---- HTTP --------------------------------------------------------------


def _endpoint() -> str:
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    if not base:
        sys.exit("OPENAI_BASE_URL is not set")
    return base + "/chat/completions"


def raw_chat(*, system: str, user: str, schema: dict, schema_name: str,
             model: str, timeout: int, temperature: float = 0.2
             ) -> tuple[str, float, dict | None]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    }
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    t0 = time.perf_counter()
    r = requests.post(_endpoint(), json=payload, headers=headers, timeout=timeout)
    dt_s = time.perf_counter() - t0
    r.raise_for_status()
    body = r.json()
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content, dt_s, body.get("usage")


# ---- Graders -----------------------------------------------------------


def _validate(schema: dict, data: dict) -> list[str]:
    return [e.message for e in Draft202012Validator(schema).iter_errors(data)]


def grade_dict_word(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except Exception as e:
        return {"schema_ok": False, "parse_error": str(e)}
    errs = _validate(llm_mod.DICT_WORD_SCHEMA, data)
    if errs:
        return {"schema_ok": False, "schema_errors": errs}
    notes = [f"senses={len(data['senses'])}"]
    for i, s in enumerate(data["senses"]):
        defs = s.get("definitions", [])
        notes.append(f"  sense[{i}] pos={s.get('pos')!r} defs={len(defs)}")
    return {"schema_ok": True, "notes": notes, "data": data}


def grade_seed(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except Exception as e:
        return {"schema_ok": False, "parse_error": str(e)}
    errs = _validate(llm_mod.SEED_SCHEMA, data)
    if errs:
        return {"schema_ok": False, "schema_errors": errs}
    return {
        "schema_ok": True,
        "structures": len(data.get("structures", [])),
        "phrases": len(data.get("phrases", [])),
        "data": data,
    }


def grade_fill_structure(raw: str, partial: dict) -> dict:
    try:
        data = json.loads(raw)
    except Exception as e:
        return {"schema_ok": False, "parse_error": str(e)}
    errs = _validate(llm_mod.FILL_STRUCTURE_SCHEMA, data)
    if errs:
        return {"schema_ok": False, "schema_errors": errs}
    violations = []
    for k, v in partial.items():
        if v is not None and data.get(k) != v:
            violations.append(f"{k}: expected {v!r}, got {data.get(k)!r}")
    return {
        "schema_ok": True,
        "preserves_given_fields": not violations,
        "violations": violations,
        "filled": {k: data.get(k) for k in partial},
    }


# ---- Cases -------------------------------------------------------------


DICT_WORD_SYSTEM = (
    "You are a bilingual dictionary. Return ONLY a JSON object matching the "
    "provided schema. Do not include prose, code fences, or commentary. "
    "Provide concise, accurate glosses and one natural example per sense."
)


def dict_word_prompt(word: str, lang: str, primary: str = "English",
                     secondary: str | None = None) -> str:
    return (
        f"Language: {lang}\nWord: {word}\n"
        f"Primary explanation language: {primary}\n"
        f"Secondary explanation language (optional): {secondary or '(none)'}\n"
        "Provide 1-3 senses. Each sense has part-of-speech, definitions, "
        "and explanations in the requested languages."
    )


SEED_SYSTEM = (
    "You generate concise language-learning content. Return ONLY a JSON "
    "object matching the schema. No prose, no code fences."
)


def seed_prompt(lang: str, n_structures: int, n_phrases: int) -> str:
    return (
        f"Generate a starter set for learners of {lang}.\n"
        f"- {n_structures} common sentence structures (clause patterns with examples).\n"
        f"- {n_phrases} common phrases or idioms with literal translation and explanation.\n"
        "Each item must include explanation_primary in English and "
        "explanation_secondary in Simplified Chinese."
    )


FILL_SYSTEM = (
    "You complete a sentence-structure entry for language learners. "
    "Return ONLY a JSON object matching the schema. Only fill empty fields. "
    "Do not invent values for fields the user already provided."
)


def fill_prompt(lang: str, partial: dict) -> str:
    return (
        f"Language: {lang}\n"
        "Partial input (already-filled fields are non-null and must not be changed):\n"
        + json.dumps(partial, ensure_ascii=False, indent=2) +
        "\nFill any null fields. explanation_primary must be English; "
        "explanation_secondary should be Simplified Chinese when present."
    )


# ---- Runner ------------------------------------------------------------


def run(*, model: str, timeout: int, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []

    cases: list[tuple[str, str, dict]] = [
        ("dict_word bank (en)",
         DICT_WORD_SYSTEM,
         {"user": dict_word_prompt("bank", "en"),
          "schema": llm_mod.DICT_WORD_SCHEMA,
          "schema_name": "dict_word",
          "temperature": 0.2,
          "grader": lambda raw: grade_dict_word(raw)}),
        ("dict_word set (en)",
         DICT_WORD_SYSTEM,
         {"user": dict_word_prompt("set", "en"),
          "schema": llm_mod.DICT_WORD_SCHEMA,
          "schema_name": "dict_word",
          "temperature": 0.2,
          "grader": lambda raw: grade_dict_word(raw)}),
        ("dict_word desafortunadamente (es)",
         DICT_WORD_SYSTEM,
         {"user": dict_word_prompt("desafortunadamente", "es"),
          "schema": llm_mod.DICT_WORD_SCHEMA,
          "schema_name": "dict_word",
          "temperature": 0.2,
          "grader": lambda raw: grade_dict_word(raw)}),
        ("seed 3 structures + 5 phrases (es)",
         SEED_SYSTEM,
         {"user": seed_prompt("Spanish", 3, 5),
          "schema": llm_mod.SEED_SCHEMA,
          "schema_name": "seed",
          "temperature": 0.3,
          "grader": lambda raw: grade_seed(raw)}),
    ]

    fill_partial = {
        "pattern": "be used to doing",
        "example_sentence": None,
        "explanation_primary": "be accustomed to doing something",
        "explanation_secondary": None,
    }
    cases.append((
        "fill_structure preserves given fields (en)",
        FILL_SYSTEM,
        {"user": fill_prompt("en", fill_partial),
         "schema": llm_mod.FILL_STRUCTURE_SCHEMA,
         "schema_name": "fill_structure",
         "temperature": 0.2,
         "grader": lambda raw: grade_fill_structure(raw, fill_partial)},
    ))

    for label, system, kwargs in cases:
        print(f"\n== {label} ==")
        grader = kwargs.pop("grader")
        try:
            raw, latency, usage = raw_chat(
                system=system, model=model, timeout=timeout, **kwargs)
        except requests.Timeout:
            print(f"  TIMEOUT after {timeout}s")
            report.append({"case": label, "latency_s": timeout, "error": "timeout"})
            continue
        except Exception as e:
            print(f"  ERROR: {e}")
            report.append({"case": label, "error": str(e)})
            continue
        grade = grader(raw)
        ok = grade.get("schema_ok")
        print(f"  latency={latency:.2f}s  schema_ok={ok}  usage={usage}")
        if not ok:
            print(f"  raw[:300]={raw[:300]!r}")
        report.append({
            "case": label,
            "latency_s": latency,
            "usage": usage,
            "grade": grade,
        })

    return report


def _is_case_passing(r: dict) -> bool:
    """A case passes if it produced a grade, schema-validated, and (for
    fill_* cases) preserved the user-provided fields."""
    g = r.get("grade") or {}
    if not g.get("schema_ok"):
        return False
    if g.get("preserves_given_fields") is False:
        return False
    return True


def write_summary(out_dir: Path, model: str, report: list[dict]) -> None:
    lines = [f"# {model}", "",
             f"_Run: {dt.datetime.now().isoformat(timespec='seconds')}_", ""]
    ok = sum(1 for r in report if _is_case_passing(r))
    total = len(report)
    lines += ["## Summary", "",
              f"- **{ok}/{total} cases passing** (schema-valid AND data-preserving)",
              ""]

    lines += ["## Cases", "", "| Case | Latency | Schema-valid | Notes |",
              "|---|---|---|---|"]
    for r in report:
        if "error" in r:
            lines.append(f"| {r['case']} | - | ERROR | `{r['error']}` |")
            continue
        g = r.get("grade") or {}
        if not g:
            lines.append(f"| {r['case']} | - | - | no grade |")
            continue
        notes = ""
        if g.get("schema_ok"):
            if "preserves_given_fields" in g:
                if g["preserves_given_fields"]:
                    notes = "preserves=True"
                else:
                    notes = "DATA LOSS: " + "; ".join(g["violations"])
            elif "structures" in g:
                notes = f"struct={g['structures']} phrases={g['phrases']}"
            else:
                notes = "; ".join(g.get("notes", []))
        else:
            errs = g.get("schema_errors") or [g.get("parse_error", "")]
            notes = "; ".join(errs)[:120]
        schema_ok = g.get("schema_ok")
        if g.get("preserves_given_fields") is False:
            schema_ok = False
        lines.append(
            f"| {r['case']} | {r['latency_s']:.2f}s | "
            f"{'yes' if schema_ok else 'no'} | {notes} |"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"\nSummary: {out_dir / 'summary.md'}")


def slugify(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower() or "model"


def main() -> int:
    args = parse_args()
    if not args.model:
        sys.exit("--model (or $OPENAI_MODEL) is required")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    runs_root = Path(args.runs_dir) if args.runs_dir \
        else Path(__file__).resolve().parent / "runs" / slugify(args.model) / stamp
    runs_root.mkdir(parents=True, exist_ok=True)
    print(f"Model: {args.model}")
    print(f"Endpoint: {os.environ.get('OPENAI_BASE_URL')}")
    print(f"Timeout: {args.timeout}s")
    print(f"Out: {runs_root}")
    os.environ["OPENAI_MODEL"] = args.model

    report = run(model=args.model, timeout=args.timeout, out_dir=runs_root)
    (runs_root / "report.json").write_text(
        json.dumps({
            "model": args.model,
            "endpoint": os.environ.get("OPENAI_BASE_URL"),
            "timeout_s": args.timeout,
            "run_at": dt.datetime.now().isoformat(timespec="seconds"),
            "cases": report,
        }, ensure_ascii=False, indent=2),
    )
    write_summary(runs_root, args.model, report)
    ok = sum(1 for r in report if _is_case_passing(r))
    print(f"\n{ok}/{len(report)} cases passing")
    return 0 if ok == len(report) else 1


if __name__ == "__main__":
    sys.exit(main())
