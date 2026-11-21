"""The offline player's saved data: card inventory, gold, war bonds, energy, the
game's flags (tutorial progress, settings) and mission progress.

Saved in state/player.json. Whenever a reply carries the player's data
(`user_data`, `user_cards`, `max_energy`, `mission_completions`), the saved values
replace the recorded ones, so the game always shows the save. server/player.py
edits them by hand.

How the game stores them:
  gold        user_data.money       (a string, e.g. "100")
  war bonds   user_data.tokens
  energy      user_data.energy, plus user_data.last_update_time: when energy was last
              brought up to date. The client counts down to the next point from it.
  max energy  max_energy (top level of init and other replies); 250 to start, raised
              by the store's "+50 Max Energy" upgrades, so it belongs to the player
  cards       user_cards = {card_id: {"num_owned": "1", "num_used": "0"}}
  flags       user_data.flags = {"tutorial": "0", "speed": "1", "guides_complete": [102, 101], ...}:
              strings, except lists such as guides_complete. Set by setUserFlag.
  missions    mission_completions (top level) = {mission_id: {"number": "2", "complete": "2",
              "star2": "0", "star3": "0"}}, or [] when empty (PHP's empty array);
              user_data.mission_wins counts every mission won, user_data.stars the stars
  decks       user_decks (top level) = {deck_id: {"deck_id": 1, "name": null, "commander_id": "1000",
              "cards": {card_id: "1"}, "dominion_id": 0}}. Set by setDeckCards; a mission's
              reward card also goes in while the deck has room (server/rewards.py)
  earned      user_data.gold_earned and user_data.cards_earned: everything ever earned
  stamina     user_data.stamina, user_data.last_stam_update_time: refills like energy (one
              point per stamina_recharge_time, 900 s, up to max_stamina, 15); arena fights cost 1
  arena       user_data.hunting_elo (the arena rating), hunting_wins, level (follows the
              rating, see LEVELS), level_name, base_xp/next_xp (the level's rating range),
              promotion_points (one per level-up, spent on commanders)

Energy over time
----------------
Nothing runs in the background. The save keeps two numbers: the energy value and
the time it was last brought up to date (the "sync point"). Whenever the player's
data is needed (any login or reply), the server compares that time with the real
clock and adds one point per full `energy_recharge_time` (60 s) that has passed, up
to the player's max energy. So time with the server switched off counts exactly
like time with it running. The sync point then moves forward by the whole minutes
used, keeping the part-way progress towards the next point, which is what the
real server does too (its +1 ticks land a whole number of minutes after the last
update, not on the clock's :00).

Standard library only.
"""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SAVE_VERSION = 11         # 2: max_energy; 3: flags and missions; 4: decks, gold/cards earned;
                          # 5: stamina and the arena; 6: event boxes; 7: salvage points and card
                          # locks; 8: achievements; 9: the daily crate, store upgrades, name and
                          # active deck; 10: raids; 11: the raid moved to the guild (guilds.py)
                          # (older saves are upgraded on load)
FIRST_CRATE_AFTER = 1799  # recorded: a new account's crate opened 1799 s after its login
DEFAULT_MAX_ENERGY = 250
DEFAULT_MAX_STAMINA = 15

# The player's levels (the game's "Battle Rank"): (level, name, the arena rating it
# starts at), from the fan wiki's Battle page. The recordings agree where they reach:
# level 1 Rookie 0-25, level 2 Infantryman 25-150, the account's level 5 Veteran 600-1000,
# rivals at level 4 rated 366-595 and at level 6 rated 1024-1663.
LEVELS = (
    (1, "Rookie", 0), (2, "Infantryman", 25), (3, "Adept", 150), (4, "Marshall", 350),
    (5, "Veteran", 600), (6, "Vanguard Officer", 1000), (7, "Dragoon", 1750), (8, "Headhunter", 3000),
    (9, "Legionnaire", 5000), (10, "Prefect", 7000), (11, "Warmaster", 9000), (12, "Centurian", 11000),
    (13, "Imperator", 13500), (14, "Immortal", 16500), (15, "Tyrant", 20000), (16, "Dark Tyrant", 35000),
    (17, "Tyrant Lord", 50000), (18, "Tyrant Overlord", 75000), (19, "Tyrant King", 100000),
    (20, "Mythic Tyrant", 125000),
)


