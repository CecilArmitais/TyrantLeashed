"""Achievements (the game's "Objectives"): what the player is working towards, and the
rewards for finishing them. docs/protocol.md, "Achievements", has the evidence.

- achievements.xml has 884. Each has a type, a requirement (`req`), a reward, and may
  follow another (`achievement_req`): finishing one shows the next in its chain.
- The permanent chains, all open from the start (the recorded login listed exactly
  their first links):
  - 1 Upgrade ("Rise to Power"): upgrade a card to level N. Progress is the highest
    level an upgrade has reached (recorded: "2" after the first).
  - 2 Battle Rating ("Warlord"): the arena rating (recorded: 75, then 105).
  - 3 Fuse ("Fusion Master"): fusions made.
  - 4 Missions ("Savior of Acheron"): complete mission 5, 8, 17, 28, 41, 68.
  - 5 Salvage ("Scavenger"): SP earned by salvaging (recorded: "10" after 10 SP).
  - 8 Kongregate ("Kongregate to Victory"): log into Kongregate. Listed, as recorded,
    but there's no Kongregate offline.
  Finishing one pays its reward at once and the reply lists it in
  `completed_achievements` (recorded three times: the reward was in that reply's gold).
- Daily battle objectives (type 9 with a `difficulty`): three at a time, one of each
  difficulty, for `duration` (23 hours; recorded: end_time = the login + 82800). They
  count arena fights only (recorded: the tutorial missions didn't move them, two arena
  fights did), from what happened in the fight:
  - 1 activate a skill N times; 2 deal N damage with a skill (0: attack; recorded: 3
    and then 48, which the recorded fights' attacks add up to);
  - 3 win N fights after playing V cards of a faction/type in each;
  - 4 destroy N enemy cards (of a faction/type); 5 win N fights (against a faction's
    commander).
  A finished one waits for the player to tap Complete (status 1; the game sends
  completeAchievement), which pays it (ours: the client has the button for status 1,
  and nothing recorded shows which achievements used it). The next set comes when the
  23 hours are up.
- Event objectives: everything else (docs/protocol.md, "Achievements"):
  - Mission levels (type 4 with a `level`, e.g. "Mesmerize Mutant Lv. 2"): win the
    mission that many times. Always on (the recorded login listed Mesmerize Mutant's).
  - Legendary Quests and PvP challenges (`legendary_quest`): a type-12 parent whose
    `sub_achievement`s are chained objectives of types 4, 9 (PvP), 10 (items dropped in
    a kind of battle), 11 (objectives of a type completed) and 13 (PvP).
  - Undated ones are always on. Dated ones (`start_time`/`end_time`: the challenges
    and the 2015 Legendary Quest) are on only when content.toml picks one: that set
    (everything with the same dates) runs server-wide for its own length, then again.
    Side-mission levels are dated by their missions' release but have no end; they're
    always on.
  - Shown when the one before (`achievement_req`) is done and the mission
    (`mission_req`) is complete. A hidden one still counts in the background unless it
    has `no_background_progress` (recorded: an account short of mission 17 had the
    first challenge link done, and its follow-ups showing).
  - PvP ones wait for Complete like the dailies; finished in the background, they're
    paid at once. The others are paid as soon as they're reached.
- The list: every open objective, sorted by `order` then id, the first 20 (both
  recorded lists had 20, in that order, cutting the mutant levels short).
"""

from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from common import jsonfile

PERMANENT_TYPES = {1, 2, 3, 4, 5, 8}
DAILY = 9
PVP_TYPES = {9, 13}                          # counted from arena fights; wait for Complete
LIST_SIZE = 20                               # recorded: both logins listed exactly 20
# Battle kinds for item drops (type 10 `battle_type`), from the objectives' own texts:
# 1 "by defeating Missions" (`mutant_req`: "from any Mutant mission"), 4 "Raid battles",
# 7 "in Wars", 8 "Brawl battles".
MISSION, RAID, WAR, BRAWL = 1, 4, 7, 8
CARD_TYPES = {"commander": 1, "assault": 2, "structure": 4}      # the file's card_type numbers
PLAYER, ENEMY = 0, 1
COMMANDER_UID = {-1: PLAYER, -2: ENEMY}


