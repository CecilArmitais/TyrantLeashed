"""Tests for the host's debug options (debug.py): every option is off without debug.toml;
with one on, whatever costs that currency still goes through, the save keeps its amount,
and the server's own "not enough" refusals are skipped. The other currencies cost as before."""

import os

import pytest

import paths
from debug import CURRENCIES, DebugOptions, DebugStore, parse
from helpers import T, arena_api, ask, make_api, raid_game, shop

NOT_CHARGED = " (debug: not charged)"


def turn_on(*currencies):
    """The host's debug.toml (conftest keeps it in the test's own state folder)."""
    paths.DEBUG_FILE.write_text("".join(f"unlimited_{c} = true\n" for c in currencies), "utf-8")


def set_save(api, arena=None, **values):
    p = api.player()
    for k, v in values.items():
        setattr(p, k, v)
    p.arena.update(arena or {})
    api.player_store.save(p)


# ---- the file ------------------------------------------------------------------------------------

def test_reading_the_file():
    assert parse("") == DebugOptions() and DebugOptions().on() == []
    assert parse("unlimited_gold = true\nunlimited_energy = false\n").on() == ["gold"]
    assert parse("".join(f"unlimited_{c} = true\n" for c in CURRENCIES)).on() == list(CURRENCIES)
    c = parse('unlimited_gold = "yes"\nunlimited_gems = true\nunlimited_stamina = true\n')
    assert c.on() == ["stamina"]                                       # a bad value is off
    assert c.problems == ("debug.toml: unlimited_gold needs true or false",
                          "debug.toml: unlimited_gems isn't an option this server has")
    c = parse("unlimited_gold = \n")
    assert c.on() == [] and "isn't valid TOML" in c.problems[0] and "every option is off" in c.problems[0]


def test_the_file_is_read_again_when_it_changes(tmp_path):
    path = tmp_path / "debug.toml"
    store = DebugStore(path)
    assert store.load() == DebugOptions()                              # no file: all off
    path.write_text("unlimited_gold = true\n", "utf-8")
    os.utime(path, (T, T))
    assert store.load().on() == ["gold"]
    path.write_text("unlimited_war_bonds = true\n", "utf-8")
    os.utime(path, (T + 1, T + 1))
    assert store.load().on() == ["war_bonds"]
    path.unlink()
    assert store.load() == DebugOptions()


def test_the_startup_lines(tmp_path):
    api = make_api(tmp_path)
    assert api._debug_report() == ""                                   # no file: not a word
    paths.DEBUG_FILE.write_text("unlimited_gold = false\n", "utf-8")
    assert api._debug_report() == ""
    paths.DEBUG_FILE.write_text("unlimited_stamina = true\nunlimited_gold = true\nunlimited_gems = 1\n", "utf-8")
    os.utime(paths.DEBUG_FILE, (T, T))
    assert api._debug_report() == (
        "\nDebug options on (debug.toml): unlimited gold, unlimited stamina; spending those costs nothing, "
        "for every player\ndebug.toml: unlimited_gems isn't an option this server has")


# ---- energy and war bonds (a tiny made-up game) --------------------------------------------------

def test_without_the_file_everything_costs(tmp_path):
    api = make_api(tmp_path)
    ask(api, "init")
    set_save(api, energy=10, energy_updated=T + 100, war_bonds=0)
    r, how = ask(api, "startMission", mission_id="2")
    assert r["result"] is False and how == "refused mission 2: needs 15 energy, has 10"
    r, how = ask(api, "buyEnergyRefillTokens")
    assert r["result"] is False and how == "refused: a refill costs 30 war bonds, has 0"


def test_unlimited_energy(tmp_path):
    api = make_api(tmp_path)
    ask(api, "init")
    set_save(api, energy=0, energy_updated=T + 100, war_bonds=0)
    turn_on("energy")
    r, how = ask(api, "startMission", mission_id="2")
    assert r["result"] is True and how.endswith("; 15 energy (0 -> 0)" + NOT_CHARGED)
    assert r["user_data"]["energy"] == "0" and api.player().energy == 0     # the real amount, shown as it is
    r, how = ask(api, "buyEnergyRefillTokens")                         # war bonds still cost
    assert r["result"] is False and how == "refused: a refill costs 30 war bonds, has 0"


