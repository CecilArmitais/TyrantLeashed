"""Tests for mission rewards and saved decks: the reward rules,
the check against every recorded mission ending, the final playCard reply, and
setDeckCards."""

import json
import xml.etree.ElementTree as ET

import pytest

import game_api as ga
import paths
from battle.missions import Mission, load_missions, parse_missions
from battle.recorded import capture_calls
from helpers import T, ask, mission_api, play_mission
from player_state import Player, SAVE_VERSION, completions_json, deck_from_json, decks_json, fill_from_start
from rewards import DECK_SIZE, RewardRules, finish_mission

NOW = 1_795_000_000
RULES = RewardRules(gold_multiplier=7)
M = Mission(id="5", name="5. Test", commander=1297, deck=(3,), gold=14, reward_cards=(320,),
            star_wins=(("2", 5), ("3", 10)), location=0)


def player(**kw) -> Player:
    p = Player(gold=100, war_bonds=50, energy=250, energy_updated=NOW, cards={}, flags={}, missions={},
               decks={"1": {"name": None, "commander_id": 1000, "cards": {"2": 1}, "dominion_id": 0}},
               gold_earned=0, cards_earned=5)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


# ---- the rules -----------------------------------------------------------------------------------

def test_rules_from_the_login_data():
    start = json.loads((paths.FIXTURE_DIR / "init.json").read_text("utf-8"))["body"]
    r = RewardRules.from_start(start)
    assert (r.loss_mult, r.mastered_mult) == (0.25, 0.5)
    assert r.star_gold == (("2", 100), ("3", 200))
    assert r.gold_multiplier == 1                  # not from the login data: the server's setting decides it
    assert RewardRules.from_start({}) == RewardRules()


def test_first_win_pays_gold_card_and_star_1():
    p = player()
    res = finish_mission(p, M, True, RULES, NOW, 1000001, "1")
    assert res.reward == {"user_id": "1000001", "gold": 98, "card": ["320"], "star": {"1": True}}
    assert (p.gold, p.gold_earned, p.cards_earned) == (198, 98, 6)
    assert p.cards["320"] == {"num_owned": 1, "num_used": 0}
    assert p.decks["1"]["cards"] == {"2": 1, "320": 1} and res.deck_changed and res.first_win
    assert p.missions["5"] == {"number": 1, "complete": 1, "star2": 0, "star3": 0} and p.mission_wins == 1


def test_repeat_wins_pay_full_gold_but_no_card():
    p = player()
    finish_mission(p, M, True, RULES, NOW, 1000001, "1")
    res = finish_mission(p, M, True, RULES, NOW, 1000001, "1")
    assert res.reward == {"user_id": "1000001", "gold": 98, "star": {"1": True}}
    assert not res.first_win and not res.deck_changed and p.cards["320"]["num_owned"] == 1
    assert p.missions["5"]["number"] == 2 and p.gold == 100 + 98 * 2


def test_a_loss_pays_a_quarter_and_counts_nothing():
    p = player()
    res = finish_mission(p, M, False, RULES, NOW, 1000001, "1")
    assert res.reward == {"user_id": "1000001", "gold": 25}         # 98 * 0.25 = 24.5, to the nearest
    assert p.missions == {} and p.mission_wins == 0 and p.cards == {} and p.gold == 125


def test_stars_2_and_3_at_5_and_10_wins_with_their_gold():
    p = player(missions={"5": {"number": 4, "complete": 4, "star2": 0, "star3": 0}})
    res = finish_mission(p, M, True, RULES, NOW, 1000001, "1")
    assert res.reward == {"user_id": "1000001", "gold": 98 + 100, "star": {"1": True, "2": True}}
    assert res.new_stars == ["2"] and p.missions["5"]["star2"] == 1 and p.stars == 2
    res = finish_mission(p, M, True, RULES, NOW, 1000001, "1")       # the 6th: star 2 again, no more gold
    assert res.reward == {"user_id": "1000001", "gold": 98, "star": {"1": True, "2": True}}
    p.missions["5"]["number"] = p.missions["5"]["complete"] = 9
    res = finish_mission(p, M, True, RULES, NOW, 1000001, "1")
    assert res.reward == {"user_id": "1000001", "gold": 98 + 200, "star": {"1": True, "2": True, "3": True}}
    assert p.missions["5"] == {"number": 10, "complete": 10, "star2": 1, "star3": 1} and p.stars == 3