@dataclass(frozen=True)
class Achievement:
    id: int
    type: int
    name: str
    desc: str
    order: int = 0
    after: int | None = None                 # achievement_req: shown once that one is done
    goal: int = 1                            # max_progress
    req: dict = field(default_factory=dict)  # as the replies show it: {"mission": "5"}, {"pvp_sub_type": {...}}
    reward: dict = field(default_factory=dict)   # as the replies show it: {"gold": "250", "card": "2999"}
    cards: tuple[tuple[int, int], ...] = ()  # reward cards: (card id, how many)
    icon: str | None = None
    portrait: str | None = None
    color: str | None = None
    duration: int | None = None
    difficulty: int | None = None
    timed: bool = False                      # start_time / end_time: an event's
    special: bool = False                    # a Legendary Quest's, or a mission level (mutants)
    start: int | None = None
    end: int | None = None
    mission_req: int | None = None           # shown once this mission is complete
    background: bool = True                  # counts while hidden (not no_background_progress)
    subs: tuple[int, ...] = ()               # type 12: its sub_achievements
    drop: dict | None = None                 # type 10: item_id, drop_rate, num, battle_type[, mutant_req]
    count_type: int | None = None            # type 11: objectives of this type completed
    portrait_bundle: str | None = None

    @property
    def daily(self) -> bool:
        return self.type == DAILY and self.difficulty is not None and "pvp_sub_type" in self.req and not self.timed

    @property
    def permanent(self) -> bool:
        return self.type in PERMANENT_TYPES and not self.timed and not self.special

    @property
    def mission_level(self) -> bool:
        return self.type == 4 and "level" in self.req

    @property
    def event(self) -> bool:
        return not self.permanent and not self.daily


def _min_level(desc: str) -> dict:
    """A raid level the drop needs, from the objective's own text ("Collect 100 Medals of
    Triumph from Raid level 5 or higher battles"): the data has no field for it."""
    m = re.search(r"level (\d+) or higher", desc, re.I)
    return {"min_level": m.group(1)} if m else {}


def load(asset_dir: Path) -> dict[int, Achievement]:
    f = Path(asset_dir) / "achievements.xml"
    if not f.is_file():
        return {}
    out = {}
    for a in ET.parse(f).getroot().findall("achievement"):
        text = lambda tag: (a.findtext(tag) or "").strip()
        num = lambda tag: int(text(tag)) if text(tag).lstrip("-").isdigit() else None
        if num("id") is None or num("type") is None:
            continue
        req_el = a.find("req")
        req, goal = {}, 1
        if req_el is not None:
            pvp = req_el.find("pvp_sub_type")
            if pvp is not None:
                req = {"pvp_sub_type": dict(pvp.attrib)}
                goal = int(pvp.get("wins") or pvp.get("value") or 1)
            else:
                req = {c.tag: (c.text or "").strip() for c in req_el if c.tag != "sub_achievement"}
                for key in ("upgrade_level", "battle_rating", "fusion", "salvage"):
                    if key in req and req[key].isdigit():
                        goal = int(req[key])
                if "level" in req and req["level"].isdigit():
                    goal = int(req["level"])
                if req.get("num", "").isdigit():                        # types 10 and 11
                    goal = int(req["num"])
        subs = tuple(int(x.text) for x in (req_el.findall("sub_achievement") if req_el is not None else ())
                     if (x.text or "").strip().isdigit())
        if subs:
            goal = len(subs)
        reward, cards = {}, []
        for c in (a.find("reward") if a.find("reward") is not None else []):
            if c.tag == "card":
                cid = c.get("id") or (c.text or "").strip()
                if cid.isdigit():
                    cards.append((int(cid), int(c.get("amount") or 1)))
                    reward.setdefault("card", cid)
            elif c.tag in ("gold", "wb", "sp") and (c.text or "").strip().isdigit():
                reward[c.tag] = c.text.strip()
        out[num("id")] = Achievement(
            num("id"), num("type"), text("name"), text("desc"), num("order") or 0, num("achievement_req"), goal,
            req, reward, tuple(cards), text("icon") or None, text("portrait") or None, text("color") or None,
            num("duration"), num("difficulty"), timed=bool(text("start_time") or text("end_time")),
            special=bool(text("legendary_quest")) or (req_el is not None and req_el.find("level") is not None),
            start=num("start_time"), end=num("end_time"), mission_req=num("mission_req"),
            background=text("no_background_progress") != "1", subs=subs,
            drop={**{k: req[k] for k in ("item_id", "drop_rate", "num", "battle_type", "mutant_req") if k in req},
                  **_min_level(text("desc"))}
            if num("type") == 10 and "item_id" in req else None,
            count_type=int(req["achievement_type"]) if num("type") == 11 and req.get("achievement_type", "").isdigit()
            else None, portrait_bundle=text("portrait_bundle") or None)
    return out


