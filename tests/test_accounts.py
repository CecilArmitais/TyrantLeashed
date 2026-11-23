"""Tests for accounts and signing in (accounts.py, game_api/account_calls.py): usernames,
the optional sign-in passwords, sessions, the game's login picking up the signed-in
account, and the names of accounts from before usernames."""

import json

import pytest

import accounts
import game_api as ga
from helpers import T, call, init_body, server


def test_usernames_are_kept_tidy_and_checked():
    assert accounts.clean_name("  Night   Hawk ") == "Night Hawk"
    assert accounts.name_problem("Zoë_1 O'Neil-X") is None
    assert accounts.name_problem("") == accounts.name_problem("x" * 21) == "A name needs 1 to 20 characters."
    assert "letters, digits" in accounts.name_problem("<b>Boss</b>")


def test_the_account_store(tmp_path):
    store = accounts.AccountStore(tmp_path / "accounts.json")
    uid, _, _ = store.create(1, name="Night Hawk")
    assert store.find("night  HAWK") == uid and store.find("Nobody") is None
    with pytest.raises(ValueError):
        store.create(2, name="NIGHT HAWK")                                  # taken, in any case
    store.set_login_password(uid, "s3cret!")
    assert store.check_login_password(uid, "s3cret!") and not store.check_login_password(uid, "s3cret")
    assert "s3cret" not in (tmp_path / "accounts.json").read_text("utf-8")   # a salted hash only
    token = store.new_session(uid, 5)
    assert store.session_user(token) == uid and store.session_user("nope") is None
    store.end_session(token)
    assert store.session_user(token) is None
    first, second = store.add_browser(uid), store.add_browser(uid)
    assert store.check(uid, first) and store.check(uid, second)            # two browsers, both good


def sign_in(api, name, password=None):
    reply, token, how = api.sign_in(name, password)
    return reply, token


def game_login(api, session, creds=None):
    r, how = api.respond("getUserAccount", {"message": "getUserAccount", **(creds or {})}, session=session)
    new = {"user_id": str(r["new_user"]), "password": r["new_password"]} if r.get("new_user") else creds
    return r, new, how


@pytest.mark.needs_game
def test_a_new_name_makes_a_player_and_the_game_logs_in_as_them(tmp_path):
    api, _ = server(tmp_path)
    reply, token = sign_in(api, "  Night   Hawk ")
    assert reply == {"ok": True, "name": "Night Hawk", "new": True, "password_set": False} and token
    saved = json.loads((tmp_path / "players" / "1000001.json").read_text("utf-8"))["player"]
    assert saved["name"] == "Night Hawk"
    r, creds, how = game_login(api, token)
    assert r["new_user"] == 1000001 and r["new_name"] == "Night Hawk" and "newly signed-in" in how
    assert call(api, creds, "init")[0]["user_data"]["name"] == "Night Hawk"
    r, again, how = game_login(api, token, creds)                          # a reload: nothing changes
    assert "new_user" not in r and again == creds and how == "player 1000001 logged in"


@pytest.mark.needs_game
def test_a_name_already_here_signs_in_as_that_player(tmp_path):
    api, _ = server(tmp_path)
    _, first = sign_in(api, "Alpha")
    _, a, _ = game_login(api, first)
    call(api, a, "setUserFlag", flag="music", value="0")
    reply, second = sign_in(api, "ALPHA")                                  # another browser, any case
    assert reply["new"] is False and reply["name"] == "Alpha" and api.accounts.ids() == [1000001]
    r, b, _ = game_login(api, second, {"user_id": "1000099", "password": "an old server's"})
    assert r["new_user"] == 1000001 and b["password"] != a["password"]
    for creds in (a, b):                                                    # both browsers keep working
        assert call(api, creds, "init")[0]["user_data"]["flags"]["music"] == "0"


@pytest.mark.needs_game
def test_a_browser_signed_in_as_someone_else_switches_player(tmp_path):
    api, _ = server(tmp_path)
    _, token_a = sign_in(api, "Alpha")
    _, a, _ = game_login(api, token_a)
    _, token_b = sign_in(api, "Bravo")
    r, b, _ = game_login(api, token_b, a)                                  # this browser's credentials are Alpha's
    assert r["new_user"] == 1000002 and r["new_name"] == "Bravo"
    assert call(api, b, "init")[0]["user_data"]["name"] == "Bravo"


@pytest.mark.needs_game
def test_signing_in_is_needed_and_signing_out_ends_it(tmp_path):
    api, _ = server(tmp_path)
    r, _, how = game_login(api, None)
    assert r["result_message"] == [ga.NOT_SIGNED_IN] and api.accounts.ids() == []
    reply, token = sign_in(api, "x" * 25)
    assert reply == {"ok": False, "message": "A name needs 1 to 20 characters."} and token is None
    _, token = sign_in(api, "Alpha")
    assert api.signed_in(token) and api.sign_in_info(token)["name"] == "Alpha"
    api.sign_out(token)
    assert not api.signed_in(token) and api.sign_in_info(token)["name"] is None
    assert game_login(api, token)[0]["result_message"] == [ga.NOT_SIGNED_IN]


@pytest.mark.needs_game
def test_passwords_when_the_server_asks_for_them(tmp_path):
    api, _ = server(tmp_path, content="[accounts]\npasswords = true\n")
    assert api.sign_in_info(None)["passwords"] is True
    assert sign_in(api, "Alpha")[0]["message"].startswith("Choose a password of at least 6 characters")
    assert sign_in(api, "Alpha", "short")[1] is None
    reply, token = sign_in(api, "Alpha", "longer one")
    assert reply["password_set"] and token
    assert sign_in(api, "alpha", "wrong one")[0] == {"ok": False, "message": "That isn't Alpha's password."}
    assert sign_in(api, "alpha", "longer one")[0]["ok"]


