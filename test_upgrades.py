"""Tests for the Upgrade screen (upgrades.py): upgrades, salvage, locks and fusion,
against the recorded upgradeCard / salvageL1CommonCards replies."""

import json

import pytest

import game_api as ga
import upgrades
from player_state import PlayerStore
import test_battle as tb

T = tb.T
CALLS = json.loads((ga.FIXTURE_DIR / "replay.json").read_text("utf-8"))["calls"]
REDEEMER, DEVOURER, LOCUST, BANSHEE = 434, 69, 363, 174        # a three-card recipe, all level 3
BLACKROCK_DRILLER, BLACKROCK = 6751, 6750                       # an upgrade fusion: 2 x Blackrock (level 6)


def recorded(message, n=0):
    return [c for c in CALLS if c["message"] == message][n]["body"]


def owned(user_cards):
    return {k: int(v["num_owned"]) for k, v in user_cards.items() if int(v["num_owned"])}


def deck_of(user_decks):
    d = next(iter(user_decks.values()))
    return int(d["commander_id"]), {k: int(v) for k, v in d["cards"].items()}


def workshop(tmp_path, cards, sp=15, deck=None, commander=1000):
    """A save with these cards, SP and deck (the active deck)."""
    api = ga.GameApi(fixture_dir=ga.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: T)
    tb.ask(api, "init")
    p = api.player()
    p.cards = {str(k): {"num_owned": n, "num_used": 0} for k, n in cards.items()}
    p.salvage = sp
    d = p.decks[api._active_deck_id()]
    d["commander_id"], d["cards"] = commander, {str(k): n for k, n in (deck or {}).items()}
    api.player_store.save(p)
    return api


def test_the_levels_file_matches_the_wiki():
    levels = upgrades.load_levels(ga.ASSET_DIR)
    costs = {r: [levels[(r, l)].sp_cost for l in range(1, 7) if (r, l) in levels] for r in (1, 2, 3, 4)}
    assert costs == {1: [5, 15, None], 2: [5, 15, 30, None], 3: [5, 15, 30, 75, 150, None],
                     4: [5, 15, 30, 75, 150, None]}
    assert [levels[(r, 1)].salvage for r in (1, 2, 3, 4, 5, 6)] == [1, 5, 20, 40, 80, 120]
    assert levels[(3, 1)].fusion_salvage == {1: 80, 2: 180}


def test_the_recorded_upgrades_and_salvage(tmp_path):
    """The recording: Cyrus 1 -> 2 (10 SP), salvage every spare level-1 common (+10 SP),
    Trap Setter 1 -> 2 (5 SP). Each reply's cards, deck and SP match the recorded ones."""
    after_cyrus, after_salvage, after_trap = (recorded("upgradeCard", 0), recorded("salvageL1CommonCards"),
                                              recorded("upgradeCard", 1))
    cards = owned(after_cyrus["user_cards"])
    cards.pop("1001")
    cards["1000"] = 1
    _, deck = deck_of(after_cyrus["user_decks"])
    api = workshop(tmp_path, cards, sp=15, deck=deck, commander=1000)
    earned, gold = api.player().cards_earned, api.player().gold

    r, how = tb.ask(api, "upgradeCard", card_id="1000", in_deck="1")
    assert how.startswith("upgraded Cyrus to level 2 for 10 SP (SP 15 -> 5)") and r["new_card_id"] == "1001"
    # As recorded: "Rise to Power" done and paid (200 gold), and its next link at 2 of 3.
    assert r["completed_achievements"] == after_cyrus["completed_achievements"]
    assert api.player().gold == gold + 200
    assert {k: (v["progress"], v["max_progress"]) for k, v in r["user_achievements"].items() if k in ("101", "102")}         == {"102": ("2", "3")}
    assert owned(r["user_cards"]) == owned(after_cyrus["user_cards"])
    assert r["user_cards"]["1000"]["num_owned"] == "0"                 # kept at 0, as recorded
    assert deck_of(r["user_decks"]) == deck_of(after_cyrus["user_decks"])
    assert int(r["user_data"]["salvage"]) == after_cyrus["user_data"]["salvage"] == 5   # the login has "15"
    assert api.player().cards_earned == earned + 1                    # recorded: 22 -> 23

    r, how = tb.ask(api, "salvageL1CommonCards")
    assert how.startswith("salvaged 10 level-1 common cards for 10 SP (SP 5 -> 15)")
    assert r["completed_achievements"] == after_salvage["completed_achievements"]     # "Scavenger", 300 gold
    assert api.player().gold == gold + 500
    assert r["user_achievements"]["502"]["progress"] == after_salvage["user_achievements"]["502"]["progress"] == "10"
    assert owned(r["user_cards"]) == owned(after_salvage["user_cards"])
    assert int(r["user_data"]["salvage"]) == 15 and r["buyback_data"] == [] and "new_card_id" not in r

    r, how = tb.ask(api, "upgradeCard", card_id="12", in_deck="1")
    assert how.startswith("upgraded Trap Setter to level 2 for 5 SP (SP 15 -> 10)")          # level 2 again: nothing new
    assert owned(r["user_cards"]) == owned(after_trap["user_cards"])
    assert deck_of(r["user_decks"]) == deck_of(after_trap["user_decks"])
    assert int(r["user_data"]["salvage"]) == 10


