"""Helpers the test files share, so no test file imports another: building a server (on
the real game files, or on a tiny made-up game), sending it requests, and the feature
setups several files use. Nothing here reads the game files when it's imported."""

import json
import random
import sys
from functools import lru_cache
from types import SimpleNamespace

import game_api as ga
import paths
import player as cli
from battle.cards import CardDB


# ---- the real game files (read on first use) -----------------------------------------------------

@lru_cache(maxsize=None)
def real_cards() -> CardDB:
    return CardDB.load(paths.ASSET_DIR)


@lru_cache(maxsize=None)
def init_body() -> dict:
    """The recorded login (init) the server starts from."""
    return json.loads((paths.FIXTURE_DIR / "init.json").read_text("utf-8"))["body"]


@lru_cache(maxsize=None)
def recorded_calls() -> list:
    """The recorded replies (replay.json)."""
    return json.loads((paths.FIXTURE_DIR / "replay.json").read_text("utf-8"))["calls"]


# ---- the clock and requests ----------------------------------------------------------------------

T = 1_795_000_000


def ask(api, message, **params):
    return api.respond(message, {"message": message, "user_id": "1000001", **params})


# ---- a tiny made-up game (cards and missions written to a temporary folder) ----------------------

CARDS = """<root>
  <unit><id>1000</id><name>Cyrus</name><health>10</health><skill id="heal" x="1" y="1"/></unit>
  <unit><id>1297</id><name>Weak Captain</name><health>1</health></unit>
  <unit><id>1300</id><name>Tough Captain</name><health>40</health></unit>
  <unit><id>1</id><name>Grunt</name><attack>1</attack><health>2</health><cost>0</cost><type>2</type><rarity>1</rarity>
    <upgrade><level>2</level><card_id>5</card_id><attack>2</attack></upgrade>
    <upgrade><level>3</level><card_id>7</card_id><health>5</health><skill id="armored" x="1"/></upgrade>
  </unit>
  <unit><id>2</id><name>Slow Gunner</name><attack>3</attack><health>4</health><cost>1</cost>
    <skill id="pierce" x="2"/><skill id="strike" x="1" all="1" trigger="Play"/><skill id="flurry"/></unit>
  <unit><id>3</id><name>Tank</name><attack>0</attack><health>9</health><cost>0</cost></unit>
  <unit><id>8001</id><name>Bunker</name><health>6</health><cost>1</cost></unit>
</root>"""


DB = CardDB.from_xml(CARDS)


MISSIONS = """<root>
  <mission><id>2</id><name>2. Cannon Fire</name><energy>15</energy><commander>1297</commander>
    <deck><card>3</card><card>3</card></deck><rewards><gold>12</gold></rewards></mission>
</root>"""


def make_api(tmp_path, engine=True):
    fx, assets = tmp_path / "fixtures", tmp_path / "assets"
    fx.mkdir()
    assets.mkdir()
    (assets / "cards_section_1.xml").write_text(CARDS, "utf-8")
    (assets / "missions_basic.xml").write_text(MISSIONS, "utf-8")
    start = {"time": T, "max_energy": 250, "energy_recharge_time": 60, "mission_file": "missions_basic.xml",
             "mission_completions": [],
             "user_data": {"user_id": "1000001", "money": "100", "tokens": "50", "energy": "250",
                           "last_update_time": str(T), "active_deck": "1", "mission_wins": "0", "stars": "0"},
             "user_cards": {"1": {"num_owned": "3", "num_used": "0"}},
             "user_decks": {"1": {"deck_id": 1, "commander_id": "1000", "cards": {"1": "3"}}}}
    (fx / "init.json").write_text(json.dumps({"source": {}, "body": start}), "utf-8")
    (fx / "getUserAccount.json").write_text(json.dumps({"source": {}, "body": {"time": T}}), "utf-8")
    return ga.GameApi(fixture_dir=fx, asset_dir=assets, clock=lambda: T + 100, battle_engine=engine)


