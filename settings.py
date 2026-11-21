"""Server-wide settings: the gold bonuses. Kept in state/settings.json, next to
the save. Without the file, the defaults apply. The server re-reads the file whenever
it changes, so `python server/player.py mission-gold ...` works while it runs.

  mission gold   each mission pays its listed <gold> times this. Default 7: every
                 recorded mission paid 7x, under the game's "Sunset Gold Bonus" event
                 (gold_multiplier 7, running 2023-12-01 to 2030-03-19).
  arena gold     each AI rival pays a base of 25 gold (the wiki's figure) times this.
                 Default 3: every recorded AI rival paid 75.

Each one can be switched off, which pays the base (x1) and keeps the multiplier for
when it's switched back on. The multipliers are whole numbers, 1 or more.

When mission gold is off, the login data the game gets leaves out the gold event: its
popup and store banner advertise a bonus that isn't there. Otherwise the game is told
the multiplier in use, so its mission popup shows what a mission pays.

Standard library only.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MISSION_GOLD = 7
DEFAULT_ARENA_GOLD = 3
GOLD_BANNER = "missiongold"          # in the gold event's banner file names (4x_missiongold_...png)


@dataclass
class Bonus:
    multiplier: int
    on: bool = True

    @property
    def effective(self) -> int:
        return self.multiplier if self.on else 1

    def describe(self) -> str:
        return f"x{self.multiplier}" + ("" if self.on else " (off: x1)")


@dataclass
class Settings:
    mission_gold: Bonus = field(default_factory=lambda: Bonus(DEFAULT_MISSION_GOLD))
    arena_gold: Bonus = field(default_factory=lambda: Bonus(DEFAULT_ARENA_GOLD))

    def describe(self) -> str:
        return f"missions {self.mission_gold.describe()}, arena {self.arena_gold.describe()}"


def _bonus(value, default: int) -> Bonus:
    if not isinstance(value, dict):
        return Bonus(default)
    mult = value.get("multiplier", default)
    if not isinstance(mult, int) or isinstance(mult, bool) or mult < 1:
        raise ValueError(f"a gold multiplier must be a whole number, 1 or more (got {mult!r})")
    return Bonus(mult, bool(value.get("on", True)))


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._settings: Settings | None = None
        self._mtime: float | None = None

    def load(self) -> Settings:
        """The settings now: the file's, or the defaults if there's no file."""
        if not self.path.is_file():
            self._settings, self._mtime = None, None
            return Settings()
        mtime = self.path.stat().st_mtime
        if self._settings is None or mtime != self._mtime:
            data = json.loads(self.path.read_text("utf-8"))
            self._settings = Settings(mission_gold=_bonus(data.get("mission_gold"), DEFAULT_MISSION_GOLD),
                                      arena_gold=_bonus(data.get("arena_gold"), DEFAULT_ARENA_GOLD))
            self._mtime = mtime
        return copy.deepcopy(self._settings)

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: {"multiplier": b.multiplier, "on": b.on}
                for name, b in (("mission_gold", settings.mission_gold), ("arena_gold", settings.arena_gold))}
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(data, indent=1), "utf-8")
        os.replace(tmp, self.path)
        self._settings, self._mtime = copy.deepcopy(settings), self.path.stat().st_mtime


def _is_gold_event(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    if "gold_multiplier" in entry:
        return True
    return any(GOLD_BANNER in str(entry.get(k) or "") for k in ("banner_picture", "web_picture", "store_banner"))


def apply_to_login(body: dict, settings: Settings) -> dict:
    """The mission gold setting in a reply's login data, in place: the multiplier the
    game is told, and the gold event left out while the bonus is off."""
    bonus = settings.mission_gold
    mult = body.get("gold_multiplier_missions")
    if isinstance(mult, dict) and "multiplier" in mult:
        mult["multiplier"] = str(bonus.effective)
    if bonus.on:
        for entry in ([body.get("event_popup")] + list(body.get("event_popups") or [])):
            if isinstance(entry, dict) and "gold_multiplier" in entry:
                entry["gold_multiplier"] = str(bonus.multiplier)
    else:
        if isinstance(body.get("event_popups"), list):
            body["event_popups"] = [e for e in body["event_popups"] if not _is_gold_event(e)]
        if _is_gold_event(body.get("event_popup")):
            body["event_popup"] = None
    return body
