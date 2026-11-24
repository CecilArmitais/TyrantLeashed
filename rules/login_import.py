"""The official game's login data as one of this server's saves, so a player can carry
their real progress over: the reply to the game's `init` call, which holds the player's
cards, decks, gold, war bonds, energy, missions, arena rating and level, items and
objectives. The sign-in page takes it (game_api/account_calls.py), or a host converts it
with `python server/tools/convert_login.py`.

- **Getting it:** in the official game, the browser's developer tools (Network) show the
  `api.php?message=init` call; its response is the login data. A HAR file of a session
  (the tools' "Save all as HAR") holds it too, and login_from_har finds it.
- **What's kept:** what this server's save holds, read as a new player's starting data
  is (player_state.player_from_start), plus what a new account's login doesn't have:
  card locks, items, and how far the objectives got.
- **What isn't:** anything about the account itself (user id, credentials, Kongregate),
  the guild and other players (they belong to the official server), and the store.
  strip_secrets drops the account's credentials before anything is read or kept: the
  reply's `request` echo repeats them.
- **Objectives** (ours: the login doesn't say which were finished): the real server paid
  a permanent objective when it was done, so the ones before each listed (open) one in
  its chain count as done, and so do the ones whose goal the save already meets; the
  progress the listed ones show is kept as the counts behind them. So nothing pays twice.

Standard library only.
"""

from __future__ import annotations

import base64
import json
import re

from rules import achievements
from rules.player_state import DEFAULT_MAX_ENERGY, Player, player_from_start

SECRET_KEYS = {"request", "password", "new_password", "syncode", "new_syncode", "hash", "client_signature",
               "udid", "kong_id", "kong_name", "kong_token", "kongregate_id", "kongregate_token"}
COUNT_TYPES = {1: "upgrade_level", 3: "fusions", 5: "salvage"}   # the permanent objectives counted in the save
INIT_CALL = re.compile(r"(?:^|[?&])message=init(?:&|$)")


def strip_secrets(value):
    """The data without the account's credentials anywhere in it (a copy)."""
    if isinstance(value, dict):
        return {k: strip_secrets(v) for k, v in value.items() if k not in SECRET_KEYS}
    if isinstance(value, list):
        return [strip_secrets(v) for v in value]
    return value


def login_from_har(har: dict) -> dict:
    """The last `init` reply in a HAR file (a browser's recording of a session).
    ValueError if it has none."""
    found = []
    for entry in (har.get("log") or {}).get("entries") or []:
        request = entry.get("request") or {}
        url = str(request.get("url") or "")
        posted = str((request.get("postData") or {}).get("text") or "")
        if not (INIT_CALL.search(url.split("?", 1)[-1]) or INIT_CALL.search(posted)):
            continue
        content = (entry.get("response") or {}).get("content") or {}
        text = content.get("text") or ""
        if content.get("encoding") == "base64":
            text = base64.b64decode(text).decode("utf-8", "replace")
        try:
            body = json.loads(text)
        except ValueError:
            continue
        if isinstance(body, dict) and isinstance(body.get("user_data"), dict):
            found.append(body)
    if not found:
        raise ValueError("the recording has no login data (no init reply with user_data)")
    return found[-1]


def player_from_login(body: dict, now: int, defs: dict | None = None,
                      default_max_energy: int = DEFAULT_MAX_ENERGY) -> Player:
    """A player from the official game's login data. defs: the objectives
    (achievements.load), to tell which were done; without them none are."""
    body = strip_secrets(body)
    if not isinstance(body.get("user_data"), dict):
        raise ValueError("there's no user_data in it")
    player = player_from_start(body, now, default_max_energy)
    player.created_from = {}
    for cid, card in (body.get("user_cards") or {}).items():
        if isinstance(card, dict) and str(card.get("is_locked", "0")) not in ("0", "", "False", "false") \
                and str(cid) in player.cards:
            player.cards[str(cid)]["is_locked"] = 1
    player.items = items_from_login(body.get("user_items"))
    if defs:
        objectives_from_login(defs, body.get("user_achievements"), player)
    return player


def items_from_login(user_items) -> dict[str, int]:
    """user_items ({id: {"id", "number"}}, or [] with none) as the save keeps them."""
    entries = user_items.values() if isinstance(user_items, dict) else user_items or []
    out = {}
    for e in entries:
        if isinstance(e, dict) and str(e.get("id", "")).isdigit() and int(e.get("number") or 0) > 0:
            out[str(int(e["id"]))] = out.get(str(int(e["id"])), 0) + int(e["number"])
    return out


def objectives_from_login(defs: dict, listed, player: Player) -> None:
    """Which permanent objectives were done, and the counts behind them (see the module's
    notes): written into the player's objectives state."""
    s = achievements.state(player)
    entries = list(listed.values()) if isinstance(listed, dict) else list(listed or [])
    shown = set()
    for e in entries:
        try:
            aid, progress = int(e["achievement_id"]), int(e.get("progress") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        a = defs.get(aid)
        if a is None or not a.permanent:
            continue
        shown.add(aid)
        key = COUNT_TYPES.get(a.type)
        if key:
            s["counts"][key] = max(s["counts"].get(key, 0), progress)
    done = set(s["done"])
    for aid in shown:
        before = defs[aid].after
        while before is not None and before in defs and before not in done:
            done.add(before)
            before = defs[before].after
    for a in defs.values():
        if a.permanent and a.id not in shown and achievements.progress(a, player) >= a.goal:
            done.add(a.id)
    s["done"] = sorted(done)