# ---- the save ------------------------------------------------------------------------------------
# player.achievements = {"done": [ids], "counts": {"upgrade_level", "fusions", "salvage"},
#   "daily": {"ids": [...], "end_time": t, "progress": {id: n}, "ready": [ids], "claimed": [ids]},
#   "rotations": n}

def state(player) -> dict:
    if player.achievements is None:
        player.achievements = {}
    s = player.achievements
    s.setdefault("done", [])
    s.setdefault("counts", {})
    s.setdefault("daily", {})
    s.setdefault("rotations", 0)
    ev = s.setdefault("event", {})
    ev.setdefault("progress", {})
    ev.setdefault("ready", [])
    return s


# ---- which event objectives are on ---------------------------------------------------------------

@dataclass(frozen=True)
class EventContext:
    """The event objectives on now: the standing ones and the challenge's run, if any."""
    on: frozenset = frozenset()
    challenge: frozenset = frozenset()       # the run's objectives (reset each run)
    run: dict | None = None                  # {"id", "start", "end"} (end None: no end)


def standing_events(defs: dict[int, Achievement]) -> frozenset:
    """Event objectives that are always on: the undated ones and the mission levels."""
    return frozenset(a.id for a in defs.values() if a.event and (not a.timed or a.mission_level))


def challenge_set(defs: dict[int, Achievement], cid: int) -> frozenset:
    """A dated objective's set: every dated objective with the same dates (mission levels
    aside, which are always on)."""
    a = defs.get(cid)
    if a is None or not a.timed or a.mission_level:
        return frozenset()
    return frozenset(b.id for b in defs.values() if b.timed and not b.mission_level and b.event
                     and (b.start, b.end) == (a.start, a.end))


def challenge_length(defs: dict[int, Achievement], cid: int) -> int | None:
    """Its own length (end - start), or None for an open-ended one."""
    a = defs.get(cid)
    return a.end - a.start if a is not None and a.start is not None and a.end is not None else None


class ChallengeStore:
    """state/challenge.json: the content.toml challenge's current run, server-wide."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            return jsonfile.read(self.path, {})
        except (OSError, ValueError):       # a broken file: the challenge starts over
            return {}

    def save(self, run: dict) -> None:
        jsonfile.write(self.path, run)

    def current(self, defs: dict[int, Achievement], cid: int, now: int) -> dict | None:
        """The run of challenge `cid` going on now: started the first time it's needed, for
        its own length; once over, the next starts. None for no challenge."""
        if not cid or not challenge_set(defs, cid):
            return None
        run = self.load()
        if run.get("id") != cid or (run.get("end") is not None and now >= run["end"]):
            length = challenge_length(defs, cid)
            run = {"id": cid, "start": now, "end": now + length if length else None}
            self.save(run)
        return run


def context(defs: dict[int, Achievement], run: dict | None = None) -> EventContext:
    challenge = challenge_set(defs, run["id"]) if run else frozenset()
    return EventContext(standing_events(defs) | challenge, challenge, run)


def sync_run(player, ctx: EventContext) -> None:
    """A new run of the challenge starts the player's progress in it over."""
    s = state(player)
    ev = s["event"]
    key = [ctx.run["id"], ctx.run["start"]] if ctx.run else None
    if ev.get("run") == key:
        return
    old = set(ev.get("run_ids") or [])
    if old:
        s["done"] = [i for i in s["done"] if i not in old]
        ev["progress"] = {k: v for k, v in ev["progress"].items() if int(k) not in old}
        ev["ready"] = [i for i in ev["ready"] if i not in old]
    ev["run"], ev["run_ids"] = key, sorted(ctx.challenge)


# ---- progress: what's shown and what counts ------------------------------------------------------