def test_unlimited_war_bonds_and_turning_it_off(tmp_path):
    api = make_api(tmp_path)
    ask(api, "init")
    set_save(api, energy=5, energy_updated=T + 100, war_bonds=0)
    turn_on("war_bonds")
    r, how = ask(api, "buyEnergyRefillTokens")
    assert r["result"] is True and how == "energy refill: +250 (5 -> 255), 30 war bonds (0 -> 0)" + NOT_CHARGED
    assert (r["user_data"]["energy"], r["user_data"]["tokens"]) == ("255", "0")
    paths.DEBUG_FILE.unlink()                                          # off again: it costs
    r, how = ask(api, "buyEnergyRefillTokens")
    assert r["result"] is False and how == "refused: a refill costs 30 war bonds, has 0"


# ---- the arena, the store, guilds and raids (the real game files) --------------------------------

@pytest.mark.needs_game
def test_unlimited_stamina_and_the_arena_extras(tmp_path):
    api = arena_api(tmp_path)
    ask(api, "init")
    set_save(api, gold=0, war_bonds=0, arena={"stamina": 0})
    turn_on("stamina", "gold", "war_bonds")
    r, how = ask(api, "startHuntingBattle", target_user_id="-1")
    assert r["result"] is True and how.endswith("stamina 0 -> 0" + NOT_CHARGED)
    ask(api, "forfeitBattle", battle_id=str(r["battle_data"]["battle_id"]))
    assert api.player().arena["stamina"] == 0
    set_save(api, gold=0)                                              # the surrender paid some gold
    r, how = ask(api, "buyStaminaRefillTokens")
    assert r["result"] is True and how == "stamina refill: 0 -> 15, 30 war bonds (0 -> 0)" + NOT_CHARGED
    set_save(api, arena={"hunting_wins": 3, "rating": 60, "rival_order": [-9, -10]})
    r, how = ask(api, "buyRivalsRefresh")
    assert how.startswith("rivals refreshed for 50 gold (0 -> 0)" + NOT_CHARGED + "; ") and r["hunting_targets"]
    p = api.player()
    assert (p.gold, p.war_bonds, p.arena["stamina"]) == (0, 0, 15)


@pytest.mark.needs_game
def test_unlimited_gold_and_war_bonds_in_the_store(tmp_path):
    api = shop(tmp_path, gold=0, bonds=0)
    turn_on("gold", "war_bonds")
    before = api.player().card_count
    r, how = ask(api, "buyPackGold", pack_id="1")
    assert r["result"] is True and how.startswith("bought Basic Pack for 100 gold (0 -> 0)" + NOT_CHARGED + ": ")
    assert int(r["user_data"]["money"]) == 0 and api.player().card_count == before + 1
    cap = api.player().max_energy
    r, how = ask(api, "buyStorePromoTokens", item_id="1", item_type="4", expected_cost="250")
    assert r["result"] is True and how.startswith("bought +50 Max Energy for 250 war bonds" + NOT_CHARGED)
    p = api.player()
    assert (p.gold, p.war_bonds, p.max_energy) == (0, 0, cap + 50)


@pytest.mark.needs_game
def test_unlimited_gold_for_guild_donations(tmp_path):
    api = shop(tmp_path, gold=0)
    turn_on("gold")
    ask(api, "createFaction", faction_name="Owls")
    r, how = ask(api, "donateFactionGold", amount="1000")
    assert how == "donated 1000 gold to Owls for 10 guild points (10 now)" + NOT_CHARGED
    assert r["faction"]["guild_points"] == 10 and api.player().gold == 0


@pytest.mark.needs_game
def test_unlimited_war_bonds_for_the_raid_refill(tmp_path):
    api, _ = raid_game(tmp_path, bonds=0)
    ask(api, "getRaidInfo")
    guild = api._my_guild()
    guild["raid"]["members"]["1000001"]["energy"] = 0
    api.guild_store.save(guild)
    turn_on("war_bonds")
    r, how = ask(api, "refillEventBattleEnergy", event_type="2", event_id="161")
    assert r["result"] is True and how == "raid energy refilled: +25 for 125 war bonds" + NOT_CHARGED
    assert api.player().war_bonds == 0
