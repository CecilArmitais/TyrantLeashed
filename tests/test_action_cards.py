"""Tests for action cards (War Metal Tyrant's one-use cards, ids 3000-3999; no health, no
timer), on small made-up cards: each acts once, before its commander, then is gone. Random
picks take the first candidate."""

import random

from battle.cards import ACTION, CardDB
from battle.engine import Battle, Setup

CARDS = """<root>
  <unit><id>1300</id><name>Captain</name><health>40</health></unit>
  <unit><id>23</id><name>Dummy</name><attack>0</attack><health>20</health></unit>
  <unit><id>24</id><name>Hitter</name><attack>2</attack><health>20</health></unit>
  <unit><id>73</id><name>Paybacker</name><attack>0</attack><health>20</health><skill id="payback"/></unit>
  <unit><id>74</id><name>Evader</name><attack>0</attack><health>20</health><skill id="evade" x="1"/></unit>
  <unit><id>3101</id><name>Airstrike</name><skill id="strike" x="3"/></unit>
  <unit><id>3102</id><name>Barrage</name><skill id="strike" x="1" all="1"/><skill id="heal" x="2" all="1"/></unit>
  <unit><id>3103</id><name>Orders</name><skill id="summon" card_id="24"/><skill id="rally" x="2" all="1"/></unit>
  <unit><id>3104</id><name>Dud</name><skill id="heal" x="2"/></unit>
  <unit><id>3107</id><name>Flashbang</name><skill id="strike" x="1" trigger="play"/><skill id="strike" x="4"/></unit>
  <unit><id>3108</id><name>Fireball</name><skill id="strike" x="2"/><skill id="recharge"/></unit>
</root>"""
DB = CardDB.from_xml(CARDS)


class Rolls(random.Random):
    """random() always gives `roll` (Recharge's chance)."""
    def __init__(self, roll: float):
        super().__init__(1)
        self.roll = roll

    def random(self) -> float:
        return self.roll


class First:
    def __init__(self, roll: float = 0.0):
        self.rng = Rolls(roll)

    def __call__(self, battle, actor, skill_id, candidates, n):
        return candidates[:n]


def battle(att, dfn, roll=0.0):
    return Battle(Setup(DB[1300], [(i + 1, DB[c]) for i, c in enumerate(att)],
                        DB[1300], [(101 + i, DB[c]) for i, c in enumerate(dfn)]), chooser=First(roll), cards=DB)


def by(turn, uid):
    return [a for a in turn.get("actions", []) if a["card_uid"] == uid]


def third_turn(action_card, dfn=(23, 23, 23), hurt=False):
    """Turn 1: a Hitter (hurt, if asked); turn 2: the enemy's first card; turn 3: the
    action card (uid 2)."""
    b = battle(att=(24, action_card, 24, 24, 24), dfn=dfn)
    b.start()
    b.play(1)
    if hurt:
        b.sides[0].assaults[0].hp = 10
    return b, b.play(2)[3]


def test_ids_3000_to_3999_are_action_cards():
    assert DB[3101].type == ACTION and DB[24].type != ACTION


def test_it_acts_before_anything_else_then_is_gone():
    b, turn = third_turn(3101)
    assert turn["plays"] == [{"card_uid": "2", "status": []}]
    strike = turn["actions"][0]                            # before the Hitter's attack
    assert strike["card_uid"] == 2 and strike["skill_id"] == "strike" and strike["targets"] == [101]
    assert [a["skill_id"] for a in turn["actions"][1:]] == [0]
    side = b.sides[0]
    assert side.actions == [] and all(u.uid != 2 for u in side.assaults + side.structures)
    assert "attack_kill" not in turn and "defend_kill" not in turn      # removed, not killed


def test_its_skills_go_off_in_card_order():
    b, turn = third_turn(3102, hurt=True)
    strike, heal = by(turn, 2)
    assert strike["skill_id"] == "strike" and strike["targets"] == [101]
    assert heal["skill_id"] == "heal" and heal["targets"] == ["1"]      # its own side's hurt Hitter


def test_a_summon_comes_first_and_the_newcomer_is_rallied():
    b, turn = third_turn(3103)
    summon, rally = by(turn, 2)
    new_uid = summon["targets"][0]
    assert summon["skill_id"] == "summon" and summon["value"] == 1      # on its own side
    assert rally["skill_id"] == "rally" and set(rally["targets"]) == {"1", str(new_uid)}
    assert [u.card.id for u in b.sides[0].assaults] == [24, 24]


def test_a_dud_does_nothing_and_is_still_gone():
    b, turn = third_turn(3104)                             # nobody hurt: Heal has no target
    assert by(turn, 2) == [] and b.sides[0].actions == []




def test_its_skills_are_not_paid_back_but_can_be_evaded():
    b, turn = third_turn(3101, dfn=(73, 23, 23))
    assert [a["skill_id"] for a in turn["actions"] if a["card_uid"] == 101] == []    # no Payback
    b, turn = third_turn(3101, dfn=(74, 23, 23))
    assert by(turn, 101)[0]["skill_id"] == "evade" and not [a for a in by(turn, 2) if a["skill_id"] == "strike"]


def test_an_on_play_skill_goes_off_as_it_lands_then_the_rest_as_it_acts():
    b, turn = third_turn(3107)
    first, second = by(turn, 2)
    assert first["value"] == 1 and second["value"] == 4
    assert turn["actions"].index(first) < turn["actions"].index(second)


# ---- Recharge ------------------------------------------------------------------------------

def test_recharge_puts_it_back_at_the_bottom_of_the_deck_as_a_new_card():
    b = battle(att=(3108, 24, 24, 24, 24), dfn=(23, 23, 23), roll=0.0)
    b.start()
    turns = b.play(1)                                      # turn 1: used (nothing to Strike yet)
    side = b.sides[0]
    back, = [a for a in turns[1]["actions"] if a["skill_id"] == "recharge"]
    assert back["card_uid"] == 1 and back["targets"] == ["1"]
    assert back["status"] == [{"card_uid": -1, "status_flag": "recharged", "value": 1},
                              {"card_uid": -1, "status_flag": "recharged", "value": 0}]
    new_uid = max(side.cards)
    assert new_uid == 6 and side.cards[new_uid].id == 3108 and side.remaining[-1] == new_uid


def test_a_recharged_card_is_drawn_and_played_again():
    b = battle(att=(3108, 24), dfn=(23, 23, 23, 23, 23), roll=0.0)
    turns = b.start()
    turns.update(b.play(1))                                # the Fireball goes back: Hitter, Fireball left
    while not b.over and b.turn < 12:
        turns.update(b.play(b.sides[0].hand[0] if len(b.sides[0].hand) > 1 else None))
    plays = [p["card_uid"] for t in sorted(turns) for p in turns[t].get("plays", []) if int(p["card_uid"]) < 101]
    assert plays[:3] == ["1", "2", "3"]                    # the Fireball, the Hitter, the Fireball again
    assert b.sides[0].cards[3].id == 3108


def test_recharge_can_fail():
    b = battle(att=(3108, 24, 24, 24, 24), dfn=(23, 23, 23), roll=0.9)
    turns = b.start()
    turns.update(b.play(1))
    assert not [a for t in turns.values() for a in t.get("actions", []) if a["skill_id"] == "recharge"]
    assert len(b.sides[0].cards) == 5
