"""Tests for War Metal Tyrant's skills brought back on mod-support: Anti-Air, Fear, Crush,
Siphon and Regeneration (War Metal's Regenerate), on small made-up cards. Random picks
take the first candidate; random() rolls are fixed per battle."""

import random

from battle.cards import CardDB
from battle import engine
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
  <unit><id>40</id><name>Shocker</name><attack>0</attack><health>10</health><skill id="shock" x="3"/></unit>
  <unit><id>41</id><name>Repairer</name><attack>0</attack><health>10</health><skill id="repair" x="4"/></unit>
  <unit><id>42</id><name>Supplier</name><attack>0</attack><health>10</health><skill id="supply" x="2"/></unit>
  <unit><id>43</id><name>Cleanser</name><attack>0</attack><health>10</health><skill id="cleanse"/></unit>
  <unit><id>44</id><name>Splitter</name><attack>0</attack><health>5</health><skill id="split"/></unit>
  <unit><id>45</id><name>Burster</name><attack>2</attack><health>10</health><skill id="burst" x="3"/></unit>
  <unit><id>46</id><name>Interceptor</name><attack>0</attack><health>20</health><skill id="intercept"/></unit>
  <unit><id>47</id><name>Backfirer</name><attack>0</attack><health>1</health><skill id="backfire" x="5"/></unit>
  <unit><id>48</id><name>Striker</name><attack>0</attack><health>10</health><skill id="strike" x="3"/></unit>
  <unit><id>49</id><name>Reckless</name><attack>1</attack><health>1</health><skill id="backfire" x="4"/></unit>
  <unit><id>50</id><name>Counterer</name><attack>0</attack><health>10</health><skill id="counter" x="2"/></unit>
  <unit><id>51</id><name>Last Shocker</name><attack>0</attack><health>1</health><skill id="shock" x="3" trigger="death"/></unit>
  <unit><id>52</id><name>Last Summoner</name><attack>0</attack><health>1</health><skill id="summon" card_id="23" trigger="death"/></unit>
  <unit><id>60</id><name>Chaoser</name><attack>0</attack><health>10</health><skill id="chaos"/></unit>
  <unit><id>62</id><name>Immobilizer</name><attack>2</attack><health>20</health><skill id="immobilize"/></unit>
  <unit><id>63</id><name>Fuser</name><attack>0</attack><health>10</health><skill id="fusion"/><skill id="strike" x="2"/></unit>
  <unit><id>64</id><name>Emulator</name><attack>0</attack><health>20</health><skill id="emulate"/></unit>
  <unit><id>65</id><name>Healer</name><attack>0</attack><health>9</health><skill id="heal" x="4"/></unit>
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
                        DB[1300], [(101 + i, DB[c]) for i, c in enumerate(dfn)]), chooser=First(roll), cards=DB)


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


# ---- the second batch: Shock, Repair, Supply, Cleanse, Split, Burst, Intercept, Backfire ----

def test_the_second_batch_is_built():
    assert {"shock", "repair", "supply", "cleanse", "split", "burst", "intercept", "backfire"} <= BUILT


def test_shock_hits_the_commander_past_a_wall():
    b = battle(att=(40, 23, 23, 23, 23, 23), dfn=(2101, 23, 23))
    b.start()
    turns = b.play(1)                                      # turn 1: it shocks as it's played
    shock, = actions(turns, 1, "strike")                   # the client's Strike lowers the bar
    assert shock == {"card_uid": 1, "skill_id": "strike", "targets": [-2], "value": 3, "status": []}
    turns = b.play(2)                                      # turn 2: the Wall; turn 3: past it
    assert actions(turns, 3, "strike")[0]["targets"] == [-2]
    assert b.sides[1].commander.hp == 40 - 3 - 3 and b.sides[1].structures[0].hp == 20


def test_shock_on_the_shocked_sides_turn_is_a_negative_heal():
    b = battle(att=(51, 23, 23, 23), dfn=(36, 23, 23))      # it shocks on death, on the enemy's turn
    b.start()
    turns = b.play(1)
    shock, = actions(turns, 2, "heal")                     # the client's Heal moves the active side's bar
    assert (shock["card_uid"], shock["targets"], shock["value"]) == (1, [-2], -3)
    assert b.sides[1].commander.hp == 37


