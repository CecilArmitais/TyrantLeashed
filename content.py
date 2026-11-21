"""What's on offer in the game: the store's packs and promos, event boxes, the raid,
guild donations, other players as arena rivals and the daily crate. Later also events and guild brawls.

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
BOX_DAYS_SHOWN = 7                              # the store shows a box with this long left (ours)
BOX_ITEM_TYPE = 8                               # store_promos item_type the game shows as an event box
TIERED_ITEM_TYPE = 10                           # ...and as a tiered box (a row of tiers that open in turn)
BOX_BANNER_TEXT = "New Event Box!"              # the banner's ribbon for a box (ours; "" shows "0")

TEMPLATE = '''# What's on offer in this Tyrant Unleashed server: the store, event boxes, the raid,
# guild donations, other players as arena rivals and the daily crate. (Events and guild brawls will go here too once the
# server has them.)
#
# Edit this file while the server runs; the game shows the change after a reload.
# Ids refer to the game's own data files (game/assets/mobile.tyrantonline.com/assets/).
# Delete the file to get the defaults back.

[store]
# The store's pack tiles: "basic" (Basic Pack, one card, 100 gold) and
# "epic" (Epic Pack, four cards with one Epic or better, 20 war bonds).
packs = ["basic", "epic"]

# Promos made from mini packs (mini_packs_36i.xml): its id, and a price in gold or war_bonds.
# Packs limited to the base set at level 1 can be opened: faction packs and boxes
# (ids 11-15, 21-25), the Basic Pack bundles (48, 49), Legendary Base (97, 98)...
promos = [
    { mini_pack = 23, war_bonds = 5 },     # Blight Pack: two Bloodthirsty cards, Epic or better
    { mini_pack = 49, gold = 400 },        # Basic Pack x4
    { mini_pack = 48, gold = 2000 },       # Basic Pack x20
]

# Keep the other recorded store items: Tabitha's Trooper, the upgrades and the
# real-money bundles (they're shown as recorded; buying them isn't supported yet).
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
    mini_pack: int
    gold: int | None = None
    war_bonds: int | None = None


@dataclass
class Content:
    packs: tuple[str, ...] = ("basic", "epic")
    promos: tuple[Promo, ...] = (Promo(23, war_bonds=5), Promo(49, gold=400), Promo(48, gold=2000))
    recorded_extras: bool = True
    boxes: tuple[int, ...] = ()
    raid: int = 0
    raid_days: int = 3
    raid_health_percent: float = 100.0
    guild_donations: bool = True
    gold_per_point: int = 100
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
    for i, p in enumerate(store.get("promos", [{"mini_pack": x.mini_pack, "gold": x.gold, "war_bonds": x.war_bonds}
                                                for x in c.promos])):
        gold, wb = p.get("gold"), p.get("war_bonds")
        if not isinstance(p.get("mini_pack"), int) or (gold is None) == (wb is None) \
                or not isinstance(gold if gold is not None else wb, int) or (gold if gold is not None else wb) < 0:
            problems.append(f"store.promos[{i}]: needs a mini_pack id and one price, gold or war_bonds")
            continue
        promos.append(Promo(p["mini_pack"], gold=gold, war_bonds=wb))
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


# ---- the store, as the game gets it ---------------------------------------------------------------

def promo_entry(mp_id, mp: dict, promo: Promo, available_cards: list[int] | None) -> dict:
    """A store_promos entry for a mini pack, in the recorded entries' shape (item_type 3)."""
    entry = {"id": str(mp_id), "name": mp["name"], "type": mp.get("type", ""), "desc": mp.get("desc", "")}
    for k in ("picture", "banner_picture", "icon"):
        if mp.get(k):
            entry[k] = mp[k]
    if promo.gold is not None:
        entry["gold_cost"] = str(promo.gold)
    else:
        entry["wb_cost"] = str(promo.war_bonds)
    entry.update({"item_type": 3, "item_id": int(mp_id), "sub_item_id": "gold" if promo.gold is not None else "wb"})
    if available_cards:
        entry["available_cards"] = [str(c) for c in available_cards]
    return entry


