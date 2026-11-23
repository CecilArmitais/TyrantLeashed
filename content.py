"""What's on offer in the game: the store's packs and promos, event boxes, the raid,
brawls, conquest, the objective challenge, guild wars, guild donations, other players as
arena rivals and the daily crate. (The events.xml events aren't built.)

Kept in state/content.toml, a file meant to be edited by hand. The first start
writes it with the defaults (the store as recorded, no boxes) and comments on every
setting. The server re-reads it whenever it changes; the game shows the new store
after a reload.

Standard library only (tomllib).
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from daily_crate import DEFAULT_EVERY_HOURS, DEFAULT_REWARDS, Reward

PACK_IDS = {"basic": "1", "epic": "1000"}       # the store's pack tiles (store_packs)

TEMPLATE = '''# What's on offer in this Tyrant Unleashed server: the store, event boxes, the raid,
# brawls, conquest, guild wars, guild donations, other players as arena rivals and the
# daily crate.
#
# Edit this file while the server runs; the game shows the change after a reload.
# Ids refer to the game's own data files (game/assets/mobile.tyrantonline.com/assets/).
# Delete the file to get the defaults back.

[store]
# The store's pack tiles: "basic" (Basic Pack, one card, 100 gold) and
# "epic" (Epic Pack, four cards with one Epic or better, 20 war bonds).
packs = ["basic", "epic"]

# The store's promos, each one of:
#   { mini_pack = <id> }   a mini pack (mini_packs_36i.xml) at the file's own price, or
#                          with your own: gold = N or war_bonds = N. 389 of its 394 open:
#                          faction boxes and packs (11-15, 21-25), level packs (52, 72-76),
#                          event and reward card packs (53, 66-70, 78-82, 91-93), the
#                          "exclusive" packs (Triton Pack 59...), Quadras (2000...), the
#                          Fusion, Fused and Complete packs (50, 88, 83, 100-104), Salvage
#                          Coins (51, 84, 90, 99), single cards (type 13, 1001-1050: their
#                          price rises with each purchase)...
#   { card = <id> }        one copy of a card for war bonds, dearer with each purchase:
#                          the file's table for its rarity (an Epic 10, 15, 20, 25, then
#                          +5; Legendary 30-60, +10; Vindicator 50-110, +20; Mythic
#                          80-170, +30). The recorded store sold Tabitha's Trooper so.
#   { bundle = <id>, gold = N }  (or war_bonds = N) one of the recorded real-money
#                          bundles, sold for gold or war bonds instead (there's no
#                          Kongregate to pay through offline):
#                          191 Platinum Crate (250 war bonds, Kronus, 50 SP);
#                          200080 Elite Pack (100 war bonds, +50 max energy, +150 card
#                          slots, +10 max stamina, +350 max SP; once);
#                          201607 New Buyer Surge (400 war bonds; once);
#                          202122 550 WB Surge Pack (550 war bonds; once).
promos = [
    { mini_pack = 23, war_bonds = 5 },     # Blight Pack: two Bloodthirsty cards, Epic or better
    { mini_pack = 49, gold = 400 },        # Basic Pack x4
    { mini_pack = 48, gold = 2000 },       # Basic Pack x20
    { card = 70584 },                      # Tabitha's Trooper: 80 war bonds, then 110, 140...
]

# Keep the recorded store's other items: the store upgrades, and the real-money
# bundles not set above (those are shown as recorded and can't be bought).
recorded_extras = true

[boxes]
# Event boxes (event_box.xml) for sale in the store, by id. None by default.
# A box holds a set number of copies of each of its cards; each purchase takes one of
# the copies left, and once all are bought the box starts over (caches only allow a few
# rounds). The last box the game ran was the 13th Hour Box (8099, Jul 9 - Oct 2, 2026):
#   on_offer = [8099]
# Tiered boxes are a row of small boxes, each opening when the one before is bought
# out, the last holding one Vindicator: Wreckage Tier (126) and Devour Tier (128).
on_offer = []

[raids]
# A raid to fight (raids_x42.xml), by id; 0 for none (the default). The last raid the
# game ran was Sentinel Icarus (161, Nov 5-8, 2026):
#   raid = 161
# Raids are in the Guild screen's Wars tab: a guild fights the boss together, so you need
# to be in one (each guild has its own run of the raid).
raid = 0
# How long each raid runs (the real ones ran three days), then a day to claim the reward;
# after that the next one starts.
days = 3
# The boss's health as a percentage of the real one, which was made for a whole guild
# of up to 50 players. Lower it for a server with few players.
health_percent = 100

[brawls]
# A brawl to run (battle_events_h52.xml), by id; 0 for none (the default). The last solo
# brawl the game ran was Winter's Kiss Brawl (344, Nov 13-16, 2026), the last guild one
# the Whiteout Guild Brawl (343):
#   brawl = 344
# It runs for its own length (most ran 78 hours), then three days to claim; after that it
# starts again. It's on the Battle screen, for players rated above 100: each fight, for
# 1 of 25 brawl energy (one back every 30 minutes), is against another player near your
# brawl points, their defense deck played by the AI. A guild brawl needs a guild, and
# ranks guilds.
brawl = 0

[conquest]
# A conquest to run (conquest.xml), by id; 0 for none (the default). The last one the
# game ran was the Aftermath Conquest (33, May 2019):
#   conquest = 33
# It runs for its own length (76 hours), then three days to claim; after that it starts
# again. It's on the Guild screen's Wars tab (the Conquest button): members fight in the
# map's zones against members of other guilds (their defense decks, played by the AI),
# each win earning the guild 50 influence there (5 for a loss), for 1 of 25 conquest
# energy. Every 4 hours each zone pays the guilds conquest points and fortresses by their
# influence rank; at the end, members claim rewards by their guild's rank and their own
# influence. It takes at least two guilds.
conquest = 0

[objectives]
# A timed objective challenge to run (achievements.xml), by the id of any objective in it;
# 0 for none (the default). The objectives with dates (PvP challenges, the 2015 Legendary
# Quest) are on only when picked here; the undated ones (mutant mission levels, the
# undated Legendary Quests) are always on. The challenge runs server-wide for its own
# length, then starts again (an open-ended one runs until this changes). The last ones:
#   challenge = 3580     # Living Mirrors PVP Challenge (28 days, Oct-Nov 2026)
#   challenge = 3450     # Extreme PVP Challenge (2025, no end)
#   challenge = 3400     # Tyrant Giveaways (2025, 334 days)
# Most need mission 17 complete to show (they count in the background before that).
challenge = 0

[wars]
# A guild war event to run (faction_wars_fp3.xml), by id; 0 for none (the default). The
# last one the game ran was the Hypothermia War (94, Nov 19-23, 2026):
#   war = 94
# Wars are in the Guild screen's Wars tab, which shows a raid instead while one is set
# ([raids] raid), so set raid = 0 to have wars. An officer or the leader presses Start;
# the guild waits for another guild that has too, and the two are matched: an 8-hour
# war (the first 30 minutes to prepare), then both wait for their next match. Members
# attack the other guild's fortress with their own decks; its members' defense decks
# defend, played by the AI. After the event, members claim rewards by their guild's
# rank and their own points; after that the next event starts.
war = 0
# How long each event runs (the real ones ran three days), then two days to claim.
days = 3
# The real game's matchmaking rules: a guild needs this many members to start a war,
# guilds more than point_gap_percent apart in the event's points aren't matched, nor
# guilds that met in the last rematch_hours. With few guilds, loosen them (e.g. 1, 100
# and 0), or two guilds may only meet once an event.
# A war's placement brawl ([brawls] brawl, e.g. 345, Hypothermia Placements, a day
# before war 94) seeds it: guilds meet the waiting guild closest to them in its points.
# Its fights also bring each guild's fortresses, as war fights do.
min_members = 5
point_gap_percent = 20
rematch_hours = 8

[guilds]
# Guilds belong to the server and are shared by its players (state/guilds/). Members can
# donate gold for guild points, which buy the guild's fortress cards. The real game had
# donations switched off by the end; a guild point cost this much gold (the client's
# default).
donations = true
gold_per_point = 100

[arena]
# The Battle button's rivals. Besides the AI rivals, other players of this server can be
# on your list: those within rank_reach battle ranks of yours (the real game's lists, at
# rank 5, had players of ranks 4 to 6), each with player_chance of being picked whenever
# the list changes (after a win, or a refresh). Fighting a player is fighting their
# defense deck, played by the AI; they get a report of it. 0 for AI rivals only.
player_chance = 0.5
rank_reach = 1

[daily_bonus]
# The crate on the home screen. It's ready again this many hours after it's opened (a
# new save's first one 30 minutes after it starts, as recorded).
every_hours = 24
# What it gives, by days in a row: the first entry on the first day, and so on, the last
# one repeating. Each gives gold, war_bonds, sp and/or card (a card id). Nothing recorded
# shows what the real crate gave, so these are ours.
rewards = [
    { gold = 100 },
    { gold = 150 },
    { gold = 200 },
    { gold = 250 },
    { sp = 10 },
    { gold = 300 },
    { war_bonds = 5 },
]
'''


@dataclass(frozen=True)
class Promo:
    mini_pack: int | None = None
    gold: int | None = None
    war_bonds: int | None = None
    card: int | None = None          # one copy of a card, its price rising with each purchase
    bundle: int | None = None        # a recorded real-money bundle (store.BUNDLES)

    @property
    def kind(self) -> str:
        return "card" if self.card is not None else "bundle" if self.bundle is not None else "mini_pack"


@dataclass
class Content:
    packs: tuple[str, ...] = ("basic", "epic")
    promos: tuple[Promo, ...] = (Promo(23, war_bonds=5), Promo(49, gold=400), Promo(48, gold=2000),
                                 Promo(card=70584))
    recorded_extras: bool = True
    boxes: tuple[int, ...] = ()
    raid: int = 0
    raid_days: int = 3
    raid_health_percent: float = 100.0
    guild_donations: bool = True
    gold_per_point: int = 100
    brawl_event: int = 0
    conquest_event: int = 0
    challenge: int = 0
    war_event: int = 0
    war_days: int = 3
    war_min_members: int = 5
    war_point_gap_percent: float = 20.0
    war_rematch_hours: float = 8.0
    arena_player_chance: float = 0.5
    arena_rank_reach: int = 1
    crate_every_hours: int = DEFAULT_EVERY_HOURS
    crate_rewards: tuple[Reward, ...] = DEFAULT_REWARDS
    problems: list[str] = field(default_factory=list)      # what in the file couldn't be used


def parse(text: str) -> Content:
    """The file's settings; anything unusable is left out and noted in `problems`."""
    data = tomllib.loads(text)
    store, boxes = data.get("store") or {}, data.get("boxes") or {}
    c = Content()
    problems = []
    packs = store.get("packs", list(c.packs))
    c.packs = tuple(p for p in packs if p in PACK_IDS)
    problems += [f"store.packs: unknown pack {p!r} (use basic or epic)" for p in packs if p not in PACK_IDS]
    promos = []
    defaults = [{k: v for k, v in (("mini_pack", x.mini_pack), ("card", x.card), ("bundle", x.bundle),
                                   ("gold", x.gold), ("war_bonds", x.war_bonds)) if v is not None} for x in c.promos]
    for i, p in enumerate(store.get("promos", defaults)):
        kinds = [k for k in ("mini_pack", "card", "bundle") if k in p]
        gold, wb = p.get("gold"), p.get("war_bonds")
        price_ok = all(v is None or (isinstance(v, int) and v >= 0) for v in (gold, wb)) \
            and not (gold is not None and wb is not None)
        if len(kinds) != 1 or not isinstance(p[kinds[0]], int) or not price_ok:
            problems.append(f"store.promos[{i}]: needs one of mini_pack, card or bundle (an id), and at most one "
                            "price, gold or war_bonds")
            continue
        if kinds == ["bundle"] and gold is None and wb is None:
            problems.append(f"store.promos[{i}]: a bundle needs a price, gold or war_bonds")
            continue
        promos.append(Promo(**{kinds[0]: p[kinds[0]]}, gold=gold, war_bonds=wb))
    c.promos = tuple(promos)
    c.recorded_extras = bool(store.get("recorded_extras", True))
    on_offer = boxes.get("on_offer", [])
    c.boxes = tuple(b for b in on_offer if isinstance(b, int))
    problems += [f"boxes.on_offer: {b!r} isn't a box id" for b in on_offer if not isinstance(b, int)]
    raids = data.get("raids") or {}
    if isinstance(raids.get("raid", 0), int) and raids.get("raid", 0) >= 0:
        c.raid = raids.get("raid", 0)
    else:
        problems.append("raids.raid: needs a raid id (0 for none)")
    if "guild_name" in raids:
        problems.append("raids.guild_name: no longer used (guilds are made in the game now)")
    if isinstance(raids.get("days", 3), int) and raids.get("days", 3) > 0:
        c.raid_days = raids.get("days", 3)
    else:
        problems.append("raids.days: needs a whole number of days, 1 or more")
    pct = raids.get("health_percent", 100)
    if isinstance(pct, (int, float)) and pct > 0:
        c.raid_health_percent = float(pct)
    else:
        problems.append("raids.health_percent: needs a number above 0")
    brawl = (data.get("brawls") or {}).get("brawl", 0)
    if isinstance(brawl, int) and not isinstance(brawl, bool) and brawl >= 0:
        c.brawl_event = brawl
    else:
        problems.append("brawls.brawl: needs a brawl id (0 for none)")
    ch = (data.get("objectives") or {}).get("challenge", 0)
    if isinstance(ch, int) and not isinstance(ch, bool) and ch >= 0:
        c.challenge = ch
    else:
        problems.append("objectives.challenge: needs an objective id (0 for none)")
    cq = (data.get("conquest") or {}).get("conquest", 0)
    if isinstance(cq, int) and not isinstance(cq, bool) and cq >= 0:
        c.conquest_event = cq
    else:
        problems.append("conquest.conquest: needs a conquest id (0 for none)")
    wars = data.get("wars") or {}
    whole = lambda v: isinstance(v, int) and not isinstance(v, bool)          # noqa: E731
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    for key, attr, ok, default, words in (
            ("war", "war_event", lambda v: whole(v) and v >= 0, 0, "needs a war event id (0 for none)"),
            ("days", "war_days", lambda v: whole(v) and v > 0, 3, "needs a whole number of days, 1 or more"),
            ("min_members", "war_min_members", lambda v: whole(v) and v >= 1, 5, "needs a whole number, 1 or more"),
            ("point_gap_percent", "war_point_gap_percent", lambda v: number(v) and v >= 0, 20,
             "needs a number, 0 or more"),
            ("rematch_hours", "war_rematch_hours", lambda v: number(v) and v >= 0, 8, "needs a number, 0 or more")):
        value = wars.get(key, default)
        if ok(value):
            setattr(c, attr, value)
        else:
            problems.append(f"wars.{key}: {words}")
    guilds = data.get("guilds") or {}
    if isinstance(guilds.get("donations", True), bool):
        c.guild_donations = guilds.get("donations", True)
    else:
        problems.append("guilds.donations: needs true or false")
    gpp = guilds.get("gold_per_point", 100)
    if isinstance(gpp, int) and not isinstance(gpp, bool) and gpp > 0:
        c.gold_per_point = gpp
    else:
        problems.append("guilds.gold_per_point: needs a whole number of gold, 1 or more")
    arena = data.get("arena") or {}
    chance = arena.get("player_chance", 0.5)
    if isinstance(chance, (int, float)) and not isinstance(chance, bool) and 0 <= chance <= 1:
        c.arena_player_chance = float(chance)
    else:
        problems.append("arena.player_chance: needs a number from 0 to 1")
    reach = arena.get("rank_reach", 1)
    if isinstance(reach, int) and not isinstance(reach, bool) and reach >= 0:
        c.arena_rank_reach = reach
    else:
        problems.append("arena.rank_reach: needs a whole number of ranks, 0 or more")
    crate = data.get("daily_bonus") or {}
    every = crate.get("every_hours", DEFAULT_EVERY_HOURS)
    if isinstance(every, int) and every > 0:
        c.crate_every_hours = every
    else:
        problems.append("daily_bonus.every_hours: needs a whole number of hours, 1 or more")
    rewards = []
    for i, r in enumerate(crate.get("rewards", None) or []):
        keys = {"gold", "war_bonds", "sp", "card"}
        if not isinstance(r, dict) or not r or set(r) - keys or not all(isinstance(v, int) and v >= 0
                                                                      for v in r.values()):
            problems.append(f"daily_bonus.rewards[{i}]: use gold, war_bonds, sp and/or card, as whole numbers")
            continue
        rewards.append(Reward(gold=r.get("gold", 0), war_bonds=r.get("war_bonds", 0), sp=r.get("sp", 0),
                              card=r.get("card") or None))
    if rewards:
        c.crate_rewards = tuple(rewards)
    c.problems = problems
    return c


class ContentStore:
    """Reads content.toml (writing the commented defaults the first time), re-reading it
    when it changes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._content: Content | None = None
        self._mtime: float | None = None

    def load(self) -> Content:
        if not self.path.is_file():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(TEMPLATE, "utf-8")
        mtime = self.path.stat().st_mtime
        if self._content is None or mtime != self._mtime:
            try:
                self._content = parse(self.path.read_text("utf-8"))
            except tomllib.TOMLDecodeError as e:
                self._content = Content(problems=[f"{self.path.name} isn't valid TOML ({e}); using the defaults"])
            self._mtime = mtime
        return copy.deepcopy(self._content)
