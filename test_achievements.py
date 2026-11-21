"""Tests for achievements (achievements.py), against the recorded login, the recorded
completions and the recorded arena fights."""

import json
import random
from types import SimpleNamespace

import pytest

import achievements as ach
import game_api as ga
from player_state import PlayerStore
import test_arena as ta
import test_battle as tb

T = tb.T
INIT = json.loads((ga.FIXTURE_DIR / "init.json").read_text("utf-8"))["body"]
CALLS = json.loads((ga.FIXTURE_DIR / "replay.json").read_text("utf-8"))["calls"]
DAILY_KEYS = {"achievement_id", "progress", "status", "name", "description", "icon", "portrait", "max_progress",
              "type", "req", "reward", "color", "end_time"}


@pytest.fixture(scope="module")
def defs():
    return ach.load(ga.ASSET_DIR)


def game(tmp_path, engine=False):
    clock = [T + 100]
    api = ga.GameApi(fixture_dir=ga.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: clock[0], battle_engine=engine)
    api.rng_factory = lambda: random.Random(1)
    return api, clock


def test_the_permanent_chains_start_where_the_recording_did(defs):
    heads = {a.id for a in ach.open_permanent(defs, SimpleNamespace(achievements=None, missions={}, arena={}))}
    recorded = {int(k) for k, v in INIT["user_achievements"].items() if v["type"] in ("1", "2", "3", "4", "5", "8")
                and "level" not in v["req"]}
    assert heads == recorded == {801, 101, 201, 301, 406, 501}
    assert [defs[i].req["mission"] for i in (406, 401, 402, 403, 404, 405)] == ["5", "8", "17", "28", "41", "68"]
    assert sum(a.daily for a in defs.values()) == 42


def test_the_login_lists_them(tmp_path, defs):
    api, _ = game(tmp_path)
    r, _ = tb.ask(api, "init")
    ua = r["user_achievements"]
    permanent = {k: v for k, v in ua.items() if v["type"] != "9"}
    assert set(permanent) == {"801", "101", "201", "301", "406", "501"}
    for k, v in permanent.items():                      # the recorded entries, field for field
        rec = INIT["user_achievements"][k]
        assert {f: str(v[f]) for f in ("name", "description", "max_progress", "type")} == \
            {f: str(rec[f]) for f in ("name", "description", "max_progress", "type")}
        assert v["req"] == rec["req"] and v["reward"] == rec["reward"]
    daily = [v for v in ua.values() if v["type"] == "9"]
    assert sorted(defs[v["achievement_id"]].difficulty for v in daily) == [1, 2, 3]
    assert all(set(v) == DAILY_KEYS and v["end_time"] == str(T + 100 + 82800) for v in daily)


def test_mission_5_finishes_the_first_link(tmp_path, defs):
    """Recorded: winning mission 5 finished "Savior of Acheron" (100 gold) and listed the
    next link, "Complete Mission #8"."""
    api, _ = game(tmp_path)
    tb.ask(api, "init")
    gold = api.player().gold
    p = api.player()
    p.won_mission("5")
    api.player_store.save(p)
    done = api._track(api.player())
    assert [a.id for a in done] == [406]
    assert api.player().gold == gold + 100
    recorded = next(c["body"] for c in CALLS if c["body"].get("completed_achievements"))
    assert ach.completed_json(done) == recorded["completed_achievements"]
    assert set(ach.user_achievements(defs, api.player())) & {"406", "401"} == {"401"}


def test_a_rating_jump_finishes_a_chain_in_one_go(tmp_path):
    api, _ = game(tmp_path)
    tb.ask(api, "init")
    p = api.player()
    p.arena["rating"], gold = 650, p.gold
    done = api._track(p)
    assert [a.id for a in done] == [201, 202]                   # 200 and 500: 200 + 500 gold
    assert p.gold == gold + 700 and p.gold_earned >= 700
    assert ach.user_achievements(api._achievement_defs(), p)["203"]["progress"] == "650"


