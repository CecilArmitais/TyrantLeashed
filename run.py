"""Run Tyrant Unleashed offline: start the server, then open the printed address.

    python server/run.py                 # http://127.0.0.1:8770/
    python server/run.py --open          # also open it in the default browser
    python server/run.py --quiet         # don't log every file served (problems still show)
    python server/run.py --engine        # missions are real battles from the battle engine
    python server/run.py --host 0.0.0.0  # let other machines connect (your network's
                                         # address for this machine, same port)
    python server/run.py --single-player # every browser is the one offline player, as
                                         # before accounts (state/player.json)

Several players can play at once: each signs in by name on the server's page (and a
password, if content.toml's [accounts] asks for one), then plays their own account and
save; a new name makes a new player.

Stop with Ctrl+C; a summary lists anything that was missing, blocked or unsupported.
Nothing here contacts Kongregate or Synapse: the server only reads local files.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from game_api import GameApi  # noqa: E402
from offline_server import OfflineServer  # noqa: E402
from paths import ARCHIVE_DIR, CLIENT_DIR  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1",
                    help="the address to listen on (0.0.0.0: every network this machine is on)")
    ap.add_argument("--single-player", action="store_true",
                    help="every browser plays the one offline player (state/player.json)")
    ap.add_argument("--open", action="store_true", help="open the game in the default browser")
    ap.add_argument("--quiet", action="store_true", help="only log problems, not every file served")
    ap.add_argument("--engine", action="store_true",
                    help="missions are real battles from the battle engine instead of replayed recordings")
    args = ap.parse_args()

    for need, what in ((CLIENT_DIR / "index.html", "client"), (ARCHIVE_DIR, "archive")):
        if not need.exists():
            sys.exit(f"Missing {what}: {need}")

    sys.stdout.reconfigure(line_buffering=True)
    api = GameApi(battle_engine=args.engine, single_player=args.single_player)
    server = OfflineServer((args.host, args.port), quiet_static=args.quiet, api=api)
    print(f"Tyrant Unleashed offline server: {server.url}")
    if args.host in ("0.0.0.0", "::", ""):
        print(f"  other machines: http://<this machine's address>:{args.port}/ (each browser is its own player)")
    if server.api.missing_fixtures:
        print(f"  NOTE: no fixtures for {', '.join(server.api.missing_fixtures)}; those calls will be unsupported.")
        print("        Build them from a recorded session with: python server/make_fixtures.py")
    else:
        who = "the offline player" if api.single_player else "each player"
        print(f"  replaying {', '.join(server.api.fixtures)} for {who} (from game/fixtures)")
        report = server.api.startup_report()
        for line in (report or "").splitlines():
            print(f"  {line}")
        if args.engine:
            print("  battles: the battle engine (missions and the arena)")
    print(f"  client  {CLIENT_DIR}\n  archive {ARCHIVE_DIR}")
    print("Ctrl+C to stop.")
    if args.open:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("\n" + server.summary())


if __name__ == "__main__":
    main()
