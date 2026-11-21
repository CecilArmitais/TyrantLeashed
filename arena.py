"""The arena offline (the game's Battle button: "hunting" rivals): the rivals, their
decks, and what a fight pays. docs/protocol.md, "Arena", has the evidence; the fan
wiki's Battle page (https://tyrantunleashed.fandom.com/wiki/Battle) has the ranks,
the AI rivals' names and some rules.

From the recordings (a new account's first two arena fights, and the account's own
rivals lists):
- The new account's rivals were AI players ("NPCs", negative ids). Until the first arena win
  the list is only the tutorial rival, -1 "Riaz" (commander Ascaris, 1 health). After
  it, nine rivals, -2 to -10, rated 0 to 100.
- Every NPC is worth 75 gold, whatever its rating: the nine above, and four on the
  account's list rated 400-1000. (The wiki: NPCs "always reward" the same gold, 25 in
  its day.) Here that's the wiki's 25 times the arena gold bonus, 3 by default
  (settings.py).
- A rival just fought moves to the end of the list.
- A fight costs 1 stamina.
- The rating rose by 75 for the tutorial win (0 against 0) and 30 for the next (75
  against 90).
- The level follows the rating (player_state.LEVELS). A level-up adds max energy on
  top of the current energy, refills stamina, and gives a promotion point.
- The account's NPCs rated 400-1000 had stronger commanders (a Terrogor at level 3, a
  tier-2 Vyander at levels 3 and 4, a tier-3 Typhon Vex at level 4), and names from
  the wiki's list.
- The recorded rival -9 (rated 90) had six different level-1 common cards of its
  commander's faction, from the base set.

From the wiki: since version 1.37.1 a loss doesn't lower the rating; a win against a
higher-rated rival gives more, up to +50 "when the opponent is around +1000 rating
higher"; a loss pays a quarter of a win's gold; you're never demoted.

Other players (a server with several, game_api.py): the account's lists, at rating 735
(rank 5), had real players of ranks 4 to 6 only (rated 366-1663), highest-rated first,
some of them mixed with NPCs. A real player rated above you paid 25 gold plus 3% of the
difference (times the arena gold bonus): 7 of the 8 recorded golds exactly, the eighth
off by one. Players rated below you paid the NPCs' 75. So, here:
- Other players within one rank of yours (content.toml: arena.rank_reach) can be on the
  list, each with a chance (arena.player_chance) whenever the list changes (a win moves
  your rating, or a refresh). Picked players take the places of the NPCs rated
  nearest them, and the list is then highest-rated first, as recorded.
- A player rival plays their defense deck (user_data.defense_deck; their active deck if
  that's unset), with the AI's moves, as NPCs do. They get a report of the fight: the
  client shows it like the others (ServiceReportData::Map reads only `data` and
  `got_revenge`); "victory" is theirs (they held). Being attacked costs and pays them
  nothing (ours).
- Revenge: the report's got_revenge 0 shows the client's Revenge button, which sends
  startRevengeBattle target_user_id reported_time (ReportBox, CombatManager::
  StartHunting). That's an arena fight against the attacker's defense deck, for the
  same stamina, gold and rating as fighting them from the list (ours). Only a report
  of a lost defense offers one (a defense that held has nothing to avenge), once
  (got_revenge then 1); a revenge fight's own defence report offers none (-1), so it
  doesn't go back and forth (ours).

Not recorded or documented, so these are ours:
- Win rating: 30 + (their rating - yours) / 50, rounded, from 0 to 50. That gives the
  recorded +30 and the wiki's cap at +1000.
- From a rating of 150 on, the nine rivals are NPCs at round ratings around yours
  (yours rounded to 50, -300 to +300), named from the wiki's list. Their commander:
  tier 1 below 500, tier 2 below 1000, tier 3 from 1000, at a level fitted to the four
  NPCs seen.
- Rival decks: min(10, 3 + rating // 30) cards from the commander's faction in the
  base set: commons below 200, up to rares below 600, up to epics below 1000, then
  legendaries too. Cards gain levels as the rating nears 1500.
- Two levels gained in one fight count as two level-ups.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field

from battle.cards import ASSAULT, COMMANDER, STRUCTURE, Card, CardDB
from player_state import Player, level_for
from settings import DEFAULT_ARENA_GOLD

# (id, name, commander, rating), as recorded.
RIVALS = (
    (-1, "Riaz", 1147, 0),            # the tutorial rival, the only one until the first win
    (-2, "Vhogga", 1143, 0),
    (-3, "counterSurge", 1147, 15),
    (-4, "GunGore", 1015, 25),
    (-5, "upset", 1019, 40),
    (-6, "1BeesKnees1", 1143, 50),
    (-7, "Riaz", 1147, 60),
    (-8, "tentamanti", 1015, 75),
    (-9, "HOWLWOLFF", 1019, 90),
    (-10, "Dorian", 1143, 100),
)
TUTORIAL_RIVAL = -1
FIRST_ORDER = (-9, -10, -4, -6, -2, -5, -8, -7, -3)   # the recorded list after the tutorial win

# The wiki's "AI Rival Names" (it spells -8's name "tentamani"; the game sent "tentamanti").
NPC_NAMES = (
    "Vindictus", "Ubbon", "Nithantep", "Rhugha", "Athonith", "Vhogga", "synapticon", "DaWayne",
    "Rudgutter", "stitch", "TheBaron", "Lokrintus", "Astarion", "Vile", "Branson", "Dorian",
    "Haloth", "Maris", "Loria", "Riaz", "MickeySmith", "GunGore", "LADYXANDRA", "NewBeeland",
    "Faceespacee", "killcode", "Donnie", "Triffid", "counterSurge", "Spider987", "tentamanti", "Vorlus21",
    "445deathRay", "MaidenOFiron", "1BeesKnees1", "HOWLWOLFF", "H8TR", "upset", "13thHour", "SamSweety",
)
# The wiki's commander tiers (first-level card ids).
TIER_COMMANDERS = {
    1: (1000, 1007, 1011, 1015, 1019),                                  # Cyrus, Malika, Ascaris, Terrogor, Maion
    2: (1003, 1053, 1035, 1041, 1047),                                  # Octane, Yurich, Brood Mother, Vyander, Empress
    3: (1023, 1181, 1029, 1187, 1059, 1119, 1099, 1199, 1105, 1205),    # Halcyon, Tabitha, Typhon Vex, Barracus, ...
}
TOP_LEVEL = {1: 4, 2: 6, 3: 6}
NPC_BASE_GOLD = 25                   # every NPC, at any rating (the wiki); times the arena gold
                                     # bonus (settings.py, 3 by default: every recorded NPC paid 75)
TUTORIAL_RATING = 75                 # the tutorial win's rating change (recorded)
SCALE_FROM = 150                     # from this rating on, the rivals are around yours
NPC_OFFSETS = (-300, -200, -100, -50, 0, 50, 100, 200, 300)
FIRST_NPC_ID = -11                   # the generated NPCs are -11 to -19 (the account had -13 to -16)
BASE_SET = 1000
STAMINA_COST = 1
LIST_SIZE = 9                        # rivals on a list (the recorded NPC lists; 18 is the wiki's most)
RANK_REACH = 1                       # other players this many ranks either side of yours (recorded: 4-6 at 5)
PLAYER_CHANCE = 0.5                  # each such player's chance of being on a list (ours)
GOLD_PER_POINT_ABOVE = 0.03          # a player rival rated above you pays 3% of the difference more (recorded)


def npc_tier_level(rating: int) -> tuple[int, int]:
    """(commander tier, commander level) for an NPC of this rating. Fitted to the four
    recorded: 400 -> tier 1 level 3, 500 -> tier 2 level 3, 750 -> tier 2 level 4,
    1000 -> tier 3 level 4."""
    if rating < 500:
        tier, level = 1, 1 + rating // 200
    elif rating < 1000:
        tier, level = 2, 3 + (rating - 500) // 250
    else:
        tier, level = 3, 4 + (rating - 1000) // 1000
    return tier, min(level, TOP_LEVEL[tier])


def commander_at(cards: CardDB, base: int, level: int) -> int:
    """The card id of a commander at a level (the ids aren't always consecutive)."""
    index = getattr(cards, "_levels_by_base", None)
    if index is None:
        index = {}
        for c in cards.cards.values():
            if c.fusion_level == 0:
                index.setdefault(c.base_id, {})[c.level] = c.id
        cards._levels_by_base = index
    levels = index[base]
    return levels[min(level, max(levels))]


def npc_commander(rating: int, rng: random.Random, cards: CardDB) -> int:
    """A commander card id for an NPC of this rating."""
    tier, level = npc_tier_level(rating)
    return commander_at(cards, rng.choice(TIER_COMMANDERS[tier]), level)


def _pool(arena: dict, cards: CardDB | None) -> list[tuple[int, str, int, int]]:
    """The rivals there are now, in their natural order: (id, name, commander, rating).
    Generated NPCs need the card data (`cards`) for their commanders.

    Each refresh (`refreshes`) rolls new NPCs: other names and commanders, and, from
    rating 150 on, ratings up to 50 either way. Below 150 a refreshed list keeps the
    recorded nine's ratings (0-100)."""
    if arena["hunting_wins"] == 0:
        return [RIVALS[0]]
    refresh = arena.get("refreshes", 0)
    by_id = {r[0]: r for r in RIVALS}
    low = arena["rating"] < SCALE_FROM
    if low and not refresh:
        return [by_id[i] for i in FIRST_ORDER]
    if cards is None:
        raise ValueError("these rivals need the card data")
    if low:
        ratings = [by_id[i][3] for i in FIRST_ORDER]
    else:
        centre = round(arena["rating"] / 50) * 50
        ratings = [centre + offset for offset in NPC_OFFSETS]
    out = []
    for slot, rating in enumerate(ratings):
        rng = random.Random(f"npc:{slot}:{rating}" if not refresh else f"npc:{refresh}:{slot}:{rating}")
        if refresh and not low:
            rating += 50 * rng.choice((-1, 0, 1))
        rating = max(0, rating)
        out.append((FIRST_NPC_ID - slot, rng.choice(NPC_NAMES), npc_commander(rating, rng, cards), rating))
    return out


def refresh_rivals(arena: dict) -> None:
    """buyRivalsRefresh: a new set of rivals (the caller takes the gold)."""
    arena["refreshes"] = arena.get("refreshes", 0) + 1
    arena["rival_order"] = []


@dataclass(frozen=True)
class PlayerRival:
    """Another player of this server, as a rival: what the list shows of them."""
    user_id: int
    name: str
    rating: int
    level: int                       # their rank
    commander: int                   # their defense deck's commander
    guild_name: str | None = None


def player_gold(mine: int, theirs: int) -> int:
    """What beating a player rival pays, before the arena gold bonus: NPC_BASE_GOLD, plus
    3% of how much higher they're rated. Recorded at rating 735, times the bonus of 3:
    159 (1663), 156 (1624), 129 (1309; this gives 126), 126 (1302), 123 (1259), 102
    (1024), 87 (865), and 75 for every player rated 705 or below."""
    return NPC_BASE_GOLD + max(0, round((theirs - mine) * GOLD_PER_POINT_ABOVE))


def player_entry(p: PlayerRival, mine: int, gold_multiplier: int) -> dict:
    """A hunting_targets entry for a player rival, in the recorded shape and key order."""
    return {"name": p.name, "user_id": p.user_id, "guild_name": p.guild_name, "hunting_elo": str(p.rating),
            "level": str(p.level), "items": [], "commander": str(p.commander), "sp": 0,
            "gold": player_gold(mine, p.rating) * gold_multiplier}


def in_reach(my_level: int, their_level: int, reach: int = RANK_REACH) -> bool:
    return abs(int(their_level) - int(my_level)) <= reach


def pick_players(candidates, my_level: int, rng: random.Random, chance: float = PLAYER_CHANCE,
                 reach: int = RANK_REACH) -> list[int]:
    """Which other players go on a list: each within `reach` ranks of yours, with
    `chance`, at most LIST_SIZE of them (a random LIST_SIZE when more are picked)."""
    near = sorted((p for p in candidates if in_reach(my_level, p.level, reach)), key=lambda p: p.user_id)
    picked = [p.user_id for p in near if rng.random() < chance]
    if len(picked) > LIST_SIZE:
        picked = sorted(rng.sample(picked, LIST_SIZE))
    return picked


def match_key(arena: dict) -> list[int]:
    """When the players on a list are picked again: when the rating moves (a win) or the
    list is refreshed. The same until then, so the list doesn't change between showing
    it and starting a fight."""
    return [int(arena.get("refreshes", 0)), int(arena["rating"])]


def matched_players(arena: dict, candidates, rng: random.Random, chance: float = PLAYER_CHANCE,
                    reach: int = RANK_REACH) -> tuple[list[int], bool]:
    """The other players picked for the current list ([] during the tutorial), and
    whether they were picked just now (the caller then saves the player). `candidates`
    is called only to pick: it returns PlayerRivals, or ids with levels."""
    if arena["hunting_wins"] == 0:
        return [], False
    key = match_key(arena)
    if list(arena.get("matched_for") or ()) == key:
        return [int(i) for i in arena.get("matched") or ()], False
    ids = pick_players(candidates(), arena["level"], rng, chance, reach) if chance > 0 else []
    arena["matched"], arena["matched_for"] = ids, key
    return ids, True


def _place_players(npcs: list[dict], players, mine: int, gold_multiplier: int) -> list[dict]:
    """The NPC list with players in: each takes the place of the NPC rated nearest them
    (the first such, on a tie). Then highest-rated first, as recorded."""
    out = list(npcs)
    for p in list(players)[:LIST_SIZE]:
        npc = [e for e in out if int(e["user_id"]) < 0]
        if npc and len(out) >= LIST_SIZE:
            out.remove(min(npc, key=lambda e: abs(int(e["hunting_elo"]) - p.rating)))
        out.append(player_entry(p, mine, gold_multiplier))
    return sorted(out, key=lambda e: -int(e["hunting_elo"]))


def rival_list(arena: dict, cards: CardDB | None = None, gold_multiplier: int = DEFAULT_ARENA_GOLD,
               players=()) -> list[dict]:
    """The rivals the player can fight now, in the game's order, as
    `hunting_targets` entries. `rival_order` lists the rivals fought, oldest first:
    those go last, in that order. Each NPC pays NPC_BASE_GOLD x gold_multiplier.
    `players`: other players (PlayerRivals) to put on the list; none during the
    tutorial."""
    pool = _pool(arena, cards)
    entries = [{"commander": str(commander), "name": name, "hunting_elo": str(rating), "level": 1, "sp": "0",
                "user_id": rid, "items": [], "gold": NPC_BASE_GOLD * gold_multiplier}
               for rid, name, commander, rating in pool]
    if players and arena["hunting_wins"] > 0:
        entries = _place_players(entries, players, arena["rating"], gold_multiplier)
    ids = [int(e["user_id"]) for e in entries]
    fought = [i for i in arena.get("rival_order") or () if i in ids]
    return [e for e in entries if int(e["user_id"]) not in fought] + [e for i in fought for e in entries
                                                                        if int(e["user_id"]) == i]


def targets_json(arena: dict, cards: CardDB | None = None, gold_multiplier: int = DEFAULT_ARENA_GOLD,
                 players=()) -> dict:
    """hunting_targets as the game gets it: {id: entry}, in order."""
    return {str(r["user_id"]): r for r in rival_list(arena, cards, gold_multiplier, players)}


def rival_deck(rival: dict, cards: CardDB) -> tuple[Card, list[Card]]:
    """(commander, cards) for a rival. The same rival at the same rating always has the
    same deck."""
    commander = cards[int(rival["commander"])]
    rating = int(rival["hunting_elo"])
    size = min(10, 3 + rating // 30)
    top = 1 if rating < 200 else 2 if rating < 600 else 3 if rating < 1000 else 4
    pool = sorted((c for c in cards.cards.values()
                   if c.set == BASE_SET and c.faction == commander.faction and c.type in (ASSAULT, STRUCTURE)
                   and c.id == c.base_id and c.fusion_level == 0 and c.rarity <= top), key=lambda c: c.id)
    rng = random.Random(f"{rival['user_id']}:{rating}")
    picked = rng.sample(pool, size) if len(pool) >= size else [rng.choice(pool) for _ in range(size)]
    frac = min(1.0, rating / 1500)
    deck = []
    for base in picked:
        levels = sorted((c for c in cards.cards.values() if c.base_id == base.id and c.fusion_level == 0),
                        key=lambda c: c.level)
        deck.append(levels[min(len(levels) - 1, round(frac * (len(levels) - 1)))])
    return commander, deck


# ---- Promote: unlocking commanders ------------------------------------------------------

UNLOCK_RARITY = {1: 2, 2: 3, 3: 4}           # user_data.commander_unlock_rarity for each tier
UNLOCK_BONUS = {1: (1, 3), 2: (2, 2), 3: (4, 0)}   # (rares, commons) that come with it (the wiki)


class UnlockError(Exception):
    """Why a commander can't be unlocked, in words the player can read."""


def owned_commanders(player: Player, cards: CardDB) -> set[int]:
    """First-level ids of the commanders the player owns (any level)."""
    out = set()
    for cid, c in player.cards.items():
        card = cards.get(cid)
        if card is not None and c["num_owned"] > 0 and card.type == COMMANDER:
            out.add(card.base_id)
    return out


def unlock_tier(owned: set[int]) -> int:
    """The tier Promote unlocks from: the lowest with a commander not yet owned (3 once
    everything is owned)."""
    return next((t for t in (1, 2, 3) if any(b not in owned for b in TIER_COMMANDERS[t])), 3)


def unlock_commander(player: Player, base_id: int, cards: CardDB, rng: random.Random) -> list[int]:
    """Unlocks a commander for a promotion point: the commander and its bonus cards go
    into the inventory. Returns the bonus cards' ids (rares first, as recorded)."""
    a = player.arena
    tier = next((t for t, bases in TIER_COMMANDERS.items() if base_id in bases), None)
    if tier is None:
        raise UnlockError("that isn't a commander Promote unlocks")
    owned = owned_commanders(player, cards)
    if base_id in owned:
        raise UnlockError("you already have that commander")
    if a["promotion_points"] < 1:
        raise UnlockError("you have no promotion points")
    if tier > unlock_tier(owned):
        raise UnlockError(f"unlock every tier {unlock_tier(owned)} commander first")
    faction = cards[base_id].faction
    rares, commons = UNLOCK_BONUS[tier]
    bonus = []
    for rarity, n in ((2, rares), (1, commons)):
        pool = sorted((c.id for c in cards.cards.values()
                       if c.set == BASE_SET and c.faction == faction and c.rarity == rarity and c.id == c.base_id
                       and c.fusion_level == 0 and c.type in (ASSAULT, STRUCTURE)))
        bonus += rng.sample(pool, n) if len(pool) >= n else [rng.choice(pool) for _ in range(n)]
    a["promotion_points"] -= 1
    player.add_card(base_id)
    for cid in bonus:
        player.add_card(cid)
    a["unlock_rarity"] = UNLOCK_RARITY[unlock_tier(owned | {base_id})]
    return bonus


def rating_change(mine: int, theirs: int, won: bool, first_win: bool) -> int:
    """A win: 30, more against a higher-rated rival (30 + the difference / 50), at most
    50. Recorded: every one of the account's 12 reported wins gave +30, and so did the
    new account's second win (75 against 90); the wiki: up to +50 at about +1000. A
    loss: 0 (the wiki, since version 1.37.1)."""
    if not won:
        return 0
    if first_win:
        return TUTORIAL_RATING
    return max(30, min(50, round(30 + (theirs - mine) / 50)))


@dataclass
class HuntResult:
    rewards: list                    # battle_data.rewards: the player's entry, then the rival's
    gold: int = 0
    rating_change: int = 0
    levels: list[int] = field(default_factory=list)       # levels reached by this fight

    def summary(self) -> str:
        out = f"{self.gold} gold, rating {self.rating_change:+d}"
        if self.levels:
            out += ", level " + ", ".join(map(str, self.levels))
        return out


def finish_hunt(player: Player, rival: dict, won: bool, loss_mult: float, now: int, user_id,
                battle_id=None) -> HuntResult:
    """Pays an arena fight into `player` and files its report. The caller saves the
    player. A loss pays loss_mult of the gold, rounded to the nearest: the account's
    reported losses paid 19 for 75 (18.75)."""
    a = player.arena
    gold = int(rival["gold"]) if won else math.floor(int(rival["gold"]) * loss_mult + 0.5)
    change = rating_change(a["rating"], int(rival["hunting_elo"]), won, a["hunting_wins"] == 0)
    rid = int(rival["user_id"])
    a["rating"] += change
    a["hunts_fought"] += 1
    if won:
        a["hunting_wins"] += 1
    if rid != TUTORIAL_RIVAL:
        a["rival_order"] = [i for i in a.get("rival_order") or () if i != rid] + [rid]
    result = HuntResult([{"user_id": str(user_id), "gold": gold, "sp": 0, "rating_change": change, "item": [],
                          "lp_change": 0},
                         {"user_id": str(rid), "gold": 0, "sp": 0, "rating_change": 0, "item": []}],
                        gold=gold, rating_change=change)
    reached = level_for(a["rating"])
    while a["level"] < reached:          # never demoted (the wiki)
        a["level"] += 1
        result.levels.append(a["level"])
        a["promotion_points"] += 1
        player.energy += player.max_energy           # over the cap: the recorded 41 -> 291
        player.energy_updated = now
        a["stamina"], a["stamina_updated"] = max(a["stamina"], a["max_stamina"]), now
    player.gold += gold
    player.gold_earned = (player.gold_earned or 0) + gold
    file_report(a, rival, won, result.rewards[0], now, battle_id)
    return result


# ---- reports (the Reports tab) -------------------------------------------------------------

REPORTS_KEPT = 16                    # the account's list had 16 (the real limit isn't known)


def file_report(arena: dict, rival: dict, won: bool, reward: dict, now: int, battle_id=None) -> None:
    """Keeps a fight for the Reports tab, newest first."""
    report = {"rival": int(rival["user_id"]), "name": rival["name"], "victory": int(won),
              "battle_id": str(battle_id if battle_id is not None else now * 1000), "time": now,
              "rewards": dict(reward)}
    arena["reports"] = ([report] + list(arena.get("reports") or []))[:REPORTS_KEPT]


DEFENCE_REPORT_TYPE = "1"            # ours: the client doesn't read `type`; the recorded attacks were 2


REVENGE_OPEN, REVENGE_TAKEN, NO_REVENGE = 0, 1, -1      # got_revenge: 0 shows the Revenge button


def file_defence_report(arena: dict, attacker_id, attacker_name: str, held: bool, now: int,
                        battle_id=None, revenge: bool = True) -> None:
    """Keeps a fight against this player's defense deck for their Reports tab: the
    attacker, and whether the defense held. It costs and pays them nothing (ours).
    It offers a revenge only when the attacker won, and the fight wasn't itself a
    revenge (`revenge` False)."""
    reward = {"user_id": "", "gold": 0, "sp": 0, "rating_change": 0, "item": [], "lp_change": 0}
    report = {"rival": int(attacker_id), "name": attacker_name, "victory": int(held),
              "battle_id": str(battle_id if battle_id is not None else now * 1000), "time": now,
              "rewards": reward, "defence": 1,
              "revenge": REVENGE_OPEN if revenge and not held else NO_REVENGE}
    arena["reports"] = ([report] + list(arena.get("reports") or []))[:REPORTS_KEPT]


def defence_report(arena: dict, attacker_id, reported_time) -> dict | None:
    """The defence report a Revenge button belongs to (startRevengeBattle sends the
    attacker and the report's time), or None."""
    return next((r for r in arena.get("reports") or [] if r.get("defence") and str(r["rival"]) == str(attacker_id)
                 and str(r["time"]) == str(reported_time)), None)


def reports_json(arena: dict, user_id) -> list[dict]:
    """battle_reports as the game gets it, in the recorded shape: one "type 2" report per
    fight the player started, `data` being compact JSON. A defence report (another
    player fought this one's defense deck) has the attacker as sender, enemy and host,
    "victory" 1 when the defense held, and got_revenge 0 while it offers a revenge
    (a lost defense, not yet avenged)."""
    out = []
    for r in arena.get("reports") or []:
        defence = bool(r.get("defence"))
        host = str(r["rival"]) if defence else str(user_id)
        rewards = {**r["rewards"], "user_id": str(user_id)} if defence else r["rewards"]
        data = {"enemy_id": str(r["rival"]), "enemy_name": r["name"], "victory": r["victory"],
                "host_id": host, "battle_id": r["battle_id"], "rewards": rewards}
        out.append({"user_id": str(user_id), "sender_id": str(r["rival"]),
                    "type": DEFENCE_REPORT_TYPE if defence else "2",
                    "data": json.dumps(data, separators=(",", ":")), "time": str(r["time"]), "status": "1",
                    "got_revenge": str(r.get("revenge", NO_REVENGE)) if defence else "-1"})
    return out
