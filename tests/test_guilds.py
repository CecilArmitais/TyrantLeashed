"""Tests for guilds (guilds.py) and the guild calls in game_api/guilds.py."""

import json
import random

import pytest

import game_api as ga
import guilds as gd
from helpers import T, ask, real_cards

NAMES = {"1": "Ann", "2": "Bo", "3": "Cy", "4": "Di"}


def info(uid):
    if str(uid) not in NAMES:
        return None
    return {"name": NAMES[str(uid)], "level": 5, "rating": 100 * int(uid), "commander_id": 1000, "last_update_time": T}


@pytest.fixture
def store(tmp_path):
    return gd.GuildStore(tmp_path / "guilds")


@pytest.fixture(scope="module")
def cards():
    return real_cards()


def guild_of(store, *members):
    """A guild led by the first member, the rest joined in order (all open)."""
    g = gd.create(store, members[0], NAMES[members[0]], "Owls", T)
    for i, uid in enumerate(members[1:], 1):
        g = gd.join(store, g["id"], uid, NAMES.get(uid, "Unknown"), 5, T + i, invited=False)
    return g


# ---- creating, joining, leaving -------------------------------------------------------------------

def test_creating_a_guild(store):
    for bad, text in (("ab", "at least 3"), ("a" * 17, "at most 16"), ("No Spaces", "Only letters and numbers")):
        with pytest.raises(gd.GuildError, match=text):
            gd.create(store, "1", "Ann", bad, T)
    g = gd.create(store, "1", "Ann", "Owls", T)
    assert (g["id"], g["leader_id"], g["members"]["1"]["role"]) == (gd.FIRST_GUILD_ID, "1", gd.LEADER)
    assert store.path(g["id"]).is_file() and store.load(g["id"]) == g
    assert [(n["type"], n["data"]) for n in g["news"]] == [(gd.JOINED, {"name": "Ann"})]
    with pytest.raises(gd.GuildError, match="already in a guild"):
        gd.create(store, "1", "Ann", "Hawks", T)
    with pytest.raises(gd.GuildError, match="already a guild called Owls"):
        gd.create(store, "2", "Bo", "OWLS", T)                         # names are unique, any case
    assert gd.create(store, "2", "Bo", "Hawks", T)["id"] == gd.FIRST_GUILD_ID + 1


def test_joining_follows_the_guilds_settings(store):
    g = gd.create(store, "1", "Ann", "Owls", T)
    gd.settings(g, "1", "", gd.INVITE_ONLY, 0)
    store.save(g)
    with pytest.raises(gd.GuildError, match="by invitation only"):
        gd.join(store, g["id"], "2", "Bo", 5, T, invited=False)
    with pytest.raises(gd.GuildError, match="hasn't invited you"):
        gd.join(store, g["id"], "2", "Bo", 5, T, invited=True)
    gd.settings(g, "1", "", gd.OPEN, 6)
    store.save(g)
    with pytest.raises(gd.GuildError, match="from level 6"):
        gd.join(store, g["id"], "2", "Bo", 5, T, invited=False)
    gd.settings(g, "1", "", gd.CLOSED, 0)
    store.save(g)
    with pytest.raises(gd.GuildError, match="isn't recruiting"):
        gd.join(store, g["id"], "2", "Bo", 5, T, invited=False)
    gd.invite(g, "1", "2", False, T)                                    # an invitation gets past all of it
    store.save(g)
    g = gd.join(store, g["id"], "2", "Bo", 1, T, invited=True)
    assert g["members"]["2"]["role"] == gd.MEMBER and g["invites"] == {}
    with pytest.raises(gd.GuildError, match="doesn't exist"):
        gd.join(store, 12345, "3", "Cy", 5, T, invited=False)


def test_a_full_guild(store):
    g = gd.create(store, "1", "Ann", "Owls", T)
    for i in range(2, gd.MAX_MEMBERS + 1):
        g["members"][str(1000 + i)] = {"role": gd.MEMBER, "joined": T, "gp_earned": 0}
    store.save(g)
    with pytest.raises(gd.GuildError, match="is full"):
        gd.join(store, g["id"], "2", "Bo", 5, T, invited=False)


