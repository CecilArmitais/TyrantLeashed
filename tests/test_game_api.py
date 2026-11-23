"""Tests for the game API (replay with time shifting) and the fixture builder."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

import game_api as ga
import paths
import make_fixtures as mf

T = 1_795_000_000          # recorded server time
NOW = T + 1_000_000        # about 11.6 days later


# ---- shift_times ---------------------------------------------------------------------------------

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


# ---- make_fixtures -------------------------------------------------------------------------------

REAL_UID, REAL_PW, REAL_SYNC = "1400000042", "ab12cd34ef56ab12cd34ef56ab12cd34", "99ffee11223344556677889900aabbccddeeff00"
REAL_NAME, UDID = "Commander8812", "5f3c-device-id-0001"


def make_session(root: Path, kong: bool = False) -> Path:
    s = root / "api" / "20261120-000000"
    s.mkdir(parents=True)
    base = {"user_id": REAL_UID, "password": REAL_PW, "syncode": REAL_SYNC, "hash": "deadbeefcafe",
            "client_signature": "feedfacefeed"}
    if kong:
        base.update({"kong_id": "7777777", "kong_name": "realperson", "kong_token": "tok-secret-123"})
    calls = [
        ("getUserAccount", {**base, "udid": UDID, "password": ""},
         {"time": T, "result": True, "new_user": int(REAL_UID), "new_password": REAL_PW, "new_syncode": REAL_SYNC,
          "new_name": REAL_NAME, "user_data": {"user_id": REAL_UID, "name": REAL_NAME, "level": "1"},
          "request": {"user_id": REAL_UID, "udid": UDID}}),
        ("recordEvent", base, {"time": T}),
        ("init", base,
         {"time": T, "user_data": {"user_id": REAL_UID, "name": REAL_NAME},
          "news": {"1": {"text": f"Welcome back {REAL_NAME}!"}},
          "store_promos": [{"start_time": T}], "request": {"password": REAL_PW}}),
    ]
    with (s / "index.jsonl").open("w", encoding="utf-8") as idx:
        for seq, (msg, params, body) in enumerate(calls, 1):
            name = f"{seq:06d}_{msg}.json"
            (s / name).write_text(json.dumps({"seq": seq, "message": msg,
                                              "request": {"query": {"message": msg, "user_id": params["user_id"]},
                                                          "form": params},
                                              "response": {"status": 200, "body": {"json": body}}}), "utf-8")
            idx.write(json.dumps({"seq": seq, "message": msg, "status": 200, "file": name}) + "\n")
    return s


def test_fixtures_have_no_account_identifiers(tmp_path):
    out = tmp_path / "fixtures"
    info = mf.build(make_session(tmp_path), out)
    assert info["session"] == "20261120-000000"
    text = (out / "getUserAccount.json").read_text("utf-8") + (out / "init.json").read_text("utf-8")
    for secret in (REAL_UID, REAL_PW, REAL_SYNC, REAL_NAME, UDID):
        assert secret not in text
    gua = json.loads((out / "getUserAccount.json").read_text("utf-8"))["body"]
    init = json.loads((out / "init.json").read_text("utf-8"))["body"]
    assert gua["new_user"] == ga.OFFLINE_USER_ID                       # int stays int
    assert gua["user_data"]["user_id"] == str(ga.OFFLINE_USER_ID)      # string stays string
    assert (gua["new_password"], gua["new_syncode"], gua["new_name"]) == (ga.OFFLINE_PASSWORD, ga.OFFLINE_SYNCODE, ga.OFFLINE_NAME)
    assert init["news"]["1"]["text"] == f"Welcome back {ga.OFFLINE_NAME}!"   # inside text, too
    assert "request" not in gua and "request" not in init
    assert init["store_promos"] == [{"start_time": T}]


def test_kongregate_sessions_are_refused(tmp_path):
    s = make_session(tmp_path, kong=True)
    with pytest.raises(mf.FixtureError, match="Kongregate"):
        mf.build(s, tmp_path / "fixtures")
    assert not (tmp_path / "fixtures").exists()
    mf.build(s, tmp_path / "fixtures", allow_kongregate=True)       # only when asked
    text = (tmp_path / "fixtures" / "init.json").read_text("utf-8")
    assert "tok-secret-123" not in text and "realperson" not in text


def test_nothing_written_if_scrubbing_misses_something(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "scrub", lambda obj, secrets: obj)
    with pytest.raises(mf.FixtureError, match="survived"):
        mf.build(make_session(tmp_path), tmp_path / "fixtures")
    assert not (tmp_path / "fixtures").exists()       # checked before anything is written


def sanitized_copy(session: Path, root: Path) -> Path:
    """The session as the workspace's capture/sanitize_recordings.py leaves it: every
    identifier already the offline player's (made here with make_fixtures' own scrub),
    plus the copy's sessions.json naming the recorded account."""
    secrets = mf.secrets_of(*mf.session_calls(session))
    out = root / "api-sanitized" / session.name
    out.mkdir(parents=True)
    for f in session.iterdir():
        text = f.read_text("utf-8")
        if f.suffix == ".json":
            text = json.dumps(mf.scrub(json.loads(text), secrets))
        (out / f.name).write_text(text, "utf-8")
    fp = hashlib.sha256(REAL_UID.encode()).hexdigest()[:8]
    (out.parent / "sessions.json").write_text(json.dumps({"sessions": {session.name: {"accounts": [fp]}}}), "utf-8")
    return out


