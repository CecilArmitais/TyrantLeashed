"""Tests for event objectives (achievements.py, game_api/achievement_calls.py): mission
levels, challenges from content.toml and their runs, background progress, the new fight kinds, item drops,
quest parents, the list's order and cap, and a challenge through the server."""

import random
from types import SimpleNamespace

import pytest

from common import paths
from helpers import arena_fight, ask, objectives_game, raid_fight, raid_game, recorded_fight
from rules import achievements as ach

pytestmark = pytest.mark.needs_game        # every test here needs the game files

LIVING_MIRRORS, EXTREME = 3580, 3450


@pytest.fixture(scope="module")
def defs():
    return ach.load(paths.ASSET_DIR)


def player(missions=(), wins=None):
    ms = {str(m): {"number": 1, "complete": 1} for m in missions}
    for m, n in (wins or {}).items():
        ms[str(m)] = {"number": n, "complete": 1}
    p = SimpleNamespace(achievements=None, missions=ms, arena={}, gold=0, gold_earned=0, war_bonds=0, salvage=0,
                        cards_earned=0, cards={}, items={})
    p.add_card = lambda cid, n=1: p.cards.__setitem__(str(cid), p.cards.get(str(cid), 0) + n)
    return p


def run_of(defs, cid, start=1_800_000_000):
    length = ach.challenge_length(defs, cid)
    run = {"id": cid, "start": start, "end": start + length if length else None}
    return ach.context(defs, run)


def stats(won=True, **kw):
    kw.setdefault("enemy_commander", SimpleNamespace(faction=1))              # an Imperial commander
    return ach.FightStats(won, **kw)


# ---- the data ------------------------------------------------------------------------------------

def test_the_sets(defs):
    assert len(ach.challenge_set(defs, LIVING_MIRRORS)) == 16 and ach.challenge_length(defs, LIVING_MIRRORS) == 28 * 86400
    assert len(ach.challenge_set(defs, EXTREME)) == 29 and ach.challenge_length(defs, EXTREME) is None
    assert ach.challenge_set(defs, 3582) == ach.challenge_set(defs, LIVING_MIRRORS)     # any of its objectives
    assert ach.challenge_set(defs, 1148) == frozenset()                                 # undated: always on
    standing = ach.standing_events(defs)
    assert {1148, 1158, 1161, 3201} <= standing and not standing & ach.challenge_set(defs, LIVING_MIRRORS)
    assert defs[3580].goal == 15 and defs[3011].goal == 25 and defs[3013].goal == 30


def test_a_new_player_sees_what_the_recording_did(defs):
    p = player()
    shown = {a for a in ach.user_achievements(defs, p) if defs[int(a)].event}
    assert shown == {str(i) for i in range(1148, 1159)}                                 # Mesmerize Mutant


# ---- mission levels ------------------------------------------------------------------------------

def test_mission_levels_are_wins(defs):
    p = player(wins={1516: 3})
    done = ach.complete_ready(defs, p, 10 ** 6)
    assert [a.id for a in done if a.mission_level] == [1148, 1149]                       # Lv. 2 and 3
    assert p.cards.get("17240") == 2                                                    # Lv. 2's and 3's
    assert ach.user_achievements(defs, p)["1150"]["progress"] == "3"                      # Lv. 4: 3/4


# ---- challenges, background progress -------------------------------------------------------------

def test_a_challenge_counts_in_the_background(defs):
    """Recorded: an account short of mission 17 listed the challenge's follow-ups, so the
    first link (no no_background_progress) had finished hidden."""
    ctx = run_of(defs, EXTREME)
    p = player()
    first = defs[3452]                                                                 # "Not Extreme - Destroyer": win 10
    assert first.mission_req == 17 and first.background and not defs[3453].background
    assert "3452" not in ach.user_achievements(defs, p, ctx)                              # hidden: mission 17
    for _ in range(10):
        ach.record_fight(defs, p, stats(True), 0, ctx)
    done = ach.complete_ready(defs, p, 10 ** 6, ctx)
    assert [a.id for a in done] == [3452] and p.war_bonds == 100                          # paid at once
    ua = ach.user_achievements(defs, p, ctx)
    assert {"3453", "3460", "3464", "3468", "3472", "3476"} <= set(ua)                   # as recorded
    assert "3450" not in ua                                                            # the parent needs mission 17
    ach.record_fight(defs, p, stats(True), 0, ctx)                                     # 3453 counts once shown
    assert ach.user_achievements(defs, p, ctx)["3453"]["progress"] == "0"                 # Progenitor commanders only


