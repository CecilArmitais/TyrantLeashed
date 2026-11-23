"""Tests for conquest (conquest.py, game_api/conquest_calls.py): the data, zone tiers, scoring rounds,
fortresses, energy, opponents, rewards, the replies, and a conquest through the server."""

import random
from functools import lru_cache

import pytest

import conquest
import game_api as ga
import paths
from helpers import call, login, server


@lru_cache(maxsize=None)
def conquest_events():
    """The conquest events (read on first use)."""
    return conquest.load(paths.ASSET_DIR)[0]


@lru_cache(maxsize=None)
def conquest_zones():
    """The conquest zones (read on first use)."""
    return conquest.load(paths.ASSET_DIR)[1]


T0 = 1_800_000_000
PHOBOS, NEXUS = 11, 12


def a_state(now=T0):
    state = conquest.new_state()
    conquest.start_run(state, conquest_events()[33], now)
    return state


def influence(state, gid, zone, amount):
    conquest.guild_row(state, gid, f"G{gid}")["influence"][str(zone)] = amount


# ---- the data ------------------------------------------------------------------------------------

@pytest.mark.needs_game
def test_the_data():
    assert len(conquest_events()) == 35 and len(conquest_zones()) == 12
    ev = conquest_events()[33]
    assert (ev.name, ev.length, ev.asset_bundle, ev.terrain_prefab) == ("Aftermath Conquest", 76 * 3600, 2006,
                                                                        "prefab_conquest_map1")
    assert conquest_zones()[PHOBOS].name == "Phobos Station" and conquest_zones()[PHOBOS].battleground_effect == 1002
    assert [t.min_rank for t in conquest_zones()[PHOBOS].tiers] == [1, 5, 10, 25, 35, 50, 75, 100, 500, 5000]
    assert all(t.conquest_points for t in conquest_zones()[NEXUS].tiers)                  # the Nexus is all points


@pytest.mark.needs_game
def test_the_tier_a_rank_reaches():
    zone = conquest_zones()[PHOBOS]
    assert (conquest.tier_for(zone, 1, "conquest_points"), conquest.tier_for(zone, 1, "fortress_card")) == (50, 8507)
    assert (conquest.tier_for(zone, 6, "conquest_points"), conquest.tier_for(zone, 6, "fortress_card")) == (30, 8506)
    assert conquest.tier_for(zone, 0, "conquest_points") is None
    assert conquest.tier_for(conquest_zones()[NEXUS], 1, "fortress_card") is None


# ---- the rules -----------------------------------------------------------------------------------

@pytest.mark.needs_game
def test_scoring_rounds():
    state = a_state()
    influence(state, 1, PHOBOS, 100)
    influence(state, 2, PHOBOS, 50)
    influence(state, 2, NEXUS, 5)
    assert conquest.score_rounds(state, conquest_zones(), T0 + conquest.ROUND_EVERY - 1) == 0
    assert conquest.score_rounds(state, conquest_zones(), T0 + conquest.ROUND_EVERY) == 1
    g1, g2 = state["guilds"]["1"], state["guilds"]["2"]
    assert (g1["points"], g2["points"]) == (50, 30 + 150)                       # Phobos rank 1 / 2; the Nexus rank 1
    assert g1["fortresses"] == {str(PHOBOS): 8507} and g2["fortresses"] == {str(PHOBOS): 8507}
    assert g1["influence"][str(PHOBOS)] == 100                                  # no decay (an unknown)
    assert conquest.score_rounds(state, conquest_zones(), T0 + conquest.ROUND_EVERY) == 0  # once a round
    assert conquest.score_rounds(state, conquest_zones(), T0 + 10 ** 7) == 18              # every round up to the end, 19 in all
    assert state["run"]["rounds"] == 19 and g1["points"] == 19 * 50
    assert [r["id"] for r in conquest.standings(state)] == ["2", "1"]
    assert conquest.guild_rank(state, 1) == 2 and conquest.guild_rank(state, 9) == 0


@pytest.mark.needs_game
def test_fortresses_come_from_the_best_zones():
    state = a_state()
    g = conquest.guild_row(state, 1, "G1")
    for z, rank in enumerate((4, 1, 3, 2, 6, 5), start=1):
        g["ranks"][str(z)], g["fortresses"][str(z)] = rank, 9000 + z
    assert conquest.fortress_cards(state, 1) == [9002, 9004, 9003, 9001, 9006]   # the five best ranked
    assert conquest.fortress_cards(state, 2) == []


