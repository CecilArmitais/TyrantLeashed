"""Tests for raids (raids.py), the raid calls in game_api.py and raid fortresses in the
battle engine."""

import json
import math
import random

import pytest

import game_api as ga
import raids
import test_battle as tb
import test_arena as ta
from battle.engine import ATTACKER, DEFENDER, FIRST_UID, Battle, RandomChooser, Setup, shuffled_deck

T = tb.T
DAY = 86400
HOUR = 3600


@pytest.fixture(scope="module")
def all_raids():
    return raids.load(ga.ASSET_DIR)


@pytest.fixture(scope="module")
def cards():
    return ta.real_cards()


# ---- the data ---------------------------------------------------------------------------------------

def test_the_raid_file(all_raids, cards):
    assert len(all_raids) == 161
    icarus = all_raids[161]
    assert (icarus.name, icarus.health, icarus.health_per_level, icarus.duration) == ("Sentinel Icarus", 15000, 1000, 28800)
    assert (icarus.deck_size, len(icarus.deck), icarus.fortresses, icarus.levels) == (10, 11, (18954, 18964), 26)
    assert len(icarus.tiers) == 26 and (icarus.tiers[0].damage, icarus.tiers[0].level) == (200, 1)
    assert icarus.tiers[0].rewards == (("pack", 1000, 5),)
    assert icarus.max_health(1, 1) == 15000 and icarus.max_health(3, 1) == 17000
    assert icarus.max_health(1, 0.02) == 300 and icarus.max_health(3, 0.02) == 340
    every = [c for r in all_raids.values() for c in (r.commander, *r.deck, *r.fortresses)]
    assert all(cards.get(c) is not None for c in every)


def test_the_boss_deck_gets_stronger_with_the_level(all_raids, cards):
    icarus = all_raids[161]
    c1, f1, d1 = raids.boss_deck(icarus, 1, cards, random.Random(1))
    assert len(d1) == 10 and len(f1) == 2 and {f.base_id for f in f1} == {18954, 18964}
    assert c1.level == 1 and all(c.level == 1 for c in (*f1, *d1))           # level 1: no upgrades
    cmax, fmax, dmax = raids.boss_deck(icarus, 26, cards, random.Random(1))
    assert all(cards.next_level(c.id) is None for c in (cmax, *fmax, *dmax))  # the top level: all of them
    c, f, d = raids.boss_deck(icarus, 10, cards, random.Random(1))
    slots = (c1, *f1, *d1)
    room = sum(raids._top_level(cards, x).level - x.level for x in slots)
    want = math.ceil(room * 9 / 25)
    assert sum(x.level - 1 for x in (c, *f, *d)) == want                    # the optimizer's share


def test_fight_damage():
    assert raids.fight_damage(0, 0, 2) == 15                 # a quick win
    assert raids.fight_damage(4, 0, 2) == 19
    assert raids.fight_damage(3, 2, 2) == 8                  # a loss with the boss untouched
    assert raids.fight_damage(0, 5, 5) == 5
    assert raids.fight_damage(0, 50, 5) == 0                 # never below 0


def test_earned_tiers(all_raids):
    icarus = all_raids[161]
    assert raids.earned_tiers(icarus, 199, 5) == []
    assert [t.id for t in raids.earned_tiers(icarus, 1000, 5)] == [26, 25, 24, 23, 22]
    assert [t.id for t in raids.earned_tiers(icarus, 1000, 3)] == [26, 25, 24]   # the level holds it back


# ---- a run -----------------------------------------------------------------------------------------

def test_energy_comes_back_while_the_raid_runs(all_raids):
    icarus = all_raids[161]
    run = raids.new_run(icarus, T, 3, 0.02)
    assert (run["energy"], run["hp"], run["level"], run["end"] - T) == (13, 300, 1, 3 * DAY)
    raids.refresh(run, icarus, T + 1799, 0.02)
    assert run["energy"] == 13
    raids.refresh(run, icarus, T + 3 * 1800 + 5, 0.02)
    assert run["energy"] == 16 and run["energy_updated"] == T + 3 * 1800    # the part-point carries on
    raids.refresh(run, icarus, T + 2 * DAY, 0.02)
    assert run["energy"] == 25                                               # the cap
    run["energy"] = 0
    run["energy_updated"] = run["end"] - 1800
    raids.refresh(run, icarus, run["end"] + DAY, 0.02)
    assert run["energy"] == 1                                                # none after the end


