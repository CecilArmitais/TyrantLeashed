"""Tests for the store's other promos (packs.py, promos.py): every mini pack kind, single
cards with rising prices, and the real-money bundles sold for gold or war bonds."""

import random
from pathlib import Path

import pytest

import content
import game_api as ga
import packs
import promos
import upgrades
from helpers import ask, real_cards, shop


@pytest.fixture(scope="module")
def data():
    return real_cards(), packs.load_mini_packs(Path(ga.ASSET_DIR)), upgrades.load_recipes(Path(ga.ASSET_DIR))


def opened(data, mid, seed=1):
    cards, mini, recipes = data
    return packs.open_pack(packs.mini_pack(mini[mid]), cards, random.Random(seed), recipes)


@pytest.mark.needs_game
def test_nearly_every_mini_pack_opens(data):
    cards, mini, recipes = data
    shut = sorted(mid for mid, mp in mini.items() if not packs.openable(packs.mini_pack(mp), cards, recipes))
    assert len(mini) == 394 and shut == ["238", "239", "240", "94", "96"]    # sets 4700 (empty) and 4000 (1 card)


@pytest.mark.needs_game
def test_each_kind_of_pack(data):
    cards, mini, recipes = data
    level = opened(data, "52")                                       # Level Pack: two level-4 Epic+ base cards
    assert len(level) == 2 and all(cards[c].level == 4 and cards[c].set == 1000 and cards[c].rarity >= 3 for c in level)
    swift = opened(data, "78")                                       # Swift Pack: event cards with Cooldown: 1
    assert all(cards[c].delay == 1 and cards[c].set == 3000 for c in swift)
    triton = mini["59"]                                              # an "exclusive" pack: its 26 listed cards
    assert set(opened(data, "59")) <= set(triton["cards"]) and len(triton["cards"]) == 26
    quadra = opened(data, "2000")                                    # Legendary Quadra: four copies of one card
    assert len(quadra) == 4 and len(set(quadra)) == 1
    complete = opened(data, "104")                                   # Righteous Complete: a fused card's recipe
    assert any(sorted(complete) == sorted(r for r, k in res for _ in range(k)) for res in recipes.values())
    assert opened(data, "5000") == [26]                              # Nimbus: a single card
    coin = packs.mini_pack(mini["51"])                               # Salvage Coin: 25 SP for 45 war bonds
    assert (coin.sp, coin.war_bonds, packs.open_pack(coin, cards, random.Random(1))) == (25, 45, [])
    assert packs.mini_pack(mini["1001"]).fixed == (37610,)           # Wavecrasher (type 13)


@pytest.mark.needs_game
def test_rising_prices():
    tabitha = {"purchase1": "80", "purchase2": "110", "purchase3": "140", "purchase4": "170", "scalar": "30"}
    assert [packs.scaling_price(tabitha, n) for n in range(6)] == [80, 110, 140, 170, 200, 230]
    table, cards = packs.load_scaling_pack(Path(ga.ASSET_DIR))
    assert table[6] == tabitha and table[3]["purchase1"] == "10" and 70584 in cards and len(cards) == 25


def test_content_file_promo_kinds():
    c = content.parse('[store]\npromos = [{ mini_pack = 51 }, { card = 70584 }, { bundle = 191, gold = 5000 },'
                      ' { bundle = 201607 }, { card = 1, bundle = 2, gold = 1 }]\n')
    assert [p.kind for p in c.promos] == ["mini_pack", "card", "bundle"] and len(c.problems) == 2


def store(api):
    r, _ = ask(api, "getStoreData")
    return {str(p["item_id"]): p for p in r["store_promos"]}


