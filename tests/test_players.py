"""Tests for several players on one server: accounts (accounts.py), logins, per-player
saves and sessions, players meeting in guilds and raids, and requests arriving at the
same time."""

import http.client
import json
import sys
import threading

import pytest

import game_api as ga
import offline_server as srv
import paths
import player as cli
from helpers import server, login, call, T, ask


# ---- accounts and logins -------------------------------------------------------------------------

@pytest.mark.needs_game
def test_each_new_browser_gets_its_own_account_and_save(tmp_path):
    api, _ = server(tmp_path)
    ra, a = login(api)
    rb, b = login(api)
    assert (ra["new_user"], rb["new_user"]) == (1000001, 1000002)
    for r in (ra, rb):
        assert len(r["new_password"]) == 32 and len(r["new_syncode"]) == 64 and r["new_name"] == f"Player{r['new_user']}"
        assert r["user_data"]["user_id"] == str(r["new_user"]) and r["user_data"]["name"] == r["new_name"]
    assert ra["new_password"] != rb["new_password"]
    call(api, a, "setUserFlag", flag="music", value="0")
    assert json.loads((tmp_path / "players" / "1000001.json").read_text("utf-8"))["player"]["flags"]["music"] == "0"
    assert json.loads((tmp_path / "players" / "1000002.json").read_text("utf-8"))["player"]["flags"].get("music") != "0"
    accounts = json.loads((tmp_path / "accounts.json").read_text("utf-8"))
    assert set(accounts["accounts"]) == {"1000001", "1000002"}
    assert ra["new_password"] not in (tmp_path / "accounts.json").read_text("utf-8")   # kept as hashes only


@pytest.mark.needs_game
def test_a_returning_browser_keeps_its_account(tmp_path):
    api, _ = server(tmp_path)
    _, a = login(api)
    call(api, a, "setUserFlag", flag="music", value="0")
    r, creds = login(api, a)
    assert "new_user" not in r and "new_password" not in r and creds == a
    assert r["new_syncode"] == api.accounts.get(1000001)["syncode"]
    r, _ = call(api, a, "init")
    assert r["user_data"]["user_id"] == "1000001" and r["user_data"]["flags"]["music"] == "0"


@pytest.mark.needs_game
def test_wrong_credentials(tmp_path):
    api, _ = server(tmp_path)
    _, a = login(api)
    r, how = call(api, {"user_id": a["user_id"], "password": "nope"}, "init")
    assert r["result"] is False and r["result_message"] == [ga.NOT_LOGGED_IN] and how == "refused init: not logged in"
    r, how = call(api, {}, "setUserFlag", flag="music", value="0")             # no login at all
    assert r["result"] is False
    r, creds = login(api, {"user_id": a["user_id"], "password": "nope"})     # logging in with them: a new player
    assert r["new_user"] == 1000002 and creds != a


@pytest.mark.needs_game
def test_a_single_player_save_becomes_player_1000001(tmp_path):
    solo = ga.GameApi(state_dir=tmp_path, clock=lambda: T)
    ask(solo, "init")
    p = solo.player()
    p.gold = 4242
    solo.player_store.save(p)
    api, _ = server(tmp_path)
    assert api.migrated and not (tmp_path / "player.json").exists()
    assert "now player 1000001's" in api.startup_report()
    old = {"user_id": "1000001", "password": ga.OFFLINE_PASSWORD}
    r, creds = login(api, old)                                    # the browser's old, public login
    assert r["new_user"] == 1000001 and creds["password"] != ga.OFFLINE_PASSWORD
    assert r["user_data"]["money"] in (4242, "4242")
    assert call(api, old, "init")[0].get("result") is not False   # still fine until the new one is used
    assert call(api, creds, "init")[0]["user_data"]["money"] in (4242, "4242")
    assert call(api, old, "init")[0]["result"] is False           # then the public one stops working
    r, again = login(api, creds)
    assert "new_user" not in r                                    # no more changes