def _mission_done(player, mid) -> bool:
    m = (player.missions or {}).get(str(mid))
    return bool(m and m.get("complete"))


def shown(a: Achievement, player, done: set) -> bool:
    """On the list (if it's on): not done, the one before done, its mission complete."""
    return a.id not in done and (a.after is None or a.after in done) and \
        (not a.mission_req or _mission_done(player, a.mission_req))


def counting(a: Achievement, player, done: set) -> bool:
    """Moving: shown, or hidden only by its mission and allowed to count in the background."""
    return a.id not in done and (a.after is None or a.after in done) and \
        (a.background or not a.mission_req or _mission_done(player, a.mission_req))


def event_progress(a: Achievement, player) -> int:
    s = state(player)
    if a.type == 4:
        m = (player.missions or {}).get(str(a.req.get("mission"))) or {}
        if a.mission_level:
            return int(m.get("number") or 0)               # a mission level: times won
        return 1 if m.get("complete") else 0
    if a.type == 11:
        return s["counts"].get(f"objectives_{a.count_type}", 0)
    if a.type == 12:
        done = set(s["done"])
        return sum(1 for x in a.subs if x in done)
    return s["event"]["progress"].get(str(a.id), 0)


def count(player, key: str, value: int, mode: str = "add") -> None:
    """Records upgrade levels ("max"), fusions and salvaged SP ("add")."""
    c = state(player)["counts"]
    c[key] = max(c.get(key, 0), value) if mode == "max" else c.get(key, 0) + value


def progress(a: Achievement, player) -> int:
    """How far the player is towards a permanent achievement."""
    c = state(player)["counts"]
    if a.type == 1:
        return c.get("upgrade_level", 0)
    if a.type == 2:
        return int((player.arena or {}).get("rating", 0))
    if a.type == 3:
        return c.get("fusions", 0)
    if a.type == 4:
        m = (player.missions or {}).get(str(a.req.get("mission")))
        return 1 if m and m.get("complete") else 0
    if a.type == 5:
        return c.get("salvage", 0)
    return 0


# ---- the list, dailies, finishing and pay --------------------------------------------------------

def open_permanent(defs: dict[int, Achievement], player) -> list[Achievement]:
    """The permanent achievements on the list: not done, and the one before (if any) done."""
    done = set(state(player)["done"])
    return [a for a in defs.values() if a.permanent and a.id not in done and (a.after is None or a.after in done)]


def ensure_dailies(defs: dict[int, Achievement], player, now: int, rng: random.Random) -> bool:
    """A fresh set of daily objectives when there's none or the last one ran out: one of
    each difficulty. True if it changed."""
    d = state(player)["daily"]
    if d.get("ids") and int(d.get("end_time", 0)) > now:
        return False
    pool = sorted((a for a in defs.values() if a.daily), key=lambda a: a.id)
    picks = []
    for diff in sorted({a.difficulty for a in pool}, reverse=True):
        options = [a for a in pool if a.difficulty == diff]
        picks.append(rng.choice(options))
    duration = min((a.duration for a in picks if a.duration), default=82800)
    state(player)["daily"] = {"ids": [a.id for a in picks], "end_time": now + duration, "progress": {},
                              "ready": [], "claimed": []}
    state(player)["rotations"] += 1
    return True


def complete_ready(defs: dict[int, Achievement], player, cap_sp: int,
                   ctx: EventContext | None = None) -> list[Achievement]:
    """Finishes every permanent achievement whose goal is met (a whole chain if need be),
    and the event objectives that pay at once (not PvP ones while shown), paying each:
    the ones finished, in order."""
    s = state(player)
    ctx = ctx if ctx is not None else context(defs)
    sync_run(player, ctx)
    finished = []
    while True:
        done = set(s["done"])
        due = [a for a in open_permanent(defs, player) if a.type != 8 and progress(a, player) >= a.goal]
        for aid in ctx.on:
            a = defs[aid]
            if not counting(a, player, done):
                continue
            if a.type in PVP_TYPES:
                if aid in s["event"]["ready"] and not shown(a, player, done):
                    due.append(a)                          # finished in the background
            elif event_progress(a, player) >= a.goal:
                due.append(a)
        if not due:
            return finished
        for a in sorted(due, key=lambda a: (a.order, a.id)):
            s["done"].append(a.id)
            if a.id in s["event"]["ready"]:
                s["event"]["ready"].remove(a.id)
                _count_done(player, a)
            pay(a, player, cap_sp)
            finished.append(a)


