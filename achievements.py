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
- Not here yet: event achievements (mutant missions, timed battle events), and the
  Legendary Quests (item drops, achievement counts).
"""

from __future__ import annotations

import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

PERMANENT_TYPES = {1, 2, 3, 4, 5, 8}
DAILY = 9
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

    @property
    def daily(self) -> bool:
        return self.type == DAILY and self.difficulty is not None and "pvp_sub_type" in self.req and not self.timed

    @property
    def permanent(self) -> bool:
        return self.type in PERMANENT_TYPES and not self.timed and not self.special


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
            special=bool(text("legendary_quest")) or (req_el is not None and req_el.find("level") is not None))
    return out


# ---- the save ---------------------------------------------------------------------------------------
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
    return s


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


def complete_ready(defs: dict[int, Achievement], player, cap_sp: int) -> list[Achievement]:
    """Finishes every permanent achievement whose goal is met (a whole chain if need be),
    paying each: the ones finished, in order."""
    s = state(player)
    finished = []
    while True:
        due = [a for a in open_permanent(defs, player) if a.type != 8 and progress(a, player) >= a.goal]
        if not due:
            return finished
        for a in sorted(due, key=lambda a: (a.order, a.id)):
            s["done"].append(a.id)
            pay(a, player, cap_sp)
            finished.append(a)


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


# ---- arena fights -----------------------------------------------------------------------------------

@dataclass
class FightStats:
    won: bool
    skill_uses: dict = field(default_factory=dict)      # (side, skill) -> times
    skill_values: dict = field(default_factory=dict)    # (side, skill) -> amount ("0": attack damage)
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
    return 0


def record_fight(defs: dict[int, Achievement], player, stats: FightStats, now: int) -> list[Achievement]:
    """Moves the daily objectives by an arena fight: the ones it finished (now ready to
    claim)."""
    d = state(player)["daily"]
    if not d.get("ids") or int(d.get("end_time", 0)) <= now:
        return []
    ready = []
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


def claim(defs: dict[int, Achievement], player, aid, cap_sp: int, now: int) -> Achievement:
    """completeAchievement: pays a finished daily objective."""
    d = state(player)["daily"]
    try:
        aid = int(aid)
    except (TypeError, ValueError):
        raise ValueError("That objective doesn't exist.")
    a = defs.get(aid)
    if a is None or aid not in d.get("ids", []):
        raise ValueError("That objective isn't on your list.")
    if aid in d.get("claimed", []):
        raise ValueError(f"{a.name} has already been completed.")
    if aid not in d.get("ready", []):
        raise ValueError(f"{a.name} isn't finished yet.")
    d["claimed"].append(aid)
    pay(a, player, cap_sp)
    return a


# ---- the replies --------------------------------------------------------------------------------------

def entry(a: Achievement, progress_now: int, status: int, end_time: int | None = None) -> dict:
    """A user_achievements entry, as recorded."""
    e = {"achievement_id": a.id, "progress": str(progress_now), "status": str(status), "name": a.name,
         "description": a.desc, "icon": a.icon, "portrait": a.portrait, "max_progress": str(a.goal),
         "type": str(a.type), "req": a.req, "reward": a.reward}
    if a.color:
        e["color"] = a.color
    if end_time is not None:
        e["end_time"] = str(end_time)
    return e


def user_achievements(defs: dict[int, Achievement], player) -> dict:
    out = {}
    for a in sorted(open_permanent(defs, player), key=lambda a: (a.order, a.id)):
        out[str(a.id)] = entry(a, progress(a, player), 0)
    d = state(player)["daily"]
    for aid in d.get("ids", []):
        a = defs.get(aid)
        if a is None or aid in d.get("claimed", []):
            continue
        out[str(aid)] = entry(a, d["progress"].get(str(aid), 0), 1 if aid in d.get("ready", []) else 0,
                              d.get("end_time"))
    return out


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
