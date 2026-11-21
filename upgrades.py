"""The Upgrade screen: upgrading cards with salvage points (SP), salvaging cards for SP,
locking cards against salvage, and fusing them. docs/protocol.md, "Upgrades, salvage
and fusion", has the evidence.

- The numbers are the game's own, from levels.xml (the fan wiki's Upgrade page has the
  same tables): for each rarity and level, what the card salvages for, and the SP to
  raise it a level. Fused cards (fusion level 1 "duals", 2 "quads") salvage for more
  (`fusion_salvage`); upgrading them costs the same.
  - Recorded: upgrading Trap Setter from level 1 to 2 cost 5 SP; salvaging ten level-1
    commons gave 10.
- Commanders cost twice as much to upgrade (`com_sp_mult` in the login data, 2;
  recorded: Cyrus 1 -> 2 cost 10) and can't be salvaged (the wiki).
- SP stops at `caps.max_salvage` (6000). The game asks "Salvage Points cap reached,
  trash this card anyway?", so a salvage at the cap still destroys the card.
- "Salvage all unfused Level 1 {0} cards for {1} SP?" (the game's words): the bulk
  salvage takes every level-1 unfused common (or rare) that isn't in the deck or locked.
  Recorded: the deck's commons were kept.
- Fusion (fusion_recipes_cj2.xml): a recipe's resources are exact card ids, so they
  must be at the level the recipe names (the wiki: "All cards must be fully upgraded");
  they're used up, and the fused card arrives at level 1. Fusing costs no SP.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from battle.cards import CardDB

COMMANDER = "commander"
DEFAULT_COMMANDER_MULT = 2      # com_sp_mult in the recorded login
DEFAULT_MAX_SALVAGE = 6000      # user_data.caps.max_salvage in the recorded login
RARITY_NAMES = {1: "common", 2: "rare", 3: "epic", 4: "legendary", 5: "vindicator", 6: "mythic"}


class UpgradeError(Exception):
    """Why something can't be done, in words the player can read."""


@dataclass(frozen=True)
class Level:
    salvage: int                       # SP for salvaging an unfused card at this level
    sp_cost: int | None                # SP to raise it to the next level (None: the last)
    fusion_salvage: dict = field(default_factory=dict)   # fusion level -> SP for a fused card


def load_levels(asset_dir: Path) -> dict[tuple[int, int], Level]:
    """(rarity, level) -> Level, from levels.xml."""
    f = Path(asset_dir) / "levels.xml"
    if not f.is_file():
        return {}
    out = {}
    for cl in ET.parse(f).getroot().findall("card_level"):
        cost = (cl.findtext("sp_cost") or "").strip()
        out[(int(cl.findtext("rarity")), int(cl.findtext("level")))] = Level(
            int(cl.findtext("salvage") or 0), int(cost) if cost else None,
            {int(fs.get("level")): int(fs.get("salvage")) for fs in cl.findall("fusion_salvage")})
    return out


def load_recipes(asset_dir: Path) -> dict[int, tuple[tuple[int, int], ...]]:
    """Fused card id -> its resources (card id, how many), from the fusion recipe file."""
    files = sorted(Path(asset_dir).glob("fusion_recipes*.xml"))
    if not files:
        return {}
    out = {}
    for r in ET.parse(files[-1]).getroot().findall("fusion_recipe"):
        res = tuple((int(x.get("card_id")), int(x.get("number", 1))) for x in r.findall("resource"))
        if (r.findtext("card_id") or "").strip().isdigit() and res:
            out[int(r.findtext("card_id"))] = res
    return out


@dataclass
class Rules:
    cards: CardDB
    levels: dict
    recipes: dict
    commander_mult: int = DEFAULT_COMMANDER_MULT
    max_salvage: int = DEFAULT_MAX_SALVAGE

    def card(self, card_id):
        card = self.cards.get(card_id) if str(card_id).lstrip("-").isdigit() else None
        if card is None:
            raise UpgradeError("That card doesn't exist.")
        return card

    def upgrade_cost(self, card_id) -> tuple[int, int]:
        """(the next level's card id, its SP cost)."""
        card = self.card(card_id)
        nxt = self.cards.next_level(card.id)
        row = self.levels.get((card.rarity, card.level))
        if nxt is None or row is None or row.sp_cost is None:
            raise UpgradeError(f"{card.name} is at its highest level.")
        return nxt.id, row.sp_cost * (self.commander_mult if card.type == COMMANDER else 1)

    def salvage_value(self, card_id) -> int:
        card = self.card(card_id)
        if card.type == COMMANDER:
            raise UpgradeError("Commanders can't be salvaged.")
        row = self.levels.get((card.rarity, card.level))
        if row is None:
            raise UpgradeError(f"{card.name} can't be salvaged.")
        return row.fusion_salvage.get(card.fusion_level, row.salvage) if card.fusion_level else row.salvage