def _count_done(player, a: Achievement) -> None:
    """Type 11 objectives count completed objectives by type (recorded text: "Complete 30
    daily Battle objectives")."""
    c = state(player)["counts"]
    c[f"objectives_{a.type}"] = c.get(f"objectives_{a.type}", 0) + 1


def pay(a: Achievement, player, cap_sp: int) -> None:
    gold = int(a.reward.get("gold", 0))
    player.gold += gold
    if gold:
        player.gold_earned = (player.gold_earned or 0) + gold      # recorded: reward gold counts
    player.war_bonds += int(a.reward.get("wb", 0))
    if a.reward.get("sp"):
        player.salvage = min(cap_sp, (player.salvage or 0) + int(a.reward["sp"]))
    for cid, n in a.cards:
        player.add_card(cid, n)


def claim(defs: dict[int, Achievement], player, aid, cap_sp: int, now: int,
          ctx: EventContext | None = None) -> Achievement:
    """completeAchievement: pays a finished daily or PvP event objective."""
    s = state(player)
    d = s["daily"]
    try:
        aid = int(aid)
    except (TypeError, ValueError):
        raise ValueError("That objective doesn't exist.")
    a = defs.get(aid)
    ctx = ctx if ctx is not None else context(defs)
    if a is not None and aid in ctx.on and a.type in PVP_TYPES and aid not in d.get("ids", []):
        if aid in s["done"]:
            raise ValueError(f"{a.name} has already been completed.")
        if aid not in s["event"]["ready"] or not shown(a, player, set(s["done"])):
            raise ValueError(f"{a.name} isn't finished yet.")
        s["event"]["ready"].remove(aid)
        s["done"].append(aid)
        _count_done(player, a)
        pay(a, player, cap_sp)
        return a
    if a is None or aid not in d.get("ids", []):
        raise ValueError("That objective isn't on your list.")
    if aid in d.get("claimed", []):
        raise ValueError(f"{a.name} has already been completed.")
    if aid not in d.get("ready", []):
        raise ValueError(f"{a.name} isn't finished yet.")
    d["claimed"].append(aid)
    _count_done(player, a)
    pay(a, player, cap_sp)
    return a


# ---- arena fights --------------------------------------------------------------------------------

@dataclass
class FightStats:
    won: bool
    skill_uses: dict = field(default_factory=dict)      # (side, skill) -> times
    skill_values: dict = field(default_factory=dict)    # (side, skill) -> amount ("0": attack damage)
    skill_targets: dict = field(default_factory=dict)   # (side, skill, target card id) -> times
    plays: list = field(default_factory=list)            # the player's cards played
    kills: list = field(default_factory=list)            # (side, card) destroyed
    enemy_commander: object = None


def side_of(uid: int, first_enemy_uid: int = 101) -> int:
    """Whose a battle uid is: commanders are -1 and -2, the cards 1.. and 101.., and the
    dominions 0 and 100 (battle/engine.py)."""
    if uid in COMMANDER_UID:
        return COMMANDER_UID[uid]
    return ENEMY if uid >= first_enemy_uid - 1 else PLAYER


def fight_stats(battle, won: bool) -> FightStats:
    """What happened in an engine battle, from its turn record (the same data the game
    is sent)."""
    att, dfn = battle.sides
    cards = {**{uid: c for uid, c in att.cards.items()}, **{uid: c for uid, c in dfn.cards.items()}}
    stats = FightStats(won, enemy_commander=dfn.commander.card)
    for turn in battle.turns.values():
        for section in ("begin", "upkeep", "actions", "end"):
            for a in turn.get(section) or []:
                if not isinstance(a, dict) or "skill_id" not in a:
                    continue
                key = (side_of(int(a["card_uid"])), str(a["skill_id"]))
                stats.skill_uses[key] = stats.skill_uses.get(key, 0) + 1
                stats.skill_values[key] = stats.skill_values.get(key, 0) + int(a.get("value") or 0)
                for t in a.get("targets") or []:
                    card = cards.get(int(t)) if str(t).lstrip("-").isdigit() else None
                    if card is not None:
                        tkey = (*key, card.id)
                        stats.skill_targets[tkey] = stats.skill_targets.get(tkey, 0) + 1
        for p in turn.get("plays") or []:
            uid = int(p["card_uid"] if isinstance(p, dict) else p[0])
            if side_of(uid) == PLAYER and uid in cards:
                stats.plays.append(cards[uid])
        for section, side in (("attack_kill", PLAYER), ("defend_kill", ENEMY)):
            for uid in turn.get(section) or []:
                if int(uid) in cards:
                    stats.kills.append((side, cards[int(uid)]))
    return stats


