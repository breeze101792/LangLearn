"""Wipe structures and phrases from the user's database.

Intended for one-off resets during development. By default wipes every
row from both tables (built-in, llm, and user-added). Vocab, sessions,
and other tables are untouched.

Usage:
    .venv_nixlab/bin/python scripts/wipe_structures_phrases.py [--lang <code>] [--yes]

    --lang <code>   Restrict to a single language; otherwise all langs.
    --yes           Skip the interactive confirmation prompt.

Exits 0 on success. Prints the count of deleted rows per table.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the project root importable when this script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config
from backend.db import get_conn, transaction


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", default=None,
                        help="Restrict the wipe to a single language code "
                             "(e.g. 'en'). Default: all languages.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation prompt.")
    args = parser.parse_args()

    db_path = config.db_path()
    print(f"target DB: {db_path}")
    if args.lang:
        print(f"scope: language='{args.lang}'")
    else:
        print("scope: ALL languages")

    with get_conn() as conn:
        if args.lang:
            n_s = conn.execute(
                "SELECT COUNT(*) FROM structures WHERE language=?", (args.lang,),
            ).fetchone()[0]
            n_p = conn.execute(
                "SELECT COUNT(*) FROM phrases WHERE language=?", (args.lang,),
            ).fetchone()[0]
        else:
            n_s = conn.execute("SELECT COUNT(*) FROM structures").fetchone()[0]
            n_p = conn.execute("SELECT COUNT(*) FROM phrases").fetchone()[0]
    print(f"would delete: {n_s} structures, {n_p} phrases")

    if not args.yes:
        sys.stdout.write("Continue? [y/N] ")
        sys.stdout.flush()
        answer = sys.stdin.readline().strip().lower()
        if answer not in ("y", "yes"):
            print("aborted.")
            return 1

    with transaction() as conn:
        if args.lang:
            cur_s = conn.execute(
                "DELETE FROM structures WHERE language=?", (args.lang,),
            )
            cur_p = conn.execute(
                "DELETE FROM phrases WHERE language=?", (args.lang,),
            )
        else:
            cur_s = conn.execute("DELETE FROM structures")
            cur_p = conn.execute("DELETE FROM phrases")
        if args.lang:
            conn.execute(
                "UPDATE languages SET seeded_at=NULL WHERE code=?",
                (args.lang,),
            )
        else:
            conn.execute("UPDATE languages SET seeded_at=NULL")
    print(f"deleted: {cur_s.rowcount} structures, {cur_p.rowcount} phrases")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
