"""Objectives (achievements.py): the chains, dailies, events and their drops.
Part of GameApi (core.py), as AchievementCalls."""

from __future__ import annotations

import achievements
import items

from .messages import CLAIM


class AchievementCalls:
    """GameApi's part for objectives (achievements.py): the chains, dailies, events and
    their drops."""

    def objectives_report(self) -> str:
        """The objective challenge content.toml sets, if any."""
        c, defs = self.content(), self._achievement_defs()
        members = achievements.challenge_set(defs, c.challenge) if c.challenge else frozenset()
        if not members:
            return ("Objective challenge: none" + (f" (objective {c.challenge} isn't a dated one)" if c.challenge else "")
                    + "; mutant mission levels and the undated Legendary Quests are always on")
        root = defs[next((x for x in sorted(members) if defs[x].type == 12), c.challenge)]
        length = achievements.challenge_length(defs, c.challenge)
        return (f"Objective challenge: {root.name} ({root.id}), {len(members)} objectives, "
                + (f"{length // 86400}-day runs" if length else "no end"))

    def _achievement_defs(self) -> dict:
        if getattr(self, "_ach_defs", None) is None:
            self._ach_defs = achievements.load(self._assets)
        return self._ach_defs

    def _events(self) -> achievements.EventContext:
        """The event objectives on now: the standing ones, and content.toml's challenge
        (its run, server-wide)."""
        defs = self._achievement_defs()
        if getattr(self, "_standing", None) is None:
            self._standing = achievements.standing_events(defs)
        run = self.challenge_store.current(defs, self.content().challenge, int(self.clock()))
        challenge = achievements.challenge_set(defs, run["id"]) if run else frozenset()
        return achievements.EventContext(self._standing | challenge, challenge, run)

    def _fight_drop(self, battle_type: int, won: bool, level: int | None = None):
        """A one-reply fight's reward `item`: what it dropped, saved to the player."""
        player = self.player()
        gains = self._event_drops(player, battle_type, won, level=level)
        if gains:
            self._track(player)
            self.player_store.save(player)
        return achievements.reward_item(gains)

    def _event_drops(self, player, battle_type: int, won: bool, mutant: bool = False,
                     level: int | None = None) -> dict:
        """The items a won battle drops for the item objectives counting now, added to the
        inventory: {item id: n}."""
        gains = achievements.drops(self._achievement_defs(), player, battle_type, won, self.rng_factory(),
                                   self._events(), mutant, level)
        for iid, n in gains.items():
            items.add(player.items, iid, n)
        if gains:
            self._ach_changed = True
        return gains

    def _track(self, player) -> list:
        """Finishes (and pays) every permanent achievement the player has now reached; the
        reply then carries them. Returns the ones finished."""
        done = achievements.complete_ready(self._achievement_defs(), player, self._cap_sp(), self._events())
        self._ach_news = getattr(self, "_ach_news", []) + done
        self._ach_changed = True
        return done

    def _achievement_list(self, player) -> dict:
        """user_achievements now: a fresh set of daily objectives if the last ran out, and
        anything reached meanwhile finished (an older save's missions and rating)."""
        defs = self._achievement_defs()
        changed = achievements.ensure_dailies(defs, player, int(self.clock()), self.rng_factory())
        if self._track(player) or changed:
            self.player_store.save(player)
        return achievements.user_achievements(defs, player, self._events())

    def _claim_achievement(self, params: dict) -> tuple[dict, str]:
        """completeAchievement achievement_id: the Complete button of a finished daily
        objective; pays it."""
        player = self.player()
        try:
            a = achievements.claim(self._achievement_defs(), player, params.get("achievement_id"), self._cap_sp(),
                                   int(self.clock()), self._events())
        except ValueError as e:
            reply = self._refuse(params, str(e))
            return reply, f"refused {CLAIM}: {e}"
        self._ach_news, self._ach_changed = [a], True
        self._track(player)                 # what it finishes in turn (a challenge's parent, a count)
        self.player_store.save(player)
        start = {"time": self._start.get("time"), "user_data": self._start.get("user_data") or {}, "user_cards": {}}
        reply = self.envelope(params)
        reply["result"] = True
        reply.update({k: v for k, v in self._prepare(start, params).items() if k in ("user_data", "user_cards")})
        return reply, f"claimed {a.name} ({a.desc})"