# ---- what each action does to the save -------------------------------------------------------------

def upgrade(rules: Rules, player, card_id, in_deck: bool, active_deck: str | None) -> tuple[int, int]:
    """Upgrades one copy of a card: (the new card id, the SP it cost). A deck copy is
    upgraded in the deck when in_deck is set or there's no spare copy (ours)."""
    card = rules.card(card_id)
    cid = str(card.id)
    if player.owned(cid) < 1:
        raise UpgradeError(f"You don't have {card.name}.")
    new_id, cost = rules.upgrade_cost(cid)
    have = player.salvage or 0
    if have < cost:
        raise UpgradeError(f"Not enough SP: upgrading {card.name} costs {cost}, and you have {have}.")
    spare = player.owned(cid) - player.in_decks(cid)
    deck = (player.decks or {}).get(str(active_deck)) if active_deck is not None else None
    in_this_deck = deck is not None and (deck["cards"].get(cid, 0) or str(deck.get("commander_id")) == cid)
    if in_this_deck and (in_deck or spare < 1):
        if str(deck.get("commander_id")) == cid:
            deck["commander_id"] = new_id
        else:
            deck["cards"][cid] -= 1
            if not deck["cards"][cid]:
                del deck["cards"][cid]
            deck["cards"][str(new_id)] = deck["cards"].get(str(new_id), 0) + 1
    player.salvage = have - cost
    player.remove_card(cid)
    player.add_card(new_id)               # recorded: an upgrade adds 1 to cards_earned
    return new_id, cost


def salvage(rules: Rules, player, card_id) -> int:
    """Salvages one spare copy of a card: the SP gained (up to the cap)."""
    card = rules.card(card_id)
    cid = str(card.id)
    value = rules.salvage_value(cid)
    if player.owned(cid) < 1:
        raise UpgradeError(f"You don't have {card.name}.")
    if player.is_locked(cid):
        raise UpgradeError(f"{card.name} is locked.")
    if player.owned(cid) - player.in_decks(cid) < 1:
        raise UpgradeError(f"{card.name} is in your deck.")
    player.remove_card(cid)
    return _gain(rules, player, value)


def bulk_salvage_list(rules: Rules, player, rarity: int) -> dict[str, int]:
    """card id -> copies the bulk salvage takes: level-1 unfused cards of the rarity,
    not commanders, not locked, not in a deck."""
    out = {}
    for cid, c in player.cards.items():
        card = rules.cards.get(cid) if cid.isdigit() else None
        if card is None or card.rarity != rarity or card.level != 1 or card.fusion_level or card.type == COMMANDER \
                or c.get("is_locked"):
            continue
        n = c["num_owned"] - player.in_decks(cid)
        if n > 0:
            out[cid] = n
    return out


def bulk_salvage(rules: Rules, player, rarity: int) -> tuple[int, int]:
    """(cards salvaged, SP gained)."""
    take = bulk_salvage_list(rules, player, rarity)
    value = sum(rules.salvage_value(cid) * n for cid, n in take.items())
    for cid, n in take.items():
        player.remove_card(cid, n)
    return sum(take.values()), _gain(rules, player, value)


def set_lock(player, card_id, locked: bool) -> None:
    c = player.cards.get(str(card_id))
    if c is None or c["num_owned"] < 1:
        raise UpgradeError("You don't have that card.")
    if locked:
        c["is_locked"] = 1
    else:
        c.pop("is_locked", None)


def fuse(rules: Rules, player, card_id) -> tuple[int, list[tuple[int, int]]]:
    """Fuses a card from its recipe: (the new card id, the resources used). Spare copies
    are used first, then deck copies (the decks are trimmed; ours)."""
    card = rules.card(card_id)
    recipe = rules.recipes.get(card.id)
    if not recipe:
        raise UpgradeError(f"{card.name} can't be made by fusion.")
    missing = [rules.cards[rid].name if rules.cards.get(rid) else str(rid)
               for rid, n in recipe if player.owned(rid) < n]
    if missing:
        raise UpgradeError(f"You don't have the cards to fuse {card.name}: {', '.join(missing)}.")
    for rid, n in recipe:
        player.remove_card(rid, n)
    player.add_card(card.id)
    return card.id, list(recipe)


def _gain(rules: Rules, player, value: int) -> int:
    have = player.salvage or 0
    player.salvage = min(rules.max_salvage, have + value)
    return player.salvage - have
