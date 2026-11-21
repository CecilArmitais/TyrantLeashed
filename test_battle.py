"""Tests for the battle engine (phase 1): card data, missions, the turn loop and plain
attacks, the replies, the server wiring, and the check against recorded battles."""

import json
from pathlib import Path

import pytest

import game_api as ga
from battle.cards import ASSAULT, COMMANDER, STRUCTURE, CardDB, card_type
from battle.engine import TURN_LIMIT, Battle, BattleError, Setup
from battle.missions import parse_missions
from battle.protocol import BattleSession
from battle.recorded import compare, from_fixtures

import xml.etree.ElementTree as ET

CARDS = """<root>
  <unit><id>1000</id><name>Cyrus</name><health>10</health><skill id="heal" x="1" y="1"/></unit>
  <unit><id>1297</id><name>Weak Captain</name><health>1</health></unit>
  <unit><id>1300</id><name>Tough Captain</name><health>40</health></unit>
  <unit><id>1</id><name>Grunt</name><attack>1</attack><health>2</health><cost>0</cost><type>2</type><rarity>1</rarity>
    <upgrade><level>2</level><card_id>5</card_id><attack>2</attack></upgrade>
    <upgrade><level>3</level><card_id>7</card_id><health>5</health><skill id="armored" x="1"/></upgrade>
  </unit>
  <unit><id>2</id><name>Slow Gunner</name><attack>3</attack><health>4</health><cost>1</cost>
    <skill id="pierce" x="2"/><skill id="strike" x="1" all="1" trigger="Play"/><skill id="flurry"/></unit>
  <unit><id>3</id><name>Tank</name><attack>0</attack><health>9</health><cost>0</cost></unit>
  <unit><id>8001</id><name>Bunker</name><health>6</health><cost>1</cost></unit>
</root>"""
DB = CardDB.from_xml(CARDS)


# ---- card data ------------------------------------------------------------------------------------

def test_upgrades_inherit_and_replace():
    grunt, lv2, lv3 = DB[1], DB[5], DB[7]
    assert (grunt.attack, grunt.health, grunt.level, grunt.skills) == (1, 2, 1, ())
    assert (lv2.attack, lv2.health, lv2.level, lv2.name, lv2.base_id, lv2.faction) == (2, 2, 2, "Grunt", 1, 2)
    assert (lv3.attack, lv3.health, lv3.level) == (2, 5, 3)
    assert [s.id for s in lv3.skills] == ["armored"]


def test_skill_attributes_and_card_types():
    gunner = DB[2]
    pierce, strike, flurry = gunner.skills
    assert (pierce.id, pierce.x, pierce.all, pierce.trigger) == ("pierce", 2, False, "activate")
    assert (strike.all, strike.trigger) == (True, "play")
    assert flurry.c == 1                                               # flurry's cooldown defaults to 1
    assert DB[1000].skill("heal").y == 1
    assert (DB[1].type, DB[1000].type, DB[8001].type) == (ASSAULT, COMMANDER, STRUCTURE)
    assert [card_type(i) for i in (999, 1500, 2500, 5000, 9000, 12000, 20000, 26000, 40000, 52000, 60000)] == [
        ASSAULT, COMMANDER, STRUCTURE, ASSAULT, STRUCTURE, ASSAULT, STRUCTURE, COMMANDER, ASSAULT, STRUCTURE, ASSAULT]


def test_missions():
    root = ET.fromstring("""<root><mission><id>3</id><name>3. Barrage</name><energy>16</energy>
        <commander>1297</commander><deck><card>1</card><card>1</card><card>2</card></deck>
        <rewards><gold>13</gold><card>12</card></rewards></mission></root>""")
    m = parse_missions(root)["3"]
    assert (m.commander, m.deck, m.energy, m.gold, m.reward_cards) == (1297, (1, 1, 2), 16, 13, (12,))


# ---- the engine -----------------------------------------------------------------------------------

def battle(att_cmd=1000, att=(1, 1, 1, 1), def_cmd=1300, dfn=(1, 1, 1)):
    """Decks in the given draw order (no shuffle), uids 1.. and 101.."""
    return Battle(Setup(DB[att_cmd], [(i + 1, DB[c]) for i, c in enumerate(att)],
                        DB[def_cmd], [(101 + i, DB[c]) for i, c in enumerate(dfn)]))


def test_hand_draws_and_the_enemys_choice():
    b = battle(att=(1, 2, 3, 1, 1), dfn=(3, 1, 1, 1))
    assert b.start() == {1: {"draws": [1, 2, 3]}}
    turns = b.play(2)                                                   # the player picks the middle card
    assert turns[1]["draws"] == [1, 2, 3] and turns[1]["plays"] == [{"card_uid": "2", "status": []}]
    assert turns[2]["draws"] == [101, 102, 103]
    assert turns[2]["plays"] == [{"card_uid": 101, "status": []}]      # the enemy: its oldest card
    assert turns[3] == {"draws": [4]}                                  # topped up to 3, then waiting
    assert b.sides[0].hand == [1, 3, 4] and b.turn == 3
    with pytest.raises(BattleError):
        b.play(2)                                                      # already played
    with pytest.raises(BattleError):
        b.play(5)                                                      # not in hand yet


