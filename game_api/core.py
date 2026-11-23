"""The game API: GameApi answers the client's messages (handle / respond), from the engine,
the saves and the server's state, or from the recordings. Each feature's calls are a part
of it in its own file (sessions.py, store.py, wars.py...), joined here."""

from __future__ import annotations

import copy
import json
import random
import threading
import time
from pathlib import Path

import achievements
import brawls
import conquest
import guilds
import paths
import raids
import store
import wars
from accounts import AccountStore
from battle.cards import CardDB
from content import Content, ContentStore
from player_state import PlayerStore
from rewards import RewardRules
from settings import Settings, SettingsStore, apply_to_login

from .achievements import AchievementCalls
from .arena import ArenaCalls
from .base import (DEFAULT_ENVELOPE, NOT_LOGGED_IN, NotSupported, OFFLINE_USER_ID, Session, mission_costs,
                   tutorial_names)
from .battles import BattleCalls
from .brawls import BrawlCalls
from .conquest import ConquestCalls
from .events import EventCalls
from .guilds import GuildCalls
from .items import ItemCalls
from .messages import (BATTLE_STARTS, BATTLE_TURN, BRAWL_MESSAGES, CLAIM, CONQUEST_MESSAGES, CRATE,
                       ENERGY_REFILL, FORFEIT, FUSE, GUILD_MESSAGES, ITEM_MESSAGES, LOCK, LOGIN, PRACTICE,
                       RAID_MESSAGES, REFRESH, REPORTS, RIVALS_REFRESH, SALVAGE, SALVAGE_ALL, SET_ACTIVE,
                       SET_DECK, SET_DEFENSE, SET_FLAG, SET_NAME, STAMINA_REFILL, STORE_MESSAGES, TARGETS,
                       UNLOCK, UPGRADE, WAR_MESSAGES)
from .player_data import PlayerDataCalls
from .raids import RaidCalls
from .replay import ReplayCalls, _as_time, echo, personalize, request_key, shift_times
from .sessions import SessionCalls
from .store import StoreCalls
from .upgrades import UpgradeCalls
from .wars import WarCalls


