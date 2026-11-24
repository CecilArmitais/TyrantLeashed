"""Tests for the arena extras: refreshing the rivals (buyRivalsRefresh), buying stamina
(buyStaminaRefillTokens), Promote (unlockCommander) and the Reports tab
(getBattleReports, revenge), checked against the account's two recorded unlocks where
there are captures."""

import json
import random

import pytest

import game_api as ga
from battle.recorded import capture_calls
from common import paths
from helpers import T, arena_api, arena_fight, ask, real_cards
from rules import arena
from rules.player_state import Player, arena_from_start

ENVELOPE = {"time", "version", "daily_bonus_time", "guild_donations_enabled", "guild_supplies_enabled", "request"}


def api_with(tmp_path, engine=True, **arena_values):
    api = ga.GameApi(fixture_dir=paths.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: T + 100, battle_engine=engine)
    ask(api, "init")
    p = api.player()
    p.arena.update(arena_values)
    api.player_store.save(p)
    return api


# ---- stamina -------------------------------------------------------------------------------------

@pytest.mark.needs_game
def test_a_stamina_refill_fills_stamina_for_war_bonds(tmp_path):
    api = api_with(tmp_path, engine=False, stamina=4)
    r, how = ask(api, "buyStaminaRefillTokens")
    assert how == "stamina refill: 4 -> 15, 30 war bonds (50 -> 20)"            # to the max, not over it
    assert r["result"] is True and r["user_data"]["stamina"] in ("15", 15) and r["user_data"]["tokens"] == "20"
    assert r["user_data"]["last_stam_update_time"] in (str(T + 100), T + 100)
    r, how = ask(api, "buyStaminaRefillTokens")
    assert r["result"] is False and r["result_message"] == ["Not enough war bonds: a refill costs 30, and you have 20."]
    assert "user_data" in r and api.player().arena["stamina"] == 15


# ---- refreshing the rivals -----------------------------------------------------------------------

@pytest.mark.needs_game
def test_refreshing_rolls_new_rivals_for_gold(tmp_path):
    api = api_with(tmp_path)
    r, how = ask(api, "buyRivalsRefresh")
    assert r["result"] is False and how == "refused rivals refresh: no arena win yet (the tutorial rival)"
    p = api.player()
    p.arena.update(hunting_wins=3, rating=60, rival_order=[-9, -10])
    api.player_store.save(p)
    before = ask(api, "getHuntingTargets")[0]["hunting_targets"]
    r, how = ask(api, "buyRivalsRefresh")
    assert how.startswith("rivals refreshed for 50 gold (100 -> 50)")
    after = r["hunting_targets"]
    assert list(after) == [str(i) for i in range(-11, -20, -1)] and list(after) != list(before)
    assert sorted(int(t["hunting_elo"]) for t in after.values()) == sorted(int(t["hunting_elo"]) for t in before.values())
    assert all(t["name"] in arena.NPC_NAMES and t["gold"] == 75 for t in after.values())
    assert r["user_data"]["money"] == "50" and "arena_battle_info" in r
    assert api.player().arena["rival_order"] == [] and api.player().arena["refreshes"] == 1
    assert ask(api, "getHuntingTargets")[0]["hunting_targets"] == after          # it stays until the next one
    r, how = ask(api, "buyRivalsRefresh")                                       # 50 -> 0
    assert ask(api, "buyRivalsRefresh")[0]["result_message"] == \
        ["Not enough gold: a refresh costs 50, and you have 0."]


@pytest.mark.needs_game
def test_a_refresh_above_150_moves_ratings_a_little():
    cards = real_cards()
    a = arena_from_start({"user_data": {}}, T)
    a.update(hunting_wins=20, rating=735)
    plain = arena.rival_list(a, cards)
    arena.refresh_rivals(a)
    refreshed = arena.rival_list(a, cards)
    assert refreshed != plain
    for p, r in zip(plain, refreshed):
        assert abs(int(p["hunting_elo"]) - int(r["hunting_elo"])) in (0, 50)


# ---- Promote -------------------------------------------------------------------------------------

