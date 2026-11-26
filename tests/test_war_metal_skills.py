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
  <unit><id>70</id><name>Freezer</name><attack>0</attack><health>10</health><skill id="freeze"/></unit>
  <unit><id>71</id><name>Mass Freezer</name><attack>0</attack><health>10</health><skill id="freeze" all="1"/></unit>
  <unit><id>72</id><name>Slow</name><attack>2</attack><health>10</health><cost>2</cost></unit>
  <unit><id>73</id><name>Paybacker</name><attack>0</attack><health>10</health><skill id="payback"/></unit>
  <unit><id>74</id><name>Evader</name><attack>0</attack><health>10</health><skill id="evade" x="1"/></unit>
  <unit><id>76</id><name>Snowcaller</name><attack>0</attack><health>3</health><skill id="blizzard"/></unit>
  <unit><id>77</id><name>Slow Summoner</name><attack>0</attack><health>10</health><skill id="summon" card_id="72" trigger="play"/></unit>
  <unit><id>78</id><name>Last Regrower</name><attack>0</attack><health>3</health><skill id="regeneration" x="2"/><skill id="shock" x="3" trigger="death"/></unit>
  <unit><id>79</id><name>Big Regrower</name><attack>0</attack><health>3</health><skill id="regeneration" x="9"/></unit>
  <unit><id>1301</id><name>Jotun</name><health>40</health><skill id="blizzard"/></unit>
  <unit><id>1302</id><name>Miasma</name><health>40</health><skill id="mist"/></unit>
  <unit><id>80</id><name>Infuser</name><type>3</type><attack>0</attack><health>10</health><skill id="infuse"/></unit>
  <unit><id>81</id><name>Raider Infuser</name><type>1</type><attack>0</attack><health>10</health><skill id="infuse" y="2"/></unit>
  <unit><id>82</id><name>Imperial</name><type>1</type><attack>0</attack><health>10</health></unit>
  <unit><id>83</id><name>Raider Healer</name><type>2</type><attack>0</attack><health>10</health><skill id="heal" x="3" y="2"/></unit>
  <unit><id>84</id><name>Progenitor</name><type>6</type><attack>0</attack><health>10</health></unit>
  <unit><id>85</id><name>Bloody</name><type>3</type><attack>0</attack><health>10</health></unit>
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


def regrower_hit(roll, disease=0, regrower=35):
    """Turn 2: an Avenger; turn 4: the Regrower beside it; turn 5: the Hard Hitter, played
    on turn 3, hits the Regrower for 5 (3 health)."""
    b = battle(att=(23, 36, 23, 23, 23), dfn=(20, regrower, 23), roll=roll)
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
    assert "defend_kill" not in turn                       # never listed as killed: the client keeps it
    assert named(turn, "avenge")                           # but it died first: its neighbour Avenges


def test_regeneration_can_fail():
    b, turn = regrower_hit(roll=0.9)
    assert not named(turn, "regeneration")
    assert turn["defend_kill"] == [102] and named(turn, "avenge")


def test_any_disease_stops_regeneration():
    b, turn = regrower_hit(roll=0.0, disease=1)            # War Metal: Disease stops it outright
    assert not named(turn, "regeneration") and turn["defend_kill"] == [102]


def test_regeneration_comes_after_its_death_skills():
    b, turn = regrower_hit(roll=0.0, regrower=78)
    back, hit = turn["actions"][:2]
    assert back["skill_id"] == "regeneration" and hit["targets"] == [102]
    # its On Death Shock goes off (shown as a negative Heal on the active side's commander)
    shock, = [a for a in turn["actions"][2:] if a["card_uid"] == 102 and a["skill_id"] in ("heal", "strike")]
    assert b.sides[0].commander.hp == 40 - 3
    assert b.sides[1].assaults[1].hp == 2 and "defend_kill" not in turn


def test_regeneration_brings_it_back_with_x_even_above_its_max():
    b, turn = regrower_hit(roll=0.0, regrower=79)
    back, hit = turn["actions"][:2]
    assert back["value"] == 9 and back["status"] == [{"card_uid": 102, "status_flag": "h", "value": 9 + 5}]
    assert hit["status"] == [{"card_uid": 102, "status_flag": "h", "value": 9}]
    assert b.sides[1].assaults[1].hp == 9 > b.sides[1].assaults[1].max_hp


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


