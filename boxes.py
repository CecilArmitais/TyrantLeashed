"""Event boxes: the boxes in event_box.xml, sold one card at a time from a stock that
runs out. docs/protocol.md, "Event boxes", has the evidence.

- A box lists its cards with a `count`: the copies of that card in the box (the 13th
  Hour Box: 6 Mythics x1, 6 Vindicators x2, 5 Legendaries x1, 5 Epics x1; 28 cards).
  Each purchase (60 war bonds) takes one of the copies still in it: the game's own
  text says "Each purchase randomly selects from the remaining cards!"
- Once every copy is bought the box starts over: a new round, all counts back (the fan
  wiki: "Since the Harvest Box, if a player purchases every card in an event pack, the
  counts reset to zero"). A box with `max_completes` (the caches: 4) sells out after
  that many rounds.
- The rarity of a pull: the official drop-rate post says boxes use the Epic -> Mythic
  (69/25/5/1) and Legendary -> Mythic (70/25/5) tables, and that rarities run "from the
  Minimum Rarity available to the Maximum Rarity available". So the table is chosen by
  the box's lowest and highest rarity; a rarity with no copies left this round is left
  out and the rest keep their proportions. The wiki agrees that the chance is not the
  remaining proportion. Within the rarity, each remaining copy is equally likely
  (ours).
- Deals (`box_discount`): a few cards at a discount, "Limit one per box": each can be
  bought once per round, except the repeatable "Buy x10". A deal card's rarity is at
  least its `min_rarity`.

Tiered boxes (`subbox`: Wreckage Tier and Devour Tier, May 2014) are a row of small
boxes: ten tiers of 10 cards, then a tier holding only the featured Vindicator. Only
the first tier is open; buying out a tier opens the next (the game: "After completing
a tier, you will unlock the next tier of cards. The last box has this great new
card!"). Each tier is pulled like a box of its own. Once the last tier is bought the
box starts over from the first, like the others (ours).
"""

from __future__ import annotations

import copy
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# The official tables (rarity, %): 1 common ... 5 vindicator, 6 mythic.
BOX_TABLES = {
    3: ((3, 69.0), (4, 25.0), (5, 5.0), (6, 1.0)),      # Epic -> Mythic: boxes from Epic up
    4: ((4, 70.0), (5, 25.0), (6, 5.0)),                # Legendary -> Mythic: boxes from Legendary up
}
PACK_TABLES = {                                         # older boxes start lower; the pack columns
    1: ((1, 79.94), (2, 15.0), (3, 5.0), (4, 0.05), (5, 0.01)),     # Base Set
    2: ((2, 79.0), (3, 15.0), (4, 5.0), (5, 1.0)),                   # Rare+
}
RARITY_NAMES = {1: "Common", 2: "Rare", 3: "Epic", 4: "Legendary", 5: "Vindicator", 6: "Mythic"}


@dataclass(frozen=True)
class Deal:
    id: int
    name: str
    desc: str
    wb_cost: int
    min_rarities: tuple[int, ...]            # one per card
    repeatable: bool = False


@dataclass(frozen=True)
class Box:
    id: int
    name: str
    desc: str
    wb_cost: int
    cards: tuple[tuple[int, int], ...]       # (card id, copies), in the file's order
    max_completes: int | None = None
    start_time: int | None = None
    end_time: int | None = None
    pictures: tuple[tuple[str, str], ...] = ()   # anim_picture, banner_picture, store_banner, web_banner...
    deals: tuple[Deal, ...] = ()
    tiers: tuple[tuple[tuple[int, int], ...], ...] = ()   # a tiered box's tiers, in order (cards is empty)

    @property
    def tiered(self) -> bool:
        return bool(self.tiers)

    @property
    def size(self) -> int:
        """Copies in one round: every tier's, for a tiered box."""
        return sum(n for tier in (self.tiers or (self.cards,)) for _, n in tier)

    def picture(self, key: str) -> str | None:
        return dict(self.pictures).get(key)


class BoxError(Exception):
    """Why a box can't be bought from, in words the player can read."""


def load_boxes(asset_dir: Path, file_name: str = "event_box.xml") -> dict[int, Box]:
    f = Path(asset_dir) / file_name
    if not f.is_file():
        return {}
    out = {}
    for b in ET.parse(f).getroot().findall("event_box"):
        try:
            bid = int((b.findtext("id") or "").strip())
        except ValueError:
            continue
        num = lambda tag: int(b.findtext(tag).strip()) if (b.findtext(tag) or "").strip().isdigit() else None
        deals = []
        for d in b.findall("box_discount"):
            deals.append(Deal(
                int(d.findtext("box_discount_id")), (d.findtext("name") or "").strip(),
                (d.findtext("desc") or "").strip(), int(d.findtext("wb_cost") or 0),
                tuple(int(c.get("min_rarity", 0)) for c in d.findall("cards/card")),
                repeatable=(d.findtext("is_repeatable") or "").strip() == "1"))
        # Tiers without cards are left out, as the game does (it numbers the rest).
        tiers = tuple(t for t in (tuple((int(c.get("id")), int(c.get("count", 1)))
                                        for c in sb.findall("card") if c.get("id"))
                                  for sb in b.findall("subbox")) if t)
        out[bid] = Box(
            bid, (b.findtext("name") or "").strip(), (b.findtext("desc") or "").strip(),
            num("wb_cost") or 0,
            tuple((int(c.get("id")), int(c.get("count", 1))) for c in b.findall("card") if c.get("id")),
            max_completes=num("max_completes"), start_time=num("start_time"), end_time=num("end_time"),
            pictures=tuple((k, b.findtext(k).strip()) for k in
                           ("anim_picture", "banner_picture", "store_banner", "web_banner", "web_picture")
                           if (b.findtext(k) or "").strip()),
            deals=tuple(deals), tiers=tiers)
    return out