def test_a_level_not_cleared_in_time_heals_the_boss(all_raids):
    icarus = all_raids[161]
    run = raids.new_run(icarus, T, 3, 0.02)
    assert raids.record_fight(run, icarus, 20, T, 0.02) is False
    assert (run["hp"], run["level_end"], run["energy"], run["damage"]) == (280, T + 8 * HOUR, 12, 20)
    raids.refresh(run, icarus, T + 8 * HOUR - 1, 0.02)
    assert run["hp"] == 280
    raids.refresh(run, icarus, T + 8 * HOUR, 0.02)
    assert (run["hp"], run["level_end"], run["damage"]) == (300, 0, 20)     # the player keeps their damage


def test_clearing_a_level(all_raids):
    icarus = all_raids[161]
    run = raids.new_run(icarus, T, 3, 0.02)
    run["hp"] = 10
    assert raids.record_fight(run, icarus, 15, T, 0.02) is True
    assert (run["level"], run["hp"], run["level_end"]) == (2, 320, 0)


def test_when_the_reward_can_be_claimed(all_raids):
    icarus = all_raids[161]
    run = raids.new_run(icarus, T, 3, 0.02)
    end = run["end"]
    assert not raids.claimable(run, end + 599) and raids.claimable(run, end + 600)
    run["level_end"] = end + HOUR                                            # a level still running at the end
    assert not raids.claimable(run, end + 600) and raids.claimable(run, end + HOUR + 600)
    assert not raids.claimable(run, run["decay"])
    run["claimed"] = 1
    assert not raids.claimable(run, end + HOUR + 600)


# ---- fortresses in the engine ------------------------------------------------------------------------

def test_fortresses_come_in_with_the_bosss_first_turn(cards):
    rng = random.Random(4)
    deck = [cards[i] for i in (12, 13, 2, 3)]
    foe = [cards[i] for i in (75, 2024, 109)]
    forts = (cards[18954], cards[18964])
    b = Battle(Setup(cards[1000], shuffled_deck(deck, FIRST_UID[ATTACKER], rng), cards[1001],
                     shuffled_deck(foe, FIRST_UID[DEFENDER], rng), defender_fortresses=forts),
               chooser=RandomChooser(random.Random(4)), cards=cards)
    b.start()
    assert not b.sides[DEFENDER].structures
    b.play(b.sides[ATTACKER].hand[0])
    plays = [p["card_uid"] for p in b.turns[2]["plays"]]
    assert plays[:2] == [104, 105] and plays[2] in range(101, 104)           # after the deck's uids, first
    assert [u.card.id for u in b.sides[DEFENDER].structures][:2] == [18954, 18964]
    assert b.sides[DEFENDER].played == [plays[2]]                            # not counted as played cards
    assert (104, 18954) in b.played and b.sides[DEFENDER].next_uid == 106
    assert [u.delay for u in b.sides[DEFENDER].structures[:2]] == [3, 5]     # from the boss's next turn
    b.play(b.sides[ATTACKER].hand[0])
    assert [u.delay for u in b.sides[DEFENDER].structures[:2]] == [2, 4]


# ---- the game server ---------------------------------------------------------------------------------

def game(tmp_path, raid=161, bonds=2000, extra=""):
    if raid is not None:
        (tmp_path / "content.toml").write_text(f"[raids]\nraid = {raid}\n{extra}", "utf-8")
    clock = [T]
    api = ga.GameApi(fixture_dir=ga.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: clock[0])
    api.rng_factory = lambda: random.Random(1)
    tb.ask(api, "init")
    p = api.player()
    p.war_bonds = bonds
    api.player_store.save(p)
    return api, clock


def fight(api):
    return tb.ask(api, "fightRaidBattle", raid_id="161", raid_level=str(api.player().raid["level"]))


def test_no_raid_by_default(tmp_path):
    api, _ = game(tmp_path, raid=None)
    r, _ = tb.ask(api, "init")
    assert r["current_raids"] == [] and r["raid_info"] is None and r["faction"] == {"faction_id": 0}
    r, how = tb.ask(api, "getRaidInfo")
    assert r["result"] is False and how == "refused getRaidInfo: no raid set in content.toml"


