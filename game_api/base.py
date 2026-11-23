"""What the API's parts share: the offline player, defaults, sessions, refusals, and small helpers
for the game files (campaign costs, tutorial names, flags)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from accounts import NAME_LENGTH  # noqa: F401  (a name's limit, ours: shared with the usernames)

DEFAULT_STAMINA_REFILL_COST = 30    # wb_stamina_cost in the recorded init
DEFAULT_REFRESH_COST = 50           # gold_rivals_cost in the recorded init


# An arena fight's battle fields, as recorded (no battleground effect).
ARENA_FIELDS = {"kv_store": [], "effect_id": 0, "battleground_effect": {"card_uid": 0},
                "battleground_effects": {"global": [{"card_uid": 0}], "attacker": [], "defender": []}}
DEFAULT_REFILL_COST = 30            # wb_energy_cost in the recorded init
INVALID_FLAG = "Invalid flag."      # the real server's words (recorded for tutorial=5)

# The one offline player. make_fixtures.py writes these in place of the recorded
# account's identifiers, so the fixtures and the server agree on who the player is.
OFFLINE_USER_ID = 1000001
OFFLINE_PASSWORD = "0ff11e0000000000000000000000c0de"
OFFLINE_SYNCODE = "0ff11e" + "0" * 54 + "c0de"   # 64 characters, like the real ones
OFFLINE_NAME = "Offline Commander"


DEFAULT_ENVELOPE = {"version": "80.560", "guild_donations_enabled": 1, "guild_supplies_enabled": 1}


class Session:
    """What a player's client is in the middle of: the battle being played, how far the
    replayed recordings have got, and the last raid fight (for a forfeit during its
    replay). A new login starts it over."""

    def __init__(self):
        self.cursors: dict[tuple, int] = defaultdict(int)
        self.battle_cursor: dict[tuple, int] = defaultdict(int)
        self.battle: dict | None = None
        self.last_raid_reply: dict | None = None


NOT_LOGGED_IN = "Your login wasn't recognized on this server. Reload the game to log in again."
NOT_SIGNED_IN = "Sign in on the server's page first: reload the game."


class NotSupported(Exception):
    """Raised with a reason the player can read; it ends up in the game's dialog."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def flag_value(raw):
    """A setUserFlag value the way the server keeps it: lists (guides_complete
    "[102,101]") as real lists, everything else as a string, like the recorded replies."""
    if isinstance(raw, str) and raw[:1] in "[{":
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return str(raw)


BASIC_MISSIONS, FULL_MISSIONS = "missions_basic.xml", "missions.xml"
BASIC_LAST = "56"   # the basic campaign's last mission (location 4); the full file's 57 is at location 5


def mission_costs(asset_dir: Path, file_name: str | None) -> dict[str, int]:
    """Mission id -> energy cost, from the mission file the game loads (`mission_file`,
    missions_basic.xml). The recorded charges for missions 1-7 match it exactly. ({} if
    the file isn't there.)"""
    if not file_name:
        return {}
    f = Path(asset_dir) / file_name
    if not f.is_file():
        return {}
    out = {}
    for m in ET.parse(f).getroot().findall("mission"):
        mid, energy = (m.findtext("id") or "").strip(), (m.findtext("energy") or "").strip()
        if mid and energy.isdigit():
            out[mid] = int(energy)
    return out


def tutorial_names(asset_dir: Path, file_name: str | None) -> list[str]:
    """The tutorial file's <tutorial> names in order. The tutorial flag is the index of
    the current one, then -1 once all are done. [] if the file isn't there."""
    if not file_name:
        return []
    f = Path(asset_dir) / file_name
    if not f.is_file():
        return []
    return [t.findtext("name") or "?" for t in ET.parse(f).getroot().findall("tutorial")]