def test_delay_counts_down_on_the_owners_turn():
    b = battle(att=(2, 1, 1), dfn=(3, 3, 3))
    b.start()
    turns = b.play(1)                                                  # delay 1: doesn't act yet
    assert "actions" not in turns[1]
    turns = b.play(2)                                                  # turn 3: the gunner can attack now
    gunner_attack = turns[3]["actions"][0]
    assert gunner_attack["card_uid"] == 1 and gunner_attack["targets"] == [101]
    assert gunner_attack["value"] == 3 and gunner_attack["status"] == [{"card_uid": 101, "status_flag": "h", "value": 6}]


def test_attack_across_or_the_commander_and_deaths():
    b = battle(att_cmd=1300, att=(5, 1, 1, 3, 3), def_cmd=1300, dfn=(1, 3, 3))
    b.start()
    turns = b.play(1)                                                  # the 2/2 Grunt (level 2), no delay
    hit = turns[1]["actions"][0]
    assert hit == {"card_uid": 1, "skill_id": 0, "targets": [-2], "value": 2, "status": []}   # nobody across
    assert b.sides[1].commander.hp == 38
    enemy = turns[2]["actions"][0]                                     # the enemy Grunt hits it back
    assert enemy == {"card_uid": 101, "skill_id": 0, "targets": ["1"], "value": 1, "block": [],
                     "status": [{"card_uid": "1", "status_flag": "h", "value": 1}]}
    turns = b.play(2)
    first = turns[3]["actions"][0]                    # hit in the previous reply: its health isn't repeated
    assert first["targets"] == [101] and first["status"] == [{"card_uid": 101, "status_flag": "h", "value": 0}]
    assert turns[3]["defend_kill"] == [101]
    assert [u.uid for u in b.sides[1].assaults] == [102]               # 101 removed; turn 4's card in slot 0


def test_dead_cards_leave_and_the_rest_slide_left():
    b = battle(att_cmd=1300, att=(5, 3, 1, 3, 3), def_cmd=1300, dfn=(1, 3, 3))
    b.start()
    b.play(2)                                                          # the 0-attack Tank in slot 0
    b.play(1)                                                          # turn 3: the 2/2 Grunt in slot 1
    assert [u.uid for u in b.sides[1].assaults] == [101, 102]
    b.play(3)                                                          # turn 5
    side = b.sides[1]
    assert all(u.alive for u in side.assaults)


def test_win_and_the_last_card_running_on():
    b = battle(att=(1,), def_cmd=1297, dfn=(3,))
    b.start()
    turns = b.play(1)                                                  # 1 damage to a 1-health commander
    assert b.over and b.winner == 1 and list(turns) == [1]
    b = battle(att_cmd=1297, att=(3,), def_cmd=1300, dfn=(1,))
    b.start()
    b.play(1)                                                          # a 0-attack Tank, then nothing to play:
    assert b.over and b.winner == 0 and b.turn < TURN_LIMIT            # the turns run on until the Grunt wins
    b = battle(att=(3,), def_cmd=1300, dfn=(3,))
    b.start()
    b.play(1)                                                          # nobody can do damage: a stall
    assert b.over and b.winner == 0 and b.turn == TURN_LIMIT


def test_a_commander_can_lose():
    b = battle(att=(3,), att_cmd=1297, dfn=(1, 1))
    b.start()
    turns = b.play(1)
    assert turns[2]["actions"][-1]["targets"] == ["1"]                 # the Grunt across hits the Tank
    assert b.winner == 0 and turns[max(turns)]["actions"][-1]["targets"] == [-1]   # the second one, the commander


def test_skills_not_built_yet_are_noted():
    b = battle(att=(2, 1, 1, 1), dfn=(7, 3, 3))
    b.start()
    b.play(1)
    assert b.ignored_skills == set()                     # flurry, strike on play, pierce, armored: all built
    mimic = CardDB.from_xml("<root><unit><id>9</id><name>Copycat</name><attack>1</attack><health>3</health>"
                            "<skill id=\"mimic\" x=\"1\"/></unit></root>")[9]
    b = Battle(Setup(DB[1000], [(1, mimic), (2, DB[3]), (3, DB[3])], DB[1300], [(101, DB[3])]))
    b.start()
    b.play(1)
    assert b.ignored_skills == {"mimic"}


# ---- the replies ----------------------------------------------------------------------------------

def test_battle_data():
    b = battle(att=(1, 2, 3, 1), def_cmd=1300, dfn=(3, 3, 3))
    s = BattleSession(b, 123, "1000001", "4")
    start = s.start_data(b.start())
    assert start["attack_deck"] == {"1": "1", "2": "2", "3": "3", "4": "1"}
    assert (start["attack_commander"], start["defend_commander"], start["hds"], start["eds"]) == ("1000", "1300", 4, 3)
    assert start["attack_power"] == 10 and start["turn"] == {"1": {"draws": [1, 2, 3]}}
    turns = b.play(3)
    turns = b.play(1)
    data = s.turn_data(turns)
    assert list(data["turn"]) == ["1", "2", "3", "4", "5"]
    assert data["turn"]["1"] == [] and data["turn"]["2"] == []        # sent before: only what changed
    assert data["turn"]["3"]["draws"] == [4] and data["turn"]["5"] == []   # deck empty: nothing to draw
    assert data["card_map"] == {"1": "1", "101": "3", "3": "3", "102": "3"}
    assert (data["attack_power"], data["defend_power"]) == (30, 20)
    assert "winner" not in data and data["battleground_effects"] == {"global": [], "attacker": [], "defender": []}


