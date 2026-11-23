"""Tests for guild wars (wars.py, game_api/wars.py): the event data, the rules (scoring,
matchmaking, fortresses, rewards), the replies, and a war between two guilds played
through the server."""

import json

import pytest

import game_api as ga
import wars
from helpers import call, login, server

EVENTS = wars.load(ga.ASSET_DIR)
TESLA, CANNON = 2700, 2736          # a defensive fortress, a siege fortress (level 1)
T0 = 1_800_000_000


def a_state(now=T0, days=3):
    state = wars.new_state()
    wars.start_run(state, EVENTS[94], now, days)
    return state


def members(groups):
    return lambda gid: groups.get(gid)


# ---- the data ------------------------------------------------------------------------------------

@pytest.mark.needs_game
def test_the_war_events():
    assert len(EVENTS) == 94
    ev = EVENTS[94]
    assert (ev.name, len(ev.tiers), ev.repair_item) == ("Hypothermia War", 39, 11)
    assert ev.tiers[0] == wars.Tier(1, 1, 11000, (("card", 17240, 1),))
    assert [t.rank for t in ev.tiers] == sorted(t.rank for t in ev.tiers)
    with_effects = [e for e in EVENTS.values() if e.effects]
    assert len(with_effects) == 50
    assert all(day >= 0 for e in with_effects for day, _ in e.effects)


def test_the_event_matches_the_recorded_one(tmp_path):
    recorded = json.loads((ga.FIXTURE_DIR / "init.json").read_text("utf-8"))["body"]["active_war_event"]
    api = ga.GameApi(state_dir=tmp_path)   # never the real ../state
    start, end = int(recorded["start_time"]), int(recorded["end_time"])
    run = {"event": 94, "start": start, "end": end, "no_new": end - wars.NO_NEW_WARS,
           "decay": end + wars.CLAIM_DAYS * 86400}
    ours = wars.event_json(EVENTS[94], run, api._mini_packs())
    assert ours["rewards"] == recorded["rewards"]                      # the 39 tiers, mini packs and all
    assert set(ours) == set(recorded) - {"repair_item_drop_rate", "placement_war"}
    for key in ("war_length", "war_battle_prep_time", "claim_reward_prevention", "battle_energy_cost",
                "repair_item_health", "wait_message", "no_new_war_time", "decay_time", "prequeue_stop_time"):
        assert ours[key] == recorded[key], key
    assert ours["more_points_start_time"] == recorded["more_points_start_time"]       # the event's start + 7 days


# ---- the rules -----------------------------------------------------------------------------------

def test_points_for_a_fight():
    assert wars.fight_points(wars.CORE, True, 20) == 180 and wars.fight_points(wars.CORE, False, 20) == 20
    assert wars.fight_points(11, True, 30) == 70 and wars.fight_points(21, False, 3) == 10
    assert wars.fight_points(wars.CORE, True, 50) == 150


def test_ranking_points_for_a_match():
    assert wars.ranking_points(500, 0) == 900 and wars.ranking_points(0, 500) == 100     # a rout
    assert wars.ranking_points(510, 500) == 802 and wars.ranking_points(500, 510) == 198  # a close one
    assert wars.ranking_points(300, 300) == 200 and wars.ranking_points(0, 0) == 200      # a tie: both lose
    assert wars.ranking_points(500, 0, doubled=True) == 1800


@pytest.mark.needs_game
def test_matchmaking():
    state = a_state()
    groups = {"1": ["a", "b"], "2": ["c", "d"], "3": ["e"]}
    name = lambda gid: f"G{gid}"                                     # noqa: E731
    for gid in ("1", "2", "3"):
        wars.queue(state, gid, T0)
    made = wars.matchmake(state, T0, members(groups), name, min_members=2)
    assert [(m["a"], m["d"]) for m in made] == [("1", "2")] and "3" not in state["queue"]   # too small: dropped
    m = made[0]
    assert (m["end"] - m["start"], m["members"]["a"]["energy"]) == (wars.WAR_LENGTH, wars.ATTACKS)
    assert wars.matchmake(state, T0, members(groups), name, min_members=2) == []        # both busy now

    m["attacks"]["1"] = 3                                             # guild 2 made no attacks
    m["points"].update({"1": 400, "2": 0})
    done = wars.finish_matches(state, m["end"])
    assert done == [m] and m["result"]["ranking"] == {"1": 900, "2": 100}
    assert "1" in state["queue"] and "2" not in state["queue"]        # no attacks: out of the queue
    assert [r["id"] for r in wars.standings(state)] == ["1", "2"]

    wars.queue(state, "2", m["end"])                                   # 100 vs 900 points: too far apart
    assert wars.matchmake(state, m["end"], members(groups), name, min_members=2) == []
    assert wars.matchmake(state, m["end"], members(groups), name, min_members=2, gap=1.0) == []   # met < 8 h ago
    again = wars.matchmake(state, m["end"], members(groups), name, min_members=2, gap=1.0, rematch_after=0)
    assert [(x["a"], x["d"]) for x in again] == [("1", "2")]

    over = a_state()
    wars.queue(over, "1", T0)
    wars.finish_matches(over, over["run"]["end"] + 1)
    assert over["queue"] == {}                                           # the event's over: no one waits

    late = a_state()
    wars.queue(late, "1", T0)
    wars.queue(late, "2", T0)
    assert wars.matchmake(late, late["run"]["no_new"] + 1, members(groups), name, min_members=1) == []