# ---- several players on one server ---------------------------------------------------------------

def server(tmp_path, content="", engine=False):
    if content:
        (tmp_path / "content.toml").write_text(content, "utf-8")
    clock = [T]
    api = ga.GameApi(state_dir=tmp_path, clock=lambda: clock[0], single_player=False, battle_engine=engine)
    api.rng_factory = lambda: random.Random(1)
    return api, clock


def login(api, creds=None):
    """getUserAccount; returns (reply, the credentials the client now has)."""
    r, how = api.respond("getUserAccount", {"message": "getUserAccount", "udid": "x", **(creds or {})})
    if r.get("new_user"):
        creds = {"user_id": str(r["new_user"]), "password": r["new_password"]}
    return r, creds


def call(api, creds, message, **params):
    return api.respond(message, {"message": message, **creds, **params})


# ---- the store -----------------------------------------------------------------------------------

def shop(tmp_path, engine=False, tutorial="-1", gold=1000, bonds=50):
    api = ga.GameApi(fixture_dir=paths.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: T, battle_engine=engine)
    api.rng_factory = lambda: random.Random(3)
    ask(api, "init")
    p = api.player()
    p.gold, p.war_bonds = gold, bonds
    p.flags["tutorial"] = tutorial
    api.player_store.save(p)
    return api


# ---- the arena -----------------------------------------------------------------------------------

def arena_api(tmp_path):
    api = ga.GameApi(fixture_dir=paths.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: T + 100, battle_engine=True)
    # The tutorial fight is lost now and then (about 1 in 9 with the starting deck): fix the random
    # source (the shuffles and the fight's own choices).
    api.rng_factory = lambda: random.Random(1)
    return api


def arena_fight(api, tid, skip="1"):
    r, how = ask(api, "startHuntingBattle", target_user_id=tid)
    if not r.get("result"):
        return r, how
    bid = str(r["battle_data"]["battle_id"])
    while api.battle is not None:
        hand = api.battle["engine"].battle.sides[0].hand
        r, how = ask(api, "playCard", battle_id=bid, card_uid=str(hand[0]), skip=skip)
    return r, how


# ---- missions the player always loses (rewards) --------------------------------------------------

LOSING = """<root>
  <mission><id>2</id><name>2. Cannon Fire</name><location_id>0</location_id><energy>15</energy>
    <commander>1297</commander><deck><card>3</card><card>3</card></deck>
    <rewards><gold>12</gold><card>2</card></rewards></mission>
  <mission><id>3</id><name>3. Wall</name><energy>1</energy><commander>1301</commander>
    <deck><card>3</card><card>3</card><card>3</card></deck><rewards><gold>16</gold></rewards></mission>
</root>"""


def mission_api(tmp_path, **start):
    api = make_api(tmp_path)
    (tmp_path / "assets" / "missions_basic.xml").write_text(LOSING, "utf-8")
    (tmp_path / "assets" / "cards_section_2.xml").write_text(
        "<root><unit><id>1301</id><name>Wall Captain</name><health>999</health></unit></root>", "utf-8")
    init = json.loads((tmp_path / "fixtures" / "init.json").read_text("utf-8"))
    init["body"].update({"gold_multiplier_missions": {"multiplier": "7", "end_time": "1900126800"},
                         "current_campaigns": [], **start})
    init["body"]["user_data"].update({"gold_earned": "0", "cards_earned": "5"})
    init["body"]["user_cards"]["1000"] = {"num_owned": "1", "num_used": "0"}     # the commander
    (tmp_path / "fixtures" / "init.json").write_text(json.dumps(init), "utf-8")
    return ga.GameApi(fixture_dir=tmp_path / "fixtures", asset_dir=tmp_path / "assets",
                      clock=lambda: T + 100, battle_engine=True)


