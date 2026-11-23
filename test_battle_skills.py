"""Tests for the battle engine's skills (phase 2): each skill on small made-up cards,
plus the reporting rules the recordings showed. Random picks take the first candidate."""

import pytest

import game_api as ga
from battle.cards import CardDB
from battle.engine import Battle, Setup
from battle.recorded import compare, from_fixtures

CARDS = """<root>
  <unit><id>1300</id><name>Captain</name><health>40</health></unit>
  <unit><id>1301</id><name>Healer Captain</name><health>40</health><skill id="heal" x="2"/></unit>
  <unit><id>10</id><name>Armored</name><attack>1</attack><health>10</health><skill id="armored" x="2"/></unit>
  <unit><id>11</id><name>Piercer</name><attack>3</attack><health>5</health><skill id="pierce" x="1"/></unit>
  <unit><id>12</id><name>Counterer</name><attack>0</attack><health>10</health><skill id="counter" x="2"/></unit>
  <unit><id>13</id><name>Striker</name><attack>0</attack><health>5</health><skill id="strike" x="3"/></unit>
  <unit><id>15</id><name>Rallier</name><attack>1</attack><health>5</health><skill id="rally" x="2"/></unit>
  <unit><id>16</id><name>Weakener</name><attack>0</attack><health>5</health><skill id="weaken" x="2"/></unit>
  <unit><id>17</id><name>Fortifier</name><attack>0</attack><health>5</health><skill id="fortify" x="1"/></unit>
  <unit><id>18</id><name>Poisoner</name><attack>0</attack><health>10</health><skill id="poison" x="4"/></unit>
  <unit><id>19</id><name>Hunter</name><attack>1</attack><health>5</health><skill id="hunt" x="2"/></unit>
  <unit><id>20</id><name>Avenger</name><attack>1</attack><health>5</health><skill id="avenge" x="2"/></unit>
  <unit><id>21</id><name>Pylon</name><attack>0</attack><health>9</health><skill id="barrier" x="2"/></unit>
  <unit><id>22</id><name>Swiper</name><attack>2</attack><health>5</health><skill id="swipe" x="1"/></unit>
  <unit><id>23</id><name>Dummy</name><attack>0</attack><health>20</health></unit>
  <unit><id>24</id><name>Hitter</name><attack>2</attack><health>20</health></unit>
  <unit><id>25</id><name>Glass</name><attack>0</attack><health>1</health></unit>
  <unit><id>26</id><name>Mortar</name><attack>0</attack><health>5</health><skill id="besiege" x="3"/></unit>
  <unit><id>28</id><name>Copycat</name><attack>0</attack><health>10</health><skill id="mimic" x="4"/></unit>
  <unit><id>27</id><name>Drainer</name><attack>2</attack><health>10</health><skill id="drain" x="3"/></unit>
  <unit><id>2100</id><name>Wall</name><health>6</health><skill id="wall"/></unit>
</root>"""
DB = CardDB.from_xml(CARDS)


def first(battle, actor, skill_id, candidates, n):
    return candidates[:n]


def battle(att, dfn, att_cmd=1300, def_cmd=1300):
    return Battle(Setup(DB[att_cmd], [(i + 1, DB[c]) for i, c in enumerate(att)],
                        DB[def_cmd], [(101 + i, DB[c]) for i, c in enumerate(dfn)]), chooser=first)


def actions(turns, turn, skill=None):
    return [a for a in turns[turn].get("actions", []) if skill is None or a["skill_id"] == skill]


def test_armor_pierce_and_blocks():
    b = battle(att=(10, 23, 23, 23), dfn=(11, 23, 23))
    b.start()
    turns = b.play(1)                                     # Armored in slot 0; the enemy Piercer hits it
    pierce, hit = actions(turns, 2)
    assert pierce == {"card_uid": 101, "skill_id": "pierce", "targets": ["1"], "value": 1, "status": []}
    assert hit["value"] == 2 and hit["block"] == [{"card_uid": "1", "skill": "armored", "value": 1}]
    assert hit["status"] == [{"card_uid": "1", "status_flag": "h", "value": 8}]


