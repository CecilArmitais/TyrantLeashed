"""Tests for the rule battleground effects that brawls and wars schedule (the optimizer's
passive BGEs): Bloodlust, Revenge, Brigade, Cold Sleep, Critical Reach, Devotion,
Enduring Rage, Heroism, Iron Will, Metamorphosis, Megamorphosis, Temporal Backlash,
Turning Tides and Zealot's Preservation, on small made-up cards. Random picks take the
first candidate."""

import pytest

from battle import effects as fx
from battle.cards import CardDB
from battle.engine import ATTACKER, Battle, Setup
from common import paths

CARDS = """<root>
  <unit><id>1300</id><name>Captain</name><health>40</health></unit>
  <unit><id>1302</id><name>Raider Medic</name><health>40</health><skill id="heal" x="3" y="2"/></unit>
  <unit><id>10</id><name>Armored</name><attack>1</attack><health>10</health><skill id="armored" x="2"/></unit>
  <unit><id>12</id><name>Counterer</name><attack>0</attack><health>10</health><skill id="counter" x="5"/></unit>
  <unit><id>16</id><name>Weakener</name><attack>0</attack><health>5</health><skill id="weaken" x="2"/></unit>
  <unit><id>22</id><name>Swiper</name><attack>2</attack><health>30</health><skill id="swipe" x="1"/></unit>
  <unit><id>23</id><name>Dummy</name><attack>0</attack><health>20</health></unit>
  <unit><id>24</id><name>Hitter</name><attack>2</attack><health>20</health></unit>
  <unit><id>25</id><name>Glass</name><attack>0</attack><health>1</health></unit>
  <unit><id>31</id><name>Loyal</name><type>1</type><attack>1</attack><health>10</health><skill id="allegiance" x="2"/></unit>
  <unit><id>32</id><name>Outsider</name><type>2</type><attack>0</attack><health>20</health></unit>
  <unit><id>40</id><name>Legionnaire</name><attack>1</attack><health>10</health><skill id="legion" x="2"/></unit>
  <unit><id>43</id><name>Berserker</name><attack>2</attack><health>10</health><skill id="berserk" x="3"/></unit>
  <unit><id>44</id><name>Valiant</name><attack>3</attack><health>10</health><skill id="valor" x="3"/></unit>
  <unit><id>45</id><name>Slow</name><attack>1</attack><health>10</health><cost>2</cost></unit>
  <unit><id>46</id><name>Medic</name><attack>0</attack><health>10</health><skill id="heal" x="3"/></unit>
</root>"""
DB = CardDB.from_xml(CARDS)


def first(battle, actor, skill_id, candidates, n):
    return candidates[:n]


def rule(name: str, value: int = 0):
    """A global rule effect, as game_api passes it."""
    effect = fx.Effect(0, name, "", "", value=value)
    assert effect.passive is not None, name
    return [(fx.SCOPE_UID["global"], "global", effect.caster(), effect.passive)]


def battle(att, dfn, effects=(), att_cmd=1300):
    return Battle(Setup(DB[att_cmd], [(i + 1, DB[c]) for i, c in enumerate(att)],
                        DB[1300], [(101 + i, DB[c]) for i, c in enumerate(dfn)]), chooser=first, effects=effects)


def actions(turns, turn, skill=None, by=None):
    return [a for a in turns[turn].get("actions", []) if (skill is None or a["skill_id"] == skill)
            and (by is None or a["card_uid"] == by)]


def ids(targets):
    return [int(t) for t in targets]