@pytest.mark.needs_game
def test_an_account_from_before_passwords_is_claimed_by_its_first_password(tmp_path):
    api, _ = server(tmp_path)
    sign_in(api, "Alpha")
    (tmp_path / "content.toml").write_text("[accounts]\npasswords = true\n", "utf-8")
    reply, _ = sign_in(api, "Alpha", "first one")
    assert reply["ok"] and reply["password_set"] and not reply["new"]
    assert not sign_in(api, "Alpha", "second one")[0]["ok"]


@pytest.mark.needs_game
def test_renaming_keeps_the_sign_in_name_and_refuses_one_taken(tmp_path):
    api, _ = server(tmp_path)
    _, a, _ = game_login(api, sign_in(api, "Alpha")[1])
    sign_in(api, "Bravo")
    r, how = call(api, a, "setUserName", name="bravo")
    assert r["result_message"] == ["The name bravo is taken."]
    r, how = call(api, a, "setUserName", name="Charlie")
    assert r["result"] and api.accounts.find("charlie") == 1000001 and api.accounts.find("Alpha") is None


@pytest.mark.needs_game
def test_accounts_from_before_usernames_get_their_players_names(tmp_path):
    store = accounts.AccountStore(tmp_path / "accounts.json")
    for _ in range(3):
        store.create(T)
    for uid, name in ((1000001, "Night Hawk"), (1000002, "night hawk")):   # the second clashes
        p = tmp_path / "players" / f"{uid}.json"
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps({"version": 13, "player": {
            "gold": 1, "war_bonds": 1, "energy": 1, "energy_updated": T, "cards": {}, "name": name}}), "utf-8")
    api, _ = server(tmp_path)
    assert [api.accounts.name_of(u) for u in (1000001, 1000002, 1000003)] == ["Night Hawk", "Player1000002",
                                                                              "Player1000003"]


# ---- bringing progress -----------------------------------------------------------------------------

def a_save(api, **changes):
    """A save from 'another server': this one's export, changed."""
    uid = api.accounts.find("Donor")
    _, data = api.export_save(uid)
    data["player"].update(changes)
    return data


@pytest.mark.needs_game
def test_a_save_brought_in_loads_at_once_when_the_server_says_so(tmp_path):
    api, _ = server(tmp_path, content='[accounts]\nimports = "on"\n')
    sign_in(api, "Donor")
    record = a_save(api, gold=7777, name="Someone Else")
    record["player"]["cards"]["99999999"] = {"num_owned": 1, "num_used": 0}       # not in this game's data
    reply, token = api.sign_in("Newcomer", None, record)[:2]
    assert reply["import"] == "loaded" and "1 cards this server's game data doesn't have" in reply["notes"][0]
    _, creds, _ = game_login(api, token)
    r = call(api, creds, "init")[0]
    assert r["user_data"]["money"] in (7777, "7777") and r["user_data"]["name"] == "Newcomer"


@pytest.mark.needs_game
def test_a_save_brought_in_waits_for_the_host_by_default(tmp_path):
    api, _ = server(tmp_path)
    sign_in(api, "Donor")
    reply = api.sign_in("Newcomer", None, a_save(api, gold=7777))[0]
    uid = api.accounts.find("Newcomer")
    assert reply["import"] == "waiting" and api._player_of(uid).gold == 100       # fresh until accepted
    waiting = api.waiting_imports()
    assert [(w["user_id"], w["name"], w["kind"]) for w in waiting] == [(uid, "Newcomer", "save")]
    assert waiting[0]["summary"]["gold"] == 7777
    api.accept_import(uid)
    assert api._player_of(uid).gold == 7777 and api._player_of(uid).name == "Newcomer"
    assert api.waiting_imports() == []
    with pytest.raises(ValueError):
        api.reject_import(uid)


@pytest.mark.needs_game
def test_imports_the_server_turns_away(tmp_path):
    api, _ = server(tmp_path, content='[accounts]\nimports = "off"\n')
    sign_in(api, "Donor")
    record = a_save(api)
    assert api.sign_in("Newcomer", None, record)[0]["message"] == "This server doesn't take imported progress."
    (tmp_path / "content.toml").write_text('[accounts]\nimports = "on"\n', "utf-8")
    reply = api.sign_in("donor", None, record)[0]
    assert reply["message"].startswith("The name Donor is taken: bringing your progress needs a name")
    assert api.sign_in("Newcomer", None, {"what": "nonsense"})[0]["message"].startswith("That file is neither")
    assert api.sign_in("Newcomer", None, {"version": 99, "player": {}})[0]["message"].startswith(
        "That save can't be read (save version 99")
    assert api.accounts.find("Newcomer") is None                   # no account for a refused import


@pytest.mark.needs_game
def test_the_official_login_data_comes_in_as_a_save(tmp_path):
    api, _ = server(tmp_path, content='[accounts]\nimports = "on"\n')
    login = json.loads(json.dumps(init_body()))
    login["user_data"]["money"] = "4321"
    login["request"] = {"password": "a real one"}
    reply = api.sign_in("Veteran", None, login)[0]
    assert reply["import"] == "loaded"
    assert api._player_of(api.accounts.find("Veteran")).gold == 4321
    assert "a real one" not in json.dumps(json.loads((tmp_path / "players" / "1000001.json").read_text("utf-8")))


@pytest.mark.needs_game
def test_a_players_save_downloads(tmp_path):
    api, _ = server(tmp_path)
    _, token = sign_in(api, "Night Hawk")
    name, data = api.my_save(token)
    assert name == "tyrant-Night-Hawk.json" and data["player"]["name"] == "Night Hawk" and data["version"] == 13
    assert api.my_save(None) is None