def level_for(rating: int) -> int:
    """The level an arena rating reaches."""
    return max(lv for lv, _, start in LEVELS if rating >= start)


def level_info(level: int) -> tuple[str, int, int]:
    """(level_name, base_xp, next_xp) for a level. The last level's next_xp is its own
    start (there's nothing after it)."""
    row = next((r for r in LEVELS if r[0] == level), LEVELS[-1])
    nxt = next((r[2] for r in LEVELS if r[0] == row[0] + 1), row[2])
    return row[1], row[2], nxt


@dataclass
class Player:
    gold: int
    war_bonds: int
    energy: int
    energy_updated: int                     # unix time energy was last brought up to date (the sync point)
    max_energy: int = DEFAULT_MAX_ENERGY    # this player's cap for refilling over time
    cards: dict[str, dict[str, int]] = field(default_factory=dict)   # id -> {"num_owned", "num_used"[, "is_locked"]}
    created_from: dict = field(default_factory=dict)
    flags: dict | None = None                # user_data.flags as the game sends them; None: not known yet
    missions: dict[str, dict[str, int]] | None = None   # id -> {"number", "complete", "star2", "star3"}
    mission_wins: int = 0                    # every mission won, repeats included
    decks: dict[str, dict] | None = None     # id -> {"name", "commander_id", "cards": {id: n}, "dominion_id"}
    gold_earned: int | None = None           # user_data.gold_earned; None: not known yet
    cards_earned: int | None = None          # user_data.cards_earned
    arena: dict | None = None                # stamina, rating, wins, level...; see ARENA_FIELDS
    boxes: dict = field(default_factory=dict)   # event box id -> {"received", "completes", "deals", "tier"} (boxes.py)
    salvage: int | None = None               # user_data.salvage: salvage points (SP); None: not known yet
    achievements: dict | None = None         # done, counts, the daily objectives (achievements.py)
    daily_bonus: dict | None = None          # the home screen crate: {"next", "streak", "last"} (daily_crate.py)
    upgrades: dict = field(default_factory=dict)   # store upgrades bought: {"energy", "slots_wb", "slots_gold", "decks"}
    max_cards: int | None = None             # user_data.caps.max_cards (1600, +10 per card slot upgrade)
    max_decks: int | None = None             # max_decks (1, +1 per Extra Deck Slot)
    name: str | None = None                  # user_data.name
    name_changes: int = 0
    active_deck: str | None = None           # user_data.active_deck
    defense_deck: str | None = None          # user_data.defense_deck

    @property
    def card_count(self) -> int:
        return sum(c["num_owned"] for c in self.cards.values())

    @property
    def stars(self) -> int:
        """user_data.stars: one per mission completed, plus its star2 and star3. (Recorded:
        22 for 8 completed missions, 7 of them with both extra stars.)"""
        return sum((m["complete"] > 0) + m["star2"] + m["star3"] for m in (self.missions or {}).values())

    def won_mission(self, mission_id, stars_earned=()) -> None:
        """A mission won: counted for that mission and in mission_wins. stars_earned is
        the battle's rewards "star" keys; "2" and "3" mark the extra stars."""
        if self.missions is None:
            self.missions = {}
        m = self.missions.setdefault(str(mission_id), {"number": 0, "complete": 0, "star2": 0, "star3": 0})
        m["number"] += 1          # in every recording these two are equal: both count wins
        m["complete"] += 1
        earned = {str(s) for s in stars_earned}
        for s in ("2", "3"):
            if s in earned:
                m["star" + s] = 1
        self.mission_wins += 1

    def add_card(self, card_id, n: int = 1) -> None:
        """n more copies of a card in the inventory, counted in cards_earned."""
        c = self.cards.setdefault(str(card_id), {"num_owned": 0, "num_used": 0})
        c["num_owned"] += n
        self.cards_earned = (self.cards_earned or 0) + n

    def owned(self, card_id) -> int:
        return int((self.cards.get(str(card_id)) or {}).get("num_owned", 0))

    def in_decks(self, card_id) -> int:
        """The most copies of a card any deck uses (as its commander, or among its cards):
        decks share the inventory."""
        cid = str(card_id)
        return max((d["cards"].get(cid, 0) + (str(d.get("commander_id")) == cid)
                    for d in (self.decks or {}).values()), default=0)

    def is_locked(self, card_id) -> bool:
        return bool((self.cards.get(str(card_id)) or {}).get("is_locked"))

    def remove_card(self, card_id, n: int = 1) -> None:
        """n fewer copies of a card. The entry stays at 0 copies, as the real server kept
        it. A deck using more copies than are left is trimmed."""
        cid = str(card_id)
        c = self.cards.setdefault(cid, {"num_owned": 0, "num_used": 0})
        c["num_owned"] = max(0, c["num_owned"] - n)
        c["num_used"] = min(c["num_used"], c["num_owned"])
        for d in (self.decks or {}).values():
            if d["cards"].get(cid, 0) > c["num_owned"]:
                if c["num_owned"]:
                    d["cards"][cid] = c["num_owned"]
                else:
                    del d["cards"][cid]