def test_replies_name_the_player_they_are_for():
    body = {"user_data": {"user_id": "1000001"},
            "battle_data": {"host_id": "1000001", "rewards": [{"user_id": "1000001"}, {"user_id": "-5"}]}}
    ga.personalize(body, 1000002)
    assert body["user_data"]["user_id"] == "1000002" and body["battle_data"]["host_id"] == "1000002"
    assert [r["user_id"] for r in body["battle_data"]["rewards"]] == ["1000002", "-5"]


# ---- sessions ------------------------------------------------------------------------------------

@pytest.mark.needs_game
def test_each_player_has_their_own_battle(tmp_path):
    api, _ = server(tmp_path, engine=True)
    _, a = login(api)
    _, b = login(api)
    for creds in (a, b):
        call(api, creds, "init")
        call(api, creds, "setUserFlag", flag="tutorial", value="-1")
    ra, how_a = call(api, a, "startMission", mission_id="2")
    rb, how_b = call(api, b, "startMission", mission_id="3")
    assert how_a.startswith("engine: started mission 2") and how_b.startswith("engine: started mission 3")
    bid_a = ra["battle_data"]["battle_id"]
    hand = [u for u in ra["battle_data"]["turn"]["1"]["draws"]]
    r, how = call(api, a, "playCard", battle_id=str(bid_a), card_uid=str(hand[0]), skip="1")
    assert "mission 2" in how                                       # A's own battle, not B's
    assert api._sessions["1000002"].battle["mission_id"] == "3"     # B's is untouched


# ---- players together ----------------------------------------------------------------------------

@pytest.mark.needs_game
def test_two_players_in_one_guild(tmp_path):
    api, _ = server(tmp_path)
    _, a = login(api)
    _, b = login(api)
    r, _ = call(api, b, "getUsersLFG")
    assert [u["user_id"] for u in r["user_lfg"]] == []              # B isn't in a guild, but needs one to see
    call(api, a, "createFaction", faction_name="Owls")
    r, _ = call(api, a, "getUsersLFG")
    assert [(u["user_id"], u["name"]) for u in r["user_lfg"]] == [("1000002", "Player1000002")]
    r, how = call(api, a, "sendFactionInvite", target_user_id=b["user_id"])
    assert how == "invited Player1000002 to Owls"
    r, how = call(api, b, "updateFaction", last_activity_id="0")
    assert [f["name"] for f in r["faction_invites"]] == ["Owls"]
    r, how = call(api, b, "acceptFactionInvite", faction_id=r["faction_invites"][0]["faction_id"])
    assert sorted((m["user_id"], m["member_role"]) for m in r["faction"]["members"]) == [("1000001", 31), ("1000002", 1)]
    call(api, b, "sendFactionMessage", chat="hello Ann", last_activity_id="0")
    r, _ = call(api, a, "updateFaction", last_activity_id="0")
    assert json.loads(r["faction_news"][-1]["data"]) == {"name": "Player1000002", "message": "hello Ann"}
    r, _ = call(api, a, "getProfileData", target_user_id=b["user_id"])
    assert r["player_info"]["name"] == "Player1000002"
    call(api, a, "setMemberRank", target_user_id=b["user_id"], rank="21")
    r, how = call(api, a, "setMemberRank", target_user_id=b["user_id"], rank="31")
    assert how == "Player1000002 made the guild leader" and r["faction"]["leader_id"] == "1000002"
    r, how = call(api, b, "kickFactionMember", target_user_id=a["user_id"])
    assert r["result_message"] == ["Demote an officer to member before removing them."]


