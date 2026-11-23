"""Conquest: a guild event on a map of zones. Members fight in a zone to win their guild
influence there; every four hours each zone ranks the guilds by influence and pays
conquest points (and access to the zone's fortress) by rank; at the end, members claim
the reward tiers their guild's conquest rank and their own influence reach.
docs/protocol.md, "Conquest", has the evidence.

The state is the server's (state/conquest.json): zones rank every guild.

From the client's code: the calls, `conquest_data` (the event, the guild's points and rank,
the player's influence, claim and energy, the zones with their effect, marker, the
guild's rank and influence and their reward tiers), the boards (which need at least two
rows), that a fight is one reply showing `rewards[0].conquest_influence`, and the map's
and popup's phases. Nothing was recorded but an empty `conquest_data` (no conquest ran).

The data (conquest.xml): 35 events (names, times, the map's bundle and prefab, reward
tiers by rank and influence) and 22 zones, 12 of them live (picture, battleground effect,
tiers of conquest points and fortress levels by rank).

The fan wiki (Conquest page, read 2026-11-21): 3-day weekend events; a win in a zone
earns the guild 50 influence there, a loss 5; fights are in surge mode, against guilds of
a similar rank; 25 attacks, one back every 30 minutes, stopping four hours before the end;
every four hours each zone ranks guilds by influence and pays conquest points and
fortress access by its tiers, the top tier reached only (not cumulative); a guild deploys
up to five fortresses, from the zones it ranks best in, attacking and defending; officers
and leaders set markers; rewards need the guild's conquest-point rank and the member's
own influence. Influence "decays" after each ranking, by an amount nobody recorded: here
it doesn't decay at all (an unknown).

Ours: the opponent (a random member, with a deck to defend with, of one of the NEAREST
guilds by conquest points), a guild needed to fight, no gold, the zone's rank shown live
(points are paid at the rounds), the claim window (the client's default 600 s after the
end) and three days to claim.
"""

from __future__ import annotations

import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import event_runs
import jsonfile

# The wiki.
WIN_INFLUENCE, LOSS_INFLUENCE = 50, 5
ENERGY_MAX = 25
ENERGY_EVERY = 1800
RECHARGE_STOPS = 4 * 3600          # before the end
ROUND_EVERY = 4 * 3600             # scoring rounds
FORTRESSES = 5                     # deployed, from the best-ranked zones
# The client.
CLAIM_WAIT = 600                   # claim_reward_prevention's default
# Ours.
DECAY_DAYS = 3
ENERGY_COST = 1
NEAREST = 3                        # opponents come from this many guilds closest in points
BOARD_SIZE = 100
MARKERS = (0, 1, 2, 3, 4)          # none, attack, defend, avoid, skull


class ConquestError(Exception):
    """Why something can't be done, in words the player can read."""


EventTier = event_runs.Tier        # min_points: the member's influence (0 when the file sets none)


@dataclass(frozen=True)
class ZoneTier:
    min_rank: int
    conquest_points: int | None = None
    fortress_card: int | None = None


@dataclass(frozen=True)
class Zone:
    id: int
    name: str
    picture: str = ""
    asset_bundle: int = 0
    order: int = 0
    region: str = ""
    desc: str = ""
    battleground_effect: int | None = None
    tiers: tuple[ZoneTier, ...] = ()


@dataclass(frozen=True)
class ConquestEvent:
    id: int
    name: str
    start_time: int = 0
    end_time: int = 0
    asset_bundle: int = 0
    terrain_prefab: str = ""
    web_link: str = ""
    tiers: tuple[EventTier, ...] = ()

    @property
    def length(self) -> int:
        return max(3600, self.end_time - self.start_time)