@pytest.mark.needs_game
def test_the_file_names_them():
    effects = {e.name: e for e in fx.load(paths.ASSET_DIR).values()}
    assert effects["Taste of Blood"].passive == ("bloodlust", 3)          # <value>3</value>
    assert effects["Fervorous Revenge"].passive == ("revenge", 3)         # from its text: "by 3"
    assert effects["Devotion"].passive == ("devotion", 4)                 # 25%: a quarter
    assert effects["Heroism"].passive == ("heroism", 2)
    assert effects["Blood Vengeance"].passive is None                     # how Avenge works already
    used = {"Bathe in Blood", "Blood Vengeance", "Brigade", "Cold Sleep", "Critical Reach", "Devotion",
            "Enduring Rage", "Fervorous Revenge", "Furious Revenge", "Heroism", "Iron Will", "Megamorphosis",
            "Metamorphosis", "Scent of Blood", "Spiteful Revenge", "Taste of Blood", "Temporal Backlash",
            "Turning Tides", "Zealot's Preservation"}                     # the brawls' and wars' rule effects
    assert {n for n in used if effects[n].passive is None} == {"Blood Vengeance"}


@pytest.mark.parametrize("effects, value", [(rule("Taste of Blood", 3), 2 + 3), ((), 2)])
def test_bloodlust(effects, value):
    b = battle(att=(24, 24, 23, 23), dfn=(23,) * 4, effects=effects)
    b.start()
    b.play(1)
    turns = b.play(2)            # the first Hitter hits the Dummy across; the second, the commander
    second = [a for a in actions(turns, 3, 0) if a["card_uid"] == 2]
    assert second[0]["value"] == value


def test_revenge_heals_and_rallies_the_allies():
    b = battle(att=(23,) * 6, dfn=(23,) * 6, effects=rule("Fervorous Revenge"))
    b.start()
    for uid in (1, 2, 3):
        b.play(uid)
    row = b.sides[ATTACKER].assaults
    row[1].hp -= 5
    b._damage(row[0], 99)
    b._after_damage()
    heal, = actions(b.turns, 7, "heal", by=-3)
    rally, = actions(b.turns, 7, "rally", by=-3)
    assert (ids(heal["targets"]), heal["value"]) == ([2], 3)
    assert (ids(rally["targets"]), rally["value"]) == ([2, 3], 3)


def test_brigade_heals_with_legions_bonus():
    b = battle(att=(23, 40, 23, 23), dfn=(23, 24, 23, 23), effects=rule("Brigade"))
    b.start()
    b.play(1)                    # a Dummy
    b.play(2)                    # the Legionnaire; the enemy Hitter hits it (10 -> 8)
    turns = b.play(3)            # another Dummy: Legion 2 x 2 = 4 extra damage, and a heal for 4
    heal, = actions(turns, 5, "heal", by=-3)
    assert (ids(heal["targets"]), heal["value"]) == ([2], 4)
    assert heal["status"] == [{"card_uid": "2", "status_flag": "h", "value": 10}]


def test_cold_sleep_heals_cards_under_stasis():
    b = battle(att=(23,) * 5, dfn=(23,) * 5, effects=rule("Cold Sleep"))
    b.start()
    b.play(1)
    dummy = b.sides[ATTACKER].assaults[0]
    dummy.protected_stasis, dummy.hp = 4, 15
    turns = b.play(2)                                        # a card played: Stasis 4 heals 2
    heal, = actions(turns, 3, "heal", by=-3)
    assert (ids(heal["targets"]), heal["value"], dummy.hp) == ([1], 2, 17)


@pytest.mark.parametrize("effects, hit", [(rule("Critical Reach"), [102, 103]), ((), [102])])
def test_critical_reach_swipes_further(effects, hit):
    b = battle(att=(22, 23, 23, 23, 23), dfn=(23,) * 6, effects=effects)
    b.start()
    for uid in (1, 2, 3):
        b.play(uid)
    turns = b.play(4)                                        # the Swiper hits 101, in a row of three
    assert [t for a in actions(turns, 7, "swipe") for t in ids(a["targets"])] == hit


def test_devotion_for_the_commanders_faction():
    b = battle(att=(23, 32, 23, 23), dfn=(23,) * 4, effects=rule("Devotion", 4))
    b.start()
    turns = b.play(1)                                        # imperial, like the Captain: +20/4
    heal, = actions(turns, 1, "heal", by=-3)
    assert heal["value"] == 5 and b.sides[ATTACKER].assaults[0].max_hp == 25
    b.play(2)                                                # a raider: nothing
    assert b.sides[ATTACKER].assaults[1].max_hp == 20