@pytest.mark.needs_game
def test_a_guild_fights_one_boss_together(tmp_path):
    api, clock = server(tmp_path, content="[raids]\nraid = 161\nhealth_percent = 2\n")
    _, a = login(api)
    _, b = login(api)
    call(api, a, "createFaction", faction_name="Owls")
    call(api, b, "joinFaction", faction_id="900001")
    call(api, a, "fightRaidBattle", raid_id="161", raid_level="1")
    call(api, b, "fightRaidBattle", raid_id="161", raid_level="1")
    r, _ = call(api, b, "getRaidInfo")
    info = r["raid_info"][0]
    damage = {m["member_id"]: m["damage"] for m in info["members"]}
    assert set(damage) == {"1000001", "1000002"} and all(d > 0 for d in damage.values())
    assert info["health"] == 300 - sum(damage.values())             # one boss, both players' damage
    assert info["energy"]["battle_energy"] == 12                    # B's own raid energy
    r, _ = call(api, a, "updateFaction", last_activity_id="0")
    assert [n["type"] for n in r["faction_news"]][-1] == 31         # A launched the level


# ---- at the same time ----------------------------------------------------------------------------

@pytest.mark.needs_game
def test_players_playing_at_once_over_http(tmp_path):
    api, _ = server(tmp_path)
    s = srv.OfflineServer(("127.0.0.1", 0), api=api, log=lambda line: None)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    host, port = s.server_address[:2]

    def post(message, params):
        conn = http.client.HTTPConnection(host, port, timeout=30)
        body = "&".join(f"{k}={v}" for k, v in params.items())
        conn.request("POST", f"/tu/mobile.tyrantonline.com/api.php?message={message}", body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        reply = json.loads(conn.getresponse().read())
        conn.close()
        return reply

    players, errors = [], []

    def play(n):
        try:
            r = post("getUserAccount", {"udid": f"device{n}"})
            creds = {"user_id": str(r["new_user"]), "password": r["new_password"]}
            players.append(creds["user_id"])
            for i in range(15):
                r = post("setUserFlag", {**creds, "flag": f"step{i}", "value": str(n)})
                assert r.get("result") is not False, r
        except Exception as e:                                       # noqa: BLE001 (reported below)
            errors.append(e)

    threads = [threading.Thread(target=play, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s.shutdown()
    s.server_close()
    assert not errors
    assert sorted(players) == ["1000001", "1000002", "1000003", "1000004"]
    for uid in players:                                              # every player's every flag, none lost
        flags = json.loads((tmp_path / "players" / f"{uid}.json").read_text("utf-8"))["player"]["flags"]
        assert {f"step{i}" for i in range(15)} <= set(flags)
        assert len({flags[f"step{i}"] for i in range(15)}) == 1     # all from that player


# ---- the admin tool ------------------------------------------------------------------------------

def run_cli(monkeypatch, capsys, tmp_path, *args):
    monkeypatch.setattr(sys, "argv", ["player.py", "--fixtures", str(paths.FIXTURE_DIR), "--state", str(tmp_path), *args])
    monkeypatch.setattr(cli, "CLOCK", lambda: T)
    try:
        cli.main()
    except SystemExit as e:
        return str(e)
    return capsys.readouterr().out


@pytest.mark.needs_game
def test_the_admin_tool_on_a_multi_player_server(tmp_path, monkeypatch, capsys):
    api, _ = server(tmp_path)
    login(api)
    login(api)
    out = run_cli(monkeypatch, capsys, tmp_path, "players")
    assert "2 players:" in out and "1000001  Player1000001" in out and "1000002  Player1000002" in out
    assert "choose one with --user" in run_cli(monkeypatch, capsys, tmp_path, "gold", "500")
    out = run_cli(monkeypatch, capsys, tmp_path, "--user", "1000002", "name", "Night", "Hawk")
    assert "Renamed Player1000002 to Night Hawk" in out
    run_cli(monkeypatch, capsys, tmp_path, "--user", "1000002", "gold", "777")
    p2 = json.loads((tmp_path / "players" / "1000002.json").read_text("utf-8"))["player"]
    p1 = json.loads((tmp_path / "players" / "1000001.json").read_text("utf-8"))["player"]
    assert (p2["name"], p2["gold"]) == ("Night Hawk", 777) and p1["gold"] != 777
    assert "There's no player 5" in run_cli(monkeypatch, capsys, tmp_path, "--user", "5", "gold", "1")
