"""Tests for event boxes (boxes.py) and the content file (content.py): the 13th Hour Box,
the last box the game ran, as the example."""

import json
import random
from collections import Counter

import pytest

import boxes
import content
import game_api as ga
import test_arena as ta
import test_battle as tb
import test_packs as tp

T = tb.T
HOUR13 = 8099


def cards():
    return ta.real_cards()


def rarity(cid):
    return cards()[cid].rarity


@pytest.fixture(scope="module")
def all_boxes():
    return boxes.load_boxes(ga.ASSET_DIR)


def test_the_13th_hour_box(all_boxes):
    box = all_boxes[HOUR13]
    assert (box.name, box.wb_cost, len(box.cards), box.size, box.max_completes, box.tiered) == \
        ("13th Hour Box", 60, 22, 28, None, False)
    copies = Counter()
    for cid, n in box.cards:
        copies[rarity(cid)] += n
    assert copies == {6: 6, 5: 12, 4: 5, 3: 5}            # Mythic quads x1, Vindicator quads x2, ...
    assert boxes.rarity_table(box, rarity) == ((3, 69.0), (4, 25.0), (5, 5.0), (6, 1.0))   # Epic -> Mythic
    assert [(d.id, d.wb_cost, len(d.min_rarities), d.repeatable) for d in box.deals] == [
        (1, 10, 1, False), (2, 30, 2, False), (3, 60, 1, False), (4, 250, 5, False), (5, 400, 10, False),
        (6, 600, 10, True)]
    assert box.picture("anim_picture") == "phobosrising_box"


def test_pulls_empty_the_box_then_it_starts_over(all_boxes):
    box = all_boxes[HOUR13]
    state, rng = boxes.new_state(), random.Random(1)
    got = [boxes.pull(box, state, rarity, rng) for _ in range(box.size - 1)]
    assert sum(boxes.remaining(box, state).values()) == 1
    got.append(boxes.pull(box, state, rarity, rng))
    assert Counter(got) == Counter({cid: n for cid, n in box.cards})     # every copy, once
    assert state == {"received": {}, "completes": 1, "deals": []}          # round 2: full again
    boxes.pull(box, state, rarity, rng)
    assert sum(state["received"].values()) == 1


def test_the_first_pull_follows_the_box_table(all_boxes):
    box = all_boxes[HOUR13]
    rng, n = random.Random(5), 40_000
    got = Counter(rarity(boxes.pull(box, boxes.new_state(), rarity, rng)) for _ in range(n))
    for r, pct in boxes.rarity_table(box, rarity):
        assert abs(100 * got[r] / n - pct) < max(0.5, pct * 0.15), r


def test_a_rarity_with_nothing_left_drops_out(all_boxes):
    box = all_boxes[HOUR13]
    epics = [cid for cid, _ in box.cards if rarity(cid) == 3]
    state = {"received": {str(c): 1 for c in epics}, "completes": 0, "deals": []}
    rng = random.Random(2)
    assert all(rarity(boxes.pull(box, dict(state, received=dict(state["received"])), rarity, rng)) != 3
               for _ in range(300))


def test_a_cache_sells_out_after_its_rounds(all_boxes):
    cache = next(b for b in all_boxes.values() if b.max_completes and not b.tiered)
    state, rng = boxes.new_state(), random.Random(4)
    for _ in range(cache.size * cache.max_completes):
        boxes.pull(cache, state, rarity, rng)
    assert boxes.sold_out(cache, state)
    with pytest.raises(boxes.BoxError, match="sold out"):
        boxes.pull(cache, state, rarity, rng)


def test_deals_once_per_round(all_boxes):
    box = all_boxes[HOUR13]
    state, rng = boxes.new_state(), random.Random(6)
    assert boxes.next_deal(box, state).id == 1
    boxes.buy_deal(box, box.deals[0], state, rarity, rng)
    assert state["deals"] == [1] and boxes.next_deal(box, state).id == 2
    with pytest.raises(boxes.BoxError, match="once per box"):
        boxes.buy_deal(box, box.deals[0], state, rarity, rng)
    vindi = boxes.buy_deal(box, box.deals[2], state, rarity, rng)          # "Vindicator+ Deal"
    assert len(vindi) == 1 and rarity(vindi[0]) >= 5
    for d in box.deals[1:5]:
        if d.id not in state["deals"]:
            boxes.buy_deal(box, d, state, rarity, rng)
    assert boxes.next_deal(box, state).id == 6 and boxes.next_deal(box, state).repeatable   # Buy x10 stays


def test_a_deal_is_all_or_nothing(all_boxes):
    box = all_boxes[HOUR13]
    high = [cid for cid, _ in box.cards if rarity(cid) >= 5]
    state = {"received": {str(c): n for c, n in box.cards if c in high}, "completes": 0, "deals": []}
    before = json.dumps(state, sort_keys=True)
    with pytest.raises(boxes.BoxError, match="Vindicator or better"):
        boxes.buy_deal(box, box.deals[2], state, rarity, random.Random(1))
    assert json.dumps(state, sort_keys=True) == before


