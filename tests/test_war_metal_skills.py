"""Tests for War Metal Tyrant's skills brought back on mod-support: Anti-Air, Fear, Crush,
Siphon and Regeneration (War Metal's Regenerate), on small made-up cards. Random picks
take the first candidate; random() rolls are fixed per battle."""

import random

from battle.cards import CardDB
from battle.engine import BUILT, Battle, Setup

CARDS = """<root>
  <unit><id>1300</id><name>Captain</name><health>40</health></unit>
  <unit><id>20</id><name>Avenger</name><attack>0</attack><health>20</health><skill id="avenge" x="2"/></unit>
  <unit><id>23</id><name>Dummy</name><attack>0</attack><health>20</health></unit>
  <unit><id>24</id><name>Hitter</name><attack>2</attack><health>20</health></unit>
  <unit><id>25</id><name>Glass</name><attack>0</attack><health>1</health></unit>
  <unit><id>30</id><name>Fearful</name><attack>2</attack><health>10</health><skill id="fear"/></unit>
  <unit><id>31</id><name>Flak</name><attack>1</attack><health>10</health><skill id="antiair" x="2"/></unit>
  <unit><id>32</id><name>Flyer</name><attack>0</attack><health>10</health><skill id="flying"/></unit>
  <unit><id>33</id><name>Crusher</name><attack>3</attack><health>10</health><skill id="crush" x="4"/></unit>
  <unit><id>34</id><name>Siphoner</name><attack>2</attack><health>10</health><skill id="siphon" x="3"/></unit>
  <unit><id>35</id><name>Regrower</name><attack>0</attack><health>3</health><skill id="regeneration" x="2"/></unit>
  <unit><id>36</id><name>Hard Hitter</name><attack>5</attack><health>20</health></unit>
  <unit><id>37</id><name>Flying Hitter</name><attack>2</attack><health>20</health><skill id="flying"/></unit>
  <unit><id>2100</id><name>Wall</name><health>6</health><skill id="wall"/></unit>
  <unit><id>2101</id><name>Big Wall</name><health>20</health><skill id="wall"/></unit>
</root>"""
DB = CardDB.from_xml(CARDS)


class Rolls(random.Random):
    """random() always gives `roll` (Flying's dodge, Regeneration's chance)."""
    getrandbits = random.Random.getrandbits

    def __init__(self, roll: float):
        super().__init__(1)
        self.roll = roll

    def random(self) -> float:
        return self.roll


class First:
    """The first candidate, with fixed rolls."""
    def __init__(self, roll: float):
        self.rng = Rolls(roll)

    def __call__(self, battle, actor, skill_id, candidates, n):
        return candidates[:n]


def battle(att, dfn, roll=0.0):
    return Battle(Setup(DB[1300], [(i + 1, DB[c]) for i, c in enumerate(att)],
                        DB[1300], [(101 + i, DB[c]) for i, c in enumerate(dfn)]), chooser=First(roll))


def actions(turns, turn, skill=None):
    return [a for a in turns[turn].get("actions", []) if skill is None or a["skill_id"] == skill]


def attacks(turns, turn):
    return actions(turns, turn, 0)


# Cards attack on the turn they're played: an attacker's first card, with nothing across,
# hits the enemy commander on turn 1.


