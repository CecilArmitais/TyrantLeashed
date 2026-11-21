"""Buying card packs: the Basic Pack, the gold Basic Pack bundles and the Epic Pack.
docs/protocol.md, "Packs", has the evidence.

- The Basic Pack (`store_packs` pack_id 1, 100 gold): one card from the base set (set
  1000) at level 1. Recorded 5 times; all 25 recorded basic draws were such cards.
- The bundles "Basic Pack x4" and "x20" (`store_promos` items 49 and 48, 400 and 2000
  gold; mini_packs_36i.xml: type 7, set 1000, level 1, <basic>4</basic> / 20): that
  many basic draws. Any mini pack limited to set 1000 at level 1, with only
  basic/rare/epic/legendary counts, works the same way.
- The Epic Pack (pack_id 1000, 20 war bonds): "four cards with one guaranteed Epic or
  higher": three basic draws and one from the base set's Epic+ column.
- Faction packs such as the Blight Pack (`store_promos` item 23, 5 war bonds; type 1,
  `<restrictions type='3' level='1' set='1000'/>`, `<epic>2</epic>`): "Two Bloodthirsty
  cards from the base set, all guaranteed Epic or higher!" Two Epic+ draws from that
  faction's base set. Its column's Vindicator row is dropped (the faction has none)
  and the rest keep their proportions: 73.7% epic, 26.3% legendary.

Each card's rarity is rolled on its own ("each card is dropped at a per card basis")
from the game's published table (the store's "View Drop Rates" page, archived):
Base Set 79.94% common, 15% rare, 5% epic, 0.05% legendary, 0.01% vindicator; Rare+
79/15/5/1; Epic+ 70/25/5; Legendary+ 75/25. Then a card of that rarity, all equally
likely (ours: the page gives rarities only).

During the tutorial the bought card also goes into the deck: the recorded tutorial
purchase did, the account's later ones didn't.
"""

from __future__ import annotations

import dataclasses
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from battle.cards import ASSAULT, STRUCTURE, CardDB

BASE_SET = 1000
# (rarity, %): 1 common, 2 rare, 3 epic, 4 legendary, 5 vindicator.
RATES = {
    "basic": ((1, 79.94), (2, 15.0), (3, 5.0), (4, 0.05), (5, 0.01)),
    "rare": ((2, 79.0), (3, 15.0), (4, 5.0), (5, 1.0)),
    "epic": ((3, 70.0), (4, 25.0), (5, 5.0)),
    "legendary": ((4, 75.0), (5, 25.0)),
    # The official post's other columns (docs/protocol.md, "Packs"), for reward packs:
    "epic_vindi_possible": ((3, 70.0), (4, 25.0), (5, 5.0)),
    "epic_mythic_possible": ((3, 69.0), (4, 25.0), (5, 5.0), (6, 1.0)),
    "legend_mythic_possible": ((4, 70.0), (5, 25.0), (6, 5.0)),
    "epic_strict": ((3, 100.0),), "legendary_strict": ((4, 100.0),), "vindicator_strict": ((5, 100.0),),
}
SETS = {"base": BASE_SET}
BASIC_PACK, EPIC_PACK = 1, 1000


@dataclass(frozen=True)
class Pack:
    name: str
    draws: tuple[tuple[str, int], ...]      # (distribution, how many cards)
    gold: int | None = None                  # its price in gold...
    war_bonds: int | None = None             # ...or in war bonds
    faction: int | None = None               # a faction pack's faction (restrictions type='3')
    set_id: int = BASE_SET                   # the set it draws from (reward packs: 4500)


class PackError(Exception):
    """Why a pack can't be bought, in words the player can read."""


def base_pool(cards: CardDB, faction: int | None = None, set_id: int = BASE_SET) -> dict[int, list[int]]:
    """rarity -> the level-1 cards of a set a pack can give (the base set: 111 in all;
    22 of a faction: 11 commons, 5 rares, 5 epics, 1 legendary)."""
    pools = getattr(cards, "_pack_pools", None)
    if pools is None:
        pools = cards._pack_pools = {}
    key = (faction, set_id)
    if key not in pools:
        pool = {}
        for c in cards.cards.values():
            if c.set == set_id and c.level == 1 and c.fusion_level == 0 and c.type in (ASSAULT, STRUCTURE) \
                    and (faction is None or c.faction == faction):
                pool.setdefault(c.rarity, []).append(c.id)
        pools[key] = {r: sorted(ids) for r, ids in pool.items()}
    return pools[key]


def rates(distribution: str, pool: dict[int, list[int]]) -> tuple[tuple[int, float], ...]:
    """The column's rarities that the pool has, with their shares. A rarity the pool
    lacks (a faction pack's Vindicator: the base set's only one is Progenitor) is left
    out, and the rest keep their proportions (ours: the page doesn't say)."""
    return tuple((r, w) for r, w in RATES[distribution] if pool.get(r))


def draw(cards: CardDB, distribution: str, rng: random.Random, faction: int | None = None,
         set_id: int = BASE_SET) -> int:
    pool = base_pool(cards, faction, set_id)
    rarities, weights = zip(*rates(distribution, pool))
    rarity = rng.choices(rarities, weights=weights)[0]
    return rng.choice(pool[rarity])


def open_pack(pack: Pack, cards: CardDB, rng: random.Random) -> list[int]:
    """The cards in one pack, in order (the Epic Pack's guaranteed card last)."""
    return [draw(cards, dist, rng, pack.faction, pack.set_id) for dist, n in pack.draws for _ in range(n)]


