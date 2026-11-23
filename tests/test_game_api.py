"""Tests for the game API: replay with time shifting, the replay library's battles and
matched requests. The fixture builder's are in test_make_fixtures.py."""

import json
from pathlib import Path

import pytest

import game_api as ga
from helpers import T, ask

NOW = T + 1_000_000        # about 11.6 days later


# ---- replay helpers ------------------------------------------------------------------------------

def test_shift_times_moves_live_times_only():
    body = {
        "time": T,
        "daily_bonus_time": str(T + 3600),
        "user_data": {"last_update_time": T - 60, "user_id": "1400000042", "install_date": str(T - 400 * 86400),
                      "level": 5, "money": 1_500_000_000},
        "card_files": {"1": {"time": T - 100_000, "file": "cards_section_1.xml"}},
        "store_promos": [{"start_time": T - 30_000, "end_time": T + 500_000}],
        "flags": True,
        "battle_id": "1795179048886",
    }
    out = ga.shift_times(body, T, 1000)
    assert out["time"] == T + 1000
    assert out["daily_bonus_time"] == str(T + 4600)                      # strings stay strings
    assert out["user_data"]["last_update_time"] == T + 940
    assert out["store_promos"][0] == {"start_time": T - 29_000, "end_time": T + 501_000}
    assert out["user_data"]["user_id"] == "1400000042"                   # years away: an id, not a time  
    assert out["user_data"]["install_date"] == str(T - 400 * 86400)      # more than a year away
    assert out["card_files"]["1"]["time"] == T - 100_000                 # file version stamp, left alone
    assert out["battle_id"] == "1795179048886"                           # milliseconds id, not a time
    assert out["flags"] is True and out["user_data"]["level"] == 5
    assert body["time"] == T                                             # input not modified


def test_encode_matches_the_real_servers_json_style():
    raw = ga.encode({"time": 1, "link": "https://www.kongregate.com/forums/1", "name": "Ünit", "list": [1, 2]})
    assert raw == b'{"time":1,"link":"https:\\/\\/www.kongregate.com\\/forums\\/1","name":"\\u00dcnit","list":[1,2]}'
    assert json.loads(raw) == {"time": 1, "link": "https://www.kongregate.com/forums/1", "name": "Ünit", "list": [1, 2]}


def test_offline_credentials_have_the_real_lengths():
    assert (len(ga.OFFLINE_PASSWORD), len(ga.OFFLINE_SYNCODE)) == (32, 64)


# ---- GameApi -------------------------------------------------------------------------------------

def write_fixtures(d: Path, **bodies):
    d.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (d / f"{name}.json").write_text(json.dumps({"source": {}, "body": body}), "utf-8")
    return d


@pytest.fixture
def api(tmp_path):
    d = write_fixtures(tmp_path / "fx",
                       getUserAccount={"time": T, "version": "80.560", "daily_bonus_time": str(T + 7200),
                                       "result": True, "new_user": ga.OFFLINE_USER_ID, "request": {"stale": 1}},
                       init={"time": T, "version": "80.560", "daily_bonus_time": str(T + 7200),
                             "guild_donations_enabled": 1, "guild_supplies_enabled": 1,
                             "user_data": {"last_update_time": str(T - 5), "user_id": "1000001"},
                             "some_event": {"start_time": str(T - 3600), "end_time": str(T + 86400)}})
    return ga.GameApi(fixture_dir=d, clock=lambda: NOW)


def test_replay_shifts_to_now_and_echoes_request(api):
    r = api.handle("init", {"message": "init", "user_id": "1000001", "password": "x"})
    assert r["time"] == NOW
    assert r["daily_bonus_time"] == str(NOW + 7200)
    assert r["user_data"]["last_update_time"] == str(NOW)      # from the player's save, which starts now
    assert r["some_event"] == {"start_time": str(NOW - 3600), "end_time": str(NOW + 86400)}
    assert r["request"]["message"] == "init" and r["request"]["password"] == "x"
    assert len(r["request"]["__request_fingerprint"]) == 40


def test_replay_returns_a_fresh_copy_each_time(api):
    a = api.handle("getUserAccount", {"message": "getUserAccount"})
    a["new_user"] = "changed"
    b = api.handle("getUserAccount", {"message": "getUserAccount"})
    assert b["new_user"] == ga.OFFLINE_USER_ID and "stale" not in b["request"]


def test_unknown_message_is_unsupported_with_the_real_error_shape(api):
    assert api.handle("startMission", {"message": "startMission"}) is None
    r = api.unsupported("startMission", {"message": "startMission", "mission_id": "1"})
    assert r["result"] is False
    assert r["result_message"] == ["Offline server: 'startMission' is not supported yet."]
    assert (r["time"], r["daily_bonus_time"], r["version"]) == (NOW, str(NOW + 7200), "80.560")
    assert r["request"]["mission_id"] == "1"


def test_missing_fixtures_reported(tmp_path):
    api = ga.GameApi(fixture_dir=write_fixtures(tmp_path / "fx", init={"time": T}))
    assert api.missing_fixtures == ["getUserAccount"]
    assert api.handle("getUserAccount", {}) is None
    assert api.unsupported("x", {})["daily_bonus_time"]          # still a valid envelope


# ---- replay library: battles and matched requests ------------------------------------------------

def battle(bid, turn, winner=None, extra=None):
    bd = {"battle_id": bid, "host_id": ga.OFFLINE_USER_ID, "turn": {str(turn): {"draws": [1, 2]}}}
    if winner is not None:
        bd.update({"winner": winner, "end_time": T + 60})
    return {"time": T, "result": True, "battle_data": bd, **(extra or {})}