def play_mission(api, mission):
    r, _ = ask(api, "startMission", mission_id=mission)
    bid = str(r["battle_data"]["battle_id"])
    while True:
        r, how = ask(api, "playCard", battle_id=bid, card_uid=str(api.battle["engine"].battle.sides[0].hand[0]),
                        skip="0")
        if r["battle_data"].get("winner") is not None:
            return r, how


# ---- objectives ----------------------------------------------------------------------------------

def objectives_game(tmp_path, engine=False):
    clock = [T + 100]
    api = ga.GameApi(fixture_dir=paths.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: clock[0], battle_engine=engine)
    api.rng_factory = lambda: random.Random(1)
    return api, clock


def recorded_fight(first, last):
    """A recorded arena fight rebuilt from its replies: every turn as last sent, and its
    cards (the replies' card_map and commanders)."""
    cards = real_cards()
    turns, card_map, data = {}, {}, {}
    for c in recorded_calls()[first:last + 1]:
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


# ---- raids ---------------------------------------------------------------------------------------

def raid_game(tmp_path, raid=161, bonds=2000, extra="health_percent = 2\n", guild="Raiders"):
    """A server with the raid set (at 2% of the boss's health, so the numbers stay small)
    and the player in a guild of their own."""
    if raid is not None:
        (tmp_path / "content.toml").write_text(f"[raids]\nraid = {raid}\n{extra}", "utf-8")
    clock = [T]
    api = ga.GameApi(fixture_dir=paths.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: clock[0])
    api.rng_factory = lambda: random.Random(1)
    ask(api, "init")
    p = api.player()
    p.war_bonds = bonds
    api.player_store.save(p)
    if guild:
        ask(api, "createFaction", faction_name=guild)
    return api, clock


def raid_run(api):
    return api._my_guild()["raid"]


def raid_fight(api):
    level = (raid_run(api) or {"level": 1})["level"]                          # no run until the first raid call
    return ask(api, "fightRaidBattle", raid_id="161", raid_level=str(level))


# ---- a made-up save, and the admin tool on it ----------------------------------------------------

SAVE_START = {
    "time": T, "max_energy": 250, "energy_recharge_time": 60,
    "user_data": {"user_id": "1000001", "money": "100", "tokens": "50", "energy": "250",
                  "last_update_time": str(T), "level": "1"},
    "user_cards": {"2": {"num_owned": "1", "num_used": "0"}, "1000": {"num_owned": "2", "num_used": "1"}},
    "user_decks": {"1": {"cards": {"2": "1"}}},
}


CARDS_XML = """<root>
  <unit><id>2</id><name>Infantry</name><upgrade><level>2</level><card_id>3</card_id></upgrade></unit>
  <unit><id>1000</id><name>Commander Halcyon</name></unit>
  <unit><id>1001</id><name>Commander Malika</name></unit>
</root>"""


def save_api(tmp_path, clock):
    fx = tmp_path / "fixtures"
    fx.mkdir(exist_ok=True)
    (fx / "init.json").write_text(json.dumps({"source": {"session": "s1", "account": "abcd"}, "body": SAVE_START}), "utf-8")
    (fx / "getUserAccount.json").write_text(json.dumps({"source": {}, "body": {
        "time": T, "result": True, "user_data": dict(SAVE_START["user_data"]), "user_cards": SAVE_START["user_cards"]}}), "utf-8")
    (fx / "replay.json").write_text(json.dumps({"source": {}, "calls": [
        {"seq": 5, "message": "buyStoreItem", "key": {"pack_id": "1"},
         "body": {"time": T + 20, "result": True, "new_cards": [{"id": 9}],
                  "user_data": {"money": "0", "tokens": "50", "energy": "250", "last_update_time": str(T + 20)},
                  "user_cards": {"2": {"num_owned": "1", "num_used": "0"}, "9": {"num_owned": "1", "num_used": "0"}}}},
    ]}), "utf-8")
    return ga.GameApi(fixture_dir=fx, clock=lambda: clock[0])


def run_player_cli(tmp_path, monkeypatch, capsys, *args, clock=T + 100):
    save_api(tmp_path, [clock])
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
