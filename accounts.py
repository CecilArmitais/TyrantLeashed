"""Player accounts: who may play on this server, so that several players' browsers can
use it at once, each with their own save (state/players/<user_id>.json).

- **Signing in** happens on the server's own page, before the game (offline_server.py):
  a player gives a username, and a password only if the server asks for one
  (content.toml's `[accounts] passwords`). The username is unique on the server, in any
  case, and is the player's name in the game. Signing in gives the browser a session,
  a cookie the page's requests carry.
- **The game's own login** (getUserAccount) then picks up that session: whenever its
  reply carries new_user, the client takes those credentials, even if it had others
  (docs/protocol.md, "Players and logins"). Each browser gets its own game password,
  so two browsers can play one account; every later request carries the user id and
  that password.

state/accounts.json holds, for each account: its username; the game passwords' SHA-256
hashes (the passwords are random, 128 bits); the sign-in password, if it has one, as a
salted scrypt hash; the sessions' hashes; its syncode; when it was made and last seen.
And the next user id. Standard library only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from pathlib import Path

import jsonfile

FIRST_USER_ID = 1000001
NAME_LENGTH = 20                    # ours: the real limit isn't known
NAME_CHARACTERS = re.compile(r"^[\w .'\-]+$")   # letters and digits of any script, space, _ . ' -
BROWSERS_KEPT = 8                   # game passwords an account keeps: one per browser (ours)
SESSIONS_KEPT = 8                   # signed-in browsers an account keeps (ours)
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}     # the sign-in passwords' hashing


def new_password() -> str:
    return secrets.token_hex(16)          # 32 hex characters, like the real ones


def new_syncode() -> str:
    return secrets.token_hex(32)          # 64 hex characters, like the real ones


def _hash(password: str) -> str:
    return hashlib.sha256(str(password).encode()).hexdigest()


def _scrypt(password: str, salt: bytes) -> str:
    return hashlib.scrypt(str(password).encode(), salt=salt, **SCRYPT).hex()


def clean_name(name) -> str:
    """A username as kept: runs of spaces as one, none at the ends."""
    return " ".join(str(name or "").split())


def name_problem(name: str) -> str | None:
    """Why a username can't be used (in words for the player), or None."""
    if not 1 <= len(name) <= NAME_LENGTH:
        return f"A name needs 1 to {NAME_LENGTH} characters."
    if not NAME_CHARACTERS.match(name):
        return "A name can have letters, digits, spaces and _ . ' - only."
    return None


class AccountStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict:
        return jsonfile.read(self.path, {"next_user_id": FIRST_USER_ID, "accounts": {}})

    def _save(self, data: dict) -> None:
        jsonfile.write(self.path, data)

    def ids(self) -> list[int]:
        return sorted(int(u) for u in self._load()["accounts"])

    def next_user_id(self) -> int:
        """The id the next new account gets."""
        return int(self._load()["next_user_id"])

    def get(self, user_id) -> dict | None:
        return self._load()["accounts"].get(str(user_id))

    def exists(self, user_id) -> bool:
        return self.get(user_id) is not None

    def create(self, now: int, user_id=None, password: str | None = None, syncode: str | None = None,
               rotate: bool = False, name: str | None = None) -> tuple[int, str, str]:
        """A new account: (user id, password, syncode). By default the next free id and
        random credentials. rotate: give it new credentials at its next login (for an
        account whose password is public, like the one a single-player save had)."""
        data = self._load()
        uid = int(user_id) if user_id is not None else int(data["next_user_id"])
        if str(uid) in data["accounts"]:
            raise ValueError(f"account {uid} already exists")
        if name is not None and self._find(data, name) is not None:
            raise ValueError(f"the name {name!r} is taken")
        password = password or new_password()
        syncode = syncode or new_syncode()
        data["accounts"][str(uid)] = {"password": _hash(password), "syncode": syncode, "created": int(now),
                                      "last_seen": int(now), **({"rotate": True} if rotate else {}),
                                      **({"name": clean_name(name)} if name is not None else {})}
        data["next_user_id"] = max(int(data["next_user_id"]), uid + 1)
        self._save(data)
        return uid, password, syncode

    # ---- the game's credentials (sent with every request) ---------------------------------------

    def check(self, user_id, password) -> bool:
        """Whether this is one of the account's game passwords (or its previous one, until
        the client has used the new one: see rotate_credentials)."""
        acc = self.get(user_id)
        if acc is None or not password:
            return False
        given = _hash(password)
        if any(hmac.compare_digest(h, given) for h in acc.get("passwords", [])):
            return True
        if hmac.compare_digest(acc["password"], given):
            if acc.get("previous"):                 # the client has the new password now
                self._update(user_id, previous=None)
            return True
        return bool(acc.get("previous")) and hmac.compare_digest(acc["previous"], given)

    def needs_rotation(self, user_id) -> bool:
        acc = self.get(user_id)
        return bool(acc and acc.get("rotate"))

    def rotate_credentials(self, user_id) -> tuple[str, str]:
        """New credentials for an account, sent in its login reply. The old password still
        works until a request comes with the new one, so a lost reply can't lock the
        player out."""
        password, syncode = new_password(), new_syncode()
        acc = self.get(user_id)
        self._update(user_id, password=_hash(password), syncode=syncode, previous=acc["password"], rotate=None)
        return password, syncode

    def add_browser(self, user_id) -> str:
        """A game password for one more browser (sent in its login reply). The account
        keeps the last BROWSERS_KEPT, so several browsers can play it, each with its own."""
        password = new_password()
        acc = self.get(user_id)
        kept = ([_hash(password)] + acc.get("passwords", []))[:BROWSERS_KEPT]
        self._update(user_id, passwords=kept)
        return password

    def set_password(self, user_id, password: str) -> None:
        self._update(user_id, password=_hash(password), previous=None, rotate=None)

    def seen(self, user_id, now: int) -> None:
        self._update(user_id, last_seen=int(now))

    # ---- usernames --------------------------------------------------------------------------------

    @staticmethod
    def _find(data: dict, name: str) -> int | None:
        key = clean_name(name).casefold()
        return next((int(u) for u, a in data["accounts"].items() if str(a.get("name", "")).casefold() == key), None)

    def find(self, name: str) -> int | None:
        """The account with this username (in any case), or None."""
        return self._find(self._load(), name)

    def name_of(self, user_id) -> str | None:
        acc = self.get(user_id)
        return acc.get("name") if acc else None

    def set_name(self, user_id, name: str) -> None:
        """Gives an account a username; ValueError if another account has it."""
        name = clean_name(name)
        other = self.find(name)
        if other is not None and other != int(user_id):
            raise ValueError(f"the name {name!r} is taken")
        self._update(user_id, name=name)

    # ---- signing in: the optional password, and the browsers' sessions -----------------------------

    def has_login_password(self, user_id) -> bool:
        acc = self.get(user_id)
        return bool(acc and acc.get("login"))

    def set_login_password(self, user_id, password: str) -> None:
        salt = secrets.token_bytes(16)
        self._update(user_id, login=f"scrypt${salt.hex()}${_scrypt(password, salt)}")

    def check_login_password(self, user_id, password) -> bool:
        acc = self.get(user_id)
        if not acc or not acc.get("login") or not password:
            return False
        _, salt, digest = acc["login"].split("$")
        return hmac.compare_digest(digest, _scrypt(password, bytes.fromhex(salt)))

    def clear_login_password(self, user_id) -> None:
        self._update(user_id, login=None)

    def new_session(self, user_id, now: int) -> str:
        """A signed-in browser's session token (its cookie); the account keeps the last
        SESSIONS_KEPT."""
        token = secrets.token_urlsafe(32)
        acc = self.get(user_id)
        kept = ([{"hash": _hash(token), "created": int(now)}] + acc.get("sessions", []))[:SESSIONS_KEPT]
        self._update(user_id, sessions=kept)
        return token

    def session_user(self, token) -> int | None:
        """The account a session token belongs to, or None."""
        if not token:
            return None
        given = _hash(token)
        return next((int(u) for u, a in self._load()["accounts"].items()
                     if any(hmac.compare_digest(s["hash"], given) for s in a.get("sessions", []))), None)

    def end_session(self, token) -> None:
        uid = self.session_user(token)
        if uid is not None:
            given = _hash(token)
            self._update(uid, sessions=[s for s in self.get(uid).get("sessions", [])
                                        if not hmac.compare_digest(s["hash"], given)])

    def _update(self, user_id, **changes) -> None:
        data = self._load()
        acc = data["accounts"][str(user_id)]
        for k, v in changes.items():
            if v is None:
                acc.pop(k, None)
            else:
                acc[k] = v
        self._save(data)
