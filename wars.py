"""Guild wars ("faction wars" in the protocol): guilds queue, get matched in pairs,
and their members attack the other guild's fortress slots for war points. A war
*event* (a few days, from faction_wars_fp3.xml) holds many such matches, and ranks the
guilds by the ranking points their matches earn; members claim reward tiers by their
guild's rank and their own points. docs/protocol.md, "Guild wars", has the evidence.

The state is the server's, not a guild's: matches pair two guilds, and the event's
standings cover all of them. It lives in state/wars.json (WarStore).

From the client's code: the calls and the reply formats (`active_war_event`,
`faction_war`, `faction_war_event_info`, the leaderboards), the Wars tab's phases, and
that a war fight is one reply, like a raid's.

Recorded (war 94's `active_war_event` in every recorded login): an 8-hour match
(`war_length` 28800) whose first 30 minutes are preparation (`war_battle_prep_time`
1800), no new matches in the event's last 6 hours, rewards claimable from 15 minutes
after the end (`claim_reward_prevention` 900) for 2 days (`decay_time`), 1 war attack
per fight, war-bond refills off (`wb_battle_energy_cost` -1).

From the fan wiki's Guild War page (read 2026-11-21):
- A guild needs at least five members to queue. Queued guilds are matched with other
  queued guilds, and stay queued after a match (matched again as soon as it ends),
  unless they made no attacks in it. No match with a guild more than 20% apart in
  points, or with one met in the last 8 hours (counted here from the match's end).
- During preparation the fortresses can be changed and the enemy's seen, not attacked.
- Each member has 20 war attacks a match, refilled when a new match starts; they don't
  recharge over time.
- Fights are in surge mode: the defending deck plays first. Both guilds' fortresses
  take part (below).
- Points: attacking the core, a win scores 200 minus 1 per turn elapsed, a loss 20;
  attacking a fortress, 100 minus 1 per turn, or 10. A win pays 70 gold, a loss 35;
  defending pays nothing.
- Points scored against a fortress damage it (defensive fortresses take double damage
  from wins); a destroyed fortress no longer takes part and leaves an empty slot that
  can still be attacked. The core can't be destroyed. Fortresses are repaired at the
  end of each match.
- Fortresses in a fight: attacking a fortress, the defender has that fortress in play;
  attacking the core, all their surviving defensive fortresses. Attacking a defensive
  fortress or the core, the attacker has their guild's surviving siege fortresses.
- A match won pays the guild 800-900 ranking points, a match lost 100-200, "depending
  on how much they won by"; doubled from a week into the event.
- A reward tier needs both the guild's rank and the member's points.

Where the wiki (an older version) disagrees with the recorded event, the recording
wins: 8-hour matches (wiki: 6), 30 minutes of preparation (wiki: 15), 3-day events
(wiki: 9), 2 days to claim (wiki: 7).

Ours (nothing recorded or documented): the margin rule for ranking points (below), a
tie as a loss for both, the fortress health (FORT_HEALTH, shown as a percentage), who
may start a war (officers and the leader), the defender being a random member of the
other guild with a deck to defend with (their defense deck, played by the AI), and
matches cut short at the event's end.
"""

from __future__ import annotations

import math
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import jsonfile

# Recorded (war 94's active_war_event).
WAR_LENGTH = 28800
PREP_TIME = 1800
CLAIM_WAIT = 900
NO_NEW_WARS = 21600               # before the event's end
CLAIM_DAYS = 2                    # decay_time = end + 172800
MORE_POINTS_AFTER = 7 * 86400     # more_points_start_time = start + 7 days: ranking points doubled
ENERGY_COST = 1
REPAIR_ITEM_HEALTH = 100
WAIT_MESSAGE = "Next war starts in 8 hours!"
# The wiki.
ATTACKS = 20
CORE_WIN, CORE_LOSS = 200, 20
FORT_WIN, FORT_LOSS = 100, 10
WIN_GOLD, LOSS_GOLD = 70, 35
WIN_RANKING, LOSS_RANKING = 800, 100      # + up to 100 by the margin (see ranking_points)
MIN_MEMBERS = 5
POINT_GAP = 0.20
REMATCH_AFTER = 8 * 3600
# Ours.
FORT_HEALTH = 1000                # damage a fortress takes before it's destroyed; shown out of 100

