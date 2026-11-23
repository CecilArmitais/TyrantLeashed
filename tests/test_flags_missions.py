"""Tests for saved game flags (tutorial progress, settings) and mission progress."""

import json

import pytest

import game_api as ga
import paths
import player as cli
from helpers import T, ask, run_cli
from player_state import SAVE_VERSION

NOW = T + 100

FLAGS = {"music": "1", "sound": "1", "tutorial": "0", "autopilot": "0", "speed": "1",
         "show_filters": "0", "show_missions": "0", "hand_pos": None, "guides_complete": None}
START = {
    "time": T, "max_energy": 250, "energy_recharge_time": 60, "tutorial_file": "tutorial12.xml",
    "mission_file": "missions_basic.xml",
    "mission_completions": [],
    "user_data": {"user_id": "1000001", "money": "100", "tokens": "50", "energy": "250",
                  "last_update_time": str(T), "level": "1", "mission_wins": "0", "stars": "0", "flags": FLAGS},
    "user_cards": {"2": {"num_owned": "1", "num_used": "0"}},
}
MISSIONS_XML = """<root>
  <location><id>1</id><name>Asphodel</name></location>
  <mission><id>1</id><name>1. Spirefall</name><energy>15</energy>
    <rewards><star><id>2</id><req type='5' compare='great_equal' amount='2' /></star>
             <star><id>3</id><req type='5' compare='great_equal' amount='3' /></star></rewards></mission>
  <mission><id>3</id><name>3. Wounds</name><energy>16</energy></mission>
</root>"""                     # mission 7 is left out on purpose: a recorded mission with no known cost
TUTORIAL_XML = """<root>
  <tutorial><id>2</id><name>Start First Mission</name></tutorial>
  <tutorial><id>5</id><name>In First Mission</name></tutorial>
  <tutorial><id>10</id><name>Won First Mission</name></tutorial>
  <tutorial><id>20</id><name>Buy a card</name></tutorial>
  <tutorial><id>35</id><name>Second Mission</name></tutorial>
  <guide><id>1</id><name>upgrade</name></guide>
</root>"""


def battle(winner=None, stars=None, extra=None):
    bd = {"battle_id": "1", "turn": {"1": {}}}
    if winner is None:                         # a start: the recorded ones carry user_data, energy already spent
        extra = {"user_data": {"energy": 235, "last_update_time": str(T), "money": "100"}, **(extra or {})}
    else:
        bd.update({"winner": winner, "end_time": T + 60,
                   "rewards": [{"user_id": "1000001", "gold": 70, **({"star": stars} if stars else {})}]})
    return {"time": T, "result": True, "battle_data": bd, **(extra or {})}


RECORDED_AFTER_WIN = {   # what the recording's winning reply carried (the save decides instead)
    "mission_completions": {"9": {"number": "5", "complete": "5", "star2": "0", "star3": "0"}},
    "user_data": {"money": 170, "mission_wins": 1, "stars": 1, "flags": dict(FLAGS, tutorial="1")},
}
CALLS = [
    ("startMission", {"mission_id": "1"}, battle()),
    ("playCard", {"skip": "0", "card_uid": "1"}, battle(1, {"1": True}, RECORDED_AFTER_WIN)),
    ("startMission", {"mission_id": "7"}, battle()),
    ("playCard", {"skip": "0", "card_uid": "2"}, battle(0, None, {"user_data": {"mission_wins": "1"}})),
    ("startHuntingBattle", {"target_user_id": "-1"}, battle()),
    ("playCard", {"skip": "0", "card_uid": "3"}, battle(1, None, {"user_data": {"mission_wins": "1"}})),
    ("startMission", {"mission_id": "3"}, battle()),
    ("playCard", {"skip": "0", "card_uid": "4"}, battle(1, {"1": True, "2": True, "3": True}, RECORDED_AFTER_WIN)),
]


