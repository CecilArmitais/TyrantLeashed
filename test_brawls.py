"""Tests for brawls (brawls.py, game_api.py): the data, the replies against the recorded
ones, energy, scoring, opponents, rewards, and brawls played through the server."""

import json
import random

import brawls
import game_api as ga
from test_players import call, login, server

EVENTS = brawls.load(ga.ASSET_DIR)
RECORDED = json.loads((ga.FIXTURE_DIR / "init.json").read_text("utf-8"))["body"]
T0 = 1_800_000_000


def a_state(event_id=344, now=T0):
    state = brawls.new_state()
    brawls.start_run(state, EVENTS[event_id], now)
    return state


# ---- the data -------------------------------------------------------------------------------------

def test_the_brawls():
    assert len(EVENTS) == 343
    solo, guild, placement = EVENTS[344], EVENTS[343], EVENTS[345]
    assert (solo.name, solo.guild_event, solo.length) == ("Winter's Kiss Brawl", False, 78 * 3600)
    assert guild.guild_event and not guild.war
    assert placement.guild_event and placement.war and placement.use_forts
    assert all(t.min_points is None for t in solo.tiers) and all(t.min_points for t in guild.tiers)
    assert solo.tiers[0] == brawls.Tier(1, 1, None, (("card", 17239, 2),))
    assert sum(1 for e in EVENTS.values() if e.effects) == 54


def test_the_brawl_matches_the_recorded_one():
    recorded = RECORDED["active_brawl_data"]
    api = ga.GameApi(state_dir=None)
    start, end = int(recorded["start_time"]), int(recorded["end_time"])
    run = {"start": start, "end": end, "decay": end + brawls.DECAY_DAYS * 86400}
    ours = brawls.event_json(EVENTS[345], run, api._mini_packs())
    assert ours == recorded                                        # the 26 tiers, the flags, the timings
    mine = brawls.player_json(a_state(345), EVENTS[345], 1000001, 0, T0)
    assert set(mine) == set(RECORDED["player_brawl_data"])
    assert set(mine["energy"]) == set(RECORDED["player_brawl_data"]["energy"]) | {"battle_energy_recharge_stop_time"}


# ---- the rules ----------------------------------------------------------------------------------------

def test_energy():
    state = a_state()
    run = state["run"]
    assert brawls.energy_now(run, None, T0) == brawls.ENERGY_MAX
    row = brawls.player_row(state, 1)
    for _ in range(3):
        brawls.spend_energy(run, row, T0 + 100)
    assert brawls.energy_now(run, row, T0 + 100) == 22 and brawls.recharging(run, row, T0 + 100)
    assert brawls.energy_now(run, row, T0 + 100 + brawls.ENERGY_EVERY) == 23
    brawls.spend_energy(run, row, T0 + 100 + brawls.ENERGY_EVERY + 600)     # keeps the part-way progress
    assert row["energy"] == 22 and row["energy_updated"] == T0 + 100 + brawls.ENERGY_EVERY
    assert brawls.energy_now(run, row, T0 + 100 + 2 * brawls.ENERGY_EVERY) == 23
    late = brawls.recharge_stop(run)
    row.update(energy=0, energy_updated=late - 60)
    assert brawls.energy_now(run, row, run["end"]) == 0 and not brawls.recharging(run, row, run["end"])


def test_points():
    assert brawls.fight_points(True, 3, 2, 20) == 55 + 3 + 2 - 3
    assert brawls.fight_points(True, 0, 0, 50) == 55 - 7
    assert brawls.fight_points(False, 9, 9, 3) == 5


def test_opponents_near_your_points():
    candidates = [(str(i), p) for i, p in enumerate((0, 10, 100, 190, 200, 210, 220, 900))]
    picks = {brawls.pick_opponent(candidates, 200, random.Random(s)) for s in range(50)}
    assert picks == {"2", "3", "4", "5", "6"}                    # the five closest to 200
    assert brawls.pick_opponent([], 0, random.Random(1)) is None