# ---- Freeze and Blizzard ------------------------------------------------------------------

def test_freeze_stops_an_enemy_until_its_turn_ends():
    assert {"freeze", "blizzard", "mist"} <= BUILT
    b = battle(att=(70, 23, 23, 23, 23), dfn=(24, 23, 23))
    b.start()
    b.play(1)                                              # turn 2: an enemy Hitter across the Freezer
    turns = b.play(2)
    freeze, = actions(turns, 3, "freeze")
    assert freeze["card_uid"] == 1 and freeze["targets"] == [101]
    assert {"card_uid": 101, "status_flag": "frozen", "value": 1} in freeze["status"]
    assert not [a for a in attacks(turns, 4) if a["card_uid"] == 101]        # turn 4: no attack
    assert {"card_uid": 101, "status_flag": "frozen", "value": 0} in turns[4]["end"]
    assert not b.sides[1].assaults[0].frozen


def test_freeze_stops_a_timer():
    def delay_after_turn_4(freezer):
        b = battle(att=(freezer, 23, 23, 23, 23), dfn=(72, 23, 23))
        b.start()
        b.play(1)                                          # turn 2: a Slow card (delay 2)
        b.play(2)                                          # turn 3: frozen (or not); turn 4 counts down
        return b.sides[1].assaults[0].delay
    assert delay_after_turn_4(23) == 1 and delay_after_turn_4(70) == 2


def test_freeze_all_and_no_payback():
    b = battle(att=(71, 23, 23, 23, 23), dfn=(73, 24, 23))
    b.start()
    b.play(1)
    b.play(2)                                              # turns 2 and 4: a Paybacker, a Hitter
    turns = b.play(3)
    freeze, = actions(turns, 5, "freeze")                  # one action, both frozen, nothing back
    assert freeze["card_uid"] == 1 and freeze["targets"] == [101, 102]
    assert not b.sides[0].assaults[0].frozen


