"""Recorded battles put back together, and replayed through the engine to compare.

A recorded battle is spread over its start reply and one reply per card played;
each reply carries only the turns that changed (earlier ones come as []). Stitched
together they give the whole battle: both decks (the player's in `attack_deck`, the
enemy's cards as `card_map` reveals them when played), every turn's events, and the
player's choices.

`compare()` runs the engine with the same decks and the same choices and reports
the first place the two differ, and whether that's a skill the engine doesn't have
yet (expected while skills are being built) or a real difference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .cards import ASSAULT, Card, CardDB
from .engine import FIRST_UID, Battle, BattleError, RandomChooser, Setup

BATTLE_STARTS = ("startMission", "startHuntingBattle")


@dataclass
class RecordedBattle:
    name: str
    start: dict                                   # the start reply's battle_data
    turns: dict[int, dict] = field(default_factory=dict)
    plays: list[int] = field(default_factory=list)  # the player's card per playCard
    card_map: dict[int, int] = field(default_factory=dict)
    winner: int | None = None


def stitch(calls) -> list[RecordedBattle]:
    """calls: (message, request params, reply body) in recorded order."""
    battles, cur = [], None
    for message, params, body in calls:
        bd = (body or {}).get("battle_data")
        if message in BATTLE_STARTS and isinstance(bd, dict):
            what = f"mission {params.get('mission_id')}" if message == "startMission" else \
                f"arena {params.get('target_user_id')}"
            cur = RecordedBattle(name=what, start=bd)
            battles.append(cur)
        elif message == "playCard" and cur is not None and isinstance(bd, dict):
            cur.plays.append(int(params.get("card_uid")))
        else:
            continue
        t = bd.get("turn") or {}
        for k, v in (t.items() if isinstance(t, dict) else enumerate(t)):
            if isinstance(v, dict) and v:
                cur.turns[int(k)] = v
        for uid, cid in (bd.get("card_map") or {}).items():
            cur.card_map[int(uid)] = int(cid)
        if bd.get("winner") is not None:
            cur.winner = int(bd["winner"])
    return battles


def from_fixtures(replay_json: Path) -> list[RecordedBattle]:
    """The scrubbed replay library (game/fixtures/replay.json)."""
    calls = json.loads(Path(replay_json).read_text("utf-8"))["calls"]
    return stitch((c["message"], c["key"], c["body"]) for c in calls)


def capture_calls(session_dir: Path) -> list[tuple[str, dict, dict | None]]:
    """A recorded session folder (captures/api/<session>) as (message, request params,
    reply body), in order."""
    calls = []
    for f in sorted(Path(session_dir).glob("*.json")):
        d = json.loads(f.read_text("utf-8"))
        req = d.get("request") or {}
        params = {**(req.get("query") or {}), **(req.get("form") or {})}
        body = ((d.get("response") or {}).get("body") or {}).get("json")
        calls.append((d.get("message"), params, body))
    return calls


def from_capture(session_dir: Path) -> list[RecordedBattle]:
    """A recorded session folder (captures/api/<session>)."""
    return stitch(capture_calls(session_dir))


# ---- replaying through the engine ----------------------------------------------------------------

UNKNOWN = Card(id=0, base_id=0, name="(never played)", type=ASSAULT, faction=0, rarity=0, set=0, level=0,
               fusion_level=0, attack=0, health=1, delay=0)


def setup_of(rec: RecordedBattle, db: CardDB) -> Setup:
    s = rec.start
    attacker = [(int(uid), db[cid]) for uid, cid in sorted(s["attack_deck"].items(), key=lambda kv: int(kv[0]))]
    defender = []
    for uid in range(FIRST_UID[1], FIRST_UID[1] + int(s.get("eds") or s.get("enemy_size") or 0)):
        cid = rec.card_map.get(uid)
        defender.append((uid, db[cid] if cid is not None else UNKNOWN))
    return Setup(db[s["attack_commander"]], attacker, db[s["defend_commander"]], defender)


def _norm(value):
    """Uids compared as text: the server mixes "1" and 1."""
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    return str(value)


def _skill_in(entry) -> str | None:
    """The skill a recorded event needs, if it isn't a plain attack."""
    if not isinstance(entry, dict):
        return None
    if entry.get("skill_id") not in (0, "0", None):
        return str(entry["skill_id"])
    for b in entry.get("block") or []:
        return str(b.get("skill"))
    for st in entry.get("status") or []:
        if st.get("status_flag") != "h":
            return str(st.get("status_flag"))
    return None


