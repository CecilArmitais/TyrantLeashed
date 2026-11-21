"""Mission rewards: what a finished mission pays into the save, and the player's
`rewards` entry that the victory or defeat screen shows.

The rules, from the recorded missions (docs/protocol.md, "Mission rewards"):

- Gold: the mission's <gold> times the mission gold bonus (settings.py: 7 by default,
  as every recorded mission paid under the game's "Sunset Gold Bonus" event; it can be
  changed or switched off). Every win pays it, repeat wins included (mission 5: 98
  the first time and 98 again).
- A loss pays `loss_gold_mult` (0.25) of that: mission 7 paid 28 of 112.
- Cards: the mission's <card> rewards, on the first win only. A prologue mission's
  card (location 0, missions 1-5) also goes into the active deck: each of those did,
  and the cards of later missions never did, not even mission 9's into the account's
  6-card deck. (We also keep the deck to 10 cards; the prologue never gets that far.)
- Stars: star 1 for winning, stars 2 and 3 once the mission has been won 5 and 10
  times (<req type='5' compare='great_equal' amount='5'/>; the account's captures have
  missions won 10 times with both). Each win lists every star it meets, the way the
  recorded repeat wins list star 1 again.

Not seen in any recording, so these are our reading of the login values:
- `star2_reward` (100) and `star3_reward` (200) are paid as gold the first time the
  star is earned.
- `already_gold_mult` (0.5) applies once the mission has all three stars. (It isn't
  repeat wins: those paid in full.)
- Fractions are rounded to the nearest (halves up), as the arena's losses were: 19
  for 75 x 0.25.

Missions give no XP: the recorded level-up came from an arena win (the level follows
the arena rating), so levels belong with the arena.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from player_state import Player, deck_size

DECK_SIZE = 10          # cards in a deck, besides the commander
PROLOGUE = 0            # the location whose reward cards go into the deck


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RewardRules:
    gold_multiplier: int = 1                  # the mission gold bonus in use (settings.py)
    loss_mult: float = 0.25
    mastered_mult: float = 0.5
    star_gold: tuple[tuple[str, int], ...] = (("2", 100), ("3", 200))

    @classmethod
    def from_start(cls, start: dict) -> "RewardRules":
        """The rules from the login data (init). The gold multiplier isn't one of them:
        it's the server's setting (settings.py)."""
        return cls(loss_mult=_num(start.get("loss_gold_mult"), 0.25),
                   mastered_mult=_num(start.get("already_gold_mult"), 0.5),
                   star_gold=(("2", int(_num(start.get("star2_reward"), 100))),
                              ("3", int(_num(start.get("star3_reward"), 200)))))


@dataclass
class MissionResult:
    reward: dict                    # the player's entry in battle_data.rewards
    gold: int = 0                   # all gold paid, star gold included
    cards: list[str] = field(default_factory=list)
    new_stars: list[str] = field(default_factory=list)    # "2"/"3" earned by this win
    first_win: bool = False
    deck_changed: bool = False

    def summary(self) -> str:
        parts = [f"{self.gold} gold"]
        if self.cards:
            parts.append("card " + ", ".join(self.cards) + (" (into the deck)" if self.deck_changed else ""))
        if self.new_stars:
            parts.append("star " + ", ".join(self.new_stars))
        return ", ".join(parts)


def mission_gold(mission, player: Player, rules: RewardRules, now: int, won: bool) -> int:
    done = (player.missions or {}).get(str(mission.id)) or {}
    gold = mission.gold * rules.gold_multiplier
    if done.get("star3"):
        gold *= rules.mastered_mult
    if not won:
        gold *= rules.loss_mult
    return math.floor(gold + 0.5)


def finish_mission(player: Player, mission, won: bool, rules: RewardRules, now: int, user_id,
                   deck_id: str | None = None) -> MissionResult:
    """Pays a finished mission into `player` (gold, cards, the win and its stars) and
    returns what was paid. The caller saves the player."""
    mid = str(mission.id)
    gold = mission_gold(mission, player, rules, now, won)
    reward = {"user_id": str(user_id), "gold": gold}
    result = MissionResult(reward)
    if won:
        before = (player.missions or {}).get(mid) or {}
        result.first_win = not before.get("complete")
        wins = int(before.get("number") or 0) + 1
        met = ["1"] + [s for s, need in mission.star_wins if wins >= need]
        result.new_stars = [s for s in met if s in ("2", "3") and not before.get("star" + s)]
        gold += sum(g for s, g in rules.star_gold if s in result.new_stars)
        if result.first_win:
            deck = (player.decks or {}).get(str(deck_id)) \
                if deck_id is not None and mission.location == PROLOGUE else None
            for cid in mission.reward_cards:
                player.add_card(cid)
                result.cards.append(str(cid))
                if deck is not None and deck_size(deck) < DECK_SIZE:
                    deck["cards"][str(cid)] = deck["cards"].get(str(cid), 0) + 1
                    result.deck_changed = True
        player.won_mission(mid, met)
        reward["gold"] = gold
        if result.cards:
            reward["card"] = result.cards
        reward["star"] = {s: True for s in met}
    player.gold += gold
    player.gold_earned = (player.gold_earned or 0) + gold
    result.gold = gold
    return result
