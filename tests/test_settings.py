"""Tests for the gold bonuses (settings.py): the defaults, switching them, what the
game is told at login, and what missions and AI rivals pay."""

import json
import random

import pytest

import game_api as ga
from common import paths
from helpers import T, ask, mission_api, play_mission, run_player_cli, init_body
from rules.settings import Bonus, Settings, SettingsStore, apply_to_login


def test_defaults_and_switching(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    s = store.load()
    assert (s.mission_gold, s.arena_gold) == (Bonus(7), Bonus(3))      # no file: the defaults
    assert s.describe() == "missions x7, arena x3"
    s.mission_gold.on = False
    s.arena_gold.multiplier = 5
    store.save(s)
    s = SettingsStore(tmp_path / "settings.json").load()
    assert (s.mission_gold.effective, s.mission_gold.multiplier, s.arena_gold.effective) == (1, 7, 5)
    assert s.describe() == "missions x7 (off: x1), arena x5"


@pytest.mark.parametrize("bad", [0, -2, 1.5, "7", True])
def test_a_multiplier_is_a_whole_number(tmp_path, bad):
    (tmp_path / "settings.json").write_text(json.dumps({"mission_gold": {"multiplier": bad}}), "utf-8")
    with pytest.raises(ValueError):
        SettingsStore(tmp_path / "settings.json").load()


def popups(body):
    return [e.get("name") or e.get("banner_picture") for e in body["event_popups"]]


def test_the_login_data_shows_the_bonus_in_use():
    body = json.loads(json.dumps(init_body()))
    apply_to_login(body, Settings(mission_gold=Bonus(5)))
    assert body["gold_multiplier_missions"]["multiplier"] == "5"
    assert body["event_popup"]["gold_multiplier"] == "5" and body["event_popups"][0]["gold_multiplier"] == "5"
    body = json.loads(json.dumps(init_body()))
    assert popups(body)[:3] == ["Sunset Gold Bonus", "4x_missiongold_halfsize_banner.png", "Platinum Crate"]
    apply_to_login(body, Settings(mission_gold=Bonus(7, on=False)))
    assert body["gold_multiplier_missions"]["multiplier"] == "1"
    assert "Sunset Gold Bonus" not in popups(body) and "4x_missiongold_halfsize_banner.png" not in popups(body)
    assert popups(body)[0] == "Platinum Crate" and body["event_popup"] is None


def test_missions_pay_by_the_setting(tmp_path):
    api = mission_api(tmp_path)
    ask(api, "init")
    r, how = play_mission(api, "2")
    assert how.endswith("paid 84 gold, card 2 (into the deck)")         # 12 x 7
    s = api.settings()
    s.mission_gold.on = False
    api.settings_store.save(s)
    r, how = play_mission(api, "2")
    assert how.endswith("(you won); paid 12 gold")
    assert ask(api, "init")[0]["gold_multiplier_missions"]["multiplier"] == "1"
    s.mission_gold.on, s.mission_gold.multiplier = True, 2
    api.settings_store.save(s)
    assert play_mission(api, "2")[1].endswith("(you won); paid 24 gold")


@pytest.mark.needs_game
def test_rivals_pay_by_the_setting(tmp_path):
    api = ga.GameApi(fixture_dir=paths.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: T, battle_engine=True)
    api.rng_factory = lambda: random.Random(1)                           # a shuffle that wins the tutorial fight
    ask(api, "init")
    assert ask(api, "getHuntingTargets")[0]["hunting_targets"]["-1"]["gold"] == 75     # 25 x 3, as recorded
    s = api.settings()
    s.arena_gold.on = False
    api.settings_store.save(s)
    assert ask(api, "getHuntingTargets")[0]["hunting_targets"]["-1"]["gold"] == 25
    s.arena_gold.on, s.arena_gold.multiplier = True, 4
    api.settings_store.save(s)
    r, how = ask(api, "startHuntingBattle", target_user_id="-1")
    bid = str(r["battle_data"]["battle_id"])
    r, how = ask(api, "playCard", battle_id=bid, card_uid=str(api.battle["engine"].battle.sides[0].hand[0]),
                    skip="1")
    assert "paid 100 gold" in how and r["battle_data"]["rewards"][0]["gold"] == 100


def test_the_commands(tmp_path, monkeypatch, capsys):
    code, out = run_player_cli(tmp_path, monkeypatch, capsys)
    assert "  gold bonus missions x7, arena x3" in out
    _, out = run_player_cli(tmp_path, monkeypatch, capsys, "mission-gold", "off")
    assert out.startswith("Missions pay their listed gold x1 (the bonus, x7, is off).")
    _, out = run_player_cli(tmp_path, monkeypatch, capsys, "arena-gold", "4")
    assert out == "AI rivals pay 25 gold x4.\n"
    _, out = run_player_cli(tmp_path, monkeypatch, capsys, "mission-gold", "on")
    assert out.startswith("Missions pay their listed gold x7.")
    code, _ = run_player_cli(tmp_path, monkeypatch, capsys, "arena-gold", "0")
    assert "whole number" in str(code)
    assert json.loads((tmp_path / "state" / "settings.json").read_text("utf-8")) == \
        {"mission_gold": {"multiplier": 7, "on": True}, "arena_gold": {"multiplier": 4, "on": True}}
