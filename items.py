"""Items: the player's inventory, from items.xml, and what using one does.
docs/protocol.md, "Items", has the evidence.

- **The data** (`items.xml`, which the client loads too): 72 items.
  - Shards and caches you collect and spend: Epic Shard (500 open an Epic pack:
    three base-set draws and one Epic+), Legendary Shard (1,000: three and one
    Legendary+), Vindicator Shard (1,000: a Vindicator Reactor), Salvage Cache (20:
    40 SP), Neuro Pack Shard (5,000: a card left blank).
  - Commander Respec ("reset a Legendary Fused Commander 1 time", the Restore tab's).
  - Energy Refill (a full energy refill), Massacre Refill and Repair Kit (no effect
    given).
  - 49 artifacts (one of each per player; sets of them craft commanders by the
    file's blueprints) and 13 event currencies and Guild Supplies.
- **What gives them** (in this server): brawl and guild war reward tiers (shards and
  Commander Respecs). Missions list an artifact each, but the real server didn't
  pay them (the recorded first wins of missions 1-9 had none), so they aren't paid.
- **The formats** (the client's code; never recorded with anything in them):
  - `user_items`: an object keyed by item id, each `{"id", "number"}`; `[]` when
    empty, as every recording had it. Any reply carrying it replaces the client's
    inventory.
  - `new_items` (a reward or claim reply): a list of `{"id", "number"}`, shown with
    the new cards.
- **Using one** (`consumeItem item_id`, the inventory's Use): takes `consume` of it
  (one if the file gives none) and gives what the file says.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import packs

ENERGY_REFILL = 1          # the energy bar's +: used in place of war bonds when owned
SALVAGE_POINTS = 1999      # never owned: shows SP given in new_items (ours: the file has it, unused)
EPIC_SHARD, LEGENDARY_SHARD, VINDICATOR_SHARD = 2000, 2001, 2002
SALVAGE_CACHE, COMMANDER_RESPEC = 2004, 2100


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    usable: bool = False
    consume: int = 0                         # how many one use takes (0: one)
    sp: int = 0                              # SP it gives
    refill: bool = False                     # a full energy refill
    pack: dict | None = None                 # {"name", "restrictions", "distribution"}: the pack it opens

    @property
    def cost(self) -> int:
        """How many one use takes."""
        return max(self.consume, 1)


class ItemError(Exception):
    """Why an item can't be used, in words the player can read."""


def load(asset_dir: Path) -> dict[int, Item]:
    """id -> Item, from items.xml (commented-out items left out)."""
    f = Path(asset_dir) / "items.xml"
    if not f.is_file():
        return {}
    out = {}
    for e in ET.parse(f).getroot().findall("item"):
        text = lambda tag: (e.findtext(tag) or "").strip()            # noqa: E731
        num = lambda tag: int(text(tag)) if text(tag).lstrip("-").isdigit() else 0   # noqa: E731
        pack = None
        p = e.find("pack")
        if p is not None:
            r, d = p.find("restrictions"), p.find("distribution")
            pack = {"name": text("name"), "restrictions": dict(r.attrib) if r is not None else {},
                    "distribution": {x.tag: int(x.text or 0) for x in (d if d is not None else [])}}
        iid = num("id")
        out[iid] = Item(iid, text("name"), usable=num("usable") == 1, consume=num("consume"), sp=num("sp"),
                        refill=num("give_energy") == 1 and num("refill") == 1, pack=pack)
    return out


# ---- the inventory (Player.items: {item id: count}) ----------------------------------------------

def owned(items: dict, item_id) -> int:
    return int(items.get(str(item_id), 0))


def add(items: dict, item_id, n: int) -> None:
    if n > 0:
        items[str(item_id)] = owned(items, item_id) + n


def take(items: dict, item_id, n: int) -> None:
    left = owned(items, item_id) - n
    if left < 0:
        raise ValueError(f"only {owned(items, item_id)} of item {item_id}")
    if left:
        items[str(item_id)] = left
    else:
        items.pop(str(item_id), None)


def user_items_json(items: dict) -> dict | list:
    """user_items: {"<id>": {"id", "number"}}, or [] for none (as recorded)."""
    return {str(i): {"id": int(i), "number": n} for i, n in sorted(items.items(), key=lambda kv: int(kv[0])) if n > 0} \
        or []


def new_items_json(gains: dict) -> list[dict]:
    """new_items: [{"id", "number"}] for what a reply just gave."""
    return [{"id": int(i), "number": n} for i, n in gains.items() if n > 0]


# ---- using one -----------------------------------------------------------------------------------

def item_pack(item: Item) -> packs.Pack | None:
    """The pack a shard opens, if it's one we can open (base set, level 1)."""
    return packs.mini_pack(item.pack) if item.pack else None


def check_use(item: Item | None, items: dict) -> None:
    """Raises ItemError unless the player can use the item now (what it gives aside)."""
    if item is None:
        raise ItemError("That item doesn't exist.")
    if not item.usable or not (item.refill or item.sp or item_pack(item)):
        raise ItemError(f"The {item.name} can't be used.")
    if owned(items, item.id) < item.cost:
        raise ItemError(f"You need x{item.cost} {item.name} to use this item.")
