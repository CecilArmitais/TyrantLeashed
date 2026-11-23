"""Tests for phase 5: Skip (skip=1), Surrender (forfeitBattle), stamina and the arena
(rivals, their decks, ratings, levels), checked against the recorded arena fights."""

import json
import random

import pytest

import arena
import game_api as ga
from battle.cards import CardDB
from battle.engine import Battle, BattleError, RandomChooser, Setup, shuffled_deck
from player_state import LEVELS, Player, PlayerStore, SAVE_VERSION, arena_from_start, level_for, level_info
from helpers import arena_api, arena_fight as fight, real_cards, DB, T, ask, mission_api, recorded_calls



# ---- Skip and Surrender ---------------------------------------------------------------------------

def long_battle(seed=1):
    db = DB
    me = [db[1]] * 3 + [db[3]] * 3
    enemy = [db[3]] * 4
    rng = random.Random(seed)
    return Battle(Setup(db[1000], shuffled_deck(me, 1, rng), db[1300], shuffled_deck(enemy, 101, rng)),
                  chooser=RandomChooser(random.Random(seed)))


def test_play_out_runs_to_the_end_in_one_reply():
    b = long_battle()
    b.start()
    turns = b.play_out(b.sides[0].hand[1])
    # Every turn with events, from the first: a stall's last turn (50) can have none.
    assert b.over and min(turns) == 1 and max(turns) <= b.turn
    assert sorted(turns) == [t for t in range(1, b.turn + 1) if b.turns.get(t)]
    with pytest.raises(BattleError):
        b.play_out()


def test_play_out_takes_the_named_card_first():
    b = long_battle(2)
    b.start()
    want = b.sides[0].hand[2]
    b.play_out(want)
    assert b.played[0][0] == want


def test_forfeit_is_a_loss():
    b = long_battle()
    b.start()
    b.forfeit()
    assert b.over and b.winner == 0
    with pytest.raises(BattleError):
        b.forfeit()


def test_skip_and_surrender_through_the_server(tmp_path):
    api = mission_api(tmp_path)
    ask(api, "init")
    r, _ = ask(api, "startMission", mission_id="3")              # a sure loss in 50 turns
    bid = str(r["battle_data"]["battle_id"])
    r, how = ask(api, "playCard", battle_id=bid, card_uid="1", skip="1")
    assert how.endswith("(skipped to the end) (you lost); paid 28 gold")
    assert r["battle_data"]["winner"] == 0 and set(r["battle_data"]["turn"]) == {str(t) for t in range(1, 51)}
    r, _ = ask(api, "startMission", mission_id="3")
    bid = str(r["battle_data"]["battle_id"])
    r, how = ask(api, "forfeitBattle", battle_id=bid)
    assert how == "surrendered mission 3; paid 28 gold"
    bd = r["battle_data"]
    assert (bd["winner"], bd["mission_id"], bd["rewards"]) == (0, "3", [{"user_id": "1000001", "gold": 28}])
    assert bd["turn"] == {"1": []} and "user_data" in r and "current_campaigns" in r
    assert api.battle is None
    assert ask(api, "forfeitBattle", battle_id=bid)[1] == "UNSUPPORTED: there is no battle in progress."


@pytest.mark.needs_game
def test_surrendering_a_replayed_battle(tmp_path):
    api = ga.GameApi(state_dir=tmp_path)                                 # the real fixtures, replaying
    api.respond("init", {"message": "init"})
    r, _ = api.respond("startMission", {"message": "startMission", "mission_id": "7"})
    r, how = api.respond("forfeitBattle", {"message": "forfeitBattle", "battle_id": r["battle_data"]["battle_id"]})
    assert how == "surrendered mission 7; paid 28 gold"
    assert r["battle_data"]["winner"] == 0 and r["battle_data"]["rewards"][0]["gold"] == 28


# ---- levels and the save ----------------------------------------------------------------------------

