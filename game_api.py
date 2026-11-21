"""The offline server's game API: answers api.php messages.

Everything comes from fixtures built by make_fixtures.py (recorded responses with
every account identifier replaced):

  getUserAccount, init   the recorded login of a new account
  startMission/playCard  recorded battles, replayed as scripts: a mission starts the
                         next recorded battle of that mission, and each card played
                         must be the card the recording played at that point. Starting
                         a mission costs its energy (from the mission file). With
                         --engine, missions are real battles instead (battle/).
                         Either way, a finished mission pays its rewards into the save:
                         gold, its card on the first win, stars (rewards.py).
  setDeckCards           real logic: the deck is saved (cards you own, up to 10)
  forfeitBattle          real logic: Surrender; the battle is lost and paid as a loss
  getHuntingTargets,     with --engine, real logic: the arena's AI rivals, fights for
  startHuntingBattle,    stamina, rating and level-ups, and the refresh button
  buyRivalsRefresh       (arena.py)
  buyStaminaRefillTokens real logic: stamina up to the max, for war bonds
  unlockCommander        real logic: Promote, a commander and bonus cards for a
                         promotion point
  getBattleReports       real logic: the Reports tab, the player's recent arena fights
  buyPackGold,           real logic: the Basic Pack, the Epic Pack, the gold Basic Pack
  buyPackTokens,         bundles and base-set faction packs (the Blight Pack), with the
  buyStorePromoGold,     game's published drop rates (packs.py), and event boxes
  buyStorePromoTokens    (boxes.py) and their deals; other promos are replayed. What the
                         store offers comes from state/content.toml (content.py)
  upgradeCard,           real logic: the Upgrade screen. Upgrades cost salvage points
  salvageCard,           (SP), salvage gives them, locks keep cards from salvage, and
  salvageL1CommonCards,  fusion turns a recipe's cards into the fused card, with the
  salvageL1RareCards,    game's own numbers (upgrades.py)
  setCardLock,
  fuseCard
  completeAchievement    real logic: achievements (achievements.py). The permanent ones
                         finish by themselves and pay at once (completed_achievements in
                         that reply); daily battle objectives count arena fights and are
                         claimed with this message
  useDailyBonus          real logic: the home screen crate (daily_crate.py; its rewards
                         are set in content.toml)
  buyUpgrade, store      real logic: +50 Max Energy, +10 Card Slots (store promos of
  promos item_type 4     item_type 4) and the deck screen's Extra Deck Slot
                         (store_upgrades.py)
  setUserName,           real logic: the player's name, active deck and defence deck
  setActiveDeck,
  setDefenseDeck
  getUserData,           real logic: the save's user_data, cards and decks; card_data
  getUserCards,          is empty, as recorded; getVersionNumber is the envelope
  getUserDecks,
  getCardData,
  getVersionNumber
  createFaction, joinFaction, leaveFaction, acceptFactionInvite, sendFactionInvite,
  kickFactionMember, setMemberRank, setAllFactionSettings, sendFactionMessage,
  updateFaction, getRecruitingFactions, getTopFactions, getUsersLFG, getProfileData,
  donateFactionGold, buyFactionCard, removeFactionCard, updateFactionWar
                         real logic: guilds (guilds.py), kept by the server in
                         state/guilds/, apart from the player's save
  getRaidInfo,           real logic: raids (raids.py), set in content.toml: each guild's
  getCurrentRaids,       run of the boss (levels, health), each member's raid energy,
  fightRaidBattle,       fights resolved by the battle engine in one reply, the reward
  claimRaidReward,       tiers; a guild is needed
  refillEventBattleEnergy,
  getRaidLeaderboard,
  getPreviousRaidTopLeaderboard
  buyEnergyRefillTokens  real logic: max energy on top of the current energy, for
                         wb_energy_cost (30) war bonds
  setUserFlag            real logic: the flag is saved (tutorial progress, guides seen,
                         battle speed, autopilot, music...), so it survives a reload
  other messages         replayed when the client asks exactly what was recorded
                         (same message and same details, e.g. buyPackGold pack_id=1)
  recordEvent            analytics; answered with an empty reply, like the real server

Every reply gets its timestamps moved forward so the recording looks current, and
a fresh `request` echo. Where a reply carries the player's data, the saved card
inventory, gold, war bonds, energy, flags and mission progress (player_state.py)
replace the recorded ones.
Anything else gets the real server's error shape,
{"result": false, "result_message": [...]}, which the game shows in a dialog.

Standard library only.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import random
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from battle.cards import CardDB
from battle.engine import ATTACKER, DEFENDER, FIRST_UID, Battle, BattleError, RandomChooser, Setup, shuffled_deck
from battle.missions import load_missions
from battle.protocol import BattleSession
import achievements
from accounts import AccountStore
import arena
import guilds
import raids
from battle import effects as bge
import boxes
import content as store_content
import daily_crate
import store_upgrades
import packs
import upgrades
from player_state import Player, PlayerStore, deck_from_json, decks_json, level_info
from rewards import DECK_SIZE, MissionResult, RewardRules, finish_mission
from content import Content, ContentStore
from settings import Settings, SettingsStore, apply_to_login

# The layout around the server folder: game/ holds the game's own files, which each
# player extracts for themselves (the client, the archived game files, the recorded
# login), and state/ the player's save and the server's settings.
ROOT_DIR = Path(__file__).resolve().parent.parent
GAME_DIR = ROOT_DIR / "game"
FIXTURE_DIR = GAME_DIR / "fixtures"
STATE_DIR = ROOT_DIR / "state"
# The game data files (cards, missions, tutorials) from the archive.
ASSET_DIR = GAME_DIR / "assets" / "mobile.tyrantonline.com" / "assets"
LOGIN = ("getUserAccount", "init")
SET_FLAG = "setUserFlag"
ENERGY_REFILL = "buyEnergyRefillTokens"
SET_DECK = "setDeckCards"
FORFEIT = "forfeitBattle"           # the Surrender button
TARGETS = "getHuntingTargets"       # the arena's rivals list
RIVALS_REFRESH = "buyRivalsRefresh"  # the refresh button on the Rivals screen
STAMINA_REFILL = "buyStaminaRefillTokens"   # the + by stamina ("attacks")
UNLOCK = "unlockCommander"          # Promote
REPORTS = "getBattleReports"        # the Reports tab
PACK_GOLD = "buyPackGold"           # the store's Basic Pack (pack_id 1)
PACK_WB = "buyPackTokens"           # the store's Epic Pack (pack_id 1000), for war bonds
PROMO_GOLD = "buyStorePromoGold"    # store promos for gold: the Basic Pack x4 / x20 bundles
PROMO_WB = "buyStorePromoTokens"    # store promos for war bonds: faction packs (the Blight Pack), boxes
UPGRADE = "upgradeCard"             # the Upgrade screen: a card one level up, for SP
SALVAGE = "salvageCard"             # ...one card for SP
SALVAGE_ALL = {"salvageL1CommonCards": 1, "salvageL1RareCards": 2}   # every spare level-1 common / rare
LOCK = "setCardLock"                # the salvage lock on a card
FUSE = "fuseCard"                   # a fusion recipe's cards into the fused card
CLAIM = "completeAchievement"       # the Complete button of a finished daily objective
CRATE = "useDailyBonus"             # the crate on the home screen
GUILD_MESSAGES = {"createFaction", "joinFaction", "acceptFactionInvite", "leaveFaction", "sendFactionInvite",
                  "kickFactionMember", "setMemberRank", "setAllFactionSettings", "sendFactionMessage", "updateFaction",
                  "getFactionNews", "getRecruitingFactions", "getTopFactions", "getUsersLFG", "getProfileData",
                  "donateFactionGold", "buyFactionCard", "removeFactionCard", "updateFactionWar"}
RAID_MESSAGES = {"getRaidInfo", "getCurrentRaids", "fightRaidBattle", "claimRaidReward", "refillEventBattleEnergy",
                 "getRaidLeaderboard", "getPreviousRaidTopLeaderboard"}
BUY_UPGRADE = "buyUpgrade"          # the deck screen's Extra Deck Slot
SET_NAME = "setUserName"
SET_ACTIVE, SET_DEFENSE = "setActiveDeck", "setDefenseDeck"
REFRESH = {"getUserData": ("user_data",), "getUserCards": ("user_data", "user_cards"),
           "getUserDecks": ("user_decks",), "getCardData": ("card_data",), "getVersionNumber": ()}
NAME_LENGTH = 20                    # ours: the real limit isn't known
DEFAULT_STAMINA_REFILL_COST = 30    # wb_stamina_cost in the recorded init
DEFAULT_REFRESH_COST = 50           # gold_rivals_cost in the recorded init
ARENA_START = "startHuntingBattle"
# An arena fight's battle fields, as recorded (no battleground effect).
ARENA_FIELDS = {"kv_store": [], "effect_id": 0, "battleground_effect": {"card_uid": 0},
                "battleground_effects": {"global": [{"card_uid": 0}], "attacker": [], "defender": []}}
DEFAULT_REFILL_COST = 30            # wb_energy_cost in the recorded init
INVALID_FLAG = "Invalid flag."      # the real server's words (recorded for tutorial=5)

# The one offline player. make_fixtures.py writes these in place of the recorded
# account's identifiers, so the fixtures and the server agree on who the player is.
OFFLINE_USER_ID = 1000001
OFFLINE_PASSWORD = "0ff11e0000000000000000000000c0de"
OFFLINE_SYNCODE = "0ff11e" + "0" * 54 + "c0de"   # 64 characters, like the real ones
OFFLINE_NAME = "Offline Commander"

# Request parameters that say nothing about *what* is being asked: credentials,
# client bookkeeping, and per-battle ids. Replay matches on everything else.
VOLATILE_PARAMS = frozenset({
    "message", "user_id", "password", "syncode", "udid", "kong_id", "kong_name", "kong_token",
    "hash", "client_signature", "client_time", "timestamp", "unity", "client_version", "device_type",
    "os_version", "platform", "data_usage", "api_stat_name", "api_stat_time", "client_events", "dummy",
    "battle_id", "host_id",
})
NOT_REPLAYED = frozenset({"getUserAccount", "init", "recordEvent"})  # handled without the replay library
BATTLE_STARTS = frozenset({"startMission", "startHuntingBattle"})     # replies with a new battle_data
BATTLE_TURN = "playCard"

SHIFT_WINDOW_S = 365 * 86400       # timestamps this close to the recorded server time are "live"
NO_SHIFT_UNDER = ("card_files",)   # file version stamps, not live times
DEFAULT_ENVELOPE = {"version": "80.560", "guild_donations_enabled": 1, "guild_supplies_enabled": 1}


class Session:
    """What a player's client is in the middle of: the battle being played, how far the
    replayed recordings have got, and the last raid fight (for a forfeit during its
    replay). A new login starts it over."""

    def __init__(self):
        self.cursors: dict[tuple, int] = defaultdict(int)
        self.battle_cursor: dict[tuple, int] = defaultdict(int)
        self.battle: dict | None = None
        self.last_raid_reply: dict | None = None


NOT_LOGGED_IN = "Your login wasn't recognized on this server. Reload the game to log in again."


class NotSupported(Exception):
    """Raised with a reason the player can read; it ends up in the game's dialog."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _as_time(v) -> int | None:
    """An int or digit string that looks like a unix time in seconds."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        x = v
    elif isinstance(v, str) and v.isdigit() and len(v) == 10:
        x = int(v)
    else:
        return None
    return x if 1_000_000_000 < x < 3_000_000_000 else None


def shift_times(obj, recorded_now: int, offset: int, path: tuple = ()):
    """Move every timestamp within SHIFT_WINDOW_S of recorded_now by offset seconds,
    keeping its type (int stays int, string stays string). Returns the new object."""
    if path and path[0] in NO_SHIFT_UNDER:
        return obj
    if isinstance(obj, dict):
        return {k: shift_times(v, recorded_now, offset, path + (k,)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shift_times(v, recorded_now, offset, path + (i,)) for i, v in enumerate(obj)]
    t = _as_time(obj)
    if t is None or abs(t - recorded_now) > SHIFT_WINDOW_S:
        return obj
    return t + offset if isinstance(obj, int) else str(t + offset)


def with_battle_id(obj, battle_id: str):
    """Every `battle_id` field set to this battle's id (same type as before)."""
    if isinstance(obj, dict):
        return {k: (type(v)(battle_id) if k == "battle_id" and isinstance(v, (int, str)) and not isinstance(v, bool)
                    else with_battle_id(v, battle_id)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [with_battle_id(v, battle_id) for v in obj]
    return obj


def battle_name(message: str, params: dict) -> str:
    if message == "startMission":
        return f"mission {params.get('mission_id')}"
    details = ", ".join(f"{k}={v}" for k, v in sorted(params.items()) if k not in VOLATILE_PARAMS)
    return f"{message} ({details})" if details else message


def battle_over(body: dict) -> bool:
    bd = body.get("battle_data") or {}
    return bd.get("winner") is not None or bd.get("end_time") is not None


def battle_won(body: dict) -> bool:
    """winner is 1 when the player won (0 for the recorded losses)."""
    return str((body.get("battle_data") or {}).get("winner")) == "1"


def flag_value(raw):
    """A setUserFlag value the way the server keeps it: lists (guides_complete
    "[102,101]") as real lists, everything else as a string, like the recorded replies."""
    if isinstance(raw, str) and raw[:1] in "[{":
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return str(raw)


def mission_costs(asset_dir: Path, file_name: str | None) -> dict[str, int]:
    """Mission id -> energy cost, from the mission file the game loads (`mission_file`,
    missions_basic.xml). The recorded charges for missions 1-7 match it exactly. ({} if
    the file isn't there.)"""
    if not file_name:
        return {}
    f = Path(asset_dir) / file_name
    if not f.is_file():
        return {}
    out = {}
    for m in ET.parse(f).getroot().findall("mission"):
        mid, energy = (m.findtext("id") or "").strip(), (m.findtext("energy") or "").strip()
        if mid and energy.isdigit():
            out[mid] = int(energy)
    return out


def tutorial_names(asset_dir: Path, file_name: str | None) -> list[str]:
    """The tutorial file's <tutorial> names in order. The tutorial flag is the index of
    the current one, then -1 once all are done. [] if the file isn't there."""
    if not file_name:
        return []
    f = Path(asset_dir) / file_name
    if not f.is_file():
        return []
    return [t.findtext("name") or "?" for t in ET.parse(f).getroot().findall("tutorial")]


def encode(reply: dict) -> bytes:
    """JSON written the way the real (PHP) server writes it: no spaces, '/' escaped
    as '\\/', non-ASCII as \\u escapes. The client's JSON reader warns about every
    value ("improper JSON formatting") when the spacing differs."""
    text = json.dumps(reply, separators=(",", ":"), ensure_ascii=True)
    return text.replace("/", "\\/").encode("ascii")


def personalize(body: dict, user_id: int) -> None:
    """A recorded reply (made for the offline player) for another player: their id where
    the client reads its own."""
    mine, uid = str(OFFLINE_USER_ID), str(user_id)
    if isinstance(body.get("user_data"), dict) and str(body["user_data"].get("user_id")) == mine:
        body["user_data"]["user_id"] = uid
    bd = body.get("battle_data")
    if isinstance(bd, dict):
        if str(bd.get("host_id")) == mine:
            bd["host_id"] = uid
        for r in bd.get("rewards") or []:
            if isinstance(r, dict) and str(r.get("user_id")) == mine:
                r["user_id"] = uid


def echo(params: dict) -> dict:
    """What the real server sends back as `request`: the parameters, plus a fingerprint."""
    out = {k: v for k, v in params.items()}
    out["__request_fingerprint"] = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()
    return out


def request_key(message: str, params: dict) -> tuple:
    """What a request asks, ignoring credentials and bookkeeping."""
    return message, tuple(sorted((k, str(v)) for k, v in params.items() if k not in VOLATILE_PARAMS))


class GameApi:
    def __init__(self, fixture_dir: Path = FIXTURE_DIR, clock=time.time, state_dir: Path | None = None,
                 asset_dir: Path | None = None, battle_engine: bool = False, single_player: bool = True) -> None:
        """state_dir: where the saves live (default: STATE_DIR, or <fixture_dir>/../state
        for other fixtures).
        asset_dir: the game data files (default: the archive's ASSET_DIR).
        battle_engine: missions are real battles from the battle engine instead of
        replayed recordings (docs/battle-engine.md; in progress).
        single_player: every login is the offline player (1000001), saved in
        state/player.json, as before accounts existed (the tests and tools use this).
        Otherwise each browser has its own account (accounts.py) and save
        (state/players/<user_id>.json), and every request must carry its credentials;
        a single-player save found in state/ becomes player 1000001's."""
        self.clock = clock
        self.battle_engine = battle_engine
        self.rng_factory = random.Random       # shuffles and random picks; tests swap in a seeded one
        self._assets = ASSET_DIR if asset_dir is None else asset_dir
        self._cards: CardDB | None = None
        self._missions: dict | None = None
        fixture_dir = Path(fixture_dir)
        self.fixtures: dict[str, dict] = {}
        for name in LOGIN:
            f = fixture_dir / f"{name}.json"
            if f.is_file():
                self.fixtures[name] = json.loads(f.read_text("utf-8"))
        assets = ASSET_DIR if asset_dir is None else asset_dir
        self.tutorials = tutorial_names(assets, self._login_value("tutorial_file"))
        self.mission_costs = mission_costs(assets, self._login_value("mission_file"))
        # The saved player starts from the recorded init (gold, war bonds, energy, cards).
        self.player_store: PlayerStore | None = None
        self.reward_rules = RewardRules()
        if state_dir is not None:
            state = Path(state_dir)
        else:
            state = STATE_DIR if fixture_dir.resolve() == FIXTURE_DIR.resolve() else fixture_dir.parent / "state"
        self.settings_store = SettingsStore(state / "settings.json")     # the gold bonuses
        self.content_store = ContentStore(state / "content.toml")        # the store and event boxes
        self.guild_store = guilds.GuildStore(state / "guilds")            # the server's guilds
        self.single_player = single_player
        self._state = state
        self.accounts = AccountStore(state / "accounts.json")             # who may play (multi-player)
        self._lock = threading.RLock()                    # one request at a time, whoever sends it
        self._stores: dict[str, PlayerStore] = {}
        self._sessions: dict[str, Session] = {}
        self.migrated = False
        start = self.fixtures.get("init")
        if start is not None:
            body = start["body"]
            self._start, self._start_source = body, start.get("source", {})
            self.reward_rules = RewardRules.from_start(body)
            self.max_energy = int(body.get("max_energy") or 250)
            self.energy_every = int(body.get("energy_recharge_time")
                                    or (body.get("user_data") or {}).get("energy_recharge_time") or 60)
            self.stamina_every = int(body.get("stamina_recharge_time")
                                     or (body.get("user_data") or {}).get("stamina_recharge_time") or 900)
        if not single_player:
            self._take_in_single_player_save()
        self.use_player(OFFLINE_USER_ID)                  # until a request says who it is
        self.replays: dict[tuple, list[dict]] = {}       # request key -> recorded bodies, in order
        self.scripts: dict[tuple, list[dict]] = {}       # battle-start request key -> recorded battles
        f = fixture_dir / "replay.json"
        if f.is_file():
            self._index(json.loads(f.read_text("utf-8")).get("calls", []))
        self._reset_session()

    # ---- players --------------------------------------------------------------------------------------

    def _save_path(self, user_id) -> Path:
        if self.single_player:
            return self._state / "player.json"
        return self._state / "players" / f"{int(user_id)}.json"

    def _store_for(self, user_id) -> PlayerStore | None:
        """A player's save (None without starting data to make one from)."""
        if self.fixtures.get("init") is None:
            return None
        uid = str(int(user_id))
        if uid not in self._stores:
            self._stores[uid] = PlayerStore(self._save_path(uid), clock=self.clock, default_max_energy=self.max_energy)
        return self._stores[uid]

    def use_player(self, user_id) -> None:
        """Act as this player: their save and their session, until another request (or
        call) says otherwise. Requests hold the lock while they do."""
        self.uid = int(user_id)
        self.player_store = self._store_for(self.uid)

    def _session(self) -> Session:
        return self._sessions.setdefault(str(self.uid), Session())

    battle = property(lambda self: self._session().battle,
                      lambda self, v: setattr(self._session(), "battle", v))
    cursors = property(lambda self: self._session().cursors)
    battle_cursor = property(lambda self: self._session().battle_cursor)
    _last_raid_reply = property(lambda self: self._session().last_raid_reply,
                                lambda self, v: setattr(self._session(), "last_raid_reply", v))

    def _take_in_single_player_save(self) -> None:
        """A save from single-player days (state/player.json) becomes player 1000001's, with
        the offline login the browser already has. That password is public, so the next
        login gives the browser new credentials (accounts.py)."""
        old, new = self._state / "player.json", self._save_path(OFFLINE_USER_ID)
        if old.is_file() and not new.exists() and not self.accounts.exists(OFFLINE_USER_ID):
            new.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old, new)
            self.accounts.create(int(self.clock()), user_id=OFFLINE_USER_ID, password=OFFLINE_PASSWORD,
                                 syncode=OFFLINE_SYNCODE, rotate=True)
            self.migrated = True

    def player_ids(self) -> list[int]:
        return [OFFLINE_USER_ID] if self.single_player else self.accounts.ids()

    def _authenticate(self, params: dict) -> int | None:
        """The player a request is from, if its user_id and password match an account."""
        uid = str(params.get("user_id") or "")
        if not uid.isdigit() or not self.accounts.check(uid, params.get("password")):
            return None
        now = int(self.clock())
        acc = self.accounts.get(uid)
        if now - int(acc.get("last_seen", 0)) >= 60:               # the guild screen's activity light
            self.accounts.seen(uid, now)
        return int(uid)

    def _login(self, params: dict) -> tuple[dict, str]:
        """getUserAccount, multi-player: a returning browser's account, or a new one for a
        browser with none (or with credentials this server doesn't know). A reply with
        new_user makes the client keep the credentials it carries."""
        now = int(self.clock())
        uid = self._authenticate(params)
        new = None
        if uid is None:
            uid, password, syncode = self.accounts.create(now)
            new, how = (uid, password, syncode), f"new player {uid}"
            had = str(params.get("user_id") or "")
            if had:
                how += f" (the browser's login for {had} isn't this server's)"
        elif self.accounts.needs_rotation(uid):
            password, syncode = self.accounts.rotate_credentials(uid)
            new, how = (uid, password, syncode), f"player {uid} logged in; new credentials replace the public ones"
        else:
            how = f"player {uid} logged in"
        self.use_player(uid)
        self._reset_session()
        if new is not None and self.player_store is not None and self.player_store.load() is None:
            player = self.player()                           # the new player's save, from the starting data
            player.name = f"Player{uid}"
            self.player_store.save(player)
        body = self._prepare(self.fixtures["getUserAccount"]["body"], params)
        for k in ("new_user", "new_password", "new_name"):
            body.pop(k, None)
        body["new_syncode"] = self.accounts.get(uid)["syncode"]
        if new is not None:
            body["new_user"], body["new_password"], body["new_syncode"] = new
            body["new_name"] = (self.player() or Player(0, 0, 0)).name if self.player_store else ""
        return body, how

    def _login_value(self, key: str):
        """A top-level value from the recorded login (e.g. which data files the game loads)."""
        return next((self.fixtures[n]["body"].get(key) for n in LOGIN
                     if n in self.fixtures and self.fixtures[n]["body"].get(key)), None)

    def _index(self, calls: list[dict]) -> None:
        open_battle = None
        for c in calls:
            message, body = c["message"], c["body"]
            if message in BATTLE_STARTS:
                key = request_key(message, c["key"])
                open_battle = {"name": battle_name(message, c["key"]), "start": body, "plays": []}
                self.scripts.setdefault(key, []).append(open_battle)
                if battle_over(body):
                    open_battle = None
            elif message == BATTLE_TURN:
                if open_battle is not None:
                    open_battle["plays"].append((request_key(message, c["key"]), body))
                    if battle_over(body):
                        open_battle = None
            elif message not in NOT_REPLAYED:
                self.replays.setdefault(request_key(message, c["key"]), []).append(body)

    def _reset_session(self) -> None:
        """A new login starts the player's session (and the recorded story) over."""
        self._sessions[str(self.uid)] = Session()

    @property
    def missing_fixtures(self) -> list[str]:
        return [n for n in LOGIN if n not in self.fixtures]

    def player(self) -> Player | None:
        """The saved player (created on first use), energy refilled to now."""
        if self.player_store is None:
            return None
        return self.player_store.current(self._start, self.energy_every, self._start_source, self.stamina_every)

    def settings(self) -> Settings:
        """The server's settings now (re-read when the file changes)."""
        return self.settings_store.load()

    def content(self) -> Content:
        """What's on offer (content.toml, re-read when it changes)."""
        return self.content_store.load()

    def _boxes(self) -> dict:
        if getattr(self, "_box_data", None) is None:
            self._box_data = boxes.load_boxes(self._assets)
        return self._box_data

    def _rarity_of(self, cid: int) -> int:
        return self._engine_data()[0][cid].rarity

    def _box_problem(self, bid: int, box) -> str | None:
        """Why a box in the content file can't be sold, or None."""
        if box is None:
            return f"boxes.on_offer: there's no box {bid} in event_box.xml"
        cards = self._engine_data()[0]
        every = [cid for t in (box.tiers or (box.cards,)) for cid, _ in t]
        missing = [cid for cid in every if cid not in cards.cards]
        if missing or not every:
            return f"boxes.on_offer: the {box.name} ({bid}) has cards the card files don't: {missing[:5]}"
        return None

    def _store(self, player: Player | None = None, from_save: bool = True) \
            -> tuple[Content, list[dict], list[dict], list[str]]:
        """The store as the content file sets it: (content, mini pack promo entries, event
        box entries, problems: what in the file can't be offered, and why). The boxes'
        progress is `player`'s, or the save's (from_save) or none."""
        c = self.content()
        problems = list(c.problems)
        mini = self._mini_packs()
        promos = []
        for promo in c.promos:
            mp = mini.get(str(promo.mini_pack))
            if mp is None or packs.mini_pack(mp) is None:
                problems.append(f"store.promos: mini pack {promo.mini_pack} " + (
                    "doesn't exist" if mp is None else "can't be opened offline yet (only base-set packs can)"))
                continue
            preview = packs.preview_cards(self._engine_data()[0], mp)
            promos.append(store_content.promo_entry(promo.mini_pack, mp, promo, preview))
        if player is None and from_save:
            player = self.player()
        now = int(self.clock())
        box_entries = []
        for bid in c.boxes:
            box = self._boxes().get(bid)
            problem = self._box_problem(bid, box)
            if problem:
                problems.append(problem)
                continue
            state = ((player.boxes if player is not None else {}).get(str(bid))) or boxes.new_state()
            if boxes.sold_out(box, state):
                continue                                  # bought out for good: it leaves the store
            box_entries.append(store_content.tiered_box_entry(box, state, now, boxes.tier(box, state))
                               if box.tiered else
                               store_content.box_entry(box, state, now, boxes.next_deal(box, state)))
        return c, promos, box_entries, problems

    def store_report(self) -> str:
        """One line on what the store offers, then any problems in the content file."""
        saved = self.player_store.load() if self.player_store is not None else None
        c, promos, box_entries, problems = self._store(saved, from_save=False)
        packs_line = ", ".join(c.packs) or "none"
        line = (f"Store ({self.content_store.path.name}): packs {packs_line}; "
                f"{len(promos)} promo{'s' * (len(promos) != 1)}; boxes: "
                + (", ".join(f"{e['name']} ({e['item_id']})" for e in box_entries) or "none"))
        return "\n".join([line] + [f"  {self.content_store.path.name}: {p}" for p in problems])

    def guild_report(self) -> str:
        """One line on the server's guilds, donations and the raid set."""
        c, n = self.content(), len(self.guild_store.all())
        raid = self._set_raid()
        return (f"Guilds (state/{self.guild_store.root.name}/): {n or 'none yet'}"
                + ("" if not n else f" guild{'s' * (n != 1)}") + "; donations "
                + (f"on ({c.gold_per_point} gold a guild point)" if c.guild_donations else "off")
                + (f"; raid: {raid.name} ({raid.id}), at {c.raid_health_percent:g}% of its health" if raid
                   else "; raid: none"))

    def startup_report(self) -> str | None:
        """Lines for the server's startup: the save, the energy that came back while the
        server was off (counted from the save's sync point to now), the gold bonuses, the
        store, and the guilds."""
        if self.player_store is None:
            return None
        bonuses = ("\nGold bonuses: " + self.settings().describe() + "\n" + self.store_report()
                   + "\n" + self.guild_report())
        if not self.single_player:
            ids = self.player_ids()
            line = (f"Players: {len(ids) or 'none yet'}" + ("" if not ids else f" ({ids[0]}-{ids[-1]})")
                    + "; each browser has its own account (state/accounts.json) and save (state/players/)")
            if self.migrated:
                line += ("\n  The single-player save (state/player.json) is now player 1000001's; that browser "
                         "gets new login credentials at its next login")
            return line + bonuses
        saved = self.player_store.load()
        if saved is None:
            return "Player save: none yet; the first login creates it from the starting data." + bonuses
        before, since = saved.energy, saved.energy_updated
        p = self.player()                                  # refills to now and saves
        line = f"Player save: {p.gold} gold, {p.war_bonds} war bonds, energy {p.energy}/{p.max_energy}"
        gained = p.energy - before
        if gained > 0:
            minutes = max(0, int(self.clock()) - since) // 60
            away = f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"
            line += f" (+{gained} regenerated in the {away} since the last update)"
        return line + bonuses

    @property
    def recorded_battles(self) -> dict[str, list[int]]:
        """Battle name -> number of card plays in each recording of it."""
        return {scripts[0]["name"]: [len(s["plays"]) for s in scripts] for scripts in self.scripts.values()}

    # ---- answering ------------------------------------------------------------------

    def respond(self, message: str, params: dict) -> tuple[dict, str]:
        """(reply, how it was answered). Unsupported requests get the error reply."""
        with self._lock:
            try:
                return self._dispatch(message, params)
            except NotSupported as e:
                return self.unsupported(message, params, e.reason), f"UNSUPPORTED: {e.reason}"

    def handle(self, message: str, params: dict) -> dict | None:
        """The reply, or None if the message isn't supported (yet)."""
        with self._lock:
            try:
                return self._dispatch(message, params)[0]
            except NotSupported:
                return None

    def _dispatch(self, message: str, params: dict) -> tuple[dict, str]:
        """Answers a message; replies after something that moved the achievements also
        carry the list (user_achievements) and any just finished (completed_achievements),
        as recorded."""
        if not self.single_player:
            if message == "getUserAccount" and "getUserAccount" in self.fixtures:
                return self._login(params)
            uid = self._authenticate(params)
            if uid is None:
                return self._refuse(params, NOT_LOGGED_IN), f"refused {message}: not logged in"
            self.use_player(uid)
        self._ach_news, self._ach_changed, self._ach_ready = [], False, []
        reply, how = self._dispatch_message(message, params)
        if self._ach_ready:
            how += "; objective finished (tap Complete): " + ", ".join(self._ach_ready)
        if self._ach_changed and reply.get("result") is not False and self.player() is not None:
            reply["user_achievements"] = achievements.user_achievements(self._achievement_defs(), self.player())
            if self._ach_news:
                reply["completed_achievements"] = achievements.completed_json(self._ach_news)
                how += "; achievement: " + ", ".join(f"{a.name} ({a.desc})" for a in self._ach_news)
        return reply, how

    def _dispatch_message(self, message: str, params: dict) -> tuple[dict, str]:
        if message in self.fixtures:
            if message == "getUserAccount":
                self._reset_session()
            return self._prepare(self.fixtures[message]["body"], params), "answered (recorded login)"
        if message == "recordEvent":
            return self.envelope(params), "answered (analytics; the real server sends an empty reply too)"
        if message == SET_FLAG:
            return self._set_flag(params)
        if message == ENERGY_REFILL:
            return self._buy_energy_refill(params)
        if message == SET_DECK:
            return self._set_deck(params)
        if message == FORFEIT and self.battle is None and self._raid_replay(params) is not None:
            return self._raid_replay(params), "forfeit during a raid fight's replay: its result again"
        if message == FORFEIT:
            return self._forfeit(params)
        if message == STAMINA_REFILL:
            return self._buy_stamina_refill(params)
        if message == UNLOCK:
            return self._unlock_commander(params)
        if message == REPORTS and self.player() is not None:
            return self._battle_reports(params)
        if message in (PACK_GOLD, PACK_WB):
            return self._buy_pack(message, params)
        if message in (UPGRADE, SALVAGE, LOCK, FUSE) or message in SALVAGE_ALL:
            return self._upgrade_screen(message, params)
        if message == CLAIM and self.player() is not None:
            return self._claim_achievement(params)
        if self.player() is not None:
            if message in GUILD_MESSAGES:
                return self._guild_message(message, params)
            if message in RAID_MESSAGES:
                return self._raid_message(message, params)
            if message == CRATE:
                return self._open_crate(params)
            if message == BUY_UPGRADE or (message in (PROMO_GOLD, PROMO_WB) and str(params.get("item_type")) == "4"):
                return self._buy_store_upgrade(message, params)
            if message == SET_NAME:
                return self._set_name(params)
            if message in (SET_ACTIVE, SET_DEFENSE):
                return self._choose_deck(message, params)
            if message in REFRESH:
                return self._refresh(message, params)
        if message == PROMO_WB and str(params.get("item_type")) in (str(store_content.BOX_ITEM_TYPE),
                                                                    str(store_content.TIERED_ITEM_TYPE)) \
                and self.player() is not None:
            return self._buy_box_deal(params) if params.get("box_discount_id") not in (None, "")                 else self._buy_box(params)
        if message in (PROMO_GOLD, PROMO_WB) and self.player() is not None:
            pack = packs.promo_pack({"store_promos": self._store()[1]}, self._mini_packs(), params.get("item_id"))
            if pack is not None and str(params.get("item_type")) == "3":
                currency = "gold" if message == PROMO_GOLD else "war bonds"
                price = pack.gold if message == PROMO_GOLD else pack.war_bonds
                if price is None:
                    return self._refuse(params, f"The {pack.name} isn't sold for {currency}."), \
                        f"refused {message}: no {currency} price"
                return self._sell_pack(params, pack, price, currency, PROMO_GOLD, with_data=True)
            # other promos: replayed if recorded, as before
        if message == RIVALS_REFRESH and self.battle_engine and self.player() is not None:
            return self._refresh_rivals(params)
        if message == TARGETS and self.battle_engine and self.player() is not None:
            return self._hunting_targets(params)
        if message in BATTLE_STARTS:
            return self._start_battle(message, params)
        if message == BATTLE_TURN:
            return self._play_card(params)
        key = request_key(message, params)
        bodies = self.replays.get(key)
        if bodies:
            i = min(self.cursors[key], len(bodies) - 1)
            self.cursors[key] += 1
            return self._prepare(bodies[i], params), f"replayed (recording {i + 1} of {len(bodies)})"
        if any(m == message for m, _ in self.replays):
            raise NotSupported(f"'{message}' was only recorded with other details, so it can't be replayed here.")
        raise NotSupported(f"'{message}' is not supported yet.")

    def _start_battle(self, message: str, params: dict) -> tuple[dict, str]:
        if self.battle_engine and message == "startMission":
            return self._start_engine_battle(params)
        if self.battle_engine and message == ARENA_START and self.player() is not None:
            return self._start_arena_battle(params)
        key = request_key(message, params)
        name = battle_name(message, params)
        scripts = self.scripts.get(key)
        if not scripts:
            raise NotSupported(f"{name} hasn't been recorded, so it can't be replayed yet.")
        charged = ""
        if message == "startMission":
            refusal, charged = self._charge_mission(params)
            if refusal is not None:
                return refusal
        n = self.battle_cursor[key]
        self.battle_cursor[key] += 1
        script = scripts[n % len(scripts)]
        battle_id = str(int(self.clock() * 1000))
        self.battle = {"id": battle_id, "name": name, "script": script, "step": 0,
                       "mission_id": str(params["mission_id"]) if message == "startMission" else None}
        how = f"replayed start of {name} (recording {n % len(scripts) + 1} of {len(scripts)})" + charged
        ended = None
        if battle_over(script["start"]):
            note, ended = self._battle_finished(script["start"])
            how += note
            self.battle = None
        reply = with_battle_id(self._prepare(script["start"], params), battle_id)
        return self._with_result(reply, ended, params), how

    def _play_card(self, params: dict) -> tuple[dict, str]:
        b = self.battle
        if b is None:
            raise NotSupported("there is no battle in progress.")
        if params.get("battle_id") and str(params["battle_id"]) != b["id"]:
            raise NotSupported("that isn't the battle in progress.")
        if "engine" in b:
            return self._play_engine_card(params)
        plays = b["script"]["plays"]
        if b["step"] >= len(plays):
            raise NotSupported("the recorded battle has no more turns.")
        want, body = plays[b["step"]]
        if request_key(BATTLE_TURN, params) != want:
            w = dict(want[1])
            how = " on auto" if w.get("skip") not in (None, "0") else ""
            raise NotSupported(f"this battle replays a recording, which played card #{w.get('card_uid')}{how} "
                               f"at this point. Other moves need the battle engine.")
        b["step"] += 1
        how = f"replayed {b['name']}, play {b['step']} of {len(plays)}"
        ended = None
        if battle_over(body) or b["step"] >= len(plays):
            note, ended = self._battle_finished(body)    # saved before the reply shows the save
            how += " (battle over)" + note
            self.battle = None
        reply = with_battle_id(self._prepare(body, params), b["id"])
        return self._with_result(reply, ended, params), how

    # ---- the battle engine ------------------------------------------------------------

    def _mission_data(self) -> dict:
        """The missions (enemy decks, energy, rewards), loaded on first use."""
        if self._missions is None:
            self._missions = load_missions(self._assets, self._login_value("mission_file"))
        return self._missions

    def _engine_data(self) -> tuple[CardDB, dict]:
        """All cards and missions, loaded on first use (about a second)."""
        if self._cards is None:
            self._cards = CardDB.load(self._assets)
        return self._cards, self._mission_data()

    def _active_deck_id(self) -> str:
        """The active deck: the save's (setActiveDeck), else user_data.active_deck from the
        starting data."""
        saved = self.player_store.load() if self.player_store is not None else None
        if saved is not None and saved.active_deck and (saved.decks or {}).get(saved.active_deck):
            return saved.active_deck
        decks = self._start.get("user_decks") or {}
        return str((self._start.get("user_data") or {}).get("active_deck") or next(iter(decks), ""))

    def _player_deck(self, cards: CardDB):
        """The player's active deck from the save: (commander, cards)."""
        player = self.player()
        decks = player.decks if player is not None else {
            str(k): deck_from_json(v) for k, v in (self._start.get("user_decks") or {}).items()}
        deck = (decks or {}).get(self._active_deck_id())
        if deck is None:
            raise NotSupported("there is no active deck.")
        ids = [cid for cid, n in deck["cards"].items() for _ in range(n)]
        missing = [i for i in [deck["commander_id"], *ids] if cards.get(i) is None]
        if missing:
            raise NotSupported(f"cards {', '.join(map(str, missing))} of your deck aren't in the card data.")
        return cards[deck["commander_id"]], [cards[i] for i in ids]

    def _start_engine_battle(self, params: dict) -> tuple[dict, str]:
        mid = str(params.get("mission_id"))
        cards, missions = self._engine_data()
        mission = missions.get(mid)
        if mission is None:
            raise NotSupported(f"mission {mid} isn't in the mission data.")
        missing = [i for i in [mission.commander, *mission.deck] if cards.get(i) is None]
        if missing:
            raise NotSupported(f"mission {mid}'s cards {', '.join(map(str, missing))} aren't in the card data.")
        commander, deck = self._player_deck(cards)
        refusal, charged = self._charge_mission(params)
        if refusal is not None:
            return refusal
        rng = self.rng_factory()
        battle = Battle(Setup(commander, shuffled_deck(deck, FIRST_UID[ATTACKER], rng), cards[mission.commander],
                              shuffled_deck([cards[i] for i in mission.deck], FIRST_UID[DEFENDER], rng)),
                        cards=cards)                                   # for Summon
        battle_id = int(self.clock() * 1000)
        session = BattleSession(battle, battle_id, str(self.uid), mid)
        turns = battle.start()
        self.battle = {"id": str(battle_id), "name": f"mission {mid}", "mission_id": mid, "engine": session}
        reply = self.envelope(params)
        reply["result"] = True
        reply["user_data"] = self.current_user_data(params)
        reply["battle_data"] = session.start_data(turns)
        return reply, f"engine: started mission {mid} ({len(deck)} cards against {len(mission.deck)})" + charged

    def _play_engine_card(self, params: dict) -> tuple[dict, str]:
        b = self.battle
        session: BattleSession = b["engine"]
        battle = session.battle
        skip = str(params.get("skip", "0")) not in ("0", "")
        try:
            # skip=1 is the game's Skip button ("Skip animations tapped"): the rest of
            # the battle is played out on auto and sent in one reply.
            turns = battle.play_out(params.get("card_uid")) if skip else battle.play(params.get("card_uid"))
        except (BattleError, ValueError, TypeError) as e:
            raise NotSupported(f"the battle engine can't do that: {e}.") from None
        how = f"engine: {b['name']}, turns {min(turns)}-{max(turns)}" if turns else f"engine: {b['name']}"
        if skip:
            how += " (skipped to the end)"
        rewards, result, won = None, None, False
        if battle.over:
            won = battle.winner == 1
            how += " (you won)" if won else " (you lost)"
            if b.get("rival"):
                result = self._finish_hunt(b["rival"], won, b["id"], battle)
                rewards = result.rewards
            else:
                result = self._finish_mission(b["mission_id"], won)
                rewards = [result.reward if result else {"user_id": str(self.uid), "gold": 0}]
            if result:
                how += "; paid " + result.summary()
            self.battle = None
        if battle.ignored_skills:
            how += "; skills not built yet: " + ", ".join(sorted(battle.ignored_skills))
        reply = self.envelope(params)
        reply["result"] = True
        reply["battle_data"] = session.turn_data(turns, rewards, end_time=int(self.clock()))
        if battle.over:                      # the last reply carries the player's data, as recorded
            reply.update(self._arena_parts(params) if b.get("rival") else self._final_parts(result, won, params))
        return reply, how

    def _forfeit(self, params: dict) -> tuple[dict, str]:
        """forfeitBattle battle_id: the Surrender button. The battle counts as lost (a
        mission pays its loss gold), and the reply is shaped like the last playCard's:
        battle_data with winner 0 and the rewards, plus the player's data."""
        b = self.battle
        if b is None:
            raise NotSupported("there is no battle in progress.")
        if params.get("battle_id") and str(params["battle_id"]) != b["id"]:
            raise NotSupported("that isn't the battle in progress.")
        self.battle = None
        if b.get("rival"):
            result = self._finish_hunt(b["rival"], False, b["id"], b["engine"].battle if "engine" in b else None)
            rewards = result.rewards
        else:
            result = self._finish_mission(b["mission_id"], False) if b.get("mission_id") else None
            rewards = [result.reward if result else {"user_id": str(self.uid), "gold": 0}]
        reply = self.envelope(params)
        reply["result"] = True
        if "engine" in b:
            session: BattleSession = b["engine"]
            session.battle.forfeit()
            reply["battle_data"] = session.turn_data({}, rewards, end_time=int(self.clock()))
        else:
            reply["battle_data"] = {"battle_id": b["id"], "host_id": str(self.uid), "turn": {},
                                    "end_time": int(self.clock()), "winner": 0, "rewards": rewards,
                                    **({"mission_id": b["mission_id"]} if b.get("mission_id") else {})}
        reply.update(self._arena_parts(params) if b.get("rival") else self._final_parts(result, False, params))
        how = f"surrendered {b['name']}"
        return reply, how + (f"; paid {result.summary()}" if result else "")

    # ---- the arena -----------------------------------------------------------------------

    def _arena_info(self) -> dict:
        """arena_battle_info, in the recorded shape. hunts_fought counts every fight."""
        a = self.player().arena
        return {"battles_fought": "0", "battles_rewarded": 1, "hunts_fought": str(a["hunts_fought"]),
                "hunts_rewarded": 1}

    def _arena_parts(self, params: dict) -> dict:
        """What an arena fight's last reply carries besides battle_data (recorded:
        user_data, arena_battle_info, hunting_targets, the limits)."""
        keys = ("user_data", "max_energy", "max_stamina")
        parts = self._prepare({"time": self._start.get("time"),
                               **{k: self._start[k] for k in keys if k in self._start}}, params)
        out = {k: parts[k] for k in keys if k in parts}
        out["arena_battle_info"] = self._arena_info()
        out["hunting_targets"] = arena.targets_json(self.player().arena, self._engine_data()[0], self.settings().arena_gold.effective)
        return out

    def _hunting_targets(self, params: dict) -> tuple[dict, str]:
        """getHuntingTargets: the rivals from the save (arena.py), in the recorded
        getHuntingTargets reply's shape."""
        recorded = next((bodies[0] for (m, _), bodies in self.replays.items() if m == TARGETS), None)
        if recorded is not None:
            reply = self._prepare(recorded, params)
        else:
            reply = self.envelope(params)
            reply["result"] = True
            reply["user_data"] = self.current_user_data(params)
        player = self.player()
        reply["hunting_targets"] = arena.targets_json(player.arena, self._engine_data()[0], self.settings().arena_gold.effective)
        reply["arena_battle_info"] = self._arena_info()
        return reply, f"rivals: {len(reply['hunting_targets'])} (rating {player.arena['rating']})"

    def _start_arena_battle(self, params: dict) -> tuple[dict, str]:
        """startHuntingBattle target_user_id: a real fight against an AI rival, for 1
        stamina."""
        player = self.player()
        tid = str(params.get("target_user_id"))
        rival = next((r for r in arena.rival_list(player.arena, self._engine_data()[0], self.settings().arena_gold.effective) if str(r["user_id"]) == tid), None)
        if rival is None:
            raise NotSupported(f"rival {tid} isn't on your rivals list.")
        cards, _ = self._engine_data()
        commander, deck = self._player_deck(cards)
        a = player.arena
        if a["stamina"] < arena.STAMINA_COST:
            reply = self._refuse(params, f"Not enough stamina: a fight needs {arena.STAMINA_COST}, and you have "
                                         f"{a['stamina']}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused arena fight: stamina {a['stamina']}"
        before = a["stamina"]
        a["stamina"] -= arena.STAMINA_COST
        self.player_store.save(player)
        enemy_commander, enemy_deck = arena.rival_deck(rival, cards)
        rng = self.rng_factory()
        battle = Battle(Setup(commander, shuffled_deck(deck, FIRST_UID[ATTACKER], rng), enemy_commander,
                              shuffled_deck(enemy_deck, FIRST_UID[DEFENDER], rng)), cards=cards)
        battle_id = int(self.clock() * 1000)
        session = BattleSession(battle, battle_id, str(self.uid),
                                extra={**ARENA_FIELDS, "enemy_id": tid, "enemy_name": rival["name"]})
        turns = battle.start()
        self.battle = {"id": str(battle_id), "name": f"arena {tid} ({rival['name']})", "mission_id": None,
                       "engine": session, "rival": rival}
        reply = self.envelope(params)
        reply["result"] = True
        reply["user_data"] = self.current_user_data(params)
        reply["battle_data"] = session.start_data(turns)
        reply["hunting_targets"] = arena.targets_json(player.arena, self._engine_data()[0], self.settings().arena_gold.effective)
        return reply, (f"engine: started arena fight with {rival['name']} ({tid}, rated {rival['hunting_elo']}): "
                       f"{len(deck)} cards against {len(enemy_deck)}; stamina {before} -> {a['stamina']}")

    def _battle_reports(self, params: dict) -> tuple[dict, str]:
        """getBattleReports (no parameters): the player's recent arena fights, newest
        first, as recorded (only `battle_reports`, no `result`). Nobody attacks the
        offline player, so there are no defence reports and nothing to take revenge on."""
        reply = self.envelope(params)
        reply["battle_reports"] = arena.reports_json(self.player().arena, self.uid)
        return reply, f"battle reports: {len(reply['battle_reports'])}"

    def _finish_hunt(self, rival: dict, won: bool, battle_id=None, battle=None) -> arena.HuntResult:
        player = self.player()
        result = arena.finish_hunt(player, rival, won, self.reward_rules.loss_mult, int(self.clock()),
                                   self.uid, battle_id)
        if battle is not None:                   # the daily objectives count what happened
            achievements.ensure_dailies(self._achievement_defs(), player, int(self.clock()), self.rng_factory())
            ready = achievements.record_fight(self._achievement_defs(), player,
                                              achievements.fight_stats(battle, won), int(self.clock()))
            self._ach_changed = True
            if ready:
                self._ach_ready = [a.name for a in ready]
        self._track(player)                      # the rating
        self.player_store.save(player)
        return result

    def _finish_mission(self, mission_id, won: bool) -> MissionResult | None:
        """Pays a finished mission into the save (rewards.py). None without a save or
        without the mission in the mission data."""
        player = self.player()
        mission = self._mission_data().get(str(mission_id))
        if player is None:
            return None
        if mission is None:                 # only a replay can get here: the win counts, nothing to pay
            if won:
                player.won_mission(str(mission_id), ["1"])
                self._track(player)
                self.player_store.save(player)
            return None
        rules = dataclasses.replace(self.reward_rules, gold_multiplier=self.settings().mission_gold.effective)
        result = finish_mission(player, mission, won, rules, int(self.clock()), self.uid,
                                self._active_deck_id())
        self._track(player)                 # "Complete Mission #5"...
        self.player_store.save(player)
        return result

    def _final_parts(self, result: MissionResult | None, won: bool, params: dict) -> dict:
        """The player's data in a mission's last reply, as recorded: user_data always,
        user_cards when a card was won, user_decks when it went into the deck,
        mission_completions for a win, and current_campaigns."""
        keys = ["user_data"]
        if result is not None and result.cards:
            keys.append("user_cards")
        if result is not None and result.deck_changed:
            keys.append("user_decks")
        if won:
            keys.append("mission_completions")
        keys.append("current_campaigns")
        parts = self._prepare({"time": self._start.get("time"),
                               **{k: self._start[k] for k in keys if k in self._start}}, params)
        return {k: parts[k] for k in keys if k in parts}

    def _with_result(self, reply: dict, result: MissionResult | None, params: dict) -> dict:
        """A replayed mission's last reply with our rewards in place of the recorded ones
        (they follow the save: a recorded first win may be a repeat here)."""
        bd = reply.get("battle_data")
        if result is None or not isinstance(bd, dict):
            return reply
        others = [r for r in bd.get("rewards") or [] if isinstance(r, dict)
                  and str(r.get("user_id")) != str(self.uid)]
        bd["rewards"] = [result.reward, *others]
        for k in ("user_cards", "user_decks", "mission_completions",
                  "new_cards_data", "completed_achievements", "user_achievements"):   # achievements aren't kept
            reply.pop(k, None)
        for k, v in self._final_parts(result, battle_won(reply), params).items():
            reply.setdefault(k, v)          # a recorded user_data stays, with the save's values already in
        return reply

    def _charge_mission(self, params: dict) -> tuple[tuple[dict, str] | None, str]:
        """Takes the mission's energy cost from the save: (refusal, None) if there isn't
        enough, else (None, a note for the log).

        The energy has already been refilled up to now. Below the maximum, the sync point
        stays put, so the part-way progress towards the next point is kept; from full, the
        refill has just set it to now. Both are what the recorded starts show.
        """
        mid = str(params.get("mission_id"))
        player = self.player()
        cost = self.mission_costs.get(mid)
        if player is None:
            return None, ""
        if cost is None:
            return None, "; no energy cost in the mission data" if self.mission_costs else ""
        if player.energy < cost:
            # The game checks its own copy first and only asks when it thinks there's
            # enough, so this means the two disagree. The refusal carries the real
            # user_data, which the game adopts (tested offline); the wording is ours.
            reply = self._refuse(params, f"Not enough energy: this mission needs {cost}, and you have "
                                         f"{player.energy}.")
            reply["user_data"] = self.current_user_data(params)
            return (reply, f"refused mission {mid}: needs {cost} energy, has {player.energy}"), ""
        before = player.energy
        player.energy -= cost
        self.player_store.save(player)
        return None, f"; {cost} energy ({before} -> {player.energy})"

    # ---- packs --------------------------------------------------------------------------------

    def _mini_packs(self) -> dict:
        if getattr(self, "_mini_pack_data", None) is None:
            self._mini_pack_data = packs.load_mini_packs(self._assets)
        return self._mini_pack_data

    def _buy_pack(self, message: str, params: dict) -> tuple[dict, str]:
        """buyPackGold / buyPackTokens pack_id: a `store_packs` pack (packs.py)."""
        if self.player() is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep cards in.")
        if str(params.get("pack_id")) not in {store_content.PACK_IDS[p] for p in self.content().packs}:
            return self._refuse(params, "That pack isn't in the store."), \
                f"refused {message}: pack {params.get('pack_id')} isn't in {self.content_store.path.name}"
        pack = packs.store_pack(self._start, params.get("pack_id"))
        if pack is None:
            raise NotSupported(f"pack {params.get('pack_id')} can't be opened offline yet.")
        currency = "gold" if message == PACK_GOLD else "war bonds"
        price = pack.gold if message == PACK_GOLD else pack.war_bonds
        if price is None:
            return self._refuse(params, f"The {pack.name} isn't sold for {currency}."), \
                f"refused {message}: no {currency} price"
        return self._sell_pack(params, pack, price, currency, PACK_GOLD)

    def _sell_pack(self, params: dict, pack, price: int, currency: str, template: str,
                   with_data: bool = False, open_cards=None, n: int | None = None) -> tuple[dict, str]:
        """Takes the price, opens the pack into the inventory (and, during the tutorial,
        the deck), and answers in the recorded reply's shape: the recorded reply for
        `template` with the save's data, plus new_cards (and new_cards_data for promos).
        open_cards(player) -> card ids replaces opening `pack` (an event box: then `pack`
        only needs a name); n is how many cards it gives."""
        player = self.player()
        have = player.gold if currency == "gold" else player.war_bonds
        problem = None
        expected = params.get("expected_cost")
        cap = player.max_cards or int(((self._start.get("user_data") or {}).get("caps") or {}).get("max_cards") or 1600)
        n = sum(count for _, count in pack.draws) if n is None else n
        if expected not in (None, "") and str(expected).isdigit() and int(expected) != price:
            problem = f"The price has changed: the {pack.name} costs {price} {currency} now."
        elif have < price:
            problem = f"Not enough {currency}: the {pack.name} costs {price}, and you have {have}."
        elif player.card_count + n > cap:
            problem = f"Your inventory is full: {player.card_count} of {cap} cards."
        cards, _ = self._engine_data()
        if not problem and open_cards is not None:
            try:
                new = open_cards(player)
            except boxes.BoxError as e:
                problem = str(e)
        elif not problem:
            new = packs.open_pack(pack, cards, self.rng_factory())
        if problem:
            reply = self._refuse(params, problem)
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused {pack.name}: {problem}"
        if currency == "gold":
            player.gold -= price
        else:
            player.war_bonds -= price
        for cid in new:
            player.add_card(cid)
        into_deck = []
        deck = (player.decks or {}).get(self._active_deck_id())
        if self._in_tutorial(player) and deck is not None:
            for cid in new:
                if sum(deck["cards"].values()) < DECK_SIZE:
                    deck["cards"][str(cid)] = deck["cards"].get(str(cid), 0) + 1
                    into_deck.append(cid)
        self.player_store.save(player)
        recorded = [b for (m, _), bodies in self.replays.items() if m == template for b in bodies]
        plain = [b for b in recorded if "user_decks" not in b] or recorded
        if plain:
            reply = self._prepare(plain[-1], params)
        else:
            reply = self.envelope(params)
            reply["result"] = True
            reply.update({k: v for k, v in self._prepare({"time": self._start.get("time"), **{
                k: self._start[k] for k in ("user_data", "user_cards") if k in self._start}}, params).items()
                if k in ("user_data", "user_cards")})
        reply["result"] = True
        reply["new_cards"] = [str(c) for c in new]
        if with_data:
            reply["new_cards_data"] = packs.new_cards_data(new)
        else:
            reply.pop("new_cards_data", None)
        if into_deck:
            reply["user_decks"] = decks_json(player.decks)
        names = ", ".join(f"{cards[c].name} ({'?CRELVM'[cards[c].rarity] if 0 < cards[c].rarity < 7 else '?'})"
                          for c in new)
        return reply, (f"bought {pack.name} for {price} {currency} ({have} -> {have - price}): {names}"
                       + ("; into the deck (tutorial)" if into_deck else ""))

    def _offered_box(self, params: dict):
        """(box, None) for a box the store has now, or (None, the refusal)."""
        try:
            bid = int(params.get("item_id"))
        except (TypeError, ValueError):
            bid = None
        box = self._boxes().get(bid) if bid is not None else None
        if box is None or bid not in self.content().boxes or self._box_problem(bid, box):
            return None, (self._refuse(params, "That box isn't in the store."),
                          f"refused: box {params.get('item_id')} isn't on offer ({self.content_store.path.name})")
        return box, None

    def _buy_box(self, params: dict) -> tuple[dict, str]:
        """buyStorePromoTokens item_type=8 (10 for a tiered box) item_id=<box> expected_cost:
        one card from an event box (its open tier), for its war bond price (boxes.py)."""
        box, refusal = self._offered_box(params)
        if refusal:
            return refusal

        def pull(player):
            state = player.boxes.setdefault(str(box.id), boxes.new_state())
            return [boxes.pull(box, state, self._rarity_of, self.rng_factory())]

        reply, how = self._sell_pack(params, packs.Pack(box.name, ()), box.wb_cost, "war bonds", PROMO_GOLD,
                                     with_data=True, open_cards=pull, n=1)
        return reply, how + self._box_progress(box)

    def _buy_box_deal(self, params: dict) -> tuple[dict, str]:
        """buyStorePromoTokens item_type=8 item_id=<box> box_discount_id=<deal>: the box's
        deal, several cards at a discount, once per round of the box (boxes.py). Found
        offline: the deal's Buy sends this, with expected_cost the box's price (60), not
        the deal's, so either is accepted."""
        box, refusal = self._offered_box(params)
        if refusal:
            return refusal
        raw = params.get("box_discount_id")
        deal = next((d for d in box.deals if str(d.id) == str(raw)), None)
        if deal is None:
            return self._refuse(params, "That deal isn't on offer."), f"refused {PROMO_WB}: no deal {raw!r}"
        player = self.player()
        state = player.boxes.get(str(box.id)) or boxes.new_state()
        offered = boxes.next_deal(box, state)
        if offered is None or offered.id != deal.id:
            return self._refuse(params, f"The {deal.name} can be bought once per box."), \
                f"refused {PROMO_WB}: {deal.name} isn't the deal on offer"

        def pull(player):
            state = player.boxes.setdefault(str(box.id), boxes.new_state())
            return boxes.buy_deal(box, deal, state, self._rarity_of, self.rng_factory())

        if str(params.get("expected_cost")) == str(box.wb_cost):
            params = {**params, "expected_cost": str(deal.wb_cost)}      # the game sends the box's price
        reply, how = self._sell_pack(params, packs.Pack(f"{box.name}: {deal.name}", ()), deal.wb_cost, "war bonds",
                                     PROMO_GOLD, with_data=True, open_cards=pull, n=len(deal.min_rarities))
        return reply, how + self._box_progress(box)

    def _box_progress(self, box) -> str:
        player = self.player()
        state = (player.boxes if player is not None else {}).get(str(box.id)) or boxes.new_state()
        limit = f" of {box.max_completes}" if box.max_completes is not None else ""
        where = f", tier {boxes.tier(box, state) + 1} of {len(box.tiers)}" if box.tiered else ""
        return (f"; {box.name} round {int(state.get('completes', 0)) + 1}{limit}{where}: "
                f"{boxes.bought(box, state)}/{box.size} bought")

    # ---- guilds -------------------------------------------------------------------------------------

    def _player_of(self, user_id) -> Player | None:
        """Any player's save: the current player's brought up to now, another's as last
        saved; None for an id this server doesn't know."""
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None
        if uid == self.uid:
            return self.player()
        if self.single_player or not self.accounts.exists(uid):
            return None
        store = self._store_for(uid)
        return store.load() if store is not None else None

    def _deck_of(self, player: Player) -> dict:
        """A player's active deck ({"commander_id", "cards"})."""
        decks = player.decks or {}
        if player.active_deck and decks.get(player.active_deck):
            return decks[player.active_deck]
        start = self._start.get("user_decks") or {}
        default = str((self._start.get("user_data") or {}).get("active_deck") or next(iter(start), ""))
        return decks.get(default) or {}

    def _last_active(self, user_id) -> int:
        if int(user_id) == self.uid or self.single_player:
            return int(self.clock())
        return int((self.accounts.get(user_id) or {}).get("last_seen", 0))

    def _guild_info(self, user_id) -> dict | None:
        """A player's public details for guild screens: name, level, rating, commander and
        when they were last active; None for a player this server doesn't know."""
        player = self._player_of(user_id)
        if player is None:
            return None
        arena = player.arena or {}
        return {"name": player.name or OFFLINE_NAME, "level": int(arena.get("level", 1)),
                "rating": int(arena.get("rating", 0)),
                "commander_id": int(self._deck_of(player).get("commander_id") or 1000),
                "last_update_time": self._last_active(user_id)}

    def _name_of(self, user_id) -> str:
        return (self._guild_info(user_id) or guilds.UNKNOWN)["name"]

    def _my_guild(self) -> dict | None:
        return self.guild_store.of_member(self.uid)

    def _guild_parts(self, guild, params: dict | None = None) -> dict:
        """What every guild reply carries: the player's guild (`faction`; {"faction_id": 0}
        without one, and then the invitations), its fortress, and, given the request, the
        news and chat after its last_activity_id."""
        if guild is None:
            invites = guilds.invites_for(self.guild_store, self.uid)
            return {"faction": dict(guilds.NO_GUILD), "fortress_slots": [],
                    "faction_invites": [guilds.list_json(g, self._guild_info) for g in invites]}
        out = {"faction": guilds.faction_json(guild, self._guild_info, self.content().gold_per_point),
               "fortress_slots": guilds.fortress_json(guild)}
        if params is not None:
            out["faction_news"] = guilds.news_json(guilds.news_since(guild, params.get("last_activity_id")))
        return out

    def _guild_reply(self, params: dict, guild, message: str | None = None, ok: bool = True) -> dict:
        """A guild reply. Any `result_message` shows in a "Guild" dialog, success or not.
        While a raid is set it also carries the raid, so the Wars tab shows it as soon as
        the player creates or joins a guild (and stops showing it when they leave)."""
        reply = self.envelope(params)
        reply["result"] = ok
        if message:
            reply["result_message"] = [message]
        reply.update(self._guild_parts(guild, params))
        if self._set_raid() is not None:
            state = self._raid_state(guild)
            reply["current_raids"] = raids.current_raids(state[0], state[1], self._mini_packs()) if state else []
            reply["raid_info"] = self._raid_info(state) if state else None
        return reply

    def _guild_rows(self, rows: list[dict], ranked: bool = False) -> list[dict]:
        rows = sorted(rows, key=lambda g: (-guilds.total_rating(g, self._guild_info), g["id"]))
        return [guilds.list_json(g, self._guild_info, i if ranked else 0) for i, g in enumerate(rows, 1)]

    def _guild_message(self, message: str, params: dict) -> tuple[dict, str]:
        """The guild calls (guilds.py): creating, joining and leaving, invitations, ranks,
        settings, chat, the guild lists, donations and the fortress."""
        store, uid, now = self.guild_store, str(self.uid), int(self.clock())
        me = self._guild_info(uid) or guilds.UNKNOWN
        guild = self._my_guild()
        try:
            if message == "createFaction":
                guild = guilds.create(store, uid, me["name"], params.get("faction_name"), now)
                return self._guild_reply(params, guild), f"created guild {guild['name']} ({guild['id']})"
            if message in ("joinFaction", "acceptFactionInvite"):
                guild = guilds.join(store, params.get("faction_id"), uid, me["name"], me["level"], now,
                                    invited=message == "acceptFactionInvite")
                return self._guild_reply(params, guild), f"joined guild {guild['name']} ({guild['id']})"
            if message == "getRecruitingFactions":
                rows = [g for g in store.all() if g["recruitment_status"] != guilds.CLOSED
                        and len(g["members"]) < guilds.MAX_MEMBERS and uid not in g["members"]]
                reply = self._guild_reply(params, guild)
                reply["factions"] = self._guild_rows(rows)
                return reply, f"guilds taking members: {len(rows)}"
            if message == "getTopFactions":
                reply = self._guild_reply(params, guild)
                reply["factions"] = self._guild_rows(store.all(), ranked=True)
                return reply, f"top guilds: {len(reply['factions'])}"
            if message == "getProfileData":
                return self._member_profile(params, guild)
            if guild is None:
                if message in ("updateFaction", "getFactionNews", "updateFactionWar"):
                    reply = self._guild_reply(params, None)
                    return reply, f"no guild; {len(reply['faction_invites'])} invitation(s)"
                if message == "getUsersLFG":
                    reply = self._guild_reply(params, None)
                    reply["user_lfg"] = []
                    return reply, "players looking for a guild: you're not in one"
                raise guilds.GuildError("You aren't in a guild.")
            if message == "leaveFaction":
                left, successor = guilds.leave(store, uid, me["name"], now, self._name_of)
                how = f"left guild {left['name']}" + ("; it had no one left, so it's disbanded" if not left["members"]
                                                    else f"; {self._name_of(successor)} leads it now" if successor else "")
                return self._guild_reply(params, None), how
            if message in ("updateFaction", "getFactionNews", "updateFactionWar"):
                self._raid_state(guild)                         # a lost raid level shows in the news
                guild = self._my_guild()
                reply = self._guild_reply(params, guild)
                return reply, (f"guild {guild['name']}: {len(guild['members'])} member(s), "
                               f"{guild['guild_points']} guild points, {len(reply['faction_news'])} news line(s)")
            if message == "getUsersLFG":
                wanted = str(params.get("name") or "").lower()
                looking = [str(u) for u in self.player_ids()                    # players without a guild
                           if str(u) != uid and store.of_member(u) is None]
                rows = [{"faction_id": "0", "user_id": u, **(self._guild_info(u) or guilds.UNKNOWN), "gp_earned": 0,
                         "member_role": 0} for u in looking if wanted in self._name_of(u).lower()]
                reply = self._guild_reply(params, guild)
                reply["user_lfg"] = rows
                return reply, f"players looking for a guild: {len(rows)}"
            if message == "sendFactionInvite":
                target = str(params.get("target_user_id"))
                if self._guild_info(target) is None:
                    raise guilds.GuildError("There's no such player on this server.")
                guilds.invite(guild, uid, target, store.of_member(target) is not None, now)
                store.save(guild)
                return self._guild_reply(params, guild, f"{self._name_of(target)} has been invited."), \
                    f"invited {self._name_of(target)} to {guild['name']}"
            if message == "kickFactionMember":
                target = str(params.get("target_user_id"))
                guilds.kick(guild, uid, me["name"], target, self._name_of(target), now)
                store.save(guild)
                return self._guild_reply(params, guild), f"removed {self._name_of(target)} from {guild['name']}"
            if message == "setMemberRank":
                target = str(params.get("target_user_id"))
                try:
                    rank = int(params.get("rank"))
                except (TypeError, ValueError):
                    raise guilds.GuildError("That rank change isn't possible.")
                what = guilds.set_rank(guild, uid, target, rank, self._name_of(target), now)
                store.save(guild)
                return self._guild_reply(params, guild), f"{self._name_of(target)} {what}"
            if message == "setAllFactionSettings":
                guilds.settings(guild, uid, params.get("text"), params.get("recruitment_status"), params.get("min_level"))
                store.save(guild)
                return self._guild_reply(params, guild), (
                    f"guild settings: recruitment {guilds.RECRUITMENT[guild['recruitment_status']]}, "
                    f"minimum level {guild['min_level']}")
            if message == "sendFactionMessage":
                entry = guilds.chat(guild, uid, me["name"], params.get("chat"), now)
                store.save(guild)
                return self._guild_reply(params, guild), f"guild chat: {me['name']}: {entry['data']['message']}"
            if message == "donateFactionGold":
                return self._donate(params, guild, me)
            if message in ("buyFactionCard", "removeFactionCard"):
                return self._fortress(message, params, guild, me)
        except guilds.GuildError as e:
            return self._guild_reply(params, self._my_guild(), str(e), ok=False), f"refused {message}: {e}"
        raise NotSupported(f"'{message}' is not supported yet.")

    def _member_profile(self, params: dict, guild) -> tuple[dict, str]:
        """getProfileData target_user_id: a guild member's profile (an empty user_id tells the
        client they must share a guild)."""
        target = str(params.get("target_user_id"))
        info = self._guild_info(target)
        reply = self.envelope(params)
        reply["result"] = True
        if info is None or guild is None or target not in guild["members"]:
            reply["player_info"] = {"user_id": ""}
            return reply, f"profile of {target}: not in your guild"
        player = self._player_of(target)
        deck = self._deck_of(player)
        defense = (player.decks or {}).get(player.defense_deck or "") or deck
        as_json = lambda d: {"commander_id": d.get("commander_id", 0),                      # noqa: E731
                             "cards": [c for c, n in (d.get("cards") or {}).items() for _ in range(n)]}
        stars = sum(int(bool(m.get("complete"))) + int(bool(m.get("star2"))) + int(bool(m.get("star3")))
                    for m in (player.missions or {}).values())
        reply["player_info"] = {"user_id": target, "name": info["name"], "rating": info["rating"],
                                "level": info["level"], "level_name": level_info(info["level"])[0],
                                "stars_earned": stars,
                                "faction_war_points": -1, "last_update_time": info["last_update_time"],
                                "deck": as_json(deck), "defense_deck": as_json(defense)}
        return reply, f"profile of {info['name']}"

    def _donate(self, params: dict, guild: dict, me: dict) -> tuple[dict, str]:
        """donateFactionGold amount=<gold>: gold_per_point gold a guild point (content.toml)."""
        c = self.content()
        if not c.guild_donations:
            raise guilds.GuildError("Donations are switched off on this server.")
        player = self.player()
        try:
            gold = int(params.get("amount"))
        except (TypeError, ValueError):
            raise guilds.GuildError("How much gold?")
        cost = gold // c.gold_per_point * c.gold_per_point
        if cost > player.gold:
            raise guilds.GuildError(f"Not enough gold: that's {cost}, and you have {player.gold}.")
        points = guilds.donate(guild, self.uid, me["name"], cost, c.gold_per_point, int(self.clock()))
        player.gold -= cost
        self.player_store.save(player)
        self.guild_store.save(guild)
        reply = self._guild_reply(params, guild)
        reply.update(self._player_parts(params, ("user_data",)))
        return reply, f"donated {cost} gold to {guild['name']} for {points} guild points ({guild['guild_points']} now)"

    def _fortress(self, message: str, params: dict, guild: dict, me: dict) -> tuple[dict, str]:
        """buyFactionCard slot_id card_id / removeFactionCard slot_id (guilds.py)."""
        cards, _ = self._engine_data()
        now = int(self.clock())
        if message == "removeFactionCard":
            name = guilds.remove_card(guild, self.uid, me["name"], params.get("slot_id"), cards, now)
            self.guild_store.save(guild)
            return self._guild_reply(params, guild), f"fortress: removed {name} from slot {params.get('slot_id')}"
        card = cards.get(params.get("card_id")) if str(params.get("card_id", "")).isdigit() else None
        what, cost = guilds.buy_card(guild, self.uid, me["name"], params.get("slot_id"), card, cards, now)
        self.guild_store.save(guild)
        return self._guild_reply(params, guild), (
            f"fortress: {what} {card.name} (level {card.level}) in slot {params.get('slot_id')}"
            + (f" for {cost} guild points ({guild['guild_points']} left)" if cost else ""))

    # ---- raids (each guild fights its own run of the raid content.toml sets) ---------------------

    def _raid_defs(self) -> dict:
        if getattr(self, "_raids", None) is None:
            self._raids = raids.load(self._assets)
        return self._raids

    def _bge_defs(self) -> dict:
        if getattr(self, "_bges", None) is None:
            self._bges = bge.load(self._assets)
        return self._bges

    def _set_raid(self):
        c = self.content()
        return self._raid_defs().get(c.raid) if c.raid else None

    def _raid_state(self, guild):
        """(raid, run, health scale) for the guild's run of the raid content.toml sets,
        brought up to now (a new run when there's none or the last one is over), or None
        when no raid is set or there's no guild. Saves the guild if anything changed."""
        raid = self._set_raid()
        if raid is None or guild is None:
            return None
        c = self.content()
        now, scale = int(self.clock()), c.raid_health_percent / 100
        run = guild.get("raid")
        before = json.dumps(run, sort_keys=True)
        if not run or run.get("id") != raid.id or now >= run.get("decay", 0) or "members" not in run:
            run = guild["raid"] = raids.new_run(raid, now, c.raid_days, scale)
        lost = raids.refresh(run, raid, now, scale)
        if lost:
            guilds.add_news(guild, guilds.RAID_LOST, {"raid_level": lost, "raid_name": raid.name}, now)
        for m in run["members"].values():
            raids.refresh_member(run, m, now)
        if json.dumps(run, sort_keys=True) != before or lost:
            self.guild_store.save(guild)
        return raid, run, scale

    def _raid_replay(self, params) -> dict | None:
        """The last raid fight's reply, if `params` name its battle: the client may send
        forfeitBattle while it plays the fight back (the Skip/Surrender button)."""
        reply = getattr(self, "_last_raid_reply", None)
        if reply is None or str(params.get("battle_id")) != str(reply["battle_data"]["battle_id"]):
            return None
        return {**reply, **self.envelope(params), "result": True}

    def _raid_info(self, state) -> list:
        raid, run, scale = state
        names = {uid: self._name_of(uid) for uid in run["members"]}
        names[str(self.uid)] = self._name_of(self.uid)
        return raids.raid_info(raid, run, self.uid, names, int(self.clock()), scale)

    def _raid_message(self, message: str, params: dict) -> tuple[dict, str]:
        raid = self._set_raid()
        if raid is None:
            return self._refuse(params, "There's no raid on."), f"refused {message}: no raid set in content.toml"
        if message == "getRaidLeaderboard":
            reply = self.envelope(params)
            reply["result"] = True
            runs = [g for g in self.guild_store.all() if (g.get("raid") or {}).get("id") == raid.id]
            runs.sort(key=lambda g: (-g["raid"]["level"], g["id"]))
            reply["raid_leaderboard"] = [{"faction_id": str(g["id"]), "name": g["name"], "raid_level": g["raid"]["level"]}
                                         for g in runs]
            return reply, f"raid leaderboard: {len(runs)} guild(s)"
        if message == "getPreviousRaidTopLeaderboard":
            reply = self.envelope(params)
            reply["result"] = True
            reply["previous_raid_top_leaderboard"] = []
            return reply, "previous raid leaderboard: none"
        guild = self._my_guild()
        if guild is None:
            return self._refuse(params, "You need to be in a guild to raid."), f"refused {message}: not in a guild"
        state = self._raid_state(guild)
        raid, run, scale = state
        m = raids.member(run, self.uid, int(self.clock()))
        if message == "fightRaidBattle":
            return self._raid_fight(params, guild, raid, run, m, scale)
        if message == "claimRaidReward":
            return self._raid_claim(params, guild, raid, run, m, scale)
        if message == "refillEventBattleEnergy":
            return self._raid_refill(params, guild, raid, run, m, scale)
        self.guild_store.save(guild)                                   # the member's part, if new
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._player_parts(params, ("user_data",)))
        reply["current_raids"] = raids.current_raids(raid, run, self._mini_packs())
        reply["raid_info"] = self._raid_info(state)
        reply.update(self._guild_parts(guild))
        return reply, (f"{raid.name}: {guild['name']} at level {run['level']}, health {max(0, run['hp'])}/"
                       f"{raid.max_health(run['level'], scale)}, raid energy {m['energy']}/{raids.ENERGY_MAX}, "
                       f"your damage {m['damage']}")

    def _raid_fight(self, params, guild, raid, run, m, scale) -> tuple[dict, str]:
        """fightRaidBattle raid_id raid_level: a whole fight in one reply, played on auto
        (the client shows it as a replay), for one of the player's raid energy. The damage
        goes to the guild's boss."""
        now = int(self.clock())
        try:
            raids.can_fight(run, m, now)
        except raids.RaidError as e:
            return self._refuse(params, str(e)), f"refused fightRaidBattle: {e}"
        cards, _ = self._engine_data()
        commander, deck = self._player_deck(cards)
        rng = self.rng_factory()
        level = run["level"]
        boss, forts, boss_cards = raids.boss_deck(raid, level, cards, rng)
        effects, entries = [], {"global": [], "attacker": [], "defender": []}
        for eid, scope in ((raid.battleground_effect, "global"), (raid.enemy_battleground_effect, "defender")):
            effect = self._bge_defs().get(eid) if eid else None
            if effect is not None:
                effects.append((bge.SCOPE_UID[scope], scope, effect.caster(level)))
                entries[scope].append(effect.entry(scope))
        battle = Battle(Setup(commander, shuffled_deck(deck, FIRST_UID[ATTACKER], rng), boss,
                              shuffled_deck(boss_cards, FIRST_UID[DEFENDER], rng), defender_fortresses=tuple(forts)),
                        chooser=RandomChooser(rng), cards=cards, effects=effects)
        battle.start()
        battle.play_out()
        last_card = FIRST_UID[DEFENDER] + len(boss_cards)
        destroyed = sum(1 for t in battle.turns.values() for uid in t.get("defend_kill") or []
                        if FIRST_UID[DEFENDER] <= int(uid) < last_card)
        bc = battle.sides[DEFENDER].commander
        damage = raids.fight_damage(destroyed, bc.hp, bc.max_hp)
        started, cleared = raids.record_fight(run, m, raid, damage, now, scale)
        name = self._name_of(self.uid)
        if started:
            guilds.add_news(guild, guilds.RAID_STARTED, {"name": name, "raid_level": level, "raid_name": raid.name}, now)
        if cleared:
            guilds.add_news(guild, guilds.RAID_WON, {"raid_level": level, "raid_name": raid.name, "name": name}, now)
        self.guild_store.save(guild)
        battle_id = int(self.clock() * 1000)
        session = BattleSession(battle, battle_id, str(self.uid),
                                extra={"enemy_name": raid.name, "enemy_id": "0", "battleground_effects": entries})
        rewards = [{"user_id": str(self.uid), "gold": 0, "card": [], "item": [], "raid_damage": damage}]
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._player_parts(params, ("user_data",)))
        reply["battle_data"] = session.turn_data(battle.turns, rewards, end_time=now)
        self._last_raid_reply = reply
        won = battle.winner == 1
        how = (f"raid fight vs {raid.name} level {level}: {'won' if won else 'lost'} in {battle.turn} turns, "
               f"{destroyed} cards destroyed, damage {damage}; {guild['name']}'s boss {max(0, run['hp'])}/"
               f"{raid.max_health(run['level'], scale)}" + (f"; level {level} cleared" if cleared else "")
               + f"; raid energy {m['energy']}")
        if battle.ignored_skills:
            how += "; skills not built yet: " + ", ".join(sorted(battle.ignored_skills))
        return reply, how

    def _raid_refill(self, params, guild, raid, run, m, scale) -> tuple[dict, str]:
        """refillEventBattleEnergy event_type=2 event_id: the player's raid energy to full, 5
        war bonds a point."""
        player = self.player()
        missing = raids.ENERGY_MAX - m["energy"]
        cost = missing * raids.REFILL_WB
        if missing <= 0:
            return self._refuse(params, "Your raid energy is already full."), "refused raid refill: full"
        if player.war_bonds < cost:
            reply = self._refuse(params, f"Not enough war bonds: a refill costs {cost}, and you have {player.war_bonds}.")
            return reply, "refused raid refill: war bonds"
        player.war_bonds -= cost
        m["energy"], m["energy_updated"] = raids.ENERGY_MAX, int(self.clock())
        self.player_store.save(player)
        self.guild_store.save(guild)
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._player_parts(params, ("user_data",)))
        reply["raid_info"] = self._raid_info((raid, run, scale))
        return reply, f"raid energy refilled: +{missing} for {cost} war bonds"

    def _raid_claim(self, params, guild, raid, run, m, scale) -> tuple[dict, str]:
        """claimRaidReward raid_id: every reward tier the player's damage and the guild's
        level reached, opened into cards."""
        now = int(self.clock())
        if m["claimed"]:
            return self._refuse(params, "You already claimed this raid's reward."), "refused raid claim: claimed"
        if not raids.claimable(run, m, now):
            return self._refuse(params, "The raid's reward can be claimed once it's over."), "refused raid claim: early"
        player = self.player()
        cards, _ = self._engine_data()
        rng = self.rng_factory()
        tiers = raids.earned_tiers(raid, m["damage"], run["level"])
        got = []
        for tier in tiers:
            for kind, rid, amount in tier.rewards:
                got += self._open_raid_reward(kind, rid, amount, cards, rng)
        for cid in got:
            player.add_card(cid)
        m["claimed"] = 1
        self.player_store.save(player)
        self.guild_store.save(guild)
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._player_parts(params, ("user_data", "user_cards")))
        reply["new_cards"] = [str(c) for c in got]
        reply["raid_info"] = self._raid_info((raid, run, scale))
        names = ", ".join(cards[c].name for c in got[:8]) + (f" and {len(got) - 8} more" if len(got) > 8 else "")
        return reply, f"raid reward: {len(tiers)} tiers, {len(got)} cards ({names or 'none'})"

    def _open_raid_reward(self, kind, rid, amount, cards, rng) -> list[int]:
        if kind == "card":
            return [rid] * amount if cards.get(rid) else []
        if kind == "pack":
            draws = packs.STORE_PACK_DRAWS.get(rid)
            pack = packs.Pack(f"pack {rid}", draws) if draws else None
        else:
            mp = self._mini_packs().get(str(rid))
            pack = packs.reward_pack(mp) if mp else None
            if pack is not None and not all(packs.rates(d, packs.base_pool(cards, pack.faction, pack.set_id))
                                            for d, _ in pack.draws):
                pack = dataclasses.replace(pack, set_id=raids.FALLBACK_REWARD_SET)   # an emptied reward set
        if pack is None:
            return []
        return [c for _ in range(amount) for c in packs.open_pack(pack, cards, rng)]

    # ---- the crate, upgrades, name, decks, refreshes ------------------------------------------------

    def _decks_limit(self) -> int:
        return int(self._start.get("max_decks_available") or 5)

    def _player_parts(self, params: dict, keys) -> dict:
        """The save's user_data / user_cards / user_decks, in the recorded login's shape."""
        start = {"time": self._start.get("time"), "user_data": self._start.get("user_data") or {},
                 "user_cards": {}, "user_decks": {}}
        prepared = self._prepare(start, params)
        return {k: prepared[k] for k in keys if k in prepared}

    def _open_crate(self, params: dict) -> tuple[dict, str]:
        """useDailyBonus (no parameters): the home screen crate (daily_crate.py)."""
        player = self.player()
        c = self.content()
        try:
            reward, streak = daily_crate.open_crate(player.daily_bonus, c.crate_rewards, c.crate_every_hours,
                                                    int(self.clock()))
        except daily_crate.NotReady as e:
            reply = self._refuse(params, str(e))
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused {CRATE}: {e}"
        daily_crate.pay(reward, player, self._cap_sp())
        self.player_store.save(player)
        reply = self.envelope(params)                      # daily_bonus_time: the next crate
        reply["result"] = True
        reply["daily_bonus"] = streak
        reply["bonus_result"] = daily_crate.bonus_result(reward)
        reply.update(self._player_parts(params, ("user_data", "user_cards") if reward.card else ("user_data",)))
        reply["new_cards"] = [str(reward.card)] if reward.card else []
        card = self._engine_data()[0].get(reward.card) if reward.card else None
        return reply, (f"opened the crate (day {streak} in a row): {reward.describe(card.name if card else None)}; "
                       f"the next one in {c.crate_every_hours}h")

    def _buy_store_upgrade(self, message: str, params: dict) -> tuple[dict, str]:
        """A store upgrade (store_upgrades.py): buyStorePromoTokens / buyStorePromoGold
        item_type=4 item_id (+50 Max Energy, +10 Card Slots), or buyUpgrade upgrade_id
        (the Extra Deck Slot)."""
        player = self.player()
        uid = str(params.get("upgrade_id") if message == BUY_UPGRADE else params.get("item_id"))
        up = store_upgrades.UPGRADES.get(uid)
        currency = "gold" if message == PROMO_GOLD else "wb"
        if up is None or (message != BUY_UPGRADE and up.currency != currency):
            return self._refuse(params, "That upgrade isn't for sale."), f"refused {message}: upgrade {uid!r}"
        try:
            price = store_upgrades.buy(player, up, params.get("expected_cost"), self._decks_limit())
        except store_upgrades.UpgradeError as e:
            reply = self._refuse(params, str(e))
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused {up.name}: {e}"
        self.player_store.save(player)
        if message == BUY_UPGRADE:
            reply = self.envelope(params)
            reply["result"] = True
            reply.update(self._player_parts(params, ("user_data", "user_decks")))
            reply["store_upgrades"] = copy.deepcopy(self._start.get("store_upgrades") or {})
            reply["max_decks"] = player.max_decks
            store_upgrades.apply_to_store(reply, player, self._decks_limit())
        else:
            recorded = [b for (m, _), bodies in self.replays.items() if m == PROMO_GOLD for b in bodies]
            plain = [b for b in recorded if "user_decks" not in b] or recorded
            reply = self._prepare(plain[-1], params) if plain else self.envelope(params)
            reply["result"] = True
            for k in ("new_cards", "new_cards_data"):
                reply.pop(k, None)
            if up.kind == "energy":
                reply["max_energy"] = player.max_energy    # the client takes the cap from here
            reply.update(self._player_parts(params, ("user_data",)))
        unit = "war bonds" if up.currency == "wb" else "gold"
        now = {"energy": f"max energy {player.max_energy}", "decks": f"{player.max_decks} deck slots"}.get(
            up.kind, f"{player.max_cards} card slots")
        return reply, f"bought {up.name} for {price} {unit}; {now}"

    def _set_name(self, params: dict) -> tuple[dict, str]:
        """setUserName name: the player's name (the reply is read for user_data)."""
        player = self.player()
        name = " ".join(str(params.get("name") or "").split())
        if not name or len(name) > NAME_LENGTH:
            return self._refuse(params, f"A name needs 1 to {NAME_LENGTH} characters."), f"refused {SET_NAME}: {name!r}"
        before, player.name = player.name, name
        player.name_changes += 1
        self.player_store.save(player)
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._player_parts(params, ("user_data",)))
        return reply, f"name: {before!r} -> {name!r}"

    def _choose_deck(self, message: str, params: dict) -> tuple[dict, str]:
        """setActiveDeck / setDefenseDeck deck_id (read back from user_data and user_decks)."""
        player = self.player()
        did = str(params.get("deck_id"))
        deck = (player.decks or {}).get(did)
        if deck is None or not did.isdigit() or not 1 <= int(did) <= (player.max_decks or 1):
            return self._refuse(params, f"Deck {did} isn't one of your decks."), f"refused {message}: deck {did}"
        if not deck.get("commander_id"):
            return self._refuse(params, f"Deck {did} has no commander yet."), f"refused {message}: deck {did} empty"
        which = "active_deck" if message == SET_ACTIVE else "defense_deck"
        setattr(player, which, did)
        self.player_store.save(player)
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._player_parts(params, ("user_data", "user_decks")))
        return reply, f"{'active' if message == SET_ACTIVE else 'defence'} deck: {did}"

    def _refresh(self, message: str, params: dict) -> tuple[dict, str]:
        """getUserData, getUserCards, getUserDecks, getCardData, getVersionNumber: what the
        game asks to see again, from the save."""
        reply = self.envelope(params)
        reply["result"] = True
        keys = REFRESH[message]
        reply.update(self._player_parts(params, [k for k in keys if k != "card_data"]))
        if "card_data" in keys:
            reply["card_data"] = []                    # as the recorded login sent it
        return reply, f"answered from the save ({', '.join(keys) or 'the envelope'})"

    # ---- achievements ---------------------------------------------------------------------------

    def _achievement_defs(self) -> dict:
        if getattr(self, "_ach_defs", None) is None:
            self._ach_defs = achievements.load(self._assets)
        return self._ach_defs

    def _cap_sp(self) -> int:
        caps = (self._start.get("user_data") or {}).get("caps") or {}
        return int(caps.get("max_salvage") or upgrades.DEFAULT_MAX_SALVAGE)

    def _track(self, player) -> list:
        """Finishes (and pays) every permanent achievement the player has now reached; the
        reply then carries them. Returns the ones finished."""
        done = achievements.complete_ready(self._achievement_defs(), player, self._cap_sp())
        self._ach_news = getattr(self, "_ach_news", []) + done
        self._ach_changed = True
        return done

    def _achievement_list(self, player) -> dict:
        """user_achievements now: a fresh set of daily objectives if the last ran out, and
        anything reached meanwhile finished (an older save's missions and rating)."""
        defs = self._achievement_defs()
        changed = achievements.ensure_dailies(defs, player, int(self.clock()), self.rng_factory())
        if self._track(player) or changed:
            self.player_store.save(player)
        return achievements.user_achievements(defs, player)

    def _claim_achievement(self, params: dict) -> tuple[dict, str]:
        """completeAchievement achievement_id: the Complete button of a finished daily
        objective; pays it."""
        player = self.player()
        try:
            a = achievements.claim(self._achievement_defs(), player, params.get("achievement_id"), self._cap_sp(),
                                   int(self.clock()))
        except ValueError as e:
            reply = self._refuse(params, str(e))
            return reply, f"refused {CLAIM}: {e}"
        self.player_store.save(player)
        self._ach_news, self._ach_changed = [a], True
        start = {"time": self._start.get("time"), "user_data": self._start.get("user_data") or {}, "user_cards": {}}
        reply = self.envelope(params)
        reply["result"] = True
        reply.update({k: v for k, v in self._prepare(start, params).items() if k in ("user_data", "user_cards")})
        return reply, f"claimed {a.name} ({a.desc})"

    # ---- the Upgrade screen ---------------------------------------------------------------------

    def _upgrade_rules(self) -> upgrades.Rules:
        if getattr(self, "_upgrade_data", None) is None:
            self._upgrade_data = (upgrades.load_levels(self._assets), upgrades.load_recipes(self._assets))
        levels, recipes = self._upgrade_data
        caps = (self._start.get("user_data") or {}).get("caps") or {}
        return upgrades.Rules(self._engine_data()[0], levels, recipes,
                              commander_mult=int(self._start.get("com_sp_mult") or upgrades.DEFAULT_COMMANDER_MULT),
                              max_salvage=int(caps.get("max_salvage") or upgrades.DEFAULT_MAX_SALVAGE))

    def _upgrade_screen(self, message: str, params: dict) -> tuple[dict, str]:
        """upgradeCard card_id in_deck, salvageCard card_id, salvageL1CommonCards,
        salvageL1RareCards, setCardLock card_id locked, fuseCard card_id (upgrades.py).
        The replies have the shape the game reads (UpgradeCardResponse and
        SalvageCardResponse): user_data, user_cards, user_decks, and new_card_id for an
        upgrade or fusion; salvage replies also carry buyback_data (empty, as recorded)."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep cards in.")
        rules = self._upgrade_rules()
        cid = params.get("card_id")
        before = player.salvage or 0
        new_id = None
        try:
            if message == UPGRADE:
                name = rules.card(cid).name
                new_id, cost = upgrades.upgrade(rules, player, cid, str(params.get("in_deck")) == "1",
                                                self._active_deck_id())
                how = f"upgraded {name} to level {rules.cards[new_id].level} for {cost} SP"
                achievements.count(player, "upgrade_level", rules.cards[new_id].level, "max")
            elif message == SALVAGE:
                name = rules.card(cid).name
                gained = upgrades.salvage(rules, player, cid)
                how = f"salvaged {name} for {gained} SP"
                achievements.count(player, "salvage", gained)
            elif message in SALVAGE_ALL:
                n, gained = upgrades.bulk_salvage(rules, player, SALVAGE_ALL[message])
                how = f"salvaged {n} level-1 {upgrades.RARITY_NAMES[SALVAGE_ALL[message]]} cards for {gained} SP"
                achievements.count(player, "salvage", gained)
            elif message == LOCK:
                locked = str(params.get("locked")) in ("1", "true", "True")
                upgrades.set_lock(player, cid, locked)
                how = f"{'locked' if locked else 'unlocked'} {rules.card(cid).name}"
            else:
                new_id, used = upgrades.fuse(rules, player, cid)
                achievements.count(player, "fusions", 1)
                how = (f"fused {rules.cards[new_id].name} from "
                       + " + ".join(f"{rules.cards[r].name}{f' x{n}' if n > 1 else ''}" for r, n in used))
        except upgrades.UpgradeError as e:
            reply = self._refuse(params, str(e))
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused {message}: {e}"
        if message != LOCK:
            self._track(player)              # "Rise to Power", "Scavenger", "Fusion Master"
        self.player_store.save(player)
        start = {"time": self._start.get("time"), "user_data": self._start.get("user_data") or {},
                 "user_cards": {}, "user_decks": {}}
        reply = self.envelope(params)
        reply["result"] = True
        reply.update({k: v for k, v in self._prepare(start, params).items()
                      if k in ("user_data", "user_cards", "user_decks")})
        if new_id is not None:
            reply["new_card_id"] = str(new_id)
        else:
            reply["buyback_data"] = []
        reply["new_cards_data"] = []
        if player.salvage != before:
            how += f" (SP {before} -> {player.salvage})"
        return reply, how

    def _in_tutorial(self, player) -> bool:
        """The tutorial is under way: its flag is a part number, not -1 (done) or -2 (off)."""
        raw = str((player.flags or {}).get("tutorial"))
        return raw.isdigit() and (not self.tutorials or int(raw) < len(self.tutorials))

    def _buy_stamina_refill(self, params: dict) -> tuple[dict, str]:
        """buyStaminaRefillTokens (no parameters; the game asks "Do you want to purchase a
        refill?" first): stamina up to the max, for wb_stamina_cost (30) war bonds.
        Unlike energy, not over the cap: given 10 + 15 = 25, the game showed 15 (it shows
        overfilled energy as e.g. 257/250), and its dialog says nothing about the cap."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep stamina in.")
        cost = int(self._start.get("wb_stamina_cost") or DEFAULT_STAMINA_REFILL_COST)
        a = player.arena
        if player.war_bonds < cost:
            reply = self._refuse(params, f"Not enough war bonds: a refill costs {cost}, and you have "
                                         f"{player.war_bonds}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused stamina refill: it costs {cost} war bonds, has {player.war_bonds}"
        before = (a["stamina"], player.war_bonds)
        a["stamina"] = max(a["stamina"], a["max_stamina"])
        a["stamina_updated"] = int(self.clock())   # at the max: the refill clock waits from now
        player.war_bonds -= cost
        self.player_store.save(player)
        reply = self.envelope(params)
        reply["result"] = True
        reply["user_data"] = self.current_user_data(params)
        return reply, (f"stamina refill: {before[0]} -> {a['stamina']}, "
                       f"{cost} war bonds ({before[1]} -> {player.war_bonds})")

    def _refresh_rivals(self, params: dict) -> tuple[dict, str]:
        """buyRivalsRefresh (no parameters, no confirmation): a new set of rivals for
        gold_rivals_cost (50) gold. The reply is the getHuntingTargets reply with the new
        list (the game empties the list while it waits)."""
        player = self.player()
        cost = int(self._start.get("gold_rivals_cost") or DEFAULT_REFRESH_COST)
        if player.arena["hunting_wins"] == 0:
            reply = self._refuse(params, "The rivals can't be refreshed before your first arena win.")
            reply["user_data"] = self.current_user_data(params)
            return reply, "refused rivals refresh: no arena win yet (the tutorial rival)"
        if player.gold < cost:
            reply = self._refuse(params, f"Not enough gold: a refresh costs {cost}, and you have {player.gold}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused rivals refresh: it costs {cost} gold, has {player.gold}"
        before = player.gold
        player.gold -= cost
        arena.refresh_rivals(player.arena)
        self.player_store.save(player)
        reply, how = self._hunting_targets(params)
        return reply, f"rivals refreshed for {cost} gold ({before} -> {player.gold}); {how}"

    def _unlock_commander(self, params: dict) -> tuple[dict, str]:
        """unlockCommander commander_id (Promote, after "Unlock this Commander?"): the
        commander and its bonus cards for a promotion point (arena.py). The reply is the
        recorded one's shape: new_commander, new_cards, user_data, user_cards, user_decks,
        and no `result`."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep cards in.")
        cards, _ = self._engine_data()
        raw = str(params.get("commander_id"))
        if not raw.isdigit():
            return self._refuse(params, "Invalid commander."), f"refused unlockCommander: {raw!r} isn't a card id"
        base = int(raw)
        try:
            bonus = arena.unlock_commander(player, base, cards, self.rng_factory())
        except arena.UnlockError as e:
            reply = self._refuse(params, f"Can't unlock that commander: {e}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused unlockCommander {base}: {e}"
        self.player_store.save(player)
        keys = ("user_data", "user_cards", "user_decks")
        reply = self.envelope(params)
        reply["new_commander"] = str(base)
        reply["new_cards"] = [str(c) for c in bonus]
        parts = self._prepare({"time": self._start.get("time"),
                               **{k: self._start[k] for k in keys if k in self._start}}, params)
        reply.update({k: parts[k] for k in keys if k in parts})
        names = ", ".join(cards[c].name for c in bonus)
        return reply, (f"unlocked {cards[base].name} ({base}) for a promotion point "
                       f"({player.arena['promotion_points']} left), with {names}")

    def _buy_energy_refill(self, params: dict) -> tuple[dict, str]:
        """buyEnergyRefillTokens: max energy added on top of the current energy (over the
        cap if need be: the game's own dialog says so, and a level-up does the same) for
        `wb_energy_cost` war bonds. The game shows exactly the user_data that comes back
        (missing fields read as 0), so the reply always carries all of it."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep energy in.")
        cost = int(self._start.get("wb_energy_cost") or DEFAULT_REFILL_COST)
        if player.war_bonds < cost:
            # The game checks its own copy first ("You do not have enough war bonds to buy
            # a refill..."), so this only happens when the two disagree.
            reply = self._refuse(params, f"Not enough war bonds: a refill costs {cost}, and you have "
                                         f"{player.war_bonds}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused: a refill costs {cost} war bonds, has {player.war_bonds}"
        before = (player.energy, player.war_bonds)
        player.energy += player.max_energy
        player.energy_updated = int(self.clock())   # at or over the max: the refill clock waits from now
        player.war_bonds -= cost
        self.player_store.save(player)
        reply = self.envelope(params)
        reply["result"] = True
        reply["user_data"] = self.current_user_data(params)
        return reply, (f"energy refill: +{player.max_energy} ({before[0]} -> {player.energy}), "
                       f"{cost} war bonds ({before[1]} -> {player.war_bonds})")

    def _battle_finished(self, body: dict) -> tuple[str, MissionResult | None]:
        """A replayed mission's end: pays its rewards into the save by the same rules as
        the battle engine. (Note for the log, what was paid.) Arena fights aren't paid yet."""
        b = self.battle
        if b is None or not b.get("mission_id"):
            return "", None
        won = battle_won(body)
        result = self._finish_mission(b["mission_id"], won)
        if result is None:
            known = self.player() is not None and won
            return (f"; mission {b['mission_id']} won, saved (not in the mission data, so nothing paid)"
                    if known else ""), None
        return f"; mission {b['mission_id']} {'won' if won else 'lost'}, paid {result.summary()}", result

    def _set_deck(self, params: dict) -> tuple[dict, str]:
        """setDeckCards deck_id, commander_id, cards (JSON {card_id: count}), dominion_id:
        saves the deck. The reply has the recorded setDeckCards reply's shape, with the
        save's data. The refusals are worded by us (no recorded ones)."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep decks in.")
        try:
            deck_id = str(int(params.get("deck_id")))
            commander = int(params.get("commander_id"))
            raw = params.get("cards") or "{}"
            counts = json.loads(raw) if isinstance(raw, str) else raw
            cards = {str(int(k)): int(v) for k, v in counts.items() if int(v) > 0}
            dominion = int(params.get("dominion_id") or 0)
        except (TypeError, ValueError, AttributeError):
            return self._refuse(params, "Invalid deck."), "refused setDeckCards: couldn't read the deck"
        saved = self.player()
        max_decks = (saved.max_decks if saved is not None and saved.max_decks else None) or int(self._start.get("max_decks") or 1)
        need = defaultdict(int)
        need[str(commander)] += 1
        for cid, n in cards.items():
            need[cid] += n
        if dominion:
            need[str(dominion)] += 1
        short = [cid for cid, n in need.items() if (player.cards.get(cid) or {}).get("num_owned", 0) < n]
        problem = (f"deck {deck_id} isn't one of your decks" if not 1 <= int(deck_id) <= max_decks
                   else f"a deck holds at most {DECK_SIZE} cards" if sum(cards.values()) > DECK_SIZE
                   else f"you don't own enough of card {', '.join(short)}" if short else None)
        if problem:
            return self._refuse(params, f"Can't save that deck: {problem}."), f"refused setDeckCards: {problem}"
        old = (player.decks or {}).get(deck_id) or {}
        player.decks = player.decks or {}
        player.decks[deck_id] = {"name": old.get("name"), "commander_id": commander, "cards": cards,
                                 "dominion_id": dominion}
        self.player_store.save(player)
        recorded = next((bodies[0] for (m, _), bodies in self.replays.items() if m == SET_DECK), None)
        if recorded is not None:
            reply = self._prepare(recorded, params)
        else:
            keys = ("user_data", "user_cards", "user_decks")
            reply = self.envelope(params)
            reply["result"] = True
            parts = self._prepare({"time": self._start.get("time"),
                                   **{k: self._start[k] for k in keys if k in self._start}}, params)
            reply.update({k: parts[k] for k in keys if k in parts})
        return reply, f"saved deck {deck_id}: commander {commander}, {sum(cards.values())} cards"

    def _set_flag(self, params: dict) -> tuple[dict, str]:
        """setUserFlag flag=<name> value=<value>: keeps the flag in the save. The reply is
        the player's user_data with the new flag, like the real server's."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep flags in.")
        flag, raw = params.get("flag"), params.get("value")
        if not flag or raw is None:
            return self._refuse(params, INVALID_FLAG), "refused: no flag or no value"
        if flag == "tutorial":
            # The flag is the index of the current tutorial part. Negative values are the
            # game's own markers: -1 all done, -2 switched off (the game sends that when it
            # loads with the tutorial unfinished but mission 1 already won). The real server
            # refused tutorial=5 for this 5-part tutorial, and the game then sent -1.
            n = len(self.tutorials)
            try:
                step = int(str(raw))
            except ValueError:
                step = None
            if step is None or (n and step >= n):
                return self._refuse(params, INVALID_FLAG), (f"refused tutorial={raw} (it has parts 0 to {n - 1}; "
                                                            f"-1 is finished), like the real server")
        known = flag in player.flags
        player.flags[flag] = flag_value(raw)
        self.player_store.save(player)
        reply = self.envelope(params)
        reply["result"] = True
        reply["user_data"] = self.current_user_data(params)
        how = f"saved {flag}={raw}"
        if flag == "tutorial":
            how += (" (tutorial finished)" if step == -1 else " (tutorial switched off by the game)" if step < -1
                    else f" (tutorial part {step + 1} of {len(self.tutorials) or '?'})")
        if not known:
            how += " (a flag the starting data didn't have)"
        return reply, how

    def _refuse(self, params: dict, message: str) -> dict:
        """The real server's refusal, as recorded: the envelope without a request echo."""
        reply = self.envelope(params)
        del reply["request"]
        reply["result"] = False
        reply["result_message"] = [message]
        return reply

    def current_user_data(self, params: dict) -> dict:
        """The player's user_data now: the starting data with the save applied. (Fields the
        save doesn't keep yet, like xp and level, are the starting ones.)"""
        start = {"time": self._start.get("time"), "user_data": self._start.get("user_data") or {}}
        return self._prepare(start, params)["user_data"]

    def _prepare(self, recorded: dict, params: dict) -> dict:
        body = copy.deepcopy(recorded)
        now = int(self.clock())
        recorded_now = int(body.get("time") or now)
        body = shift_times(body, recorded_now, now - recorded_now)
        body["time"] = now
        body["request"] = echo(params)
        if self.uid != OFFLINE_USER_ID:
            personalize(body, self.uid)
        if "gold_multiplier_missions" in body or "event_popups" in body or "event_popup" in body:
            apply_to_login(body, self.settings())   # the mission gold bonus in use, as the game shows it
        if isinstance(body.get("store_promos"), list) or isinstance(body.get("store_packs"), dict):
            c, promos, box_entries, _ = self._store()
            store_content.apply_to_store(body, c, promos, box_entries, set(self._mini_packs()))
        if isinstance(body.get("store_promos"), list) or isinstance(body.get("store_upgrades"), dict):
            player = self.player()
            if player is not None:
                store_upgrades.apply_to_store(body, player, self._decks_limit())
        if any(k in body for k in ("current_raids", "raid_info", "faction", "faction_invites", "fortress_slots")):
            player = self.player()
            guild = self._my_guild() if player is not None else None
            state = self._raid_state(guild)
            if "current_raids" in body:
                body["current_raids"] = raids.current_raids(state[0], state[1], self._mini_packs()) if state else []
            if "raid_info" in body:
                body["raid_info"] = self._raid_info(state) if state else None
            if player is not None:                          # the server's guilds, not the recording's
                parts = self._guild_parts(self._my_guild())
                body["faction"] = parts["faction"]
                if "fortress_slots" in body:
                    body["fortress_slots"] = parts["fortress_slots"]
                if "faction_invites" in body:
                    body["faction_invites"] = parts.get("faction_invites", [])
        if "guild_donations_enabled" in body:
            body["guild_donations_enabled"] = int(self.content().guild_donations)
        if "daily_bonus_time" in body:
            saved = self.player_store.load() if self.player_store is not None else None
            if saved is not None and saved.daily_bonus:
                body["daily_bonus_time"] = str(int(saved.daily_bonus["next"]))
        if "user_achievements" in body or "completed_achievements" in body:
            player = self.player()
            if player is not None:
                body["user_achievements"] = self._achievement_list(player)
                body["completed_achievements"] = achievements.completed_json(getattr(self, "_ach_news", []))
        if isinstance(body.get("user_data"), dict) or isinstance(body.get("user_cards"), dict):
            player = self.player()
            if player is not None:
                PlayerStore.apply(body, player)   # the save, not the recording, decides these values
        return body

    def envelope(self, params: dict) -> dict:
        """The fields every real response carries."""
        base = (self.fixtures.get("init") or self.fixtures.get("getUserAccount") or {}).get("body", {})
        now = int(self.clock())
        out = {k: base.get(k, v) for k, v in DEFAULT_ENVELOPE.items()}
        out["guild_donations_enabled"] = int(self.content().guild_donations)   # content.toml, not the recording
        bonus = base.get("daily_bonus_time")
        saved = self.player_store.load() if self.player_store is not None else None
        if saved is not None and saved.daily_bonus:
            out["daily_bonus_time"] = str(int(saved.daily_bonus["next"]))     # the crate's timer
        elif bonus is not None and _as_time(bonus):
            out["daily_bonus_time"] = str(int(bonus) - int(base["time"]) + now)
        else:
            out["daily_bonus_time"] = str(now + 86400)
        out["time"] = now
        out["request"] = echo(params)
        return out

    def unsupported(self, message: str, params: dict, reason: str | None = None) -> dict:
        body = self.envelope(params)
        body["result"] = False
        body["result_message"] = [f"Offline server: {reason or repr(message) + ' is not supported yet.'}"]
        return body
