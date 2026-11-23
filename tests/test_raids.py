"""Tests for raids (raids.py), the raid calls in game_api/raids.py and raid fortresses in the
battle engine."""

import json
import math
import random

import pytest

import game_api as ga
import raids
from battle.engine import ATTACKER, DEFENDER, FIRST_UID, Battle, RandomChooser, Setup, shuffled_deck
from helpers import raid_game as game, raid_run as run_of, raid_fight as fight, T, ask, real_cards

DAY = 86400
HOUR = 3600


@pytest.fixture(scope="module")
def all_raids():
    return raids.load(ga.ASSET_DIR)


@pytest.fixture(scope="module")
def cards():
    return real_cards()


# ---- the data ---------------------------------------------------------------------------------------

@pytest.mark.needs_game
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


@pytest.mark.needs_game
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


@pytest.mark.needs_game
def test_earned_tiers(all_raids):
    icarus = all_raids[161]
    assert raids.earned_tiers(icarus, 199, 5) == []
    assert [t.id for t in raids.earned_tiers(icarus, 1000, 5)] == [26, 25, 24, 23, 22]
    assert [t.id for t in raids.earned_tiers(icarus, 1000, 3)] == [26, 25, 24]   # the level holds it back


# ---- a run -----------------------------------------------------------------------------------------

@pytest.mark.needs_game
def test_energy_comes_back_while_the_raid_runs(all_raids):
    icarus = all_raids[161]
    run = raids.new_run(icarus, T, 3, 0.02)
    assert (run["hp"], run["level"], run["end"] - T, run["members"]) == (300, 1, 3 * DAY, {})
    m = raids.member(run, 1000001, T)
    assert (m["energy"], run["members"]["1000001"] is m) == (13, True)
    raids.refresh_member(run, m, T + 1799)
    assert m["energy"] == 13
    raids.refresh_member(run, m, T + 3 * 1800 + 5)
    assert m["energy"] == 16 and m["energy_updated"] == T + 3 * 1800       # the part-point carries on
    raids.refresh_member(run, m, T + 2 * DAY)
    assert m["energy"] == 25                                               # the cap
    m["energy"] = 0
    m["energy_updated"] = run["end"] - 1800
    raids.refresh_member(run, m, run["end"] + DAY)
    assert m["energy"] == 1                                                # none after the end


@pytest.mark.needs_game
def test_a_level_not_cleared_in_time_heals_the_boss(all_raids):
    icarus = all_raids[161]
    run = raids.new_run(icarus, T, 3, 0.02)
    m = raids.member(run, 1000001, T)
    assert raids.record_fight(run, m, icarus, 20, T, 0.02) == (True, False)    # it started the level's clock
    assert (run["hp"], run["level_end"], m["energy"], m["damage"]) == (280, T + 8 * HOUR, 12, 20)
    assert raids.record_fight(run, m, icarus, 5, T + 60, 0.02) == (False, False)
    assert raids.refresh(run, icarus, T + 8 * HOUR - 1, 0.02) is None and run["hp"] == 275
    assert raids.refresh(run, icarus, T + 8 * HOUR, 0.02) == 1                 # level 1 lost
    assert (run["hp"], run["level_end"], m["damage"]) == (300, 0, 25)        # the member keeps their damage


@pytest.mark.needs_game
def test_members_share_the_boss(all_raids):
    icarus = all_raids[161]
    run = raids.new_run(icarus, T, 3, 0.02)
    a, b = raids.member(run, 1, T), raids.member(run, 2, T)
    raids.record_fight(run, a, icarus, 200, T, 0.02)
    assert raids.record_fight(run, b, icarus, 100, T + 60, 0.02) == (False, True)   # b lands the final blow
    assert (run["level"], run["hp"], a["damage"], b["damage"], a["energy"], b["energy"]) == (2, 320, 200, 100, 12, 12)
    info = raids.raid_info(icarus, run, 1, {"1": "Ann", "2": "Bo"}, T + 120, 0.02)[0]
    assert info["members"] == [{"member_id": "1", "member_name": "Ann", "damage": 200},
                               {"member_id": "2", "member_name": "Bo", "damage": 100}]
    assert info["energy"]["battle_energy"] == 12


@pytest.mark.needs_game
def test_clearing_a_level(all_raids):
    icarus = all_raids[161]
    run = raids.new_run(icarus, T, 3, 0.02)
    run["hp"] = 10
    assert raids.record_fight(run, raids.member(run, 1, T), icarus, 15, T, 0.02) == (True, True)
    assert (run["level"], run["hp"], run["level_end"]) == (2, 320, 0)