def test_joining_drops_other_invitations(store):
    owls, hawks = gd.create(store, "1", "Ann", "Owls", T), gd.create(store, "2", "Bo", "Hawks", T)
    gd.invite(owls, "1", "3", False, T)
    gd.invite(hawks, "2", "3", False, T)
    store.save(owls)
    store.save(hawks)
    assert [g["name"] for g in gd.invites_for(store, "3")] == ["Owls", "Hawks"]
    gd.join(store, hawks["id"], "3", "Cy", 5, T, invited=True)
    assert gd.invites_for(store, "3") == []


def test_the_leader_leaving_hands_over(store):
    g = guild_of(store, "1", "2", "3")
    g["members"]["3"]["role"] = gd.OFFICER                              # the officer, though Bo joined first
    store.save(g)
    left, successor = gd.leave(store, "1", "Ann", T + 10, lambda uid: NAMES[uid])
    g = store.load(g["id"])
    assert (successor, g["leader_id"], g["members"]["3"]["role"]) == ("3", "3", gd.LEADER)
    assert [(n["type"], n["data"]) for n in g["news"][-2:]] == [(gd.LEFT, {"name": "Ann"}), (gd.NEW_LEADER, {"name": "Cy"})]
    gd.leave(store, "3", "Cy", T + 20, lambda uid: NAMES[uid])
    assert store.load(g["id"])["leader_id"] == "2"                       # then the longest-serving member
    left, successor = gd.leave(store, "2", "Bo", T + 30)
    assert successor is None and store.load(g["id"]) is None and not store.path(g["id"]).exists()   # disbanded
    with pytest.raises(gd.GuildError, match="aren't in a guild"):
        gd.leave(store, "2", "Bo", T)


# ---- ranks, invitations, settings -------------------------------------------------------------------

def test_ranks(store):
    g = guild_of(store, "1", "2", "3")
    with pytest.raises(gd.GuildError, match="Only the Guild Leader"):
        gd.set_rank(g, "2", "3", gd.OFFICER, "Cy", T)
    assert gd.set_rank(g, "1", "2", gd.OFFICER, "Bo", T) == "promoted to officer"
    with pytest.raises(gd.GuildError, match="isn't possible"):
        gd.set_rank(g, "1", "3", gd.LEADER, "Cy", T)                    # a member must be an officer first
    assert gd.set_rank(g, "1", "2", gd.LEADER, "Bo", T) == "made the guild leader"
    assert (g["leader_id"], gd.role(g, "2"), gd.role(g, "1")) == ("2", gd.LEADER, gd.OFFICER)
    assert g["news"][-1]["type"] == gd.NEW_LEADER
    assert gd.set_rank(g, "2", "1", gd.MEMBER, "Ann", T) == "demoted to member"


def test_invitations_and_kicks(store):
    g = guild_of(store, "1", "2", "3")
    with pytest.raises(gd.GuildError, match="Recruitment privileges"):
        gd.invite(g, "2", "4", False, T)                                # a plain member can't invite
    gd.set_rank(g, "1", "2", gd.OFFICER, "Bo", T)
    gd.invite(g, "2", "4", False, T)                                    # an officer can
    assert g["invites"]["4"]["by"] == "2"
    with pytest.raises(gd.GuildError, match="already in a guild"):
        gd.invite(g, "2", "9", True, T)
    with pytest.raises(gd.GuildError, match="Only the Guild Leader"):
        gd.kick(g, "2", "Bo", "3", "Cy", T)
    with pytest.raises(gd.GuildError, match="Demote an officer"):
        gd.kick(g, "1", "Ann", "2", "Bo", T)
    gd.kick(g, "1", "Ann", "3", "Cy", T)
    assert "3" not in g["members"] and g["news"][-1]["data"] == {"kicker_name": "Ann", "name": "Cy"}


