"""Check the battle engine against recorded battles.

    python server/battle_check.py               # the scrubbed recordings in game/fixtures
    python server/battle_check.py --captures    # also every recorded session in captures/api
    python server/battle_check.py -v            # show where each battle first differs

Each recorded battle is played again with the same decks and the same cards played.
A battle "matches" when every turn is identical. Until every skill is built, most
battles differ at their first skill; those are listed as waiting for that skill,
while a difference in a plain attack is a bug.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from battle.cards import CardDB  # noqa: E402
from battle.recorded import compare, from_capture, from_fixtures  # noqa: E402
from game_api import ASSET_DIR, FIXTURE_DIR  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--captures", action="store_true", help="also check every session in captures/api")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    db = CardDB.load(ASSET_DIR)
    sources = [("fixtures", from_fixtures(FIXTURE_DIR / "replay.json"))]
    if args.captures:
        for session in sorted((ROOT / "captures" / "api").iterdir()):
            if session.is_dir():
                sources.append((f"session {session.name}", from_capture(session)))

    waiting, bugs, matched, total = Counter(), [], 0, 0
    for label, battles in sources:
        for i, rec in enumerate(battles):
            total += 1
            r = compare(rec, db)
            where = f"{label} #{i} {rec.name}"
            if r.ok:
                matched += 1
                line = f"MATCH   {where}: all {r.turns} turns"
            elif r.needs_skill:
                waiting[r.needs_skill] += 1
                line = f"WAITING {where}: {r.matched_turns} of {r.turns} turns match, then {r.needs_skill}"
            else:
                bugs.append(where)
                line = f"DIFFERS {where}: {r.matched_turns} of {r.turns} turns match"
            print(line)
            if args.verbose or (not r.ok and not r.needs_skill):
                if r.difference:
                    print(f"        {r.difference}")
    print(f"\n{matched} of {total} battles match completely.")
    if waiting:
        print("Waiting for skills: " + ", ".join(f"{s} ({n})" for s, n in waiting.most_common()))
    if bugs:
        print(f"{len(bugs)} differ in plain attacks (bugs): " + "; ".join(bugs))
        sys.exit(1)


if __name__ == "__main__":
    main()
