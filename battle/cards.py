"""Card data from the game's own files (cards_section_*.xml).

Each <unit> is a card's first level. Each <upgrade> inside it is the next level: it
starts as a copy of the level before and replaces what it lists (its own card id,
attack, health, delay...). An upgrade that lists any skill replaces the whole skill
list. The card type (assault, structure, commander) comes from the unit's id range.
As in the optimizer's xml.cpp (reference/tuo).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path

ASSAULT, STRUCTURE, COMMANDER = "assault", "structure", "commander"
TRIGGERS = ("activate", "play", "attacked", "death")


@dataclass(frozen=True)
class SkillSpec:
    id: str                 # the XML skill id: "strike", "armored", "besiege", ...
    x: int = 0              # amount
    y: int = 0              # faction it's limited to (0: any)
    n: int = 0              # number of targets (0: one, unless `all`)
    c: int = 0              # cooldown in turns
    s: str = ""             # a skill it refers to (e.g. enhance's)
    s2: str = ""
    all: bool = False
    card_id: int = 0        # summon's card
    trigger: str = "activate"


@dataclass(frozen=True)
class Card:
    id: int
    base_id: int            # the unit's first-level id
    name: str
    type: str
    faction: int            # <type>: 1 imperial, 2 raider, 3 bloodthirsty, 4 xeno, 5 righteous, 6 progenitor
    rarity: int
    set: int
    level: int
    fusion_level: int
    attack: int
    health: int
    delay: int
    skills: tuple[SkillSpec, ...] = field(default=())
    fortress_type: int = 0  # a guild fortress card: 1 defense, 2 siege (0: not one)
    gp_cost: int = 0        # what this level costs a guild, in guild points

    def skill(self, skill_id: str) -> SkillSpec | None:
        return next((s for s in self.skills if s.id == skill_id), None)


def card_type(base_id: int) -> str:
    """From the id range of the unit, as the optimizer does."""
    for upper, kind in ((1000, ASSAULT), (2000, COMMANDER), (3000, STRUCTURE), (8000, ASSAULT),
                        (10000, STRUCTURE), (17000, ASSAULT), (25000, STRUCTURE), (30000, COMMANDER),
                        (50001, ASSAULT), (55001, STRUCTURE)):
        if base_id < upper:
            return kind
    return ASSAULT


def _int(text, default: int = 0) -> int:
    if text is None or str(text).strip() == "":
        return default
    return int(float(text))


def _skill(node: ET.Element) -> SkillSpec:
    sid = node.get("id", "")
    return SkillSpec(
        id=sid, x=_int(node.get("x")), y=_int(node.get("y")), n=_int(node.get("n")),
        c=_int(node.get("c"), 1 if sid == "flurry" else 0),
        s=node.get("s", ""), s2=node.get("s2", ""), all=node.get("all", "0") not in ("0", ""),
        card_id=_int(node.get("card_id")), trigger=(node.get("trigger") or "activate").lower())


def _level(prev: Card | None, node: ET.Element, base_id: int) -> Card:
    """One level of a card: the previous level with this node's values on top."""
    text = lambda tag: node.findtext(tag)   # noqa: E731
    skills = node.findall("skill")
    if prev is None:
        cid = _int(text("id"))
        prev = Card(id=cid, base_id=cid, name="", type=card_type(cid), faction=1, rarity=1, set=0, level=1,
                    fusion_level=0, attack=0, health=0, delay=0, skills=())
    changes = {}
    if text("card_id") is not None:
        changes["id"] = _int(text("card_id"))
    for tag, key in (("name", "name"),):
        if text(tag) is not None:
            changes[key] = text(tag).strip()
    for tag, key in (("attack", "attack"), ("health", "health"), ("cost", "delay"), ("rarity", "rarity"),
                     ("type", "faction"), ("set", "set"), ("level", "level"), ("fusion_level", "fusion_level"),
                     ("fortress_type", "fortress_type"), ("gp_cost", "gp_cost")):
        if text(tag) is not None:
            changes[key] = _int(text(tag))
    if skills:
        changes["skills"] = tuple(_skill(s) for s in skills)
    return replace(prev, **changes)


def parse_unit(unit: ET.Element) -> list[Card]:
    """All levels of one <unit>, first level first."""
    first = _level(None, unit, 0)
    levels = [first]
    for up in unit.findall("upgrade"):
        levels.append(_level(levels[-1], up, first.base_id))
    return levels


class CardDB:
    """Every card level by id."""

    def __init__(self, cards: dict[int, Card]):
        self.cards = cards

    def __getitem__(self, card_id) -> Card:
        return self.cards[int(card_id)]

    def get(self, card_id) -> Card | None:
        return self.cards.get(int(card_id))

    def __len__(self) -> int:
        return len(self.cards)

    def next_level(self, card_id) -> Card | None:
        """The card's next level (its upgrade), or None at the last level."""
        chains = getattr(self, "_chains", None)
        if chains is None:
            chains = self._chains = {}
            for card in self.cards.values():
                chains.setdefault(card.base_id, {})[card.level] = card
        card = self.get(card_id)
        return None if card is None else chains.get(card.base_id, {}).get(card.level + 1)

    @classmethod
    def from_xml(cls, *texts: str) -> "CardDB":
        """From XML text (for tests)."""
        cards = {}
        for text in texts:
            for unit in ET.fromstring(text).iter("unit"):
                for card in parse_unit(unit):
                    cards[card.id] = card
        return cls(cards)

    @classmethod
    def load(cls, asset_dir: Path) -> "CardDB":
        """All cards_section_*.xml in the game data folder, in file order."""
        files = sorted(Path(asset_dir).glob("cards_section_*.xml"), key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
        if not files:
            raise FileNotFoundError(f"no cards_section_*.xml in {asset_dir}")
        cards = {}
        for f in files:
            for _, el in ET.iterparse(f):
                if el.tag == "unit":
                    for card in parse_unit(el):
                        cards[card.id] = card
                    el.clear()
        return cls(cards)
