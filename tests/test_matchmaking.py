"""Tests for other players as arena rivals (arena.py, game_api/arena_calls.py): who gets matched, what
they pay, their place on the list, fighting their defense deck, and their report of it."""

import json
import random

import pytest

from helpers import T, call, login, real_cards, server
from rules import arena, content
from rules.player_state import arena_from_start, level_for

# The account's recorded list (rating 735): each real rival's rating, rank and gold.
RECORDED_PLAYERS = [(1663, 6, 159), (1624, 6, 156), (1309, 6, 129), (1302, 6, 126), (1259, 6, 123),
                    (1024, 6, 102), (865, 5, 87), (705, 5, 75), (677, 5, 75), (675, 5, 75), (595, 4, 75),
                    (585, 4, 75), (480, 4, 75), (479, 4, 75), (465, 4, 75), (405, 4, 75), (375, 4, 75),
                    (366, 4, 75)]


def rival(uid, rating, level, commander=1000, name=None, guild=None):
    return arena.PlayerRival(uid, name or f"Player{uid}", rating, level, commander, guild)


def an_arena(rating=735, wins=20):
    a = arena_from_start({"user_data": {}}, T)
    a.update(hunting_wins=wins, rating=rating)
    a["level"] = max(a["level"], level_for(rating))
    return a


# ---- the rules -----------------------------------------------------------------------------------

def test_player_gold_fits_the_recorded_list():
    golds = [arena.player_gold(735, r) * 3 for r, _, _ in RECORDED_PLAYERS]
    misses = [(r, g, mine) for (r, _, g), mine in zip(RECORDED_PLAYERS, golds) if g != mine]
    assert misses == [(1309, 129, 126)]                  # 17 of 18 exactly, the other off by 3 (one point)


def test_the_recorded_players_were_all_within_a_rank():
    assert {level for _, level, _ in RECORDED_PLAYERS} == {4, 5, 6}                  # the account: rank 5
    assert all(arena.in_reach(5, level) for _, level, _ in RECORDED_PLAYERS)


def test_only_players_within_reach_are_picked():
    candidates = [rival(1000000 + level, 0, level) for level in range(2, 9)]
    rng = random.Random(1)
    assert arena.pick_players(candidates, 5, rng, chance=1) == [1000004, 1000005, 1000006]
    assert arena.pick_players(candidates, 5, rng, chance=1, reach=2) == [1000003, 1000004, 1000005, 1000006,
                                                                         1000007]
    assert arena.pick_players(candidates, 5, rng, chance=0) == []
    many = [rival(1000000 + i, 0, 5) for i in range(30)]
    picked = arena.pick_players(many, 5, rng, chance=1)
    assert len(picked) == arena.LIST_SIZE and len(set(picked)) == arena.LIST_SIZE
    half = [len(arena.pick_players(many, 5, random.Random(s), chance=0.5)) for s in range(20)]
    assert 0 < min(half) and max(half) <= arena.LIST_SIZE


def test_the_picks_hold_until_the_list_changes():
    a = an_arena()
    found = [rival(1000002, 700, 5)]
    ids, picked = arena.matched_players(a, lambda: found, random.Random(1), chance=1)
    assert (ids, picked) == ([1000002], True) and a["matched_for"] == [0, 735]

    def nobody_asked():
        raise AssertionError("picked again")
    assert arena.matched_players(a, nobody_asked, random.Random(1), chance=1) == ([1000002], False)
    a["rating"] = 765                                                       # a win
    assert arena.matched_players(a, lambda: [], random.Random(1), chance=1) == ([], True)
    a["refreshes"] += 1                                                     # a refresh
    assert arena.matched_players(a, lambda: found, random.Random(1), chance=1) == ([1000002], True)
    tutorial = an_arena(rating=0, wins=0)
    assert arena.matched_players(tutorial, nobody_asked, random.Random(1), chance=1) == ([], False)