ARENA_FIELDS = ("stamina", "stamina_updated", "max_stamina", "rating", "hunting_wins", "level",
                "promotion_points", "hunts_fought", "rival_order", "refreshes", "unlock_rarity", "reports")
# Arena fields added later, filled in for saves that don't have them.
ARENA_DEFAULTS = {"refreshes": 0, "unlock_rarity": 2, "reports": []}


def arena_from_start(start: dict, now: int) -> dict:
    """The arena part of a save, from the starting data. unlock_rarity is
    user_data.commander_unlock_rarity: the rarity of the commanders Promote can unlock
    now (2: tier 1, 3: tier 2, 4: tier 3)."""
    ud = start.get("user_data") or {}
    rating = int(ud.get("hunting_elo") or 0)
    return {"stamina": int(ud.get("stamina") or DEFAULT_MAX_STAMINA), "stamina_updated": now,
            "max_stamina": int(start.get("max_stamina") or DEFAULT_MAX_STAMINA), "rating": rating,
            "hunting_wins": int(ud.get("hunting_wins") or 0),
            "level": max(int(ud.get("level") or 1), level_for(rating)),
            "promotion_points": int(ud.get("promotion_points") or 0),
            "hunts_fought": int(((start.get("arena_battle_info") or {}).get("hunts_fought")) or 0),
            "rival_order": [], "refreshes": 0, "reports": [],
            "unlock_rarity": int(ud.get("commander_unlock_rarity") or ARENA_DEFAULTS["unlock_rarity"])}


def deck_size(deck: dict) -> int:
    """Cards in a saved deck, not counting the commander and dominion."""
    return sum(deck["cards"].values())


def deck_from_json(d: dict) -> dict:
    """A user_decks entry (strings, as the game sends it) as the save keeps it."""
    return {"name": d.get("name"), "commander_id": int(d.get("commander_id") or 0),
            "cards": {str(k): int(v) for k, v in (d.get("cards") or {}).items() if int(v) > 0},
            "dominion_id": int(d.get("dominion_id") or 0)}


def decks_json(decks: dict) -> dict | list:
    """user_decks as the game gets it: card ids in number order, counts as strings."""
    if not decks:
        return []
    return {did: {"deck_id": int(did), "name": d.get("name"), "commander_id": str(d["commander_id"]),
                  "cards": {cid: str(n) for cid, n in sorted(d["cards"].items(), key=lambda kv: int(kv[0]))},
                  "dominion_id": d.get("dominion_id", 0)}
            for did, d in sorted(decks.items(), key=lambda kv: int(kv[0]))}