def store_pack(start: dict, pack_id) -> Pack | None:
    """A `store_packs` entry (buyPackGold / buyPackTokens) as a Pack, if we can open it."""
    entry = (start.get("store_packs") or {}).get(str(pack_id))
    if not isinstance(entry, dict):
        return None
    pid = int(pack_id)
    if pid == BASIC_PACK:
        draws = (("basic", 1),)
    elif pid == EPIC_PACK:
        draws = (("basic", 3), ("epic", 1))
    else:
        return None
    gold, wb = entry.get("gold_cost"), entry.get("wb_cost")
    return Pack(entry.get("name") or f"pack {pid}", draws, gold=int(gold) if gold not in (None, "") else None,
                war_bonds=int(wb) if wb not in (None, "") else None)


def load_mini_packs(asset_dir: Path, file_name: str = "mini_packs_36i.xml") -> dict[str, dict]:
    """id -> {"name", "restrictions": {...}, "distribution": {name: count}, and the store
    fields it has: type, desc, picture, banner_picture, icon, gold_cost, wb_cost} from
    the mini pack file."""
    f = Path(asset_dir) / file_name
    if not f.is_file():
        files = sorted(Path(asset_dir).glob("mini_packs_*.xml"))
        if not files:
            return {}
        f = files[-1]
    out = {}
    for mp in ET.parse(f).getroot().findall("mini_pack"):
        mid = (mp.findtext("id") or "").strip()
        r = mp.find("restrictions")
        dist = mp.find("distribution")
        out[mid] = {"name": (mp.findtext("name") or "").strip(),
                    "restrictions": dict(r.attrib) if r is not None else {},
                    "distribution": {d.tag: int(d.text or 0) for d in (dist if dist is not None else [])}}
        for k in ("type", "desc", "picture", "banner_picture", "icon", "gold_cost", "wb_cost"):
            if (mp.findtext(k) or "").strip():
                out[mid][k] = mp.findtext(k).strip()
    return out


def mini_pack(mp: dict, gold: int | None = None, war_bonds: int | None = None) -> Pack | None:
    """A mini pack as a Pack, if it's one we can open: limited to the base set at level 1,
    perhaps to one faction (type 1-5), with basic/rare/epic/legendary counts only."""
    r = {k: v for k, v in mp["restrictions"].items() if k != "no_preview"}
    faction = r.pop("type", None)
    if r != {"set": str(BASE_SET), "level": "1"} or (faction is not None and faction not in "12345")             or not mp["distribution"] or any(d not in RATES for d in mp["distribution"]):
        return None
    return Pack(mp["name"], tuple(mp["distribution"].items()), gold=gold, war_bonds=war_bonds,
                faction=int(faction) if faction else None)


def reward_pack(mp: dict) -> Pack | None:
    """A mini pack given as a reward (raids): like mini_pack, but from any set at level 1
    (the reward packs: set 4500, "one Epic reward card from recent events")."""
    r = {k: v for k, v in mp["restrictions"].items() if k not in ("no_preview",)}
    faction = r.pop("type", None)
    set_id = r.pop("set", None)
    if r != {"level": "1"} or not (set_id or "").isdigit() or (faction is not None and faction not in "12345") \
            or not mp["distribution"] or any(d not in RATES for d in mp["distribution"]):
        return None
    return Pack(mp["name"], tuple(mp["distribution"].items()), faction=int(faction) if faction else None,
                set_id=int(set_id))


STORE_PACK_DRAWS = {1: (("basic", 1),), 100: (("basic", 1), ("rare", 1)), 1000: (("basic", 3), ("epic", 1))}


def preview_cards(cards: CardDB, mp: dict) -> list[int] | None:
    """A promo's available_cards: every card the pack can give, highest rarity first (the
    recorded Blight Pack listed its faction's legendary, then its 5 epics by id), or None
    for no_preview packs."""
    pack = mini_pack(mp)
    if pack is None or mp["restrictions"].get("no_preview") == "1":
        return None
    pool = base_pool(cards, pack.faction)
    rarities = {r for dist, _ in pack.draws for r, _ in rates(dist, pool)}
    return [c for r in sorted(rarities, reverse=True) for c in pool[r]]


def promo_pack(start: dict, mini_packs: dict, item_id) -> Pack | None:
    """A `store_promos` mini pack (item_type 3) as a Pack, if it's one we can open (see
    mini_pack). Its price, in gold or war bonds, is the store entry's."""
    promos = start.get("store_promos") or []
    entry = next((p for p in (promos.values() if isinstance(promos, dict) else promos)
                  if isinstance(p, dict) and str(p.get("item_id")) == str(item_id)
                  and str(p.get("item_type")) == "3"), None)
    mp = mini_packs.get(str(item_id))
    if entry is None or mp is None:
        return None
    gold, wb = entry.get("gold_cost"), entry.get("wb_cost")
    pack = mini_pack(mp, gold=int(gold) if gold not in (None, "") else None,
                     war_bonds=int(wb) if wb not in (None, "") else None)
    return None if pack is None else dataclasses.replace(pack, name=entry.get("name") or mp["name"])


def new_cards_data(card_ids: list[int]) -> list[dict]:
    """new_cards_data as recorded: one entry per card, with how many this purchase gave."""
    counts: dict[int, int] = {}
    for cid in card_ids:
        counts[cid] = counts.get(cid, 0) + 1
    return [{"id": str(cid), "number": n} for cid, n in counts.items()]