@pytest.mark.needs_game
def test_players_take_the_places_of_the_nearest_npcs():
    a = an_arena()
    npcs = arena.rival_list(a, real_cards())
    players = [rival(1000002, 760, 5, commander=1007, guild="Owls"), rival(1000003, 1663, 6, commander=1047)]
    rivals = arena.rival_list(a, real_cards(), 3, players)
    assert len(rivals) == arena.LIST_SIZE
    ratings = [int(r["hunting_elo"]) for r in rivals]
    assert ratings == sorted(ratings, reverse=True)                         # highest first, as recorded
    gone = {r["user_id"] for r in npcs} - {r["user_id"] for r in rivals}
    assert {int(r["hunting_elo"]) for r in npcs if r["user_id"] in gone} == {750, 1050}   # the nearest NPCs
    top = rivals[0]
    assert list(top) == ["name", "user_id", "guild_name", "hunting_elo", "level", "items", "commander", "sp",
                         "gold"]                                            # the recorded keys, in order
    assert top == {"name": "Player1000003", "user_id": 1000003, "guild_name": None, "hunting_elo": "1663",
                   "level": "6", "items": [], "commander": "1047", "sp": 0, "gold": 159}
    owls = next(r for r in rivals if r["user_id"] == 1000002)
    assert (owls["guild_name"], owls["gold"], owls["level"]) == ("Owls", 78, "5")   # rated 25 above: 1 more x 3
    a["rival_order"] = [1000003]
    assert arena.rival_list(a, real_cards(), 3, players)[-1]["user_id"] == 1000003  # fought: last
    assert arena.rival_list(an_arena(rating=0, wins=0), real_cards(), 3, players)[0]["user_id"] == -1  # tutorial


def test_a_defence_report():
    a = an_arena()
    arena.file_defence_report(a, 1000001, "Ann", held=True, now=T + 5, battle_id=123)
    arena.file_report(a, {"user_id": -12, "name": "Vile"}, True, {"user_id": "1000002", "gold": 75, "sp": 0,
                                                                  "rating_change": 30, "item": [],
                                                                  "lp_change": 0}, T + 9)
    own, defence = arena.reports_json(a, 1000002)
    assert (own["type"], own["sender_id"], json.loads(own["data"])["host_id"]) == ("2", "-12", "1000002")
    assert {k: defence[k] for k in ("user_id", "sender_id", "type", "time", "status", "got_revenge")} == \
           {"user_id": "1000002", "sender_id": "1000001", "type": "1", "time": str(T + 5), "status": "1",
            "got_revenge": "-1"}                                            # held: nothing to avenge
    assert json.loads(defence["data"]) == {
        "enemy_id": "1000001", "enemy_name": "Ann", "victory": 1, "host_id": "1000001", "battle_id": "123",
        "rewards": {"user_id": "1000002", "gold": 0, "sp": 0, "rating_change": 0, "item": [], "lp_change": 0}}


def test_only_a_lost_defense_offers_a_revenge():
    a = an_arena()
    arena.file_defence_report(a, 1000001, "Ann", held=False, now=T + 5)
    arena.file_defence_report(a, 1000003, "Cid", held=True, now=T + 6)
    held, lost = arena.reports_json(a, 1000002)
    assert (held["sender_id"], held["got_revenge"]) == ("1000003", "-1")
    assert (lost["sender_id"], lost["got_revenge"]) == ("1000001", "0")     # 0: the Revenge button
    assert json.loads(lost["data"])["victory"] == 0


def test_a_revenge_report_offers_none():
    a = an_arena()
    arena.file_defence_report(a, 1000001, "Ann", held=False, now=T + 5, revenge=False)
    assert arena.reports_json(a, 1000002)[0]["got_revenge"] == "-1"
    assert arena.defence_report(a, 1000001, T + 5)["revenge"] == arena.NO_REVENGE
    assert arena.defence_report(a, 1000001, T + 6) is None and arena.defence_report(a, 1000003, T + 5) is None