def recorded_fight(first, last):
    """A recorded arena fight rebuilt from its replies: every turn as last sent, and its
    cards (the replies' card_map and commanders)."""
    cards = ta.real_cards()
    turns, card_map, data = {}, {}, {}
    for c in CALLS[first:last + 1]:
        bd = c["body"].get("battle_data") or {}
        data.update(bd)
        card_map.update(bd.get("card_map") or {})
        for k, v in (bd.get("turn") or {}).items():
            if isinstance(v, dict) and v:
                turns[int(k)] = v
    side = lambda lo, hi: SimpleNamespace(cards={int(u): cards[int(c)] for u, c in card_map.items() if lo <= int(u) < hi})
    att, dfn = side(1, 100), side(101, 200)
    att.commander = SimpleNamespace(card=cards[int(data["attack_commander"])])
    dfn.commander = SimpleNamespace(card=cards[int(data["defend_commander"])])
    return SimpleNamespace(turns=turns, sides=(att, dfn)), data.get("winner") == 1


def test_fight_counts_match_the_recorded_progress(defs):
    """Recorded: "Battle: Deal 200 Attack Damage" went 0 -> 3 -> 48 over the two arena
    fights; the fights' own attacks add up to that."""
    attack = defs[2025]
    progress = 0
    for first, last in ((114, 115), (117, 129)):
        battle, won = recorded_fight(first, last)
        assert won
        progress += ach.fight_progress(attack, ach.fight_stats(battle, won))
        recorded = int(CALLS[last]["body"]["user_achievements"]["2025"]["progress"])
        assert progress == recorded
    assert progress == 48


def test_fight_kinds(defs):
    battle, won = recorded_fight(117, 129)
    stats = ach.fight_stats(battle, won)
    wins = next(a for a in defs.values() if a.daily and a.req["pvp_sub_type"].get("type_id") == "5"
                and "type" not in a.req["pvp_sub_type"])
    assert ach.fight_progress(wins, stats) == 1                 # "Win N Battles"
    enemy = stats.enemy_commander.faction
    beat = next(a for a in defs.values() if a.daily and a.req["pvp_sub_type"].get("type_id") == "5"
                and a.req["pvp_sub_type"].get("type") == str(enemy))
    assert ach.fight_progress(beat, stats) == 1                 # "Defeat 5 <faction> Commanders"
    destroyed = next(a for a in defs.values() if a.daily and a.req["pvp_sub_type"].get("type_id") == "4"
                     and "type" not in a.req["pvp_sub_type"])
    assert ach.fight_progress(destroyed, stats) == sum(1 for s, c in stats.kills if s == ach.ENEMY
                                                       and c.type == "assault") > 0


def test_daily_objectives_through_the_server(tmp_path, defs):
    api, clock = game(tmp_path, engine=True)
    tb.ask(api, "init")
    p = api.player()
    easy = next(a for a in defs.values() if a.daily and a.req["pvp_sub_type"] == {
        "type_id": "5", "wins": "5", "value": "1", "is_player": "0"})      # "Battle: Win 5 Battles"
    p.achievements["daily"].update(ids=[easy.id], progress={str(easy.id): 4})
    p.arena["stamina"] = 15
    api.player_store.save(p)
    r, how = ta.fight(api, "-1")                                # the tutorial rival: a sure win
    assert r["user_achievements"][str(easy.id)]["status"] == "1" and "achievement" not in how
    before = api.player().war_bonds
    r, how = tb.ask(api, "completeAchievement", achievement_id=str(easy.id))
    assert r["result"] and how == f"claimed {easy.name} ({easy.desc}); achievement: {easy.name} ({easy.desc})"
    assert r["completed_achievements"][0]["id"] == str(easy.id) and str(easy.id) not in r["user_achievements"]
    assert api.player().war_bonds == before + int(easy.reward.get("wb", 0))
    r, _ = tb.ask(api, "completeAchievement", achievement_id=str(easy.id))
    assert r["result"] is False and r["result_message"] == [f"{easy.name} has already been completed."]
    clock[0] += 82800                                           # 23 hours: a new set
    r, _ = tb.ask(api, "init")
    assert sum(v["type"] == "9" for v in r["user_achievements"].values()) == 3
    assert PlayerStore(tmp_path / "player.json").load().achievements["rotations"] == 2


def test_card_rewards(tmp_path, defs):
    api, _ = game(tmp_path)
    tb.ask(api, "init")
    p = api.player()
    for m in ("5", "8"):
        p.won_mission(m)
    before = p.owned(2999)
    done = api._track(p)
    assert [a.id for a in done] == [406, 401] and p.owned(2999) == before + 1   # a Salvage Crate