def _matches(card, faction: str | None, card_type: str | None) -> bool:
    if faction and str(card.faction) != str(faction):
        return False
    if card_type and CARD_TYPES.get(card.type) != int(card_type):
        return False
    return True


def fight_progress(a: Achievement, stats: FightStats) -> int:
    """How much one arena fight moves a daily objective."""
    p = a.req.get("pvp_sub_type") or {}
    kind, side = p.get("type_id"), PLAYER if p.get("is_player") == "1" else ENEMY
    skill, faction, card_type = p.get("skill_id"), p.get("type"), p.get("card_type")
    if kind == "1":
        return stats.skill_uses.get((PLAYER, str(skill)), 0)
    if kind == "2":
        return stats.skill_values.get((PLAYER, str(skill)), 0)
    if kind == "3":
        played = sum(1 for c in stats.plays if _matches(c, faction, card_type))
        return 1 if stats.won and played >= int(p.get("value") or 1) else 0
    if kind == "4":
        # is_player 0: the enemy's cards destroyed.
        return sum(1 for s, c in stats.kills if s == (ENEMY if side == ENEMY else PLAYER)
                   and _matches(c, faction, card_type))
    if kind == "5":
        return 1 if stats.won and (not faction or str(stats.enemy_commander.faction) == str(faction)) else 0
    if kind == "6":
        # "Protect the Mythic Orbo in 20 battles and win": the skill used on that card
        # (the Orbo has no Protect of its own), `value` times in a won fight.
        times = stats.skill_targets.get((PLAYER, str(skill), int(p.get("card_id") or 0)), 0)
        return 1 if stats.won and times >= int(p.get("value") or 1) else 0
    if kind == "7":
        # "Win 250 battles while having activated Heal."
        return 1 if stats.won and stats.skill_uses.get((PLAYER, str(skill)), 0) >= int(p.get("value") or 1) else 0
    return 0


def record_fight(defs: dict[int, Achievement], player, stats: FightStats, now: int,
                 ctx: EventContext | None = None) -> list[Achievement]:
    """Moves the daily objectives and the PvP event ones by an arena fight: the ones it
    finished (now ready to claim; hidden event ones are paid by complete_ready)."""
    s = state(player)
    ctx = ctx if ctx is not None else context(defs)
    sync_run(player, ctx)
    ready = []
    done, ev = set(s["done"]), s["event"]
    for aid in sorted(ctx.on):
        a = defs[aid]
        if a.type not in PVP_TYPES or aid in ev["ready"] or not counting(a, player, done):
            continue
        n = ev["progress"].get(str(aid), 0) + fight_progress(a, stats)
        ev["progress"][str(aid)] = min(n, a.goal)
        if n >= a.goal:
            ev["ready"].append(aid)
            if shown(a, player, done):
                ready.append(a)
    d = s["daily"]
    if not d.get("ids") or int(d.get("end_time", 0)) <= now:
        return ready
    for aid in d["ids"]:
        a = defs.get(aid)
        if a is None or aid in d["ready"] or aid in d["claimed"]:
            continue
        n = d["progress"].get(str(aid), 0) + fight_progress(a, stats)
        d["progress"][str(aid)] = min(n, a.goal)
        if n >= a.goal:
            d["ready"].append(aid)
            ready.append(a)
    return ready


# ---- item drops (type 10) ------------------------------------------------------------------------