CORE = 1
DEFENSE = (11, 12)
SIEGE = (21, 22)
SLOTS = (CORE,) + DEFENSE + SIEGE

# News types (the client's): 11 a war started / matched, 12 won, 13 lost.
NEWS_STARTED, NEWS_WON, NEWS_LOST = 11, 12, 13


class WarError(Exception):
    """Why something can't be done, in words the player can read."""


@dataclass(frozen=True)
class Tier:
    id: int
    rank: int                     # the guild's rank must be this or better
    min_points: int               # the member's points must be at least this
    rewards: tuple[tuple[str, int, int], ...]     # (card / mini_pack / pack / item, id, amount)


@dataclass(frozen=True)
class WarEvent:
    id: int
    name: str
    web_picture: str = ""
    web_link: str = ""
    start_time: int = 0
    end_time: int = 0
    tiers: tuple[Tier, ...] = ()
    effects: tuple[tuple[int, int], ...] = ()     # (day of the event from 0, battleground effect id)
    repair_item: int | None = None
    refill_item: int | None = None


def load(asset_dir: Path, file_name: str | None = None) -> dict[int, WarEvent]:
    """The war events in faction_wars_*.xml, by id."""
    asset_dir = Path(asset_dir)
    path = asset_dir / file_name if file_name else next(iter(sorted(asset_dir.glob("faction_wars_*.xml"))), None)
    if path is None or not path.is_file():
        return {}
    out = {}
    for w in ET.parse(path).getroot().findall("faction_war"):
        start = int(w.findtext("start_time") or 0)
        tiers = []
        for t in (w.find("rewards") if w.find("rewards") is not None else []):
            rewards = tuple((kind, int(e.get("id")), int(e.get("amount") or 1))
                            for kind in ("card", "mini_pack", "pack", "item") for e in t.findall(kind))
            tiers.append(Tier(int(t.findtext("id")), int(t.findtext("rank") or 1),
                              int(t.findtext("min_points") or 0), rewards))
        effects = []
        config = w.find("battleground_config")
        for e in (config if config is not None else []):
            day = max(0, (int(e.get("start_time") or start) - start) // 86400)
            effects.append((day, int(e.get("id"))))
        out[int(w.findtext("id"))] = WarEvent(
            int(w.findtext("id")), w.findtext("name") or "", w.findtext("web_picture") or "",
            w.findtext("web_link") or "", start, int(w.findtext("end_time") or 0),
            tuple(sorted(tiers, key=lambda t: t.rank)), tuple(sorted(effects)),
            int(w.findtext("repair_item")) if w.findtext("repair_item") else None,
            int(w.findtext("refill_item")) if w.findtext("refill_item") else None)
    return out


# ---- the server's war state ----------------------------------------------------------------------

class WarStore:
    """state/wars.json, written whole and atomically."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        return {**new_state(), **jsonfile.read(self.path, {})}

    def save(self, state: dict) -> None:
        jsonfile.write(self.path, state)


def new_state() -> dict:
    return {"run": None, "guilds": {}, "members": {}, "queue": {}, "matches": [], "previous": [],
            "next_match": 1}


def start_run(state: dict, event: WarEvent, now: int, days: int) -> None:
    """A new run of an event, from now: the last run's standings become `previous`."""
    if state.get("run"):
        state["previous"] = standings(state)
    end = now + days * 86400
    state.update(run={"event": event.id, "start": now, "end": end, "no_new": end - NO_NEW_WARS,
                      "decay": end + CLAIM_DAYS * 86400},
                 guilds={}, members={}, queue={}, matches=[])


def phase(state: dict, now: int) -> str | None:
    """"war" (matches can be played), "claim" (the event is over; rewards), or None."""
    run = state.get("run")
    if not run:
        return None
    if run["start"] <= now <= run["end"]:
        return "war"
    if run["end"] < now <= run["decay"]:
        return "claim"
    return None


def guild_row(state: dict, gid, name: str) -> dict:
    return state["guilds"].setdefault(str(gid), {"points": 0, "name": name})


def member_row(state: dict, uid) -> dict:
    return state["members"].setdefault(str(uid), {"points": 0, "claimed": 0})


def standings(state: dict) -> list[dict]:
    """The event's guilds, best first: {"id", "name", "points", "rank"}."""
    rows = sorted(state["guilds"].items(), key=lambda kv: (-kv[1]["points"], int(kv[0])))
    return [{"id": gid, "name": g["name"], "points": g["points"], "rank": i} for i, (gid, g) in enumerate(rows, 1)]


def rank_of(state: dict, gid) -> int:
    """A guild's rank in the event; after every ranked guild when it has none."""
    rows = standings(state)
    return next((r["rank"] for r in rows if r["id"] == str(gid)), len(rows) + 1)


# ---- matches -----------------------------------------------------------------------------------

def match_of(state: dict, gid) -> dict | None:
    """The guild's match being played (not finished), if any."""
    return next((m for m in state["matches"] if not m["done"] and str(gid) in (m["a"], m["d"])), None)


def other(match: dict, gid) -> str:
    return match["d"] if str(gid) == match["a"] else match["a"]


def battle_phase(match: dict, now: int) -> bool:
    return match["start"] + PREP_TIME <= now < match["end"]


def can_queue(state: dict, gid, members: int, now: int, min_members: int = MIN_MEMBERS) -> None:
    """Why a guild can't start a war now, as a WarError."""
    run = state.get("run")
    if phase(state, now) != "war":
        raise WarError("There's no guild war on right now.")
    if now > run["no_new"]:
        raise WarError("No new wars: the event ends soon.")
    if members < min_members:
        raise WarError(f"A guild needs at least {min_members} members to start a war.")
    if str(gid) in state["queue"] or match_of(state, gid):
        raise WarError("Your guild is already at war.")


def queue(state: dict, gid, now: int) -> None:
    state["queue"][str(gid)] = now


def _met_recently(state: dict, a: str, b: str, now: int, rematch_after: int) -> bool:
    """Whether the two met within `rematch_after` seconds, counted from their match's end
    (ours: the wiki counts from the matching, but with its 6-hour matches; with the
    recorded 8-hour ones that would never stop a rematch)."""
    return any({m["a"], m["d"]} == {a, b} and now - m["end"] < rematch_after for m in state["matches"])


def _close_enough(state: dict, a: str, b: str, gap: float) -> bool:
    pa = state["guilds"].get(a, {}).get("points", 0)
    pb = state["guilds"].get(b, {}).get("points", 0)
    return abs(pa - pb) <= gap * max(pa, pb)


def matchmake(state: dict, now: int, members_of, names, min_members: int = MIN_MEMBERS,
              gap: float = POINT_GAP, rematch_after: int = REMATCH_AFTER, seeds: dict | None = None) -> list[dict]:
    """Pairs queued guilds that aren't in a match, longest-waiting first, within the
    rules (enough members, points within `gap`, not met in `rematch_after`). A guild
    that no longer exists, or is too small, leaves the queue. Returns the new matches.
    members_of(gid): the guild's member ids, None when it's gone. names(gid): its name.
    seeds: a placement brawl's guild points (brawls.placement_seeds): each guild then
    meets the waiting guild closest to it in them (ours), the longest-waiting on a tie."""
    run = state.get("run")
    if phase(state, now) != "war" or now > run["no_new"]:
        return []
    waiting = []
    for gid, since in sorted(state["queue"].items(), key=lambda kv: (kv[1], int(kv[0]))):
        members = members_of(gid)
        if members is None or len(members) < min_members:
            del state["queue"][gid]
        elif not match_of(state, gid):
            waiting.append(gid)
    made = []
    while waiting:
        a = waiting.pop(0)
        order = sorted(waiting, key=lambda g: abs(seeds.get(a, 0) - seeds.get(g, 0))) if seeds else waiting
        b = next((g for g in order if _close_enough(state, a, g, gap)
                  and not _met_recently(state, a, g, now, rematch_after)), None)
        if b is None:
            continue
        waiting.remove(b)
        m = {"id": state["next_match"], "a": a, "d": b, "start": now, "end": min(now + WAR_LENGTH, run["end"]),
             "names": {a: names(a), b: names(b)}, "points": {a: 0, b: 0}, "attacks": {a: 0, b: 0},
             "damage": {a: {}, b: {}}, "members": {}, "done": False, "result": None}
        state["next_match"] += 1
        for gid in (a, b):
            guild_row(state, gid, names(gid))["name"] = names(gid)
            for uid in members_of(gid):
                fighter(m, uid, gid)
        state["matches"].append(m)
        made.append(m)
    return made


def fighter(match: dict, uid, gid) -> dict:
    """A member's line in a match (made with a full attack bar on first sight)."""
    return match["members"].setdefault(str(uid), {"guild": str(gid), "points": 0, "wins": 0, "losses": 0,
                                                  "defend_wins": 0, "defend_losses": 0, "energy": ATTACKS})


def ranking_points(own: int, theirs: int, doubled: bool = False) -> int:
    """A finished match's ranking points for a guild. Won: 800, plus up to 100 by the
    margin; lost: 200, less up to 100 by the margin (the wiki's ranges; the margin,
    (winner - loser) / winner, is ours). A tie counts as a loss for both (200)."""
    top = max(own, theirs)
    margin = abs(own - theirs) / top if top else 0.0
    if own > theirs:
        points = WIN_RANKING + round(100 * margin)
    else:
        points = LOSS_RANKING + 100 - round(100 * margin)
    return points * (2 if doubled else 1)


def finish_matches(state: dict, now: int) -> list[dict]:
    """Ends every match whose time is up: ranking points to both guilds, and the guilds
    that made no attacks leave the queue. Once the event is over the queue empties.
    Returns the matches finished."""
    run = state.get("run") or {}
    done = []
    if run and now > run["end"]:
        state["queue"] = {}
    for m in state["matches"]:
        if m["done"] or now < m["end"]:
            continue
        a, d = m["a"], m["d"]
        doubled = m["start"] >= run.get("start", 0) + MORE_POINTS_AFTER
        gained = {a: ranking_points(m["points"][a], m["points"][d], doubled),
                  d: ranking_points(m["points"][d], m["points"][a], doubled)}
        for gid in (a, d):
            guild_row(state, gid, m["names"][gid])["points"] += gained[gid]
            if not m["attacks"][gid]:
                state["queue"].pop(gid, None)
        winner = a if m["points"][a] > m["points"][d] else d if m["points"][d] > m["points"][a] else None
        m.update(done=True, result={"winner": winner, "ranking": gained})
        done.append(m)
    return done


# ---- fights -------------------------------------------------------------------------------------

def slot_health(match: dict, gid, slot: int) -> int:
    """A slot's health out of FORT_HEALTH in this match (the core never falls)."""
    if slot == CORE:
        return FORT_HEALTH
    return max(0, FORT_HEALTH - int(match["damage"][str(gid)].get(str(slot), 0)))


def fight_fortresses(match: dict, my_gid, my_slots: dict, enemy_gid, enemy_slots: dict, slot: int):
    """(my fortress card ids, the enemy's) for an attack on the enemy's `slot`. slots:
    the guilds' {slot: card id} (guild["slots"])."""
    def alive(gid, slots, s):
        card = int(slots.get(str(s), 0) or 0)
        return card if card and slot_health(match, gid, s) > 0 else 0
    if slot == CORE:
        theirs = [alive(enemy_gid, enemy_slots, s) for s in DEFENSE]
    else:
        theirs = [alive(enemy_gid, enemy_slots, slot)]
    mine = [alive(my_gid, my_slots, s) for s in SIEGE] if slot in (CORE,) + DEFENSE else []
    return [c for c in mine if c], [c for c in theirs if c]


def fight_points(slot: int, won: bool, turns: int) -> int:
    if slot == CORE:
        return max(0, CORE_WIN - turns) if won else CORE_LOSS
    return max(0, FORT_WIN - turns) if won else FORT_LOSS


def record_fight(state: dict, match: dict, uid, gid, defender_uid, slot: int, won: bool, turns: int,
                 fortress: bool = True) -> tuple[int, int]:
    """Counts a fight: the attack spent, points to the member and the guild, damage to
    the slot's fortress (fortress: there was one in the fight; an empty or destroyed
    slot takes none), the defender's tally. Returns (war points, slot damage)."""
    enemy = other(match, gid)
    me = fighter(match, uid, gid)
    me["energy"] -= ENERGY_COST
    points = fight_points(slot, won, turns)
    damage = 0
    if won and fortress and slot != CORE and slot_health(match, enemy, slot) > 0:
        damage = min(points * (2 if slot in DEFENSE else 1), slot_health(match, enemy, slot))
        hurt = match["damage"][enemy]
        hurt[str(slot)] = int(hurt.get(str(slot), 0)) + damage
    me["points"] += points
    me["wins" if won else "losses"] += 1
    match["points"][str(gid)] += points
    match["attacks"][str(gid)] += 1
    member_row(state, uid)["points"] += points
    if defender_uid is not None:
        foe = fighter(match, defender_uid, enemy)
        foe["defend_losses" if won else "defend_wins"] += 1
    return points, damage


def effect_on(event: WarEvent, run: dict, now: int) -> int | None:
    """The event's battleground effect today (the last one whose day has come)."""
    day = max(0, (now - run["start"]) // 86400)
    today = [e for d, e in event.effects if d <= day]
    return today[-1] if today else None


# ---- rewards ------------------------------------------------------------------------------------

def claimable(state: dict, now: int) -> bool:
    run = state.get("run")
    return bool(run) and run["end"] + CLAIM_WAIT <= now <= run["decay"]


def earned_tiers(event: WarEvent, rank: int, points: int) -> list[Tier]:
    return [t for t in event.tiers if rank <= t.rank and points >= t.min_points]


def owes_rewards(state: dict, event: WarEvent, uid, gid, now: int) -> bool:
    """Whether a member has rewards still to claim (the wiki: they can't be kicked
    before claiming)."""
    if phase(state, now) != "claim":
        return False
    m = state["members"].get(str(uid))
    return bool(m) and not m["claimed"] and bool(earned_tiers(event, rank_of(state, gid), m["points"]))


# ---- what the client gets ------------------------------------------------------------------------

def tier_json(tier: Tier, mini_packs: dict) -> dict:
    """A reward tier as the recorded active_war_event had it."""
    out = {"id": str(tier.id), "rank": str(tier.rank), "min_points": str(tier.min_points)}
    for kind, rid, amount in tier.rewards:
        entry = {"id": str(rid), "amount": str(amount)}
        if kind == "mini_pack":
            mp = mini_packs.get(str(rid)) or {}
            entry.update({"name": mp.get("name", ""), "desc": mp.get("desc", ""),
                          "web_picture": mp.get("web_picture") or mp.get("picture", "")})
        out[kind] = entry
    return out


def event_json(event: WarEvent, run: dict, mini_packs: dict) -> dict:
    """active_war_event, in the recorded shape, for this run's times."""
    return {"id": str(event.id), "name": event.name, "start_time": str(run["start"]), "end_time": str(run["end"]),
            "web_picture": event.web_picture, "web_link": event.web_link,
            **({"repair_item": str(event.repair_item)} if event.repair_item else {}),
            **({"refill_item": str(event.refill_item)} if event.refill_item else {}),
            "rewards": {"tier": [tier_json(t, mini_packs) for t in event.tiers]},
            "war_length": WAR_LENGTH, "war_battle_prep_time": PREP_TIME, "prequeue_stop_time": run["start"],
            "more_points_start_time": run["start"] + MORE_POINTS_AFTER, "no_new_war_time": run["no_new"],
            "claim_reward_prevention": CLAIM_WAIT, "decay_time": run["decay"], "battle_energy_cost": ENERGY_COST,
            "repair_item_health": REPAIR_ITEM_HEALTH, "wait_message": WAIT_MESSAGE}


IDLE_MESSAGE = "No guild war is on right now."


def idle_event_json() -> dict:
    """An active_war_event that isn't on (its start, end and decay all 0). The Wars tab
    waits for one: it draws only after a reply carrying active_war_event (the client's
    GUILD_WARS_UPDATED), and with none on it shows this wait_message and the guild's
    fortress. Without it the tab says "Loading..." for good."""
    return {"id": "0", "name": "", "start_time": "0", "end_time": "0", "web_picture": "", "web_link": "",
            "rewards": {"tier": []}, "war_length": WAR_LENGTH, "war_battle_prep_time": PREP_TIME,
            "no_new_war_time": 0, "claim_reward_prevention": CLAIM_WAIT, "decay_time": 0,
            "battle_energy_cost": ENERGY_COST, "repair_item_health": REPAIR_ITEM_HEALTH, "wait_message": IDLE_MESSAGE}


def event_info_json(state: dict, gid, uid, member_ids, names) -> dict:
    """faction_war_event_info: the guild's and the player's points, rank and claim, and
    the members' points (the client reads the block only when it has `members`).
    member_ids: the guild's members now; names(uid): a member's name."""
    g = state["guilds"].get(str(gid)) if gid is not None else None
    me = state["members"].get(str(uid), {"points": 0, "claimed": 0})
    rows = sorted(((names(u), state["members"].get(str(u), {}).get("points", 0)) for u in member_ids),
                  key=lambda r: -r[1])
    return {"faction_war_points": g["points"] if g else 0,
            "user_info": {"faction_war_points": me["points"], "claimed_rewards": int(bool(me["claimed"]))},
            "our_rank": rank_of(state, gid) if gid is not None else 0, "start_war_prevented_time": 0,
            "members": [{"member_name": n, "faction_war_points": p} for n, p in rows]}


def slots_json(match: dict | None, gid, slots: dict) -> list[dict]:
    """A guild's fortress in a war, health out of 100."""
    out = []
    for s in SLOTS[1:]:
        health = slot_health(match, gid, s) if match else FORT_HEALTH
        out.append({"faction_id": str(gid), "slot_id": s, "card_id": int(slots.get(str(s), 0) or 0),
                    "health": math.ceil(health * 100 / FORT_HEALTH)})
    return out


def faction_war_json(state: dict, gid, name: str, uid, slots_of, names, now: int,
                     queue_message: str = "Waiting for a match...") -> dict | None:
    """faction_war for the player's guild: queued (defender "0"), matched, or None.
    slots_of(gid): that guild's {slot: card id}; names(uid): a member's name."""
    m = match_of(state, gid)
    if m is None and str(gid) not in state["queue"]:
        return None
    if m is None:
        return {"faction_war_id": "0", "message": queue_message, "queue_message": queue_message,
                "attacker_faction_id": str(gid), "attacker_faction_name": name,
                "defender_faction_id": "0", "defender_faction_name": "",
                "attacker_faction_points": 0, "defender_faction_points": 0,
                "battle_energy": 0, "max_battle_energy": ATTACKS, "battle_energy_update_time": now,
                "battle_energy_recharge_time": -1, "start_time": 0, "end_time": 0,
                "attacker_faction_fortress_slots": slots_json(None, gid, slots_of(gid)),
                "defender_faction_fortress_slots": [],
                "attacker_faction_battleground": {"id": "0", "end_time": 0},
                "defender_faction_battleground": {"id": "0", "end_time": 0},
                "attacker_faction_members": [], "defender_faction_members": []}
    me = m["members"].get(str(uid)) or {"energy": ATTACKS}     # a newcomer gets a full bar (fighter)
    if now < m["start"] + PREP_TIME:
        message = f"Preparing for war with {m['names'][other(m, gid)]}: attacks open after the preparation."
    else:
        message = f"At war with {m['names'][other(m, gid)]}!"

    def members(side):
        rows = [{"member_name": names(u), "current_war_points": f["points"], "wins": f["wins"],
                 "losses": f["losses"], "defend_wins": f["defend_wins"], "defend_losses": f["defend_losses"]}
                for u, f in m["members"].items() if f["guild"] == side]
        return sorted(rows, key=lambda r: -r["current_war_points"])
    out = {"faction_war_id": str(m["id"]), "message": message, "queue_message": queue_message,
           "battle_energy": me["energy"], "max_battle_energy": ATTACKS, "battle_energy_update_time": now,
           "battle_energy_recharge_time": -1, "start_time": m["start"], "end_time": m["end"]}
    for role, side in (("attacker", m["a"]), ("defender", m["d"])):
        out.update({f"{role}_faction_id": side, f"{role}_faction_name": m["names"][side],
                    f"{role}_faction_points": m["points"][side],
                    f"{role}_faction_fortress_slots": slots_json(m, side, slots_of(side)),
                    f"{role}_faction_battleground": {"id": "0", "end_time": 0},
                    f"{role}_faction_members": members(side)})
    return out


def leaderboard_json(rows: list[dict]) -> list[dict]:
    """faction_war_leaderboard entries."""
    return [{"faction_id": r["id"], "faction_name": r["name"], "faction_war_points": r["points"],
             "rank": r["rank"]} for r in rows]


def pick_defender(candidates: list, rng: random.Random):
    """The member who defends against an attack: any member with a deck to defend with."""
    return rng.choice(sorted(candidates, key=str)) if candidates else None