@pytest.mark.needs_game
@pytest.mark.parametrize("seeds, pairs", [(None, [("1", "2")]), ({"1": 900, "2": 50, "3": 800}, [("1", "3")])])
def test_a_placement_brawl_seeds_the_matchmaking(seeds, pairs):
    state = a_state()
    groups = {"1": ["a"], "2": ["b"], "3": ["c"]}
    for gid in ("1", "2", "3"):
        wars.queue(state, gid, T0)
    made = wars.matchmake(state, T0, members(groups), str, min_members=1, seeds=seeds)
    assert [(m["a"], m["d"]) for m in made] == pairs                # the longest-waiting meets its nearest seed


@pytest.mark.needs_game
def test_placement_seeds_come_from_the_placement_brawl():
    import brawls
    events = brawls.load(ga.ASSET_DIR)
    assert events[345].war and events[345].use_forts and not events[344].war
    state = brawls.new_state()
    brawls.start_run(state, events[345], T0)                         # Hypothermia Placements
    brawls.record_fight(state, brawls.player_row(state, "a", "A"), "900001", True, 60, "Owls")
    assert brawls.placement_seeds(state, events) == {"900001": 60}
    brawls.start_run(state, events[344], T0 + 10 ** 6)               # a solo brawl after it: its top still counts
    assert brawls.placement_seeds(state, events) == {"900001": 60}
    brawls.start_run(state, events[344], T0 + 2 * 10 ** 6)           # and no longer once it's gone
    assert brawls.placement_seeds(state, events) == {}


@pytest.mark.needs_game
def test_a_match_ends_with_the_event():
    state = a_state(days=1)
    for gid in ("1", "2"):
        wars.queue(state, gid, T0)
    later = state["run"]["end"] - wars.NO_NEW_WARS - 60
    m = wars.matchmake(state, later, members({"1": ["a"], "2": ["b"]}), str, min_members=1)[0]
    assert m["end"] == state["run"]["end"]


@pytest.mark.needs_game
def test_fortresses_in_a_fight():
    state = a_state()
    m = {"a": "1", "d": "2", "damage": {"1": {}, "2": {}}}
    mine = {"21": CANNON, "22": CANNON + 1, "11": TESLA}
    theirs = {"11": TESLA, "12": TESLA + 1, "21": CANNON}
    assert wars.fight_fortresses(m, "1", mine, "2", theirs, wars.CORE) == ([CANNON, CANNON + 1], [TESLA, TESLA + 1])
    assert wars.fight_fortresses(m, "1", mine, "2", theirs, 12) == ([CANNON, CANNON + 1], [TESLA + 1])
    assert wars.fight_fortresses(m, "1", mine, "2", theirs, 21) == ([], [CANNON])   # siege on siege: none of ours
    assert wars.fight_fortresses(m, "1", mine, "2", theirs, 22) == ([], [])         # an empty slot
    m["damage"]["2"]["11"] = wars.FORT_HEALTH                                         # destroyed
    m["damage"]["1"]["21"] = wars.FORT_HEALTH
    assert wars.fight_fortresses(m, "1", mine, "2", theirs, wars.CORE) == ([CANNON + 1], [TESLA + 1])
    assert wars.fight_fortresses(m, "1", mine, "2", theirs, 11) == ([CANNON + 1], [])


