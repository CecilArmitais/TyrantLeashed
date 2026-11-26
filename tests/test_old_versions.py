"""Tests for War Metal Tyrant's own versions of skills Tyrant Unleashed kept but changed
(wmjam, wmevade...; engine OLD_VERSIONS), on small made-up cards. Random picks take the
first candidate; random() rolls come from a fixed list (the last one repeats)."""

import random

from battle.cards import CardDB
from battle.engine import BUILT, OLD_VERSIONS, Battle, Setup

CARDS = """<root>
  <unit><id>1300</id><name>Captain</name><health>40</health></unit>
  <unit><id>23</id><name>Dummy</name><attack>0</attack><health>20</health></unit>
  <unit><id>24</id><name>Hitter</name><attack>2</attack><health>20</health></unit>
  <unit><id>25</id><name>Glass</name><attack>0</attack><health>1</health></unit>
  <unit><id>36</id><name>Hard Hitter</name><attack>5</attack><health>20</health></unit>
  <unit><id>43</id><name>Cleanser</name><attack>0</attack><health>10</health><skill id="cleanse"/></unit>
  <unit><id>50</id><name>Counterer</name><attack>0</attack><health>10</health><skill id="counter" x="2"/></unit>
  <unit><id>65</id><name>Healer</name><attack>0</attack><health>9</health><skill id="heal" x="4"/></unit>
  <unit><id>72</id><name>Slow</name><attack>2</attack><health>10</health><cost>2</cost></unit>
  <unit><id>122</id><name>Quick</name><attack>2</attack><health>10</health><cost>1</cost></unit>
  <unit><id>90</id><name>Old Jammer</name><attack>0</attack><health>10</health><skill id="wmjam"/></unit>
  <unit><id>91</id><name>Old Evader</name><attack>0</attack><health>20</health><skill id="wmevade"/></unit>
  <unit><id>92</id><name>Striker</name><attack>0</attack><health>10</health><skill id="strike" x="3"/></unit>
  <unit><id>93</id><name>Old Paybacker</name><attack>0</attack><health>20</health><skill id="wmpayback"/></unit>
  <unit><id>94</id><name>Slippery Striker</name><attack>0</attack><health>10</health><skill id="strike" x="3"/><skill id="evade" x="1"/></unit>
  <unit><id>95</id><name>Mortar</name><attack>0</attack><health>10</health><skill id="besiege" x="2"/></unit>
  <unit><id>96</id><name>Old Tributer</name><attack>0</attack><health>20</health><skill id="wmtribute"/></unit>
  <unit><id>97</id><name>Protector</name><attack>0</attack><health>10</health><skill id="protect" x="2"/></unit>
  <unit><id>98</id><name>Old Flurrier</name><attack>2</attack><health>20</health><skill id="wmflurry" x="2"/></unit>
  <unit><id>99</id><name>Old Mimic</name><attack>0</attack><health>20</health><skill id="wmmimic"/></unit>
  <unit><id>100</id><name>Mimicked</name><attack>0</attack><health>20</health><skill id="strike" x="2"/><skill id="protect" x="1"/></unit>
  <unit><id>110</id><name>Old Summoner</name><attack>0</attack><health>10</health><skill id="wmsummon" card_id="23"/></unit>
  <unit><id>111</id><name>Old Rusher</name><attack>0</attack><health>10</health><skill id="wmrush" x="2"/></unit>
  <unit><id>113</id><name>Old Refresher</name><attack>0</attack><health>10</health><skill id="wmrefresh"/></unit>
  <unit><id>114</id><name>Old Counterer</name><attack>0</attack><health>10</health><skill id="wmcounter" x="2"/></unit>
  <unit><id>115</id><name>Armored Old Counterer</name><attack>0</attack><health>10</health><skill id="wmcounter" x="2"/><skill id="armored" x="5"/></unit>
  <unit><id>116</id><name>Old Leecher</name><attack>3</attack><health>10</health><skill id="wmleech" x="2"/></unit>
  <unit><id>117</id><name>Old Valor</name><attack>1</attack><health>20</health><skill id="wmvalor" x="3"/></unit>
  <unit><id>118</id><name>Old Poisoner</name><attack>1</attack><health>20</health><skill id="wmpoison" x="2"/></unit>
  <unit><id>120</id><name>Old Diseaser</name><attack>1</attack><health>20</health><skill id="wmdisease"/></unit>
  <unit><id>121</id><name>Old Swiper</name><attack>1</attack><health>20</health><skill id="wmswipe"/></unit>
</root>"""
DB = CardDB.from_xml(CARDS)


