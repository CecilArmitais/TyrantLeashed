"""The API's replayed battles: a recorded battle played back as a script (replay.py has
the matching and time shifting).
Part of GameApi (core.py), as ReplayCalls."""

from __future__ import annotations

from .base import NotSupported
from .messages import ARENA_START, BATTLE_STARTS, BATTLE_TURN, REVENGE_START
from .replay import battle_name, battle_over, NOT_REPLAYED, request_key, with_battle_id


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
