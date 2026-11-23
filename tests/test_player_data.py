"""Tests for the player's own data (game_api/player_data_calls.py): the daily crate
(daily_crate.py), the deck slot and decks, the name, and the refresh calls."""

import json
import random

import pytest

import game_api as ga
import paths
from helpers import T, ask, init_body

pytestmark = pytest.mark.needs_game        # every test here needs the game files

DAY = 86400


def game(tmp_path, bonds=2000, gold=50000):
    clock = [T]
    api = ga.GameApi(fixture_dir=paths.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: clock[0])
    api.rng_factory = lambda: random.Random(1)
    ask(api, "init")
    p = api.player()
    p.war_bonds, p.gold = bonds, gold
    api.player_store.save(p)
    return api, clock


# ---- the crate -----------------------------------------------------------------------------------

def test_a_new_saves_crate_opens_as_recorded(tmp_path):
    api, clock = game(tmp_path)
    r, _ = ask(api, "getUserData")
    assert int(r["daily_bonus_time"]) - T == int(init_body()["daily_bonus_time"]) - int(init_body()["time"]) == 1799
    r, how = ask(api, "useDailyBonus")
    assert r["result"] is False and r["result_message"] == ["The crate isn't ready yet: 0h 29m left."]


def test_opening_the_crate_day_after_day(tmp_path):
    api, clock = game(tmp_path, gold=0)
    clock[0] += 1800
    r, how = ask(api, "useDailyBonus")
    assert how == "opened the crate (day 1 in a row): 100 gold; the next one in 24h"
    assert r["bonus_result"] == {"bonus": {"money": 100, "tokens": 0, "salvage": 0}, "rewards": []}
    assert r["user_data"]["money"] in (100, "100") and r["new_cards"] == [] and r["daily_bonus"] == 1
    assert int(r["daily_bonus_time"]) == clock[0] + DAY
    clock[0] += DAY + 3600                                     # the next day, within a day of it being ready
    r, how = ask(api, "useDailyBonus")
    assert how.startswith("opened the crate (day 2 in a row): 150 gold")
    clock[0] += 3 * DAY                                        # skipped two days: back to day 1
    r, how = ask(api, "useDailyBonus")
    assert how.startswith("opened the crate (day 1 in a row): 100 gold")


def test_the_crate_rewards_come_from_the_content_file(tmp_path):
    (tmp_path / "content.toml").write_text("[daily_bonus]\nevery_hours = 4\nrewards = [{ card = 2999, sp = 5 }]\n",
                                           "utf-8")
    api, clock = game(tmp_path)
    clock[0] += 1800
    r, how = ask(api, "useDailyBonus")
    assert how == "opened the crate (day 1 in a row): 5 SP, Salvage Crate; the next one in 4h"
    assert r["new_cards"] == ["2999"] and r["user_cards"]["2999"]["num_owned"] == "1"
    assert r["bonus_result"]["bonus"]["card"] == 2999


# ---- the deck slot and decks ---------------------------------------------------------------------

def test_deck_slots_and_the_active_deck(tmp_path):
    api, _ = game(tmp_path, bonds=1000)
    r, how = ask(api, "buyUpgrade", upgrade_id="101")
    assert how == "bought Extra Deck Slot for 100 war bonds; 2 deck slots"
    assert r["max_decks"] == 2 and r["store_upgrades"]["2"]["wb"]["wb_cost"] == "100"
    r, _ = ask(api, "setDeckCards", deck_id="2", commander_id="1000", cards=json.dumps({"2": 1}), dominion_id="0")
    assert r["result"]
    r, how = ask(api, "setActiveDeck", deck_id="2")
    assert how == "active deck: 2" and str(r["user_data"]["active_deck"]) == "2" and api._active_deck_id() == "2"
    r, how = ask(api, "setDefenseDeck", deck_id="1")
    assert how == "defence deck: 1"
    r, _ = ask(api, "setActiveDeck", deck_id="3")
    assert r["result_message"] == ["Deck 3 isn't one of your decks."]
    for _ in range(3):
        ask(api, "buyUpgrade", upgrade_id="101")
    assert api.player().max_decks == 5                          # max_decks_available
    r, _ = ask(api, "buyUpgrade", upgrade_id="101")
    assert r["result_message"] == ["You already have every Extra Deck Slot upgrade."]
    r, _ = ask(api, "getStoreData")
    assert r["store_upgrades"]["2"] == []


# ---- name and refreshes --------------------------------------------------------------------------

def test_the_name(tmp_path):
    api, _ = game(tmp_path)
    r, how = ask(api, "setUserName", name="  Rook  ")
    assert how == "name: 'Offline Commander' -> 'Rook'" and r["user_data"]["name"] == "Rook"
    assert str(r["user_data"]["name_changes"]) == "1"
    r, _ = ask(api, "setUserName", name=" ")
    assert r["result"] is False


def test_refresh_calls(tmp_path):
    api, _ = game(tmp_path, gold=1234)
    r, _ = ask(api, "getUserData")
    assert r["result"] and str(r["user_data"]["money"]) == "1234"
    r, _ = ask(api, "getUserCards")
    assert set(r["user_cards"]) == set(api.player().cards)
    r, _ = ask(api, "getUserDecks")
    assert "1" in r["user_decks"]
    r, _ = ask(api, "getCardData")
    assert r["card_data"] == []
    r, how = ask(api, "getVersionNumber")
    assert r["version"] == "80.560" and how == "answered from the save (the envelope)"