@pytest.mark.needs_game
def test_a_single_card_gets_dearer(tmp_path):
    api = shop(tmp_path, bonds=500)
    tabitha = store(api)["13835"]                                    # the recorded entry's id
    assert (tabitha["card"], tabitha["wb_cost"], tabitha["type"]) == ("70584", "80", 13)
    r, how = ask(api, "buyStorePromoTokens", item_type="3", item_id="13835", expected_cost="80")
    assert r["new_cards"] == ["70584"] and how.startswith("bought Tabitha's Trooper for 80 war bonds"), how
    assert next(p for p in r["store_promos"] if p["item_id"] == 13835)["wb_cost"] == "110"
    r, how = ask(api, "buyStorePromoTokens", item_type="3", item_id="13835", expected_cost="80")
    assert r["result_message"] == ["The price has changed: the Tabitha's Trooper costs 110 war bonds now."]
    ask(api, "buyStorePromoTokens", item_type="3", item_id="13835", expected_cost="110")
    assert api.player().war_bonds == 500 - 80 - 110 and store(api)["13835"]["wb_cost"] == "140"


@pytest.mark.needs_game
def test_mini_packs_at_the_files_prices(tmp_path):
    (tmp_path / "content.toml").write_text('[store]\npromos = [{ mini_pack = 51 }, { mini_pack = 2000 },'
                                           ' { mini_pack = 1001 }]\n', "utf-8")
    api = shop(tmp_path, bonds=1000)
    offered = store(api)
    assert (offered["51"]["wb_cost"], offered["2000"]["wb_cost"], offered["1001"]["wb_cost"]) == ("45", "180", "200")
    before = api.player().salvage
    r, how = ask(api, "buyStorePromoTokens", item_type="3", item_id="51", expected_cost="45")
    assert r["new_items"] == [{"id": 1999, "number": 25}] and api.player().salvage == before + 25, how
    r, _ = ask(api, "buyStorePromoTokens", item_type="3", item_id="2000", expected_cost="180")
    assert len(r["new_cards"]) == 4 and len(set(r["new_cards"])) == 1 and r["new_cards_data"][0]["number"] == 4
    r, _ = ask(api, "buyStorePromoTokens", item_type="3", item_id="1001", expected_cost="200")
    assert r["new_cards"] == ["37610"] and store(api)["1001"]["wb_cost"] == "325"


@pytest.mark.needs_game
def test_bundles_for_gold(tmp_path):
    (tmp_path / "content.toml").write_text('[store]\npromos = [{ bundle = 191, gold = 500 },'
                                           ' { bundle = 200080, war_bonds = 10 }]\n', "utf-8")
    api = shop(tmp_path, gold=1000, bonds=20)
    offered = store(api)
    crate, elite = offered["191"], offered["200080"]
    assert (crate["item_type"], crate["gold_cost"], crate["card"]) == (7, "500", 224) and "web_bundle" not in crate
    p = api.player()
    bonds, energy, slots, salvage = p.war_bonds, p.max_energy, p.max_cards, p.salvage
    r, how = ask(api, "buyStorePromoGold", item_type="7", item_id="191", expected_cost="500")
    assert r["new_cards"] == ["224"] and r["new_items"] == [{"id": 1999, "number": 50}], how
    assert (api.player().war_bonds, api.player().salvage) == (bonds + 250, salvage + 50)
    r, how = ask(api, "buyStorePromoTokens", item_type="7", item_id="200080", expected_cost="10")
    p = api.player()
    assert (p.war_bonds, p.max_energy, p.max_cards, p.max_salvage) == (bonds + 250 - 10 + 100, energy + 50,
                                                                        slots + 150, 6000 + 350), how
    assert r["max_energy"] == energy + 50 and "200080" not in store(api)               # one time only
    r, _ = ask(api, "buyStorePromoTokens", item_type="7", item_id="200080", expected_cost="10")
    assert r["result_message"] == ["You already bought the Elite Pack: it's one time only."]
    r, _ = ask(api, "buyStorePromoGold", item_type="7", item_id="191", expected_cost="500")
    assert r["result"] is True                                                         # the crate isn't one-time