def test_all_five_are_built():
    assert {"antiair", "fear", "crush", "siphon", "regeneration"} <= BUILT
    b = battle(att=(30, 31, 33, 34, 35), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    assert not b.ignored_skills


def test_fear_attacks_past_the_card_across():
    b = battle(att=(30, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)                                              # turn 1: the Fearful hits the commander (38)
    turns = b.play(2)                                      # turn 3: a Dummy is across it now
    hit, = attacks(turns, 3)
    assert hit["card_uid"] == 1 and hit["targets"] == [-2] and hit["value"] == 2
    assert b.sides[1].commander.hp == 36 and b.sides[1].assaults[0].hp == 20


def test_fear_still_hits_a_wall_first():
    b = battle(att=(30, 23, 23, 23, 23, 23), dfn=(2101, 23, 23))
    b.start()
    b.play(1)                                              # turn 1: the commander (38); turn 2: the Wall
    b.play(2)                                              # turn 3: the Wall; turn 4: a Dummy across
    turns = b.play(3)
    hit, = attacks(turns, 5)
    wall = b.sides[1].structures[0]
    assert hit["targets"] == [wall.uid] and wall.hp == 16 and b.sides[1].commander.hp == 38


def test_anti_air_never_misses_flying_and_hits_it_harder():
    b = battle(att=(31, 23, 23, 23), dfn=(32, 23, 23), roll=0.0)    # every Flying dodge roll succeeds
    b.start()
    b.play(1)
    turns = b.play(2)
    hit, = attacks(turns, 3)
    assert hit["targets"] == [101] and hit["value"] == 3                # 1 attack + Anti-Air 2
    assert b.sides[1].assaults[0].hp == 7
    b = battle(att=(24, 23, 23, 23), dfn=(32, 23, 23), roll=0.0)    # without it: a miss
    b.start()
    b.play(1)
    b.play(2)
    assert b.sides[1].assaults[0].hp == 10


def test_flying_never_dodges_flying():
    b = battle(att=(37, 23, 23, 23), dfn=(32, 23, 23), roll=0.0)    # every Flying dodge roll succeeds
    b.start()
    b.play(1)
    turns = b.play(2)
    hit, = attacks(turns, 3)
    assert hit["targets"] == [101] and hit["value"] == 2           # War Metal: Flying hits Flying
    assert b.sides[1].assaults[0].hp == 8


def test_anti_air_does_nothing_against_the_rest():
    b = battle(att=(31, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    hit, = attacks(turns, 3)
    assert hit["value"] == 1


def test_crush_hits_the_commander_after_a_kill():
    b = battle(att=(33, 23, 23, 23), dfn=(25, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    crush, = actions(turns, 3, "strike")                   # shown as a Strike at the commander
    assert crush == {"card_uid": 1, "skill_id": "strike", "targets": [-2], "value": 4, "status": []}
    assert turns[3]["defend_kill"] == [101]
    assert b.sides[1].commander.hp == 40 - 3 - 4           # turn 1's attack, then the Crush


def test_crush_needs_a_kill():
    b = battle(att=(33, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    assert not actions(turns, 3, "strike") and b.sides[1].commander.hp == 40 - 3


def test_crush_goes_through_a_wall_first():
    b = battle(att=(33, 23, 23, 23, 23, 23), dfn=(2101, 25, 23))
    b.start()
    b.play(1)                                              # turn 1: the commander (37); turn 2: the Wall
    b.play(2)                                              # turn 3: the Wall (17); turn 4: the Glass across
    turns = b.play(3)                                      # turn 5: the Glass dies, the Crush hits the Wall
    crush, = actions(turns, 5, "strike")
    wall = b.sides[1].structures[0]
    assert crush["targets"] == [wall.uid] and crush["value"] == 4 and crush["block"] == []
    assert crush["status"] == [{"card_uid": wall.uid, "status_flag": "h", "value": 13}]
    assert wall.hp == 13 and b.sides[1].commander.hp == 37


def test_siphon_heals_the_own_commander():
    b = battle(att=(34, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.sides[0].commander.hp = 30
    b.play(1)
    turns = b.play(2)
    heal, = actions(turns, 3, "heal")
    assert heal["card_uid"] == 1 and heal["targets"] == [-1] and heal["value"] == 2     # up to the damage
    assert b.sides[0].commander.hp == 32


def test_siphon_needs_a_hurt_commander():
    b = battle(att=(34, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    assert not actions(turns, 3, "heal") and b.sides[0].commander.hp == 40


def regrower_hit(roll, disease=0):
    """Turn 2: an Avenger; turn 4: the Regrower beside it; turn 5: the Hard Hitter, played
    on turn 3, hits the Regrower for 5 (3 health)."""
    b = battle(att=(23, 36, 23, 23, 23), dfn=(20, 35, 23), roll=roll)
    b.start()
    b.play(1)
    b.play(2)
    b.sides[1].assaults[1].diseased = disease
    return b, b.play(3)[5]


def named(turn, skill):
    return [a for a in turn["actions"] if a["skill_id"] == skill]


def test_regeneration_brings_a_card_back():
    b, turn = regrower_hit(roll=0.0)                       # every roll succeeds
    regrower = b.sides[1].assaults[1]
    assert regrower.uid == 102 and regrower.alive and regrower.hp == 2
    # Shown before the hit, raising the card's health by the hit's 5: the client takes the
    # hit's damage off the card itself, and kills it at 0 whatever status follows.
    back, hit = turn["actions"][:2]
    assert back == {"card_uid": 102, "skill_id": "regeneration", "targets": [102], "value": 2,
                    "status": [{"card_uid": 102, "status_flag": "h", "value": 2 + 5}]}
    assert hit["targets"] == [102] and hit["value"] == 5
    assert hit["status"] == [{"card_uid": 102, "status_flag": "h", "value": 2}]
    assert "defend_kill" not in turn                       # not dead: no Avenge for its neighbour either
    assert not named(turn, "avenge")


def test_regeneration_can_fail():
    b, turn = regrower_hit(roll=0.9)
    assert not named(turn, "regeneration")
    assert turn["defend_kill"] == [102] and named(turn, "avenge")


def test_disease_stops_regeneration():
    b, turn = regrower_hit(roll=0.0, disease=2)            # Disease soaks all 2 of it
    assert not named(turn, "regeneration") and turn["defend_kill"] == [102]


def test_regeneration_after_disease_soaks_some():
    b, turn = regrower_hit(roll=0.0, disease=1)
    assert b.sides[1].assaults[1].hp == 1 and "defend_kill" not in turn


def test_crush_counts_a_kill_that_regenerates():
    b = battle(att=(33, 23, 23, 23), dfn=(35, 23, 23), roll=0.0)
    b.start()
    b.play(1)
    turns = b.play(2)
    assert actions(turns, 3, "regeneration") and actions(turns, 3, "strike")
    assert b.sides[1].assaults[0].hp == 2 and b.sides[1].commander.hp == 40 - 3 - 4
