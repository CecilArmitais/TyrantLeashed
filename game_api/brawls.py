"""Brawls (brawls.py): the event, fights and rewards.
Part of GameApi (core.py), as BrawlCalls."""

from __future__ import annotations

import achievements
import brawls
import guilds
from battle import effects as bge
from battle.engine import ATTACKER, Battle, DEFENDER, FIRST_UID, RandomChooser, Setup, shuffled_deck
from battle.protocol import BattleSession


class BrawlCalls:
    """GameApi's part for brawls (brawls.py): the event, fights and rewards. A brawl is the
    server's, shared by all its players."""

    def _brawl_report(self) -> str:
        c = self.content()
        event = self._brawl_events().get(c.brawl_event) if c.brawl_event else None
        if event is None:
            return "; brawls: none" + (f" (there's no brawl {c.brawl_event})" if c.brawl_event else "")
        kind = "a guild brawl" if event.guild_event else "a solo brawl"
        return f"; brawl: {event.name} ({event.id}), {kind} of {event.length // 3600} hours"

    def _brawl_events(self) -> dict:
        if self._brawls is None:
            self._brawls = brawls.load(self._assets)
        return self._brawls

    def _brawl_state(self):
        """(the brawl content.toml sets or None, the brawl state), a new run started when
        the last one is over (or another brawl is set)."""
        c, now = self.content(), int(self.clock())
        event = self._brawl_events().get(c.brawl_event) if c.brawl_event else None
        state = self.brawl_store.load()
        if event is None:
            return None, state
        run = state.get("run")
        if not run or run["event"] != event.id or now > run["decay"]:
            brawls.start_run(state, event, now)
            self.brawl_store.save(state)
        return event, state

    def _brawl_rank(self, event, state) -> int:
        """The rank the brawl shows the player: their own, or in a guild brawl their guild's."""
        if event.guild_event:
            guild = self._my_guild()
            return brawls.rank_of(state, guild["id"], True) if guild else 0
        return brawls.rank_of(state, self.uid, False)

    def _brawl_parts(self) -> dict:
        """active_brawl_data and player_brawl_data while a brawl is on or being claimed
        (sent together: init and getHuntingTargets take them as the whole brawl)."""
        event, state = self._brawl_state()
        now = int(self.clock())
        if event is None or brawls.phase(state, now) is None:
            return {}
        return {"active_brawl_data": brawls.event_json(event, state["run"], self._mini_packs()),
                "player_brawl_data": brawls.player_json(state, event, self.uid, self._brawl_rank(event, state), now)}

    def _patch_battle_energy(self, user_data: dict) -> None:
        """user_data's battle energy is the brawl's while one runs: the Fight button checks
        it (with the regeneration the client works out from it)."""
        event, state = self._brawl_state()
        now = int(self.clock())
        if event is None or brawls.phase(state, now) != "brawl":
            return
        run, row = state["run"], state["players"].get(str(self.uid))
        user_data["battle_energy"] = str(brawls.ENERGY.current(run, row, now))
        user_data["battle_energy_update_time"] = str(now)
        user_data["battle_energy_recharge_time"] = brawls.ENERGY_EVERY if brawls.ENERGY.recharging(run, row, now) else -1

    def _brawl_message(self, message: str, params: dict) -> tuple[dict, str]:
        """The brawl calls (none take parameters): a fight, the reward claim, the boards."""
        event, state = self._brawl_state()
        now = int(self.clock())
        guild_event = bool(event and event.guild_event)
        if message in ("getBrawlLeaderboard", "getBrawlMemberLeaderboard", "getPreviousBrawlTopLeaderboard"):
            reply = self._ok(params)
            if message == "getPreviousBrawlTopLeaderboard":
                reply["previous_brawl_leaderboard"] = brawls.board_json(state.get("previous") or [],
                                                                        bool(state.get("previous_guild_event")))
                return reply, f"last brawl's board: {len(reply['previous_brawl_leaderboard'])}"
            if message == "getBrawlLeaderboard":
                rows = brawls.standings(state, guild_event)[:brawls.BOARD_SIZE] if event else []
                reply["brawl_leaderboard"] = brawls.board_json(rows, guild_event)
                return reply, f"brawl board: {len(rows)} {'guild' if guild_event else 'player'}(s)"
            guild = self._my_guild()
            members = list(guild["members"]) if guild and event else []
            rows = sorted(({"id": str(u), "name": self._name_of(u),
                            "points": state["players"].get(str(u), {}).get("points", 0)} for u in members),
                          key=lambda r: (-r["points"], int(r["id"])))
            reply["brawl_leaderboard"] = brawls.board_json([{**r, "rank": i} for i, r in enumerate(rows, 1)], False)
            return reply, f"guild's brawl board: {len(rows)} member(s)"
        if event is None or brawls.phase(state, now) is None:
            words = "There's no brawl on right now."
            return self._refuse(params, words), f"refused {message}: {words}"
        if message == "fightBrawlBattle":
            return self._brawl_fight(params, event, state)
        return self._brawl_claim(params, event, state)

    def _brawl_fight(self, params: dict, event, state: dict) -> tuple[dict, str]:
        """fightBrawlBattle: a whole fight in one reply (as raid and war fights are), in
        surge mode, against a player near the player's brawl points (their defense deck,
        played by the AI). Sent with the enemy as the battle's attacker."""
        now, run = int(self.clock()), state["run"]
        player, guild = self.player(), self._my_guild()
        row = state["players"].get(str(self.uid))
        words = ("The brawl is over." if brawls.phase(state, now) != "brawl"
                 else "Brawls open at a battle rating above 100." if player.arena["rating"] < brawls.MIN_RATING
                 else "You must be in a Guild to compete in this Guild Brawl." if event.guild_event and guild is None
                 else "You're out of brawl energy. Wait for more energy."
                 if brawls.ENERGY.current(run, row, now) < brawls.ENERGY_COST else None)
        if words:
            return self._refuse(params, words), f"refused fightBrawlBattle: {words}"
        mates = set(guild["members"]) if guild and event.guild_event else set()
        candidates = []
        for uid in self.player_ids():
            if uid != self.uid and str(uid) not in mates and self._player_rival(uid) is not None:
                candidates.append((str(uid), state["players"].get(str(uid), {}).get("points", 0)))
        rng = self.rng_factory()
        mine = row["points"] if row else 0
        foe = brawls.pick_opponent(candidates, mine, rng)
        if foe is None:
            words = "There's no one to brawl right now."
            return self._refuse(params, words), f"refused fightBrawlBattle: {words}"
        cards, _ = self._engine_data()
        commander, deck = self._player_deck(cards)
        foe_commander, foe_deck = self._deck_cards(self._defense_deck_of(self._player_of(foe)), cards,
                                                   f"{self._name_of(foe)}'s defense deck")
        effects, entries = [], {"global": [], "attacker": [], "defender": []}
        eid = brawls.effect_on(event, run, now)
        effect = self._bge_defs().get(eid) if eid else None
        if effect is not None:
            effects.append((bge.SCOPE_UID["global"], "global", effect.caster(1), effect.passive))
            entries["global"].append(effect.entry("global"))
        mine, theirs = [], []
        if event.use_forts:                            # a placement brawl: the guilds' fortresses, as in a war
            mine = self._fortress_cards(guild, guilds.SIEGE_SLOTS, cards)
            theirs = self._fortress_cards(self.guild_store.of_member(foe), guilds.DEFENSE_SLOTS, cards)
        battle = Battle(Setup(foe_commander, shuffled_deck(foe_deck, FIRST_UID[ATTACKER], rng), commander,
                              shuffled_deck(deck, FIRST_UID[DEFENDER], rng), attacker_fortresses=tuple(theirs),
                              defender_fortresses=tuple(mine), attacker_wins_stall=True),
                        chooser=RandomChooser(rng), cards=cards, effects=effects)
        battle.start()
        battle.play_out(oldest=True)
        won = battle.winner == 0                       # the player is the battle's defender
        destroyed = sum(len(t.get("attack_kill") or []) for t in battle.turns.values())
        unplayed = len(battle.sides[DEFENDER].remaining)
        points = brawls.fight_points(won, destroyed, unplayed, battle.turn)
        row = brawls.player_row(state, self.uid, self._name_of(self.uid))
        brawls.ENERGY.spend(run, row, now)
        brawls.record_fight(state, row, guild["id"] if event.guild_event else None, won, points,
                            guild["name"] if event.guild_event else "")
        self.brawl_store.save(state)
        foe_name = self._name_of(foe)
        session = BattleSession(battle, int(self.clock() * 1000), str(self.uid), host_is_attacker=False,
                                extra={"enemy_name": foe_name, "enemy_id": foe, "battleground_effects": entries})
        rewards = [{"user_id": str(self.uid), "gold": 0, "sp": 0, "item": self._fight_drop(achievements.BRAWL, won),
                    "rating_change": 0, "pvp_points": points}]
        reply = self._ok(params)
        reply.update(self._player_parts(params, ("user_data",)))
        reply["battle_data"] = session.turn_data(battle.turns, rewards, end_time=now)
        reply.update(self._brawl_parts())
        how = (f"brawl fight vs {foe_name}'s defense deck"
               + (f" with {len(theirs)} of their fortresses" if theirs else "")
               + (f" and {len(mine)} of ours" if mine else "")
               + f": {'won' if won else 'lost'} in {battle.turn} turns"
               + (f" ({destroyed} of their cards destroyed, {unplayed} of yours unplayed)" if won else "")
               + f", {points} points ({row['points']} in all, {row['wins']}/{row['battles']} won); "
               + f"brawl energy {brawls.ENERGY.current(run, row, now)}/{brawls.ENERGY_MAX}")
        if battle.ignored_skills:
            how += "; skills not built yet: " + ", ".join(sorted(battle.ignored_skills))
        return reply, how

    @staticmethod
    def _fortress_cards(guild: dict | None, slots, cards) -> list:
        """The fortress cards a guild has in some of its slots (guild["slots"])."""
        found = [int((guild or {}).get("slots", {}).get(str(s), 0) or 0) for s in slots]
        return [cards[c] for c in found if c and cards.get(c) is not None]

    def _brawl_claim(self, params: dict, event, state: dict) -> tuple[dict, str]:
        """claimBrawlRewards: every tier the player's rank (a guild brawl: the guild's rank
        and the player's points) reaches, opened into cards (read like a pack purchase)."""
        now = int(self.clock())
        row = state["players"].get(str(self.uid))
        guild = self._my_guild()
        rank = self._brawl_rank(event, state)
        tiers = brawls.earned_tiers(event, rank, row["points"]) if row else []
        words = ("The brawl's rewards can be claimed a little after it ends." if not brawls.claimable(state, now)
                 else "You didn't take part in this brawl." if not row or not row["battles"]
                 else "You already claimed your brawl rewards." if row["claimed"]
                 else "You must be in a Guild to claim this Guild Brawl's rewards." if event.guild_event and not guild
                 else "You didn't earn a reward in this brawl." if not tiers else None)
        if words:
            return self._refuse(params, words), f"refused claimBrawlRewards: {words}"
        reply, got, gains = self._pay_claim(params, tiers, row, lambda: self.brawl_store.save(state))
        reply.update(self._brawl_parts())
        return reply, (f"brawl rewards (rank {rank}, {row['points']} points): {len(tiers)} tiers, "
                       f"{self._describe_gains(got, gains)}")