@dataclass
class Comparison:
    name: str
    turns: int                       # recorded turns
    matched_turns: int               # turns identical from the start
    difference: str | None = None    # the first difference, described
    needs_skill: str | None = None   # set if the difference is a skill not built yet
    ignored_skills: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.difference is None


class ScriptedChooser:
    """Random picks taken from the recording: for a skill used by a card, the targets of
    the next recorded action by that card with that skill in the same turn. Falls back
    to a random pick when the recording has none (the battles have already parted)."""

    def __init__(self, rec: RecordedBattle):
        self.rec = rec
        self.used: set[tuple[int, str, int]] = set()
        self.fallback = RandomChooser()

    def __call__(self, battle, actor, skill_id, candidates, n):
        turn = self.rec.turns.get(battle.turn, {})
        actions = [a for sec in ("upkeep", "actions") for a in (turn.get(sec) or []) if isinstance(a, dict)]
        for i, a in enumerate(actions):
            key = (battle.turn, skill_id, i)
            if key in self.used or str(a.get("card_uid")) != str(actor.uid) or a.get("skill_id") != skill_id:
                continue
            self.used.add(key)
            wanted = {int(t) for t in a.get("targets") or []}
            picked = [c for c in candidates if c.uid in wanted]
            if len(picked) == min(n, len(candidates)):
                return picked
            break
        return self.fallback(battle, actor, skill_id, candidates, n)


def compare(rec: RecordedBattle, db: CardDB) -> Comparison:
    battle = Battle(setup_of(rec, db), chooser=ScriptedChooser(rec))
    got: dict[int, dict] = {}
    try:
        got.update(battle.start())
        for uid in rec.plays:
            if battle.over:
                break
            got.update(battle.play(uid))
    except BattleError as e:
        return Comparison(rec.name, len(rec.turns), 0, f"the engine refused a recorded move: {e}")
    last = max(rec.turns) if rec.turns else 0
    result = Comparison(rec.name, len(rec.turns), 0, ignored_skills=tuple(sorted(battle.ignored_skills)))
    for t in range(1, max(last, max(got, default=0)) + 1):
        want, have = rec.turns.get(t, {}), got.get(t, {})
        if _norm(want) == _norm(have):
            result.matched_turns += 1
            continue
        for section in list(want) + [s for s in have if s not in want]:
            w, h = want.get(section), have.get(section)
            if _norm(w) == _norm(h):
                continue
            w, h = w or [], h or []
            for i in range(max(len(w), len(h))):
                we, he = (w[i] if i < len(w) else None), (h[i] if i < len(h) else None)
                if _norm(we) != _norm(he):
                    result.difference = f"turn {t} {section}[{i}]: recorded {json.dumps(we)}, engine {json.dumps(he)}"
                    result.needs_skill = _skill_in(we) or _skill_in(he)
                    if result.needs_skill is None:      # a plain event differs: was it caused by a skill earlier this turn?
                        for e in (want.get("actions") or [])[:i if section == "actions" else None]:
                            result.needs_skill = result.needs_skill or _skill_in(e)
                    return result
    if rec.winner is not None and battle.winner != rec.winner:
        result.difference = f"recorded winner {rec.winner}, engine {battle.winner}"
    return result
