"""Tests for buying packs (packs.py): the card pool, the published drop rates, the
Basic Pack, its gold bundles and the Epic Pack, and the replies."""

import random
from collections import Counter

import pytest

import game_api as ga
import packs
from helpers import shop, ask, real_cards, init_body

RECORDED_DRAWS = [9, 6763, 75, 4, 2024, 112, 109, 45, 314, 169, 2100, 9, 109, 172, 115, 100, 197, 104,
                  4, 317, 2024, 71, 254, 504, 115]   # every recorded basic draw, both accounts


def cards():
    return real_cards()


@pytest.mark.needs_game
def test_the_pool_is_the_level_1_base_set():
    pool = packs.base_pool(cards())
    assert {r: len(ids) for r, ids in pool.items()} == {1: 55, 2: 25, 3: 25, 4: 5, 5: 1}
    every = {c for ids in pool.values() for c in ids}
    assert all(c in every for c in RECORDED_DRAWS)


@pytest.mark.needs_game
def test_rarities_follow_the_published_table():
    rng = random.Random(7)
    n = 200_000
    got = Counter(cards()[packs.draw(cards(), "basic", rng)].rarity for _ in range(n))
    for rarity, pct in packs.RATES["basic"]:
        assert abs(100 * got[rarity] / n - pct) < max(0.4, pct * 0.4), rarity
    epic = Counter(cards()[packs.draw(cards(), "epic", rng)].rarity for _ in range(20_000))
    assert set(epic) <= {3, 4, 5} and abs(epic[3] / 200 - 70) < 2


def test_the_store_packs():
    basic = packs.store_pack(init_body(), 1)
    assert (basic.name, basic.draws, basic.gold, basic.war_bonds) == ("Basic Pack", (("basic", 1),), 100, None)
    epic = packs.store_pack(init_body(), 1000)
    assert (epic.draws, epic.war_bonds) == ((("basic", 3), ("epic", 1)), 20)
    for seed in range(30):
        opened = packs.open_pack(epic, cards(), random.Random(seed))
        assert len(opened) == 4 and cards()[opened[-1]].rarity >= 3              # the guaranteed one, last
    assert packs.store_pack(init_body(), 7) is None


def test_the_gold_bundles_come_from_the_mini_pack_file():
    mini = packs.load_mini_packs(ga.ASSET_DIR)
    x4, x20 = packs.promo_pack(init_body(), mini, 49), packs.promo_pack(init_body(), mini, 48)
    assert (x4.name, x4.draws, x4.gold) == ("Basic Pack x4", (("basic", 4),), 400)
    assert (x20.draws, x20.gold) == ((("basic", 20),), 2000)
    assert packs.promo_pack(init_body(), mini, 104) is None                    # a fusion pack: not ours to open


def test_new_cards_data_counts_each_card():
    assert packs.new_cards_data([75, 4, 75]) == [{"id": "75", "number": 2}, {"id": "4", "number": 1}]


@pytest.mark.needs_game
def test_buying_a_basic_pack(tmp_path):
    api = shop(tmp_path)
    before = api.player().card_count
    r, how = ask(api, "buyPackGold", pack_id="1")
    assert how.startswith("bought Basic Pack for 100 gold (1000 -> 900): ")
    assert r["result"] is True and len(r["new_cards"]) == 1 and "user_decks" not in r and "new_cards_data" not in r
    cid = r["new_cards"][0]
    assert int(r["user_cards"][cid]["num_owned"]) >= 1 and int(r["user_data"]["money"]) == 900
    assert api.player().card_count == before + 1 and int(r["user_data"]["cards_earned"]) == 6
    assert "store_packs" in r                                          # the recorded reply's shape


@pytest.mark.needs_game
def test_during_the_tutorial_the_card_goes_into_the_deck(tmp_path):
    api = shop(tmp_path, tutorial="3")                                 # the tutorial's "Buy a card"
    r, how = ask(api, "buyPackGold", pack_id="1")
    assert how.endswith("; into the deck (tutorial)") and r["new_cards"][0] in r["user_decks"]["1"]["cards"]


