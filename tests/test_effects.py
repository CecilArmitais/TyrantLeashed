"""Tests for battleground effects (battle/effects.py and the engine's effect phase)."""

import random

import pytest

import paths
from battle import effects as fx
from battle.engine import ATTACKER, DEFENDER, FIRST_UID, Battle, RandomChooser, Setup, shuffled_deck
from helpers import real_cards

pytestmark = pytest.mark.needs_game        # every test here needs the game files


@pytest.fixture(scope="module")
def all_effects():
    return fx.load(paths.ASSET_DIR)


def test_the_effects_file(all_effects):
    assert len(all_effects) == 161
    silus = all_effects[3002]
    assert (silus.name, silus.picture, silus.kv_mult) == ("Silus' Pact", "siluspact.png", "raid_level")
    assert [(s.id, s.x, s.all) for s in silus.specs(10)] == [
        ("overload", 0, True), ("rally", 50, True), ("protect", 100, True), ("enfeeble", 50, True), ("weaken", 100, True)]
    octane = all_effects[3001]
    assert [(s.id, s.x) for s in octane.specs(3)][:3] == [("overload", 0), ("strike", 2), ("heal", 2)]   # .6 x 3 -> 2
    assert all_effects[10].specs(7)[0].x == 1                    # not kv_mult: the level doesn't count
    assert all_effects[37].skills == ()                          # "This Raid features ... Overload": a note
    assert silus.entry("defender") == {"id": 3002, "name": "Silus' Pact", "desc": silus.desc,
                                       "web_picture": "siluspact.png", "card_uid": -5}


def fight(effects, seed=3):
    cards = real_cards()
    rng = random.Random(seed)
    deck = [cards[i] for i in (12, 13, 2, 3, 4, 350)]
    foe = [cards[i] for i in (75, 2024, 109, 45, 106)]
    b = Battle(Setup(cards[1000], shuffled_deck(deck, FIRST_UID[ATTACKER], rng), cards[1001],
                     shuffled_deck(foe, FIRST_UID[DEFENDER], rng)), chooser=RandomChooser(random.Random(seed)),
               cards=cards, effects=effects)
    b.start()
    b.play_out()
    return b


def test_an_effect_is_cast_each_turn_of_its_side(all_effects):
    silus = all_effects[3002]
    b = fight([(fx.SCOPE_UID["defender"], "defender", silus.caster(10))])
    acts = [(t, a) for t, turn in b.turns.items() for a in turn.get("actions", []) if a["card_uid"] == -5]
    assert acts and all(t % 2 == 0 for t, _ in acts)            # the defender's turns only
    hostile = [a for _, a in acts if a["skill_id"] in ("enfeeble", "weaken")]
    helpful = [a for _, a in acts if a["skill_id"] in ("rally", "protect")]
    assert all(int(t) < 101 for a in hostile for t in a["targets"])      # the attacker's cards
    assert all(int(t) >= 101 for a in helpful for t in a["targets"])     # its own
    assert not b.ignored_skills


def test_a_global_effect_works_for_both_sides(all_effects):
    rally = next(e for e in all_effects.values() if [s.id for s in e.skills] == ["rally"] and e.skills[0].all)
    b = fight([(fx.SCOPE_UID["global"], "global", rally.caster())])
    sides = {t % 2 for t, turn in b.turns.items() for a in turn.get("actions", []) if a["card_uid"] == -3}
    assert sides == {0, 1}


def test_no_effects_changes_nothing():
    plain, again = fight([]), fight([])
    assert plain.turns == again.turns and plain.winner == again.winner


def test_ballroom_blitz_raises_its_blitz_plates_on_arrival(all_effects):
    blitz = all_effects[3000]
    assert [(s.id, s.x, s.filter) for s in blitz.specs(5)][:2] == [
        ("perm_max_attack", 5, "filterBallroomBlitz"), ("perm_max_health", 22, "filterBallroomBlitz")]  # .9, 4.35 x 5
    cards = real_cards()
    plate = cards[4452]                                              # Blitz Plate: 3 attack, 7 health
    rng = random.Random(1)
    b = Battle(Setup(cards[1000], shuffled_deck([cards[i] for i in (12, 13, 2, 3, 4)], FIRST_UID[ATTACKER], rng),
                     cards[1001], [(101 + i, plate) for i in range(5)]),
               chooser=RandomChooser(random.Random(1)), cards=cards,
               effects=[(fx.SCOPE_UID["defender"], "defender", blitz.caster(5), blitz.passive)])
    b.start()
    b.play(b.sides[ATTACKER].hand[0])
    arrived, = [a for a in b.turns[2]["actions"] if a["card_uid"] == -5 and a["skill_id"] == "berserk"]
    unit = b.sides[DEFENDER].assaults[0]
    assert arrived["targets"] == [101] and (unit.perm_attack, unit.max_hp) == (5, 7 + 22)
    assert not any("perm_max" in s for s in b.ignored_skills)
