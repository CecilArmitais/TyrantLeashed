"""Turn the official game's login data into a save for this server (login_import.py).

    python server/convert_login.py init.json            # the init reply, as the browser showed it
    python server/convert_login.py session.har          # or a HAR recording with it in
    python server/convert_login.py init.json -o mine.json

The save it writes can be brought in on the sign-in page (a new name), or by a host
with player.py. The account's credentials in the data are dropped first. Standard
library only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import achievements  # noqa: E402
import paths  # noqa: E402
from login_import import login_from_har, player_from_login  # noqa: E402
from player_state import save_json  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("login", help="the init reply (JSON) or a HAR file with it")
    ap.add_argument("-o", "--out", default="tyrant-save.json", help="the save to write (default: tyrant-save.json)")
    args = ap.parse_args()
    try:
        data = json.loads(Path(args.login).read_text("utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"Can't read {args.login}: {e}")
    try:
        body = login_from_har(data) if isinstance(data, dict) and "log" in data else data
        defs = achievements.load(paths.ASSET_DIR) if paths.ASSET_DIR.is_dir() else None
        player = player_from_login(body, int(time.time()), defs)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        sys.exit(f"That isn't the game's login data: {e}")
    Path(args.out).write_text(json.dumps(save_json(player, int(time.time())), indent=1, sort_keys=True), "utf-8")
    won = sum(1 for m in (player.missions or {}).values() if m.get("complete"))
    print(f"Wrote {args.out}: level {(player.arena or {}).get('level', 1)}, {player.gold} gold, "
          f"{player.war_bonds} war bonds, {sum(c['num_owned'] for c in player.cards.values())} cards, "
          f"{won} missions won, {len(player.items)} kinds of items"
          + ("" if defs else " (no game files here, so the objectives start over)"))


if __name__ == "__main__":
    main()