def test_settings(store):
    g = guild_of(store, "1", "2")
    with pytest.raises(gd.GuildError, match="Only the Guild Leader"):
        gd.settings(g, "2", "hi", 0, 0)
    for status, level in ((3, 0), (0, 31), ("x", 0)):
        with pytest.raises(gd.GuildError, match="aren't valid"):
            gd.settings(g, "1", "hi", status, level)
    gd.settings(g, "1", 'Raid at "8"!\\ [now]' + "x" * 300, "1", "12")
    assert (g["recruitment_status"], g["min_level"]) == (gd.INVITE_ONLY, 12)
    assert g["message"].startswith("Raid at '8'! now") and len(g["message"]) == gd.MESSAGE_MAX


# ---- chat and news ----------------------------------------------------------------------------------

def test_chat_and_news(store):
    g = guild_of(store, "1", "2")
    with pytest.raises(gd.GuildError, match="Type a message"):
        gd.chat(g, "1", "Ann", "   ", T)
    e = gd.chat(g, "1", "Ann", 'Say "hi" \\ to [Bo]' + "!" * 200, T)
    assert e["activity_id"] is None                                     # it gets one when the guild is saved
    store.save(g)
    assert e["data"]["message"].startswith("Say 'hi' to Bo") and len(e["data"]["message"]) == gd.CHAT_MAX
    news = gd.news_json(gd.news_since(g, 0))
    assert [n["activity_id"] for n in news] == [1, 2, 3] and [n["type"] for n in news] == [1, 1, 5]
    data = news[-1]["data"]
    assert isinstance(data, str) and "\\" not in data and json.loads(data)["name"] == "Ann"
    assert gd.news_since(g, 2) == [g["news"][-1]] and gd.news_since(g, 3) == []
    assert gd.news_since(g, 999) == g["news"]                           # not this guild's id: the backlog
    for i in range(gd.NEWS_KEPT + 20):
        gd.chat(g, "2", "Bo", f"line {i}", T)
        store.save(g)                                                   # as the server does, line by line
    assert len(g["news"]) == gd.NEWS_KEPT and len(gd.news_since(g, 0)) == gd.NEWS_BACKLOG
    assert g["news"][-1]["activity_id"] == 3 + gd.NEWS_KEPT + 20          # ids keep counting


def test_ids_are_never_reused(store):
    """The client skips news ids it has shown, even after switching guilds, so they only
    grow across the whole server; a disbanded guild's id doesn't come back either."""
    owls = gd.create(store, "1", "Ann", "Owls", T)
    gd.leave(store, "1", "Ann", T)                                      # the last member: disbanded
    hawks = gd.create(store, "1", "Ann", "Hawks", T)
    assert hawks["id"] == owls["id"] + 1
    assert hawks["news"][0]["activity_id"] > owls["news"][-1]["activity_id"]
    assert gd.news_since(hawks, owls["news"][-1]["activity_id"]) == hawks["news"]   # shown, not skipped
    again = gd.GuildStore(store.root)                                   # the counters are on disk
    assert again.next_id() == hawks["id"] + 1


# ---- guild points and the fortress --------------------------------------------------------------------

def test_donations(store):
    g = guild_of(store, "1", "2")
    with pytest.raises(gd.GuildError, match="costs 100 gold"):
        gd.donate(g, "2", "Bo", 99, 100, T)
    assert gd.donate(g, "2", "Bo", 550, 100, T) == 5
    assert (g["guild_points"], g["members"]["2"]["gp_earned"]) == (5, 5)
    assert g["news"][-1]["type"] == gd.DONATED and g["news"][-1]["data"]["points"] == 5