@pytest.mark.needs_game
def test_energy_and_fights():
    state = a_state()
    run = state["run"]
    m = conquest.member_row(state, 7, "Ann")
    conquest.guild_row(state, 1, "G1")
    conquest.ENERGY.spend(run, m, T0 + 10)
    assert conquest.ENERGY.current(run, m, T0 + 10) == 24
    assert conquest.ENERGY.current(run, m, T0 + 10 + conquest.ENERGY_EVERY) == 25
    assert conquest.ENERGY.recharge_stop(run) == run["end"] - 4 * 3600
    assert conquest.record_fight(state, m, 1, PHOBOS, True) == 50
    assert conquest.record_fight(state, m, 1, PHOBOS, False) == 5
    assert (m["influence"], m["zones"], m["battles"], m["wins"]) == (55, {str(PHOBOS): 55}, 2, 1)
    assert state["guilds"]["1"]["influence"] == {str(PHOBOS): 55}


def test_opponent_guilds_near_yours():
    candidates = [(str(i), p) for i, p in enumerate((0, 100, 480, 500, 530, 2000))]
    picks = {conquest.pick_guild(candidates, 500, random.Random(s)) for s in range(40)}
    assert picks == {"2", "3", "4"}
    assert conquest.pick_guild([], 0, random.Random(1)) is None


@pytest.mark.needs_game
def test_rewards():
    ev = conquest_events()[33]
    assert conquest.earned_tiers(ev, 0, 10 ** 6) == []
    top = conquest.earned_tiers(ev, 1, 10 ** 6)
    assert len(top) == len(ev.tiers)
    few = conquest.earned_tiers(ev, 1, 50)
    assert few and all(t.min_points <= 50 for t in few)


@pytest.mark.needs_game
def test_the_replies():
    state = a_state()
    influence(state, 1, PHOBOS, 100)
    state["guilds"]["1"]["markers"][str(PHOBOS)] = 2
    data = conquest.conquest_json(conquest_events()[33], conquest_zones(), state, 1, 7, {}, {}, T0 + 5)
    for key in ("id", "asset_bundle", "terrain_prefab", "conquest_points", "conquest_rank", "start_time", "end_time",
                "decay_time", "claim_reward_prevention", "user_conquest_data", "zone_data", "rewards"):
        assert key in data, key
    assert data["user_conquest_data"]["energy"]["battle_energy"] == 25 and data["conquest_rank"] == 1
    phobos = next(z for z in data["zone_data"] if z["id"] == PHOBOS)
    assert (phobos["marker_id"], phobos["my_guild_rank"], phobos["my_guild_influence"]) == (2, 1, 100)
    ranks = [t["min_rank"] for t in phobos["conquest_reward"]["tier"]]
    assert ranks == sorted(ranks)                                            # ascending: the client chains them
    board = conquest.board_json("x", "X", [{"id": 1, "name": "G1", "stat": 100, "rank": 1}], None)
    assert len(board["data"]) == 2                                           # the client reads two rows or more
    assert conquest.board_json("x", "X", [], None)["data"] == []
    assert not empty_strings(data) and not empty_strings(board)             # the client shows "" as "0"


def empty_strings(value, path="") -> list[str]:
    """Where a reply holds an empty string."""
    if value == "":
        return [path]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in empty_strings(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in empty_strings(v, f"{path}[{i}]")]
    return []


# ---- through the server --------------------------------------------------------------------------

def setup(tmp_path, content="[conquest]\nconquest = 33\n"):
    """Ann leads Owls with Cid, Bob leads Bats; all past the arena's tutorial fight."""
    api, clock = server(tmp_path, content=content, engine=True)
    creds = []
    for _ in range(3):
        _, c = login(api)
        call(api, c, "init")
        store = api._store_for(c["user_id"])
        p = store.load()
        p.arena.update(hunting_wins=1, rating=150, level=3)
        store.save(p)
        creds.append(c)
    ann, bob, cid = creds
    call(api, ann, "createFaction", faction_name="Owls")
    call(api, cid, "joinFaction", faction_id=str(api.guild_store.by_name("Owls")["id"]))
    return api, clock, ann, bob, cid


@pytest.mark.needs_game
def test_no_conquest_unless_set(tmp_path):
    api, clock = server(tmp_path, engine=True)
    _, ann = login(api)
    r, _ = call(api, ann, "init")
    assert r["conquest_data"] == []                                        # as recorded
    assert call(api, ann, "getConquestUpdate")[0]["conquest_data"] == []
    assert call(api, ann, "fightConquestBattle", zone_id="11")[0]["result_message"] == \
        ["There's no conquest on right now."]


