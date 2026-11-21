"""Raids: a boss with a big health pool, fought by a guild level after level, each
member on their own raid energy. The run lives in the guild (guilds.py).
docs/protocol.md, "Raids", has the evidence.

The data (raids_x42.xml, 161 raids) gives each raid its boss commander, a deck pool
("will pick [deck size] random cards from this"), fortress cards, health and
health_per_level, how long a level lasts (`duration`), a battleground effect, and reward
tiers, each with a damage and a level.

The fan wiki (Raid page): raids last three days; a level not cleared in time refills
the boss's health; each level adds 1000 health and stronger cards; raid energy holds
25 attacks, starts at 13 and refills one per 30 minutes while the raid runs (recorded:
max_battle_energy 25, battle_energy_recharge_time 1800); raids aren't in surge mode.

What the optimizer (reference/tuo) supplies:
- The boss's cards get stronger by level: at level L of N, ceil(U * (L - 1) / (N - 1))
  upgrades go to random cards (commander and fortresses too), where U is every upgrade
  the deck could take; at level N all are at their top level.
- A fight's raid damage: 15 + the boss's non-summoned cards destroyed - 10 * the boss
  commander's health left / its max health (its "ARD" score).
"""

from __future__ import annotations

import math
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Tier:
    id: int
    damage: int
    level: int
    rewards: tuple[tuple[str, int, int], ...]     # (kind: card / mini_pack / pack, id, amount)


@dataclass(frozen=True)
class Raid:
    id: int
    name: str
    desc: str
    level_desc: str
    commander: int
    deck: tuple[int, ...]                 # the pool
    deck_size: int
    fortresses: tuple[int, ...] = ()
    fortress_count: int = 0
    health: int = 0
    health_per_level: int = 1000
    duration: int = 28800
    battleground_effect: int | None = None
    enemy_battleground_effect: int | None = None
    start_time: int | None = None
    end_time: int | None = None
    pictures: tuple[tuple[str, str], ...] = ()
    tiers: tuple[Tier, ...] = ()
    levels: int = 0                       # the level by which the boss's cards are all at their top

    def max_health(self, level: int, scale: float = 1.0) -> int:
        return max(1, round((self.health + self.health_per_level * (level - 1)) * scale))

    def picture(self, key: str) -> str | None:
        return dict(self.pictures).get(key)


def load(asset_dir: Path, file_name: str | None = None) -> dict[int, Raid]:
    files = [Path(asset_dir) / file_name] if file_name else sorted(Path(asset_dir).glob("raids*.xml"))
    if not files or not files[-1].is_file():
        return {}
    out = {}
    for r in ET.parse(files[-1]).getroot().findall("raid"):
        text = lambda tag, node=r: (node.findtext(tag) or "").strip()
        num = lambda tag, node=r: int(text(tag, node)) if text(tag, node).lstrip("-").isdigit() else None
        if num("id") is None or num("commander") is None:
            continue
        tiers = []
        for t in r.findall("rewards/tier"):
            rewards = tuple((c.tag, int(c.get("id")), int(c.get("amount") or 1))
                            for c in t if c.tag in ("card", "mini_pack", "pack") and (c.get("id") or "").isdigit())
            tiers.append(Tier(num("id", t) or 0, num("damage", t) or 0, num("level", t) or 0, rewards))
        deck = tuple(int(c.text) for c in r.findall("deck/card") if (c.text or "").strip().isdigit())
        out[num("id")] = Raid(
            num("id"), text("name"), text("desc"), text("level_desc"), num("commander"), deck,
            num("deck_size") or min(10, len(deck)),
            fortresses=tuple(int(c.text) for c in r.findall("fortress/card") if (c.text or "").strip().isdigit()),
            fortress_count=num("fortress_count") or 0,
            health=num("health") or 0, health_per_level=num("health_per_level") or 1000,
            duration=num("duration") or 28800,
            battleground_effect=num("battleground_effect"), enemy_battleground_effect=num("enemy_battleground_effect"),
            start_time=num("start_time"), end_time=num("end_time"),
            pictures=tuple((k, text(k)) for k in ("desc_image", "desc_bundle", "web_picture", "web_link") if text(k)),
            tiers=tuple(sorted(tiers, key=lambda t: t.level)),
            levels=max((t.level for t in tiers), default=0) or num("raid_levels") or 10)
    return out


# ---- the boss's deck ---------------------------------------------------------------------------------