def test_fixtures_from_a_sanitized_session_match_the_raw_ones(tmp_path):
    raw = make_session(tmp_path / "raw")
    mf.build(raw, tmp_path / "from_raw")
    clean = sanitized_copy(raw, tmp_path / "clean")
    info = mf.build(clean, tmp_path / "from_clean")
    assert info["replaced"] == 0                                     # nothing left to replace
    assert info["account"] == hashlib.sha256(REAL_UID.encode()).hexdigest()[:8]   # from sessions.json
    for name in ("getUserAccount", "init", "replay"):
        assert (tmp_path / "from_clean" / f"{name}.json").read_bytes() == \
            (tmp_path / "from_raw" / f"{name}.json").read_bytes()


def test_a_sanitized_session_without_its_index_is_labelled_sanitized(tmp_path):
    clean = sanitized_copy(make_session(tmp_path / "raw"), tmp_path / "clean")
    (clean.parent / "sessions.json").unlink()
    assert mf.build(clean, tmp_path / "fx")["account"] == "sanitized"


def test_sanitized_kongregate_sessions_are_still_refused(tmp_path):
    clean = sanitized_copy(make_session(tmp_path / "raw", kong=True), tmp_path / "clean")
    with pytest.raises(mf.FixtureError, match="Kongregate"):
        mf.build(clean, tmp_path / "fx")


def test_without_raw_recordings_the_sanitized_copy_is_used(tmp_path, monkeypatch, capsys):
    captures = tmp_path / "captures"
    sanitized_copy(make_session(tmp_path / "raw"), captures)         # only captures/api-sanitized
    monkeypatch.setattr(sys, "argv", ["make_fixtures.py", "--captures", str(captures), "--out", str(tmp_path / "fx")])
    mf.main()
    assert "Fixtures built from session 20261120-000000" in capsys.readouterr().out
    assert json.loads((tmp_path / "fx" / "init.json").read_text("utf-8"))["body"]["user_data"]["user_id"] == \
        str(ga.OFFLINE_USER_ID)


REAL_CAPTURES = paths.CAPTURES_DIR / "api"
REAL_SANITIZED = REAL_CAPTURES.parent / "api-sanitized"


def _fixture_session() -> str:
    try:
        return json.loads((ga.FIXTURE_DIR / "init.json").read_text("utf-8"))["source"]["session"]
    except (OSError, ValueError, KeyError):
        return ""


@pytest.mark.skipif(not (REAL_SANITIZED / _fixture_session()).is_dir() or not _fixture_session(),
                    reason="no sanitized copy of the fixtures' session here")
def test_the_real_fixtures_can_be_rebuilt_from_the_sanitized_recordings(tmp_path):
    mf.build(REAL_SANITIZED / _fixture_session(), tmp_path / "fx")
    for name in ("getUserAccount", "init", "replay"):
        assert (tmp_path / "fx" / f"{name}.json").read_bytes() == (ga.FIXTURE_DIR / f"{name}.json").read_bytes(), name


@pytest.mark.skipif(not REAL_CAPTURES.is_dir(), reason="no recorded sessions on this machine")
def test_real_fixtures_contain_no_identifier_from_any_session(tmp_path):
    """Build from the real recordings, then look for every identifier any session ever sent."""
    built = None
    for s in sorted(REAL_CAPTURES.iterdir(), reverse=True):
        try:
            built = mf.build(s, tmp_path / "fx")
            break
        except mf.FixtureError:
            continue
    if built is None:
        pytest.skip("no non-Kongregate session recorded")
    every = {}
    for s in REAL_CAPTURES.iterdir():
        for f in s.glob("*.json"):
            try:
                every.update(mf.secrets_of(json.loads(f.read_text("utf-8"))))
            except (mf.FixtureError, ValueError):
                pass
    text = "".join(p.read_text("utf-8") for p in (tmp_path / "fx").glob("*.json"))
    assert every and not [s for s in every if s in text]


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


def ask(api, message, **params):
    return api.respond(message, {"message": message, "user_id": "1000001", "password": "p", **params})


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


def test_builder_exports_a_clean_replay_library(tmp_path):
    s = make_session(tmp_path)
    # later calls, including one the server answered with an HTML error page
    later = [
        ("startMission", {"mission_id": "1", "battle_id": "x"}, {"time": T, "battle_data": {"host_id": REAL_UID}}, 200),
        ("setUserFlag", {"flag": "tutorial", "value": "1"}, {"time": T, "result": True, "request": {"password": REAL_PW}}, 200),
        ("recordEvent", {"category": "x"}, {"time": T}, 200),
        ("getBoardData", {"board": "basic"}, None, 504),
    ]
    with (s / "index.jsonl").open("a", encoding="utf-8") as idx:
        for seq, (msg, params, body, status) in enumerate(later, 4):
            name = f"{seq:06d}_{msg}.json"
            resp = {"status": status, "body": {"json": body} if body is not None else {"text": "<html>504</html>"}}
            form = {"user_id": REAL_UID, "password": REAL_PW, "syncode": REAL_SYNC, **params}
            (s / name).write_text(json.dumps({"seq": seq, "message": msg, "request": {"query": {}, "form": form},
                                              "response": resp}), "utf-8")
            idx.write(json.dumps({"seq": seq, "message": msg, "status": status, "file": name}) + "\n")
    info = mf.build(s, tmp_path / "fx")
    replay = json.loads((tmp_path / "fx" / "replay.json").read_text("utf-8"))
    assert [c["message"] for c in replay["calls"]] == ["startMission", "setUserFlag"]   # no login, analytics, 504
    assert replay["calls"][0]["key"] == {"mission_id": "1"}                             # battle_id is volatile
    assert replay["calls"][0]["body"]["battle_data"]["host_id"] == str(ga.OFFLINE_USER_ID)
    assert "request" not in replay["calls"][1]["body"]
    text = (tmp_path / "fx" / "replay.json").read_text("utf-8")
    assert REAL_UID not in text and REAL_PW not in text and REAL_SYNC not in text
    assert info["replay_calls"] == 2