def test_upgrade_refusals(tmp_path):
    api = workshop(tmp_path, {12: 1, 14: 1, 1000: 1}, sp=4, deck={12: 1})
    r, _ = tb.ask(api, "upgradeCard", card_id="12", in_deck="1")
    assert r["result"] is False and r["result_message"] == ["Not enough SP: upgrading Trap Setter costs 5, and you have 4."]
    r, _ = tb.ask(api, "upgradeCard", card_id="14", in_deck="0")
    assert r["result_message"] == ["Trap Setter is at its highest level."]
    r, _ = tb.ask(api, "upgradeCard", card_id="13", in_deck="0")
    assert r["result_message"] == ["You don't have Trap Setter."]


def test_a_spare_copy_or_the_deck_copy(tmp_path):
    api = workshop(tmp_path, {12: 2}, sp=100, deck={12: 1})
    r, _ = tb.ask(api, "upgradeCard", card_id="12", in_deck="0")      # the spare one
    assert owned(r["user_cards"]) == {"12": 1, "13": 1} and deck_of(r["user_decks"])[1] == {"12": 1}
    r, _ = tb.ask(api, "upgradeCard", card_id="12", in_deck="1")      # the deck's
    assert owned(r["user_cards"]) == {"13": 2} and deck_of(r["user_decks"])[1] == {"13": 1}


def test_salvage_values_and_the_cap(tmp_path):
    api = workshop(tmp_path, {6763: 2, 2: 1, 1000: 1}, sp=5990)       # Demon of Embers: epic, 20 SP
    r, how = tb.ask(api, "salvageCard", card_id="6763")
    assert how.startswith("salvaged Demon of Embers for 10 SP (SP 5990 -> 6000)")        # stops at the cap...
    assert owned(r["user_cards"])["6763"] == 1                                  # ...the card goes anyway
    r, _ = tb.ask(api, "salvageCard", card_id="1000")
    assert r["result_message"] == ["Commanders can't be salvaged."]


def test_locks_keep_cards_from_salvage(tmp_path):
    api = workshop(tmp_path, {75: 1, 4: 2, 9: 1}, sp=0, deck={9: 1})
    r, how = tb.ask(api, "setCardLock", card_id="75", locked="1")
    assert how.startswith("locked Scavenger") and r["user_cards"]["75"]["is_locked"] == "1"
    r, _ = tb.ask(api, "salvageCard", card_id="75")
    assert r["result_message"] == ["Scavenger is locked."]
    r, how = tb.ask(api, "salvageL1CommonCards")
    assert how.startswith("salvaged 2 level-1 common cards for 2 SP (SP 0 -> 2)")         # not the lock, not the deck
    assert owned(r["user_cards"]) == {"75": 1, "9": 1}
    tb.ask(api, "setCardLock", card_id="75", locked="0")
    assert not api.player().is_locked("75")
    saved = PlayerStore(tmp_path / "player.json").load()                       # kept in the save
    assert saved.salvage == 2 and saved.cards["75"].get("is_locked") is None


def test_bulk_rares_skip_fused_and_upgraded_cards(tmp_path):
    api = workshop(tmp_path, {320: 2, 321: 1, 1000: 1}, sp=0)          # Imperial APC level 1 x2, level 2
    r, how = tb.ask(api, "salvageL1RareCards")
    assert how.startswith("salvaged 2 level-1 rare cards for 10 SP (SP 0 -> 10)") and owned(r["user_cards"]) == {"321": 1,
                                                                                                        "1000": 1}


def test_fusion(tmp_path):
    api = workshop(tmp_path, {DEVOURER: 1, LOCUST: 1, BANSHEE: 1, BLACKROCK: 2}, deck={BANSHEE: 1})
    r, how = tb.ask(api, "fuseCard", card_id=str(REDEEMER))
    assert how.startswith("fused Redeemer from Devourer + Locust Swarm + Banshee") and r["new_card_id"] == str(REDEEMER)
    assert owned(r["user_cards"]) == {str(BLACKROCK): 2, str(REDEEMER): 1}
    assert deck_of(r["user_decks"])[1] == {}                           # Banshee left the deck with the fusion
    r, how = tb.ask(api, "fuseCard", card_id=str(BLACKROCK_DRILLER))
    assert how.startswith("fused Blackrock Driller from Blackrock x2") and owned(r["user_cards"]) == {str(REDEEMER): 1,
                                                                                             str(BLACKROCK_DRILLER): 1}
    r, _ = tb.ask(api, "fuseCard", card_id=str(REDEEMER))
    assert r["result_message"] == ["You don't have the cards to fuse Redeemer: Devourer, Locust Swarm, Banshee."]


@pytest.mark.parametrize("message,params", [("upgradeCard", {"card_id": "x"}), ("salvageCard", {"card_id": "99999999"})])
def test_unknown_cards(tmp_path, message, params):
    api = workshop(tmp_path, {12: 1})
    r, _ = tb.ask(api, message, **params)
    assert r["result"] is False and r["result_message"] == ["That card doesn't exist."]