@pytest.mark.needs_game
def test_fortress_cards(store, cards):
    g = guild_of(store, "1", "2")
    tesla, tesla2, cannon = cards[2700], cards[2701], cards[2736]
    with pytest.raises(gd.GuildError, match="Only officers"):
        gd.buy_card(g, "2", "Bo", 11, tesla, cards, T)
    with pytest.raises(gd.GuildError, match="siege slot"):
        gd.buy_card(g, "1", "Ann", 11, cannon, cards, T)
    with pytest.raises(gd.GuildError, match="isn't a fortress slot"):
        gd.buy_card(g, "1", "Ann", 1, tesla, cards, T)                    # the core holds no card
    with pytest.raises(gd.GuildError, match="more Guild Points"):
        gd.buy_card(g, "1", "Ann", 11, tesla, cards, T)
    g["guild_points"] = 1000
    assert gd.buy_card(g, "1", "Ann", 11, tesla, cards, T) == ("bought", 50)
    assert (g["guild_points"], g["cards"], g["slots"]) == (950, {"2700": 1}, {"11": 2700})
    assert gd.buy_card(g, "1", "Ann", 11, tesla2, cards, T) == ("upgraded", 450)   # the copy becomes level 2
    assert (g["guild_points"], g["cards"], g["slots"]) == (500, {"2701": 1}, {"11": 2701})
    assert [n["type"] for n in g["news"][-2:]] == [gd.CARD_BUY, gd.CARD_UPGRADE]
    assert gd.remove_card(g, "1", "Ann", 11, cards, T) == "Tesla Coil"
    assert g["slots"] == {} and gd.spare(g, 2701) == 1
    assert gd.buy_card(g, "1", "Ann", 12, tesla2, cards, T) == ("equipped", 0)     # a spare copy is free
    assert g["guild_points"] == 500 and g["slots"] == {"12": 2701}
    out = gd.fortress_json(g)
    assert [(s["slot_id"], s["card_id"], s["health"]) for s in out] == [(11, 0, 100), (12, 2701, 100), (21, 0, 100),
                                                                       (22, 0, 100)]


# ---- what the client reads ---------------------------------------------------------------------------

def test_the_guild_as_the_client_reads_it(store):
    g = guild_of(store, "1", "2", "7")                                  # 7: a player the server doesn't know
    g["cards"] = {"2700": 2}
    out = gd.faction_json(g, info, 100)
    assert (out["faction_id"], out["leader_name"], out["num_members"], out["total_rating"]) == ("900001", "Ann", 3, 300)
    assert out["faction_cards"] == {"2700": {"num_owned": 2}} and out["gp_cost"] == 100
    assert not {"faction_war_points", "raid_level", "conquest_points"} & set(out)   # they'd replace the rating
    roles = {m["user_id"]: (m["name"], m["member_role"]) for m in out["members"]}
    assert roles == {"1": ("Ann", 31), "2": ("Bo", 1), "7": ("Unknown", 1)}
    assert gd.list_json(g, info, 2) == {"faction_id": "900001", "name": "Owls", "leader_name": "Ann", "num_members": 3,
                                        "max_members": 50, "total_rating": 300, "rank": 2}


# ---- the game server ----------------------------------------------------------------------------------

def game(tmp_path, content="", gold=5000):
    if content:
        (tmp_path / "content.toml").write_text(content, "utf-8")
    clock = [T]
    api = ga.GameApi(fixture_dir=ga.FIXTURE_DIR, state_dir=tmp_path, clock=lambda: clock[0])
    api.rng_factory = lambda: random.Random(1)
    ask(api, "init")
    p = api.player()
    p.gold = gold
    api.player_store.save(p)
    return api, clock


def other_guild(api, name="Hawks", leader="2", status=gd.OPEN):
    g = gd.create(api.guild_store, leader, "someone", name, T)
    g["recruitment_status"] = status
    api.guild_store.save(g)
    return g


@pytest.mark.needs_game
def test_no_guild_and_the_server_guilds(tmp_path):
    api, _ = game(tmp_path)
    r, _ = ask(api, "init")
    assert r["faction"] == {"faction_id": 0} and r["faction_invites"] == []
    assert r["guild_donations_enabled"] == 1                            # content.toml's, not the recording's 0
    r, how = ask(api, "getRecruitingFactions")
    assert r["factions"] == [] and how == "guilds taking members: 0"   # never the recorded guilds
    hawks = other_guild(api)
    other_guild(api, "Closed", "3", gd.CLOSED)
    r, _ = ask(api, "getRecruitingFactions")
    assert [(f["name"], f["leader_name"], f["num_members"]) for f in r["factions"]] == [("Hawks", "Unknown", 1)]
    hawks["invites"]["1000001"] = {"by": "2", "time": T}
    api.guild_store.save(hawks)
    r, how = ask(api, "updateFaction", last_activity_id="0")
    assert [f["name"] for f in r["faction_invites"]] == ["Hawks"] and how == "no guild; 1 invitation(s)"
    r, how = ask(api, "sendFactionMessage", chat="hi", last_activity_id="0")
    assert r["result"] is False and r["result_message"] == ["You aren't in a guild."]