def make_api(tmp_path, clock=NOW):
    fx, assets = tmp_path / "fixtures", tmp_path / "assets"
    fx.mkdir(exist_ok=True)
    assets.mkdir(exist_ok=True)
    (assets / "tutorial12.xml").write_text(TUTORIAL_XML, "utf-8")
    (assets / "missions_basic.xml").write_text(MISSIONS_XML, "utf-8")
    (fx / "init.json").write_text(json.dumps({"source": {}, "body": START}), "utf-8")
    (fx / "getUserAccount.json").write_text(json.dumps({"source": {}, "body": {
        "time": T, "result": True, "tutorial_file": "tutorial12.xml", "user_data": START["user_data"]}}), "utf-8")
    calls = [{"seq": i, "message": m, "key": k, "body": b} for i, (m, k, b) in enumerate(CALLS, 1)]
    (fx / "replay.json").write_text(json.dumps({"source": {}, "calls": calls}), "utf-8")
    return ga.GameApi(fixture_dir=fx, clock=lambda: clock, asset_dir=assets)


def saved(tmp_path):
    data = json.loads((tmp_path / "state" / "player.json").read_text("utf-8"))
    assert data["version"] == SAVE_VERSION
    return data["player"]


def win(api, mission, card):
    r, _ = ask(api, "startMission", mission_id=mission)
    return ask(api, "playCard", battle_id=r["battle_data"]["battle_id"], card_uid=card, skip="0")


# ---- flags ---------------------------------------------------------------------------------------

def test_flag_is_saved_and_survives_a_restart(tmp_path):
    api = make_api(tmp_path)
    r, _ = ask(api, "init")
    assert r["user_data"]["flags"]["tutorial"] == "0"
    r, how = ask(api, "setUserFlag", flag="tutorial", value="2")
    assert r["result"] is True and r["user_data"]["flags"]["tutorial"] == "2"
    assert r["user_data"]["money"] == "100" and r["request"]["flag"] == "tutorial"
    assert how == "saved tutorial=2 (tutorial part 3 of 5)"
    ask(api, "setUserFlag", flag="speed", value="4")
    assert saved(tmp_path)["flags"]["speed"] == "4"

    restarted = make_api(tmp_path)                                     # a new server process
    for message in ("getUserAccount", "init"):
        flags = ask(restarted, message)[0]["user_data"]["flags"]
        assert (flags["tutorial"], flags["speed"], flags["music"]) == ("2", "4", "1")


def test_guides_complete_is_kept_as_a_list(tmp_path):
    api = make_api(tmp_path)
    r, _ = ask(api, "setUserFlag", flag="guides_complete", value="[102,101]")
    assert r["user_data"]["flags"]["guides_complete"] == [102, 101]
    assert b'"guides_complete":[102,101]' in ga.encode(r)            # as the recorded replies have it


def test_tutorial_past_its_last_part_is_refused_like_the_real_server(tmp_path):
    api = make_api(tmp_path)
    ask(api, "setUserFlag", flag="tutorial", value="4")
    r, how = ask(api, "setUserFlag", flag="tutorial", value="5")
    assert r["result"] is False and r["result_message"] == ["Invalid flag."]
    assert set(r) == {"time", "version", "result", "result_message", "daily_bonus_time",
                      "guild_donations_enabled", "guild_supplies_enabled"}          # the recorded shape: no echo
    assert "refused tutorial=5" in how
    assert saved(tmp_path)["flags"]["tutorial"] == "4"
    r, how = ask(api, "setUserFlag", flag="tutorial", value="-1")      # what the game sends next
    assert r["result"] is True and how == "saved tutorial=-1 (tutorial finished)"
    for bad in ("x", "", "1.5"):
        assert ask(api, "setUserFlag", flag="tutorial", value=bad)[0]["result"] is False


def test_tutorial_switched_off_by_the_game(tmp_path):
    # Seen offline: reloading mid-tutorial with mission 1 won, the game logs "disabling
    # tutorials. past first mission" and sends tutorial=-2.
    api = make_api(tmp_path)
    ask(api, "setUserFlag", flag="tutorial", value="2")
    r, how = ask(api, "setUserFlag", flag="tutorial", value="-2")
    assert r["result"] is True and how == "saved tutorial=-2 (tutorial switched off by the game)"
    assert saved(tmp_path)["flags"]["tutorial"] == "-2"


def test_unknown_flag_is_kept_and_missing_details_refused(tmp_path):
    api = make_api(tmp_path)
    r, how = ask(api, "setUserFlag", flag="new_thing", value="3")
    assert r["user_data"]["flags"]["new_thing"] == "3" and "didn't have" in how
    assert ask(api, "setUserFlag", flag="speed")[0]["result_message"] == ["Invalid flag."]
    assert ask(api, "setUserFlag", value="1")[0]["result"] is False


