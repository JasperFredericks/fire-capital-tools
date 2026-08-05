#!/usr/bin/env python3
"""
One-off migration: relabel Scorecard Pro history rows that were filed under
a report title instead of a property name.

Background. tools/scorecard_pro/parsing.py's cash-flow parser used to take
a file's first header line as the property name. For exports from this
reporting system that line is the report heading -- "Income Statement - 12
Month" -- which is identical for every property it exports. Every such
property therefore collapsed onto one history key, and because
scorecard_history is keyed on (property_key, month), each upload silently
overwrote the previous property's months. The parser now reads the
"Properties:" line instead, which carries the real identity.

The rows already written under the old label are real data and should keep
their trend history rather than being deleted or orphaned, so this renames
the key in place. Nothing but property_key and property_name is touched --
the script proves that by fingerprinting every other column before and
after and refusing to commit if the fingerprint moves.

ORDERING. Run this AFTER the parser fix is deployed, never before. The key
this writes is the key the *fixed* parser produces; if the old code is
still live, the next upload would create the old key again alongside the
new one and split the history in two.

Usage (dry run prints the plan and changes nothing):

    python migrate_scorecard_property_key.py
    python migrate_scorecard_property_key.py --apply

Honours SCORECARD_PRO_DB_PATH, so in production run it with the same
environment as the app (its default fallback is the repo-local file, which
is not what production uses).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

from tools import scorecard_history as sh

OLD_KEY = "income statement - 12 month"
NEW_NAME = "1120 Jackson Street"


def data_fingerprint(conn) -> tuple[int, str]:
    """Hash of every column except the two label columns. If this changes,
    the migration altered financial data and must not be committed."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM scorecard_history ORDER BY property_key, month_start")]
    payload = [
        {k: v for k, v in r.items() if k not in ("property_key", "property_name")}
        for r in rows
    ]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return len(rows), digest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="commit the change (default is a dry run)")
    ap.add_argument("--old-key", default=OLD_KEY)
    ap.add_argument("--new-name", default=NEW_NAME)
    args = ap.parse_args()

    new_key = sh.normalize_property_key(args.new_name)
    print("database :", sh.get_db_path())
    print("old key  :", repr(args.old_key))
    print("new key  :", repr(new_key), "  (name %r)" % args.new_name)
    print()

    with sh.get_connection() as conn:
        before_n, before_fp = data_fingerprint(conn)
        affected = conn.execute(
            "SELECT COUNT(*) FROM scorecard_history WHERE property_key = ?",
            (args.old_key,)).fetchone()[0]
        clash = conn.execute(
            "SELECT COUNT(*) FROM scorecard_history WHERE property_key = ?",
            (new_key,)).fetchone()[0]

        print("total rows        :", before_n)
        print("rows to relabel   :", affected)
        print("rows already under the new key:", clash)
        print("data fingerprint  :", before_fp)

        if affected == 0:
            print("\nNothing to do — no rows carry the old key.")
            return 0

        if clash:
            # Renaming into an occupied (property_key, month) would violate
            # the primary key, or worse, merge two properties' months. Stop.
            print("\nABORT: rows already exist under the new key. Merging them "
                  "could collide on (property_key, month) — resolve by hand.")
            return 1

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
            return 0

        cur = conn.execute(
            "UPDATE scorecard_history SET property_key = ?, property_name = ? WHERE property_key = ?",
            (new_key, args.new_name, args.old_key))
        after_n, after_fp = data_fingerprint(conn)

        if after_n != before_n or after_fp != before_fp:
            conn.rollback()
            print("\nABORT and ROLLED BACK: row count or financial data changed.")
            print("  rows  %s -> %s" % (before_n, after_n))
            print("  fp    %s -> %s" % (before_fp, after_fp))
            return 1

        conn.commit()
        print("\nrows updated      :", cur.rowcount)
        print("row count         : %s -> %s (unchanged)" % (before_n, after_n))
        print("data fingerprint  : unchanged (%s)" % after_fp[:16])
        print("history under new key:", len(sh.get_history(conn, new_key)), "rows")
        print("old key now returns  :", len(sh.get_history(conn, args.old_key)), "rows")
        print("\nDone.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