def test_levels_follow_the_rating():
    # The wiki's 20 ranks.
    assert [level_for(r) for r in (0, 24, 25, 149, 150, 349, 350, 599, 600, 999, 1000, 1749, 1750, 124999, 125000,
                                   10 ** 6)] == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 19, 20, 20]
    assert [r[0] for r in LEVELS] == list(range(1, 21))
    assert level_info(1) == ("Rookie", 0, 25) and level_info(2) == ("Infantryman", 25, 150)   # recorded
    assert level_info(5) == ("Veteran", 600, 1000)                   # the account's level 5, recorded
    assert level_info(4) == ("Marshall", 350, 600) and level_info(6) == ("Vanguard Officer", 1000, 1750)
    assert level_info(20) == ("Mythic Tyrant", 125000, 125000)       # the last: nothing after it
    # The account's real rivals: level 4 at 366-595, level 6 at 1024-1663.
    assert {level_for(r) for r in (366, 375, 405, 465, 479, 480, 585, 595)} == {4}
    assert {level_for(r) for r in (1024, 1259, 1302, 1309, 1624, 1663)} == {6}


def test_the_save_keeps_the_arena_and_refills_stamina(tmp_path):
    start = json.loads((ga.FIXTURE_DIR / "init.json").read_text("utf-8"))["body"]
    clock = [T]
    store = PlayerStore(tmp_path / "p.json", clock=lambda: clock[0])
    p = store.current(start, 60)
    assert p.arena == {"stamina": 15, "stamina_updated": T, "max_stamina": 15, "rating": 0, "hunting_wins": 0,
                       "level": 1, "promotion_points": 0, "hunts_fought": 0, "rival_order": [], "refreshes": 0,
                       "unlock_rarity": 2, "reports": [], "matched": [], "matched_for": []}
    p.arena["stamina"], p.arena["stamina_updated"] = 10, T
    store.save(p)
    clock[0] = T + 900 * 3 + 100                                         # 3 points, 100 s towards the 4th
    p = store.current(start, 60, stamina_every=900)
    assert (p.arena["stamina"], p.arena["stamina_updated"]) == (13, T + 2700)
    body = {"user_data": {"stamina": "10", "last_stam_update_time": "0", "hunting_elo": "0", "level": "1",
                          "level_name": "Rookie", "base_xp": "0", "next_xp": "25", "promotion_points": 0},
            "max_stamina": 15}
    PlayerStore.apply(body, p)
    assert body["user_data"] == {"stamina": "13", "last_stam_update_time": str(T + 2700), "hunting_elo": "0",
                                 "level": "1", "level_name": "Rookie", "base_xp": "0", "next_xp": "25",
                                 "promotion_points": 0}


def test_an_older_save_gets_the_arena(tmp_path):
    api = mission_api(tmp_path)
    ask(api, "init")
    f = tmp_path / "state" / "player.json"
    data = json.loads(f.read_text("utf-8"))
    del data["player"]["arena"]
    data["version"] = 4
    f.write_text(json.dumps(data), "utf-8")
    api2 = ga.GameApi(fixture_dir=tmp_path / "fixtures", asset_dir=tmp_path / "assets", clock=lambda: T + 100)
    assert api2.player().arena["stamina"] == 15
    assert json.loads(f.read_text("utf-8"))["version"] == SAVE_VERSION


# ---- rivals ---------------------------------------------------------------------------------------------



def recorded_targets():
    return [c["body"]["hunting_targets"] for c in recorded_calls() if c["message"] == "getHuntingTargets"]


def test_the_rivals_lists_match_the_recordings():
    first, second, third = recorded_targets()
    a = arena_from_start({"user_data": {}}, T)
    assert arena.targets_json(a) == first                               # the tutorial rival only
    a.update(hunting_wins=1, rating=75, rival_order=list(arena.FIRST_ORDER))
    assert arena.targets_json(a) == second
    a.update(hunting_wins=2, rating=105, rival_order=list(arena.FIRST_ORDER[1:]) + [-9])
    assert arena.targets_json(a) == third


@pytest.mark.needs_game
def test_rivals_around_a_higher_rating():
    a = arena_from_start({"user_data": {}}, T)
    a.update(hunting_wins=20, rating=735)
    with pytest.raises(ValueError):
        arena.rival_list(a)                                          # these need the card data
    rivals = arena.rival_list(a, real_cards())
    assert [r["user_id"] for r in rivals] == list(range(-11, -20, -1))
    assert [int(r["hunting_elo"]) for r in rivals] == [450, 550, 650, 700, 750, 800, 850, 950, 1050]
    assert {r["gold"] for r in rivals} == {75} and {r["level"] for r in rivals} == {1}   # every NPC, as recorded
    assert all(r["name"] in arena.NPC_NAMES for r in rivals)
    assert arena.rival_list(a, real_cards()) == rivals                   # stable until the rating moves
    a["rival_order"] = [-13]
    assert [r["user_id"] for r in arena.rival_list(a, real_cards())][-1] == -13   # fought: last