def boss_deck(raid: Raid, level: int, cards, rng: random.Random):
    """(commander, fortresses, cards) for a fight at this level: deck_size cards from the
    pool, then the level's share of upgrades spread at random (the optimizer's rule)."""
    pool = list(raid.deck)
    rng.shuffle(pool)
    picks = pool[:raid.deck_size]
    forts = list(raid.fortresses)
    if raid.fortress_count and len(forts) > raid.fortress_count:
        rng.shuffle(forts)
        forts = forts[:raid.fortress_count]
    slots = [cards[raid.commander]] + [cards[f] for f in forts] + [cards[c] for c in picks]
    top = lambda c: _top_level(cards, c)
    opportunities = sum(top(c).level - c.level for c in slots)
    n = max(2, raid.levels)
    points = opportunities if level >= n else math.ceil(opportunities * (max(1, level) - 1) / (n - 1))
    upgradable = list(range(len(slots)))
    while points and upgradable:
        i = rng.choice(upgradable)
        nxt = cards.next_level(slots[i].id)
        if nxt is None:
            upgradable.remove(i)
            continue
        slots[i] = nxt
        points -= 1
    return slots[0], slots[1:1 + len(forts)], slots[1 + len(forts):]


def _top_level(cards, card):
    while True:
        nxt = cards.next_level(card.id)
        if nxt is None:
            return card
        card = nxt


# ---- a fight's damage and the rewards -------------------------------------------------------------------

def fight_damage(destroyed: int, boss_hp: int, boss_max_hp: int) -> int:
    """Raid damage for one fight (the optimizer's formula), at least 0."""
    return max(0, round(15 + destroyed - 10 * max(0, boss_hp) / max(1, boss_max_hp)))


def earned_tiers(raid: Raid, damage: int, level: int) -> list[Tier]:
    """The reward tiers reached: the player's damage and the raid's level both at least
    the tier's (ours; the client shows both)."""
    return [t for t in raid.tiers if damage >= t.damage and level >= t.level]


# ---- a raid run: the guild's state --------------------------------------------------------------------
# guild["raid"] = {"id", "start", "end", "decay", "level", "level_start", "level_end", "hp",
#   "members": {user_id: {"damage", "fights", "claimed", "energy", "energy_updated"}}}
# The boss (its level, health and the level's clock) is the guild's; each member has their
# own raid energy, damage and reward claim.

ENERGY_MAX = 25            # the wiki; recorded max_battle_energy 25
ENERGY_START = 13          # the wiki: "starts at 13"
ENERGY_EVERY = 1800        # recorded battle_energy_recharge_time; the wiki: one every 30 minutes
REFILL_WB = 5              # the wiki: refills "for 5 per attack"
CLAIM_WAIT = 600           # the client's default claim_reward_prevention
CLAIM_DAYS = 1             # ours: how long after the raid its reward can be claimed
FALLBACK_REWARD_SET = 4500 # reward packs whose set has no level-1 cards left draw from here instead


class RaidError(Exception):
    """Why a raid action can't happen, in words the player can read."""


def new_run(raid: Raid, now: int, days: int, scale: float) -> dict:
    end = now + days * 86400
    return {"id": raid.id, "start": now, "end": end, "decay": end + CLAIM_DAYS * 86400, "level": 1,
            "level_start": 0, "level_end": 0, "hp": raid.max_health(1, scale), "members": {}}


def member(run: dict, user_id, now: int) -> dict:
    """A member's part of the run, made when they first take part (raid energy starts
    at 13 then)."""
    return run["members"].setdefault(str(user_id), {"damage": 0, "fights": 0, "claimed": 0,
                                                     "energy": ENERGY_START, "energy_updated": int(now)})


def refresh(run: dict, raid: Raid, now: int, scale: float) -> int | None:
    """Brings the boss up to now: a level not cleared in time refills its health.
    Returns that level, if it was lost just now."""
    if run["level_end"] and now >= run["level_end"]:
        run["hp"] = raid.max_health(run["level"], scale)          # not cleared in time: full health again
        run["level_start"] = run["level_end"] = 0
        return run["level"]
    return None


def refresh_member(run: dict, m: dict, now: int) -> None:
    """Raid energy back over time, only while the raid runs."""
    stop = min(now, run["end"])
    if m["energy"] < ENERGY_MAX and stop > m["energy_updated"]:
        gained = (stop - m["energy_updated"]) // ENERGY_EVERY
        m["energy"] = min(ENERGY_MAX, m["energy"] + gained)
        m["energy_updated"] = m["energy_updated"] + gained * ENERGY_EVERY if m["energy"] < ENERGY_MAX else stop
    elif m["energy"] >= ENERGY_MAX:
        m["energy_updated"] = max(m["energy_updated"], stop)