def refill(energy: int, updated: int, now: int, max_energy: int, every: int) -> tuple[int, int]:
    """Energy after refilling up to now: (energy, new sync point).

    Below the maximum, one point per `every` seconds since the sync point; the sync
    point moves on by whole points so the part-way progress to the next point is kept.
    At or above the maximum (a level-up or a bought refill can overfill it) nothing is
    added and the sync point is just now. If the sync point is in the future (the
    computer's clock was set back), regeneration restarts from now instead of
    stalling until the clock catches up.
    """
    if every <= 0 or energy >= max_energy or now < updated:
        return energy, now
    points = (now - updated) // every
    energy = min(max_energy, energy + points)
    if energy >= max_energy:
        return energy, now
    return energy, updated + points * every


MISSION_FIELDS = ("number", "complete", "star2", "star3")


def completions_json(missions: dict) -> dict | list:
    """mission_completions as the game gets it: strings, and [] when there are none
    (PHP writes an empty array that way)."""
    if not missions:
        return []
    return {mid: {k: str(v) for k, v in m.items()} for mid, m in missions.items()}


def fill_from_start(player: Player, start: dict, now: int | None = None) -> bool:
    """Flags, mission progress, decks and the earned counts from the starting data, where
    the save doesn't have them yet (a new save, or one from before they were kept).
    True if anything changed."""
    ud = start.get("user_data") or {}
    changed = False
    if player.flags is None:
        player.flags = copy.deepcopy(ud.get("flags") or {})
        changed = True
    if player.missions is None:
        mc = start.get("mission_completions")
        player.missions = {str(k): {f: int(v.get(f, 0)) for f in MISSION_FIELDS}
                           for k, v in (mc.items() if isinstance(mc, dict) else ())}
        player.mission_wins = int(ud.get("mission_wins") or 0)
        changed = True
    if player.decks is None:
        decks = start.get("user_decks")
        player.decks = {str(k): deck_from_json(v) for k, v in (decks.items() if isinstance(decks, dict) else ())}
        changed = True
    for key in ("gold_earned", "cards_earned"):
        if getattr(player, key) is None:
            setattr(player, key, int(ud.get(key) or 0))
            changed = True
    if player.arena is None:
        player.arena = arena_from_start(start, int(time.time()) if now is None else now)
        changed = True
    if player.salvage is None:
        player.salvage = int(ud.get("salvage") or 0)
        changed = True
    if player.max_cards is None:
        player.max_cards = int((ud.get("caps") or {}).get("max_cards") or 1600)
        changed = True
    if player.max_decks is None:
        player.max_decks = int(start.get("max_decks") or 1)
        changed = True
    if player.name is None:
        player.name = str(ud.get("name") or "")
        player.name_changes = int(ud.get("name_changes") or 0)
        changed = True
    for key in ("active_deck", "defense_deck"):
        if getattr(player, key) is None:
            setattr(player, key, str(ud.get(key) or next(iter(player.decks or {}), "1")))
            changed = True
    if player.daily_bonus is None:          # an older save: its crate is ready now
        player.daily_bonus = {"next": int(time.time()) if now is None else now, "streak": 0, "last": 0}
        changed = True
    return changed


def first_crate_after(start: dict) -> int:
    """When a new save's crate is ready: the recorded login's daily_bonus_time, counted
    from its time (1799 s), or FIRST_CRATE_AFTER."""
    try:
        return max(0, int(start["daily_bonus_time"]) - int(start["time"]))
    except (KeyError, TypeError, ValueError):
        return FIRST_CRATE_AFTER


def _like(old, value: int):
    """value written the way the recording wrote this field (string or number)."""
    return str(value) if isinstance(old, str) else value