@pytest.mark.needs_game
def test_npc_commanders_fit_the_recorded_ones():
    cards = real_cards()
    # The account's NPCs: (rating, commander card).
    for rating, cid in ((400, 1017), (500, 1043), (750, 1044), (1000, 1032)):
        tier, level = arena.npc_tier_level(rating)
        card = cards[cid]
        assert card.base_id in arena.TIER_COMMANDERS[tier] and card.level == level, rating
    for tier, bases in arena.TIER_COMMANDERS.items():
        for base in bases:
            for level in range(1, arena.TOP_LEVEL[tier] + 1):
                c = cards[arena.commander_at(cards, base, level)]
                assert (c.base_id, c.level, c.fusion_level, c.type) == (base, level, 0, "commander")
    assert arena.commander_at(cards, 1000, 4) == 1110                   # Cyrus: not 1003


@pytest.mark.needs_game
def test_a_rivals_deck_matches_the_recorded_kind():
    cards = real_cards()
    rival = next(r for r in recorded_targets()[1].values() if r["user_id"] == -9)
    commander, deck = arena.rival_deck(rival, cards)
    # The recording: Maion and six different level-1 commons of its faction, from the base set.
    assert commander.name == "Maion" and len(deck) == 6 and len({c.id for c in deck}) == 6
    assert all((c.faction, c.rarity, c.set, c.level) == (commander.faction, 1, 1000, 1) for c in deck)
    assert arena.rival_deck(rival, cards) == (commander, deck)          # the same every time
    strong = dict(rival, hunting_elo="1500")
    _, deck = arena.rival_deck(strong, cards)
    assert len(deck) == 10 and max(c.rarity for c in deck) <= 4
    assert all(c.level == max(d.level for d in cards.cards.values() if d.base_id == c.base_id and d.fusion_level == 0)
               for c in deck)                                         # fully upgraded at 1500


def test_ratings():
    assert arena.rating_change(0, 0, True, first_win=True) == 75       # recorded
    assert arena.rating_change(75, 90, True, first_win=False) == 30    # recorded
    assert arena.rating_change(735, 1735, True, first_win=False) == 50  # the wiki's cap, at +1000
    assert arena.rating_change(735, 3000, True, first_win=False) == 50
    assert arena.rating_change(1000, 0, True, first_win=False) == 30   # at least 30: the account's 12 wins
    assert arena.rating_change(3000, 0, True, first_win=False) == 30
    assert arena.rating_change(75, 90, False, first_win=False) == 0    # the wiki: losses cost nothing


def test_a_win_with_a_level_up_matches_the_recording():
    p = Player(gold=222, war_bonds=50, energy=41, energy_updated=T, max_energy=250, gold_earned=1122,
               arena=arena_from_start({"user_data": {"stamina": "14"}}, T))
    rival = recorded_targets()[0]["-1"]
    res = arena.finish_hunt(p, rival, True, 0.25, T + 5, 1000001)
    recorded = next(c["body"] for c in recorded_calls()[110:] if c["message"] == "playCard"
                    and (c["body"].get("battle_data") or {}).get("winner") is not None)
    assert res.rewards == recorded["battle_data"]["rewards"]
    ud = recorded["user_data"]
    assert (p.gold, p.gold_earned, p.energy) == (int(ud["money"]), int(ud["gold_earned"]), int(ud["energy"]))
    a = p.arena
    assert (a["rating"], a["hunting_wins"], a["level"], a["promotion_points"], a["stamina"]) == \
           (int(ud["hunting_elo"]), int(ud["hunting_wins"]), int(ud["level"]), ud["promotion_points"], 15)
    assert res.levels == [2] and p.energy_updated == T + 5


def test_the_second_recorded_win():
    p = Player(gold=297, war_bonds=50, energy=291, energy_updated=T, gold_earned=1197,
               arena=dict(arena_from_start({"user_data": {"stamina": "14"}}, T), rating=75, hunting_wins=1,
                          level=2, promotion_points=1, rival_order=list(arena.FIRST_ORDER)))
    rival = recorded_targets()[1]["-9"]
    res = arena.finish_hunt(p, rival, True, 0.25, T, 1000001)
    assert res.rewards[0] == {"user_id": "1000001", "gold": 75, "sp": 0, "rating_change": 30, "item": [],
                              "lp_change": 0}
    assert (p.gold, p.arena["rating"], p.arena["hunting_wins"], res.levels) == (372, 105, 2, [])
    assert p.arena["rival_order"][-1] == -9