def can_fight(run: dict, m: dict, now: int) -> None:
    if m["energy"] < 1:
        raise RaidError("You're out of raid energy.")
    if now >= run["end"] and not run["level_end"] > now:
        raise RaidError("The raid is over: no new levels can be started.")


def record_fight(run: dict, m: dict, raid: Raid, damage: int, now: int, scale: float) -> tuple[bool, bool]:
    """A member's fight against the boss: (it started the level's clock, it cleared the level)."""
    m["energy"] -= 1
    m["fights"] += 1
    started = not run["level_end"]
    if started:                                                   # the first fight starts the level's clock
        run["level_start"], run["level_end"] = now, now + raid.duration
    m["damage"] += damage
    run["hp"] -= damage
    if run["hp"] > 0:
        return started, False
    run["level"] += 1
    run["hp"] = raid.max_health(run["level"], scale)
    run["level_start"] = run["level_end"] = 0
    return started, True


def claimable(run: dict, m: dict, now: int) -> bool:
    level_end = run["level_end"] or run["end"]
    return now >= max(run["end"], level_end) + CLAIM_WAIT and now < run["decay"] and not m["claimed"]


# ---- the replies ------------------------------------------------------------------------------------------

def tier_json(tier: Tier, mini_packs: dict) -> dict:
    out = {"id": str(tier.id), "damage": tier.damage, "level": tier.level}
    for kind, rid, amount in tier.rewards[:1]:                    # the client shows one per tier
        if kind == "card":
            out["card"] = {"id": rid, "amount": amount}
        elif kind == "pack":
            out["pack"] = {"id": rid}
        else:
            mp = mini_packs.get(str(rid)) or {}
            out["mini_pack"] = {"id": rid, "name": mp.get("name", ""), "desc": mp.get("desc", ""),
                                "web_picture": mp.get("web_picture") or mp.get("picture", "")}
    return out


def current_raids(raid: Raid, run: dict, mini_packs: dict) -> list:
    """current_raids, as the client reads it (ServiceRaidEventData)."""
    p = raid.picture
    return [{"id": raid.id, "name": raid.name, "desc": raid.desc, "desc_image": p("desc_image") or "",
             "desc_bundle": int(p("desc_bundle") or 0), "level_desc": raid.level_desc,
             "web_picture": p("web_picture") or "", "web_link": p("web_link") or "",
             "start_time": run["start"], "end_time": run["end"], "decay_time": run["decay"],
             "no_new_raid_time": run["end"], "claim_reward_prevention": CLAIM_WAIT, "health": raid.health,
             "rewards": {"tier": [tier_json(t, mini_packs) for t in raid.tiers]}}]


def raid_info(raid: Raid, run: dict, user_id, names: dict, now: int, scale: float) -> list:
    """raid_info, as the client reads it (ServiceRaidStatusData): the guild's boss, every
    member who has fought (and the player), and the player's raid energy. names: user
    id -> name."""
    running = bool(run["level_end"]) and run["level_end"] > now
    if now >= run["end"]:
        level_end, status = max(run["level_end"] or 0, run["end"]), 0
    else:
        level_end, status = (run["level_end"] if running else 0), (0 if running else 1)
    me = run["members"].get(str(user_id)) or {"damage": 0, "claimed": 0, "energy": ENERGY_START,
                                               "energy_updated": now}
    shown = {uid: m for uid, m in run["members"].items() if m["damage"] or uid == str(user_id)}
    shown.setdefault(str(user_id), me)
    members = sorted(({"member_id": uid, "member_name": names.get(uid, "Unknown"), "damage": m["damage"]}
                      for uid, m in shown.items()), key=lambda r: -r["damage"])
    return [{"raid_id": raid.id, "raid_level": run["level"], "health": max(0, run["hp"]),
             "max_health": raid.max_health(run["level"], scale), "raid_level_start": run["level_start"] if running else 0,
             "raid_level_end": level_end, "claimed_rewards": int(bool(me["claimed"])), "status": status,
             "members": members,
             "energy": {"battle_energy": me["energy"], "max_battle_energy": ENERGY_MAX,
                        "cost_per_battle_energy": REFILL_WB, "battle_energy_recharge_time": ENERGY_EVERY,
                        "battle_energy_recharge_start_time": run["start"],
                        "battle_energy_recharge_stop_time": run["end"],
                        "battle_energy_update_time": me["energy_updated"]}}]