@pytest.mark.needs_game
def test_when_the_reward_can_be_claimed(all_raids):
    icarus = all_raids[161]
    run = raids.new_run(icarus, T, 3, 0.02)
    m = raids.member(run, 1, T)
    end = run["end"]
    assert not raids.claimable(run, m, end + 599) and raids.claimable(run, m, end + 600)
    run["level_end"] = end + HOUR                                            # a level still running at the end
    assert not raids.claimable(run, m, end + 600) and raids.claimable(run, m, end + HOUR + 600)
    assert not raids.claimable(run, m, run["decay"])
    m["claimed"] = 1
    assert not raids.claimable(run, m, end + HOUR + 600)


# ---- fortresses in the engine ------------------------------------------------------------------------

@pytest.mark.needs_game
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


def me(api):
    return run_of(api)["members"]["1000001"]


def change_run(api, **values):
    """Set parts of the guild's run (and the player's part of it) directly."""
    ask(api, "getRaidInfo")
    guild = api._my_guild()
    for k, v in values.items():
        (guild["raid"]["members"]["1000001"] if k in ("damage", "energy", "claimed") else guild["raid"])[k] = v
    api.guild_store.save(guild)


@pytest.mark.needs_game
def test_no_raid_by_default(tmp_path):
    api, _ = game(tmp_path, raid=None)
    r, _ = ask(api, "init")
    assert r["current_raids"] == [] and r["raid_info"] is None and r["faction"]["name"] == "Raiders"
    r, how = ask(api, "getRaidInfo")
    assert r["result"] is False and how == "refused getRaidInfo: no raid set in content.toml"


@pytest.mark.needs_game
def test_a_raid_needs_a_guild(tmp_path):
    api, _ = game(tmp_path, guild=None)
    r, _ = ask(api, "init")
    assert r["current_raids"] == [] and r["raid_info"] is None and r["faction"] == {"faction_id": 0}
    r, how = ask(api, "fightRaidBattle", raid_id="161", raid_level="1")
    assert r["result"] is False and r["result_message"] == ["You need to be in a guild to raid."]


@pytest.mark.needs_game
def test_a_raid_in_init_and_the_guild(tmp_path):
    api, _ = game(tmp_path)
    r, _ = ask(api, "init")
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
    assert guild["faction_id"] == "900001" and guild["name"] == "Raiders" and "faction_war_points" not in guild
    r, how = ask(api, "getRaidInfo")
    assert r["result"] and how == ("Sentinel Icarus: Raiders at level 1, health 300/300, raid energy 13/25, "
                                   "your damage 0")


@pytest.mark.needs_game
def test_the_boss_has_its_real_health_by_default(tmp_path):
    api, _ = game(tmp_path, extra="days = 1\n")
    r, _ = ask(api, "getRaidInfo")
    assert r["raid_info"][0]["max_health"] == 15000 and r["current_raids"][0]["end_time"] == T + DAY


def slow_deck(api):
    """A deck of delay-4 cards, so the boss gets turns (raid commanders have 2-5 health,
    and a fast deck often wins on turn 1)."""
    cards, _ = api._engine_data()
    slow = [cards[64582]] * 4 + [cards[64552]] * 4
    api._player_deck = lambda _cards: (cards[1000], slow)


@pytest.mark.needs_game
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
    run = run_of(api)
    assert (me(api)["energy"], me(api)["damage"], run["hp"], me(api)["fights"]) == (12, damage, 300 - damage, 1)
    assert run["level_end"] == T + 8 * HOUR
    r, _ = ask(api, "updateFaction", last_activity_id="0")
    assert [n["type"] for n in r["faction_news"]] == [1, 31]               # joined, then "launched a level 1 raid"
    r, _ = ask(api, "getRaidInfo")
    info = r["raid_info"][0]
    assert (info["status"], info["raid_level_start"], info["raid_level_end"]) == (0, T, T + 8 * HOUR)
    assert info["members"][0]["damage"] == damage and info["energy"]["battle_energy"] == 12
    again = ga.GameApi(fixture_dir=ga.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: clock[0])
    assert again._my_guild()["raid"] == run                                  # kept in the guild's file
    assert "raid" not in json.loads((tmp_path / "player.json").read_text("utf-8"))["player"]


@pytest.mark.needs_game
def test_a_lost_level_shows_in_the_news(tmp_path):
    api, clock = game(tmp_path)
    fight(api)
    clock[0] += 8 * HOUR
    r, _ = ask(api, "updateFaction", last_activity_id="2")
    assert [(n["type"], json.loads(n["data"])) for n in r["faction_news"]] == \
        [(33, {"raid_level": 1, "raid_name": "Sentinel Icarus"})]
    assert run_of(api)["hp"] == 300