@pytest.mark.needs_game
def test_unlocking_a_commander(tmp_path):
    api = api_with(tmp_path, engine=False, promotion_points=2)
    cards = real_cards()
    r, how = ask(api, "unlockCommander", commander_id="1007")           # Malika, tier 1
    assert set(r) - ENVELOPE == {"new_commander", "new_cards", "user_data", "user_cards", "user_decks"}   # as recorded
    assert r["new_commander"] == "1007" and how.startswith("unlocked Malika (1007) for a promotion point (1 left)")
    new = [cards[int(c)] for c in r["new_cards"]]
    # The wiki's tier-1 bonus, as both recorded unlocks: a rare then three commons, the faction's level-1 base set.
    assert [c.rarity for c in new] == [2, 1, 1, 1] and len({c.id for c in new}) == 4
    assert all((c.faction, c.set, c.level, c.id) == (2, 1000, 1, c.base_id) for c in new)
    ud = r["user_data"]
    # cards earned: 5 + the commander + 4
    assert (int(ud["promotion_points"]), int(ud["cards_earned"]), int(ud["commander_unlock_rarity"])) == (1, 10, 2)
    assert r["user_cards"]["1007"]["num_owned"] == "1"
    for cid, why in (("1007", "you already have that commander"), ("1000", "you already have that commander"),
                     ("1041", "unlock every tier 1 commander first"), ("1297", "that isn't a commander Promote unlocks")):
        r, how = ask(api, "unlockCommander", commander_id=cid)
        assert r["result"] is False and r["result_message"] == [f"Can't unlock that commander: {why}."], cid
    assert ask(api, "unlockCommander", commander_id="abc")[0]["result_message"] == ["Invalid commander."]
    ask(api, "unlockCommander", commander_id="1011")
    assert ask(api, "unlockCommander", commander_id="1015")[0]["result_message"] == \
        ["Can't unlock that commander: you have no promotion points."]


@pytest.mark.needs_game
def test_tiers_open_in_order_with_their_bonuses():
    cards = real_cards()
    p = Player(gold=0, war_bonds=0, energy=0, energy_updated=T, cards={"1000": {"num_owned": 1, "num_used": 0}},
                  arena=dict(arena_from_start({"user_data": {}}, T), promotion_points=20))
    rng = random.Random(1)
    for base in (1007, 1011, 1015):
        arena.unlock_commander(p, base, cards, rng)
    assert p.arena["unlock_rarity"] == 2
    arena.unlock_commander(p, 1019, cards, rng)                          # the last of tier 1
    assert p.arena["unlock_rarity"] == 3
    bonus = arena.unlock_commander(p, 1041, cards, rng)                  # Vyander, tier 2
    assert [cards[c].rarity for c in bonus] == [2, 2, 1, 1]
    with pytest.raises(arena.UnlockError):
        arena.unlock_commander(p, 1029, cards, rng)                      # tier 3 needs all of tier 2
    for base in (1003, 1053, 1035, 1047):
        arena.unlock_commander(p, base, cards, rng)
    assert p.arena["unlock_rarity"] == 4
    bonus = arena.unlock_commander(p, 1029, cards, rng)                  # Typhon Vex, tier 3: four rares
    assert [cards[c].rarity for c in bonus] == [2, 2, 2, 2] and {cards[c].faction for c in bonus} == {2}


@pytest.mark.skipif(not paths.RAW_CAPTURES_DIR.is_dir(), reason="no captures here")
def test_the_recorded_unlocks_follow_the_rule():
    """The account's two unlocks (nothing from them is printed): the bonus cards fit the
    tier-1 rule, one promotion point each, cards_earned +5."""
    cards = real_cards()
    seen = 0
    for session in sorted(paths.RAW_CAPTURES_DIR.iterdir()):
        prev = None
        for m, params, body in capture_calls(session):
            if not isinstance(body, dict):
                continue
            if m == "unlockCommander":
                base = int(params["commander_id"])
                new = [cards[int(c)] for c in body["new_cards"]]
                rarities, factions = [c.rarity for c in new], {c.faction for c in new}
                assert body["new_commander"] == str(base) and rarities == [2, 1, 1, 1]
                assert factions == {cards[base].faction} and {c.set for c in new} == {1000}
                ud = body["user_data"]
                points, earned = int(ud["promotion_points"]), int(ud["cards_earned"])
                assert (points, earned) == (int(prev["promotion_points"]) - 1, int(prev["cards_earned"]) + 5)
                assert "result" not in body
                seen += 1
            if isinstance(body.get("user_data"), dict):
                prev = body["user_data"]
    assert seen == 2