def test_standings_and_rewards():
    state = a_state()
    for uid, points in ((1, 300), (2, 500), (3, 0)):
        row = brawls.player_row(state, uid, f"P{uid}")
        brawls.record_fight(state, row, None, True, points)
    assert [(r["id"], r["rank"]) for r in brawls.standings(state, False)] == [("2", 1), ("1", 2), ("3", 3)]
    assert brawls.rank_of(state, 1, False) == 2 and brawls.rank_of(state, 9, False) == 0
    solo = EVENTS[344]
    assert len(brawls.earned_tiers(solo, 1, 0)) == len(solo.tiers)            # rank 1 reaches every tier
    assert [t.rank for t in brawls.earned_tiers(solo, 40000, 0)] == [50000]
    assert brawls.earned_tiers(solo, 0, 10**6) == []
    guild = EVENTS[343]
    low = brawls.earned_tiers(guild, 1, 0)
    assert all(t.min_points is not None and t.min_points <= 0 for t in low)   # guild tiers need points too
    assert len(brawls.earned_tiers(guild, 1, 10**6)) == len(guild.tiers)


def test_a_new_run_keeps_the_last_board():
    state = a_state()
    row = brawls.player_row(state, 7, "Ann")
    brawls.record_fight(state, row, None, True, 60)
    brawls.start_run(state, EVENTS[344], state["run"]["decay"] + 1)
    assert state["players"] == {} and state["previous"] == [{"id": "7", "name": "Ann", "points": 60, "rank": 1}]
    assert brawls.board_json(state["previous"], False) == [
        {"user_id": "7", "points": "60", "points_rank": 1, "rank": 1, "stat": "60", "name": "Ann"}]


# ---- through the server -----------------------------------------------------------------------------

def players(tmp_path, content="[brawls]\nbrawl = 344\n", n=3):
    """n players, all past the arena's tutorial fight and rated 150."""
    api, clock = server(tmp_path, content=content, engine=True)
    creds = []
    for _ in range(n):
        _, c = login(api)
        call(api, c, "init")
        store = api._store_for(c["user_id"])
        p = store.load()
        p.arena.update(hunting_wins=1, rating=150, level=3)
        store.save(p)
        creds.append(c)
    return api, clock, creds


def test_no_brawl_unless_set(tmp_path):
    api, clock = server(tmp_path, engine=True)
    _, ann = login(api)
    r, _ = call(api, ann, "init")
    assert "active_brawl_data" not in r and "player_brawl_data" not in r      # the recording's isn't passed on
    r, _ = call(api, ann, "fightBrawlBattle")
    assert r["result_message"] == ["There's no brawl on right now."]
    for message, key in (("getBrawlLeaderboard", "brawl_leaderboard"), ("getBrawlMemberLeaderboard", "brawl_leaderboard"),
                         ("getPreviousBrawlTopLeaderboard", "previous_brawl_leaderboard")):
        assert call(api, ann, message)[0][key] == []