class Rolls(random.Random):
    """random() gives these rolls in turn, then the last one again and again."""
    getrandbits = random.Random.getrandbits

    def __init__(self, rolls):
        super().__init__(1)
        self.rolls = list(rolls)

    def random(self) -> float:
        return self.rolls.pop(0) if len(self.rolls) > 1 else self.rolls[0]


class First:
    """The first candidate, with fixed rolls."""
    def __init__(self, rolls):
        self.rng = Rolls(rolls)

    def __call__(self, battle, actor, skill_id, candidates, n):
        return candidates[:n]


def battle(att, dfn, rolls=(0.0,)):
    return Battle(Setup(DB[1300], [(i + 1, DB[c]) for i, c in enumerate(att)],
                        DB[1300], [(101 + i, DB[c]) for i, c in enumerate(dfn)]), chooser=First(rolls), cards=DB)


def actions(turns, turn, skill=None, section="actions"):
    return [a for a in turns[turn].get(section, []) if skill is None or a["skill_id"] == skill]


def attacks(turns, turn):
    return actions(turns, turn, 0)


def status(uid, flag, value):
    return {"card_uid": uid, "status_flag": flag, "value": value}


# Cards act on the turn they're played; the attacker plays on odd turns, the defender on even
# ones (its oldest card). play(n) runs the attacker's turn and the defender's after it.


def test_all_are_built_and_shown_as_the_unleashed_skill():
    assert set(OLD_VERSIONS) <= BUILT
    assert all(sid == "wm" + base for sid, base in OLD_VERSIONS.items())
    b = battle(att=(90, 91, 93, 96, 98, 99, 110, 111, 113, 114, 116, 117, 118, 120, 121), dfn=(23, 23, 23))
    b.start()
    turns = b.play(1)
    for uid in range(2, 6):
        turns.update(b.play(uid))
    assert not b.ignored_skills
    shown = {a["skill_id"] for t in turns.values() for section in t.values() if isinstance(section, list)
             for a in section if isinstance(a, dict) and "skill_id" in a}
    assert not {s for s in shown if isinstance(s, str) and s.startswith("wm")}


# ---- Jam ----------------------------------------------------------------------------------------

def test_old_jam_jams_an_active_enemy_half_the_time():
    b = battle(att=(90, 23, 23, 23), dfn=(24, 23, 23), rolls=(0.0,))
    b.start()
    b.play(1)                                              # turn 2: the Hitter hits the Jammer (8)
    turns = b.play(2)
    jam, = actions(turns, 3, "jam")                        # shown as Unleashed's Jam
    assert jam["card_uid"] == 1 and jam["targets"] == [101]
    assert status(101, "jammed", 1) in jam["status"]
    assert not attacks(turns, 4) and b.sides[0].assaults[0].hp == 8


def test_old_jam_that_misses_shows_nothing():
    b = battle(att=(90, 23, 23, 23), dfn=(24, 23, 23), rolls=(0.9,))
    b.start()
    b.play(1)
    turns = b.play(2)
    assert not actions(turns, 3, "jam") and not actions(turns, 3, "evade")
    assert attacks(turns, 4) and b.sides[0].assaults[0].hp == 6


def test_old_jam_skips_a_card_whose_timer_is_running():
    b = battle(att=(90, 23, 23, 23), dfn=(122, 23, 23))      # the Quick has 1 turn left on turn 3
    b.start()
    b.play(1)
    turns = b.play(2)
    assert not actions(turns, 3, "jam")