# ---- through the server -------------------------------------------------------------------------------


@pytest.mark.needs_game
def test_an_arena_fight_through_the_server(tmp_path):
    api = arena_api(tmp_path)
    ask(api, "init")
    r, how = ask(api, "getHuntingTargets")
    assert how == "rivals: 1 (rating 0)" and list(r["hunting_targets"]) == ["-1"]
    assert r["arena_battle_info"] == {"battles_fought": "0", "battles_rewarded": 1, "hunts_fought": "0",
                                      "hunts_rewarded": 1}
    assert "UNSUPPORTED: rival -9 isn't on your rivals list." == ask(api, "startHuntingBattle",
                                                                       target_user_id="-9")[1]
    r, how = ask(api, "startHuntingBattle", target_user_id="-1")
    assert how.startswith("engine: started arena fight with Riaz (-1, rated 0)") and how.endswith("stamina 15 -> 14")
    bd = r["battle_data"]
    assert {k: bd[k] for k in ("enemy_id", "enemy_name", "effect_id", "kv_store", "defend_commander", "eds")} == \
           {"enemy_id": "-1", "enemy_name": "Riaz", "effect_id": 0, "kv_store": [], "defend_commander": "1147",
            "eds": 3}
    assert bd["battleground_effects"] == {"global": [{"card_uid": 0}], "attacker": [], "defender": []}
    assert r["user_data"]["stamina"] in ("14", 14)
    while api.battle is not None:
        hand = api.battle["engine"].battle.sides[0].hand
        r, how = ask(api, "playCard", battle_id=str(bd["battle_id"]), card_uid=str(hand[0]), skip="0")
    assert how.endswith("(you won); paid 75 gold, rating +75, level 2")      # Ascaris has 1 health
    assert r["battle_data"]["rewards"][1] == {"user_id": "-1", "gold": 0, "sp": 0, "rating_change": 0, "item": []}
    assert "mission_id" not in r["battle_data"] and r["battle_data"]["enemy_id"] == "-1"
    ud = r["user_data"]
    assert (ud["level"], ud["level_name"], ud["base_xp"], ud["next_xp"], ud["hunting_elo"]) == \
           ("2", "Infantryman", "25", "150", "75")
    assert int(ud["energy"]) == 500 and int(ud["stamina"]) == 15             # the level-up
    assert list(r["hunting_targets"]) == [str(i) for i in arena.FIRST_ORDER]
    assert r["arena_battle_info"]["hunts_fought"] == "1" and "max_energy" in r


@pytest.mark.needs_game
def test_a_fight_needs_stamina(tmp_path):
    api = arena_api(tmp_path)
    ask(api, "init")
    p = api.player()
    p.arena["stamina"] = 0
    api.player_store.save(p)
    r, how = ask(api, "startHuntingBattle", target_user_id="-1")
    assert r["result"] is False and r["result_message"] == ["Not enough stamina: a fight needs 1, and you have 0."]
    assert how == "refused arena fight: stamina 0" and api.battle is None


@pytest.mark.needs_game
def test_surrendering_an_arena_fight(tmp_path):
    api = arena_api(tmp_path)
    ask(api, "init")
    r, _ = ask(api, "startHuntingBattle", target_user_id="-1")
    r, how = ask(api, "forfeitBattle", battle_id=str(r["battle_data"]["battle_id"]))
    assert how == "surrendered arena -1 (Riaz); paid 19 gold, rating +0"   # 75 x 0.25, to the nearest
    assert r["battle_data"]["winner"] == 0 and "hunting_targets" in r
    assert api.player().arena["hunts_fought"] == 1 and api.player().arena["hunting_wins"] == 0


@pytest.mark.needs_game
def test_without_the_engine_the_arena_replays(tmp_path):
    api = ga.GameApi(fixture_dir=ga.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: T + 100)
    ask(api, "init")
    assert ask(api, "getHuntingTargets")[1].startswith("replayed")
    assert ask(api, "startHuntingBattle", target_user_id="-1")[1].startswith("replayed")