@pytest.mark.needs_game
def test_creating_a_guild_in_the_game(tmp_path):
    api, _ = game(tmp_path)
    r, how = ask(api, "createFaction", faction_name="x!")
    assert r["result"] is False and r["result_message"] == ["Invalid name! Must be at least 3 characters."]
    assert r["faction"] == {"faction_id": 0}
    r, how = ask(api, "createFaction", faction_name="Owls")
    assert r["result"] is True and how == "created guild Owls (900001)" and "result_message" not in r
    f = r["faction"]
    assert (f["faction_id"], f["name"], f["leader_id"], f["num_members"]) == ("900001", "Owls", "1000001", 1)
    assert f["members"][0]["member_role"] == gd.LEADER and r["fortress_slots"][0]["slot_id"] == 11
    assert (tmp_path / "guilds" / "900001.json").is_file()
    assert "faction" not in json.loads((tmp_path / "player.json").read_text("utf-8"))["player"]   # apart from the save
    r, _ = ask(api, "init")
    assert r["faction"]["name"] == "Owls"                               # the login carries it
    r, how = ask(api, "getTopFactions")
    assert [(f["name"], f["rank"]) for f in r["factions"]] == [("Owls", 1)]
    r, how = ask(api, "getProfileData", target_user_id="1000001")
    assert r["player_info"]["user_id"] == "1000001" and r["player_info"]["deck"]["commander_id"]
    r, _ = ask(api, "getProfileData", target_user_id="2")
    assert r["player_info"] == {"user_id": ""}                          # not in the guild


@pytest.mark.needs_game
def test_joining_and_leaving_another_guild(tmp_path):
    api, _ = game(tmp_path)
    hawks = other_guild(api)
    r, how = ask(api, "joinFaction", faction_id=str(hawks["id"]))
    assert r["result"] and how == "joined guild Hawks (900001)" and r["faction"]["num_members"] == 2
    r, how = ask(api, "leaveFaction")
    assert r["faction"] == {"faction_id": 0} and how == "left guild Hawks"
    assert api.guild_store.load(hawks["id"])["leader_id"] == "2"
    other_guild(api, "Shut", "3", gd.CLOSED)
    r, how = ask(api, "joinFaction", faction_id="900002")
    assert r["result"] is False and r["result_message"] == ["Shut isn't recruiting."]


@pytest.mark.needs_game
def test_chat_settings_and_news_in_the_game(tmp_path):
    api, clock = game(tmp_path)
    ask(api, "createFaction", faction_name="Owls")
    clock[0] += 60
    r, how = ask(api, "sendFactionMessage", chat='hello "all"', last_activity_id="1")
    assert [json.loads(n["data"]) for n in r["faction_news"]] == [{"name": api._name_of(1000001),
                                                                   "message": "hello 'all'"}]
    r, how = ask(api, "setAllFactionSettings", text="Raid tonight", recruitment_status="2", min_level="5")
    assert how == "guild settings: recruitment closed, minimum level 5"
    assert (r["faction"]["message"], r["faction"]["recruitment_status"], r["faction"]["min_level"]) == ("Raid tonight", 2, 5)
    r, _ = ask(api, "updateFaction", last_activity_id="0")
    assert [n["activity_id"] for n in r["faction_news"]] == [1, 2]
    r, how = ask(api, "sendFactionInvite", target_user_id="555")
    assert r["result_message"] == ["There's no such player on this server."]


@pytest.mark.needs_game
def test_donating_gold(tmp_path):
    api, _ = game(tmp_path, gold=1050)
    ask(api, "createFaction", faction_name="Owls")
    r, how = ask(api, "donateFactionGold", amount="1050")
    assert how == "donated 1000 gold to Owls for 10 guild points (10 now)"
    assert api.player().gold == 50 and r["faction"]["guild_points"] == 10 and r["user_data"]["money"] in (50, "50")
    assert r["faction"]["members"][0]["gp_earned"] == 10
    r, how = ask(api, "donateFactionGold", amount="500")
    assert r["result"] is False and r["result_message"] == ["Not enough gold: that's 500, and you have 50."]