def test_freeze_can_be_evaded():
    b = battle(att=(70, 23, 23, 23, 23), dfn=(74, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    assert not actions(turns, 3, "freeze") and actions(turns, 3, "evade")
    assert not b.sides[1].assaults[0].frozen


def test_cleanse_thaws_only_an_active_card():
    b = battle(att=(23, 43, 72, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    dummy = b.sides[0].assaults[0]
    dummy.frozen = True                                    # frozen, its timer out: thawed
    turns = b.play(2)
    cleanse, = actions(turns, 3, "cleanse")
    assert cleanse["status"] == [{"card_uid": "1", "status_flag": "frozen", "value": 0}]
    assert not dummy.frozen


def test_cleanse_leaves_a_frozen_timer():
    b = battle(att=(72, 43, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    slow = b.sides[0].assaults[0]
    slow.frozen = True                                     # frozen with its timer running (1): left
    turns = b.play(2)
    assert not actions(turns, 3, "cleanse")


def blizzard_battle(att, dfn, att_commander=1300, dfn_commander=1301):
    return Battle(Setup(DB[att_commander], [(i + 1, DB[c]) for i, c in enumerate(att)],
                        DB[dfn_commander], [(101 + i, DB[c]) for i, c in enumerate(dfn)]),
                  chooser=First(0.0), cards=DB)


def test_blizzard_hides_the_enemys_cards_until_active():
    b = blizzard_battle(att=(23, 23, 23, 23, 23), dfn=(72, 23, 23, 23))
    b.start()
    turns = b.play(1)                                      # turn 2: Jotun's side plays a Slow card
    assert turns[2]["plays"] == [{"card_uid": 101,
                                  "status": [{"card_uid": 101, "status_flag": "blizzard", "value": 1}]}]
    turns = b.play(2)                                      # turn 4: a card with no timer isn't
    assert turns[4]["plays"] == [{"card_uid": 102, "status": []}]
    assert not [a for t in turns.values() for a in t.get("upkeep", []) if a["skill_id"] == "blizzard"]
    turns = b.play(3)                                      # turn 6: the Slow card's timer runs out
    reveal, = [a for a in turns[6]["upkeep"] if a["skill_id"] == "blizzard"]
    assert reveal == {"card_uid": 101, "skill_id": "blizzard", "targets": [101], "value": 0,
                      "status": [{"card_uid": 101, "status_flag": "blizzard", "value": 0}]}
    assert not b.sides[1].assaults[0].hidden


def test_blizzard_never_hides_the_players_own_cards():
    b = blizzard_battle(att=(72, 23, 23, 23), dfn=(23, 23, 23), att_commander=1301, dfn_commander=1300)
    turns = b.start()
    turns = b.play(1)
    assert turns[1]["plays"] == [{"card_uid": "1", "status": []}] and not b.sides[0].assaults[0].hidden


def test_blizzard_gone_shows_what_it_hid():
    b = blizzard_battle(att=(24, 23, 23, 23, 23), dfn=(76, 72, 23, 23), dfn_commander=1300)
    b.start()
    b.play(1)                                              # turn 2: the Snowcaller (3 health)
    turns = b.play(2)                                      # turn 3: hit for 2; turn 4: a hidden Slow card
    assert turns[4]["plays"][0]["status"] == [{"card_uid": 102, "status_flag": "blizzard", "value": 1}]
    turns = b.play(3)                                      # turn 5: the Snowcaller dies
    assert turns[5]["defend_kill"] == [101]
    reveal, = actions(turns, 5, "blizzard")
    assert reveal["targets"] == [102] and not b.sides[1].assaults[0].hidden


def test_blizzard_hides_a_summoned_card_too():
    b = blizzard_battle(att=(23, 23, 23, 23, 23), dfn=(77, 23, 23))
    b.start()
    turns = b.play(1)                                      # turn 2: it summons a Slow card on play
    summon, = actions(turns, 2, "summon")
    assert summon["status"] == [{"card_uid": 104, "status_flag": "blizzard", "value": 1}]


def test_mist_hides_as_blizzard_does_under_its_own_name():
    b = blizzard_battle(att=(23, 23, 23, 23, 23), dfn=(72, 23, 23, 23), dfn_commander=1302)
    b.start()
    turns = b.play(1)                                      # turn 2: Miasma's side plays a Slow card
    assert turns[2]["plays"] == [{"card_uid": 101,
                                  "status": [{"card_uid": 101, "status_flag": "mist", "value": 1}]}]
    b.play(2)
    turns = b.play(3)                                      # turn 6: its timer runs out
    reveal, = [a for a in turns[6]["upkeep"] if a["skill_id"] == "mist"]
    assert reveal["status"] == [{"card_uid": 101, "status_flag": "mist", "value": 0}]


# ---- Infuse --------------------------------------------------------------------------------

def test_infuse_makes_a_card_the_casters_faction():
    assert "infuse" in BUILT
    b = battle(att=(80, 85, 85, 85, 85), dfn=(82, 85, 85))
    b.start()
    b.play(1)                                              # turn 2: an Imperial across
    turns = b.play(2)
    infuse, = actions(turns, 3, "infuse")                  # the Infuser's own faction: Bloodthirsty
    assert infuse["card_uid"] == 1 and infuse["targets"] == [101] and infuse["value"] == 3
    assert infuse["status"] == [{"card_uid": 101, "status_flag": "infused", "value": 3}]
    imperial = b.sides[1].assaults[0]
    assert imperial.faction == 3 and imperial.card.faction == 1
    assert not actions(b.play(3), 5, "infuse")             # nothing left that isn't Bloodthirsty


def test_infuse_takes_its_own_faction_and_picks_either_side():
    b = battle(att=(81, 85, 82, 82, 82), dfn=(84, 82, 82))
    b.start()
    b.play(1)                                              # turn 2: a Progenitor (every faction already)
    turns = b.play(2)                                      # turn 3: the attacker's own Bloody
    infuse, = actions(turns, 3, "infuse")
    assert infuse["targets"] == ["2"] and infuse["value"] == 2
    assert b.sides[0].assaults[1].faction == 2 and b.sides[1].assaults[0].faction == 6


def test_an_infused_cards_helpful_skills_follow_it():
    def heals(infused):
        b = battle(att=(83, 85, 23, 23, 23), dfn=(23, 23, 23))
        b.start()
        b.play(1)
        b.play(2)                                          # turn 3: a Bloody beside the Raider Healer
        healer, bloody = b.sides[0].assaults[:2]
        healer.infused = 3 if infused else 0
        bloody.hp = 5
        return [a["targets"] for a in actions(b.play(3), 5, "heal")]
    assert heals(False) == [] and heals(True) == [["2"]]   # "Heal Raider" is now "Heal Bloodthirsty"
