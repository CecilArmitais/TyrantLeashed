"""Tests for the offline player's save: storage, energy refill, replies, and the edit command."""

import json
import os
import sys
from pathlib import Path

import pytest

import game_api as ga
import player as cli
from player_state import SAVE_VERSION, Player, PlayerStore, refill

T = 1_795_000_000


# ---- energy refill -------------------------------------------------------------------------

@pytest.mark.parametrize("energy, updated, now, expected", [
    (200, T, T + 59, (200, T)),                 # not a full point yet
    (200, T, T + 60, (201, T + 60)),
    (200, T, T + 150, (202, T + 120)),          # keeps the 30 s towards the next point
    (245, T, T + 10_000, (250, T + 10_000)),    # reaches the maximum: time is just now
    (250, T, T + 500, (250, T + 500)),          # full
    (291, T, T + 500, (291, T + 500)),          # overfilled by a level-up: left alone
    (200, T + 1000, T, (200, T)),               # clock set back: restart from now, don't stall
])
def test_refill(energy, updated, now, expected):
    assert refill(energy, updated, now, max_energy=250, every=60) == expected


# ---- the store ------------------------------------------------------------------------------

START = {
    "time": T, "max_energy": 250, "energy_recharge_time": 60,
    "user_data": {"user_id": "1000001", "money": "100", "tokens": "50", "energy": "250",
                  "last_update_time": str(T), "level": "1"},
    "user_cards": {"2": {"num_owned": "1", "num_used": "0"}, "1000": {"num_owned": "2", "num_used": "1"}},
    "user_decks": {"1": {"cards": {"2": "1"}}},
}


def test_create_save_load(tmp_path):
    clock = [T + 5]
    store = PlayerStore(tmp_path / "player.json", clock=lambda: clock[0])
    p = store.create_from(START, {"session": "s1"})
    assert (p.gold, p.war_bonds, p.energy, p.energy_updated) == (100, 50, 250, T + 5)
    assert p.cards == {"2": {"num_owned": 1, "num_used": 0}, "1000": {"num_owned": 2, "num_used": 1}}
    assert p.card_count == 3
    again = PlayerStore(tmp_path / "player.json").load()            # a fresh store reads the file
    assert again == p


def test_store_rereads_the_file_when_it_changes(tmp_path):
    a = PlayerStore(tmp_path / "player.json", clock=lambda: T)
    b = PlayerStore(tmp_path / "player.json", clock=lambda: T)
    a.create_from(START)
    assert b.load().gold == 100
    p = a.load()
    p.gold = 999
    a.save(p)
    os.utime(tmp_path / "player.json", (T + 10, T + 10))             # make sure the change is visible
    assert b.load().gold == 999


def test_unknown_save_version_is_refused(tmp_path):
    f = tmp_path / "player.json"
    f.write_text(json.dumps({"version": 99, "player": {}}), "utf-8")
    with pytest.raises(ValueError, match="version"):
        PlayerStore(f).load()


def test_apply_replaces_only_existing_fields_and_keeps_their_type():
    p = Player(gold=1234, war_bonds=77, energy=150, energy_updated=T + 30,
               cards={"5": {"num_owned": 3, "num_used": 0}})
    body = {"user_data": {"money": "100", "tokens": 50, "energy": "250", "last_update_time": str(T), "xp": "9"},
            "user_cards": {"2": {"num_owned": "1", "num_used": "0"}}, "new_cards": [2]}
    PlayerStore.apply(body, p)
    assert body["user_data"] == {"money": "1234", "tokens": 77, "energy": "150", "last_update_time": str(T + 30), "xp": "9"}
    assert body["user_cards"] == {"5": {"num_owned": "3", "num_used": "0"}}
    assert body["new_cards"] == [2]                                  # what a reply *shows* is left alone
    partial = {"user_data": {"money": "5"}}
    PlayerStore.apply(partial, p)
    assert partial == {"user_data": {"money": "1234"}}                # nothing added


# ---- in the game API -------------------------------------------------------------------------------

def make_api(tmp_path, clock):
    fx = tmp_path / "fixtures"
    fx.mkdir(exist_ok=True)
    (fx / "init.json").write_text(json.dumps({"source": {"session": "s1", "account": "abcd"}, "body": START}), "utf-8")
    (fx / "getUserAccount.json").write_text(json.dumps({"source": {}, "body": {
        "time": T, "result": True, "user_data": dict(START["user_data"]), "user_cards": START["user_cards"]}}), "utf-8")
    (fx / "replay.json").write_text(json.dumps({"source": {}, "calls": [
        {"seq": 5, "message": "buyStoreItem", "key": {"pack_id": "1"},
         "body": {"time": T + 20, "result": True, "new_cards": [{"id": 9}],
                  "user_data": {"money": "0", "tokens": "50", "energy": "250", "last_update_time": str(T + 20)},
                  "user_cards": {"2": {"num_owned": "1", "num_used": "0"}, "9": {"num_owned": "1", "num_used": "0"}}}},
    ]}), "utf-8")
    return ga.GameApi(fixture_dir=fx, clock=lambda: clock[0])


