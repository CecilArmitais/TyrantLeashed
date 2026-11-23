"""Logins, accounts and saves, the session's battle, and other players' saves, decks and names.
Part of GameApi (core.py), as SessionCalls."""

from __future__ import annotations

import os
from pathlib import Path

import arena
import guilds
from player_state import Player, PlayerStore

from .base import OFFLINE_NAME, OFFLINE_PASSWORD, OFFLINE_SYNCODE, OFFLINE_USER_ID, Session
from .messages import LOGIN


class SessionCalls:
    """GameApi's part for logins, accounts and saves, the session's battle, and other players' saves, decks and names."""

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

    def _reset_session(self) -> None:
        """A new login starts the player's session (and the recorded story) over."""
        self._sessions[str(self.uid)] = Session()

    def player(self) -> Player | None:
        """The saved player (created on first use), energy refilled to now."""
        if self.player_store is None:
            return None
        return self.player_store.current(self._start, self.energy_every, self._start_source, self.stamina_every)

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

    def _defense_deck_of(self, player: Player) -> dict:
        """A player's defense deck (user_data.defense_deck), or their active deck while
        that's unset or empty."""
        defense = (player.decks or {}).get(player.defense_deck or "")
        return defense if defense and defense.get("commander_id") else self._deck_of(player)

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