def test_the_arena_settings():
    c = content.parse(content.TEMPLATE)
    assert (c.arena_player_chance, c.arena_rank_reach, c.problems) == (0.5, 1, [])
    c = content.parse("[arena]\nplayer_chance = 2\nrank_reach = -1\n")
    assert (c.arena_player_chance, c.arena_rank_reach) == (0.5, 1)
    assert c.problems == ["arena.player_chance: needs a number from 0 to 1",
                          "arena.rank_reach: needs a whole number of ranks, 0 or more"]


# ---- through the server --------------------------------------------------------------------------

def fight(api, creds, tid):
    """startHuntingBattle, then Skip to the end: (the start reply, the last reply, its log line)."""
    start, how = call(api, creds, "startHuntingBattle", target_user_id=str(tid))
    if not start.get("result"):
        return start, start, how
    session = api._sessions[creds["user_id"]]
    r = start
    while session.battle is not None:
        hand = session.battle["engine"].battle.sides[0].hand
        r, how = call(api, creds, "playCard", battle_id=str(start["battle_data"]["battle_id"]),
                      card_uid=str(hand[0]), skip="1")
    return start, r, how


def two_players(tmp_path, chance=1):
    api, clock = server(tmp_path, content=f"[arena]\nplayer_chance = {chance}\n", engine=True)
    _, a = login(api)
    _, b = login(api)
    for creds in (a, b):
        call(api, creds, "init")
    return api, clock, a, b


def give_b_a_defense_deck(api, b):
    """B gets a second deck slot and Malika's cards, builds deck 2 and makes it the defense
    deck, the game's way (setDeckCards, setDefenseDeck). Deck 1 stays the active one."""
    store = api._store_for(b["user_id"])
    p = store.load()
    p.max_decks = 2
    for cid in (1007, 2, 2, 3, 4):
        p.add_card(cid)
    store.save(p)
    r, how = call(api, b, "setDeckCards", deck_id="2", commander_id="1007", cards=json.dumps({"2": 2, "3": 1}))
    assert r["result"], how
    r, how = call(api, b, "setDefenseDeck", deck_id="2")
    assert how == "defence deck: 2" and r["user_data"]["defense_deck"] == "2"
    assert r["user_data"]["active_deck"] == "1"


def give_b_a_losing_defense(api, b):
    """B's defense deck becomes deck 2: Malika and no cards, which A's deck beats."""
    store = api._store_for(b["user_id"])
    p = store.load()
    p.max_decks = 2
    p.add_card(1007)
    store.save(p)
    assert call(api, b, "setDeckCards", deck_id="2", commander_id="1007", cards="{}")[0]["result"]
    assert call(api, b, "setDefenseDeck", deck_id="2")[0]["result"]


def a_beats_b(tmp_path):
    """Two players past the arena's tutorial fight, and A beats B's defense deck.
    Returns (api, clock, a, b, B's report of it)."""
    api, clock, a, b = two_players(tmp_path)
    fight(api, b, -1)
    give_b_a_losing_defense(api, b)
    fight(api, a, -1)
    call(api, a, "getHuntingTargets")
    clock[0] += 60
    _, end, how = fight(api, a, 1000002)
    assert end["battle_data"]["winner"] == 1, how
    r, _ = call(api, b, "getBattleReports")
    return api, clock, a, b, r["battle_reports"][0]