class GameApi(SessionCalls, ReplayCalls, BattleCalls, ArenaCalls, StoreCalls, GuildCalls, WarCalls,
              ConquestCalls, BrawlCalls, RaidCalls, EventCalls, PlayerDataCalls, AchievementCalls,
              UpgradeCalls, ItemCalls):
    """Answers the client's messages. Each base is one feature's calls (its own file in
    this package); they all work on this object's state, set up in __init__."""

    def __init__(self, fixture_dir: Path | None = None, clock=time.time, state_dir: Path | None = None,
                 asset_dir: Path | None = None, battle_engine: bool = False, single_player: bool = True) -> None:
        """fixture_dir: the recorded login and replies (default: paths.FIXTURE_DIR).
        state_dir: where the saves live (default: paths.state_dir_for(fixture_dir)).
        asset_dir: the game data files (default: paths.ASSET_DIR).
        battle_engine: missions are real battles from the battle engine instead of
        replayed recordings (battle/).
        single_player: every login is the offline player (1000001), saved in
        state/player.json, as before accounts existed (the tests and tools use this).
        Otherwise each browser has its own account (accounts.py) and save
        (state/players/<user_id>.json), and every request must carry its credentials;
        a single-player save found in state/ becomes player 1000001's."""
        self.clock = clock
        self.battle_engine = battle_engine
        self.rng_factory = random.Random       # shuffles and random picks; tests swap in a seeded one
        self._assets = paths.ASSET_DIR if asset_dir is None else asset_dir     # read now: tests patch paths
        self._cards: CardDB | None = None
        self._missions: dict | None = None
        self._missions_by_file: dict = {}
        # The game files, each read on first use by the part that needs it.
        self._ach_defs = self._standing = self._bges = self._items = self._upgrade_data = None
        self._brawls = self._conquests = self._raids = self._wars = None
        self._scaling_pack = self._mini_pack_data = self._box_data = None
        self._reset_objective_news()
        fixture_dir = paths.FIXTURE_DIR if fixture_dir is None else Path(fixture_dir)
        self.fixtures: dict[str, dict] = {}
        for name in LOGIN:
            f = fixture_dir / f"{name}.json"
            if f.is_file():
                self.fixtures[name] = json.loads(f.read_text("utf-8"))
        assets = self._assets
        self.tutorials = tutorial_names(assets, self._login_value("tutorial_file"))
        self.mission_costs = mission_costs(assets, self._login_value("mission_file"))
        self._costs_by_file = {self._login_value("mission_file"): self.mission_costs}
        # The saved player starts from the recorded init (gold, war bonds, energy, cards).
        self.player_store: PlayerStore | None = None
        self.reward_rules = RewardRules()
        state = paths.state_dir_for(fixture_dir) if state_dir is None else Path(state_dir)
        self.settings_store = SettingsStore(state / "settings.json")     # the gold bonuses
        self.content_store = ContentStore(state / "content.toml")        # the store and event boxes
        self.guild_store = guilds.GuildStore(state / "guilds")            # the server's guilds
        self.war_store = wars.WarStore(state / "wars.json")              # the server's guild wars
        self.brawl_store = brawls.BrawlStore(state / "brawls.json")      # the server's brawls
        self.challenge_store = achievements.ChallengeStore(state / "challenge.json")   # the objective challenge
        self.conquest_store = conquest.ConquestStore(state / "conquest.json")    # the server's conquest
        self.single_player = single_player
        self._state = state
        self.accounts = AccountStore(state / "accounts.json")             # who may play (multi-player)
        self._lock = threading.RLock()                    # one request at a time, whoever sends it
        self._stores: dict[str, PlayerStore] = {}
        self._sessions: dict[str, Session] = {}
        self.migrated = False
        # The recorded login the saves start from (none: the server refuses until it's made).
        self._start: dict = {}
        self._start_source: dict = {}
        self.max_energy, self.energy_every, self.stamina_every = 250, 60, 900
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

    @property
    def missing_fixtures(self) -> list[str]:
        return [n for n in LOGIN if n not in self.fixtures]

    def settings(self) -> Settings:
        """The server's settings now (re-read when the file changes)."""
        return self.settings_store.load()

    def content(self) -> Content:
        """What's on offer (content.toml, re-read when it changes)."""
        return self.content_store.load()

    def startup_report(self) -> str | None:
        """Lines for the server's startup: the save, the energy that came back while the
        server was off (counted from the save's sync point to now), the gold bonuses, the
        store, and the guilds."""
        if self.player_store is None:
            return None
        bonuses = ("\nGold bonuses: " + self.settings().describe() + "\n" + self.store_report()
                   + "\n" + self.guild_report() + self._war_report() + self._brawl_report()
                   + self._conquest_report() + "\n" + self.objectives_report())
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

    # ---- answering -------------------------------------------------------------------------------

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

    def _reset_objective_news(self) -> None:
        """What a request's objectives did, for its reply: finished achievements to show,
        whether the list changed, and objectives ready to complete. Reset per request."""
        self._ach_news: list = []
        self._ach_changed = False
        self._ach_ready: list[str] = []

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
        self._reset_objective_news()
        reply, how = self._dispatch_message(message, params)
        if self._ach_ready:
            how += "; objective finished (tap Complete): " + ", ".join(self._ach_ready)
        if self._ach_changed and reply.get("result") is not False and self.player() is not None:
            reply["user_achievements"] = achievements.user_achievements(self._achievement_defs(), self.player(),
                                                                        self._events())
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
        if message in STORE_MESSAGES:
            sold = self._store_message(message, params)
            if sold is not None:
                return sold
            # a promo the server doesn't sell: replayed if recorded, below
        if message in (UPGRADE, SALVAGE, LOCK, FUSE) or message in SALVAGE_ALL:
            return self._upgrade_screen(message, params)
        if message == CLAIM and self.player() is not None:
            return self._claim_achievement(params)
        if self.player() is not None:
            if message == PRACTICE:
                return self._practice_fight(params)
            if message in GUILD_MESSAGES:
                return self._guild_message(message, params)
            if message in RAID_MESSAGES:
                return self._raid_message(message, params)
            if message in WAR_MESSAGES:
                return self._war_message(message, params)
            if message in BRAWL_MESSAGES:
                return self._brawl_message(message, params)
            if message in CONQUEST_MESSAGES:
                return self._conquest_message(message, params)
            if message in ITEM_MESSAGES:
                return self._item_message(message, params)
            if message == CRATE:
                return self._open_crate(params)
            if message == SET_NAME:
                return self._set_name(params)
            if message in (SET_ACTIVE, SET_DEFENSE):
                return self._choose_deck(message, params)
            if message in REFRESH:
                return self._refresh(message, params)
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

    def _refuse(self, params: dict, message: str) -> dict:
        """The real server's refusal, as recorded: the envelope without a request echo."""
        reply = self.envelope(params)
        del reply["request"]
        reply["result"] = False
        reply["result_message"] = [message]
        return reply

    def _ok(self, params: dict) -> dict:
        """A reply that worked: the envelope, with result true."""
        reply = self.envelope(params)
        reply["result"] = True
        return reply

    def _start_parts(self, params: dict, keys) -> dict:
        """The recorded login's parts `keys` (those it has), brought up to now with the save
        applied (_prepare): the parts of a reply that show the player's state."""
        parts = self._prepare({"time": self._start.get("time"),
                               **{k: self._start[k] for k in keys if k in self._start}}, params)
        return {k: parts[k] for k in keys if k in parts}

    def _player_parts(self, params: dict, keys) -> dict:
        """The save's user_data / user_cards / user_decks, in the recorded login's shape."""
        start = {"time": self._start.get("time"), "user_data": self._start.get("user_data") or {},
                 "user_cards": {}, "user_decks": {}}
        prepared = self._prepare(start, params)
        return {k: prepared[k] for k in keys if k in prepared}

    def current_user_data(self, params: dict) -> dict:
        """The player's user_data now: the starting data with the save applied. (Fields the
        save doesn't keep yet, like xp and level, are the starting ones.)"""
        return self._player_parts(params, ("user_data",))["user_data"]

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
            c, promo_entries, box_entries, _ = self._store()
            store.apply_offers(body, c, promo_entries, box_entries, set(self._mini_packs()))
        if isinstance(body.get("store_promos"), list) or isinstance(body.get("store_upgrades"), dict):
            player = self.player()
            if player is not None:
                store.apply_upgrades(body, player, self._decks_limit())
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
        if "active_war_event" in body or "faction_war_event_info" in body or "faction_war" in body:
            for key in ("active_war_event", "faction_war_event_info", "faction_war"):
                body.pop(key, None)                         # the recording's event; ours, if any, below
            if self.player() is not None:
                body.update(self._war_parts(self._my_guild()))
        if "mission_file" in body and self.player() is not None:
            body["mission_file"] = self._mission_file()        # the full campaign past mission 56
        if "conquest_data" in body and self.player() is not None:
            body["conquest_data"] = self._conquest_parts(self._my_guild()).get("conquest_data", [])
        if "active_brawl_data" in body or "player_brawl_data" in body:
            body.pop("active_brawl_data", None)              # the recording's brawl; ours, if any, below
            body.pop("player_brawl_data", None)
            if self.player() is not None:
                body.update(self._brawl_parts())
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
                body["completed_achievements"] = achievements.completed_json(self._ach_news)
        if isinstance(body.get("user_data"), dict) or isinstance(body.get("user_cards"), dict):
            player = self.player()
            if player is not None:
                PlayerStore.apply(body, player)   # the save, not the recording, decides these values
                if "battle_energy" in body["user_data"]:
                    self._patch_battle_energy(body["user_data"])
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