@pytest.mark.needs_game
def test_recording_a_fight():
    state = a_state()
    for gid in ("1", "2"):
        wars.queue(state, gid, T0)
    m = wars.matchmake(state, T0, members({"1": ["a"], "2": ["b"]}), str, min_members=1)[0]
    assert wars.record_fight(state, m, "a", "1", "b", 11, True, 10) == (90, 180)          # defensive: double damage
    assert wars.record_fight(state, m, "a", "1", "b", 21, True, 10) == (90, 90)
    assert wars.record_fight(state, m, "a", "1", "b", wars.CORE, True, 10) == (190, 0)     # the core never falls
    assert wars.record_fight(state, m, "a", "1", "b", 21, False, 10) == (10, 0)
    for _ in range(11):                                                 # 90 + 11 x 90 > 1000
        wars.record_fight(state, m, "a", "1", "b", 21, True, 10)
    assert wars.slot_health(m, "2", 21) == 0
    assert wars.record_fight(state, m, "a", "1", "b", 21, True, 10) == (90, 0)              # destroyed: points, no damage
    assert wars.record_fight(state, m, "a", "1", "b", 12, True, 10, fortress=False) == (90, 0)   # an empty slot
    assert wars.slot_health(m, "2", 12) == wars.FORT_HEALTH
    a, b = m["members"]["a"], m["members"]["b"]
    assert (a["energy"], a["wins"], a["losses"], a["points"]) == (wars.ATTACKS - 17, 16, 1, 1550)
    assert (b["defend_wins"], b["defend_losses"]) == (1, 16)
    assert m["points"] == {"1": 1550, "2": 0} and state["members"]["a"]["points"] == 1550


@pytest.mark.needs_game
def test_rewards():
    ev = EVENTS[94]
    assert wars.earned_tiers(ev, 900, 10**6) == []                       # every tier needs a rank of 800 or better
    tiers = wars.earned_tiers(ev, 800, 20)
    assert [t.rewards for t in tiers] == [(("pack", 1000, 1),)]
    assert len(wars.earned_tiers(ev, 1, 11000)) == 39
    state = a_state()
    state["guilds"]["1"] = {"points": 900, "name": "G1"}
    state["members"]["a"] = {"points": 500, "claimed": 0}
    run = state["run"]
    assert not wars.claimable(state, run["end"]) and wars.claimable(state, run["end"] + wars.CLAIM_WAIT)
    assert not wars.owes_rewards(state, ev, "a", "1", run["end"] - 1)       # still at war
    assert wars.owes_rewards(state, ev, "a", "1", run["end"] + 1)
    state["members"]["a"]["claimed"] = 1
    assert not wars.owes_rewards(state, ev, "a", "1", run["end"] + 1)


@pytest.mark.needs_game
def test_the_war_as_the_client_gets_it():
    state = a_state()
    wars.queue(state, "1", T0)
    slots = lambda gid: {"11": TESLA} if gid == "1" else {"21": CANNON}     # noqa: E731
    queued = wars.faction_war_json(state, "1", "G1", "a", slots, str, T0)
    assert (queued["defender_faction_id"], queued["attacker_faction_id"], queued["battle_energy_recharge_time"]) == \
           ("0", "1", -1)
    assert queued["attacker_faction_members"] == [] == queued["defender_faction_members"]
    assert queued["message"] == queued["queue_message"] == "Waiting for a match..."   # never "" (shown as "0")
    wars.queue(state, "2", T0)
    m = wars.matchmake(state, T0, members({"1": ["a"], "2": ["b"]}), lambda g: f"G{g}", min_members=1)[0]
    wars.record_fight(state, m, "a", "1", "b", 11, True, 10)
    fw = wars.faction_war_json(state, "2", "G2", "b", slots, lambda u: u.upper(), T0 + wars.PREP_TIME)
    assert (fw["attacker_faction_id"], fw["defender_faction_id"], fw["attacker_faction_points"]) == ("1", "2", 90)
    assert fw["battle_energy"] == wars.ATTACKS and fw["max_battle_energy"] == wars.ATTACKS
    assert fw["attacker_faction_members"] == [{"member_name": "A", "current_war_points": 90, "wins": 1, "losses": 0,
                                               "defend_wins": 0, "defend_losses": 0}]
    health = {s["slot_id"]: s["health"] for s in fw["defender_faction_fortress_slots"]}
    assert health == {11: 82, 12: 100, 21: 100, 22: 100}                  # 180 of 1000 damage
    assert fw["message"] == "At war with G1!"
    info = wars.event_info_json(state, "1", "a", ["a"], lambda u: "Ann")
    assert info["user_info"] == {"faction_war_points": 90, "claimed_rewards": 0}
    assert info["members"] == [{"member_name": "Ann", "faction_war_points": 90}]


