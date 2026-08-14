"""Import vocab from the old wordbank.db (a SQLite file from another
project) into the user's LangLearn database.

The old schema is just a single WORD table:

    CREATE TABLE WORD (
        word        TEXT,
        times       REAL,
        familiar    REAL,   -- 0..3, where 0 is a 'trash bin' the user
                           -- does not want to bring over
        create_time TEXT,
        timestamp   REAL,
        forgotten   REAL
    );

We bring across only `word` and `familiar`. `familiar` is mapped to the
project's Leitner box (1..5) by adding 1, so:

    old familiar 0 → skip (trash in the old system)
    old familiar 1 → project box 2
    old familiar 2 → project box 3
    old familiar 3 → project box 4

All words are tagged language='en' (the old DB did not carry language).
The project's `glossary` column is NOT NULL, so a placeholder is used;
the user can re-look up any word in the dictionary to populate the
real glossary. The `next_due` date is derived from the box by the
existing add_vocab service so the words are scheduled for review
correctly.

This is a one-shot importer. Re-running it is safe: existing rows
match on (user_id, language, word) and the source field is updated,
but no duplicate row is created.

Usage:
    .venv_nixlab/bin/python scripts/import_wordbank.py [SOURCE_DB]

    SOURCE_DB defaults to /home/shaowu/wordbank.db.

    --dry-run     Print what would be imported without writing.
    --user-id N   Override the target user (default: config.DEFAULT_USER_ID).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Make the project root importable when this script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config
from backend.services import vocab as vocab_service


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "source",
        nargs="?",
        default="/home/shaowu/wordbank.db",
        help="Path to the old wordbank SQLite file (default: %(default)s).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Read the source DB and print what would be imported, "
             "without writing anything to the project's DB.",
    )
    p.add_argument(
        "--user-id",
        type=int,
        default=config.DEFAULT_USER_ID,
        help="Target user id (default: %(default)s).",
    )
    p.add_argument(
        "--language",
        default="en",
        help="Language code to tag every imported word with "
             "(default: %(default)s).",
    )
    return p.parse_args()


def read_source(src_path: Path) -> list[tuple[str, int]]:
    """Return a list of (word, familiar) tuples from the source DB,
    skipping familiar <= 0 (the old trash bin)."""
    if not src_path.exists():
        raise FileNotFoundError(f"source DB not found: {src_path}")
    conn = sqlite3.connect(str(src_path))
    try:
        rows = conn.execute(
            "SELECT word, familiar FROM WORD WHERE familiar > 0"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for word, familiar in rows:
        if not word or not str(word).strip():
            continue
        out.append((str(word).strip(), int(familiar)))
    return out


def main() -> int:
    args = parse_args()
    src_path = Path(args.source)
    print(f"source DB: {src_path}")
    print(f"target user_id: {args.user_id}")
    print(f"target language: {args.language}")

    rows = read_source(src_path)
    if not rows:
        print("source DB has no rows with familiar > 0; nothing to import.")
        return 0

    # Tally by source-side familiar for the summary.
    by_familiar: dict[int, int] = {}
    for _, familiar in rows:
        by_familiar[familiar] = by_familiar.get(familiar, 0) + 1
    print(f"source rows to import: {len(rows)}")
    for f in sorted(by_familiar):
        print(f"  familiar={f}: {by_familiar[f]} words -> box {f + 1}")

    if args.dry_run:
        print("dry-run; no rows written.")
        return 0

    created = 0
    updated = 0
    skipped = 0
    for word, familiar in rows:
        # Map old familiar (1..3) -> project box (2..4). The vocab
        # service clamps to MIN_BOX..MAX_BOX either way, so passing
        # the target box here just records the user's existing
        # progress into the new system.
        try:
            result = vocab_service.add_vocab(
                user_id=args.user_id,
                language=args.language,
                word=word,
                source="user",
                pos=None,
                glossary="(imported from wordbank)",
                example=None,
                explanation_primary=None,
                explanation_secondary=None,
                leitner_box=familiar + 1,
            )
        except ValueError as e:
            print(f"  skip {word!r}: {e}", file=sys.stderr)
            skipped += 1
            continue
        if result.get("created"):
            created += 1
        else:
            updated += 1

    print(f"created: {created}")
    print(f"updated (already present): {updated}")
    if skipped:
        print(f"skipped: {skipped}")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