# ---- the content file ---------------------------------------------------------------------------------

def test_the_content_file_is_written_with_the_defaults(tmp_path):
    store = content.ContentStore(tmp_path / "content.toml")
    c = store.load()
    assert (tmp_path / "content.toml").read_text("utf-8") == content.TEMPLATE
    assert (c.packs, c.boxes, c.recorded_extras, c.problems) == (("basic", "epic"), (), True, [])
    assert [(p.mini_pack, p.gold, p.war_bonds, p.card) for p in c.promos] == [
        (23, None, 5, None), (49, 400, None, None), (48, 2000, None, None), (None, None, None, 70584)]


def test_problems_in_the_content_file(tmp_path):
    c = content.parse('[store]\npacks = ["basic", "gold"]\npromos = [{ mini_pack = 23, gold = 1, war_bonds = 2 }]\n'
                      '[boxes]\non_offer = [8099, "x"]\n')
    assert c.packs == ("basic",) and c.promos == () and c.boxes == (8099,)
    assert len(c.problems) == 3
    (tmp_path / "content.toml").write_text("[store\n", "utf-8")
    broken = content.ContentStore(tmp_path / "content.toml").load()
    assert broken.packs == ("basic", "epic") and "isn't valid TOML" in broken.problems[0]


def sell(tmp_path, text, bonds=500):
    (tmp_path / "content.toml").write_text(text, "utf-8")
    return tp.shop(tmp_path, bonds=bonds)


def test_the_default_store_is_the_recorded_one(tmp_path):
    api = tp.shop(tmp_path)
    r, _ = tb.ask(api, "init")
    strip = lambda ps: [{k: v for k, v in p.items() if k not in ("start_time", "end_time")} for p in ps]
    assert strip(r["store_promos"]) == strip(tp.INIT["store_promos"])
    assert r["store_banner"] == tp.INIT["store_banner"] and r["store_packs"] == tp.INIT["store_packs"]


def test_the_content_file_sets_the_store(tmp_path):
    api = sell(tmp_path, '[store]\npacks = ["basic"]\npromos = [{ mini_pack = 11, war_bonds = 9 },'
                         ' { mini_pack = 49, gold = 300 }]\nrecorded_extras = false\n')
    r, _ = tb.ask(api, "getStoreData")
    assert list(r["store_packs"]) == ["1"]
    assert [(p["item_id"], p.get("gold_cost"), p.get("wb_cost")) for p in r["store_promos"]] == [
        (49, "300", None), (11, None, "9")]
    imperial = r["store_promos"][1]
    assert imperial["name"] == "Imperial Box" and len(imperial["available_cards"]) == 6   # 5 epics, 1 legendary
    assert r["store_banner"]["promo_id"] == 11       # no Blight Pack now: the file's first promo leads
    reply, how = tb.ask(api, "buyStorePromoGold", item_id="49", item_type="3", expected_cost="300")
    assert reply["result"] and "for 300 gold" in how
    reply, how = tb.ask(api, "buyPackTokens", pack_id="1000")
    assert reply["result"] is False and reply["result_message"] == ["That pack isn't in the store."]


def test_a_box_in_the_store(tmp_path):
    api = sell(tmp_path, "[boxes]\non_offer = [8099]\n")
    r, _ = tb.ask(api, "init")
    entry = r["store_promos"][0]
    assert (entry["item_type"], entry["item_id"], entry["name"], entry["wb_cost"]) == (8, 8099, "13th Hour Box", "60")
    assert entry["end_time"] - T == 7 * 86400 and entry["box_discount"]["name"] == "13th Hour Deal"
    assert r["store_banner"]["promo_id"] == 8099
    assert "boxes: 13th Hour Box (8099)" in api.store_report()


def test_buying_from_a_box(tmp_path):
    api = sell(tmp_path, "[boxes]\non_offer = [8099]\n", bonds=130)
    before = api.player().card_count
    r, how = tb.ask(api, "buyStorePromoTokens", item_id="8099", item_type="8", expected_cost="60")
    assert r["result"] and len(r["new_cards"]) == 1 and r["user_data"]["tokens"] == "70"
    cid = int(r["new_cards"][0])
    assert how.startswith("bought 13th Hour Box for 60 war bonds (130 -> 70): ")
    assert how.endswith("13th Hour Box round 1: 1/28 bought")
    p = api.player()
    assert p.card_count == before + 1 and p.boxes["8099"]["received"] == {str(cid): 1}
    entry = next(e for e in r["store_promos"] if e["item_id"] == 8099)
    assert entry["cards_received"] == {str(cid): "1"}                     # strings: the game reads numbers as 0
    assert entry["card"][0] == {"id": "62902", "count": 1} and len(entry["card"]) == 22
    assert "store_banner" in r          # the game only takes store_promos from a reply that has it
    tb.ask(api, "buyStorePromoTokens", item_id="8099", item_type="8", expected_cost="60")
    r, how = tb.ask(api, "buyStorePromoTokens", item_id="8099", item_type="8", expected_cost="60")
    assert r["result"] is False and r["result_message"] == [
        "Not enough war bonds: the 13th Hour Box costs 60, and you have 10."]
    r, _ = tb.ask(api, "buyStorePromoTokens", item_id="8099", item_type="8", expected_cost="50")
    assert r["result_message"] == ["The price has changed: the 13th Hour Box costs 60 war bonds now."]
    r, _ = tb.ask(api, "buyStorePromoTokens", item_id="101", item_type="8", expected_cost="60")
    assert r["result_message"] == ["That box isn't in the store."]