class PlayerStore:
    """Reads and writes the save file. Re-reads it whenever it changes on disk, so
    edits made with server/player.py while the server runs are picked up."""

    def __init__(self, path: Path, clock=time.time, default_max_energy: int = DEFAULT_MAX_ENERGY) -> None:
        self.path = Path(path)
        self.clock = clock
        self.default_max_energy = default_max_energy
        self._player: Player | None = None
        self._mtime: float | None = None

    # ---- file ----------------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Player | None:
        if not self.path.is_file():
            self._player, self._mtime = None, None
            return None
        mtime = self.path.stat().st_mtime
        if self._player is None or mtime != self._mtime:
            data = json.loads(self.path.read_text("utf-8"))
            version = data.get("version")
            if version not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, SAVE_VERSION):
                raise ValueError(f"{self.path}: save version {version}, expected {SAVE_VERSION}")
            p = data["player"]
            self._player = Player(
                gold=int(p["gold"]), war_bonds=int(p["war_bonds"]), energy=int(p["energy"]),
                energy_updated=int(p["energy_updated"]),
                max_energy=int(p.get("max_energy", self.default_max_energy)),
                cards={str(k): {"num_owned": int(v["num_owned"]), "num_used": int(v.get("num_used", 0)),
                                **({"is_locked": 1} if v.get("is_locked") else {})}
                       for k, v in p["cards"].items()},
                created_from=p.get("created_from", {}),
                flags=p.get("flags"),
                missions=None if p.get("missions") is None else {
                    str(k): {f: int(v.get(f, 0)) for f in MISSION_FIELDS} for k, v in p["missions"].items()},
                mission_wins=int(p.get("mission_wins", 0)),
                decks=None if p.get("decks") is None else {
                    str(k): {"name": v.get("name"), "commander_id": int(v["commander_id"]),
                             "cards": {str(c): int(n) for c, n in v["cards"].items()},
                             "dominion_id": int(v.get("dominion_id", 0))} for k, v in p["decks"].items()},
                gold_earned=None if p.get("gold_earned") is None else int(p["gold_earned"]),
                cards_earned=None if p.get("cards_earned") is None else int(p["cards_earned"]),
                arena=None if p.get("arena") is None else {**copy.deepcopy(ARENA_DEFAULTS), **{
                    k: (list(v) if isinstance(v, list) else int(v)) for k, v in p["arena"].items()}},
                boxes={str(k): {"received": {str(c): int(n) for c, n in (v.get("received") or {}).items()},
                                "completes": int(v.get("completes", 0)),
                                "deals": [int(d) for d in v.get("deals") or []],
                                **({"tier": int(v["tier"])} if "tier" in v else {})}
                       for k, v in (p.get("boxes") or {}).items()},
                salvage=None if p.get("salvage") is None else int(p["salvage"]),
                achievements=copy.deepcopy(p.get("achievements")),
                daily_bonus=copy.deepcopy(p.get("daily_bonus")),
                upgrades={k: int(v) for k, v in (p.get("upgrades") or {}).items()},
                max_cards=None if p.get("max_cards") is None else int(p["max_cards"]),
                max_decks=None if p.get("max_decks") is None else int(p["max_decks"]),
                name=p.get("name"), name_changes=int(p.get("name_changes") or 0),
                active_deck=None if p.get("active_deck") is None else str(p["active_deck"]),
                defense_deck=None if p.get("defense_deck") is None else str(p["defense_deck"]),
            )
            self._mtime = mtime
            if version != SAVE_VERSION:
                self.save(self._player)       # upgrade the file in place
        return self._player

    def save(self, player: Player) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": SAVE_VERSION, "saved": int(self.clock()), "player": asdict(player)}
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), "utf-8")
        os.replace(tmp, self.path)
        self._player, self._mtime = player, self.path.stat().st_mtime

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
        self._player, self._mtime = None, None

    # ---- the player ------------------------------------------------------------------

    def create_from(self, start: dict, source: dict | None = None) -> Player:
        """A new save from a starting reply (the recorded `init`): its gold, war bonds,
        energy, max energy and cards. Energy counts from now."""
        ud = start.get("user_data") or {}
        cards = {str(k): {"num_owned": int(v.get("num_owned", 1)), "num_used": int(v.get("num_used", 0))}
                 for k, v in (start.get("user_cards") or {}).items()}
        player = Player(gold=int(ud.get("money", 0)), war_bonds=int(ud.get("tokens", 0)),
                        energy=int(ud.get("energy", 0)), energy_updated=int(self.clock()),
                        max_energy=int(start.get("max_energy") or self.default_max_energy),
                        cards=cards, created_from=source or {},
                        daily_bonus={"next": int(self.clock()) + first_crate_after(start), "streak": 0, "last": 0})
        fill_from_start(player, start, int(self.clock()))
        self.save(player)
        return player

    def current(self, start: dict, every: int, source: dict | None = None, stamina_every: int = 900) -> Player:
        """The saved player, created from `start` if there's no save yet, with energy
        and stamina refilled up to now (and saved, if that changed anything)."""
        now = int(self.clock())
        player = self.load() or self.create_from(start, source)
        changed = fill_from_start(player, start, now)     # a save from before flags, missions... were kept
        energy, updated = refill(player.energy, player.energy_updated, now, player.max_energy, every)
        if (energy, updated) != (player.energy, player.energy_updated):
            player.energy, player.energy_updated = energy, updated
            changed = True
        a = player.arena
        stamina, s_updated = refill(a["stamina"], a["stamina_updated"], now, a["max_stamina"], stamina_every)
        if (stamina, s_updated) != (a["stamina"], a["stamina_updated"]):
            a["stamina"], a["stamina_updated"] = stamina, s_updated
            changed = True
        if changed:
            self.save(player)
        return player

    @staticmethod
    def apply(body: dict, player: Player) -> dict:
        """Put the saved values into a reply, in place of the recorded ones. Only fields
        the reply already has are replaced, so every reply keeps its recorded shape."""
        ud = body.get("user_data")
        if isinstance(ud, dict):
            for key, value in (("money", player.gold), ("tokens", player.war_bonds),
                               ("energy", player.energy), ("last_update_time", player.energy_updated),
                               ("mission_wins", player.mission_wins), ("stars", player.stars),
                               ("gold_earned", player.gold_earned), ("cards_earned", player.cards_earned),
                               ("salvage", player.salvage), ("name_changes", player.name_changes),
                               ("daily_bonus", (player.daily_bonus or {}).get("streak"))):
                if key in ud and value is not None:
                    ud[key] = _like(ud[key], value)
            if isinstance(ud.get("flags"), dict) and player.flags is not None:
                ud["flags"] = copy.deepcopy(player.flags)
            for key in ("name", "active_deck", "defense_deck"):
                if key in ud and getattr(player, key) not in (None, ""):
                    ud[key] = _like(ud[key], getattr(player, key)) if key != "name" else player.name
            if isinstance(ud.get("caps"), dict) and player.max_cards is not None:
                ud["caps"]["max_cards"] = _like(ud["caps"].get("max_cards"), player.max_cards)
            a = player.arena
            if a is not None:
                name, base, nxt = level_info(a["level"])
                for key, value in (("stamina", a["stamina"]), ("last_stam_update_time", a["stamina_updated"]),
                                   ("hunting_elo", a["rating"]), ("hunting_wins", a["hunting_wins"]),
                                   ("level", a["level"]), ("base_xp", base), ("next_xp", nxt),
                                   ("promotion_points", a["promotion_points"]),
                                   ("commander_unlock_rarity", a.get("unlock_rarity", 2))):
                    if key in ud:
                        ud[key] = _like(ud[key], value)
                if "level_name" in ud:
                    ud["level_name"] = name
        if "mission_completions" in body and player.missions is not None:
            body["mission_completions"] = completions_json(player.missions)
        if "max_energy" in body and not isinstance(body["max_energy"], (dict, list, bool)):
            body["max_energy"] = _like(body["max_energy"], player.max_energy)
        if "max_decks" in body and player.max_decks is not None:
            body["max_decks"] = _like(body["max_decks"], player.max_decks)
        if "max_stamina" in body and player.arena is not None and not isinstance(body["max_stamina"], (dict, list)):
            body["max_stamina"] = _like(body["max_stamina"], player.arena["max_stamina"])
        if isinstance(body.get("user_cards"), dict):
            # A lock as "is_locked":"1" (the game reads 1, "1" or true; never recorded).
            body["user_cards"] = {cid: {"num_owned": str(c["num_owned"]), "num_used": str(c["num_used"]),
                                        **({"is_locked": "1"} if c.get("is_locked") else {})}
                                  for cid, c in player.cards.items()}
        if "user_decks" in body and player.decks is not None:
            body["user_decks"] = decks_json(player.decks)
        return body