def test_a_mastered_mission_pays_half():
    p = player(missions={"5": {"number": 10, "complete": 10, "star2": 1, "star3": 1}})
    assert finish_mission(p, M, True, RULES, NOW, 1000001, "1").reward["gold"] == 49
    assert finish_mission(p, M, False, RULES, NOW, 1000001, "1").reward["gold"] == 12     # 98 * .5 * .25


def test_without_the_bonus_a_mission_pays_its_listed_gold():
    assert finish_mission(player(), M, True, RewardRules(), NOW, 1000001, "1").reward["gold"] == 14


def test_only_a_prologue_card_goes_into_the_deck_and_only_with_room():
    later = Mission(id="9", name="9. Nest", commander=1297, deck=(3,), gold=19, reward_cards=(42,), location=1)
    p = player()
    res = finish_mission(p, later, True, RULES, NOW, 1000001, "1")
    assert res.cards == ["42"] and not res.deck_changed and p.decks["1"]["cards"] == {"2": 1}
    full = {str(i): 1 for i in range(1, DECK_SIZE + 1)}
    p = player(decks={"1": {"name": None, "commander_id": 1000, "cards": dict(full), "dominion_id": 0}})
    res = finish_mission(p, M, True, RULES, NOW, 1000001, "1")
    assert res.cards == ["320"] and not res.deck_changed and p.decks["1"]["cards"] == full
    assert p.cards["320"]["num_owned"] == 1


@pytest.mark.needs_game
def test_star_conditions_come_from_the_mission_file():
    xml = """<root><mission><id>1</id><name>1.</name><commander>1297</commander><rewards><gold>10</gold>
      <card>6</card><item>1051</item>
      <star><id>2</id><req type='5' compare='great_equal' amount='5' /></star>
      <star><id>3</id><req type='5' compare='great_equal' amount='10' /></star>
      <star><id>4</id><req type='9' compare='great_equal' amount='1' /></star></rewards></mission></root>"""
    m = parse_missions(ET.fromstring(xml))["1"]
    assert (m.gold, m.reward_cards, m.star_wins) == (10, (6,), (("2", 5), ("3", 10)))   # unknown kinds left out
    assert m.location == -1                                            # no <location_id>
    real = load_missions(paths.ASSET_DIR, "missions_basic.xml")
    assert len(real) == 56 and {m.star_wins for m in real.values()} == {(("2", 5), ("3", 10))}
    assert [mid for mid, m in real.items() if m.location == 0] == ["1", "2", "3", "4", "5"]


# ---- against the recordings ----------------------------------------------------------------------

def check_recorded_endings(calls, start) -> int:
    """Runs a recorded session's mission endings through the rules, from the state the
    recording shows just before each one, and compares everything they paid. The
    number of endings checked."""
    pl = Player(gold=0, war_bonds=0, energy=0, energy_updated=NOW, cards={}, flags={}, missions=None)
    fill_from_start(pl, start)
    rules = RewardRules(**{**RewardRules.from_start(start).__dict__, "gold_multiplier": 7})   # the recordings' x7
    missions = load_missions(paths.ASSET_DIR, start.get("mission_file") or "missions_basic.xml")
    deck_id = str(start["user_data"]["active_deck"])
    mission, n = None, 0
    for message, params, body in calls:
        if not isinstance(body, dict):
            continue
        bd = body.get("battle_data") if isinstance(body.get("battle_data"), dict) else {}
        if message in ("startMission", "startHuntingBattle"):
            mission = params.get("mission_id") if message == "startMission" else None
        ended = message == "playCard" and bd.get("winner") is not None
        ud = body.get("user_data") if isinstance(body.get("user_data"), dict) else {}
        if not ended:          # keep up with what the recording did in between (buying, deck edits)
            if "money" in ud:
                pl.gold = int(ud["money"])
            if "cards_earned" in ud:
                pl.cards_earned = int(ud["cards_earned"])
            if isinstance(body.get("user_decks"), dict):
                pl.decks = {str(k): deck_from_json(v) for k, v in body["user_decks"].items()}
            if isinstance(body.get("user_cards"), dict):
                pl.cards = {k: {"num_owned": int(v["num_owned"]), "num_used": int(v["num_used"])}
                            for k, v in body["user_cards"].items()}
            if isinstance(body.get("mission_completions"), dict):
                pl.missions = {k: {f: int(v[f]) for f in ("number", "complete", "star2", "star3")}
                               for k, v in body["mission_completions"].items()}
            continue
        if mission is None:
            continue                                                  # arena: not paid yet
        before = pl.gold
        won = int(bd["winner"]) == 1
        res = finish_mission(pl, missions[str(mission)], won, rules, int(body["time"]), 0, deck_id)
        # Only small extracted values go into the asserts below: on a failure pytest prints
        # whatever an assert line mentions, and a captured reply carries account ids.
        recorded = {k: v for k, v in bd["rewards"][0].items() if k != "user_id"}
        paid = {k: v for k, v in res.reward.items() if k != "user_id"}
        assert paid == recorded, f"mission {mission}"
        achievements = sum(int((a.get("reward") or {}).get("gold", 0)) for a in body.get("completed_achievements") or [])
        money, cards_earned = int(ud["money"]), int(ud["cards_earned"])
        assert money == before + res.gold + achievements, f"mission {mission}"
        assert cards_earned == pl.cards_earned, f"mission {mission}"
        has_cards, has_decks, has_completions = ("user_cards" in body), ("user_decks" in body), \
            ("mission_completions" in body)
        assert (has_cards, has_decks, has_completions) == (bool(res.cards), res.deck_changed, won), f"mission {mission}"
        if res.deck_changed:
            recorded_decks = body["user_decks"]
            assert decks_json(pl.decks) == recorded_decks, f"mission {mission}"
        if res.cards:
            owned = {c: body["user_cards"][c]["num_owned"] for c in res.cards}
            assert owned == {c: str(pl.cards[c]["num_owned"]) for c in res.cards}, f"mission {mission}"
        if won:
            recorded_completions = body["mission_completions"]
            assert completions_json(pl.missions) == recorded_completions, f"mission {mission}"
        n += 1
    return n


