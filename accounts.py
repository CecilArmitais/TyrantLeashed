"""Player accounts: who may play on this server, so that several players' browsers can
use it at once, each with their own save (state/players/<user_id>.json).

The client logs in with getUserAccount. A browser with no account sends no user id and
gets one back (new_user, new_password, new_syncode), which it keeps and sends with
every later request; a returning browser sends its user id and password. Whenever a
login reply carries new_user, the client takes those credentials, even if it had
others (docs/protocol.md, "Players and logins").

state/accounts.json holds each account's password as a SHA-256 hash (the passwords are
random, 128 bits), its syncode, when it was made and last seen, and the next user id.
Standard library only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

FIRST_USER_ID = 1000001


def new_password() -> str:
    return secrets.token_hex(16)          # 32 hex characters, like the real ones


def new_syncode() -> str:
    return secrets.token_hex(32)          # 64 hex characters, like the real ones


def _hash(password: str) -> str:
    return hashlib.sha256(str(password).encode()).hexdigest()


class AccountStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict:
        if self.path.is_file():
            return json.loads(self.path.read_text("utf-8"))
        return {"next_user_id": FIRST_USER_ID, "accounts": {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(data, indent=1), "utf-8")
        os.replace(tmp, self.path)

    def ids(self) -> list[int]:
        return sorted(int(u) for u in self._load()["accounts"])

    def get(self, user_id) -> dict | None:
        return self._load()["accounts"].get(str(user_id))

    def exists(self, user_id) -> bool:
        return self.get(user_id) is not None

    def create(self, now: int, user_id=None, password: str | None = None, syncode: str | None = None,
               rotate: bool = False) -> tuple[int, str, str]:
        """A new account: (user id, password, syncode). By default the next free id and
        random credentials. rotate: give it new credentials at its next login (for an
        account whose password is public, like the one a single-player save had)."""
        data = self._load()
        uid = int(user_id) if user_id is not None else int(data["next_user_id"])
        if str(uid) in data["accounts"]:
            raise ValueError(f"account {uid} already exists")
        password = password or new_password()
        syncode = syncode or new_syncode()
        data["accounts"][str(uid)] = {"password": _hash(password), "syncode": syncode, "created": int(now),
                                      "last_seen": int(now), **({"rotate": True} if rotate else {})}
        data["next_user_id"] = max(int(data["next_user_id"]), uid + 1)
        self._save(data)
        return uid, password, syncode

    def check(self, user_id, password) -> bool:
        """Whether this is the account's password (or its previous one, until the client
        has used the new one: see rotate_credentials)."""
        acc = self.get(user_id)
        if acc is None or not password:
            return False
        given = _hash(password)
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

    def set_password(self, user_id, password: str) -> None:
        self._update(user_id, password=_hash(password), previous=None, rotate=None)

    def seen(self, user_id, now: int) -> None:
        self._update(user_id, last_seen=int(now))

    def _update(self, user_id, **changes) -> None:
        data = self._load()
        acc = data["accounts"][str(user_id)]
        for k, v in changes.items():
            if v is None:
                acc.pop(k, None)
            else:
                acc[k] = v
        self._save(data)