def test_replayed_replies_show_the_saved_flags(tmp_path):
    api = make_api(tmp_path)
    r, _ = win(api, "1", "1")
    assert r["user_data"]["flags"]["tutorial"] == "0"                  # recorded "1", but nothing set it here


def test_old_save_gets_flags_and_missions_from_the_starting_data(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "player.json").write_text(json.dumps({"version": 2, "player": {
        "gold": 5, "war_bonds": 6, "energy": 250, "energy_updated": NOW, "max_energy": 250, "cards": {}}}), "utf-8")
    p = make_api(tmp_path).player()
    assert p.flags == FLAGS and p.missions == {} and p.mission_wins == 0 and p.gold == 5
    assert saved(tmp_path)["flags"]["tutorial"] == "0"


# ---- missions ------------------------------------------------------------------------------------

def test_a_won_mission_is_saved_and_shown(tmp_path):
    api = make_api(tmp_path)
    assert ask(api, "init")[0]["mission_completions"] == []           # PHP's empty array
    r, how = win(api, "1", "1")
    assert how == "replayed mission 1, play 1 of 1 (battle over); mission 1 won, paid 0 gold"   # no <gold>
    assert r["mission_completions"] == {"1": {"number": "1", "complete": "1", "star2": "0", "star3": "0"}}
    assert (r["user_data"]["mission_wins"], r["user_data"]["stars"]) == (1, 1)   # recorded as numbers here

    r, _ = ask(make_api(tmp_path), "init")                              # after a restart
    assert r["mission_completions"]["1"]["complete"] == "1"
    assert (r["user_data"]["mission_wins"], r["user_data"]["stars"]) == ("1", "1")


def test_losses_and_arena_wins_dont_count(tmp_path):
    api = make_api(tmp_path)
    r, how = win(api, "7", "2")
    assert how.endswith("(battle over)") and r["user_data"]["mission_wins"] == "0"
    r, _ = ask(api, "startHuntingBattle", target_user_id="-1")
    r, how = ask(api, "playCard", battle_id=r["battle_data"]["battle_id"], card_uid="3", skip="0")
    assert how.endswith("(battle over)")
    assert saved(tmp_path)["missions"] == {} and saved(tmp_path)["mission_wins"] == 0


def test_repeat_wins_and_extra_stars(tmp_path):
    api = make_api(tmp_path)
    win(api, "1", "1")
    ask(api, "getUserAccount")                                         # a new login replays from the top
    r, how = win(api, "1", "1")
    assert how.endswith("mission 1 won, paid 100 gold, star 2")       # star 2 at 2 wins in this test's data
    assert r["battle_data"]["rewards"] == [{"user_id": "1000001", "gold": 100, "star": {"1": True, "2": True}}]
    r, _ = win(api, "3", "4")
    assert r["mission_completions"]["1"] == {"number": "2", "complete": "2", "star2": "1", "star3": "0"}
    # The recording gave mission 3 all three stars; the rules (no star conditions here) decide instead.
    assert r["mission_completions"]["3"] == {"number": "1", "complete": "1", "star2": "0", "star3": "0"}
    assert (r["user_data"]["mission_wins"], r["user_data"]["stars"]) == (3, 3)


# ---- mission energy costs ------------------------------------------------------------------------

def set_energy(api, energy, updated=NOW):
    p = api.player()
    p.energy, p.energy_updated = energy, updated
    api.player_store.save(p)


def test_starting_a_mission_costs_its_energy(tmp_path):
    api = make_api(tmp_path)
    assert api.mission_costs == {"1": 15, "3": 16}
    r, how = ask(api, "startMission", mission_id="1")
    assert how == "replayed start of mission 1 (recording 1 of 1); 15 energy (250 -> 235)"
    assert r["user_data"]["energy"] == 235                          # a number, as recorded in start replies
    assert r["user_data"]["last_update_time"] == str(NOW)             # from full: the refill clock starts now
    assert saved(tmp_path)["energy"] == 235


def test_starting_below_the_max_keeps_the_part_way_progress(tmp_path):
    api = make_api(tmp_path)
    set_energy(api, 100, NOW - 30)                                     # 30 s towards the next point
    r, _ = ask(api, "startMission", mission_id="3")
    assert (r["user_data"]["energy"], r["user_data"]["last_update_time"]) == (84, str(NOW - 30))


