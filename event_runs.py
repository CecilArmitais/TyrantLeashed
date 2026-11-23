"""What the timed events share: brawls (brawls.py), conquest (conquest.py) and guild wars
(wars.py). Each keeps its own state file and rules; this is the part that's the same.

- **The state file** (StateStore): each event's own, in state/, written whole.
- **A run** is one event going on, in its state's "run": {"event", "start", "end",
  "decay", ...}. From the start to the end the event is on (fights); after the end, up
  to the decay, it's over and its rewards can be claimed, from a wait after the end
  (the client's claim_reward_prevention, which differs by event).
- **Reward tiers**, from the event files: a tier's rank must be reached (this rank or
  better), and where the tier sets min_points, so must the points. A player gets every
  tier they reach.
- **Battleground effects by day:** an event file's battleground_config gives effects
  from a time; each day of a run has the last one whose day has come.
- **Opponents** (pick_nearest): one of the few closest in points.
- **Battle energy**, the client's own formula (EventEnergyBar.Refresh): one more every
  so often from the last update, not before the start and not after the recharge stops
  (some hours before the end), up to the most.

Raids (raids.py) aren't here: a raid's run belongs to a guild and has boss levels, its
tiers are by damage and level, and its energy starts lower and refills to the end.

Standard library only."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import jsonfile

REWARD_KINDS = ("card", "mini_pack", "pack", "item")
DAY = 86400


# ---- the state file ------------------------------------------------------------------------------

class StateStore:
    """An event's state file (state/brawls.json, ...), written whole (jsonfile.py). A load is
    the event's new_state() with what the file has on top."""

    def __init__(self, path: Path, new_state):
        self.path = Path(path)
        self.new_state = new_state

    def load(self) -> dict:
        return {**self.new_state(), **jsonfile.read(self.path, {})}

    def save(self, state: dict) -> None:
        jsonfile.write(self.path, state)


# ---- runs ----------------------------------------------------------------------------------------

def new_run(event_id: int, now: int, end: int, claim_days: int, **extra) -> dict:
    """A run of an event from now to `end`, its rewards claimable up to `claim_days` after
    the end (the decay); `extra`: the event's own fields."""
    return {"event": event_id, "start": now, "end": end, "decay": end + claim_days * DAY, **extra}


def phase(state: dict, now: int, active: str) -> str | None:
    """`active` while the run is on, "claim" after its end up to its decay, else None."""
    run = state.get("run")
    if not run:
        return None
    if run["start"] <= now <= run["end"]:
        return active
    if run["end"] < now <= run["decay"]:
        return "claim"
    return None


def claimable(state: dict, now: int, wait: int) -> bool:
    """Whether rewards can be claimed now: `wait` after the run's end, up to its decay."""
    run = state.get("run")
    return bool(run) and run["end"] + wait <= now <= run["decay"]


# ---- opponents -----------------------------------------------------------------------------------

def pick_nearest(candidates: list[tuple[str, int]], my_points: int, rng: random.Random, nearest: int,
                 order=str) -> str | None:
    """One of the `nearest` candidates (id, points) closest to my points; ties in distance
    go by `order` of the id."""
    if not candidates:
        return None
    close = sorted(candidates, key=lambda c: (abs(c[1] - my_points), order(c[0])))[:nearest]
    return rng.choice(sorted(c[0] for c in close))


# ---- reward tiers --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Tier:
    id: int
    rank: int                       # the rank must be this or better
    min_points: int | None          # the points must be at least this (None: the tier sets none)
    rewards: tuple[tuple[str, int, int], ...]     # (card / mini_pack / pack / item, id, amount)


def _number(element, tag: str, default):
    text = (element.findtext(tag) or "").strip()
    return int(text) if text else default


def tiers_of(rewards, min_points_default: int | None = None) -> list[Tier]:
    """The tiers in an event's <rewards> element (None: none), in the file's order. A tier
    without min_points gets `min_points_default`."""
    out = []
    for t in (rewards if rewards is not None else []):
        got = tuple((kind, int(e.get("id")), int(e.get("amount") or 1)) for kind in REWARD_KINDS for e in t.findall(kind))
        out.append(Tier(int(t.findtext("id")), _number(t, "rank", 1), _number(t, "min_points", min_points_default), got))
    return out


def earned_tiers(tiers, rank: int, points: int) -> list[Tier]:
    """The tiers a rank (0: none) and points reach."""
    if rank < 1:
        return []
    return [t for t in tiers if rank <= t.rank and (t.min_points is None or points >= t.min_points)]


def tier_json(tier: Tier, mini_packs: dict) -> dict:
    """A reward tier as the recorded active_brawl_data and active_war_event had it: every
    value a string, a mini pack with its name, text and picture."""
    out = {"id": str(tier.id), "rank": str(tier.rank)}
    if tier.min_points is not None:
        out["min_points"] = str(tier.min_points)
    for kind, rid, amount in tier.rewards:
        entry = {"id": str(rid), "amount": str(amount)}
        if kind == "mini_pack":
            mp = mini_packs.get(str(rid)) or {}
            entry.update({"name": mp.get("name", ""), "desc": mp.get("desc", ""),
                          "web_picture": mp.get("web_picture") or mp.get("picture", "")})
        out[kind] = entry
    return out


# ---- battleground effects by day -----------------------------------------------------------------

def effects_of(config, start: int) -> tuple[tuple[int, int], ...]:
    """(day of the event from 0, effect id) from a <battleground_config> element (None:
    none), by day. An effect without a start time is the first day's."""
    effects = []
    for e in (config if config is not None else []):
        effects.append((max(0, (int(e.get("start_time") or start) - start) // DAY), int(e.get("id"))))
    return tuple(sorted(effects))


def effect_on(effects, run: dict, now: int) -> int | None:
    """The battleground effect today: the last one whose day has come."""
    day = max(0, (now - run["start"]) // DAY)
    today = [e for d, e in effects if d <= day]
    return today[-1] if today else None


# ---- battle energy -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Energy:
    """An event's battle energy. A player's row keeps "energy" and "energy_updated"."""
    most: int
    every: int                      # seconds for one more
    stops: int                      # the recharge stops this long before the run's end
    cost: int = 1                   # a fight's

    def recharge_stop(self, run: dict) -> int:
        return run["end"] - self.stops

    def current(self, run: dict, row: dict | None, now: int) -> int:
        """The energy now (a player without a row: full)."""
        if row is None:
            return self.most
        gained = max(0, (min(now, self.recharge_stop(run)) - max(row["energy_updated"], run["start"])) // self.every)
        return min(self.most, row["energy"] + gained)

    def spend(self, run: dict, row: dict, now: int) -> None:
        """Brings the row's energy up to now (keeping the part-way progress), then spends
        one fight's."""
        since = max(row["energy_updated"], run["start"])
        gained = max(0, (min(now, self.recharge_stop(run)) - since) // self.every)
        if row["energy"] + gained >= self.most:
            row["energy"], row["energy_updated"] = self.most, now
        else:
            row["energy"] += gained
            row["energy_updated"] = since + gained * self.every
        row["energy"] -= self.cost

    def recharging(self, run: dict, row: dict | None, now: int) -> bool:
        """Whether the energy is coming back now (below the most, before the stop)."""
        return self.current(run, row, now) < self.most and run["start"] <= now < self.recharge_stop(run)