# ---- in the server ---------------------------------------------------------------------------------

MISSIONS = """<root>
  <mission><id>2</id><name>2. Cannon Fire</name><energy>15</energy><commander>1297</commander>
    <deck><card>3</card><card>3</card></deck><rewards><gold>12</gold></rewards></mission>
</root>"""
T = 1_795_000_000


def make_api(tmp_path, engine=True):
    fx, assets = tmp_path / "fixtures", tmp_path / "assets"
    fx.mkdir()
    assets.mkdir()
    (assets / "cards_section_1.xml").write_text(CARDS, "utf-8")
    (assets / "missions_basic.xml").write_text(MISSIONS, "utf-8")
    start = {"time": T, "max_energy": 250, "energy_recharge_time": 60, "mission_file": "missions_basic.xml",
             "mission_completions": [],
             "user_data": {"user_id": "1000001", "money": "100", "tokens": "50", "energy": "250",
                           "last_update_time": str(T), "active_deck": "1", "mission_wins": "0", "stars": "0"},
             "user_cards": {"1": {"num_owned": "3", "num_used": "0"}},
             "user_decks": {"1": {"deck_id": 1, "commander_id": "1000", "cards": {"1": "3"}}}}
    (fx / "init.json").write_text(json.dumps({"source": {}, "body": start}), "utf-8")
    (fx / "getUserAccount.json").write_text(json.dumps({"source": {}, "body": {"time": T}}), "utf-8")
    return ga.GameApi(fixture_dir=fx, asset_dir=assets, clock=lambda: T + 100, battle_engine=engine)


def ask(api, message, **params):
    return api.respond(message, {"message": message, "user_id": "1000001", **params})


def test_a_mission_from_the_engine(tmp_path):
    api = make_api(tmp_path)
    ask(api, "init")
    r, how = ask(api, "startMission", mission_id="2")
    assert how == "engine: started mission 2 (3 cards against 2); 15 energy (250 -> 235)"
    bd = r["battle_data"]
    assert r["result"] is True and r["user_data"]["energy"] == "235"
    assert bd["attack_deck"] == {"1": "1", "2": "1", "3": "1"} and bd["turn"] == {"1": {"draws": [1, 2, 3]}}
    r, how = ask(api, "playCard", battle_id=str(bd["battle_id"]), card_uid="2", skip="0")
    assert how == "engine: mission 2, turns 1-1 (you won); paid 84 gold"   # 12 x 7 (the default bonus)
    bd = r["battle_data"]
    assert bd["winner"] == 1 and bd["mission_id"] == "2" and bd["end_time"] == T + 100
    assert bd["rewards"] == [{"user_id": "1000001", "gold": 84, "star": {"1": True}}]
    assert r["mission_completions"]["2"]["complete"] == "1" and r["user_data"]["mission_wins"] == "1"
    assert r["user_data"]["money"] == "184"
    assert api.battle is None
    assert ask(api, "playCard", card_uid="1", skip="0")[1] == "UNSUPPORTED: there is no battle in progress."


def test_engine_refusals(tmp_path):
    api = make_api(tmp_path)
    ask(api, "init")
    r, how = ask(api, "startMission", mission_id="9")
    assert how == "UNSUPPORTED: mission 9 isn't in the mission data."
    r, _ = ask(api, "startMission", mission_id="2")
    bid = str(r["battle_data"]["battle_id"])
    assert "isn't in the hand" in ask(api, "playCard", battle_id=bid, card_uid="7", skip="0")[1]
    assert "(skipped to the end)" in ask(api, "playCard", battle_id=bid, card_uid="1", skip="1")[1]   # Skip on auto


def test_without_the_engine_missions_replay(tmp_path):
    api = make_api(tmp_path, engine=False)
    ask(api, "init")
    assert "hasn't been recorded" in ask(api, "startMission", mission_id="2")[1]


# ---- against the recordings -------------------------------------------------------------------------

ASSETS = ga.ASSET_DIR
REPLAY = ga.FIXTURE_DIR / "replay.json"


@pytest.mark.skipif(not (ASSETS.is_dir() and REPLAY.is_file()), reason="needs the archive and the recordings")
def test_recorded_battles_match_up_to_the_first_skill():
    db = CardDB.load(ASSETS)
    results = {}
    for i, rec in enumerate(from_fixtures(REPLAY)):
        r = compare(rec, db)
        results[f"{i} {rec.name}"] = r
        assert r.ok or r.needs_skill, f"{rec.name} differs in a plain attack: {r.difference}"
    assert results["0 mission 1"].ok and results["12 arena -1"].ok