def load(asset_dir: Path) -> tuple[dict[int, ConquestEvent], dict[int, Zone]]:
    """(events by id, live zones by id) from conquest.xml."""
    path = Path(asset_dir) / "conquest.xml"
    if not path.is_file():
        return {}, {}
    root = ET.parse(path).getroot()
    text = lambda e, tag: (e.findtext(tag) or "").strip()                # noqa: E731
    events = {}
    for e in root.findall("conquest_event"):
        tiers = event_runs.tiers_of(e.find("rewards"), 0)
        events[int(text(e, "id"))] = ConquestEvent(
            int(text(e, "id")), text(e, "name"), int(text(e, "start_time") or 0), int(text(e, "end_time") or 0),
            int(text(e, "asset_bundle") or 0), text(e, "terrain_prefab"), text(e, "web_link"), tuple(tiers))
    zones = {}
    for z in root.findall("conquest_zone"):
        if text(z, "disabled") not in ("", "0"):
            continue
        tiers = []
        for t in (z.find("conquest_reward") if z.find("conquest_reward") is not None else []):
            tiers.append(ZoneTier(int(text(t, "min_rank") or 0),
                                  int(text(t, "conquest_points")) if text(t, "conquest_points") else None,
                                  int(text(t, "fortress_card")) if text(t, "fortress_card") else None))
        zones[int(text(z, "id"))] = Zone(
            int(text(z, "id")), text(z, "name"), text(z, "picture"), int(text(z, "asset_bundle") or 0),
            int(text(z, "order") or 0), text(z, "region"), text(z, "desc"),
            int(text(z, "battleground_effect")) if text(z, "battleground_effect") else None,
            tuple(sorted(tiers, key=lambda t: t.min_rank)))
    return events, zones


# ---- the server's conquest state -----------------------------------------------------------------

class ConquestStore:
    """state/conquest.json, written whole and atomically."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        return {**new_state(), **jsonfile.read(self.path, {})}

    def save(self, state: dict) -> None:
        jsonfile.write(self.path, state)


def new_state() -> dict:
    return {"run": None, "guilds": {}, "members": {}, "previous": []}


def start_run(state: dict, event: ConquestEvent, now: int) -> None:
    """A new run from now, for the event's own length; the last run's guild standings
    become `previous`."""
    if state.get("run"):
        state["previous"] = standings(state)[:BOARD_SIZE]
    end = now + event.length
    state.update(run={"event": event.id, "start": now, "end": end, "decay": end + DECAY_DAYS * 86400, "rounds": 0},
                 guilds={}, members={})


def phase(state: dict, now: int) -> str | None:
    """"conquest" (fights), "claim" (over; rewards), or None."""
    return event_runs.phase(state, now, "conquest")


def guild_row(state: dict, gid, name: str = "") -> dict:
    row = state["guilds"].setdefault(str(gid), {"name": name, "points": 0, "influence": {}, "markers": {},
                                                "ranks": {}, "fortresses": {}})
    if name:
        row["name"] = name
    return row


def member_row(state: dict, uid, name: str = "") -> dict:
    row = state["members"].setdefault(str(uid), {"name": name, "influence": 0, "zones": {}, "guild": None,
                                                 "energy": ENERGY_MAX, "energy_updated": state["run"]["start"],
                                                 "claimed": 0, "battles": 0, "wins": 0})
    if name:
        row["name"] = name
    return row


# ---- ranks and scoring rounds --------------------------------------------------------------------

def zone_ranking(state: dict, zone_id) -> list[tuple[str, int]]:
    """(guild id, influence) in a zone, best first; guilds with none aren't ranked."""
    rows = [(gid, int(g["influence"].get(str(zone_id), 0))) for gid, g in state["guilds"].items()]
    return sorted([r for r in rows if r[1] > 0], key=lambda r: (-r[1], int(r[0])))


def zone_rank(state: dict, zone_id, gid) -> int:
    return next((i for i, (g, _) in enumerate(zone_ranking(state, zone_id), 1) if g == str(gid)), 0)


def tier_for(zone: Zone, rank: int, kind: str):
    """The best tier of a kind ("conquest_points" or "fortress_card") a rank reaches: the
    one with the lowest min_rank at or above the rank (not cumulative)."""
    if rank < 1:
        return None
    return next((getattr(t, kind) for t in zone.tiers if getattr(t, kind) and t.min_rank >= rank), None)


