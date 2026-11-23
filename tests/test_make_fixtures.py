"""Tests for the fixture builder (make_fixtures.py): no account identifier in the
fixtures, Kongregate sessions refused, the sanitized copy, and the replay library."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

import game_api as ga
import make_fixtures as mf
import paths
from helpers import T

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


def _fixture_session() -> str:
    try:
        return json.loads((paths.FIXTURE_DIR / "init.json").read_text("utf-8"))["source"]["session"]
    except (OSError, ValueError, KeyError):
        return ""


@pytest.mark.skipif(not (paths.SANITIZED_CAPTURES_DIR / _fixture_session()).is_dir() or not _fixture_session(),
                    reason="no sanitized copy of the fixtures' session here")
def test_the_real_fixtures_can_be_rebuilt_from_the_sanitized_recordings(tmp_path):
    mf.build(paths.SANITIZED_CAPTURES_DIR / _fixture_session(), tmp_path / "fx")
    for name in ("getUserAccount", "init", "replay"):
        assert (tmp_path / "fx" / f"{name}.json").read_bytes() == (paths.FIXTURE_DIR / f"{name}.json").read_bytes(), name


@pytest.mark.skipif(not paths.RAW_CAPTURES_DIR.is_dir(), reason="no recorded sessions on this machine")
def test_real_fixtures_contain_no_identifier_from_any_session(tmp_path):
    """Build from the real recordings, then look for every identifier any session ever sent."""
    built = None
    for s in sorted(paths.RAW_CAPTURES_DIR.iterdir(), reverse=True):
        try:
            built = mf.build(s, tmp_path / "fx")
            break
        except mf.FixtureError:
            continue
    if built is None:
        pytest.skip("no non-Kongregate session recorded")
    every = {}
    for s in paths.RAW_CAPTURES_DIR.iterdir():
        for f in s.glob("*.json"):
            try:
                every.update(mf.secrets_of(json.loads(f.read_text("utf-8"))))
            except (mf.FixtureError, ValueError):
                pass
    text = "".join(p.read_text("utf-8") for p in (tmp_path / "fx").glob("*.json"))
    assert every and not [s for s in every if s in text]


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