# ---- through the server --------------------------------------------------------------------------

WAR_CONTENT = "[wars]\nwar = 94\nmin_members = 1\npoint_gap_percent = 100\nrematch_hours = 0\n"


def two_guilds(tmp_path, content=WAR_CONTENT):
    """Ann leads Owls, Bob leads Bats; both past the tutorial."""
    api, clock = server(tmp_path, content=content, engine=True)
    _, ann = login(api)
    _, bob = login(api)
    for creds in (ann, bob):
        call(api, creds, "init")
    call(api, ann, "createFaction", faction_name="Owls")
    call(api, bob, "createFaction", faction_name="Bats")
    return api, clock, ann, bob


@pytest.mark.needs_game
def test_no_war_event_unless_set(tmp_path):
    api, clock = server(tmp_path, engine=True)
    _, ann = login(api)
    r, _ = call(api, ann, "init")
    assert r["active_war_event"]["id"] == "0" and r["active_war_event"]["decay_time"] == 0   # not the recording's:
    assert r["active_war_event"]["wait_message"] == "No guild war is on right now."      # an idle one, so the
    assert "faction_war" not in call(api, ann, "updateFactionWar")[0]       # Wars tab draws
    r, how = call(api, ann, "fightFactionWar")
    assert r["result_message"] == ["There's no guild war on right now."]


@pytest.mark.needs_game
def test_no_idle_war_event_while_a_raid_is_set(tmp_path):
    """Any active_war_event runs the Wars tab's war display, which hides the raid panel."""
    api, clock = server(tmp_path, content="[raids]\nraid = 161\n", engine=True)
    _, ann = login(api)
    r, _ = call(api, ann, "init")
    assert "active_war_event" not in r and "faction_war_event_info" not in r   # nor the recording's
    call(api, ann, "createFaction", faction_name="Owls")
    r, _ = call(api, ann, "updateFaction", last_activity_id="0")
    assert "active_war_event" not in r and r["current_raids"]