@pytest.mark.needs_game
def test_a_conquest(tmp_path):
    api, clock, ann, bob, cid = setup(tmp_path)
    r, _ = call(api, ann, "init")
    cd = r["conquest_data"]
    assert (cd["id"], cd["start_time"], len(cd["zone_data"])) == (33, clock[0], 12)
    assert "conquest_data" in call(api, ann, "updateFaction", last_activity_id="0")[0]     # the Conquest button
    r, _ = call(api, ann, "fightConquestBattle", zone_id="11")
    assert r["result_message"] == ["There's no other guild to fight right now."]
    call(api, bob, "createFaction", faction_name="Bats")

    r, how = call(api, ann, "fightConquestBattle", zone_id="11")
    assert how.startswith("conquest fight in Phobos Station vs Player1000002 of Bats"), how
    bd = r["battle_data"]
    gained = bd["rewards"][0]["conquest_influence"]
    assert bd["host_is_attacker"] is False and gained == (50 if bd["winner"] == 1 else 5)
    cd = call(api, ann, "getConquestUpdate")[0]["conquest_data"]
    phobos = next(z for z in cd["zone_data"] if z["id"] == 11)
    assert (phobos["my_guild_rank"], phobos["my_guild_influence"]) == (1, gained)
    assert cd["user_conquest_data"]["influence"] == gained and cd["user_conquest_data"]["energy"]["battle_energy"] == 24

    assert call(api, cid, "setConquestMarker", zone_id="11", marker_id="1")[0]["result_message"] == \
        ["Only officers and the Guild Leader can set markers."]
    r, _ = call(api, ann, "setConquestMarker", zone_id="11", marker_id="1")
    assert next(z for z in r["conquest_data"]["zone_data"] if z["id"] == 11)["marker_id"] == 1

    clock[0] += conquest.ROUND_EVERY                                           # a scoring round
    cd = call(api, ann, "getConquestUpdate")[0]["conquest_data"]
    assert (cd["conquest_points"], cd["conquest_rank"]) == (50, 1)            # Phobos Station rank 1
    r, how = call(api, ann, "fightConquestBattle", zone_id="12")
    assert "8507" in r["battle_data"]["card_map"].values()                   # Owls' Phobos Station fortress

    r, _ = call(api, ann, "getConquestLeaderboard")
    board = r["conquest_leaderboard"]["data"]
    assert board[0]["name"] == "Owls" and [b["name"] for b in board[1:]] == ["Owls"]
    r, _ = call(api, bob, "getConquestZoneRankings", zone_id="11")
    assert [b["name"] for b in r["conquest_zone_data"]["rankings"]["data"]] == ["Bats", "Owls"]   # Bats unranked
    r, _ = call(api, cid, "getGuildInfluenceLeaderboard")
    assert [b["name"] for b in r["conquest_influence_leaderboard"]["data"]] == ["Player1000003", "Player1000001",
                                                                                 "Player1000003"]

    run = api.conquest_store.load()["run"]
    assert call(api, ann, "claimConquestReward")[0]["result_message"] == \
        ["The conquest's rewards can be claimed a little after it ends."]
    clock[0] = run["end"] + conquest.CLAIM_WAIT
    r, how = call(api, ann, "claimConquestReward")
    assert r["result"] is True and r["new_cards"], how
    assert call(api, ann, "claimConquestReward")[0]["result_message"] == ["You already claimed your conquest rewards."]
    assert call(api, cid, "claimConquestReward")[0]["result_message"] == ["You didn't take part in this conquest."]
    assert call(api, ann, "fightConquestBattle", zone_id="11")[0]["result_message"] == ["The conquest is over."]

    clock[0] = run["decay"] + 1                                               # a new run
    r, _ = call(api, ann, "getPreviousConquestTopLeaderboard")
    assert [(g["name"], g["conquest_points"]) for g in r["previous_conquest_top_leaderboard"]][0][0] == "Owls"


@pytest.mark.needs_game
def test_conquest_needs_a_guild(tmp_path):
    api, clock, ann, bob, cid = setup(tmp_path)
    r, _ = call(api, bob, "fightConquestBattle", zone_id="11")
    assert r["result_message"] == ["You must be in a guild to fight in Conquest."]
    assert call(api, ann, "fightConquestBattle", zone_id="99")[0]["result_message"] == ["There's no such zone."]


@pytest.mark.needs_game
def test_a_zones_rule_effect_works_in_its_fights(tmp_path, monkeypatch):
    seen = []

    class Watched(ga.conquest_calls.Battle):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self.passive)
    monkeypatch.setattr(ga.conquest_calls, "Battle", Watched)    # the conquest fight's module (game_api/conquest_calls.py)
    api, clock, ann, bob, cid = setup(tmp_path)
    call(api, bob, "createFaction", faction_name="Bats")
    r, how = call(api, ann, "fightConquestBattle", zone_id="5")              # Infested Depot: Devour
    assert how.startswith("conquest fight in Infested Depot"), how
    assert r["battle_data"]["battleground_effects"]["global"][0]["name"] == "Devour"
    assert {side: {name: value for name, (value, _) in rules.items()} for side, rules in seen[-1].items()} == \
        {0: {"devour": 4}, 1: {"devour": 4}}