def test_a_solo_brawl(tmp_path):
    api, clock, (ann, bob, cid) = players(tmp_path)
    r, _ = call(api, ann, "init")
    ab, pb = r["active_brawl_data"], r["player_brawl_data"]
    assert (ab["id"], ab["name"], ab["start_time"]) == ("344", "Winter's Kiss Brawl", str(clock[0]))
    assert (pb["points"], pb["current_rank"], pb["energy"]["battle_energy"]) == (0, 0, 25)
    assert r["user_data"]["battle_energy"] == "25"
    assert "active_brawl_data" in call(api, ann, "getHuntingTargets")[0]

    r, how = call(api, ann, "fightBrawlBattle")
    assert how.startswith("brawl fight vs Player100000"), how
    bd = r["battle_data"]
    assert bd["host_is_attacker"] is False and bd["enemy_id"] in ("1000002", "1000003")
    reward = bd["rewards"][0]
    won = bd["winner"] == 1
    assert reward["rating_change"] == 0 and (reward["pvp_points"] == 5 if not won else reward["pvp_points"] > 40)
    assert int(next(iter(bd["card_map"]))) < 101                             # surge: the enemy moved first
    pb = r["player_brawl_data"]
    assert pb["points"] == reward["pvp_points"] and pb["energy"]["battle_energy"] == 24 and pb["current_rank"] == 1
    assert r["user_data"]["battle_energy"] == "24" and r["user_data"]["battle_energy_recharge_time"] == 1800
    for _ in range(2):
        call(api, ann, "fightBrawlBattle")
    r, _ = call(api, ann, "getBrawlLeaderboard")
    board = r["brawl_leaderboard"]
    assert [(b["user_id"], b["rank"]) for b in board] == [("1000001", 1)] and board[0]["name"] == "Player1000001"

    r, _ = call(api, ann, "claimBrawlRewards")
    assert r["result_message"] == ["The brawl's rewards can be claimed a little after it ends."]
    run = api.brawl_store.load()["run"]
    clock[0] = run["end"] + brawls.CLAIM_WAIT
    r, how = call(api, ann, "claimBrawlRewards")
    assert r["result"] is True and r["new_cards"], how
    shards = {2000: 500, 2001: 3050, 2002: 1600}                              # Epic, Legendary, Vindicator Shards
    assert {i["id"]: i["number"] for i in r["new_items"]} == shards and r["player_brawl_data"]["claimed_rewards"] == 1
    assert {int(k): v["number"] for k, v in r["user_items"].items()} == shards
    assert api.player().items == {str(k): n for k, n in shards.items()}
    assert int(r["user_data"]["cards_earned"]) == 5 + len(r["new_cards"])     # counted once
    assert call(api, ann, "claimBrawlRewards")[0]["result_message"] == ["You already claimed your brawl rewards."]
    assert call(api, bob, "claimBrawlRewards")[0]["result_message"] == ["You didn't take part in this brawl."]

    clock[0] = run["decay"] + 1                                               # a new run
    r, _ = call(api, bob, "getPreviousBrawlTopLeaderboard")
    assert [b["user_id"] for b in r["previous_brawl_leaderboard"]] == ["1000001"]
    assert call(api, bob, "init")[0]["player_brawl_data"]["points"] == 0


def test_brawl_refusals(tmp_path):
    api, clock, (ann,) = players(tmp_path, n=1)
    r, _ = call(api, ann, "fightBrawlBattle")
    assert r["result_message"] == ["There's no one to brawl right now."]
    store = api._store_for(ann["user_id"])
    p = store.load()
    p.arena["rating"] = 100
    store.save(p)
    assert call(api, ann, "fightBrawlBattle")[0]["result_message"] == ["Brawls open at a battle rating above 100."]
    (tmp_path / "two").mkdir()
    api2, clock2, (a2, b2) = players(tmp_path / "two", n=2)
    state = api2.brawl_store.load()
    row = brawls.player_row(state, a2["user_id"])
    row["energy"], row["energy_updated"] = 0, clock2[0]
    api2.brawl_store.save(state)
    assert call(api2, a2, "fightBrawlBattle")[0]["result_message"] == ["You're out of brawl energy. Wait for more energy."]
    assert call(api2, a2, "init")[0]["user_data"]["battle_energy"] == "0"


def test_a_guild_brawl(tmp_path):
    api, clock, (ann, bob, cid) = players(tmp_path, content="[brawls]\nbrawl = 343\n")
    r, _ = call(api, ann, "fightBrawlBattle")
    assert r["result_message"] == ["You must be in a Guild to compete in this Guild Brawl."]
    call(api, ann, "createFaction", faction_name="Owls")
    owls = api.guild_store.by_name("Owls")
    call(api, bob, "joinFaction", faction_id=str(owls["id"]))
    call(api, cid, "createFaction", faction_name="Bats")
    for _ in range(3):
        r, how = call(api, ann, "fightBrawlBattle")
        assert "Player1000003" in how, how                                   # never a guildmate
    points = api.brawl_store.load()["players"]["1000001"]["points"]
    r, _ = call(api, ann, "getBrawlLeaderboard")
    assert r["brawl_leaderboard"] == [{"faction_id": str(owls["id"]), "points": str(points), "points_rank": 1,
                                       "rank": 1, "stat": str(points), "name": "Owls"}]
    r, _ = call(api, bob, "getBrawlMemberLeaderboard")
    assert [(b["name"], b["points"]) for b in r["brawl_leaderboard"]] == [("Player1000001", str(points)),
                                                                          ("Player1000002", "0")]
    assert call(api, bob, "init")[0]["player_brawl_data"]["current_rank"] == 1   # the guild's rank