def test_a_raid_in_init_and_the_guild(tmp_path):
    api, _ = game(tmp_path)
    r, _ = tb.ask(api, "init")
    raid = r["current_raids"][0]
    assert (raid["id"], raid["name"], raid["start_time"], raid["end_time"]) == (161, "Sentinel Icarus", T, T + 3 * DAY)
    assert raid["claim_reward_prevention"] == 600 and len(raid["rewards"]["tier"]) == 26
    assert raid["rewards"]["tier"][0] == {"id": "26", "damage": 200, "level": 1, "pack": {"id": 1000}}
    assert raid["rewards"]["tier"][2]["mini_pack"]["name"] == "Legendary Pack"
    info = r["raid_info"][0]
    assert (info["raid_level"], info["health"], info["max_health"], info["status"]) == (1, 300, 300, 1)
    name = api.player().name or ga.OFFLINE_NAME
    assert info["members"] == [{"member_id": "1000001", "member_name": name, "damage": 0}]
    assert info["energy"]["battle_energy"] == 13 and info["energy"]["max_battle_energy"] == 25
    guild = r["faction"]
    assert guild["faction_id"] == str(raids.GUILD_ID) and guild["name"] == "Offline Guild"
    assert guild["members"][0]["member_role"] == raids.LEADER and guild["leader_id"] == "1000001"
    r, how = tb.ask(api, "getRaidInfo")
    assert r["result"] and how == "Sentinel Icarus: level 1, health 300/300, raid energy 13/25, your damage 0"


def test_the_content_file_sets_the_guild_and_the_boss_health(tmp_path):
    api, _ = game(tmp_path, extra='guild_name = "Solo Raiders"\nhealth_percent = 100\ndays = 1\n')
    r, _ = tb.ask(api, "getRaidInfo")
    assert r["faction"]["name"] == "Solo Raiders" and r["raid_info"][0]["max_health"] == 15000
    assert r["current_raids"][0]["end_time"] == T + DAY


def slow_deck(api):
    """A deck of delay-4 cards, so the boss gets turns (raid commanders have 2-5 health,
    and a fast deck often wins on turn 1)."""
    cards, _ = api._engine_data()
    slow = [cards[64582]] * 4 + [cards[64552]] * 4
    api._player_deck = lambda _cards: (cards[1000], slow)


def test_a_raid_fight(tmp_path):
    api, clock = game(tmp_path)
    slow_deck(api)
    r, how = fight(api)
    assert r["result"] is True
    bd = r["battle_data"]
    assert bd["enemy_name"] == "Sentinel Icarus" and bd["winner"] in (0, 1)
    damage = bd["rewards"][0]["raid_damage"]
    assert bd["rewards"] == [{"user_id": "1000001", "gold": 0, "card": [], "item": [], "raid_damage": damage}]
    assert damage >= 5 and how.startswith("raid fight vs Sentinel Icarus level 1: ")
    assert {"18954", "18964"} <= {bd["card_map"].get(p["card_uid"] if isinstance(p["card_uid"], str) else str(p["card_uid"]))
                                  for p in bd["turn"]["2"]["plays"]}       # the fortresses
    assert bd["battleground_effects"] == {"global": [], "attacker": [], "defender": []}
    run = api.player().raid
    assert (run["energy"], run["damage"], run["hp"], run["fights"]) == (12, damage, 300 - damage, 1)
    assert run["level_end"] == T + 8 * HOUR
    r, _ = tb.ask(api, "getRaidInfo")
    info = r["raid_info"][0]
    assert (info["status"], info["raid_level_start"], info["raid_level_end"]) == (0, T, T + 8 * HOUR)
    assert info["members"][0]["damage"] == damage and info["energy"]["battle_energy"] == 12
    again = ga.GameApi(fixture_dir=ga.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: clock[0])
    assert again.player().raid == run                                        # saved


