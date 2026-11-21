"""Run Tyrant Unleashed offline: start the local server, then open the printed address.

    python server/run.py                 # http://127.0.0.1:8770/
    python server/run.py --open          # also open it in the default browser
    python server/run.py --quiet         # don't log every file served (problems still show)
    python server/run.py --engine        # missions are real battles from the battle engine
                                         # (in progress: all skills but Mimic)

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
from offline_server import ARCHIVE_DIR, CLIENT_DIR, OfflineServer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--open", action="store_true", help="open the game in the default browser")
    ap.add_argument("--quiet", action="store_true", help="only log problems, not every file served")
    ap.add_argument("--engine", action="store_true",
                    help="missions are real battles from the battle engine instead of replayed recordings")
    args = ap.parse_args()

    for need, what in ((CLIENT_DIR / "index.html", "client"), (ARCHIVE_DIR, "archive")):
        if not need.exists():
            sys.exit(f"Missing {what}: {need}")

    sys.stdout.reconfigure(line_buffering=True)
    server = OfflineServer(("127.0.0.1", args.port), quiet_static=args.quiet,
                           api=GameApi(battle_engine=args.engine))
    print(f"Tyrant Unleashed offline server: {server.url}")
    if server.api.missing_fixtures:
        print(f"  NOTE: no fixtures for {', '.join(server.api.missing_fixtures)}; those calls will be unsupported.")
        print("        Build them from a recorded session with: python server/make_fixtures.py")
    else:
        print(f"  replaying {', '.join(server.api.fixtures)} as the offline player (from game/fixtures)")
        report = server.api.startup_report()
        for line in (report or "").splitlines():
            print(f"  {line}")
        if args.engine:
            print("  battles: the battle engine (missions and the arena; every skill but Mimic)")
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