def test_shown_pvp_objectives_wait_for_complete(defs):
    ctx = run_of(defs, LIVING_MIRRORS)
    p = player(missions=[17])
    mirror_25 = defs[3583]
    ach.state(p)["done"].append(3582)
    ach.state(p)["event"]["progress"][str(mirror_25.id)] = 24
    ready = ach.record_fight(defs, p, stats(True), 0, ctx)
    assert [a.id for a in ready] == [3583]
    assert ach.complete_ready(defs, p, 10 ** 6, ctx) == []                                # waits
    ua = ach.user_achievements(defs, p, ctx)
    assert ua["3583"]["status"] == "1" and ua["3583"]["end_time"] == str(ctx.run["end"])
    a = ach.claim(defs, p, 3583, 10 ** 6, 0, ctx)
    assert a.id == 3583 and p.war_bonds == 50 and "3584" in ach.user_achievements(defs, p, ctx)
    with pytest.raises(ValueError, match="already been completed"):
        ach.claim(defs, p, 3583, 10 ** 6, 0, ctx)


def test_a_new_run_starts_over(defs):
    p = player(missions=[17])
    ctx = run_of(defs, LIVING_MIRRORS)
    ach.state(p)["done"] += [3582, 3583]
    ach.complete_ready(defs, p, 10 ** 6, ctx)
    nxt = run_of(defs, LIVING_MIRRORS, start=ctx.run["end"])
    ach.complete_ready(defs, p, 10 ** 6, nxt)
    assert 3582 not in ach.state(p)["done"] and "3582" in ach.user_achievements(defs, p, nxt)


def test_the_challenge_store(tmp_path, defs):
    store = ach.ChallengeStore(tmp_path / "challenge.json")
    assert store.current(defs, 0, 100) is None
    run = store.current(defs, LIVING_MIRRORS, 100)
    assert run == {"id": LIVING_MIRRORS, "start": 100, "end": 100 + 28 * 86400}
    assert store.current(defs, LIVING_MIRRORS, 200) == run
    assert store.current(defs, LIVING_MIRRORS, run["end"])["start"] == run["end"]          # then again
    assert store.current(defs, EXTREME, 300) == {"id": EXTREME, "start": 300, "end": None}


def test_the_parent_and_counts(defs):
    p = player(missions=[17])
    ctx = run_of(defs, LIVING_MIRRORS)
    ach.state(p)["done"] += sorted(i for i in defs[LIVING_MIRRORS].subs)
    done = ach.complete_ready(defs, p, 10 ** 6, ctx)
    assert [a.id for a in done] == [LIVING_MIRRORS] and p.war_bonds == 500             # all 15 done
    q = player()
    ach.state(q)["counts"]["objectives_9"] = 30
    assert ach.event_progress(defs[3013], q) == 30                                    # "Complete 30 daily objectives"


# ---- the new fight kinds, drops ------------------------------------------------------------------

def test_kinds_6_and_7(defs):
    orbo = defs[3272]                                     # "Protect the Mythic Orbo in 20 battles and win"
    hit = stats(True, skill_targets={(ach.PLAYER, "protect", 58573): 2})
    assert ach.fight_progress(orbo, hit) == 1
    assert ach.fight_progress(orbo, stats(False, skill_targets={(ach.PLAYER, "protect", 58573): 2})) == 0
    assert ach.fight_progress(orbo, stats(True, skill_targets={(ach.PLAYER, "rally", 58573): 2})) == 0
    heal = defs[3111]                                     # "Win 250 battles while having activated Heal."
    assert ach.fight_progress(heal, stats(True, skill_uses={(ach.PLAYER, "heal"): 1})) == 1
    assert ach.fight_progress(heal, stats(True, skill_uses={(ach.ENEMY, "heal"): 1})) == 0


def test_fight_stats_record_targets():
    battle, won = recorded_fight(117, 129)
    s = ach.fight_stats(battle, won)
    assert s.skill_targets and all(side in (ach.PLAYER, ach.ENEMY) for side, _, _ in s.skill_targets)


def test_drops(defs):
    ctx = run_of(defs, 3001)                              # the 2015 Legendary Quest: Cypher Scopes from missions
    p = player(missions=[5, 8, 17])
    ach.state(p)["done"] += [406, 401, 402]
    always = SimpleNamespace(random=lambda: 0.0)
    assert ach.drops(defs, p, ach.MISSION, True, always, ctx) == {3001: 1}
    assert ach.drops(defs, p, ach.MISSION, False, always, ctx) == {}                   # wins only
    assert ach.drops(defs, p, ach.RAID, True, always, ctx) == {}
    assert ach.drops(defs, p, ach.MISSION, True, SimpleNamespace(random=lambda: 0.5), ctx) == {}
    assert ach.event_progress(defs[3011], p) == 1
    assert ach.reward_item({3001: 1}) == {"3001": 1} and ach.reward_item({}) == []
    assert ach.drops(defs, player(), ach.MISSION, True, always, ctx) == {}             # 402 not done yet


