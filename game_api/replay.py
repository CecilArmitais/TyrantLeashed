"""Replaying the recordings: matching requests to recorded replies, moving recorded times to
now, putting the player in, and encoding replies; and the API's replayed battles."""

from __future__ import annotations

import hashlib
import json

from .base import NotSupported, OFFLINE_USER_ID
from .messages import ARENA_START, BATTLE_STARTS, BATTLE_TURN, REVENGE_START


# Request parameters that say nothing about *what* is being asked: credentials,
# client bookkeeping, and per-battle ids. Replay matches on everything else.
VOLATILE_PARAMS = frozenset({
    "message", "user_id", "password", "syncode", "udid", "kong_id", "kong_name", "kong_token",
    "hash", "client_signature", "client_time", "timestamp", "unity", "client_version", "device_type",
    "os_version", "platform", "data_usage", "api_stat_name", "api_stat_time", "client_events", "dummy",
    "battle_id", "host_id",
})
NOT_REPLAYED = frozenset({"getUserAccount", "init", "recordEvent"})  # handled without the replay library


SHIFT_WINDOW_S = 365 * 86400       # timestamps this close to the recorded server time are "live"
NO_SHIFT_UNDER = ("card_files",)   # file version stamps, not live times


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


class ReplayCalls:
    """GameApi's part for answering from the recordings: the replay library and replayed battles."""

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

    @property
    def recorded_battles(self) -> dict[str, list[int]]:
        """Battle name -> number of card plays in each recording of it."""
        return {scripts[0]["name"]: [len(s["plays"]) for s in scripts] for scripts in self.scripts.values()}

    def _last_recorded(self, message: str, params: dict) -> dict | None:
        """The last recorded reply to `message` (one without user_decks, if there's one),
        brought up to now; None when it wasn't recorded. The store answers from these."""
        recorded = [b for (m, _), bodies in self.replays.items() if m == message for b in bodies]
        plain = [b for b in recorded if "user_decks" not in b] or recorded
        return self._prepare(plain[-1], params) if plain else None

    def _start_battle(self, message: str, params: dict) -> tuple[dict, str]:
        if self.battle_engine and message == "startMission":
            return self._start_engine_battle(params)
        if self.battle_engine and message == ARENA_START and self.player() is not None:
            return self._start_arena_battle(params)
        if self.battle_engine and message == REVENGE_START and self.player() is not None:
            return self._start_revenge(params)
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
        # A practice fight's playCard carries the deck flag (fight_attack_deck) as its
        # battle_id: the client keeps both in the same field (CombatManager +32).
        if params.get("battle_id") and str(params["battle_id"]) != b["id"] and not b.get("practice"):
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