def test_enduring_rage_heals_and_protects_on_berserk():
    b = battle(att=(43, 23, 23), dfn=(23,) * 3, effects=rule("Enduring Rage"))
    b.start()
    turns = b.play(1)                                        # it hits the commander: Berserk 3
    protect, = actions(turns, 1, "protect", by=-3)
    assert (ids(protect["targets"]), protect["value"]) == ([1], 2)     # 3 / 2, rounded up


def test_heroism_protects_and_rewards_kills():
    b = battle(att=(44, 23, 23, 23), dfn=(25, 23, 23, 23), effects=rule("Heroism", 2))
    b.start()
    turns = b.play(1)
    protect, = actions(turns, 1, "protect", by=-3)          # Valor 3: Protect 2 each turn
    assert (ids(protect["targets"]), protect["value"]) == ([1], 2)
    turns = b.play(2)                                        # it kills the Glass across: +3 attack
    kill, = actions(turns, 3, "berserk", by=-3)
    assert kill["value"] == 3 and b.sides[ATTACKER].assaults[0].perm_attack == 3


@pytest.mark.parametrize("effects, weakened", [(rule("Iron Will", 4), False), ((), True)])
def test_iron_will_keeps_weaken_off_armor(effects, weakened):
    b = battle(att=(10, 23, 23), dfn=(16, 23, 23), effects=effects)
    b.start()
    turns = b.play(1)                                        # the enemy Weakener's turn follows
    assert bool(actions(turns, 2, "weaken")) == weakened


@pytest.mark.parametrize("name", ["Metamorphosis", "Megamorphosis"])
def test_faction_limited_skills_reach_every_faction(name):
    def healed(effects):
        b = battle(att=(23,) * 4, dfn=(23,) * 4, effects=effects, att_cmd=1302)
        b.start()
        b.play(1)
        b.sides[ATTACKER].assaults[0].hp -= 5                # an imperial Dummy; the heal is for raiders
        return actions(b.play(2), 3, "heal", by=-1)
    assert healed(rule(name)) and not healed(())


def test_megamorphosis_allegiance_for_every_faction():
    def gained(effects):
        b = battle(att=(31, 32, 23, 23), dfn=(23,) * 4, effects=effects)
        b.start()
        b.play(1)
        b.play(2)                                            # a raider joins the imperial Loyal
        return b.sides[ATTACKER].assaults[0].perm_attack
    assert (gained(rule("Megamorphosis")), gained(rule("Metamorphosis")), gained(())) == (2, 0, 0)


@pytest.mark.parametrize("effects, stasis", [(rule("Temporal Backlash"), 3), ((), 0)])
def test_temporal_backlash_counter_gives_stasis(effects, stasis):
    b = battle(att=(12, 45, 23, 23), dfn=(23,) * 4, effects=effects)
    b.start()
    b.play(1)                                                # Counter 5 in play
    turns = b.play(2)                                        # a delayed card of its faction: Stasis 5/2
    assert [a["value"] for a in actions(turns, 3, "stasis", by=2)] == ([stasis] if stasis else [])


def test_turning_tides_rallies_by_the_attack_taken():
    b = battle(att=(24, 23, 23), dfn=(16, 23, 23), effects=rule("Turning Tides"))
    b.start()
    turns = b.play(1)                                        # the enemy Weakener takes the Hitter's 2
    rally, = actions(turns, 2, "rally", by=-3)
    assert (ids(rally["targets"]), rally["value"]) == ([101], 2)


def test_zealots_preservation_protects_what_an_assault_heals():
    b = battle(att=(23, 46, 23, 23), dfn=(23,) * 4, effects=rule("Zealot's Preservation", 2))
    b.start()
    b.play(1)
    b.sides[ATTACKER].assaults[0].hp -= 5
    heal, = actions(b.play(2), 3, "heal", by=2)              # the Medic heals the Dummy 3: Protect 2
    assert {"card_uid": "1", "status_flag": "protect", "value": 2} in heal["status"]
