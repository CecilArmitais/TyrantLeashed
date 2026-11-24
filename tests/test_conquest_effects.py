"""Tests for the rule battleground effects of conquest's zones (the optimizer's passive
BGEs): Fortification, Counterflux, Devour, Oath of Loyalty and Virulence, on small made-up cards. Random picks
take the first candidate."""

import pytest

from battle import effects as fx
from battle.cards import CardDB
from battle.engine import DEFENDER, Battle, Setup
from common import paths

CARDS = """<root>
  <unit><id>1300</id><name>Captain</name><health>40</health></unit>
  <unit><id>10</id><name>Armored</name><attack>1</attack><health>10</health><skill id="armored" x="2"/></unit>
  <unit><id>12</id><name>Counterer</name><attack>0</attack><health>10</health><skill id="counter" x="5"/></unit>
  <unit><id>23</id><name>Dummy</name><attack>0</attack><health>20</health></unit>
  <unit><id>24</id><name>Hitter</name><attack>2</attack><health>20</health></unit>
  <unit><id>30</id><name>Leecher</name><attack>2</attack><health>10</health><skill id="leech" x="3"/></unit>
  <unit><id>31</id><name>Loyal</name><type>1</type><attack>1</attack><health>10</health><skill id="allegiance" x="2"/></unit>
  <unit><id>32</id><name>Outsider</name><type>2</type><attack>0</attack><health>20</health></unit>
  <unit><id>2101</id><name>Tower</name><type>1</type><health>10</health></unit>
</root>"""
DB = CardDB.from_xml(CARDS)


def first(battle, actor, skill_id, candidates, n):
    return candidates[:n]


def rule(name: str, value: int = 0):
    """A global rule effect, as game_api passes it."""
    effect = fx.Effect(0, name, "", "", value=value)
    return [(fx.SCOPE_UID["global"], "global", effect.caster(), effect.passive)]


def battle(att, dfn, effects=()):
    return Battle(Setup(DB[1300], [(i + 1, DB[c]) for i, c in enumerate(att)],
                        DB[1300], [(101 + i, DB[c]) for i, c in enumerate(dfn)]), chooser=first, effects=effects)


def actions(turns, turn, skill=None):
    return [a for a in turns[turn].get("actions", []) if skill is None or a["skill_id"] == skill]


@pytest.mark.needs_game
def test_the_rule_effects_in_the_file():
    effects = {e.name: e for e in fx.load(paths.ASSET_DIR).values()}
    assert effects["Devour"].passive == ("devour", 4)            # <value>4</value>
    assert effects["Oath of Loyalty"].passive == ("oath_of_loyalty", 1)
    assert effects["Counterflux"].passive == ("counterflux", 4)  # no value: "X/4", its text
    assert effects["Fortification"].passive == ("fortification", 1)
    assert effects["Virulence"].passive == ("virulence", 1)
    assert effects["Halted Orders"].passive is None              # nothing to do (see effects.PASSIVE)
    assert effects["Nexus Resonance"].passive is None            # no rule known for it
    assert all(effects[n].skills == () for n in fx.PASSIVE)


def test_fortification_lends_a_neighbours_armor():
    def hit(effects):
        b = battle(att=(23, 10, 23, 23), dfn=(24, 23, 23, 23), effects=effects)
        b.start()
        b.play(1)                                          # a Dummy; the Hitter hits it
        turns = b.play(2)                                  # an Armored beside it
        return next(a for a in actions(turns, 4, 0) if a["card_uid"] == 101)
    plain = hit(())
    assert plain["value"] == 2 and plain["block"] == []
    fort = hit(rule("Fortification"))
    assert fort["value"] == 0 and fort["block"] == [{"card_uid": "1", "skill": "armored", "value": 2}]


def test_counterflux_heals_and_berserks_the_counter():
    b = battle(att=(12, 23, 23, 23), dfn=(24, 23, 23, 23), effects=rule("Counterflux"))
    b.start()
    turns = b.play(1)                                      # the Hitter hits the Counterer: 10 -> 8
    counter, = actions(turns, 2, "counter")
    assert counter["value"] == 5
    flux, = [a for a in actions(turns, 2, "berserk") if a["card_uid"] == -3]
    assert flux["targets"] == ["1"] and flux["value"] == 2       # 5 / 4, rounded up
    assert flux["status"] == [{"card_uid": "1", "status_flag": "perm_max_attack", "value": 2},
                              {"card_uid": "1", "status_flag": "h", "value": 10}]
    counterer = b.sides[0].assaults[0]
    assert (counterer.hp, counterer.attack_power()) == (10, 2)


def test_devour_feeds_leech_hitting_an_assault():
    b = battle(att=(30, 23, 23, 23), dfn=(23, 23, 23, 23), effects=rule("Devour", 4))
    b.start()
    turns = b.play(1)                                      # nothing across: it hits the commander
    assert not [a for t in turns for a in actions(turns, t) if a["card_uid"] == -3]
    turns = b.play(2)                                      # now a Dummy across
    devour, = [a for a in actions(turns, 3, "berserk") if a["card_uid"] == -3]
    assert devour["targets"] == [1] or devour["targets"] == ["1"]
    assert devour["value"] == 1                            # Leech 3 / 4, rounded up
    leecher = b.sides[0].assaults[0]
    assert (leecher.perm_attack, leecher.perm_health, leecher.attack_power()) == (1, 2 + 1, 3)   # Leech's 2, Devour's 1


def test_no_devour_without_the_effect():
    b = battle(att=(30, 23, 23, 23), dfn=(23, 23, 23, 23))
    b.start()
    b.play(1)
    b.play(2)
    assert b.sides[0].assaults[0].perm_attack == 0


def test_oath_of_loyalty_counts_the_cards_of_its_faction():
    b = battle(att=(23, 32, 2101, 31, 23, 23), dfn=(23,) * 6, effects=rule("Oath of Loyalty", 1))
    b.start()
    for uid in (1, 2, 3):                                  # an imperial Dummy, a raider, an imperial tower
        b.play(uid)
    turns = b.play(4)                                      # Loyal: Allegiance 2 x (the Dummy + the tower)
    oath, = actions(turns, 7, "allegiance")
    assert oath["card_uid"] == 4 and oath["value"] == 4
    loyal, = [u for u in b.sides[0].assaults if u.uid == 4]
    assert (loyal.perm_attack, loyal.max_hp) == (4, 14)


def test_without_the_oath_allegiance_is_only_for_allies():
    b = battle(att=(23, 32, 2101, 31, 23, 23), dfn=(23,) * 6)
    b.start()
    for uid in (1, 2, 3, 4):
        b.play(uid)
    loyal, = [u for u in b.sides[0].assaults if u.uid == 4]
    assert loyal.perm_attack == 0 and not actions(b.turns, 7, "allegiance")


@pytest.mark.parametrize("effects, left, right", [(rule("Virulence"), 5, 5), ((), 0, 0)])
def test_virulence_spreads_the_poison_of_the_dead(effects, left, right):
    b = battle(att=(23,) * 8, dfn=(23,) * 8, effects=effects)
    b.start()
    for uid in (1, 2, 3, 4):
        b.play(uid)                                        # four Dummies on each side
    row = b.sides[DEFENDER].assaults
    row[1].poisoned, row[2].poisoned = 2, 3
    b._damage(row[1], 99)                                  # two neighbours die in one hit
    b._damage(row[2], 99)
    b._after_damage()
    # the left one gets each dead card's poison; the right one, the run's (2 + 3)
    assert (row[0].poisoned, row[3].poisoned) == (left, right)
