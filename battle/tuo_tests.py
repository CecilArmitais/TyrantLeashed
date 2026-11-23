"""The optimizer's own battle test decks (reference/tuo/tests/*.csv), run on our engine.

Each test line is "your deck; enemy deck[; battleground effect]". Despite the
"check_win" in its test code, the optimizer's win check is switched off
(sim_test.cpp: `double eps = 1.;  // disable`), so upstream these lines only check
that a battle runs without crashing. Here they're a crash test across many real
skill combinations; the win rates are shown for information. Decks are written the
optimizer's way: "Commander, Card, Card-1#4, ..." where "-N" picks level N (no
suffix: the top level) and "#N" repeats a card. Cards come from the optimizer's
copy of the card data (tests/sim/data), so the numbers match what it tested with.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from .cards import COMMANDER, STRUCTURE, Card, CardDB
from .engine import Battle, RandomChooser, Setup, shuffled_deck, FIRST_UID, ATTACKER, DEFENDER
from paths import TUO_DIR   # the optimizer's source, beside the workspace

TEST_FILES = ("test_sinlge_units.csv", "test_multi_units.csv", "test_whole_decks.csv", "test_crash.csv")


def simplify(name: str) -> str:
    return "".join(c.lower() for c in name if c not in ";:,\"'! ")


class Names:
    """Card lookup by the optimizer's names."""

    def __init__(self, db: CardDB):
        self.by_name: dict[str, list[Card]] = {}
        top: dict[int, Card] = {}
        for card in db.cards.values():
            if card.base_id not in top or card.level > top[card.base_id].level:
                top[card.base_id] = card
            self.by_name.setdefault(simplify(f"{card.name}-{card.level}"), []).append(card)
        for card in sorted(top.values(), key=lambda c: c.id):      # plain name: the top level
            self.by_name.setdefault(simplify(card.name), []).append(card)

    def card(self, spec: str, commander: bool = False) -> tuple[Card, int]:
        """'Name-1#4' -> (card, 4). Several cards can share a name (a commander and an
        assault both called "Barracus the Traitor"): the one of the wanted kind, newest."""
        spec = spec.strip()
        count = 1
        m = re.match(r"^(.*?)\s*(?:#(\d+)|\((\d+)\))$", spec)        # "Name#4" or "Name (4)"
        if m:
            spec, count = m.group(1), int(m.group(2) or m.group(3))
        found = self.by_name.get(simplify(spec))
        if not found:
            raise KeyError(f"unknown card {spec!r}")
        kind = [c for c in found if (c.type == COMMANDER) == commander]
        return max(kind or found, key=lambda c: c.base_id), count


@dataclass
class Deck:
    commander: Card
    dominion: Card | None
    cards: list[Card]


def parse_deck(text: str, names: Names) -> Deck:
    """The commander can be anywhere in the list (the optimizer takes the first one)."""
    parts = [p for p in (x.strip() for x in text.split(",")) if p]
    commander, dominion, cards = None, None, []
    for spec in parts:
        card, n = names.card(spec, commander=commander is None)
        if card.type == COMMANDER:
            commander = commander or card
        elif card.type == STRUCTURE and 50001 <= card.base_id <= 55000:
            dominion = card
        else:
            cards += [card] * n
    assert commander is not None, "no commander"
    return Deck(commander, dominion, cards)


def custom_decks() -> dict[str, list[str]]:
    """The optimizer's named decks (tests/sim/data/customdecks.txt): name -> deck
    strings. A name whose value is /regex/ stands for every deck matching it."""
    plain, groups = {}, {}
    f = TUO_DIR / "tests" / "sim" / "data" / "customdecks.txt"
    if not f.is_file():
        return {}
    for line in f.read_text("utf-8").splitlines():
        if ":" not in line or line.lstrip().startswith("//"):
            continue
        name, value = (x.strip() for x in line.split(":", 1))
        if value.startswith("/") and value.endswith("/"):
            groups[name] = value[1:-1]
        else:
            plain[name] = [value]
    out = dict(plain)
    for name, pattern in groups.items():
        out[name] = [v for k, vs in plain.items() if re.match(pattern, k) for v in vs]
    return out


def test_lines(files=TEST_FILES) -> list[tuple[str, str, str, str]]:
    """(file, your deck, enemy deck, bge) for each test line. A named deck group on
    both sides ("Bug66;Bug66") becomes one line per deck in the group, against itself."""
    named = custom_decks()
    out = []
    for name in files:
        for line in (TUO_DIR / "tests" / name).read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            fields = [f.strip() for f in line.split(";")]
            you, enemy, bge = fields[0], fields[1], fields[2] if len(fields) > 2 else ""
            if you in named and enemy in named:
                mine, theirs = named[you], named[enemy]
                out += [(name, a, b, bge) for a, b in zip(mine, theirs if theirs else mine)]
            else:
                out.append((name, named.get(you, [you])[0], named.get(enemy, [enemy])[0], bge))
    return out


def run(you: Deck, enemy: Deck, rng: random.Random, cards=None) -> int:
    """One battle, both sides on auto (the attacker plays the first card in hand, as
    the optimizer's default "random" deck strategy). Returns the winner (1: you)."""
    setup = Setup(you.commander, shuffled_deck(you.cards, FIRST_UID[ATTACKER], rng),
                  enemy.commander, shuffled_deck(enemy.cards, FIRST_UID[DEFENDER], rng),
                  attacker_dominion=you.dominion, defender_dominion=enemy.dominion)
    b = Battle(setup, chooser=RandomChooser(rng), cards=cards)
    b.start()
    while not b.over:
        b.play(b.active.hand[0])
    return b.winner


def win_rate(you: Deck, enemy: Deck, n: int = 100, seed: int = 1, cards=None) -> float:
    """cards: the card data, for Summon."""
    rng = random.Random(seed)
    return sum(run(you, enemy, rng, cards) == 1 for _ in range(n)) / n
