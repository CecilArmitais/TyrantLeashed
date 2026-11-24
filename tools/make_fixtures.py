"""Build the offline server's starting data from a recorded session.

Finds a recorded `getUserAccount` and the `init` that followed it, plus every call
after them, replaces every account identifier (user id, password, syncode, name,
device id, Kongregate id and token) with the offline player's, drops the `request`
echo (which repeats the credentials), checks that none of the originals are left
anywhere, and writes to game/fixtures/:
  getUserAccount.json, init.json   the login, replayed with times moved to now
  replay.json                      the session's later calls (battles, flags, store...),
                                   replayed when the client asks the same thing

Sessions where the game was signed in to Kongregate are skipped: that is a real
account.

It works from the raw recordings (captures/api) or from the workspace's sanitized
copy (captures/api-sanitized, written by capture/sanitize_recordings.py), whose
identifiers are already the offline player's: those are left as they are, and the
account fingerprint comes from the copy's sessions.json. Without --session it tries
the raw sessions first, newest first, then the sanitized ones. Standard library only.
From the Tyrant folder:

    python server/tools/make_fixtures.py                      # newest suitable session
    python server/tools/make_fixtures.py --session captures/api/20261120-012700
    python server/tools/make_fixtures.py --session captures/api-sanitized/20261120-012700
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the server folder
from common.paths import CAPTURES_DIR, FIXTURE_DIR  # noqa: E402
from game_api import (NOT_REPLAYED, OFFLINE_NAME, OFFLINE_PASSWORD,  # noqa: E402
                      OFFLINE_SYNCODE, OFFLINE_USER_ID, VOLATILE_PARAMS)
KONG_PARAMS = ("kong_id", "kong_token", "kong_name")
RECORDING_DIRS = ("api", "api-sanitized")   # under --captures: raw recordings, then the sanitized copy
SANITIZED_INDEX = "sessions.json"           # the sanitized copy's list of sessions and their accounts
MIN_SECRET_LEN = 4  # shorter values are too likely to match unrelated data


class FixtureError(Exception):
    pass


def _params(rec: dict) -> dict:
    req = rec.get("request") or {}
    out = {}
    for src in (req.get("query") or {}, req.get("form") or {}):
        for k, v in src.items():
            out[k] = v[-1] if isinstance(v, list) else v
    return out


def _body(rec: dict) -> dict:
    body = ((rec.get("response") or {}).get("body") or {}).get("json")
    if not isinstance(body, dict):
        raise FixtureError(f"call #{rec.get('seq')} has no JSON response")
    return body


def find_pair(session: Path, allow_kongregate: bool = False) -> tuple[dict, dict]:
    """The first getUserAccount in the session and the first init after it."""
    rows = sorted((json.loads(l) for l in (session / "index.jsonl").read_text("utf-8").splitlines() if l.strip()),
                  key=lambda r: r["seq"])
    gua = next((r for r in rows if r["message"] == "getUserAccount" and r.get("status") == 200), None)
    if gua is None:
        raise FixtureError(f"{session.name}: no getUserAccount")
    init = next((r for r in rows if r["message"] == "init" and r["seq"] > gua["seq"] and r.get("status") == 200), None)
    if init is None:
        raise FixtureError(f"{session.name}: no init after getUserAccount")
    recs = [json.loads((session / r["file"]).read_text("utf-8")) for r in (gua, init)]
    if not allow_kongregate and any(_params(r).get(k) for r in recs for k in KONG_PARAMS):
        raise FixtureError(f"{session.name}: signed in to Kongregate (a real account); not used")
    return recs[0], recs[1]


def secrets_of(*recs: dict) -> dict[str, object]:
    """Every account identifier in these calls, mapped to its offline replacement."""
    found: dict[str, object] = {}

    def add(value, replacement):
        if value is None or isinstance(value, (dict, list, bool)):
            return
        s = str(value).strip()
        if len(s) >= MIN_SECRET_LEN and s != str(replacement):    # already the offline player's: nothing to hide
            found[s] = replacement

    for rec in recs:
        p = _params(rec)
        add(p.get("user_id"), OFFLINE_USER_ID)
        add(p.get("password"), OFFLINE_PASSWORD)
        add(p.get("syncode"), OFFLINE_SYNCODE)
        for k in ("udid", "kong_id", "kong_token", "hash", "client_signature"):
            add(p.get(k), "")
        add(p.get("kong_name"), OFFLINE_NAME)
        try:
            b = _body(rec)
        except FixtureError:  # e.g. a 504 HTML page: parameters only
            continue
        add(b.get("new_user"), OFFLINE_USER_ID)
        add(b.get("new_password"), OFFLINE_PASSWORD)
        add(b.get("new_syncode"), OFFLINE_SYNCODE)
        add(b.get("new_name"), OFFLINE_NAME)
        ud = b.get("user_data") or {}
        add(ud.get("user_id"), OFFLINE_USER_ID)
        add(ud.get("name"), OFFLINE_NAME)
    return found


def scrub(obj, secrets: dict[str, object]):
    """Replace secrets: whole values keep their type; inside longer strings, as text."""
    if isinstance(obj, dict):
        return {k: scrub(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, secrets) for v in obj]
    if isinstance(obj, bool) or obj is None:
        return obj
    s = str(obj)
    if s in secrets:
        new = secrets[s]
        if isinstance(obj, int) and isinstance(new, int):
            return new
        return str(new)
    if isinstance(obj, str):
        for secret, new in sorted(secrets.items(), key=lambda kv: -len(kv[0])):
            if secret in obj:
                obj = obj.replace(secret, str(new))
    return obj


def account_fingerprint(session: Path, gua: dict) -> str:
    """sha256 of the recorded account's user id, first 8 hex digits. A sanitized
    session holds the offline id instead, so its fingerprint comes from the copy's
    sessions.json ("sanitized" if that doesn't say)."""
    uid = str(_params(gua).get("user_id") or _body(gua).get("new_user"))
    if uid != str(OFFLINE_USER_ID):
        return hashlib.sha256(uid.encode()).hexdigest()[:8]
    try:
        index = json.loads((session.parent / SANITIZED_INDEX).read_text("utf-8"))
        accounts = index["sessions"][session.name]["accounts"]
    except (OSError, ValueError, KeyError, TypeError):
        accounts = []
    return accounts[0] if len(accounts) == 1 and isinstance(accounts[0], str) else "sanitized"


def session_calls(session: Path) -> list[dict]:
    rows = sorted((json.loads(l) for l in (session / "index.jsonl").read_text("utf-8").splitlines() if l.strip()),
                  key=lambda r: r["seq"])
    return [json.loads((session / r["file"]).read_text("utf-8")) for r in rows]


def replay_entries(calls: list[dict], after_seq: int, secrets: dict) -> list[dict]:
    """Every later call worth replaying: what was asked (minus volatile and account
    parameters) and what the server answered (minus the request echo)."""
    out = []
    for rec in calls:
        if rec.get("seq", 0) <= after_seq or rec.get("message") in NOT_REPLAYED:
            continue
        if (rec.get("response") or {}).get("status") != 200:
            continue
        try:
            body = _body(rec)
        except FixtureError:
            continue
        key = {k: v for k, v in _params(rec).items() if k not in VOLATILE_PARAMS}
        out.append({"seq": rec["seq"], "message": rec["message"], "key": scrub(key, secrets),
                    "body": scrub({k: v for k, v in body.items() if k != "request"}, secrets)})
    return out


def build(session: Path, out_dir: Path = FIXTURE_DIR, allow_kongregate: bool = False) -> dict:
    gua, init = find_pair(session, allow_kongregate)
    calls = session_calls(session)
    if not allow_kongregate and any(_params(r).get(k) for r in calls for k in KONG_PARAMS):
        raise FixtureError(f"{session.name}: signed in to Kongregate (a real account); not used")
    secrets = secrets_of(gua, init, *calls)
    fingerprint = account_fingerprint(session, gua)
    source = {"session": session.name, "account": fingerprint}
    written = {}
    for name, rec in (("getUserAccount", gua), ("init", init)):
        body = {k: v for k, v in _body(rec).items() if k != "request"}
        written[name] = {"source": {**source, "seq": rec["seq"]}, "body": scrub(body, secrets)}
    written["replay"] = {"source": source, "calls": replay_entries(calls, init["seq"], secrets)}
    for name, fx in written.items():
        text = json.dumps(fx, ensure_ascii=False)
        leftovers = [s for s in secrets if s in text]
        if leftovers:
            raise FixtureError(f"{name}: {len(leftovers)} identifier(s) survived scrubbing; nothing written")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, fx in written.items():
        (out_dir / f"{name}.json").write_text(json.dumps(fx, ensure_ascii=False, indent=1), "utf-8")
    return {"session": session.name, "account": fingerprint, "replaced": len(secrets),
            "replay_calls": len(written["replay"]["calls"]),
            "files": [str(out_dir / f"{n}.json") for n in written]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="session folder (default: newest one that qualifies)")
    ap.add_argument("--captures", default=str(CAPTURES_DIR))
    ap.add_argument("--out", default=str(FIXTURE_DIR))
    ap.add_argument("--allow-kongregate", action="store_true",
                    help="also accept sessions signed in to Kongregate (a real account)")
    args = ap.parse_args()

    if args.session:
        candidates = [Path(args.session)]
    else:
        candidates = []
        for name in RECORDING_DIRS:
            api = Path(args.captures) / name
            if api.is_dir():
                candidates += sorted((d for d in api.iterdir() if d.is_dir()), reverse=True)
    reasons = []
    for session in candidates:
        try:
            info = build(session, Path(args.out), args.allow_kongregate)
        except (FixtureError, OSError, ValueError) as e:
            reasons.append(str(e))
            continue
        print(f"Fixtures built from session {info['session']} (account {info['account']}); "
              f"{info['replaced']} identifiers replaced; {info['replay_calls']} later calls kept for replay.")
        for f in info["files"]:
            print("  ", f)
        return
    sys.exit("No suitable session found:\n  " + "\n  ".join(reasons or ["no sessions"]))


if __name__ == "__main__":
    main()
