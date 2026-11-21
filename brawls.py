"""Brawls: a timed player-versus-player event. Each fight is against another player's
defense deck (played by the AI) and scores brawl points; when the brawl is over, players
claim the reward tiers their rank (and, in a guild brawl, their guild's rank and their own
points) reach. docs/protocol.md, "Brawls", has the evidence.

The state is the server's (state/brawls.json): the standings cover every player.

From the client's code: the calls (none take parameters), `active_brawl_data` and
`player_brawl_data` and which replies replace them, the leaderboards, the popup's phases,
that a brawl fight is one reply read like a raid's or a war's, its `pvp_points` reward,
that the Fight button needs `user_data.battle_energy` (with the regeneration the client
works out itself) while the brawl's bar shows `player_brawl_data.energy`, and that the
brawl is shown only to players rated above 100.

Recorded (brawl 345, a guild placement brawl, in every recorded login): rewards claimable
from 1200 s after the end until 3 days after it, 1 energy a fight, 25 energy recharging
one per 1800 s. The data file (battle_events_h52.xml, 343 brawls) has the names, times,
guild/solo flags, battleground effects by day and reward tiers.

The fan wiki (Brawl page, read 2026-11-21): 25 attacks, one back every 30 minutes, the
recharge stopping with less than 6 hours left; surge mode (the defender plays first); no
fortresses.

The optimizer (reference/tuo, evaluate_brawl_score): a win scores 55, plus the enemy
cards destroyed, plus the player's cards never played, minus 1 per 8 turns; a loss 5.

Ours: the opponent (a random one of the NEAREST players by brawl points, outside the
player's guild in a guild brawl, with a deck to defend with); no gold per fight; nothing
for the defender; a player must have fought to claim; in a guild brawl, the guild's points
are its members' and the tiers need the guild's rank and the member's points (as in guild
wars); the win streak is counted but not scored (the client never shows it).
"""

from __future__ import annotations

import json
import os
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Recorded (brawl 345).
CLAIM_WAIT = 1200
DECAY_DAYS = 3
ENERGY_COST = 1
ENERGY_MAX = 25
ENERGY_EVERY = 1800
# The wiki.
RECHARGE_STOPS = 6 * 3600         # before the end
# The optimizer.
WIN_BASE = 55
LOSS_POINTS = 5
# The client.
MIN_RATING = 101                  # the brawl shows for an arena rating above 100
# Ours.
NEAREST = 5                       # opponents are picked among this many closest in points
BOARD_SIZE = 100


class BrawlError(Exception):
    """Why something can't be done, in words the player can read."""


@dataclass(frozen=True)
class Tier:
    id: int
    rank: int
    min_points: int | None
    rewards: tuple[tuple[str, int, int], ...]     # (card / mini_pack / pack / item, id, amount)


@dataclass(frozen=True)
class BrawlEvent:
    id: int
    name: str
    start_time: int = 0
    end_time: int = 0
    web_picture: str = ""
    web_link: str = ""
    guild_event: bool = False
    war: bool = False                 # a guild war's placement brawl
    use_forts: bool = False
    tiers: tuple[Tier, ...] = ()
    effects: tuple[tuple[int, int], ...] = ()     # (day from 0, battleground effect id)

    @property
    def length(self) -> int:
        return max(3600, self.end_time - self.start_time)