@pytest.mark.needs_game
def test_a_war_between_two_guilds(tmp_path):
    api, clock, ann, bob = two_guilds(tmp_path)
    store = api.guild_store
    owls, bats = store.by_name("Owls"), store.by_name("Bats")
    owls["slots"].update({"21": CANNON})
    bats["slots"].update({"11": TESLA})
    store.save(owls)
    store.save(bats)
    r, _ = call(api, ann, "init")
    ev = r["active_war_event"]
    assert (ev["id"], ev["start_time"], ev["end_time"]) == ("94", str(clock[0]), str(clock[0] + 3 * 86400))
    assert r["faction_war_event_info"]["our_rank"] == 1 and "faction_war" not in r

    r, how = call(api, ann, "fightFactionWar")
    assert how == "Owls queued for a war" and r["faction_war"]["defender_faction_id"] == "0"
    r, how = call(api, bob, "fightFactionWar")
    assert how == "Bats queued and matched with Owls"
    fw = r["faction_war"]
    assert (fw["attacker_faction_name"], fw["defender_faction_name"]) == ("Owls", "Bats")
    news = [(n["type"], json.loads(n["data"])) for n in call(api, ann, "updateFaction", last_activity_id="0")[0]["faction_news"]]
    assert (11, {"target_faction_name": "Bats"}) in news

    r, how = call(api, ann, "startFactionWarBattle", slot_id="11")
    assert r["result_message"] == ["The war hasn't started yet: attacks open after the preparation."]
    r, how = call(api, ann, "removeFactionCard", slot_id="21")                  # preparation: still allowed
    assert r["result"] is True and how.startswith("fortress: removed")
    owls = store.by_name("Owls")
    owls["slots"]["21"] = CANNON                                              # (back, without spending points)
    store.save(owls)

    clock[0] += wars.PREP_TIME
    gold = api._store_for(ann["user_id"]).load().gold
    r, how = call(api, ann, "startFactionWarBattle", slot_id="11")          # Bats' Tesla Coil
    assert how.startswith("war fight on Bats's slot 11 vs Player1000002's defense deck with 1 of their fortresses "
                          "and 1 of ours"), how
    bd = r["battle_data"]
    assert bd["host_is_attacker"] is False                                   # surge: the defender moves first
    assert bd["attack_commander"] == "1000" and bd["enemy_name"] == "Player1000002"
    first_played = int(next(iter(bd["card_map"])))
    assert first_played < 101                     # the enemy's (its cards and fortress are 1..100): it moved first
    assert str(TESLA) in bd["card_map"].values() and str(CANNON) in bd["card_map"].values()
    won = bd["winner"] == 1
    reward = bd["rewards"][0]
    assert reward["gold"] == (70 if won else 35) and reward["war_points"] == (100 - len(bd["turn"]) if won else 10)
    assert reward["slot_damage"] == (-(-2 * reward["war_points"] * 100 // wars.FORT_HEALTH) if won else 0)
    assert api._store_for(ann["user_id"]).load().gold == gold + reward["gold"]
    fw = r["faction_war"]
    assert fw["battle_energy"] == wars.ATTACKS - 1 and fw["attacker_faction_points"] == reward["war_points"]

    r, how = call(api, bob, "buyFactionCard", slot_id="12", card_id=str(TESLA))
    assert r["result_message"] == ["You can't edit fortress cards while at war."]
    for _ in range(3):
        call(api, bob, "startFactionWarBattle", slot_id="1")
    r, _ = call(api, bob, "updateFactionWar")
    assert r["faction_war"]["defender_faction_members"][0]["wins"] + \
        r["faction_war"]["defender_faction_members"][0]["losses"] == 3

    clock[0] += wars.WAR_LENGTH                                              # the match is over
    r, _ = call(api, ann, "getFactionWarLeaderboard")
    board = r["faction_war_leaderboard"]
    assert sorted(b["faction_name"] for b in board) == ["Bats", "Owls"]
    assert {b["faction_war_points"] for b in board} <= set(range(100, 901))
    news = [n["type"] for n in call(api, ann, "updateFaction", last_activity_id="0")[0]["faction_news"]]
    assert 12 in news or 13 in news
    r, _ = call(api, ann, "updateFactionWar")
    assert r["faction_war"]["defender_faction_id"] != "0"                   # both attacked: matched again


@pytest.mark.needs_game
def test_claiming_rewards(tmp_path):
    api, clock, ann, bob = two_guilds(tmp_path)
    call(api, ann, "fightFactionWar")
    call(api, bob, "fightFactionWar")
    clock[0] += wars.PREP_TIME
    for _ in range(5):
        call(api, ann, "startFactionWarBattle", slot_id="1")
    r, _ = call(api, ann, "claimFactionWarRewards")
    assert r["result_message"] == ["The war's rewards can be claimed a little after it ends."]
    state = api.war_store.load()
    clock[0] = state["run"]["end"] + wars.CLAIM_WAIT
    r, how = call(api, ann, "claimFactionWarRewards")
    assert r["result"] is True, how
    assert r["new_cards"] and r["new_items"] == [] and r["faction_war_event_info"]["user_info"]["claimed_rewards"] == 1
    owned = api._store_for(ann["user_id"]).load().cards
    assert all(c in owned for c in r["new_cards"])
    r, _ = call(api, ann, "claimFactionWarRewards")
    assert r["result_message"] == ["You already claimed your war rewards."]
    r, _ = call(api, bob, "claimFactionWarRewards")                         # Bob never attacked
    assert r["result_message"] == ["You didn't earn a reward in this war."]


@pytest.mark.needs_game
def test_only_officers_start_wars_and_rewards_protect_from_kicks(tmp_path):
    api, clock = server(tmp_path, content=WAR_CONTENT, engine=True)
    _, ann = login(api)
    _, cid = login(api)
    _, bob = login(api)
    for creds in (ann, cid, bob):
        call(api, creds, "init")
    call(api, ann, "createFaction", faction_name="Owls")
    call(api, cid, "joinFaction", faction_id=str(api.guild_store.by_name("Owls")["id"]))
    call(api, bob, "createFaction", faction_name="Bats")
    r, _ = call(api, cid, "fightFactionWar")
    assert r["result_message"] == ["Only officers and the Guild Leader can start a war."]
    call(api, ann, "fightFactionWar")
    call(api, bob, "fightFactionWar")
    clock[0] += wars.PREP_TIME
    for _ in range(3):
        call(api, cid, "startFactionWarBattle", slot_id="1")
    clock[0] = api.war_store.load()["run"]["end"] + 1
    r, _ = call(api, ann, "kickFactionMember", target_user_id=cid["user_id"])
    assert r["result_message"] == ["Player1000002 hasn't claimed their war rewards yet."]


@pytest.mark.needs_game
def test_the_war_needs_members(tmp_path):
    api, clock, ann, bob = two_guilds(tmp_path, content="[wars]\nwar = 94\n")
    r, _ = call(api, ann, "fightFactionWar")
    assert r["result_message"] == ["A guild needs at least 5 members to start a war."]