@pytest.mark.needs_game
def test_out_of_raid_energy_and_the_refill(tmp_path):
    api, _ = game(tmp_path)
    change_run(api, energy=0)
    r, how = fight(api)
    assert r["result"] is False and r["result_message"] == ["You're out of raid energy."]
    r, how = ask(api, "refillEventBattleEnergy", event_type="2", event_id="161")
    assert r["result"] and how == "raid energy refilled: +25 for 125 war bonds"
    assert api.player().war_bonds == 2000 - 125 and r["raid_info"][0]["energy"]["battle_energy"] == 25
    r, how = ask(api, "refillEventBattleEnergy", event_type="2", event_id="161")
    assert r["result"] is False and how == "refused raid refill: full"


@pytest.mark.needs_game
def test_clearing_levels_by_fighting(tmp_path):
    api, _ = game(tmp_path)
    change_run(api, hp=1, energy=25)
    r, how = fight(api)
    assert "level 1 cleared" in how
    run = run_of(api)
    assert (run["level"], run["hp"], run["level_end"]) == (2, 320, 0)
    r, _ = ask(api, "updateFaction", last_activity_id="1")
    assert [n["type"] for n in r["faction_news"]] == [31, 32]              # launched, won
    r, _ = ask(api, "getRaidLeaderboard", raid_id="161")
    assert r["raid_leaderboard"] == [{"faction_id": "900001", "name": "Raiders", "raid_level": 2}]


@pytest.mark.needs_game
def test_forfeit_during_the_replay_gives_the_same_result(tmp_path):
    api, _ = game(tmp_path)
    r, _ = fight(api)
    bid = r["battle_data"]["battle_id"]
    f, how = ask(api, "forfeitBattle", battle_id=str(bid))
    assert f["result"] and f["battle_data"] == r["battle_data"] and how.startswith("forfeit during a raid fight")
    assert me(api)["energy"] == 12                                          # not charged twice


def owned(api):
    return sum(int(c["num_owned"]) for c in api.player().cards.values())


@pytest.mark.needs_game
def test_claiming_the_reward(tmp_path):
    api, clock = game(tmp_path)
    change_run(api, damage=1000, level=5)
    r, how = ask(api, "claimRaidReward", raid_id="161")
    assert r["result"] is False and how == "refused raid claim: early"
    clock[0] = run_of(api)["end"] + 600
    before = owned(api)
    r, how = ask(api, "claimRaidReward", raid_id="161")
    # tiers 26-22: 5 epic packs (20), a card, 5 Legendary Packs (20), a card, a card
    assert r["result"] and how.startswith("raid reward: 5 tiers, 43 cards (")
    assert len(r["new_cards"]) == 43 and all(isinstance(c, str) for c in r["new_cards"])
    assert r["new_cards"].count("60885") >= 2 and "68339" in r["new_cards"]
    assert owned(api) == before + 43
    assert r["raid_info"][0]["claimed_rewards"] == 1
    r, how = ask(api, "claimRaidReward", raid_id="161")
    assert r["result"] is False and how == "refused raid claim: claimed"


@pytest.mark.needs_game
def test_a_new_run_after_the_last_one_is_over(tmp_path):
    api, clock = game(tmp_path)
    fight(api)
    clock[0] = run_of(api)["decay"]
    r, _ = ask(api, "getRaidInfo")
    run = run_of(api)
    assert run["start"] == clock[0] and me(api)["damage"] == 0 and me(api)["energy"] == 13


@pytest.mark.needs_game
def test_leaving_the_guild_mid_raid(tmp_path):
    api, _ = game(tmp_path)
    fight(api)
    r, how = ask(api, "leaveFaction")
    assert r["faction"] == {"faction_id": 0} and "disbanded" in how
    r, _ = ask(api, "fightRaidBattle", raid_id="161", raid_level="1")
    assert r["result_message"] == ["You need to be in a guild to raid."]


@pytest.mark.needs_game
def test_reward_packs_from_an_emptied_set(tmp_path):
    api, _ = game(tmp_path)
    cards, _ = api._engine_data()
    got = api._open_raid_reward("mini_pack", 94, 2, cards, random.Random(2))   # set 4000: no level-1 cards
    assert got and all(cards[c].set == raids.FALLBACK_REWARD_SET for c in got)


@pytest.mark.needs_game
def test_a_raid_with_a_battleground_effect(tmp_path):
    api, _ = game(tmp_path, raid=55)
    slow_deck(api)
    r, how = ask(api, "fightRaidBattle", raid_id="55", raid_level="1")
    assert r["result"], how
    bd = r["battle_data"]
    entry = bd["battleground_effects"]["defender"][0]
    assert entry["name"] == "Silus' Pact" and entry["card_uid"] == -5
    acts = [a for t in bd["turn"].values() if t for a in t.get("actions", []) if a["card_uid"] == -5]
    assert acts and all(int(t) % 2 == 0 for t, turn in bd["turn"].items() if turn
                        for a in turn.get("actions", []) if a["card_uid"] == -5)