def drops(defs: dict[int, Achievement], player, battle_type: int, won: bool, rng: random.Random,
          ctx: EventContext | None = None, mutant: bool = False, level: int | None = None) -> dict[int, int]:
    """Items a battle drops for the type-10 objectives counting now (each its own
    `drop_rate` roll): {item id: how many}. Missions drop on a win ("by defeating
    Missions"); raid, war and brawl fights on every fight ("from Raid battles": a raid
    fight is nearly always lost to the boss). `level`: a raid's, for the drops that need a
    level ("Raid level 5 or higher"). The caller adds them to the inventory; the
    objectives' progress is counted here."""
    if not won and battle_type == MISSION:
        return {}
    s = state(player)
    ctx = ctx if ctx is not None else context(defs)
    sync_run(player, ctx)
    done, ev, gains = set(s["done"]), s["event"], {}
    for aid in sorted(ctx.on):
        a = defs[aid]
        if not a.drop or int(a.drop.get("battle_type") or 0) != battle_type or not counting(a, player, done):
            continue
        if a.drop.get("mutant_req") == "1" and not mutant:
            continue
        if a.drop.get("min_level") and (level or 0) < int(a.drop["min_level"]):
            continue
        if rng.random() < float(a.drop.get("drop_rate") or 0):
            iid = int(a.drop["item_id"])
            gains[iid] = gains.get(iid, 0) + 1
            ev["progress"][str(aid)] = min(ev["progress"].get(str(aid), 0) + 1, a.goal)
    return gains


def reward_item(gains: dict[int, int]):
    """A battle reward's `item`: {"<item id>": n} (the client shows the first); [] for none,
    as recorded."""
    return {str(i): n for i, n in gains.items()} or []


# ---- the replies ---------------------------------------------------------------------------------

def entry(a: Achievement, progress_now: int, status: int, end_time: int | None = None) -> dict:
    """A user_achievements entry, as recorded."""
    e = {"achievement_id": a.id, "progress": str(progress_now), "status": str(status), "name": a.name,
         "description": a.desc, "icon": a.icon, "portrait": a.portrait}
    if a.portrait_bundle:
        e["portrait_bundle"] = a.portrait_bundle            # recorded for Mesmerize Mutant's
    e.update({"max_progress": str(a.goal), "type": str(a.type), "req": a.req, "reward": a.reward})
    if a.color:
        e["color"] = a.color
    if end_time is not None:
        e["end_time"] = str(end_time)
    return e


def user_achievements(defs: dict[int, Achievement], player, ctx: EventContext | None = None) -> dict:
    """The open objectives, sorted by order then id, the first LIST_SIZE (as recorded)."""
    s = state(player)
    ctx = ctx if ctx is not None else context(defs)
    sync_run(player, ctx)
    rows = []
    for a in open_permanent(defs, player):
        rows.append((a, entry(a, progress(a, player), 0)))
    d = s["daily"]
    for aid in d.get("ids", []):
        a = defs.get(aid)
        if a is None or aid in d.get("claimed", []):
            continue
        rows.append((a, entry(a, d["progress"].get(str(aid), 0), 1 if aid in d.get("ready", []) else 0,
                              d.get("end_time"))))
    done = set(s["done"])
    run_end = ctx.run.get("end") if ctx.run else None
    for aid in ctx.on:
        a = defs[aid]
        if not shown(a, player, done) or aid in d.get("ids", []):
            continue
        status = 1 if a.type in PVP_TYPES and aid in s["event"]["ready"] else 0
        rows.append((a, entry(a, min(event_progress(a, player), a.goal), status,
                              run_end if aid in ctx.challenge else None)))
    rows.sort(key=lambda r: (r[0].order, r[0].id))
    # Ones waiting for Complete always make the cut (ours: nothing recorded had one), or a
    # finished objective could sit out of reach.
    waiting = [r for r in rows if r[1]["status"] == "1"]
    keep = waiting[:LIST_SIZE] + [r for r in rows if r[1]["status"] != "1"][:max(0, LIST_SIZE - len(waiting))]
    keep.sort(key=lambda r: (r[0].order, r[0].id))
    return {str(a.id): e for a, e in keep}


def completed_json(done: list[Achievement]) -> list[dict]:
    """completed_achievements, as recorded."""
    out = []
    for a in done:
        e = {"id": str(a.id), "type": str(a.type), "order": str(a.order), "name": a.name, "desc": a.desc,
             "icon": a.icon}
        if a.portrait:
            e["portrait"] = a.portrait
        e.update({"req": a.req, "reward": a.reward})
        out.append(e)
    return out