@pytest.mark.needs_game
def test_a_player_rival_is_their_defense_deck_played_by_the_ai(tmp_path):
    api, clock, a, b = two_players(tmp_path)
    _, _, how = fight(api, b, -1)
    assert how.endswith("(you won); paid 75 gold, rating +75, level 2")      # B is past the tutorial
    give_b_a_defense_deck(api, b)
    r, how = call(api, a, "getHuntingTargets")
    assert list(r["hunting_targets"]) == ["-1"]                              # A isn't: the tutorial rival only
    fight(api, a, -1)
    r, how = call(api, a, "getHuntingTargets")
    targets = r["hunting_targets"]
    assert how == "rivals: 9 (rating 75)" and "1000002" in targets
    assert targets["1000002"] == {"name": "Player1000002", "user_id": 1000002, "guild_name": None,
                                  "hunting_elo": "75", "level": "2", "items": [], "commander": "1007", "sp": 0,
                                  "gold": 75}
    assert call(api, a, "getHuntingTargets")[0]["hunting_targets"] == targets    # the same list until it changes
    b_before = api._store_for(b["user_id"]).load()
    gold, rating = b_before.gold, b_before.arena["rating"]

    start, end, how = fight(api, a, 1000002)
    assert start["battle_data"]["enemy_id"] == "1000002" and start["battle_data"]["enemy_name"] == "Player1000002"
    assert start["battle_data"]["defend_commander"] == "1007"                # deck 2, not B's active deck 1
    won = end["battle_data"]["winner"] == 1
    assert end["battle_data"]["rewards"][1]["user_id"] == "1000002"
    assert how.endswith("(you won); paid 75 gold, rating +30" if won else "(you lost); paid 19 gold, rating +0")

    b_after = api._store_for(b["user_id"]).load()
    assert (b_after.gold, b_after.arena["rating"]) == (gold, rating)         # being attacked costs nothing
    r, how = call(api, b, "getBattleReports")
    report = r["battle_reports"][0]
    data = json.loads(report["data"])
    assert (report["sender_id"], report["got_revenge"], data["enemy_name"], data["victory"]) == \
           ("1000001", "0" if won else "-1", "Player1000001", int(not won))  # B's side: did the defense hold
    r, _ = call(api, a, "getBattleReports")
    assert json.loads(r["battle_reports"][0]["data"])["enemy_id"] == "1000002"


@pytest.mark.needs_game
def test_the_defense_deck_is_the_one_fought(tmp_path):
    api, clock, a, b = two_players(tmp_path)
    fight(api, b, -1)
    give_b_a_defense_deck(api, b)
    fight(api, a, -1)
    call(api, a, "getHuntingTargets")
    call(api, a, "startHuntingBattle", target_user_id="1000002")
    defender = api._sessions[a["user_id"]].battle["engine"].battle.sides[1]
    assert defender.commander.card.id == 1007
    assert sorted(c.id for c in defender.cards.values()) == [2, 2, 3]      # deck 2's cards, by the AI


@pytest.mark.needs_game
def test_without_a_defense_deck_the_active_one_defends(tmp_path):
    api, clock, a, b = two_players(tmp_path)
    fight(api, b, -1)
    store = api._store_for(b["user_id"])
    p = store.load()
    p.defense_deck = None
    store.save(p)
    fight(api, a, -1)
    r, _ = call(api, a, "getHuntingTargets")
    assert r["hunting_targets"]["1000002"]["commander"] == "1000"            # deck 1's commander


@pytest.mark.needs_game
def test_no_player_rivals_when_switched_off_or_out_of_reach(tmp_path):
    api, clock, a, b = two_players(tmp_path, chance=0)
    fight(api, b, -1)
    fight(api, a, -1)
    r, _ = call(api, a, "getHuntingTargets")
    assert all(int(i) < 0 for i in r["hunting_targets"])

    (tmp_path / "reach").mkdir()
    api, clock, a, b = two_players(tmp_path / "reach")
    fight(api, b, -1)
    store = api._store_for(b["user_id"])
    p = store.load()
    p.arena.update(rating=1100, level=6)                                     # rank 6, A will be rank 2
    store.save(p)
    fight(api, a, -1)
    r, _ = call(api, a, "getHuntingTargets")
    assert all(int(i) < 0 for i in r["hunting_targets"])


@pytest.mark.needs_game
def test_a_player_who_has_not_fought_yet_is_not_a_rival(tmp_path):
    api, clock, a, b = two_players(tmp_path)
    fight(api, a, -1)                                                        # B hasn't been to the arena
    r, _ = call(api, a, "getHuntingTargets")
    assert all(int(i) < 0 for i in r["hunting_targets"])


