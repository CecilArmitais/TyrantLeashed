"""Tests for turning the official game's login data into a save (login_import.py,
convert_login.py): credentials dropped, a HAR's init found, what carries over, and
which objectives count as done."""

import base64
import json
import sys
from functools import lru_cache

import pytest

from common import paths
from helpers import T, init_body
from rules import achievements
from rules import login_import as li
from tools import convert_login


@lru_cache(maxsize=None)
def defs():
    return achievements.load(paths.ASSET_DIR)


def test_credentials_are_dropped_wherever_they_are():
    data = {"request": {"password": "p"}, "user_data": {"user_id": "5", "name": "N", "kong_token": "t"},
            "list": [{"syncode": "s", "keep": 1}]}
    assert li.strip_secrets(data) == {"user_data": {"user_id": "5", "name": "N"}, "list": [{"keep": 1}]}


def har(*entries):
    return {"log": {"entries": list(entries)}}


def entry(url, body, posted="", base64_body=False):
    text = json.dumps(body)
    content = {"text": base64.b64encode(text.encode()).decode(), "encoding": "base64"} if base64_body else {"text": text}
    return {"request": {"url": url, "postData": {"text": posted}}, "response": {"content": content}}


def test_the_init_reply_is_found_in_a_har():
    login = {"user_data": {"money": "5"}}
    found = li.login_from_har(har(entry("https://x/api.php?message=getUserAccount", {"user_data": {}}),
                                  entry("https://x/api.php?message=init", login),
                                  entry("https://x/api.php?message=initX", {"user_data": {"money": "9"}})))
    assert found == login
    posted = li.login_from_har(har(entry("https://x/api.php", login, posted="user_id=1&message=init&x=2",
                                         base64_body=True)))
    assert posted == login
    with pytest.raises(ValueError):
        li.login_from_har(har(entry("https://x/api.php?message=getUserAccount", login)))


def test_items_as_the_save_keeps_them():
    assert li.items_from_login([]) == {}
    assert li.items_from_login({"2000": {"id": "2000", "number": "500"}, "x": {"id": "bad"}}) == {"2000": 500}


@pytest.mark.needs_game
def test_a_new_accounts_login_is_a_new_save():
    p = li.player_from_login(init_body(), T, defs())
    assert (p.gold, p.war_bonds, len(p.cards)) == (100, 50, 5) and p.achievements["done"] == []


@pytest.mark.needs_game
def test_a_progressed_login_carries_over():
    body = json.loads(json.dumps(init_body()))
    ud = body["user_data"]
    ud.update(money="12345", tokens="678", salvage="90", hunting_elo="900", level="6", name="Real Name")
    first = next(iter(body["user_cards"]))
    body["user_cards"][first]["is_locked"] = "1"
    body["user_items"] = {"2000": {"id": "2000", "number": "500"}}
    body["mission_completions"] = {"8": {"complete": "1", "stars": "1"}}
    body["request"] = {"password": "real one"}
    body["user_achievements"] = {"103": {"achievement_id": "103", "progress": "4", "status": "0"}}
    p = li.player_from_login(body, T, defs())
    assert (p.gold, p.war_bonds, p.salvage, p.arena["rating"], p.arena["level"]) == (12345, 678, 90, 900, 6)
    assert p.cards[first].get("is_locked") == 1 and p.items == {"2000": 500}
    done = set(p.achievements["done"])
    assert {101, 102} <= done and 103 not in done                 # before the listed one: paid already
    assert 401 in done                                             # its goal (mission 8) already met
    assert p.achievements["counts"]["upgrade_level"] == 4          # the listed one's progress


@pytest.mark.needs_game
def test_the_converter_writes_a_save(tmp_path, monkeypatch, capsys):
    (tmp_path / "init.json").write_text(json.dumps({**init_body(), "request": {"password": "x"}}), "utf-8")
    out = tmp_path / "save.json"
    monkeypatch.setattr(sys, "argv", ["convert_login.py", str(tmp_path / "init.json"), "-o", str(out)])
    convert_login.main()
    save = json.loads(out.read_text("utf-8"))
    assert save["player"]["gold"] == 100 and "password" not in out.read_text("utf-8")
    assert "100 gold, 50 war bonds, 5 cards" in capsys.readouterr().out
