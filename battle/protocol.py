"""A battle as the game client gets it: the `battle_data` of the start reply and of
each playCard reply. The fields and their types copy the recorded replies (see the
Battles section of docs/protocol.md)."""

from __future__ import annotations

from .engine import ATTACKER, DEFENDER, Battle

NO_BATTLEGROUND_EFFECTS = {"global": [], "attacker": [], "defender": []}


class BattleSession:
    """One battle in progress on the server, and the replies describing it."""

    def __init__(self, battle: Battle, battle_id: int, host_id: str, mission_id: str | None = None,
                 extra: dict | None = None, host_is_attacker: bool = True):
        """extra: fields every reply of this battle carries on top (an arena fight's
        enemy_id, enemy_name and battleground fields).

        host_is_attacker False: the player is the battle's defender, who moves second
        (a surge fight, run with the enemy as the attacker). The client then
        takes the even turns as the player's (BattleUIController.get_IsPlayerTurn), and
        `winner` stays the player's own result (ServiceCombatData.Map reads it as
        IsWinner)."""
        self.battle = battle
        self.host_is_attacker = host_is_attacker
        self.battle_id = battle_id
        self.host_id = str(host_id)
        self.mission_id = mission_id
        self.extra = extra or {}

    def _base(self) -> dict:
        b = self.battle
        att, dfn = b.sides[ATTACKER], b.sides[DEFENDER]
        att_plays = len(att.played)
        host, enemy = (att, dfn) if self.host_is_attacker else (dfn, att)
        return {
            "host_is_attacker": self.host_is_attacker,
            "battle_id": self.battle_id,
            "host_id": self.host_id,
            "kv_store": None,
            "attack_commander": str(att.commander.card.id),
            "attack_deck": {str(uid): str(card.id) for uid, card in att.cards.items()},
            "defend_commander": str(dfn.commander.card.id),
            "hds": len(host.cards),
            "eds": len(enemy.cards),
            # The recorded values: 10 at the start, +10 per card played, at most 50.
            "attack_power": 10 + 10 * min(att_plays, 4),
            "upkept": True,
            "enemy_id": None,
            "player_size": len(host.cards),
            "enemy_size": len(enemy.cards),
            **self.extra,
        }

    def start_data(self, turns: dict[int, dict]) -> dict:
        data = self._base()
        data["turn"] = {str(t): v for t, v in turns.items()}
        return data

    def turn_data(self, turns: dict[int, dict], rewards: list | None = None, end_time: int | None = None) -> dict:
        """After a card is played: every turn so far, the ones before this reply's
        first turn as [] (the real server sends only what changed)."""
        b = self.battle
        data = self._base()
        data["card_map"] = {str(uid): str(cid) for uid, cid in b.played}
        data["defend_power"] = 10 * min(len(b.sides[DEFENDER].played), 4)
        data.setdefault("battleground_effects", NO_BATTLEGROUND_EFFECTS)
        if b.over:
            data["end_time"] = end_time
            data["winner"] = b.winner if self.host_is_attacker else int(b.winner == 0)
            if self.mission_id is not None:
                data["mission_id"] = str(self.mission_id)
            data["rewards"] = rewards or []
            data["upkept"] = False
        data["turn"] = {str(t): turns.get(t, []) for t in range(1, b.turn + 1)}
        return data
