"""Backfill glossary for vocab rows that have a placeholder.

Walks every vocab row whose glossary is the placeholder set by
scripts/import_wordbank.py (or any other placeholder you want to
backfill) and runs the dictionary chain from the user's settings.
The first non-empty result wins — same logic the /api/dictionary/lookup
endpoint uses. If a chain result is found, the row is updated via
add_vocab, which preserves the existing leitner_box and next_due so
the user's spaced-repetition schedule is not disturbed.

Usage:
    .venv_nixlab/bin/python scripts/backfill_glossary.py

    --placeholder  TEXT  Filter to rows whose glossary starts with this
                         string (default: '(imported from wordbank)').
    --language     CODE  Restrict to a single language (default: all).
    --provider     NAME  Force a specific provider, ignoring the chain.
                         Useful for forcing AI when WordNet misses a word.
    --limit        N     Process at most N rows (default: all).
    --dry-run            Print what would be updated without writing.
    --user-id      N     Override the target user (default: config.DEFAULT_USER_ID).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Make the project root importable when this script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config
from backend.db import get_conn
from backend.services import settings as settings_svc
from backend.services import vocab as vocab_svc
from backend.services.dictionaries import registry as dict_registry
from backend.services.dictionaries.base import WordEntry

# The chain executor reads from a provider registry that is populated
# by app.py. Importing the registry module doesn't register the
# built-in providers on its own — that's done at Flask app startup.
# We mimic that here so the chain works the same way it does for the
# browser session.
dict_registry.bootstrap()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--placeholder",
        default="(imported from wordbank)",
        help="Only process rows whose glossary equals this string "
             "(default: %(default)s). Match is exact.",
    )
    p.add_argument(
        "--language",
        default=None,
        help="Restrict to a single language code (default: every language).",
    )
    p.add_argument(
        "--provider",
        default=None,
        help="Force a specific provider name (e.g. 'wordnet' or 'llm'), "
             "ignoring the user's chain order. Useful for forcing AI "
             "where WordNet misses.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N rows (default: all).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Read the DB and print what would be updated, "
             "without writing anything.",
    )
    p.add_argument(
        "--user-id",
        type=int,
        default=config.DEFAULT_USER_ID,
        help="Target user id (default: %(default)s).",
    )
    return p.parse_args()


def fetch_placeholder_rows(*, user_id: int, placeholder: str,
                           language: str | None, limit: int | None) -> list[dict]:
    """Return rows whose glossary is the placeholder (exact match)."""
    sql = ("SELECT id, language, word, leitner_box FROM vocab_items "
           "WHERE user_id = ? AND glossary = ?")
    params: list = [user_id, placeholder]
    if language is not None:
        sql += " AND language = ?"
        params.append(language)
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def lookup_one(*, word: str, lang: str, settings: dict,
               provider_override: str | None) -> WordEntry:
    """Run the chain (or a forced provider) and return the entry.

    Mirrors the logic in /api/dictionary/lookup, minus the HTTP layers.
    The chain order, explanation languages, and provider override all
    come from the same source the route uses.
    """
    chain = settings.get("dict_chain_json", {}).get(lang, []) if isinstance(
        settings.get("dict_chain_json"), dict) else []
    if provider_override is not None:
        return dict_registry.lookup_with_provider(
            word=word, lang=lang, provider_name=provider_override,
            explanation_primary=settings.get("explanation_primary"),
            explanation_secondary=settings.get("explanation_secondary"),
        ).entry
    return dict_registry.lookup_via_chain(
        word=word, lang=lang, chain=chain,
        explanation_primary=settings.get("explanation_primary"),
        explanation_secondary=settings.get("explanation_secondary"),
    ).entry


def main() -> int:
    args = parse_args()
    print(f"target user_id: {args.user_id}")
    print(f"placeholder filter: {args.placeholder!r}")
    if args.language:
        print(f"language filter: {args.language}")
    if args.provider:
        print(f"forced provider: {args.provider}")
    if args.limit:
        print(f"limit: {args.limit}")

    rows = fetch_placeholder_rows(
        user_id=args.user_id,
        placeholder=args.placeholder,
        language=args.language,
        limit=args.limit,
    )
    if not rows:
        print("no rows to process.")
        return 0
    print(f"rows to process: {len(rows)}")

    settings = settings_svc.get_settings(args.user_id)

    created = 0
    updated = 0
    skipped_empty = 0
    failed = 0
    for row in rows:
        word = row["word"]
        lang = row["language"]
        try:
            entry = lookup_one(
                word=word, lang=lang, settings=settings,
                provider_override=args.provider,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {word}: {e}", file=sys.stderr)
            failed += 1
            continue
        if entry.is_empty:
            skipped_empty += 1
            continue
        if args.dry_run:
            print(f"  would-update {word} -> source={entry.source} "
                  f"len(defs)={len(entry.senses[0].definitions) if entry.senses else 0}")
            continue
        # Drive add_vocab directly so we can distinguish created vs
        # updated. auto_add_from_lookup returns a bool that conflates
        # the two, which is fine for the live /api/dictionary/lookup
        # path, but unhelpful here. add_vocab preserves leitner_box
        # and next_due on update, so the user's spaced-repetition
        # schedule is not disturbed.
        sense = entry.senses[0]
        d = sense.definitions[0]
        try:
            res = vocab_svc.add_vocab(
                user_id=args.user_id,
                language=lang,
                word=word,
                source=entry.source or "llm",
                sense_idx=0,
                pos=sense.pos,
                glossary=d.glossary,
                example=d.example,
                explanation_primary=(sense.explanations or {}).get("primary"),
                explanation_secondary=(sense.explanations or {}).get("secondary"),
                # Carry the existing box through so a fresh `add_vocab`
                # call (where the word already exists) hits the UPDATE
                # branch instead of being treated as a new insert.
                leitner_box=row["leitner_box"],
            )
        except ValueError as e:
            print(f"  FAIL {word}: {e}", file=sys.stderr)
            failed += 1
            continue
        if res.get("created"):
            created += 1
        else:
            updated += 1

    print(f"updated (existing row): {updated}")
    print(f"created (new row): {created}")
    print(f"skipped (no chain result): {skipped_empty}")
    if failed:
        print(f"failed: {failed}")
    if args.dry_run:
        print("dry-run; no rows written.")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