def test_out_of_raid_energy_and_the_refill(tmp_path):
    api, _ = game(tmp_path)
    p = api.player()
    p.raid["energy"] = 0
    api.player_store.save(p)
    r, how = fight(api)
    assert r["result"] is False and r["result_message"] == ["You're out of raid energy."]
    r, how = tb.ask(api, "refillEventBattleEnergy", event_type="2", event_id="161")
    assert r["result"] and how == "raid energy refilled: +25 for 125 war bonds"
    assert api.player().war_bonds == 2000 - 125 and r["raid_info"][0]["energy"]["battle_energy"] == 25
    r, how = tb.ask(api, "refillEventBattleEnergy", event_type="2", event_id="161")
    assert r["result"] is False and how == "refused raid refill: full"


def test_clearing_levels_by_fighting(tmp_path):
    api, _ = game(tmp_path)
    p = api.player()
    p.raid["hp"], p.raid["energy"] = 1, 25
    api.player_store.save(p)
    r, how = fight(api)
    assert "level 1 cleared" in how
    run = api.player().raid
    assert (run["level"], run["hp"], run["level_end"]) == (2, 320, 0)
    r, _ = tb.ask(api, "getRaidLeaderboard", raid_id="161")
    assert r["raid_leaderboard"] == [{"faction_id": str(raids.GUILD_ID), "name": "Offline Guild", "raid_level": 2}]


def test_forfeit_during_the_replay_gives_the_same_result(tmp_path):
    api, _ = game(tmp_path)
    r, _ = fight(api)
    bid = r["battle_data"]["battle_id"]
    f, how = tb.ask(api, "forfeitBattle", battle_id=str(bid))
    assert f["result"] and f["battle_data"] == r["battle_data"] and how.startswith("forfeit during a raid fight")
    assert api.player().raid["energy"] == 12                                 # not charged twice


def owned(api):
    return sum(int(c["num_owned"]) for c in api.player().cards.values())


def test_claiming_the_reward(tmp_path):
    api, clock = game(tmp_path)
    p = api.player()
    p.raid["damage"], p.raid["level"] = 1000, 5
    api.player_store.save(p)
    r, how = tb.ask(api, "claimRaidReward", raid_id="161")
    assert r["result"] is False and how == "refused raid claim: early"
    clock[0] = p.raid["end"] + 600
    before = owned(api)
    r, how = tb.ask(api, "claimRaidReward", raid_id="161")
    # tiers 26-22: 5 epic packs (20), a card, 5 Legendary Packs (20), a card, a card
    assert r["result"] and how.startswith("raid reward: 5 tiers, 43 cards (")
    assert len(r["new_cards"]) == 43 and all(isinstance(c, str) for c in r["new_cards"])
    assert r["new_cards"].count("60885") >= 2 and "68339" in r["new_cards"]
    assert owned(api) == before + 43
    assert r["raid_info"][0]["claimed_rewards"] == 1
    r, how = tb.ask(api, "claimRaidReward", raid_id="161")
    assert r["result"] is False and how == "refused raid claim: claimed"


def test_a_new_run_after_the_last_one_is_over(tmp_path):
    api, clock = game(tmp_path)
    fight(api)
    first = api.player().raid
    clock[0] = first["decay"]
    r, _ = tb.ask(api, "getRaidInfo")
    run = api.player().raid
    assert run["start"] == clock[0] and run["damage"] == 0 and run["energy"] == 13


def test_reward_packs_from_an_emptied_set(tmp_path):
    api, _ = game(tmp_path)
    cards, _ = api._engine_data()
    got = api._open_raid_reward("mini_pack", 94, 2, cards, random.Random(2))   # set 4000: no level-1 cards
    assert got and all(cards[c].set == raids.FALLBACK_REWARD_SET for c in got)


def test_a_raid_with_a_battleground_effect(tmp_path):
    api, _ = game(tmp_path, raid=55)
    slow_deck(api)
    r, how = tb.ask(api, "fightRaidBattle", raid_id="55", raid_level="1")
    assert r["result"], how
    bd = r["battle_data"]
    entry = bd["battleground_effects"]["defender"][0]
    assert entry["name"] == "Silus' Pact" and entry["card_uid"] == -5
    acts = [a for t in bd["turn"].values() if t for a in t.get("actions", []) if a["card_uid"] == -5]
    assert acts and all(int(t) % 2 == 0 for t, turn in bd["turn"].items() if turn
                        for a in turn.get("actions", []) if a["card_uid"] == -5)