@pytest.mark.needs_game
def test_revenge(tmp_path):
    api, clock, a, b, report = a_beats_b(tmp_path)                          # A beats B's defense deck
    assert (report["sender_id"], report["got_revenge"]) == ("1000001", "0")

    r, how = call(api, b, "startRevengeBattle", target_user_id="1000001", reported_time=str(int(report["time"]) + 1))
    assert r["result"] is False and r["result_message"] == ["There's no fight of theirs against you to take revenge for."]
    stamina = int(api._store_for(b["user_id"]).load().arena["stamina"])
    clock[0] += 60
    start, how = call(api, b, "startRevengeBattle", target_user_id="1000001", reported_time=report["time"])
    assert how.startswith("engine: started revenge on Player1000001 (player 1000001's defense deck, rated 105)")   # A won: +30
    assert how.endswith(f"stamina {stamina} -> {stamina - 1}")
    bd = start["battle_data"]
    assert (bd["enemy_id"], bd["enemy_name"], bd["defend_commander"]) == ("1000001", "Player1000001", "1000")
    r, how = call(api, b, "getBattleReports")
    assert r["battle_reports"][0]["got_revenge"] == "1"                     # taken: the button goes
    session = api._sessions[b["user_id"]]
    while session.battle is not None:
        hand = session.battle["engine"].battle.sides[0].hand
        end, how = call(api, b, "playCard", battle_id=str(bd["battle_id"]), card_uid=str(hand[0]), skip="1")
    won = end["battle_data"]["winner"] == 1
    # A is rated 30 above B now: (25 + 1) x 3 = 78 gold, a quarter on a loss; 30 + 30/50 rating
    assert how.endswith("(you won); paid 78 gold, rating +31" if won else "(you lost); paid 20 gold, rating +0")
    assert "hunting_targets" in end and "arena_battle_info" in end           # an arena fight's last reply

    r, _ = call(api, b, "getBattleReports")
    own = r["battle_reports"][0]
    assert (own["type"], own["sender_id"], own["got_revenge"]) == ("2", "1000001", "-1")   # B's own fight
    assert r["battle_reports"][1]["got_revenge"] == "1"
    r, how = call(api, b, "startRevengeBattle", target_user_id="1000001", reported_time=report["time"])
    assert r["result_message"] == ["You've already taken revenge on Player1000001 for that fight."]

    r, _ = call(api, a, "getBattleReports")
    theirs = r["battle_reports"][0]
    assert (theirs["sender_id"], theirs["got_revenge"]) == ("1000002", "-1")   # no revenge on a revenge
    assert json.loads(theirs["data"])["victory"] == int(not won)
    r, how = call(api, a, "startRevengeBattle", target_user_id="1000002", reported_time=theirs["time"])
    assert r["result_message"] == ["That was a revenge by Player1000002: it can't be avenged."]


@pytest.mark.needs_game
def test_revenge_needs_stamina(tmp_path):
    api, clock, a, b, report = a_beats_b(tmp_path)
    store = api._store_for(b["user_id"])
    p = store.load()
    p.arena["stamina"] = 0
    store.save(p)
    r, how = call(api, b, "startRevengeBattle", target_user_id="1000001", reported_time=report["time"])
    assert r["result"] is False and how == "refused arena fight: stamina 0"
    r, _ = call(api, b, "getBattleReports")
    assert r["battle_reports"][0]["got_revenge"] == "0"                     # still there to take


@pytest.mark.needs_game
def test_no_revenge_when_the_defense_held(tmp_path):
    api, clock, a, b = two_players(tmp_path)
    fight(api, b, -1)
    fight(api, a, -1)
    call(api, a, "getHuntingTargets")
    start, how = call(api, a, "startHuntingBattle", target_user_id="1000002")
    call(api, a, "forfeitBattle", battle_id=str(start["battle_data"]["battle_id"]))   # A surrenders: B held
    r, _ = call(api, b, "getBattleReports")
    report = r["battle_reports"][0]
    assert (report["sender_id"], report["got_revenge"], json.loads(report["data"])["victory"]) == ("1000001", "-1", 1)
    r, how = call(api, b, "startRevengeBattle", target_user_id="1000001", reported_time=report["time"])
    assert r["result_message"] == ["Your defense held against Player1000001: there's nothing to avenge."]