def test_every_recorded_mission_ending_pays_the_same():
    calls = json.loads((paths.FIXTURE_DIR / "replay.json").read_text("utf-8"))["calls"]
    start = json.loads((paths.FIXTURE_DIR / "init.json").read_text("utf-8"))["body"]
    # missions 1-7 won (5, 6 and 7 twice), and mission 7 lost twice
    assert check_recorded_endings([(c["message"], c["key"], c["body"]) for c in calls], start) == 12


@pytest.mark.skipif(not paths.RAW_CAPTURES_DIR.is_dir(), reason="no captures here")
def test_the_accounts_captured_mission_endings_pay_the_same():
    """The account's own sessions (kept private; nothing from them is printed). Each one
    starts from that session's login."""
    n = 0
    for session in sorted(paths.RAW_CAPTURES_DIR.iterdir()):
        calls = capture_calls(session)
        start = next((b for m, _, b in calls if m == "init" and isinstance(b, dict)), None)
        if start is not None:
            n += check_recorded_endings(calls, start)
    assert n >= 3                     # mission 8 won twice, mission 9's first win (card 42)


# ---- the server ----------------------------------------------------------------------------------


def test_the_final_reply_of_a_first_win(tmp_path):
    api = mission_api(tmp_path)
    ask(api, "init")
    r, how = play_mission(api, "2")
    assert how.endswith("(you won); paid 84 gold, card 2 (into the deck)")
    assert r["battle_data"]["rewards"] == [{"user_id": "1000001", "gold": 84, "card": ["2"], "star": {"1": True}}]
    keys = [k for k in r if k in ("user_data", "user_cards", "user_decks", "mission_completions", "current_campaigns")]
    assert keys == ["user_data", "user_cards", "user_decks", "mission_completions", "current_campaigns"]
    ud = r["user_data"]
    assert (ud["money"], ud["gold_earned"], ud["cards_earned"], ud["mission_wins"]) == ("184", "84", "6", "1")
    assert r["user_cards"]["2"] == {"num_owned": "1", "num_used": "0"}
    assert r["user_decks"]["1"]["cards"] == {"1": "3", "2": "1"}
    assert r["mission_completions"] == {"2": {"number": "1", "complete": "1", "star2": "0", "star3": "0"}}
    # The next battle uses the deck with the new card in it.
    r, _ = ask(api, "startMission", mission_id="2")
    assert len(r["battle_data"]["attack_deck"]) == 4