def test_repair_heals_a_damaged_structure():
    b = battle(att=(2101, 41, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    b.sides[0].structures[0].hp = 10
    turns = b.play(2)
    repair, = actions(turns, 3, "heal")                    # shown as a Heal on it
    wall = b.sides[0].structures[0]
    assert repair["card_uid"] == 2 and repair["targets"] == [str(wall.uid)] and repair["value"] == 4
    assert wall.hp == 14


def test_supply_heals_itself_and_its_hurt_neighbours():
    b = battle(att=(23, 42, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    b.sides[0].assaults[0].hp = 19                         # the neighbour is hurt; the Supplier isn't
    turns = b.play(2)
    supply, = actions(turns, 3, "heal")
    assert supply["card_uid"] == 2 and supply["targets"] == ["1"] and supply["value"] == 2
    assert b.sides[0].assaults[0].hp == 20                 # no more than its max
    turns = b.play(3)                                      # nobody hurt: nothing
    assert not actions(turns, 5, "heal")


def test_cleanse_takes_the_bad_statuses_off():
    b = battle(att=(23, 43, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    dummy = b.sides[0].assaults[0]
    dummy.poisoned, dummy.diseased, dummy.jammed = 3, 2, True
    turns = b.play(2)
    cleanse, = actions(turns, 3, "cleanse")
    assert cleanse["card_uid"] == 2 and cleanse["targets"] == ["1"]
    assert cleanse["status"] == [{"card_uid": "1", "status_flag": "poison", "value": 0},
                                 {"card_uid": "1", "status_flag": "disease", "value": 0},
                                 {"card_uid": "1", "status_flag": "jammed", "value": 0}]
    assert (dummy.poisoned, dummy.diseased, dummy.jammed) == (0, 0, False)


def test_split_copies_the_card_onto_its_own_side():
    b = battle(att=(44, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    turns = b.play(1)
    split, = actions(turns, 1, "summon")                   # the client's Summon: 1 = this side's turn
    assert split == {"card_uid": 1, "skill_id": "summon", "targets": ["5"], "value": 1, "status": []}
    copy = b.sides[0].assaults[1]
    assert copy.uid == 5 and copy.card.id == 44 and copy.hp == copy.max_hp and (5, 44) in b.played
    assert len(b.sides[0].assaults) == 2                   # the copy waits for its next turn
    turns = b.play(2)                                      # both split on turn 3, then a Dummy
    assert len(actions(turns, 3, "summon")) == 2 and len(b.sides[0].assaults) == 5


def test_split_stops_when_the_row_is_full(monkeypatch):
    monkeypatch.setitem(engine.ROW_SLOTS, engine.ASSAULT, 2)
    b = battle(att=(44, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)                                              # the copy makes 2
    turns = b.play(2)                                      # a Dummy makes 3... and no more copies
    assert not actions(turns, 3, "summon") and len(b.sides[0].assaults) == 3


def test_a_death_summon_on_the_other_sides_turn_says_so():
    b = battle(att=(52, 23, 23, 23), dfn=(36, 23, 23))
    b.start()
    turns = b.play(1)
    summon, = actions(turns, 2, "summon")                  # killed on the enemy's turn
    assert summon["value"] == 0 and summon["targets"] == ["5"]


def test_burst_hits_an_unhurt_assault_harder():
    b = battle(att=(45, 23, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)                                              # turn 1: the commander (no Burst)
    turns = b.play(2)
    hit, = attacks(turns, 3)
    assert hit["targets"] == [101] and hit["value"] == 2 + 3
    turns = b.play(3)
    hit, = attacks(turns, 5)
    assert hit["value"] == 2                               # hurt now


def test_intercept_takes_a_skill_aimed_at_its_neighbour():
    b = battle(att=(23, 46, 23, 23, 23), dfn=(48, 23, 23))
    b.start()
    b.play(1)                                              # turn 2: the Striker hits the Dummy
    turns = b.play(2)                                      # turn 3: the Interceptor beside it
    intercept, = actions(turns, 4, "intercept")
    strike, = actions(turns, 4, "strike")
    assert intercept == {"card_uid": 2, "skill_id": "intercept", "targets": ["2"], "value": 0, "status": []}
    assert strike["targets"] == ["2"]
    assert b.sides[0].assaults[1].hp == 17 and b.sides[0].assaults[0].hp == 17   # turn 2's 3, then none


def test_backfire_hurts_its_own_commander():
    b = battle(att=(47, 23, 23, 23), dfn=(36, 23, 23))      # killed on the enemy's turn
    b.start()
    turns = b.play(1)
    backfire, = actions(turns, 2, "strike")              # its health comes along (shown this reply)
    assert (backfire["card_uid"], backfire["targets"], backfire["value"]) == (1, [-1], 5)
    assert b.sides[0].commander.hp == 35


def test_backfire_on_its_own_turn_is_a_negative_heal():
    b = battle(att=(49, 23, 23, 23, 23), dfn=(50, 23, 23))  # Counter kills it as it attacks
    b.start()
    b.play(1)
    turns = b.play(2)
    backfire, = actions(turns, 3, "heal")
    assert backfire["targets"] == [-1] and backfire["value"] == -4
    assert b.sides[0].commander.hp == 36


# ---- the third batch: Chaos, Immobilize, Fusion, Emulate --------------------------------------

def test_the_third_batch_is_built():
    assert {"chaos", "immobilize", "fusion", "emulate"} <= BUILT


def test_chaos_turns_an_enemy_skill_on_its_own_side():
    b = battle(att=(60, 23, 23, 23, 23), dfn=(48, 23, 23))
    b.start()
    b.play(1)                                              # turn 2: the Striker arrives (and strikes us)
    turns = b.play(2)                                      # turn 3: the Chaoser chaoses it
    chaos, = actions(turns, 3, "chaos")
    assert chaos["card_uid"] == 1 and chaos["targets"] == [101]
    assert {"card_uid": 101, "status_flag": "chaos", "value": 1} in chaos["status"]
    strike, = actions(turns, 4, "strike")                  # turn 4: it strikes its own side
    assert strike["card_uid"] == 101 and strike["targets"] == [102]
    assert b.sides[1].assaults[1].hp == 20 - 3
    assert {"card_uid": 101, "status_flag": "chaos", "value": 0} in turns[4]["end"]
    assert not b.sides[1].assaults[0].chaosed


def test_immobilize_stops_the_next_attack():
    b = battle(att=(62, 23, 23, 23, 23), dfn=(24, 23, 23), roll=0.0)    # every roll succeeds
    b.start()
    b.play(1)
    turns = b.play(2)                                      # turn 3: it hits the Hitter
    immobilize, = actions(turns, 3, "immobilize")
    assert immobilize["targets"] == [101]
    assert {"card_uid": 101, "status_flag": "immobilized", "value": 1} in immobilize["status"]
    assert not [a for a in attacks(turns, 4) if a["card_uid"] == 101]        # turn 4: no attack
    assert {"card_uid": 101, "status_flag": "immobilized", "value": 0} in turns[4]["end"]


def test_immobilize_can_fail():
    b = battle(att=(62, 23, 23, 23, 23), dfn=(24, 23, 23), roll=0.9)
    b.start()
    b.play(1)
    turns = b.play(2)
    assert not actions(turns, 3, "immobilize") and [a for a in attacks(turns, 4) if a["card_uid"] == 101]


def test_fusion_doubles_skills_with_three_active():
    b = battle(att=(63, 63, 63, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)                                      # two: no Fusion yet
    assert not actions(turns, 3, "fusion") and {a["value"] for a in actions(turns, 3, "strike")} == {2}
    turns = b.play(3)                                      # the third: all three double up
    fusions = actions(turns, 5, "fusion")
    assert [f["card_uid"] for f in fusions] == [1, 2, 3]
    assert fusions[0]["status"] == [{"card_uid": "1", "status_flag": "enhance_strike", "value": 2}]
    assert {a["value"] for a in actions(turns, 5, "strike")} == {4}


def test_emulate_copies_a_help_given_across_from_it():
    b = battle(att=(64, 23, 23, 23, 23), dfn=(23, 65, 23))
    b.start()
    b.play(1)                                              # turn 2: an enemy Dummy across the Emulator
    b.sides[0].assaults[0].hp = 10
    b.sides[1].assaults[0].hp = 10
    turns = b.play(2)                                      # turn 4: the enemy Healer heals that Dummy
    heal, copy = actions(turns, 4, "heal")
    assert heal["card_uid"] == 102 and heal["targets"] == [101]
    assert copy["card_uid"] == 1 and copy["targets"] == ["1"] and copy["value"] == 4
    assert copy["status"] == [{"card_uid": "1", "status_flag": "h", "value": 14}]   # once
    assert b.sides[0].assaults[0].hp == 14