def ask(api, message, **params):
    return api.respond(message, {"message": message, **params})[0]


def test_login_shows_the_save_and_it_survives_a_restart(tmp_path):
    clock = [T + 100]
    api = make_api(tmp_path, clock)
    r = ask(api, "init")
    assert (r["user_data"]["money"], r["user_data"]["tokens"], r["user_data"]["energy"]) == ("100", "50", "250")
    assert (tmp_path / "state" / "player.json").is_file()
    p = api.player()
    p.gold, p.war_bonds = 4321, 88
    p.cards["777"] = {"num_owned": 2, "num_used": 0}
    api.player_store.save(p)

    restarted = make_api(tmp_path, clock)                             # a new server process
    for message in ("getUserAccount", "init"):
        r = ask(restarted, message)
        assert (r["user_data"]["money"], r["user_data"]["tokens"]) == ("4321", "88")
        assert r["user_cards"]["777"] == {"num_owned": "2", "num_used": "0"}


def test_replayed_replies_show_the_save_not_the_recording(tmp_path):
    clock = [T + 100]
    api = make_api(tmp_path, clock)
    ask(api, "init")
    r = ask(api, "buyStoreItem", pack_id="1")
    assert r["user_data"]["money"] == "100"                           # recorded "0" not applied
    assert set(r["user_cards"]) == {"2", "1000"}                     # recorded new card not added
    assert r["new_cards"] == [{"id": 9}]                             # the reply still shows the pack opening


def test_energy_refills_between_sessions(tmp_path):
    clock = [T + 100]
    api = make_api(tmp_path, clock)
    ask(api, "init")
    p = api.player()
    p.energy, p.energy_updated = 100, T + 100
    api.player_store.save(p)
    clock[0] = T + 100 + 3600 + 25                                    # an hour (and a bit) later
    r = ask(make_api(tmp_path, clock), "init")
    assert r["user_data"]["energy"] == "160"
    assert r["user_data"]["last_update_time"] == str(T + 100 + 3600)  # the 25 s towards the next point are kept
    assert json.loads((tmp_path / "state" / "player.json").read_text("utf-8"))["player"]["energy"] == 160


# ---- the edit command ------------------------------------------------------------------------------

CARDS_XML = """<root>
  <unit><id>2</id><name>Infantry</name><upgrade><level>2</level><card_id>3</card_id></upgrade></unit>
  <unit><id>1000</id><name>Commander Halcyon</name></unit>
  <unit><id>1001</id><name>Commander Malika</name></unit>
</root>"""


def run_cli(tmp_path, monkeypatch, capsys, *args, clock=T + 100):
    make_api(tmp_path, [clock])
    xml = tmp_path / "xml"
    xml.mkdir(exist_ok=True)
    (xml / "cards_section_1.xml").write_text(CARDS_XML, "utf-8")
    monkeypatch.setattr(cli, "CARD_XML_DIR", xml)
    monkeypatch.setattr(cli, "CLOCK", lambda: clock)
    monkeypatch.setattr(sys, "argv", ["player.py", "--fixtures", str(tmp_path / "fixtures"),
                                      "--state", str(tmp_path / "state"), *args])
    code = 0
    try:
        cli.main()
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr().out


def saved(tmp_path):
    return json.loads((tmp_path / "state" / "player.json").read_text("utf-8"))["player"]


def test_cli_show(tmp_path, monkeypatch, capsys):
    _, out = run_cli(tmp_path, monkeypatch, capsys)
    assert "gold       100" in out and "war bonds  50" in out
    assert "energy     250 / 250 (full)" in out
    assert "x2   Commander Halcyon, 1 used" in out and "x1   Infantry" in out


def test_cli_sets_values(tmp_path, monkeypatch, capsys):
    run_cli(tmp_path, monkeypatch, capsys, "gold", "500")
    run_cli(tmp_path, monkeypatch, capsys, "bonds", "75")
    run_cli(tmp_path, monkeypatch, capsys, "energy", "40", clock=T + 200)
    s = saved(tmp_path)
    assert (s["gold"], s["war_bonds"], s["energy"], s["energy_updated"]) == (500, 75, 40, T + 200)
    _, out = run_cli(tmp_path, monkeypatch, capsys, clock=T + 200 + 90)
    # 41 after one minute and a half; 209 missing = 30 s + 208 x 60 s = 3h 28m 30s
    assert "energy     41 / 250 (next point in 0m 30s, full in 3h 28m)" in out