def test_energy_that_came_back_counts_before_the_cost(tmp_path):
    api = make_api(tmp_path)
    set_energy(api, 10, NOW - 5 * 60)                                  # 5 minutes ago: 15 by now
    r, how = ask(api, "startMission", mission_id="1")
    assert r["result"] is True and how.endswith("; 15 energy (15 -> 0)")


def test_not_enough_energy_is_refused_with_the_real_numbers(tmp_path):
    api = make_api(tmp_path)
    set_energy(api, 10)
    r, how = ask(api, "startMission", mission_id="1")
    assert r["result"] is False and how == "refused mission 1: needs 15 energy, has 10"
    assert r["result_message"] == ["Not enough energy: this mission needs 15, and you have 10."]
    assert r["user_data"]["energy"] == "10"                           # the game adopts this copy
    assert "battle_data" not in r and "request" not in r and api.battle is None
    assert saved(tmp_path)["energy"] == 10
    set_energy(api, 15)                                                # exactly enough
    r, how = ask(api, "startMission", mission_id="1")
    assert r["result"] is True and "(recording 1 of 1)" in how and "(15 -> 0)" in how


def test_what_isnt_charged(tmp_path):
    api = make_api(tmp_path)
    ask(api, "init")
    r, how = ask(api, "startMission", mission_id="9")                 # not recorded: refused, free
    assert r["result"] is False and saved(tmp_path)["energy"] == 250
    r, how = ask(api, "startMission", mission_id="7")                 # recorded, but no cost in the data
    assert how.endswith("; no energy cost in the mission data") and r["user_data"]["energy"] == 250
    r, how = ask(api, "startHuntingBattle", target_user_id="-1")      # arena: not mission energy
    assert r["result"] is True and saved(tmp_path)["energy"] == 250


# ---- buying an energy refill ---------------------------------------------------------------------

def test_refill_adds_max_energy_on_top_for_war_bonds(tmp_path):
    api = make_api(tmp_path)
    set_energy(api, 5, NOW - 40)
    r, how = ask(api, "buyEnergyRefillTokens")
    assert r["result"] is True and how == "energy refill: +250 (5 -> 255), 30 war bonds (50 -> 20)"
    ud = r["user_data"]                                                # the game shows exactly this
    assert (ud["energy"], ud["tokens"], ud["money"]) == ("255", "20", "100")
    assert ud["last_update_time"] == str(NOW)                         # over the max: clock waits from now
    assert r["request"]["message"] == "buyEnergyRefillTokens"
    p = saved(tmp_path)
    assert (p["energy"], p["war_bonds"]) == (255, 20)


def test_refill_over_the_cap_and_with_a_bigger_max(tmp_path):
    api = make_api(tmp_path)
    p = api.player()
    p.energy, p.max_energy, p.war_bonds = 291, 300, 100
    api.player_store.save(p)
    r, how = ask(api, "buyEnergyRefillTokens")
    assert r["user_data"]["energy"] == "591" and "+300 (291 -> 591)" in how


def test_regenerated_energy_counts_before_the_refill(tmp_path):
    api = make_api(tmp_path)
    set_energy(api, 10, NOW - 5 * 60)
    _, how = ask(api, "buyEnergyRefillTokens")
    assert "(15 -> 265)" in how


def test_refill_without_enough_war_bonds_is_refused(tmp_path):
    api = make_api(tmp_path)
    p = api.player()
    p.energy, p.war_bonds = 5, 20
    api.player_store.save(p)
    r, how = ask(api, "buyEnergyRefillTokens")
    assert r["result"] is False and how == "refused: a refill costs 30 war bonds, has 20"
    assert r["result_message"] == ["Not enough war bonds: a refill costs 30, and you have 20."]
    assert (r["user_data"]["energy"], r["user_data"]["tokens"]) == ("5", "20")   # the game resyncs to this
    assert (saved(tmp_path)["energy"], saved(tmp_path)["war_bonds"]) == (5, 20)


def test_refill_cost_comes_from_the_login_data(tmp_path):
    api = make_api(tmp_path)
    api._start["wb_energy_cost"] = "45"
    _, how = ask(api, "buyEnergyRefillTokens")
    assert how.endswith("45 war bonds (50 -> 5)")


