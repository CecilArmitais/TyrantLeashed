"""The arena: rivals, fights, revenge, reports, stamina and Promote.
Part of GameApi (core.py), as ArenaCalls."""

from __future__ import annotations

import achievements
import arena
from battle.engine import ATTACKER, Battle, DEFENDER, FIRST_UID, RandomChooser, Setup, shuffled_deck
from battle.protocol import BattleSession
from player_state import Player

from .base import ARENA_FIELDS, DEFAULT_REFRESH_COST, DEFAULT_STAMINA_REFILL_COST, NotSupported
from .messages import TARGETS


class ArenaCalls:
    """GameApi's part for the arena: rivals, fights, revenge, reports, stamina and Promote."""

    # ---- the arena -----------------------------------------------------------------------

    def _arena_info(self) -> dict:
        """arena_battle_info, in the recorded shape. hunts_fought counts every fight."""
        a = self.player().arena
        return {"battles_fought": "0", "battles_rewarded": 1, "hunts_fought": str(a["hunts_fought"]),
                "hunts_rewarded": 1}

    def _arena_parts(self, params: dict) -> dict:
        """What an arena fight's last reply carries besides battle_data (recorded:
        user_data, arena_battle_info, hunting_targets, the limits)."""
        keys = ("user_data", "max_energy", "max_stamina")
        parts = self._prepare({"time": self._start.get("time"),
                               **{k: self._start[k] for k in keys if k in self._start}}, params)
        out = {k: parts[k] for k in keys if k in parts}
        out["arena_battle_info"] = self._arena_info()
        out["hunting_targets"] = self._targets(self.player())
        return out

    def _player_rival(self, user_id) -> arena.PlayerRival | None:
        """Another player as an arena rival; None if they can't be one: no save, not yet
        past the arena's tutorial fight, or a defense deck the engine can't play."""
        player = self._player_of(user_id)
        a = (player.arena if player is not None else None) or {}
        if player is None or int(a.get("hunting_wins", 0)) < 1:
            return None
        try:
            commander, _ = self._deck_cards(self._defense_deck_of(player), self._engine_data()[0], "the deck")
        except NotSupported:
            return None
        guild = self.guild_store.of_member(int(user_id))
        return arena.PlayerRival(int(user_id), player.name or f"Player{user_id}", int(a["rating"]),
                                 int(a["level"]), commander.id, guild["name"] if guild else None)

    def _arena_candidates(self) -> list[arena.PlayerRival]:
        """Every other player who can be a rival now."""
        found = (self._player_rival(uid) for uid in self.player_ids() if uid != self.uid)
        return [p for p in found if p is not None]

    def _player_rivals(self, player: Player) -> list[arena.PlayerRival]:
        """The other players on the current player's rivals list (picked again when the
        list changes: arena.matched_players), as they are now. None with one player."""
        if self.single_player:
            return []
        c = self.content()
        ids, picked = arena.matched_players(player.arena, self._arena_candidates, self.rng_factory(),
                                            c.arena_player_chance, c.arena_rank_reach)
        if picked:
            self.player_store.save(player)
        found = (self._player_rival(uid) for uid in ids if uid != self.uid)
        return [p for p in found if p is not None]

    def _rivals(self, player: Player) -> list[dict]:
        """The current player's rivals list (arena.py): AI rivals, and other players."""
        return arena.rival_list(player.arena, self._engine_data()[0], self.settings().arena_gold.effective,
                                self._player_rivals(player))

    def _targets(self, player: Player) -> dict:
        return {str(r["user_id"]): r for r in self._rivals(player)}

    def _hunting_targets(self, params: dict) -> tuple[dict, str]:
        """getHuntingTargets: the rivals from the save (arena.py), in the recorded
        getHuntingTargets reply's shape."""
        recorded = next((bodies[0] for (m, _), bodies in self.replays.items() if m == TARGETS), None)
        if recorded is not None:
            reply = self._prepare(recorded, params)
        else:
            reply = self.envelope(params)
            reply["result"] = True
            reply["user_data"] = self.current_user_data(params)
        player = self.player()
        reply["hunting_targets"] = self._targets(player)
        reply["arena_battle_info"] = self._arena_info()
        return reply, f"rivals: {len(reply['hunting_targets'])} (rating {player.arena['rating']})"

    def _start_arena_battle(self, params: dict) -> tuple[dict, str]:
        """startHuntingBattle target_user_id: a real fight, for 1 stamina, against an AI
        rival or another player's defense deck (played by the AI)."""
        player = self.player()
        tid = str(params.get("target_user_id"))
        rival = next((r for r in self._rivals(player) if str(r["user_id"]) == tid), None)
        if rival is None:
            raise NotSupported(f"rival {tid} isn't on your rivals list.")
        return self._fight_rival(params, player, rival)

    def _start_revenge(self, params: dict) -> tuple[dict, str]:
        """startRevengeBattle target_user_id reported_time: a defence report's Revenge
        button. A fight against the attacker's defense deck, like fighting them from the
        rivals list (stamina, gold, rating); each report of a lost defense gives one."""
        player = self.player()
        tid, when = str(params.get("target_user_id")), str(params.get("reported_time"))
        report = arena.defence_report(player.arena, tid, when)
        if report is None or report.get("revenge", arena.NO_REVENGE) != arena.REVENGE_OPEN:
            words = ("There's no fight of theirs against you to take revenge for." if report is None
                     else f"You've already taken revenge on {report['name']} for that fight."
                     if report.get("revenge") == arena.REVENGE_TAKEN
                     else f"Your defense held against {report['name']}: there's nothing to avenge."
                     if report.get("victory")
                     else f"That was a revenge by {report['name']}: it can't be avenged.")
            return self._refuse(params, words), f"refused revenge on {tid}: {words}"
        other = self._player_rival(tid)
        if other is None:
            words = f"{report['name']} can't be fought now (no deck to fight)."
            return self._refuse(params, words), f"refused revenge on {tid}: {words}"
        rival = arena.player_entry(other, player.arena["rating"], self.settings().arena_gold.effective)
        return self._fight_rival(params, player, rival, revenge=report)

    def _fight_rival(self, params: dict, player: Player, rival: dict, revenge: dict | None = None):
        """Starts an arena fight against a rival entry (hunting_targets'), for 1 stamina:
        an AI rival's generated deck, or a player's defense deck, played by the AI.
        revenge: the defence report it avenges (closed once the fight starts)."""
        tid = str(rival["user_id"])
        cards, _ = self._engine_data()
        commander, deck = self._player_deck(cards)
        if int(rival["user_id"]) > 0:                    # another player: their defense deck
            enemy_commander, enemy_deck = self._deck_cards(
                self._defense_deck_of(self._player_of(rival["user_id"])), cards, f"{rival['name']}'s defense deck")
            whose = f"player {tid}'s defense deck"
        else:
            enemy_commander, enemy_deck = arena.rival_deck(rival, cards)
            whose = tid
        a = player.arena
        if a["stamina"] < arena.STAMINA_COST:
            reply = self._refuse(params, f"Not enough stamina: a fight needs {arena.STAMINA_COST}, and you have "
                                         f"{a['stamina']}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused arena fight: stamina {a['stamina']}"
        before = a["stamina"]
        a["stamina"] -= arena.STAMINA_COST
        if revenge is not None:
            revenge["revenge"] = arena.REVENGE_TAKEN
        self.player_store.save(player)
        rng = self.rng_factory()
        battle = Battle(Setup(commander, shuffled_deck(deck, FIRST_UID[ATTACKER], rng), enemy_commander,
                              shuffled_deck(enemy_deck, FIRST_UID[DEFENDER], rng)),
                        chooser=RandomChooser(rng), cards=cards)          # the server's random source (seedable)
        battle_id = int(self.clock() * 1000)
        session = BattleSession(battle, battle_id, str(self.uid),
                                extra={**ARENA_FIELDS, "enemy_id": tid, "enemy_name": rival["name"]})
        turns = battle.start()
        kind = "revenge on" if revenge is not None else "arena"
        self.battle = {"id": str(battle_id), "name": f"{kind} {tid} ({rival['name']})", "mission_id": None,
                       "engine": session, "rival": rival, "revenge": revenge is not None}
        reply = self.envelope(params)
        reply["result"] = True
        reply["user_data"] = self.current_user_data(params)
        reply["battle_data"] = session.start_data(turns)
        reply["hunting_targets"] = self._targets(player)
        what = "revenge on" if revenge is not None else "arena fight with"
        return reply, (f"engine: started {what} {rival['name']} ({whose}, rated {rival['hunting_elo']}): "
                       f"{len(deck)} cards against {len(enemy_deck)}; stamina {before} -> {a['stamina']}")

    def _battle_reports(self, params: dict) -> tuple[dict, str]:
        """getBattleReports (no parameters): the player's recent arena fights, newest
        first, as recorded (only `battle_reports`, no `result`): their own, and other
        players' fights against their defense deck (with a Revenge button)."""
        reply = self.envelope(params)
        reply["battle_reports"] = arena.reports_json(self.player().arena, self.uid)
        return reply, f"battle reports: {len(reply['battle_reports'])}"

    def _finish_hunt(self, rival: dict, won: bool, battle_id=None, battle=None,
                     revenge: bool = False) -> arena.HuntResult:
        player = self.player()
        result = arena.finish_hunt(player, rival, won, self.reward_rules.loss_mult, int(self.clock()),
                                   self.uid, battle_id)
        if battle is not None:                   # the daily objectives count what happened
            achievements.ensure_dailies(self._achievement_defs(), player, int(self.clock()), self.rng_factory())
            ready = achievements.record_fight(self._achievement_defs(), player,
                                              achievements.fight_stats(battle, won), int(self.clock()),
                                              self._events())
            self._ach_changed = True
            if ready:
                self._ach_ready = [a.name for a in ready]
        self._track(player)                      # the rating
        self.player_store.save(player)
        rid = int(rival["user_id"])
        if rid > 0 and rid != self.uid:          # another player's defense deck: tell them
            store = self._store_for(rid)
            defender = store.load() if store is not None else None
            if defender is not None and defender.arena is not None:
                arena.file_defence_report(defender.arena, self.uid, player.name or f"Player{self.uid}", not won,
                                          int(self.clock()), battle_id, revenge=not revenge)
                store.save(defender)
        return result

    def _buy_stamina_refill(self, params: dict) -> tuple[dict, str]:
        """buyStaminaRefillTokens (no parameters; the game asks "Do you want to purchase a
        refill?" first): stamina up to the max, for wb_stamina_cost (30) war bonds.
        Unlike energy, not over the cap: given 10 + 15 = 25, the game showed 15 (it shows
        overfilled energy as e.g. 257/250), and its dialog says nothing about the cap."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep stamina in.")
        cost = int(self._start.get("wb_stamina_cost") or DEFAULT_STAMINA_REFILL_COST)
        a = player.arena
        if player.war_bonds < cost:
            reply = self._refuse(params, f"Not enough war bonds: a refill costs {cost}, and you have "
                                         f"{player.war_bonds}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused stamina refill: it costs {cost} war bonds, has {player.war_bonds}"
        before = (a["stamina"], player.war_bonds)
        a["stamina"] = max(a["stamina"], a["max_stamina"])
        a["stamina_updated"] = int(self.clock())   # at the max: the refill clock waits from now
        player.war_bonds -= cost
        self.player_store.save(player)
        reply = self.envelope(params)
        reply["result"] = True
        reply["user_data"] = self.current_user_data(params)
        return reply, (f"stamina refill: {before[0]} -> {a['stamina']}, "
                       f"{cost} war bonds ({before[1]} -> {player.war_bonds})")

    def _refresh_rivals(self, params: dict) -> tuple[dict, str]:
        """buyRivalsRefresh (no parameters, no confirmation): a new set of rivals for
        gold_rivals_cost (50) gold. The reply is the getHuntingTargets reply with the new
        list (the game empties the list while it waits)."""
        player = self.player()
        cost = int(self._start.get("gold_rivals_cost") or DEFAULT_REFRESH_COST)
        if player.arena["hunting_wins"] == 0:
            reply = self._refuse(params, "The rivals can't be refreshed before your first arena win.")
            reply["user_data"] = self.current_user_data(params)
            return reply, "refused rivals refresh: no arena win yet (the tutorial rival)"
        if player.gold < cost:
            reply = self._refuse(params, f"Not enough gold: a refresh costs {cost}, and you have {player.gold}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused rivals refresh: it costs {cost} gold, has {player.gold}"
        before = player.gold
        player.gold -= cost
        arena.refresh_rivals(player.arena)
        self.player_store.save(player)
        reply, how = self._hunting_targets(params)
        return reply, f"rivals refreshed for {cost} gold ({before} -> {player.gold}); {how}"

    def _unlock_commander(self, params: dict) -> tuple[dict, str]:
        """unlockCommander commander_id (Promote, after "Unlock this Commander?"): the
        commander and its bonus cards for a promotion point (arena.py). The reply is the
        recorded one's shape: new_commander, new_cards, user_data, user_cards, user_decks,
        and no `result`."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep cards in.")
        cards, _ = self._engine_data()
        raw = str(params.get("commander_id"))
        if not raw.isdigit():
            return self._refuse(params, "Invalid commander."), f"refused unlockCommander: {raw!r} isn't a card id"
        base = int(raw)
        try:
            bonus = arena.unlock_commander(player, base, cards, self.rng_factory())
        except arena.UnlockError as e:
            reply = self._refuse(params, f"Can't unlock that commander: {e}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused unlockCommander {base}: {e}"
        self.player_store.save(player)
        keys = ("user_data", "user_cards", "user_decks")
        reply = self.envelope(params)
        reply["new_commander"] = str(base)
        reply["new_cards"] = [str(c) for c in bonus]
        parts = self._prepare({"time": self._start.get("time"),
                               **{k: self._start[k] for k in keys if k in self._start}}, params)
        reply.update({k: parts[k] for k in keys if k in parts})
        names = ", ".join(cards[c].name for c in bonus)
        return reply, (f"unlocked {cards[base].name} ({base}) for a promotion point "
                       f"({player.arena['promotion_points']} left), with {names}")