def test_the_final_reply_of_a_repeat_win_and_of_a_loss(tmp_path):
    api = mission_api(tmp_path)
    ask(api, "init")
    play_mission(api, "2")
    r, how = play_mission(api, "2")
    assert how.endswith("(you won); paid 84 gold")
    assert [k for k in ("user_data", "user_cards", "user_decks", "mission_completions", "current_campaigns")
            if k in r] == ["user_data", "mission_completions", "current_campaigns"]
    r, how = play_mission(api, "3")                         # 50 turns can't get through 999 health: a loss
    assert how.endswith("(you lost); paid 28 gold")
    assert r["battle_data"]["rewards"] == [{"user_id": "1000001", "gold": 28}]
    assert [k for k in ("user_data", "user_cards", "user_decks", "mission_completions", "current_campaigns")
            if k in r] == ["user_data", "current_campaigns"]
    assert r["user_data"]["money"] == str(100 + 84 + 84 + 28)


@pytest.mark.needs_game
def test_a_replayed_mission_pays_by_the_save_not_the_recording(tmp_path):
    api = ga.GameApi(state_dir=tmp_path)                                # the real fixtures, replaying
    api.respond("init", {"message": "init"})
    r, _ = api.respond("startMission", {"message": "startMission", "mission_id": "1"})
    bid = r["battle_data"]["battle_id"]
    for uid in ("1", "2", "3", "4", "5", "6"):                          # mission 1's recorded plays
        if api.battle is None:
            break
        want = dict(api.battle["script"]["plays"][api.battle["step"]][0][1])
        r, how = api.respond("playCard", {"message": "playCard", "battle_id": bid, **want})
    assert how.endswith("mission 1 won, paid 70 gold, card 6 (into the deck)")
    assert r["battle_data"]["rewards"][0] == {"user_id": "1000001", "gold": 70, "card": ["6"], "star": {"1": True}}
    assert r["user_data"]["money"] == 170 and "user_cards" in r and "user_decks" in r


# ---- saved decks ---------------------------------------------------------------------------------

def set_deck(api, cards, commander="1000", deck_id="1"):
    return ask(api, "setDeckCards", deck_id=deck_id, commander_id=commander, dominion_id="0",
                  cards=json.dumps({k: str(v) for k, v in cards.items()}), activeYN="1")


def test_set_deck_cards_saves_the_deck(tmp_path):
    api = mission_api(tmp_path)
    ask(api, "init")
    r, how = set_deck(api, {"1": 2})
    assert how == "saved deck 1: commander 1000, 2 cards"
    assert r["result"] is True and r["user_decks"]["1"]["cards"] == {"1": "2"}
    assert ga.GameApi(fixture_dir=tmp_path / "fixtures", asset_dir=tmp_path / "assets", clock=lambda: T,
                      battle_engine=True).respond("init", {"message": "init"})[0]["user_decks"]["1"]["cards"] == \
        {"1": "2"}                                                      # after a restart
    r, _ = ask(api, "startMission", mission_id="2")
    assert len(r["battle_data"]["attack_deck"]) == 2


@pytest.mark.parametrize("cards, commander, deck_id, why", [
    ({"1": 4}, "1000", "1", "you don't own enough of card 1"),
    ({"2": 1}, "1000", "1", "you don't own enough of card 2"),
    ({"1": 1}, "1297", "1", "you don't own enough of card 1297"),
    ({"1": 3}, "1000", "2", "deck 2 isn't one of your decks"),
    ({str(i): 1 for i in range(1, DECK_SIZE + 2)}, "1000", "1", f"a deck holds at most {DECK_SIZE} cards"),
])
def test_set_deck_cards_refusals(tmp_path, cards, commander, deck_id, why):
    api = mission_api(tmp_path)
    ask(api, "init")
    r, how = set_deck(api, cards, commander, deck_id)
    assert r["result"] is False and r["result_message"] == [f"Can't save that deck: {why}."]
    assert how == f"refused setDeckCards: {why}"
    assert api.player().decks["1"]["cards"] == {"1": 3}


def test_an_older_save_gets_decks_and_earned_counts(tmp_path):
    api = mission_api(tmp_path)
    ask(api, "init")
    f = tmp_path / "state" / "player.json"
    data = json.loads(f.read_text("utf-8"))
    for k in ("decks", "gold_earned", "cards_earned"):
        del data["player"][k]
    data["version"] = 3
    f.write_text(json.dumps(data), "utf-8")
    api2 = ga.GameApi(fixture_dir=tmp_path / "fixtures", asset_dir=tmp_path / "assets", clock=lambda: T + 100)
    p = api2.player()
    assert p.decks == {"1": {"name": None, "commander_id": 1000, "cards": {"1": 3}, "dominion_id": 0}}
    assert (p.gold_earned, p.cards_earned) == (0, 5)
    assert json.loads(f.read_text("utf-8"))["version"] == SAVE_VERSION
