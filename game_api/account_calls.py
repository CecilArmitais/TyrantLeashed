"""Signing in on the server's own page (offline_server.py serves it): a username, a
password if content.toml's [accounts] asks for one, and a session for the browser; the
progress a new player can bring (imports); and a player's save to take elsewhere.
Part of GameApi (core.py), as AccountCalls.

An import is either a save from a server like this one, or the official game's login
data (its `init` reply), converted to a save (login_import.py). Nothing can check
either, so content.toml's `[accounts] imports` decides: "off" takes none, "review" keeps
each waiting in state/imports/<user_id>.json until the host accepts it (player.py), and
"on" loads it at once. Only a new name can bring one, so no one's progress is replaced
by someone else's file."""

from __future__ import annotations

import json

from common import jsonfile
from common.accounts import clean_name, name_problem
from rules.login_import import player_from_login
from rules.player_state import Player, player_from_save, save_json

LOGIN_PASSWORD_LENGTH = 6           # the least a sign-in password may have (ours)
CARDS_SHOWN = 5                     # the rarest cards a waiting import's summary names


class ImportProblem(Exception):
    """Why an import can't be used, in words the player can read."""


class AccountCalls:
    """GameApi's part for signing in, sessions, imports and exports (accounts.py)."""

    # ---- the sign-in page -------------------------------------------------------------------------

    def sign_in_info(self, session: str | None) -> dict:
        """What the sign-in page shows: the server's rules, and who this browser is
        signed in as (None if no one)."""
        with self._lock:
            c = self.content()
            uid = None if self.single_player else self.accounts.session_user(session)
            return {"single_player": self.single_player, "passwords": c.account_passwords,
                    "imports": c.account_imports, "name": self.accounts.name_of(uid) if uid else None}

    def signed_in(self, session: str | None) -> bool:
        """Whether this browser may load the game: signed in, or a single-player server."""
        with self._lock:
            return self.single_player or self.accounts.session_user(session) is not None

    def sign_in(self, name, password=None, record=None) -> tuple[dict, str | None, str]:
        """Signs a browser in as `name`, making the account (and its save) if the name is
        new, with `record` (an import) if one came: (the page's reply, the new session
        token or None, a line for the log)."""
        with self._lock:
            if self.single_player:
                return self._sign_in_refused("This server has one player, so there's nothing to sign in to.")
            c, now = self.content(), int(self.clock())
            name = clean_name(name)
            problem = name_problem(name)
            if problem:
                return self._sign_in_refused(problem)
            uid = self.accounts.find(name)
            password = str(password or "")
            claim = False
            if c.account_passwords:
                if uid is not None and self.accounts.has_login_password(uid):
                    if not self.accounts.check_login_password(uid, password):
                        return self._sign_in_refused(f"That isn't {self.accounts.name_of(uid)}'s password.")
                elif len(password) < LOGIN_PASSWORD_LENGTH:
                    return self._sign_in_refused(
                        f"Choose a password of at least {LOGIN_PASSWORD_LENGTH} characters for this server "
                        "(never one you use elsewhere).")
                else:
                    claim = True
            imported = None
            if record is not None:
                if c.account_imports == "off":
                    return self._sign_in_refused("This server doesn't take imported progress.")
                if uid is not None:
                    return self._sign_in_refused(f"The name {self.accounts.name_of(uid)} is taken: bringing your "
                                                 "progress needs a name of your own.")
                try:
                    imported = self._read_import(record, now)
                except ImportProblem as e:
                    return self._sign_in_refused(str(e))
            new = uid is None
            if new:
                uid, _, _ = self.accounts.create(now, name=name)
                self.use_player(uid)
                player = self.player()                   # the new player's save, from the starting data
                if player is not None:
                    player.name = name
                    self.player_store.save(player)
            if claim:
                self.accounts.set_login_password(uid, password)
            token = self.accounts.new_session(uid, now)
            shown = self.accounts.name_of(uid)
            reply = {"ok": True, "name": shown, "new": new, "password_set": claim}
            how = f"{'new player' if new else 'player'} {uid} ({shown}) signed in" + \
                (" with a new password" if claim else "")
            if imported is not None:
                player, kind, notes = imported
                reply["notes"] = notes
                if c.account_imports == "on":
                    self._load_import(uid, player)
                    reply["import"] = "loaded"
                    how += f"; their {kind} import is loaded"
                else:
                    jsonfile.write(self._import_path(uid), {"name": shown, "kind": kind, "received": now,
                                                            "notes": notes, "save": save_json(player, now)})
                    reply["import"] = "waiting"
                    how += f"; their {kind} import waits for the host (python server/tools/player.py imports)"
            return reply, token, how

    def sign_out(self, session: str | None) -> None:
        with self._lock:
            if session:
                self.accounts.end_session(session)

    def my_save(self, session: str | None) -> tuple[str, dict] | None:
        """A signed-in player's save, to download: (the file name, its data), or None."""
        with self._lock:
            uid = None if self.single_player else self.accounts.session_user(session)
            if uid is None:
                return None
            return self.export_save(uid)

    @staticmethod
    def _sign_in_refused(message: str) -> tuple[dict, None, str]:
        return {"ok": False, "message": message}, None, f"sign-in refused: {message}"

    # ---- imports ----------------------------------------------------------------------------------

    def _read_import(self, record, now: int) -> tuple[Player, str, list[str]]:
        """An import as a player: (the player, "save" or "login", notes for the player).
        ImportProblem if it's neither, or can't be read."""
        if isinstance(record, dict) and "player" in record and "version" in record:
            kind = "save"
            try:
                player = player_from_save(record, self.max_energy)
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                raise ImportProblem(f"That save can't be read ({e}).") from None
        elif isinstance(record, dict) and isinstance(record.get("user_data"), dict):
            kind = "login"
            try:
                player = player_from_login(record, now, self._achievement_defs(), self.max_energy)
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                raise ImportProblem(f"That login data can't be read ({e}).") from None
        else:
            raise ImportProblem("That file is neither a save from a server like this one nor the game's "
                                "login data.")
        return player, kind, self._fit_import(player)

    def _fit_import(self, player: Player) -> list[str]:
        """Leaves out what this server's game data doesn't have (cards, items), and says
        so; the import's name gives way to the account's."""
        cards, _ = self._engine_data()
        notes = []
        unknown = [cid for cid in player.cards if not str(cid).isdigit() or int(cid) not in cards.cards]
        for cid in unknown:
            del player.cards[cid]
        for deck in (player.decks or {}).values():
            deck["cards"] = {c: n for c, n in deck["cards"].items() if c.isdigit() and int(c) in cards.cards}
            if deck.get("commander_id") and int(deck["commander_id"]) not in cards.cards:
                deck["commander_id"] = 0
        if unknown:
            notes.append(f"{len(unknown)} cards this server's game data doesn't have were left out.")
        item_defs = self._item_defs()
        odd = [i for i in player.items if not str(i).isdigit() or int(i) not in item_defs]
        for i in odd:
            del player.items[i]
        if odd:
            notes.append(f"{len(odd)} items this server's game data doesn't have were left out.")
        player.created_from = {"import": True}
        return notes

    def _load_import(self, uid: int, player: Player) -> None:
        """Makes an import the player's save, under the account's name."""
        self.use_player(uid)
        player.name = self.accounts.name_of(uid)
        player.energy_updated = int(self.clock())          # energy refills from now
        self.player_store.save(player)

    def _import_path(self, uid: int):
        return self._state / "imports" / f"{int(uid)}.json"

    def waiting_imports(self) -> list[dict]:
        """Imports waiting for the host, oldest first, each with a summary of the player."""
        with self._lock:
            folder = self._state / "imports"
            out = []
            for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
                if not f.stem.isdigit():
                    continue
                w = jsonfile.read(f)
                player = player_from_save(w["save"], self.max_energy)
                out.append({"user_id": int(f.stem), "name": w.get("name"), "kind": w.get("kind"),
                            "received": w.get("received"), "notes": w.get("notes", []),
                            "summary": self._summary(player)})
            return sorted(out, key=lambda w: w["received"] or 0)

    def _summary(self, player: Player) -> dict:
        cards, _ = self._engine_data()
        owned = [(cards[int(c)], v["num_owned"]) for c, v in player.cards.items() if int(c) in cards.cards]
        rarest = sorted(owned, key=lambda cn: (-cn[0].rarity, cn[0].name))[:CARDS_SHOWN]
        return {"level": (player.arena or {}).get("level", 1), "gold": player.gold, "war_bonds": player.war_bonds,
                "sp": player.salvage or 0, "cards": sum(n for _, n in owned),
                "missions_won": sum(1 for m in (player.missions or {}).values() if m.get("complete")),
                "rarest": [f"{c.name} x{n}" for c, n in rarest]}

    def accept_import(self, uid: int) -> str:
        """The host's OK: the waiting import replaces the player's save."""
        with self._lock:
            f = self._import_path(uid)
            if not f.is_file():
                raise ValueError(f"player {uid} has no import waiting")
            w = jsonfile.read(f)
            self._load_import(int(uid), player_from_save(w["save"], self.max_energy))
            f.unlink()
            return f"{w.get('name')}'s import is now their save."

    def reject_import(self, uid: int) -> str:
        with self._lock:
            f = self._import_path(uid)
            if not f.is_file():
                raise ValueError(f"player {uid} has no import waiting")
            name = jsonfile.read(f).get("name")
            f.unlink()
            return f"{name}'s import is turned down; they keep the save they have."

    # ---- exports ----------------------------------------------------------------------------------

    def export_save(self, uid: int) -> tuple[str, dict]:
        """A player's save as a file to take to another server: (a file name, its data)."""
        with self._lock:
            self.use_player(uid)
            player = self.player()
            if player is None:
                raise ValueError("there is no starting data to make a save from")
            name = "".join(ch if ch.isalnum() else "-" for ch in (player.name or str(uid))).strip("-") or str(uid)
            return f"tyrant-{name}.json", json.loads(json.dumps(save_json(player, int(self.clock()))))