def box_entry(box, state: dict, now: int, next_deal=None) -> dict:
    """A store_promos entry for an event box, in the shape the game reads (found in its
    code, ServicePromoItemData/ServiceGachaData, and checked in the game): item_type 8
    shows the event box screen; `card` lists the box's cards with their copies, and
    `cards_received` the copies bought this round, by card id, as strings (the game
    reads a number there as 0); `box_discount` is the deal on offer. Buying sends
    buyStorePromoTokens item_type=8 (with box_discount_id for the deal)."""
    entry = _box_base(box, now, BOX_ITEM_TYPE)
    got = state.get("received") or {}
    entry["card"] = [{"id": str(cid), "count": n} for cid, n in box.cards]
    entry["cards_received"] = {str(cid): str(int(got[str(cid)])) for cid, _ in box.cards if got.get(str(cid))}
    if next_deal is not None:
        entry["box_discount"] = {"box_discount_id": next_deal.id, "name": next_deal.name,
                                 "desc": next_deal.desc, "wb_cost": str(next_deal.wb_cost)}
    return entry


def tiered_box_entry(box, state: dict, now: int, open_tier: int) -> dict:
    """A store_promos entry for a tiered box, in the shape the game reads (its code,
    ServiceMultiGachaData; checked in the game): item_type 10 shows the tiered box
    screen; `subbox` lists the tiers, each {"card": [{"id", "count"}]}, and
    `cards_received` the copies bought, by tier number (from 1) and card id, as strings.
    The game opens the tier after the last one bought out, so the finished tiers are
    sent full. `cards_received` must be there (the game reads it unchecked). Buying
    sends buyStorePromoTokens item_type=10."""
    entry = _box_base(box, now, TIERED_ITEM_TYPE)
    got = state.get("received") or {}
    entry["subbox"] = [{"card": [{"id": str(cid), "count": n} for cid, n in t]} for t in box.tiers]
    received = {str(i + 1): {str(cid): str(n) for cid, n in t} for i, t in enumerate(box.tiers[:open_tier])}
    current = {str(cid): str(int(got[str(cid)])) for cid, _ in box.tiers[open_tier] if got.get(str(cid))}
    if current:
        received[str(open_tier + 1)] = current
    entry["cards_received"] = received
    return entry


def _box_base(box, now: int, item_type: int) -> dict:
    """What every box entry has: the name, price, time left (always 7 days) and pictures."""
    entry = {"item_type": item_type, "item_id": box.id, "id": box.id, "name": box.name, "desc": box.desc,
             "wb_cost": str(box.wb_cost), "sub_item_id": "wb", "picture": "tyrantpack",
             "start_time": now - 3600, "end_time": now + BOX_DAYS_SHOWN * 86400}
    for src, key in (("store_banner", "icon"), ("banner_picture", "banner_picture"),
                     ("anim_picture", "anim_picture"), ("web_banner", "web_banner")):
        if box.picture(src):
            entry[key] = box.picture(src)
    return entry


def apply_to_store(body: dict, content: Content, promo_entries: list[dict], box_entries: list[dict],
                   mini_pack_ids: set[str]) -> dict:
    """The store parts of a reply (store_packs, store_promos, store_banner), in place, from
    the content file. Recorded mini pack promos are replaced by the content file's; the
    other recorded items stay if recorded_extras is on."""
    if isinstance(body.get("store_packs"), dict):
        keep = {PACK_IDS[p] for p in content.packs}
        body["store_packs"] = {k: v for k, v in body["store_packs"].items() if k in keep}
    if isinstance(body.get("store_promos"), list):
        # The recorded order, with each recorded mini pack promo replaced by the content
        # file's (or dropped); new promos follow the last one, boxes come first.
        fresh = {str(e["item_id"]): e for e in promo_entries}
        promos, last = [], 0
        for p in body["store_promos"]:
            if isinstance(p, dict) and str(p.get("item_type")) == "3" and str(p.get("item_id")) in mini_pack_ids:
                if str(p.get("item_id")) in fresh:
                    promos.append(fresh.pop(str(p.get("item_id"))))
                    last = len(promos)
            elif content.recorded_extras:
                promos.append(p)
        rest = [e for e in promo_entries if str(e["item_id"]) in fresh]
        body["store_promos"] = box_entries + promos[:last] + rest + promos[last:]
        banner = body.get("store_banner")
        offered = {str(p.get("item_id")) for p in body["store_promos"]}
        lead = (box_entries + promo_entries + [None])[0]
        if box_entries or not isinstance(banner, dict) or str(banner.get("promo_id")) not in offered:
            if lead is None:
                body.pop("store_banner", None)
            else:
                body["store_banner"] = {"name": lead["name"], "picture": lead.get("icon", ""),
                                        "promo_id": lead["item_id"],
                                        "discount": BOX_BANNER_TEXT if lead in box_entries
                                        else (banner or {}).get("discount", ""),
                                        "end_time": lead.get("end_time", 1)}
    return body