def test_counter_even_when_blocked():
    b = battle(att=(12, 23, 23, 23), dfn=(24, 23, 23))
    b.start()
    turns = b.play(1)
    attack, counter = actions(turns, 2)
    assert attack["value"] == 2
    assert counter == {"card_uid": "1" if False else 1, "skill_id": "counter", "targets": [101], "value": 2,
                       "block": [], "status": [{"card_uid": 101, "status_flag": "h", "value": 18},
                                               {"card_uid": "1", "status_flag": "h", "value": 8}]}


def test_strike_heal_rally_weaken():
    b = battle(att=(13, 15, 23, 23, 23), dfn=(24, 24, 23), att_cmd=1301)
    b.start()
    b.play(1)                                              # Striker; the enemy Hitter hits it (5 -> 3)
    turns = b.play(2)                                      # Rallier
    heal, = actions(turns, 3, "heal")                      # the commander acts first and heals the Striker
    assert heal["card_uid"] == -1 and heal["targets"] == ["1"] and heal["status"] == [
        {"card_uid": "1", "status_flag": "h", "value": 5}]
    strike, = actions(turns, 3, "strike")                  # its health was shown this reply: repeated
    assert strike == {"card_uid": 1, "skill_id": "strike", "targets": [101], "value": 3,
                      "status": [{"card_uid": 101, "status_flag": "h", "value": 17},
                                 {"card_uid": "1", "status_flag": "h", "value": 5}]}
    rally, = actions(turns, 3, "rally")
    assert rally["targets"] == ["2"] and rally["status"] == [{"card_uid": "2", "status_flag": "attack_boost", "value": 2}]
    assert turns[3]["end"] == [{"card_uid": "2", "status_flag": "attack_boost", "value": 0}]
    b = battle(att=(16, 23, 23, 23), dfn=(24, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    weaken, = actions(turns, 3, "weaken")
    assert weaken["targets"] == [101] and weaken["status"][0] == {"card_uid": 101, "status_flag": "attack_boost", "value": -2}


def test_fortify_is_adjacent_and_permanent():
    b = battle(att=(23, 17, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    b.play(2)                                              # Fortifier in slot 1, next to the Dummy
    turns = b.play(3)                                      # another Dummy in slot 2: both neighbours
    fortify, = actions(turns, 5, "fortify")
    assert fortify["targets"] == ["1", "3"] and fortify["value"] == 1
    assert fortify["status"][:2] == [{"card_uid": "1", "status_flag": "perm_max_health", "value": 2},
                                     {"card_uid": "3", "status_flag": "perm_max_health", "value": 1}]
    assert b.sides[0].assaults[0].max_hp == 22


def test_poison_ticks_at_the_end_of_the_poisoned_cards_turn():
    b = battle(att=(18, 23, 23, 23), dfn=(24, 23, 23))
    b.start()
    turns = b.play(1)                                      # the Hitter attacks the Poisoner
    tick = actions(turns, 2, "poison")[-1]
    assert tick["card_uid"] == 1 and tick["targets"] == [101] and tick["value"] == 4
    assert tick["status"][:2] == [{"card_uid": 101, "status_flag": "poison", "value": 2},
                                  {"card_uid": 101, "status_flag": "h", "value": 16}]


def test_hunt_shows_as_swipe_and_swipe_hits_neighbours():
    b = battle(att=(23, 23, 23, 23, 23), dfn=(19, 23, 23))
    b.start()
    turns = b.play(1)
    hunt, = actions(turns, 2, "swipe")                     # the server shows Hunt with Swipe's name
    assert hunt["card_uid"] == 101 and hunt["targets"] == ["1"] and hunt["value"] == 2
    b = battle(att=(23, 23, 23, 23, 23, 23), dfn=(23, 22, 23))
    b.start()
    b.play(1)
    b.play(2)
    turns = b.play(3)                                      # three Dummies; the Swiper lands across slot 1
    swipes = actions(turns, 6, "swipe")                    # one action per card hit
    assert [s["targets"] for s in swipes] == [["1"], ["3"]] and swipes[0]["value"] == 1


def test_mimic_uses_an_enemy_skill_at_its_own_value():
    b = battle(att=(28, 23, 23, 23), dfn=(13, 23, 23, 23))
    b.start()
    turns = b.play(1)                                     # nothing to copy yet
    assert not actions(turns, 1, "mimic")
    turns = b.play(2)                                     # the enemy Striker (Strike 3) is in play now
    mimic, strike = actions(turns, 3, "mimic") + actions(turns, 3, "strike")
    assert (mimic["card_uid"], mimic["targets"], mimic["value"]) == (1, [101], 4)
    assert (strike["card_uid"], strike["targets"], strike["value"]) == (1, [101], 4)   # Mimic's 4, not 3


def test_drain_heals_with_no_targets():
    b = battle(att=(27, 23, 23, 23), dfn=(24, 23, 23, 23))
    b.start()
    b.play(1)                                             # the Drainer; the enemy Hitter hits it (10 -> 8)
    turns = b.play(2)                                     # it hits the Hitter (2) and drains that back
    drain, = actions(turns, 3, "drain")
    # no targets: the client's Drain_Target animation on any card threw errors every frame
    assert drain["card_uid"] == 1 and drain["targets"] == [] and drain["value"] == 2
    assert {"card_uid": "1", "status_flag": "h", "value": 10} in drain["status"]     # 8 + 2


def test_wall_takes_hits_meant_for_the_commander():
    b = battle(att=(24, 23, 23, 23), dfn=(2100, 23, 23))
    b.start()
    b.play(1)                                              # the enemy plays the Wall (a structure)
    turns = b.play(2)
    hit = actions(turns, 3)[0]
    assert hit["targets"] == [101] and hit["block"] == [{"card_uid": 101, "skill": "wall", "value": 2}]


def test_mortar_prefers_structures():
    b = battle(att=(26, 23, 23, 23), dfn=(2100, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    mortar, = actions(turns, 3, "besiege")
    assert mortar["targets"] == [101] and mortar["value"] == 3        # structure: full value
    b = battle(att=(26, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    mortar, = actions(turns, 3, "besiege")
    assert mortar["value"] == 2                                       # assault: half, rounded up


def test_avenge():
    b = battle(att=(20, 25, 23, 23), dfn=(24, 24, 23))
    b.start()
    b.play(1)                                              # Avenger in slot 0
    turns = b.play(2)                                      # Glass in slot 1, killed by the second Hitter
    avenge, = actions(turns, 4, "avenge")
    assert avenge["card_uid"] == 1 and avenge["value"] == 2
    assert avenge["status"][:2] == [{"card_uid": "1", "status_flag": "avenge_attack", "value": 2},
                                    {"card_uid": "1", "status_flag": "perm_max_health", "value": 2}]


def test_barrier_on_play_each_turn_and_after_hits():
    b = battle(att=(24, 23, 23, 23, 23), dfn=(21, 23, 23, 23))
    b.start()
    turns = b.play(1)
    on_play, = actions(turns, 2, "barrier")
    assert on_play["status"] == [{"card_uid": 101, "status_flag": "protect", "value": 2}]
    turns = b.play(2)                                      # turn 3: the Hitter hits it through Protect 2
    hit = actions(turns, 3)[0]
    assert hit["value"] == 0 and hit["block"] == [{"card_uid": 101, "skill": "protect", "value": 2}]
    assert turns[4]["begin"] == [{"card_uid": 101, "status_flag": "protect", "value": 0}]
    assert turns[4]["upkeep"][0]["skill_id"] == "barrier"


def test_a_last_single_card_is_played_for_you():
    b = battle(att=(23, 23), dfn=(24, 24, 24))
    b.start()
    turns = b.play(1)                                      # one card left, nothing to draw
    assert {"card_uid": "2", "status": []} in turns[3]["plays"]


ASSETS = ga.ASSET_DIR
REPLAY = ga.FIXTURE_DIR / "replay.json"


@pytest.mark.skipif(not (ASSETS.is_dir() and REPLAY.is_file()), reason="needs the archive and the recordings")
def test_every_recorded_battle_matches():
    db = CardDB.load(ASSETS)
    for rec in from_fixtures(REPLAY):
        r = compare(rec, db)
        assert r.ok, f"{rec.name}: {r.difference}"
