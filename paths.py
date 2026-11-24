"""Where things are: the folders around the server.

The server folder sits beside game/ (the game's own files, which each player extracts
for themselves: the client, the archived game files, the recorded login) and state/
(the saves and the server's settings). captures/ and reference/ belong to the
workspace around it and are only read by optional checks.

Read these as paths.X when they're needed, not copied at import: the tests point
STATE_DIR at a temporary folder (tests/conftest.py), and a copy wouldn't follow.
"""

from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
ROOT_DIR = SERVER_DIR.parent                                  # holds server/, game/ and state/
GAME_DIR = ROOT_DIR / "game"
FIXTURE_DIR = GAME_DIR / "fixtures"                           # the recorded login and replies
ARCHIVE_DIR = GAME_DIR / "assets"                             # the archived game files, by host
ASSET_DIR = ARCHIVE_DIR / "mobile.tyrantonline.com" / "assets"   # the game data (cards, missions...)
CLIENT_DIR = GAME_DIR / "client" / "tyu-web-2.30.80"         # the web client
STATE_DIR = ROOT_DIR / "state"
WEB_DIR = SERVER_DIR / "web"                                  # what the offline page loads (the shim)
DEBUG_FILE = SERVER_DIR / "debug.toml"                        # the host's debug options, if any (debug.py; gitignored)
CAPTURES_DIR = ROOT_DIR / "captures"                          # recordings (optional checks only):
RAW_CAPTURES_DIR = CAPTURES_DIR / "api"                       # the raw ones (login tokens: in no repository)
SANITIZED_CAPTURES_DIR = CAPTURES_DIR / "api-sanitized"       # every secret replaced
TUO_DIR = ROOT_DIR / "reference" / "tuo"                      # the optimizer's source (optional checks)


def state_dir_for(fixture_dir: Path) -> Path:
    """Where the saves go for a set of fixtures: STATE_DIR for the game's own, else a
    state/ beside the fixtures folder (a test's made-up game)."""
    fixture_dir = Path(fixture_dir)
    return STATE_DIR if fixture_dir.resolve() == FIXTURE_DIR.resolve() else fixture_dir.parent / "state"