# ---- Evade --------------------------------------------------------------------------------------

def test_old_evade_avoids_every_skill_half_the_time():
    b = battle(att=(92, 92, 23, 23), dfn=(91, 23, 23), rolls=(0.0,))
    b.start()
    b.play(1)
    turns = b.play(2)                                      # turn 3: two Strikes, both evaded
    assert len(actions(turns, 3, "evade")) == 2 and not actions(turns, 3, "strike")
    assert b.sides[1].assaults[0].hp == 20


def test_old_evade_can_fail():
    b = battle(att=(92, 92, 23, 23), dfn=(91, 23, 23), rolls=(0.9,))
    b.start()
    b.play(1)
    turns = b.play(2)
    assert not actions(turns, 3, "evade") and b.sides[1].assaults[0].hp == 14


# ---- Payback ------------------------------------------------------------------------------------

def test_old_payback_strikes_back_half_the_time():
    b = battle(att=(92, 23, 23, 23), dfn=(93, 23, 23), rolls=(0.0,))
    b.start()
    b.play(1)
    turns = b.play(2)
    strike, back = actions(turns, 3, "strike")
    assert strike["targets"] == [101] and back["card_uid"] == 101 and back["targets"] == ["1"]
    assert back["value"] == 3 and b.sides[0].assaults[0].hp == 7


def test_old_payback_can_fail():
    b = battle(att=(92, 23, 23, 23), dfn=(93, 23, 23), rolls=(0.9,))
    b.start()
    b.play(1)
    turns = b.play(2)
    assert len(actions(turns, 3, "strike")) == 1 and b.sides[0].assaults[0].hp == 10


def test_old_payback_answers_only_its_five_skills():
    b = battle(att=(95, 23, 23, 23), dfn=(93, 23, 23), rolls=(0.0,))    # Mortar's besiege: not one
    b.start()
    b.play(1)
    turns = b.play(2)
    assert len(actions(turns, 3, "besiege")) == 1 and b.sides[0].assaults[0].hp == 10


def test_old_payback_can_be_evaded():
    b = battle(att=(94, 23, 23, 23), dfn=(93, 23, 23), rolls=(0.0,))    # the striker has Evade 1
    b.start()
    b.play(1)
    turns = b.play(2)
    assert len(actions(turns, 3, "strike")) == 1
    evade, = actions(turns, 3, "evade")
    assert evade["card_uid"] == 1 and b.sides[0].assaults[0].hp == 10


def test_an_old_jam_paid_back_rolls_its_chance_again():
    # the Jam lands (0.0), Payback (0.0), then the Jam going back misses (0.9)
    b = battle(att=(90, 23, 23, 23), dfn=(93, 23, 23), rolls=(0.0, 0.0, 0.9))
    b.start()
    b.play(1)
    turns = b.play(2)
    jam, = actions(turns, 3, "jam")
    assert jam["targets"] == [101] and not b.sides[0].assaults[0].jammed
    b = battle(att=(90, 23, 23, 23), dfn=(93, 23, 23), rolls=(0.0,))
    b.start()
    b.play(1)
    turns = b.play(2)
    jam, back = actions(turns, 3, "jam")                   # back on the Jammer, for the rest of its turn
    assert back["card_uid"] == 101 and back["targets"] == ["1"] and status("1", "jammed", 1) in back["status"]


# ---- Tribute ------------------------------------------------------------------------------------

def test_old_tribute_shares_a_protect_half_the_time():
    b = battle(att=(96, 97, 23, 23), dfn=(23, 23, 23), rolls=(0.0,))
    b.start()
    b.play(1)
    turns = b.play(2)                                      # turn 3: Protect on the Tributer
    protect, = actions(turns, 3, "protect")
    assert protect["card_uid"] == 2 and protect["targets"] == ["1", "2"]
    assert protect["status"][:2] == [status("1", "protect", 2), status("2", "protect", 2)]


