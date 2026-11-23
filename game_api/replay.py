"""Replaying the recordings: matching requests to recorded replies, moving recorded times to
now, putting the player in, and encoding replies. The API's replayed battles are ReplayCalls
(replay_calls.py)."""

from __future__ import annotations

import hashlib
import json

from .base import OFFLINE_USER_ID


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