def load(asset_dir: Path, file_name: str | None = None) -> dict[int, BrawlEvent]:
    """The brawls in battle_events_*.xml, by id."""
    asset_dir = Path(asset_dir)
    path = asset_dir / file_name if file_name else next(iter(sorted(asset_dir.glob("battle_events_*.xml"))), None)
    if path is None or not path.is_file():
        return {}
    out = {}
    for b in ET.parse(path).getroot().findall("battle_event"):
        start = int(b.findtext("start_time") or 0)
        tiers = []
        rewards = b.find("rewards")
        for t in (rewards if rewards is not None else []):
            got = tuple((kind, int(e.get("id")), int(e.get("amount") or 1))
                        for kind in ("card", "mini_pack", "pack", "item") for e in t.findall(kind))
            mp = t.findtext("min_points")
            tiers.append(Tier(int(t.findtext("id")), int(t.findtext("rank") or 1), int(mp) if mp else None, got))
        effects = []
        config = b.find("battleground_config")
        for e in (config if config is not None else []):
            effects.append((max(0, (int(e.get("start_time") or start) - start) // 86400), int(e.get("id"))))
        flag = lambda tag: (b.findtext(tag) or "0").strip() not in ("", "0")    # noqa: E731
        out[int(b.findtext("id"))] = BrawlEvent(
            int(b.findtext("id")), (b.findtext("name") or "").strip(), start, int(b.findtext("end_time") or 0),
            (b.findtext("web_picture") or "").strip(), (b.findtext("web_link") or "").strip(),
            flag("guild_event"), flag("war"), flag("use_forts"),
            tuple(sorted(tiers, key=lambda t: (t.rank, t.id))), tuple(sorted(effects)))
    return out


# ---- the server's brawl state --------------------------------------------------------------------

class BrawlStore:
    """state/brawls.json, written whole and atomically."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.is_file():
            return new_state()
        return {**new_state(), **json.loads(self.path.read_text("utf-8"))}

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.path)


def new_state() -> dict:
    return {"run": None, "players": {}, "guilds": {}, "previous": [], "previous_guild_event": False}


def start_run(state: dict, event: BrawlEvent, now: int) -> None:
    """A new run of a brawl from now, for its own length; the last run's top becomes
    `previous`."""
    if state.get("run"):
        guild_event = bool(state["run"].get("guild_event"))
        state["previous"] = standings(state, guild_event)[:BOARD_SIZE]
        state["previous_guild_event"] = guild_event
    end = now + event.length
    state.update(run={"event": event.id, "start": now, "end": end, "decay": end + DECAY_DAYS * 86400,
                      "guild_event": event.guild_event},
                 players={}, guilds={})


def phase(state: dict, now: int) -> str | None:
    """"brawl" (fights), "claim" (over; rewards), or None."""
    run = state.get("run")
    if not run:
        return None
    if run["start"] <= now <= run["end"]:
        return "brawl"
    if run["end"] < now <= run["decay"]:
        return "claim"
    return None


def player_row(state: dict, uid, name: str = "") -> dict:
    row = state["players"].setdefault(str(uid), {"points": 0, "battles": 0, "wins": 0, "win_streak": 0,
                                                 "energy": ENERGY_MAX, "energy_updated": state["run"]["start"],
                                                 "claimed": 0, "name": name})
    if name:
        row["name"] = name
    return row


# ---- energy (the client's own formula, EventEnergyBar.Refresh) --------------------------------------

def recharge_stop(run: dict) -> int:
    return run["end"] - RECHARGE_STOPS


def energy_now(run: dict, row: dict | None, now: int) -> int:
    """energy + one per ENERGY_EVERY from the last update (not before the start, not past
    the stop), at most ENERGY_MAX."""
    if row is None:
        return ENERGY_MAX
    gained = max(0, (min(now, recharge_stop(run)) - max(row["energy_updated"], run["start"])) // ENERGY_EVERY)
    return min(ENERGY_MAX, row["energy"] + gained)


def spend_energy(run: dict, row: dict, now: int) -> None:
    """Brings the energy up to now (keeping the part-way progress), then spends one fight's."""
    since = max(row["energy_updated"], run["start"])
    gained = max(0, (min(now, recharge_stop(run)) - since) // ENERGY_EVERY)
    if row["energy"] + gained >= ENERGY_MAX:
        row["energy"], row["energy_updated"] = ENERGY_MAX, now
    else:
        row["energy"] += gained
        row["energy_updated"] = since + gained * ENERGY_EVERY
    row["energy"] -= ENERGY_COST


def recharging(run: dict, row: dict | None, now: int) -> bool:
    return energy_now(run, row, now) < ENERGY_MAX and run["start"] <= now < recharge_stop(run)


# ---- fights ------------------------------------------------------------------------------------

def fight_points(won: bool, enemy_destroyed: int, own_unplayed: int, turns: int) -> int:
    """The optimizer's brawl score: a win 55 + the enemy cards destroyed + the player's
    cards never played - (turns + 7) // 8; a loss 5."""
    if not won:
        return LOSS_POINTS
    return max(0, WIN_BASE + enemy_destroyed + own_unplayed - (turns + 7) // 8)


def record_fight(state: dict, row: dict, gid, won: bool, points: int, guild_name: str = "") -> None:
    row["battles"] += 1
    row["points"] += points
    if won:
        row["wins"] += 1
        row["win_streak"] += 1
    else:
        row["win_streak"] = 0
    if gid is not None:
        g = state["guilds"].setdefault(str(gid), {"points": 0, "name": guild_name})
        g["points"] += points
        g["name"] = guild_name or g["name"]


def pick_opponent(candidates: list[tuple[str, int]], my_points: int, rng: random.Random,
                  nearest: int = NEAREST) -> str | None:
    """One of the `nearest` candidates (user id, brawl points) closest to my points."""
    if not candidates:
        return None
    close = sorted(candidates, key=lambda c: (abs(c[1] - my_points), str(c[0])))[:nearest]
    return rng.choice(sorted(c[0] for c in close))


def effect_on(event: BrawlEvent, run: dict, now: int) -> int | None:
    """The brawl's battleground effect today (the last one whose day has come)."""
    day = max(0, (now - run["start"]) // 86400)
    today = [e for d, e in event.effects if d <= day]
    return today[-1] if today else None


# ---- standings and rewards -------------------------------------------------------------------------

def standings(state: dict, guild_event: bool) -> list[dict]:
    """The players (a guild brawl: the guilds) who scored, best first: {"id", "name",
    "points", "rank"}. Ties go to the lower id."""
    source = state["guilds"] if guild_event else {u: r for u, r in state["players"].items() if r["battles"]}
    rows = sorted(source.items(), key=lambda kv: (-kv[1]["points"], int(kv[0])))
    return [{"id": i, "name": r.get("name", ""), "points": r["points"], "rank": n} for n, (i, r) in enumerate(rows, 1)]


def rank_of(state: dict, key, guild_event: bool) -> int:
    """A player's (a guild brawl: a guild's) rank; 0 without one."""
    return next((r["rank"] for r in standings(state, guild_event) if r["id"] == str(key)), 0)


def claimable(state: dict, now: int) -> bool:
    run = state.get("run")
    return bool(run) and run["end"] + CLAIM_WAIT <= now <= run["decay"]


def earned_tiers(event: BrawlEvent, rank: int, points: int) -> list[Tier]:
    """The tiers a rank (and, where a tier sets one, the points) reach."""
    if rank < 1:
        return []
    return [t for t in event.tiers if rank <= t.rank and (t.min_points is None or points >= t.min_points)]


# ---- what the client gets ------------------------------------------------------------------------

def tier_json(tier: Tier, mini_packs: dict) -> dict:
    """A reward tier as the recorded active_brawl_data had it."""
    out = {"id": str(tier.id), "rank": str(tier.rank)}
    for kind, rid, amount in tier.rewards:
        entry = {"id": str(rid), "amount": str(amount)}
        if kind == "mini_pack":
            mp = mini_packs.get(str(rid)) or {}
            entry.update({"name": mp.get("name", ""), "desc": mp.get("desc", ""),
                          "web_picture": mp.get("web_picture") or mp.get("picture", "")})
        out[kind] = entry
    if tier.min_points is not None:
        out["min_points"] = str(tier.min_points)
    return out


def event_json(event: BrawlEvent, run: dict, mini_packs: dict) -> dict:
    """active_brawl_data, in the recorded shape, for this run's times."""
    out = {"id": str(event.id), "name": event.name, "start_time": str(run["start"]), "end_time": str(run["end"]),
           "web_picture": event.web_picture}
    if event.web_link:
        out["web_link"] = event.web_link
    for flag in ("use_forts", "guild_event", "war"):
        if getattr(event, flag):
            out[flag] = "1"
    out.update({"rewards": {"tier": [tier_json(t, mini_packs) for t in event.tiers]},
                "decay_time": run["decay"], "battle_energy_cost": ENERGY_COST, "claim_reward_prevention": CLAIM_WAIT})
    return out


def player_json(state: dict, event: BrawlEvent, uid, rank: int, now: int) -> dict:
    """player_brawl_data, complete (the client zeroes what's left out)."""
    run = state["run"]
    row = state["players"].get(str(uid))
    energy = energy_now(run, row, now)
    return {"user_id": str(uid), "battle_event_id": event.id, "num_wins": row["wins"] if row else 0,
            "points": row["points"] if row else 0, "win_streak": row["win_streak"] if row else 0,
            "claimed_rewards": int(bool(row and row["claimed"])), "seeding": 0, "current_rank": rank,
            "energy": {"battle_energy_recharge_time": ENERGY_EVERY, "max_battle_energy": ENERGY_MAX,
                       "battle_energy": energy if row else ENERGY_MAX,
                       "battle_energy_update_time": row["energy_updated"] if row else run["start"],
                       "battle_energy_recharge_start_time": run["start"],
                       "battle_energy_recharge_stop_time": recharge_stop(run)}}


def board_json(rows: list[dict], guild_event: bool) -> list[dict]:
    """brawl_leaderboard / previous_brawl_leaderboard entries, in the recorded shape."""
    key = "faction_id" if guild_event else "user_id"
    return [{key: str(r["id"]), "points": str(r["points"]), "points_rank": r["rank"], "rank": r["rank"],
             "stat": str(r["points"]), "name": r["name"]} for r in rows]