def test_old_tribute_can_fail():
    b = battle(att=(96, 97, 23, 23), dfn=(23, 23, 23), rolls=(0.9,))
    b.start()
    b.play(1)
    turns = b.play(2)
    protect, = actions(turns, 3, "protect")
    assert protect["targets"] == ["1"] and protect["status"][:1] == [status("1", "protect", 2)]


# ---- Flurry -------------------------------------------------------------------------------------

def test_old_flurry_attacks_x_more_times_half_the_time():
    b = battle(att=(98, 23, 23, 23), dfn=(23, 23, 23), rolls=(0.0,))
    turns = b.start()
    turns.update(b.play(1))
    flurry, = actions(turns, 1, "flurry")
    assert flurry["card_uid"] == 1 and flurry["value"] == 2
    assert len(attacks(turns, 1)) == 3 and b.sides[1].commander.hp == 40 - 6


def test_old_flurry_can_fail():
    b = battle(att=(98, 23, 23, 23), dfn=(23, 23, 23), rolls=(0.9,))
    b.start()
    turns = b.play(1)
    assert not actions(turns, 1, "flurry") and len(attacks(turns, 1)) == 1


def test_old_flurry_goes_on_at_the_commander_once_the_card_across_dies():
    b = battle(att=(98, 23, 23, 23), dfn=(25, 23, 23), rolls=(0.0,))
    b.start()
    b.play(1)
    turns = b.play(2)
    assert [a["targets"] for a in attacks(turns, 3)] == [[101], [-2], [-2]]


# ---- Mimic --------------------------------------------------------------------------------------