def test_cli_cards(tmp_path, monkeypatch, capsys):
    _, out = run_cli(tmp_path, monkeypatch, capsys, "card", "1001", "+2")
    assert "Card 1001 (Commander Malika): 0 -> 2." in out
    run_cli(tmp_path, monkeypatch, capsys, "card", "3", "=4")
    _, out = run_cli(tmp_path, monkeypatch, capsys, "card", "2", "-1")
    assert "Deck 1 used more than that, so it now has 0." in out      # taken out of the saved deck too
    s = saved(tmp_path)
    assert s["decks"]["1"]["cards"] == {}
    assert s["cards"] == {"1000": {"num_owned": 2, "num_used": 1}, "1001": {"num_owned": 2, "num_used": 0},
                          "3": {"num_owned": 4, "num_used": 0}}
    code, _ = run_cli(tmp_path, monkeypatch, capsys, "card", "99999", "+1")
    assert "isn't in the card data" in str(code)
    code, _ = run_cli(tmp_path, monkeypatch, capsys, "card", "1001", "-5")
    assert "only have 2" in str(code)


def test_cli_max_energy(tmp_path, monkeypatch, capsys):
    run_cli(tmp_path, monkeypatch, capsys, "max-energy", "300")
    run_cli(tmp_path, monkeypatch, capsys, "energy", "290")
    _, out = run_cli(tmp_path, monkeypatch, capsys, clock=T + 100 + 600)
    assert "energy     300 / 300 (full)" in out                       # refilled to the player's own cap
    run_cli(tmp_path, monkeypatch, capsys, "energy", "350")
    _, out = run_cli(tmp_path, monkeypatch, capsys)
    assert "energy     350 / 300 (over the max" in out
    code, _ = run_cli(tmp_path, monkeypatch, capsys, "max-energy", "0")
    assert "too low" in str(code)


# ---- energy across server downtime -----------------------------------------------------------------

def test_version_1_save_is_upgraded(tmp_path):
    f = tmp_path / "player.json"
    f.write_text(json.dumps({"version": 1, "player": {
        "gold": 5, "war_bonds": 6, "energy": 7, "energy_updated": T, "cards": {}}}), "utf-8")
    p = PlayerStore(f, clock=lambda: T, default_max_energy=250).load()
    assert (p.gold, p.energy, p.max_energy) == (5, 7, 250)
    assert json.loads(f.read_text("utf-8"))["version"] == SAVE_VERSION


def test_server_off_for_hours_counts_up_to_the_players_own_max(tmp_path):
    clock = [T + 100]
    api = make_api(tmp_path, clock)
    ask(api, "init")
    p = api.player()
    p.energy, p.energy_updated, p.max_energy = 100, T + 100, 300
    api.player_store.save(p)
    del api                                                            # server stops

    clock[0] = T + 100 + 4 * 3600                                     # four hours later, server starts again
    restarted = make_api(tmp_path, clock)                             # 240 points' worth, capped at 300
    assert restarted.startup_report() == ("Player save: 100 gold, 50 war bonds, energy 300/300 "
                                          "(+200 regenerated in the 4h 0m since the last update)\n"
                                          "Gold bonuses: missions x7, arena x3\n"
                                          "Store (content.toml): packs basic, epic; 3 promos; boxes: none\n"
                                          "Guilds (state/guilds/): none yet; donations on (100 gold a guild point); "
                                          "raid: none")
    r = ask(restarted, "init")
    assert r["user_data"]["energy"] == "300" and r["max_energy"] == 300  # the player's cap, not the recorded 250
    assert r["user_data"]["last_update_time"] == str(clock[0])        # full: the sync point is now


def test_server_off_briefly_keeps_partial_progress(tmp_path):
    clock = [T + 100]
    api = make_api(tmp_path, clock)
    ask(api, "init")
    p = api.player()
    p.energy, p.energy_updated = 10, T + 100
    api.player_store.save(p)
    clock[0] = T + 100 + 25 * 60 + 45                                  # 25 minutes 45 seconds later
    restarted = make_api(tmp_path, clock)
    assert "(+25 regenerated in the 25m since the last update)" in restarted.startup_report()
    r = ask(restarted, "init")
    assert r["user_data"]["energy"] == "35"
    assert r["user_data"]["last_update_time"] == str(T + 100 + 25 * 60)  # the 45 s count towards the next point


def test_startup_report_without_a_save(tmp_path):
    api = make_api(tmp_path, [T])
    assert "none yet" in api.startup_report()
    assert not (tmp_path / "state" / "player.json").exists()          # reporting doesn't create it


def test_cli_reset_needs_yes(tmp_path, monkeypatch, capsys):
    run_cli(tmp_path, monkeypatch, capsys, "gold", "500")
    code, _ = run_cli(tmp_path, monkeypatch, capsys, "reset")
    assert "--yes" in str(code) and saved(tmp_path)["gold"] == 500
    run_cli(tmp_path, monkeypatch, capsys, "reset", "--yes")
    assert not (tmp_path / "state" / "player.json").exists()