# ---- a player's progress through a box ----------------------------------------------------------
# The save keeps, per box id: {"received": {card id: copies bought this round},
# "completes": rounds bought out, "deals": deal ids bought this round}, and for a tiered
# box "tier": the open tier, from 0 ("received" is that tier's).

def new_state() -> dict:
    return {"received": {}, "completes": 0, "deals": []}


def tier(box: Box, state: dict) -> int:
    """A tiered box's open tier, from 0."""
    return min(int(state.get("tier", 0)), len(box.tiers) - 1) if box.tiers else 0


def open_cards(box: Box, state: dict) -> tuple[tuple[int, int], ...]:
    """The cards a pull can come from now: the box's, or its open tier's."""
    return box.tiers[tier(box, state)] if box.tiers else box.cards


def remaining(box: Box, state: dict) -> dict[int, int]:
    """card id -> copies still in the box (its open tier) this round."""
    got = state.get("received") or {}
    return {cid: n - int(got.get(str(cid), 0)) for cid, n in open_cards(box, state)
            if n - int(got.get(str(cid), 0)) > 0}


def bought(box: Box, state: dict) -> int:
    """Copies bought this round (a tiered box counts its finished tiers too)."""
    done = sum(n for t in box.tiers[:tier(box, state)] for _, n in t)
    return done + sum(n for _, n in open_cards(box, state)) - sum(remaining(box, state).values())


def sold_out(box: Box, state: dict) -> bool:
    return box.max_completes is not None and int(state.get("completes", 0)) >= box.max_completes


def rarity_table(box: Box, rarity_of, cards=None) -> tuple[tuple[int, float], ...]:
    """The table for the box's cards (or `cards`, a tier's): from their lowest rarity to
    their highest (see the module notes). One rarity gives that rarity; cards no
    published table fits (Vindicator up, say) weigh their rarities by their copies (ours)."""
    cards = box.cards if cards is None else cards
    rarities = {rarity_of(cid) for cid, _ in cards}
    lo, hi = min(rarities), max(rarities)
    if lo == hi:
        return ((lo, 100.0),)
    table = BOX_TABLES.get(lo) or PACK_TABLES.get(lo)
    if table is None or table[-1][0] < hi:
        copies: dict[int, int] = {}
        for cid, n in cards:
            copies[rarity_of(cid)] = copies.get(rarity_of(cid), 0) + n
        return tuple(sorted((r, float(n)) for r, n in copies.items()))
    return table


def pull(box: Box, state: dict, rarity_of, rng: random.Random, min_rarity: int = 0) -> int:
    """One card from the box, or its open tier (state is updated). A bought-out tier
    opens the next; a finished round starts the next one."""
    if sold_out(box, state):
        raise BoxError(f"The {box.name} is sold out.")
    left = remaining(box, state)
    by_rarity: dict[int, list[tuple[int, int]]] = {}
    for cid, n in left.items():
        by_rarity.setdefault(rarity_of(cid), []).append((cid, n))
    table = [(r, w) for r, w in rarity_table(box, rarity_of, open_cards(box, state))
             if r in by_rarity and r >= min_rarity]
    if not table:
        # Rarities outside the table, or none left at min_rarity: fall back to the copies left.
        table = [(r, float(sum(n for _, n in v))) for r, v in by_rarity.items() if r >= min_rarity]
    if not table:
        raise BoxError(f"No {RARITY_NAMES.get(min_rarity, 'such')} or better card is left in the {box.name}"
                       " this round.")
    rarities, weights = zip(*table)
    rarity = rng.choices(rarities, weights=weights)[0]
    ids, copies = zip(*by_rarity[rarity])
    cid = rng.choices(ids, weights=copies)[0]
    got = state.setdefault("received", {})
    got[str(cid)] = int(got.get(str(cid), 0)) + 1
    if not remaining(box, state):
        if box.tiers and tier(box, state) + 1 < len(box.tiers):   # the next tier opens
            state["tier"], state["received"] = tier(box, state) + 1, {}
            return cid
        state["completes"] = int(state.get("completes", 0)) + 1  # bought out: the next round starts full
        state["received"], state["deals"] = {}, []
        if box.tiers:
            state["tier"] = 0
    return cid


def next_deal(box: Box, state: dict) -> Deal | None:
    """The deal on offer: the first one not bought this round (repeatable ones stay)."""
    used = {int(d) for d in state.get("deals") or []}
    return next((d for d in box.deals if d.repeatable or d.id not in used), None)


def buy_deal(box: Box, deal: Deal, state: dict, rarity_of, rng: random.Random) -> list[int]:
    """The deal's cards, one pull each at its min_rarity (state is updated)."""
    if not deal.repeatable and deal.id in {int(d) for d in state.get("deals") or []}:
        raise BoxError(f"The {deal.name} can be bought once per box.")
    completes = int(state.get("completes", 0))
    if box.max_completes is not None:
        stock = box.size - bought(box, state) + (box.max_completes - completes - 1) * box.size
        if stock < len(deal.min_rarities):
            raise BoxError(f"The {box.name} doesn't have {len(deal.min_rarities)} cards left.")
    work = copy.deepcopy(state)                         # all the deal's cards, or none
    got = [pull(box, work, rarity_of, rng, r) for r in deal.min_rarities]
    if not deal.repeatable and int(work.get("completes", 0)) == completes:
        work.setdefault("deals", []).append(deal.id)    # a round that ended during the deal starts afresh
    state.clear()
    state.update(work)
    return got