def test_old_mimic_uses_all_the_skills_of_a_card():
    b = battle(att=(99, 23, 23, 23), dfn=(100, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)                                      # turn 3: Strike 2 and Protect 1, as its own
    mimic, strike, protect = [a for a in actions(turns, 3) if a["card_uid"] == 1]
    assert mimic["skill_id"] == "mimic" and mimic["targets"] == [101]
    assert (strike["skill_id"], strike["targets"], strike["value"]) == ("strike", [101], 2)
    assert (protect["skill_id"], protect["targets"], protect["value"]) == ("protect", ["1"], 1)
    assert protect["status"][0] == status("1", "protect", 1)


def test_old_mimic_on_a_card_with_no_skills_does_nothing_more():
    b = battle(att=(99, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    assert [a["skill_id"] for a in actions(turns, 3) if a["card_uid"] == 1] == ["mimic"]


# ---- Summon, Rush ------------------------------------------------------------------------------

def test_old_summon_summons_every_turn():
    b = battle(att=(110, 23, 23, 23), dfn=(23, 23, 23))
    turns = b.start()
    turns.update(b.play(1))
    turns.update(b.play(2))
    assert actions(turns, 1, "summon") and actions(turns, 3, "summon")
    assert [u.card.id for u in b.sides[0].assaults] == [110, 23, 23, 23]


def test_old_rush_takes_x_off_a_timer_at_most_to_0():
    b = battle(att=(72, 111, 23, 23), dfn=(23, 23, 23))       # the Slow: 1 turn left on turn 3
    b.start()
    b.play(1)
    turns = b.play(2)
    rush, = actions(turns, 3, "rush")
    assert rush["card_uid"] == 2 and rush["targets"] == ["1"] and rush["value"] == 1
    assert b.sides[0].assaults[0].delay == 0


# ---- Refresh, Counter, Leech --------------------------------------------------------------------

def test_old_refresh_heals_to_full_after_the_poison():
    b = battle(att=(113, 23, 23, 23), dfn=(36, 23, 23))
    b.start()
    b.play(1)                                              # turn 2: the Hard Hitter: 5 off
    b.sides[0].assaults[0].poisoned = 2
    turns = b.play(2)                                      # turn 3 ends: poison 2, then full
    poison, refresh = [a for a in actions(turns, 3) if a["skill_id"] in ("poison", "refresh")]
    assert poison["skill_id"] == "poison" and refresh["skill_id"] == "refresh" and refresh["value"] == 7
    assert b.sides[0].assaults[0].hp == 10 - 5          # the Hard Hitter again on turn 4


def test_old_counter_needs_damage():
    b = battle(att=(24, 23, 23, 23), dfn=(114, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    counter, = actions(turns, 3, "counter")
    assert counter["card_uid"] == 101 and counter["targets"] == ["1"] and counter["value"] == 2
    b = battle(att=(24, 23, 23, 23), dfn=(115, 23, 23))      # Armor 5: the attack does nothing
    b.start()
    b.play(1)
    turns = b.play(2)
    assert not actions(turns, 3, "counter") and b.sides[0].assaults[0].hp == 20


def test_old_leech_heals_up_to_x_but_not_past_its_max():
    b = battle(att=(116, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    b.sides[0].assaults[0].hp = 5
    turns = b.play(2)                                      # 3 damage: heals 2
    leech, = actions(turns, 3, "leech")
    assert leech["value"] == 2 and leech["status"][0] == status("1", "h", 7)
    assert b.sides[0].assaults[0].max_hp == 10
    b.sides[0].assaults[0].hp = 9
    turns = b.play(3)
    leech, = actions(turns, 5, "leech")
    assert leech["value"] == 1 and b.sides[0].assaults[0].hp == 10


# ---- Valor, Poison, Disease, Swipe --------------------------------------------------------------

def test_old_valor_hits_harder_while_outnumbered():
    b = battle(att=(117,), dfn=(23, 23, 23))              # one card: the turns run on
    b.start()
    turns = b.play(1)
    assert attacks(turns, 1)[0]["value"] == 1              # 1 against 0
    assert attacks(turns, 3)[0]["value"] == 1              # 1 against 1
    assert attacks(turns, 5)[0]["value"] == 1 + 3          # 1 against 2


def test_old_poison_ticks_at_the_start_of_the_poisoned_cards_turn():
    b = battle(att=(118, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    venom, = actions(turns, 3, "venom")                    # shown landing as Unleashed's Venom
    assert venom["targets"] == [101] and venom["status"][0] == status(101, "poison", 2)
    tick, = actions(turns, 4, "poison", section="upkeep")
    assert tick["targets"] == [101] and tick["value"] == 2
    assert b.sides[1].assaults[0].hp == 20 - 1 - 2


def test_old_poison_keeps_the_stronger_one():
    b = battle(att=(118, 23, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    b.sides[1].assaults[0].wm_poison = 5
    turns = b.play(2)
    assert not actions(turns, 3, "venom") and b.sides[1].assaults[0].wm_poison == 5


def test_old_disease_stops_healing():
    b = battle(att=(120, 23, 23, 23), dfn=(23, 65, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    disease, = actions(turns, 3, "disease")
    assert disease["targets"] == [101] and disease["status"][0] == status(101, "disease", 1)
    assert not actions(turns, 4, "heal") and b.sides[1].assaults[0].hp == 19   # Heal passes it over


def test_cleanse_takes_old_poison_and_disease_off():
    b = battle(att=(23, 43, 23, 23), dfn=(23, 23, 23))
    b.start()
    b.play(1)
    dummy = b.sides[0].assaults[0]
    dummy.wm_poison, dummy.wm_diseased = 2, True
    turns = b.play(2)
    cleanse, = actions(turns, 3, "cleanse")
    assert cleanse["status"] == [status("1", "poison", 0), status("1", "disease", 0)]
    assert (dummy.wm_poison, dummy.wm_diseased) == (0, False)


def test_old_swipe_attacks_left_to_right():
    b = battle(att=(23, 121, 23, 23, 23), dfn=(50, 23, 50, 23))
    b.start()
    turns = {}
    for uid in (1, 2, 3, 4):
        turns.update(b.play(uid))
    assert [a["targets"] for a in attacks(turns, 3)] == [[-2]]          # nothing across: no Swipe
    assert [a["targets"] for a in attacks(turns, 7)] == [[101], [102], [103]]
    assert len(actions(turns, 7, "counter")) == 2                       # each Counterer hits back