def test_raid_drops_that_need_a_level(defs):
    """"Earn 100 Marks of the Mythic in Raids level 5 or higher" (4111, after 3101)."""
    assert defs[4111].drop["min_level"] == "5" and "min_level" not in defs[3002].drop
    p = player(missions=[130])
    ach.state(p)["done"].append(3101)
    always = SimpleNamespace(random=lambda: 0.0)
    assert ach.drops(defs, p, ach.RAID, True, always, level=4) == {}
    assert ach.drops(defs, p, ach.RAID, True, always, level=5) == {3009: 1}
    assert ach.drops(defs, p, ach.RAID, False, always, level=5) == {3009: 1}             # every raid fight counts


# ---- the list ------------------------------------------------------------------------------------

def test_the_list_is_sorted_and_capped(defs):
    ctx = run_of(defs, EXTREME)
    p = player(missions=[17])
    ach.state(p)["done"].append(3452)
    ua = ach.user_achievements(defs, p, ctx)
    assert len(ua) == ach.LIST_SIZE
    keys = [int(k) for k in ua]
    assert keys == sorted(keys, key=lambda i: (defs[i].order, i))
    assert "1158" not in ua                                                            # mutants (order 50) cut


# ---- through the server --------------------------------------------------------------------------

def test_a_challenge_through_the_server(tmp_path, defs):
    (tmp_path / "content.toml").write_text("[objectives]\nchallenge = 3580\n", "utf-8")
    api, clock = objectives_game(tmp_path, engine=True)
    ask(api, "init")
    assert api.content().challenge == LIVING_MIRRORS
    p = api.player()
    p.won_mission("17")
    p.arena["stamina"] = 15
    ach.state(p)["done"].append(3582)
    ach.state(p)["event"]["progress"]["3583"] = 24
    api.player_store.save(p)
    r, _ = ask(api, "init")
    assert "3583" in r["user_achievements"]
    r, how = arena_fight(api, "-1")                                                       # a sure win
    assert r["user_achievements"]["3583"]["status"] == "1", how
    before = api.player().war_bonds
    r, how = ask(api, "completeAchievement", achievement_id="3583")
    assert r["result"] and api.player().war_bonds == before + 50, how
    run = api.challenge_store.load()
    assert run["id"] == LIVING_MIRRORS and run["end"] - run["start"] == 28 * 86400
    clock[0] = run["end"]                                                              # a new run
    r, _ = ask(api, "init")
    assert "3582" in r["user_achievements"]


def test_complete_finishes_the_parent(tmp_path, defs):
    """Completing a challenge's last sub-objective finishes its parent in the same reply."""
    (tmp_path / "content.toml").write_text("[objectives]\nchallenge = 3580\n", "utf-8")
    api, _ = objectives_game(tmp_path, engine=True)
    ask(api, "init")
    p = api.player()
    p.won_mission("17")
    p.arena["stamina"] = 15
    ach.state(p)["done"] += [i for i in defs[LIVING_MIRRORS].subs if i != 3583]
    ach.state(p)["event"]["progress"]["3583"] = 24
    api.player_store.save(p)
    arena_fight(api, "-1")                                                                # a sure win: Mirror 25 ready
    before = api.player().war_bonds
    r, how = ask(api, "completeAchievement", achievement_id="3583")
    assert [c["id"] for c in r["completed_achievements"]] == ["3583", "3580"], how
    assert api.player().war_bonds == before + 50 + 500 and "3580" not in r["user_achievements"]


def test_waiting_objectives_make_the_cut(defs):
    """A finished PvP objective past the 20th place is still listed, for its Complete."""
    ctx = run_of(defs, LIVING_MIRRORS)
    p = player(missions=range(1, 143))
    ach.state(p)["done"].append(3582)
    ach.state(p)["event"]["ready"].append(3583)
    ach.state(p)["event"]["progress"]["3583"] = 25
    ua = ach.user_achievements(defs, p, ctx)
    assert len(ua) == ach.LIST_SIZE and ua["3583"]["status"] == "1"
    keys = [int(k) for k in ua]
    assert keys == sorted(keys, key=lambda i: (defs[i].order, i))


class AlwaysDrop(random.Random):
    """Every drop roll succeeds."""
    def random(self):
        return 0.0


def test_a_raid_fight_drops_an_item(tmp_path, defs):
    """"Eradicate the Great Threat" (3002, after the 2015 Legendary Quest): Pantheon Idols
    from raid battles, lost ones included, in the reward's `item` and the inventory."""
    api, _ = raid_game(tmp_path)
    p = api.player()
    ach.state(p)["done"].append(3001)
    api.player_store.save(p)
    api.rng_factory = lambda: AlwaysDrop(1)
    r, how = raid_fight(api)
    assert r["battle_data"]["rewards"][0]["item"] == {"3005": 1}, how
    assert api.player().items["3005"] == 1 and ach.event_progress(defs[3002], api.player()) == 1