# ---- the edit command ----------------------------------------------------------------------------

def made_up_cli(tmp_path, monkeypatch, capsys, *args):
    """player.py on the made-up game (make_api): what it printed."""
    make_api(tmp_path)
    monkeypatch.setattr(paths, "ASSET_DIR", tmp_path / "assets")
    return run_cli(monkeypatch, capsys, tmp_path / "fixtures", tmp_path / "state", *args, clock=NOW,
                   cards_dir=tmp_path / "assets")[1]


def test_cli_tutorial_skip(tmp_path, monkeypatch, capsys):
    out = made_up_cli(tmp_path, monkeypatch, capsys)
    assert "tutorial   part 1 of 5: Start First Mission" in out
    assert "missions   none won yet (0 wins, 0 stars)" in out
    assert "Tutorial marked finished" in made_up_cli(tmp_path, monkeypatch, capsys, "tutorial", "skip")
    p = saved(tmp_path)
    assert p["flags"]["tutorial"] == "-1" and set(p["missions"]) == {"1"} and p["mission_wins"] == 1
    out = made_up_cli(tmp_path, monkeypatch, capsys)
    assert "tutorial   finished" in out and "missions   1 won (1 wins, 1 stars)" in out
    assert "already finished" in made_up_cli(tmp_path, monkeypatch, capsys, "tutorial", "skip")
    r, _ = ask(make_api(tmp_path), "init")
    assert r["user_data"]["flags"]["tutorial"] == "-1" and "1" in r["mission_completions"]


def test_cli_music_and_sound(tmp_path, monkeypatch, capsys):
    assert "settings   music on, sound on, battle speed 1x, autopilot off" in made_up_cli(tmp_path, monkeypatch, capsys)
    assert "Music off." in made_up_cli(tmp_path, monkeypatch, capsys, "music", "off")
    made_up_cli(tmp_path, monkeypatch, capsys, "sound", "off")
    assert (saved(tmp_path)["flags"]["music"], saved(tmp_path)["flags"]["sound"]) == ("0", "0")
    assert "settings   music off, sound off" in made_up_cli(tmp_path, monkeypatch, capsys)
    r, _ = ask(make_api(tmp_path), "init")
    assert (r["user_data"]["flags"]["music"], r["user_data"]["flags"]["sound"]) == ("0", "0")
    made_up_cli(tmp_path, monkeypatch, capsys, "music", "on")
    assert saved(tmp_path)["flags"]["music"] == "1"


def test_game_toggles_music_like_the_real_server(tmp_path):
    # The real server's reply to the game's own toggle (recorded): result true, and the
    # flag back in user_data.flags as a string.
    api = make_api(tmp_path)
    r, how = ask(api, "setUserFlag", flag="music", value="0")
    assert r["result"] is True and r["user_data"]["flags"]["music"] == "0" and how == "saved music=0"
    assert set(r) == {"time", "version", "result", "user_data", "daily_bonus_time",
                      "guild_donations_enabled", "guild_supplies_enabled", "request"}


@pytest.mark.parametrize("flag, expected", [("-1", "finished"), ("2", "part 3 of 5: Won First Mission"),
                                            ("-2", "switched off (-2; the game does this when it loads "
                                                   "with mission 1 already won)"),
                                            ("9", "part 10 of 5"), (None, "unknown (None)")])
def test_tutorial_status(flag, expected):
    names = ["Start First Mission", "In First Mission", "Won First Mission", "Buy a card", "Second Mission"]
    assert cli.tutorial_status(flag, names) == expected


@pytest.mark.needs_game
def test_the_full_campaign_after_mission_56(tmp_path):
    """missions_basic.xml (locations 0-4, missions 1-56) until mission 56 is won, then
    missions.xml: its mission 57 is at location 5, with the side and mutant missions."""
    api = ga.GameApi(fixture_dir=paths.FIXTURE_DIR, state_dir=tmp_path, battle_engine=True)
    r, _ = ask(api, "init")
    assert r["mission_file"] == "missions_basic.xml" and "57" not in api._mission_data()
    p = api.player()
    p.won_mission("56")
    api.player_store.save(p)
    r, _ = ask(api, "init")
    assert r["mission_file"] == "missions.xml"
    missions = api._mission_data()
    assert missions["57"].location == 5 and missions["1516"].mutant and api._mission_costs()["1516"] == 50