# ---- reports -------------------------------------------------------------------------------------

@pytest.mark.needs_game
def test_reports_list_the_players_fights_newest_first(tmp_path):
    api = arena_api(tmp_path)
    ask(api, "init")
    r, how = ask(api, "getBattleReports")
    assert how == "battle reports: 0" and r["battle_reports"] == [] and "result" not in r
    arena_fight(api, "-1", skip="0")                                       # the tutorial win
    r, _ = ask(api, "startHuntingBattle", target_user_id="-9")
    bid = str(r["battle_data"]["battle_id"])
    ask(api, "forfeitBattle", battle_id=bid)                         # a surrender: a loss
    reports = ask(api, "getBattleReports")[0]["battle_reports"]
    assert [list(r) for r in reports] == [["user_id", "sender_id", "type", "data", "time", "status", "got_revenge"]] * 2
    lost, won = reports
    assert (lost["sender_id"], lost["type"], lost["status"], lost["got_revenge"]) == ("-9", "2", "1", "-1")
    assert lost["data"] == ('{"enemy_id":"-9","enemy_name":"HOWLWOLFF","victory":0,"host_id":"1000001",'
                            f'"battle_id":"{bid}","rewards":{{"user_id":"1000001","gold":19,"sp":0,'
                            '"rating_change":0,"item":[],"lp_change":0}}')
    data = json.loads(won["data"])
    assert (data["enemy_id"], data["victory"], data["rewards"]["gold"], data["rewards"]["rating_change"]) == \
        ("-1", 1, 75, 75)


def test_reports_keep_the_latest_16():
    a = arena_from_start({"user_data": {}}, T)
    rival = {"user_id": -4, "name": "GunGore", "gold": 75, "hunting_elo": "25"}
    for n in range(20):
        arena.file_report(a, rival, True, {"user_id": "1", "gold": 75}, T + n)
    assert len(a["reports"]) == 16 and a["reports"][0]["time"] == T + 19


@pytest.mark.skipif(not paths.RAW_CAPTURES_DIR.is_dir(), reason="no captures here")
def test_reports_have_the_recorded_shape():
    """The account's reports (nothing from them is printed): same keys, same order, same
    kinds of values, and every one a fight the account started."""
    a = arena_from_start({"user_data": {}}, T)
    arena.file_report(a, {"user_id": -4, "name": "GunGore", "gold": 75}, False,
                      {"user_id": "1000001", "gold": 19, "sp": 0, "rating_change": 0, "item": [], "lp_change": 0}, T)
    ours = arena.reports_json(a, 1000001)[0]
    seen = 0
    for session in sorted(paths.RAW_CAPTURES_DIR.iterdir()):
        for m, params, body in capture_calls(session):
            if m != "getBattleReports" or not isinstance(body, dict):
                continue
            for rep in body["battle_reports"]:
                same_keys = list(rep) == list(ours)
                kinds = (rep["type"], rep["status"], rep["got_revenge"])
                data, mine = json.loads(rep["data"]), json.loads(ours["data"])
                data_keys = (list(data), list(data["rewards"]))
                host_is_owner = data["host_id"] == rep["user_id"] and data["enemy_id"] == rep["sender_id"]
                compact = ", " not in rep["data"] and ": " not in rep["data"]
                assert same_keys and kinds == ("2", "1", "-1") and host_is_owner and compact
                assert data_keys == (list(mine), list(mine["rewards"]))
                seen += 1
    assert seen == 32