def rounds_due(run: dict, now: int) -> int:
    """Scoring rounds that have come by now: one every ROUND_EVERY from the start, up to the end."""
    last = min(now, run["end"])
    return max(0, (last - run["start"]) // ROUND_EVERY)


def score_rounds(state: dict, zones: dict[int, Zone], now: int) -> int:
    """Scores every round that has come and isn't scored yet: each zone ranks the guilds by
    influence, and each guild gets the points of the top points tier its rank reaches and
    access to the top fortress level. Influence doesn't decay (an unknown: see above).
    Returns the rounds scored."""
    run = state["run"]
    due = rounds_due(run, now) - run["rounds"]
    for _ in range(max(0, due)):
        for zone in zones.values():
            for rank, (gid, _) in enumerate(zone_ranking(state, zone.id), 1):
                g = state["guilds"][gid]
                g["points"] += tier_for(zone, rank, "conquest_points") or 0
                g["ranks"][str(zone.id)] = rank
                card = tier_for(zone, rank, "fortress_card")
                if card:
                    g["fortresses"][str(zone.id)] = card
                else:
                    g["fortresses"].pop(str(zone.id), None)
        run["rounds"] += 1
    return max(0, due)


def standings(state: dict) -> list[dict]:
    """Guilds that took part, by conquest points (then influence, then id): {"id", "name",
    "points", "rank"}."""
    rows = [(gid, g) for gid, g in state["guilds"].items() if g["points"] or any(g["influence"].values())]
    rows.sort(key=lambda kv: (-kv[1]["points"], -sum(kv[1]["influence"].values()), int(kv[0])))
    return [{"id": gid, "name": g["name"], "points": g["points"], "rank": i} for i, (gid, g) in enumerate(rows, 1)]


def guild_rank(state: dict, gid) -> int:
    return next((r["rank"] for r in standings(state) if r["id"] == str(gid)), 0)


def fortress_cards(state: dict, gid) -> list[int]:
    """The fortresses a guild deploys: from the zones it ranked best in (at the last round),
    at most FORTRESSES."""
    g = state["guilds"].get(str(gid))
    if not g:
        return []
    zones = sorted(g["fortresses"], key=lambda z: (g["ranks"].get(z, 10**9), int(z)))
    return [g["fortresses"][z] for z in zones[:FORTRESSES]]


# ---- energy (the client's formula, as for brawls: event_runs.py) ---------------------------------

ENERGY = event_runs.Energy(ENERGY_MAX, ENERGY_EVERY, RECHARGE_STOPS, ENERGY_COST)
recharge_stop = ENERGY.recharge_stop
energy_now = ENERGY.current
spend_energy = ENERGY.spend


# ---- fights --------------------------------------------------------------------------------------

def record_fight(state: dict, member: dict, gid, zone_id, won: bool) -> int:
    """A fight's influence (50 a win, 5 a loss) to the guild in the zone and to the member."""
    influence = WIN_INFLUENCE if won else LOSS_INFLUENCE
    g = state["guilds"][str(gid)]
    g["influence"][str(zone_id)] = int(g["influence"].get(str(zone_id), 0)) + influence
    member["influence"] += influence
    member["zones"][str(zone_id)] = int(member["zones"].get(str(zone_id), 0)) + influence
    member["guild"] = str(gid)
    member["battles"] += 1
    member["wins"] += int(won)
    return influence


def pick_guild(candidates: list[tuple[str, int]], my_points: int, rng: random.Random,
               nearest: int = NEAREST) -> str | None:
    """One of the `nearest` guilds (id, conquest points) closest to my guild's points."""
    if not candidates:
        return None
    close = sorted(candidates, key=lambda c: (abs(c[1] - my_points), int(c[0])))[:nearest]
    return rng.choice(sorted(c[0] for c in close))


# ---- rewards -------------------------------------------------------------------------------------

def claimable(state: dict, now: int) -> bool:
    return event_runs.claimable(state, now, CLAIM_WAIT)


def earned_tiers(event: ConquestEvent, rank: int, influence: int) -> list[EventTier]:
    return event_runs.earned_tiers(event.tiers, rank, influence)


# ---- what the client gets ------------------------------------------------------------------------

def no_empty(d: dict) -> dict:
    """Leaves out empty text: the client reads an empty string as "0" (it shows it, and
    loads `images/0` for a picture), and reads a missing one as empty."""
    return {k: v for k, v in d.items() if v != ""}


def event_tier_json(tier: EventTier, mini_packs: dict) -> dict:
    out = {"id": str(tier.id), "rank": tier.rank, "min_points": tier.min_points}
    for kind, rid, amount in tier.rewards:
        if kind == "card":
            out["card"] = {"id": rid, "amount": amount}
        elif kind == "pack":
            out["pack"] = {"id": rid}
        elif kind == "mini_pack":
            mp = mini_packs.get(str(rid)) or {}
            out["mini_pack"] = no_empty({"id": rid, "name": mp.get("name", ""), "desc": mp.get("desc", ""),
                                         "web_picture": mp.get("web_picture") or mp.get("picture", "")})
    return out


def zone_json(zone: Zone, state: dict, gid, effect) -> dict:
    """A zone in conquest_data.zone_data. Tiers go in ascending min_rank (the client links
    each to the one before it)."""
    g = state["guilds"].get(str(gid)) if gid is not None else None
    tiers = []
    for t in zone.tiers:
        entry = {"min_rank": t.min_rank}
        if t.conquest_points:
            entry["conquest_points"] = t.conquest_points
        if t.fortress_card:
            entry["fortress_card"] = t.fortress_card
        tiers.append(entry)
    return no_empty({
            "id": zone.id, "name": zone.name, "desc": zone.desc, "picture": zone.picture,
            "asset_bundle": zone.asset_bundle,
            "battleground_effect_data": (no_empty({"name": effect.name, "desc": effect.desc,
                                                   "web_picture": effect.picture})
                                         if effect is not None else {}),
            "marker_id": int(g["markers"].get(str(zone.id), 0)) if g else 0,
            "my_guild_rank": zone_rank(state, zone.id, gid) if gid is not None else 0,
            "my_guild_influence": int(g["influence"].get(str(zone.id), 0)) if g else 0,
            "conquest_reward": {"tier": tiers}})


def conquest_json(event: ConquestEvent, zones: dict[int, Zone], state: dict, gid, uid, effects: dict,
                  mini_packs: dict, now: int) -> dict:
    """conquest_data: the event, the guild's standing, the player's, the zones, the tiers."""
    run = state["run"]
    g = state["guilds"].get(str(gid)) if gid is not None else None
    me = state["members"].get(str(uid))
    return no_empty({
            "id": event.id, "name": event.name, "web_link": event.web_link, "asset_bundle": event.asset_bundle,
            "terrain_prefab": event.terrain_prefab,
            "conquest_points": g["points"] if g else 0, "conquest_rank": guild_rank(state, gid) if gid else 0,
            "start_time": run["start"], "end_time": run["end"], "decay_time": run["decay"],
            "claim_reward_prevention": CLAIM_WAIT,
            "user_conquest_data": {
                "claimed_reward": int(bool(me and me["claimed"])), "influence": me["influence"] if me else 0,
                "energy": {"battle_energy": energy_now(run, me, now), "max_battle_energy": ENERGY_MAX,
                           "cost_per_battle_energy": 0, "battle_energy_recharge_time": ENERGY_EVERY,
                           "battle_energy_recharge_start_time": run["start"],
                           "battle_energy_recharge_stop_time": recharge_stop(run),
                           "battle_energy_update_time": me["energy_updated"] if me else run["start"]}},
            "zone_data": [zone_json(z, state, gid, effects.get(z.battleground_effect))
                          for z in sorted(zones.values(), key=lambda z: (z.order, z.id))],
            "rewards": {"tier": [event_tier_json(t, mini_packs) for t in event.tiers]}})


def board_json(key: str, title: str, rows: list[dict], first: dict | None) -> dict:
    """A ranking board: `data[0]` is the fixed first row (the player's own guild or self),
    the rest the list. The client reads `data` only when it has two rows or more.
    Text left empty (a row's `header`, `desc`, `icon`, `web_icon`; the board's `note`) is
    left out: the client reads an empty string as "0", shows it, and loads `images/0` for
    an icon."""
    def row(r):
        return {"name": r["name"], "stat": str(r["stat"]), "rank": r["rank"], "user_id": int(r["id"]),
                "color": "FFFFFF", "p": 0.0}
    data = [row(first or rows[0])] if (first or rows) else []
    data += [row(r) for r in rows] or ([row(first)] if first else [])
    return {"ranking_name": key, "name": title, "data": data}
