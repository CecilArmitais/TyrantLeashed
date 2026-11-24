"""The crate on the home screen (the game's "daily bonus"): it opens once its timer runs
out, and gives a reward. docs/protocol.md, "The daily crate", has the evidence.

- Found in the client (RewardBox, RewardManager): every reply's `daily_bonus_time`
  says when the crate is ready; tapping it then plays "crateopen" and sends
  useDailyBonus. The reply is read for `bonus_result` {"bonus": {money, tokens, card,
  salvage}, "rewards": [...]}, `user_data`, `user_cards` and `new_cards` (a card shows
  in the new card popup). `daily_bonus` (top level) is the days in a row.
- Recorded: a new account's crate opened 1799 seconds after its first login, with 0
  days in a row. Nothing recorded shows a crate being opened, so what it gives and how
  often are ours, set in content.toml [daily_bonus]: every 24 hours by default, the
  reward picked by the days in a row (the last one repeats). A day is in a row when the
  crate is opened before it has been ready for a whole period.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_EVERY_HOURS = 24


@dataclass(frozen=True)
class Reward:
    gold: int = 0
    war_bonds: int = 0
    sp: int = 0
    card: int | None = None

    def describe(self, card_name=None) -> str:
        parts = [f"{self.gold} gold" if self.gold else "", f"{self.war_bonds} war bonds" if self.war_bonds else "",
                 f"{self.sp} SP" if self.sp else "",
                 (card_name or f"card {self.card}") if self.card else ""]
        return ", ".join(p for p in parts if p) or "nothing"


DEFAULT_REWARDS = (Reward(gold=100), Reward(gold=150), Reward(gold=200), Reward(gold=250), Reward(sp=10),
                   Reward(gold=300), Reward(war_bonds=5))


class NotReady(Exception):
    pass


def ready_in(state: dict, now: int) -> int:
    """Seconds until the crate is ready (0: now)."""
    return max(0, int(state.get("next", 0)) - now)


def open_crate(state: dict, rewards, every_hours: int, now: int) -> tuple[Reward, int]:
    """(the reward, days in a row). The state moves on to the next crate."""
    wait = ready_in(state, now)
    if wait:
        raise NotReady(f"The crate isn't ready yet: {wait // 3600}h {wait % 3600 // 60}m left.")
    period = max(1, int(every_hours)) * 3600
    in_a_row = int(state.get("streak", 0)) and now - int(state.get("next", 0)) < period
    streak = int(state.get("streak", 0)) + 1 if in_a_row else 1
    rewards = tuple(rewards) or DEFAULT_REWARDS
    reward = rewards[min(streak, len(rewards)) - 1]
    state.update(next=now + period, streak=streak, last=now)
    return reward, streak


def pay(reward: Reward, player, cap_sp: int) -> None:
    player.gold += reward.gold
    if reward.gold:
        player.gold_earned = (player.gold_earned or 0) + reward.gold
    player.war_bonds += reward.war_bonds
    if reward.sp:
        player.salvage = min(cap_sp, (player.salvage or 0) + reward.sp)
    if reward.card:
        player.add_card(reward.card)


def bonus_result(reward: Reward) -> dict:
    """bonus_result, in the shape the client reads (ServiceBonusResultData)."""
    bonus = {"money": reward.gold, "tokens": reward.war_bonds, "salvage": reward.sp}
    if reward.card:
        bonus["card"] = reward.card
    return {"bonus": bonus, "rewards": []}