@pytest.mark.needs_game
def test_donations_can_be_switched_off(tmp_path):
    api, _ = game(tmp_path, content="[guilds]\ndonations = false\n")
    r, _ = ask(api, "init")
    assert r["guild_donations_enabled"] == 0
    ask(api, "createFaction", faction_name="Owls")
    r, how = ask(api, "donateFactionGold", amount="500")
    assert r["result_message"] == ["Donations are switched off on this server."] and r["guild_donations_enabled"] == 0


@pytest.mark.needs_game
def test_the_fortress_in_the_game(tmp_path):
    api, _ = game(tmp_path, content="[guilds]\ngold_per_point = 10\n", gold=600)
    ask(api, "createFaction", faction_name="Owls")
    ask(api, "donateFactionGold", amount="600")                       # 60 guild points
    r, how = ask(api, "buyFactionCard", slot_id="21", card_id="2736")
    assert how == "fortress: bought Lightning Cannon (level 1) in slot 21 for 50 guild points (10 left)"
    assert r["faction"]["faction_cards"] == {"2736": {"num_owned": 1}}
    assert [s["card_id"] for s in r["fortress_slots"]] == [0, 0, 2736, 0]
    r, how = ask(api, "buyFactionCard", slot_id="21", card_id="2737")
    assert r["result_message"][0].startswith("You need more Guild Points")
    r, how = ask(api, "removeFactionCard", slot_id="21")
    assert how == "fortress: removed Lightning Cannon from slot 21" and r["fortress_slots"][2]["card_id"] == 0
    r, _ = ask(api, "updateFaction", last_activity_id="0")
    assert [n["type"] for n in r["faction_news"]] == [1, 41, 21, 23]


@pytest.mark.needs_game
def test_practice_fights_against_a_guild_member(tmp_path):
    from helpers import call, login, server
    api, clock = server(tmp_path, engine=True)
    creds = []
    for _ in range(3):
        _, c = login(api)
        call(api, c, "init")
        creds.append(c)
    ann, bob, cid = creds
    call(api, ann, "createFaction", faction_name="Owls")
    call(api, cid, "joinFaction", faction_id=str(api.guild_store.by_name("Owls")["id"]))
    before = api._store_for(ann["user_id"]).load()

    r, how = call(api, ann, "fightPracticeBattle", target_user_id=bob["user_id"], fight_attack_deck="0", is_surge="0")
    assert r["result_message"] == ["You can only practice against a member of your guild."], how
    r, how = call(api, ann, "fightPracticeBattle", target_user_id=ann["user_id"], fight_attack_deck="0", is_surge="0")
    assert r["result_message"] == ["You can't practice against yourself."]

    r, how = call(api, ann, "fightPracticeBattle", target_user_id=cid["user_id"], fight_attack_deck="0", is_surge="1")
    assert how.startswith("practice surge vs Player1000003's defense deck"), how
    bd = r["battle_data"]
    assert bd["host_is_attacker"] is False and bd["rewards"] == [{"user_id": ann["user_id"], "gold": 0, "sp": 0}]
    assert bd["enemy_name"] == "Player1000003" and "winner" in bd

    r, how = call(api, ann, "fightPracticeBattle", target_user_id=cid["user_id"], fight_attack_deck="1", is_surge="0")
    assert how.startswith("engine: started a practice fight with Player1000003's attack deck"), how
    hand = r["battle_data"]["turn"]["1"]["draws"] if "1" in r["battle_data"]["turn"] else None
    r, how = call(api, ann, "playCard", card_uid=str((hand or [1])[0]), skip="1", battle_id="1")   # the deck flag
    assert "(you won)" in how or "(you lost)" in how, how
    assert r["battle_data"]["rewards"] == [{"user_id": ann["user_id"], "gold": 0, "sp": 0}] and "user_data" in r
    after = api._store_for(ann["user_id"]).load()
    assert (after.gold, after.arena["rating"], after.arena["stamina"]) == \
        (before.gold, before.arena["rating"], before.arena["stamina"])          # nothing paid, nothing spent
