"""Run the optimizer's own test decks on our engine: a crash test.

    python server/battle_tuo_check.py            # 100 battles per test line
    python server/battle_tuo_check.py -n 20      # quicker

Each line of reference/tuo/tests/*.csv pits two decks against each other. The
optimizer's own suite only checks that these battles run (its win check is
switched off), so a line passes here when every battle finishes without an
error; the first deck's win rate is shown for information. Lines that need
something the engine can't read (a battleground effect, a named mission) are
skipped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from battle.cards import CardDB  # noqa: E402
from battle.tuo_tests import Names, parse_deck, test_lines, win_rate  # noqa: E402
from paths import TUO_DIR  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=100, help="battles per test line")
    args = ap.parse_args()
    if not (TUO_DIR / "tests").is_dir():
        sys.exit(f"The optimizer isn't in {TUO_DIR}.")
    db = CardDB.load(TUO_DIR / "tests" / "sim" / "data")
    names = Names(db)
    passed = run = 0
    for f, you, enemy, bge in test_lines():
        try:
            a, b = parse_deck(you, names), parse_deck(enemy, names)
        except (KeyError, AssertionError) as e:
            print(f"SKIP  {f}: {e}")
            continue
        if bge:
            print(f"SKIP  {f}: battleground effect {bge!r}")
            continue
        run += 1
        try:
            rate = win_rate(a, b, args.n, cards=db)
        except Exception as e:                                   # noqa: BLE001 - this is a crash test
            print(f"CRASH {type(e).__name__}: {e}  {you}  vs  {enemy}")
            continue
        passed += 1
        print(f"OK    first deck wins {rate:4.0%}  {you}  vs  {enemy}")
    print(f"\n{passed} of {run} ran without errors.")
    sys.exit(0 if passed == run else 1)


if __name__ == "__main__":
    main()