@pytest.mark.needs_game
def test_the_bundles_and_the_epic_pack(tmp_path):
    api = shop(tmp_path, gold=3000)
    r, how = ask(api, "buyStorePromoGold", item_id="49", item_type="3", expected_cost="400")
    assert how.startswith("bought Basic Pack x4 for 400 gold") and len(r["new_cards"]) == 4
    assert sum(e["number"] for e in r["new_cards_data"]) == 4
    r, how = ask(api, "buyStorePromoGold", item_id="48", item_type="3", expected_cost="2000")
    assert len(r["new_cards"]) == 20 and int(r["user_data"]["money"]) == 600
    r, how = ask(api, "buyPackTokens", pack_id="1000")
    assert how.startswith("bought Epic Pack for 20 war bonds (50 -> 30)") and len(r["new_cards"]) == 4
    assert real_cards()[int(r["new_cards"][-1])].rarity >= 3


@pytest.mark.needs_game
def test_refusals(tmp_path):
    api = shop(tmp_path, gold=50, bonds=10)
    r, how = ask(api, "buyPackGold", pack_id="1")
    assert r["result"] is False and r["result_message"] == ["Not enough gold: the Basic Pack costs 100, and you have 50."]
    assert "user_data" in r
    r, _ = ask(api, "buyPackTokens", pack_id="1000")
    assert r["result_message"] == ["Not enough war bonds: the Epic Pack costs 20, and you have 10."]
    r, _ = ask(api, "buyStorePromoGold", item_id="49", item_type="3", expected_cost="300")
    assert r["result_message"] == ["The price has changed: the Basic Pack x4 costs 400 gold now."]
    r, _ = ask(api, "buyPackTokens", pack_id="1")
    assert r["result_message"] == ["The Basic Pack isn't sold for war bonds."]
    p = api.player()
    p.gold = 1000
    p.cards["1"] = {"num_owned": 1600, "num_used": 0}
    api.player_store.save(p)
    r, _ = ask(api, "buyPackGold", pack_id="1")
    assert r["result_message"][0].startswith("Your inventory is full: ")
    assert api.player().gold == 1000                                   # nothing taken


# ---- faction packs (the Blight Pack) -------------------------------------------------------------

@pytest.mark.needs_game
def test_a_faction_pool_and_its_epic_column():
    pool = packs.base_pool(cards(), 3)                                  # Bloodthirsty
    assert {r: len(ids) for r, ids in pool.items()} == {1: 11, 2: 5, 3: 5, 4: 1}
    assert packs.rates("epic", pool) == ((3, 70.0), (4, 25.0))         # no Vindicator to give
    rng = random.Random(2)
    got = Counter(cards()[packs.draw(cards(), "epic", rng, 3)].rarity for _ in range(40_000))
    assert abs(got[3] / 400 - 73.7) < 1.5 and set(got) == {3, 4}


def test_the_blight_pack_is_mini_pack_23():
    mini = packs.load_mini_packs(ga.ASSET_DIR)
    blight = packs.promo_pack(init_body(), mini, 23)
    assert (blight.name, blight.draws, blight.war_bonds, blight.gold, blight.faction) == \
        ("Blight Pack", (("epic", 2),), 5, None, 3)


@pytest.mark.needs_game
def test_buying_the_blight_pack(tmp_path):
    api = shop(tmp_path, bonds=7)
    r, how = ask(api, "buyStorePromoTokens", item_id="23", item_type="3", expected_cost="5")
    assert how.startswith("bought Blight Pack for 5 war bonds (7 -> 2): ")
    got = [cards()[int(c)] for c in r["new_cards"]]
    assert len(got) == 2 and all(c.faction == 3 and c.rarity >= 3 and c.set == 1000 for c in got)
    assert sum(e["number"] for e in r["new_cards_data"]) == 2 and r["user_data"]["tokens"] in ("2", 2)
    r, _ = ask(api, "buyStorePromoTokens", item_id="23", item_type="3", expected_cost="5")
    assert r["result_message"] == ["Not enough war bonds: the Blight Pack costs 5, and you have 2."]
    r, _ = ask(api, "buyStorePromoGold", item_id="23", item_type="3", expected_cost="5")
    assert r["result_message"] == ["The Blight Pack isn't sold for gold."]