def library(*calls):
    return [{"seq": i, "message": m, "key": k, "body": b} for i, (m, k, b) in enumerate(calls, 10)]


@pytest.fixture
def game(tmp_path):
    d = write_fixtures(tmp_path / "fx",
                       getUserAccount={"time": T, "result": True},
                       init={"time": T, "version": "80.560", "daily_bonus_time": str(T + 7200)})
    calls = library(
        ("startMission", {"mission_id": "1"}, battle("111", 1)),
        ("buyStoreItem", {"pack_id": "1"}, {"time": T, "result": True, "n": 1}),
        ("playCard", {"skip": "0", "card_uid": "1"}, battle("111", 1, winner=1, extra={"user_data": {"xp": "10"}})),
        ("startMission", {"mission_id": "2"}, battle("222", 1)),
        ("playCard", {"skip": "0", "card_uid": "2"}, battle("222", 3)),
        ("playCard", {"skip": "0", "card_uid": "3"}, battle("222", 5, winner=1)),
        ("buyStoreItem", {"pack_id": "1"}, {"time": T, "result": True, "n": 2}),
        ("startMission", {"mission_id": "2"}, battle("333", 1)),
        ("playCard", {"skip": "0", "card_uid": "5"}, battle("333", 3, winner=1)),
        ("startHuntingBattle", {"target_user_id": "-1"}, battle("444", 1)),
        ("playCard", {"skip": "0", "card_uid": "1"}, battle("444", 1, winner=1)),
    )
    (d / "replay.json").write_text(json.dumps({"source": {}, "calls": calls}), "utf-8")
    return ga.GameApi(fixture_dir=d, clock=lambda: NOW)


def test_battle_replays_as_a_script(game):
    r, how = ask(game, "startMission", mission_id="1")
    bid = r["battle_data"]["battle_id"]
    assert bid == str(NOW * 1000) and how == "replayed start of mission 1 (recording 1 of 1)"
    assert r["time"] == NOW
    r, how = ask(game, "playCard", battle_id=bid, card_uid="1", skip="0", host_id="1000001")
    assert r["battle_data"]["battle_id"] == bid and r["battle_data"]["winner"] == 1
    assert r["battle_data"]["end_time"] == NOW + 60                     # shifted with everything else
    assert r["user_data"] == {"xp": "10"}
    assert how == "replayed mission 1, play 1 of 1 (battle over); mission 1 won, saved (not in the mission data, so nothing paid)"
    r, how = ask(game, "playCard", battle_id=bid, card_uid="1", skip="0")
    assert how == "UNSUPPORTED: there is no battle in progress."


def test_wrong_card_is_explained(game):
    r, _ = ask(game, "startMission", mission_id="2")
    bid = r["battle_data"]["battle_id"]
    r, how = ask(game, "playCard", battle_id=bid, card_uid="3", skip="0")
    assert r["result"] is False and "played card #2 at this point" in r["result_message"][0]
    r, how = ask(game, "playCard", battle_id=bid, card_uid="2", skip="1")       # auto wasn't what was recorded
    assert r["result"] is False
    r, how = ask(game, "playCard", battle_id=bid, card_uid="2", skip="0")
    assert how == "replayed mission 2, play 1 of 2"                            # still waiting at the same point
    r, how = ask(game, "playCard", battle_id="999", card_uid="3", skip="0")
    assert how == "UNSUPPORTED: that isn't the battle in progress."


def test_repeated_missions_cycle_through_recordings(game):
    hows = [ask(game, "startMission", mission_id="2")[1] for _ in range(3)]
    assert hows == ["replayed start of mission 2 (recording 1 of 2)",
                    "replayed start of mission 2 (recording 2 of 2)",
                    "replayed start of mission 2 (recording 1 of 2)"]


def test_unrecorded_mission_and_arena_battle(game):
    r, how = ask(game, "startMission", mission_id="9")
    assert r["result_message"] == ["Offline server: mission 9 hasn't been recorded, so it can't be replayed yet."]
    r, how = ask(game, "startHuntingBattle", target_user_id="-1")
    assert how == "replayed start of startHuntingBattle (target_user_id=-1) (recording 1 of 1)"
    r, how = ask(game, "playCard", battle_id=r["battle_data"]["battle_id"], card_uid="1", skip="0")
    assert how.endswith("(battle over)")


def test_matched_requests_replay_in_recorded_order(game):
    r1, h1 = ask(game, "buyStoreItem", pack_id="1")
    r2, h2 = ask(game, "buyStoreItem", pack_id="1")
    r3, h3 = ask(game, "buyStoreItem", pack_id="1")
    assert (r1["n"], r2["n"], r3["n"]) == (1, 2, 2)                    # then keeps the last answer
    assert (h1, h3) == ("replayed (recording 1 of 2)", "replayed (recording 2 of 2)")
    r, how = ask(game, "buyStoreItem", pack_id="7")
    assert "only recorded with other details" in r["result_message"][0]


def test_record_event_is_an_empty_reply(game):
    r, how = ask(game, "recordEvent", category="tutorial", sub_category="5", item_id="1")
    assert set(r) == {"time", "version", "daily_bonus_time", "guild_donations_enabled", "guild_supplies_enabled", "request"}
    assert how.startswith("answered")


def test_login_starts_the_story_again(game):
    ask(game, "buyStoreItem", pack_id="1")
    ask(game, "startMission", mission_id="1")
    ask(game, "getUserAccount")
    r, _ = ask(game, "buyStoreItem", pack_id="1")
    assert r["n"] == 1 and game.battle is None