def test_buying_a_box_deal(tmp_path):
    api = sell(tmp_path, "[boxes]\non_offer = [8099]\n", bonds=100)
    # The deal's Buy, as the game sends it: the box's price as expected_cost.
    r, how = tb.ask(api, "buyStorePromoTokens", item_id="8099", item_type="8", box_discount_id="1",
                    expected_cost="60")
    assert r["result"] and len(r["new_cards"]) == 1 and r["user_data"]["tokens"] == "90"
    assert how.startswith("bought 13th Hour Box: 13th Hour Deal for 10 war bonds (100 -> 90): ")
    assert api.player().boxes["8099"]["deals"] == [1]
    entry = next(e for e in r["store_promos"] if e["item_id"] == 8099)
    assert entry["box_discount"]["box_discount_id"] == 2
    r, _ = tb.ask(api, "buyStorePromoTokens", item_id="8099", item_type="8", box_discount_id="1",
                  expected_cost="60")
    assert r["result"] is False and r["result_message"] == ["The 13th Hour Deal can be bought once per box."]
    r, how = tb.ask(api, "buyStorePromoTokens", item_id="8099", item_type="8", box_discount_id="2",
                    expected_cost="60")
    assert r["result"] and len(r["new_cards"]) == 2 and r["user_data"]["tokens"] == "60"


def test_an_unknown_box_is_reported(tmp_path):
    api = sell(tmp_path, "[boxes]\non_offer = [1]\n")
    report = api.store_report()
    assert "boxes: none" in report and "there's no box 1" in report


# ---- tiered boxes ---------------------------------------------------------------------------------

DEVOUR = 128


def test_the_devour_tier(all_boxes):
    box = all_boxes[DEVOUR]
    assert (box.name, box.tiered, box.cards, len(box.tiers), box.size) == ("Devour Tier", True, (), 11, 101)
    assert [sum(n for _, n in t) for t in box.tiers] == [10] * 10 + [1]
    assert box.tiers[-1] == ((6243, 1),) and cards()[6243].name == "Darkon"        # the featured Vindicator
    assert boxes.rarity_table(box, rarity, box.tiers[0]) == ((2, 79.0), (3, 15.0), (4, 5.0), (5, 1.0))
    assert boxes.rarity_table(box, rarity, box.tiers[-1]) == ((5, 100.0),)


def test_tiers_open_in_turn(all_boxes):
    box = all_boxes[DEVOUR]
    state, rng = boxes.new_state(), random.Random(3)
    first = [boxes.pull(box, state, rarity, rng) for _ in range(10)]
    assert Counter(first) == Counter(dict(box.tiers[0]))                 # tier 1, every copy
    assert (boxes.tier(box, state), state["received"], boxes.bought(box, state)) == (1, {}, 10)
    rest = [boxes.pull(box, state, rarity, rng) for _ in range(90)]
    assert boxes.tier(box, state) == 10 and boxes.remaining(box, state) == {6243: 1}
    assert boxes.pull(box, state, rarity, rng) == 6243                   # the last pull: Darkon
    every = Counter()
    for t in box.tiers:
        every.update(dict(t))
    assert Counter(first + rest + [6243]) == every
    assert (state["tier"], state["completes"], state["received"]) == (0, 1, {})   # round 2, from tier 1


def test_a_tiered_box_in_the_store(tmp_path):
    api = sell(tmp_path, "[boxes]\non_offer = [128]\n", bonds=1000)
    r, _ = tb.ask(api, "init")
    entry = r["store_promos"][0]
    assert (entry["item_type"], entry["item_id"], entry["name"], entry["cards_received"]) == (10, 128, "Devour Tier", {})
    assert len(entry["subbox"]) == 11 and entry["subbox"][-1] == {"card": [{"id": "6243", "count": 1}]}
    assert "card" not in entry and "box_discount" not in entry
    for _ in range(11):
        r, how = tb.ask(api, "buyStorePromoTokens", item_id="128", item_type="10", expected_cost="60")
        assert r["result"]
    assert how.endswith("Devour Tier round 1, tier 2 of 11: 11/101 bought")
    entry = next(e for e in r["store_promos"] if e["item_id"] == 128)
    received = entry["cards_received"]
    assert received["1"] == {str(c): str(n) for c, n in api._boxes()[DEVOUR].tiers[0]}   # tier 1 sent full
    assert list(received) == ["1", "2"] and sum(int(n) for n in received["2"].values()) == 1
    assert api.player().war_bonds == 1000 - 11 * 60
