"""Tests for items (items.py, game_api/items.py, player_state.py): the data, the formats, using
items, the energy bar's refill with one, and the save."""

import pytest
import json

import game_api as ga
import items
from battle.cards import CardDB
from player_state import SAVE_VERSION, PlayerStore
from helpers import call, login, server

DEFS = items.load(ga.ASSET_DIR)


# ---- the data and formats ------------------------------------------------------------------------

@pytest.mark.needs_game
def test_the_data():
    assert len(DEFS) == 72 and 2003 not in DEFS                          # the commented-out ones left out
    epic, vindi, cache = DEFS[items.EPIC_SHARD], DEFS[items.VINDICATOR_SHARD], DEFS[items.SALVAGE_CACHE]
    assert (epic.name, epic.cost, epic.pack["distribution"]) == ("Epic Shard", 500, {"basic": 3, "epic": 1})
    assert (vindi.cost, vindi.pack["distribution"]) == (1000, {"vindicator_strict": 1})
    assert (cache.cost, cache.sp, cache.pack) == (20, 40, None)
    assert DEFS[items.ENERGY_REFILL].refill and DEFS[items.ENERGY_REFILL].cost == 1
    assert not DEFS[items.COMMANDER_RESPEC].usable


def test_formats():
    assert items.user_items_json({}) == []                                  # as recorded
    assert items.user_items_json({"2001": 3050, "2000": 500}) == {
        "2000": {"id": 2000, "number": 500}, "2001": {"id": 2001, "number": 3050}}
    assert items.new_items_json({2002: 1600, 2000: 0}) == [{"id": 2002, "number": 1600}]


def test_the_inventory():
    inv = {}
    items.add(inv, 2000, 500)
    items.add(inv, "2000", 250)
    assert items.owned(inv, 2000) == 750
    items.take(inv, 2000, 750)
    assert inv == {}


@pytest.mark.needs_game
def test_what_can_be_used():
    inv = {"2000": 499, "2100": 1, "10": 1}
    for item_id, words in ((2000, "You need x500 Epic Shard to use this item."),
                           (2100, "The Commander Respec can't be used."),
                           (10, "The Massacre Refill can't be used."),       # no effect given
                           (2200, "The Neuro Pack Shard can't be used.")):
        try:
            items.check_use(DEFS.get(item_id), inv)
            raise AssertionError(item_id)
        except items.ItemError as e:
            assert str(e) == words
    items.check_use(DEFS[items.ENERGY_REFILL], {"1": 1})


# ---- through the server --------------------------------------------------------------------------

def setup(tmp_path, owned):
    api, clock = server(tmp_path, engine=True)
    _, ann = login(api)
    call(api, ann, "init")
    store = api._store_for(ann["user_id"])
    p = store.load()
    p.items = {str(k): n for k, n in owned.items()}
    store.save(p)
    return api, ann, store


@pytest.mark.needs_game
def test_the_login_and_inventory(tmp_path):
    api, ann, _ = setup(tmp_path, {})
    assert call(api, ann, "init")[0]["user_items"] == []
    api, ann, _ = setup(tmp_path / "b", {2001: 1200})
    assert call(api, ann, "init")[0]["user_items"] == {"2001": {"id": 2001, "number": 1200}}
    r, _ = call(api, ann, "getUserItems")
    assert r["result"] is True and r["user_items"]["2001"]["number"] == 1200


@pytest.mark.needs_game
def test_shards_open_packs(tmp_path):
    api, ann, store = setup(tmp_path, {2000: 750, 2002: 1000})
    cards = CardDB.load(ga.ASSET_DIR)
    r, how = call(api, ann, "consumeItem", item_id="2000")
    assert r["result"] is True, how
    assert len(r["new_cards"]) == 4 and cards[int(r["new_cards"][-1])].rarity >= 3     # one Epic+
    assert r["user_items"]["2000"]["number"] == 250 and r["new_items"] == []
    assert all(store.load().owned(c) for c in r["new_cards"])
    assert call(api, ann, "consumeItem", item_id="2000")[0]["result_message"] == \
        ["You need x500 Epic Shard to use this item."]
    r, _ = call(api, ann, "consumeItem", item_id="2002")
    assert r["new_cards"] == ["17239"] and "2002" not in r["user_items"]      # a Vindicator Reactor


@pytest.mark.needs_game
def test_salvage_cache_and_refusals(tmp_path):
    api, ann, store = setup(tmp_path, {2004: 25, 2100: 1})
    sp = store.load().salvage or 0
    r, how = call(api, ann, "consumeItem", item_id="2004")
    assert r["result"] is True and int(r["user_data"]["salvage"]) == sp + 40, how
    assert r["new_items"] == [{"id": items.SALVAGE_POINTS, "number": 40}]        # shown as "Salvage Points"
    assert r["user_items"] == {"2004": {"id": 2004, "number": 5}, "2100": {"id": 2100, "number": 1}}
    p = store.load()
    p.items["2004"], p.salvage = 20, api._cap_sp()
    store.save(p)
    assert call(api, ann, "consumeItem", item_id="2004")[0]["result_message"] == \
        [f"Your SP is full: {api._cap_sp()} of {api._cap_sp()}."]
    assert call(api, ann, "consumeItem", item_id="2100")[0]["result_message"] == ["The Commander Respec can't be used."]
    assert call(api, ann, "consumeItem", item_id="99999")[0]["result_message"] == ["That item doesn't exist."]


@pytest.mark.needs_game
def test_energy_refills(tmp_path):
    api, ann, store = setup(tmp_path, {1: 2})
    p = store.load()
    energy, wb = p.energy, p.war_bonds
    r, how = call(api, ann, "consumeItem", item_id="1")                         # the inventory's Use
    assert r["result"] is True and int(r["user_data"]["energy"]) == energy + p.max_energy, how
    r, how = call(api, ann, "buyEnergyRefillTokens")                            # the energy bar's +
    assert "an Energy Refill (0 left)" in how and r["user_items"] == []
    p = store.load()
    assert (p.energy, p.war_bonds) == (energy + 2 * p.max_energy, wb)          # no war bonds spent
    _, how = call(api, ann, "buyEnergyRefillTokens")
    assert "war bonds" in how and store.load().war_bonds < wb                   # none left: war bonds


@pytest.mark.needs_game
def test_the_save(tmp_path):
    _, _, store = setup(tmp_path, {2001: 10})
    saved = json.loads(store.path.read_text("utf-8"))
    assert saved["version"] == SAVE_VERSION == 13 and saved["player"]["items"] == {"2001": 10}
    saved["version"], saved["player"]["items"] = 11, None                     # an older save
    del saved["player"]["items"]
    store.path.write_text(json.dumps(saved), "utf-8")
    assert PlayerStore(store.path).load().items == {}
