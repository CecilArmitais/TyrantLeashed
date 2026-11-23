"""Guild wars (wars.py): the event, matches, fights and rewards.
Part of GameApi (core.py), as WarCalls."""

from __future__ import annotations

import achievements
import brawls
import guilds
import wars
from battle import effects as bge
from battle.engine import ATTACKER, Battle, DEFENDER, FIRST_UID, RandomChooser, Setup, shuffled_deck
from battle.protocol import BattleSession

from .base import NotSupported


class WarCalls:
    """GameApi's part for guild wars (wars.py): the event, matches, fights and rewards. A
    war event is the server's, shared by all its guilds."""

    def _war_report(self) -> str:
        c = self.content()
        event = self._war_events().get(c.war_event) if c.war_event else None
        if event is None:
            return "; guild wars: none" + (f" (there's no war event {c.war_event})" if c.war_event else "")
        return (f"; guild wars: {event.name} ({event.id}), {c.war_days}-day events, guilds of "
                f"{c.war_min_members}+ members"
                + ("; the raid takes the Wars tab while it's set" if self._set_raid() is not None else ""))

    def _war_events(self) -> dict:
        if self._wars is None:
            self._wars = wars.load(self._assets)
        return self._wars

    def _war_names(self, gid) -> str:
        g = self.guild_store.load(gid)
        return g["name"] if g else "a disbanded guild"

    def _war_members(self, gid):
        g = self.guild_store.load(gid)
        return list(g["members"]) if g else None

    def _war_slots(self, gid) -> dict:
        g = self.guild_store.load(gid)
        return dict(g["slots"]) if g else {}

    def _war_state(self):
        """(the war event content.toml sets or None, the war state), brought up to now:
        a new run when the last one is over (or another event is set), matches whose time
        is up finished, queued guilds matched. Their news goes to the guilds."""
        c, now = self.content(), int(self.clock())
        event = self._war_events().get(c.war_event) if c.war_event else None
        state = self.war_store.load()
        if event is None:
            return None, state
        run, changed = state.get("run"), False
        if not run or run["event"] != event.id or now > run["decay"]:
            wars.start_run(state, event, now, c.war_days)
            changed = True
        for m in wars.finish_matches(state, now):
            changed = True
            a, d = m["a"], m["d"]
            pa, pd = m["points"][a], m["points"][d]
            for gid, other, won in ((a, d, pa > pd), (d, a, pd > pa)):
                self._war_news(gid, wars.NEWS_WON if won else wars.NEWS_LOST,
                               {"target_faction_name": m["names"][other], "winner_faction_points": max(pa, pd),
                                "loser_faction_points": min(pa, pd)}, now)
        made = wars.matchmake(state, now, self._war_members, self._war_names, c.war_min_members,
                              c.war_point_gap_percent / 100, int(c.war_rematch_hours * 3600),
                              seeds=brawls.placement_seeds(self.brawl_store.load(), self._brawl_events()))
        for m in made:
            changed = True
            for gid, other in ((m["a"], m["d"]), (m["d"], m["a"])):
                self._war_news(gid, wars.NEWS_STARTED, {"target_faction_name": m["names"][other]}, now)
        if changed:
            self.war_store.save(state)
        return event, state

    def _war_news(self, gid, kind: int, data: dict, now: int) -> None:
        g = self.guild_store.load(gid)
        if g is not None:
            guilds.add_news(g, kind, data, now)
            self.guild_store.save(g)

    def _war_parts(self, guild) -> dict:
        """What a reply carries while a war event is on (or being claimed):
        active_war_event, faction_war_event_info and, while the guild is queued or at
        war, faction_war. The client takes a reply with active_war_event as the whole
        war state, so they always go together. With no war on, an idle active_war_event
        (wars.idle_event_json): the Wars tab draws only once it has had one. But not while
        a raid is set: any active_war_event runs the tab's war display, which hides the raid
        panel (FortressController.onGuildWarsUpdated -> HideDisplays), and the raid draws
        without one."""
        event, state = self._war_state()
        now = int(self.clock())
        if event is None or wars.phase(state, now) is None:
            if self._set_raid() is not None:
                return {}
            return {"active_war_event": wars.idle_event_json(), "faction_war_event_info": []}
        out = {"active_war_event": wars.event_json(event, state["run"], self._mini_packs())}
        if guild is None:
            out["faction_war_event_info"] = []
            return out
        out["faction_war_event_info"] = wars.event_info_json(state, guild["id"], self.uid, list(guild["members"]),
                                                             self._name_of)
        fw = wars.faction_war_json(state, guild["id"], guild["name"], self.uid, self._war_slots, self._name_of, now)
        if fw is not None:
            out["faction_war"] = fw
        return out

    def _war_message(self, message: str, params: dict) -> tuple[dict, str]:
        """The guild war calls: Start, a fight, the reward claim and the leaderboards."""
        event, state = self._war_state()
        guild, now = self._my_guild(), int(self.clock())
        if message == "getFactionWarLeaderboard":
            reply = self._guild_reply(params, guild)
            reply["faction_war_leaderboard"] = wars.leaderboard_json(wars.standings(state)[:100])
            return reply, f"war leaderboard: {len(reply['faction_war_leaderboard'])} guild(s)"
        if message == "getFactionWarMatchLeaderboard":
            rows = []
            for gid in sorted(state["queue"], key=lambda g: (state["queue"][g], int(g))):
                g = state["guilds"].get(gid, {})
                rows.append({"id": gid, "name": self._war_names(gid), "points": g.get("points", 0),
                             "rank": wars.rank_of(state, gid) if g else 0})
            reply = self._guild_reply(params, guild)
            reply["faction_war_leaderboard"] = wars.leaderboard_json(rows)
            return reply, f"war matchmaking: {len(rows)} guild(s) queued"
        if message == "getPreviousFactionWarTopLeaderboard":
            rows = []
            for r in state.get("previous") or []:
                g = self.guild_store.load(r["id"])
                row = guilds.list_json(g, self._guild_info, r["rank"]) if g else {
                    "faction_id": r["id"], "name": r["name"], "rank": r["rank"]}
                rows.append({**row, "faction_war_points": r["points"]})
            reply = self._guild_reply(params, guild)
            reply["previous_war_top_leaderboard"] = rows
            return reply, f"last war event's guilds: {len(rows)}"
        if event is None or wars.phase(state, now) is None:
            words = "There's no guild war on right now."
        elif guild is None:
            words = "You aren't in a guild."
        else:
            words = None
        if words:
            if message == "fightFactionWar":
                return self._guild_reply(params, guild, words, ok=False), f"refused {message}: {words}"
            return self._refuse(params, words), f"refused {message}: {words}"
        if message == "fightFactionWar":
            return self._war_start(params, state, guild)
        if message == "startFactionWarBattle":
            return self._war_fight(params, event, state, guild)
        return self._war_claim(params, event, state, guild)

    def _war_start(self, params: dict, state: dict, guild: dict) -> tuple[dict, str]:
        """fightFactionWar (the Start button): the guild joins the queue, and is matched
        with a queued guild if one fits."""
        now, c = int(self.clock()), self.content()
        try:
            if guilds.role(guild, self.uid) < guilds.OFFICER:
                raise wars.WarError("Only officers and the Guild Leader can start a war.")
            wars.can_queue(state, guild["id"], len(guild["members"]), now, c.war_min_members)
        except wars.WarError as e:
            return self._guild_reply(params, guild, str(e), ok=False), f"refused fightFactionWar: {e}"
        wars.queue(state, guild["id"], now)
        self.war_store.save(state)
        self._war_news(guild["id"], wars.NEWS_STARTED, {"name": self._name_of(self.uid)}, now)
        reply = self._guild_reply(params, self._my_guild())        # matches any queued guild that fits
        fw = reply.get("faction_war") or {}
        if fw.get("defender_faction_id", "0") == "0":
            how = f"{guild['name']} queued for a war"
        else:
            foe = fw["defender_faction_name"] if fw["attacker_faction_id"] == str(guild["id"]) else fw["attacker_faction_name"]
            how = f"{guild['name']} queued and matched with {foe}"
        return reply, how

    def _war_defender(self, enemy: dict, cards, rng):
        """(user id, commander, deck) of the enemy member who defends: any with a defense
        deck the engine can play (wars.pick_defender)."""
        ready = {}
        for uid in enemy["members"]:
            player = self._player_of(uid)
            if player is None:
                continue
            try:
                ready[uid] = self._deck_cards(self._defense_deck_of(player), cards, "the deck")
            except NotSupported:
                continue
        uid = wars.pick_defender(list(ready), rng)
        return (uid, *ready[uid]) if uid is not None else None

    def _war_fight(self, params: dict, event, state: dict, guild: dict) -> tuple[dict, str]:
        """startFactionWarBattle slot_id: an attack on the other guild's fortress slot,
        a whole fight in one reply (as raids are), in surge mode: the defending member's
        defense deck plays first. Sent with the enemy as the battle's attacker
        (host_is_attacker false), which is how the client knows who moves first."""
        now = int(self.clock())
        gid = str(guild["id"])
        m = wars.match_of(state, gid)
        try:
            slot = int(params.get("slot_id"))
        except (TypeError, ValueError):
            slot = 0
        me = wars.fighter(m, self.uid, gid) if m else None
        enemy = self.guild_store.load(wars.other(m, gid)) if m else None
        words = ("Your guild isn't at war." if m is None
                 else "The war hasn't started yet: attacks open after the preparation."
                 if now < m["start"] + wars.PREP_TIME
                 else "There's no such fortress slot." if slot not in wars.SLOTS
                 else "You have no war attacks left in this war." if me["energy"] < wars.ENERGY_COST
                 else "The other guild no longer exists." if enemy is None else None)
        if words:
            return self._refuse(params, words), f"refused startFactionWarBattle: {words}"
        cards, _ = self._engine_data()
        rng = self.rng_factory()
        commander, deck = self._player_deck(cards)
        foe = self._war_defender(enemy, cards, rng)
        if foe is None:
            words = f"No one in {enemy['name']} has a deck to defend with."
            return self._refuse(params, words), f"refused startFactionWarBattle: {words}"
        foe_uid, foe_commander, foe_deck = foe
        mine, theirs = wars.fight_fortresses(m, gid, guild["slots"], str(enemy["id"]), enemy["slots"], slot)
        effects, entries = [], {"global": [], "attacker": [], "defender": []}
        eid = wars.effect_on(event, state["run"], now)
        effect = self._bge_defs().get(eid) if eid else None
        if effect is not None:
            effects.append((bge.SCOPE_UID["global"], "global", effect.caster(1), effect.passive))
            entries["global"].append(effect.entry("global"))
        battle = Battle(Setup(foe_commander, shuffled_deck(foe_deck, FIRST_UID[ATTACKER], rng), commander,
                              shuffled_deck(deck, FIRST_UID[DEFENDER], rng),
                              attacker_fortresses=tuple(cards[c] for c in theirs),
                              defender_fortresses=tuple(cards[c] for c in mine), attacker_wins_stall=True),
                        chooser=RandomChooser(rng), cards=cards, effects=effects)
        battle.start()
        battle.play_out(oldest=True)
        won = battle.winner == 0                       # the player is the battle's defender
        points, damage = wars.record_fight(state, m, self.uid, gid, foe_uid, slot, won, battle.turn,
                                           fortress=bool(theirs) and slot != wars.CORE)
        gold = wars.WIN_GOLD if won else wars.LOSS_GOLD
        player = self.player()
        player.gold += gold
        player.gold_earned = (player.gold_earned or 0) + gold
        self.player_store.save(player)
        self.war_store.save(state)
        foe_name = self._name_of(foe_uid)
        session = BattleSession(battle, int(self.clock() * 1000), str(self.uid), host_is_attacker=False,
                                extra={"enemy_name": foe_name, "enemy_id": str(foe_uid),
                                       "battleground_effects": entries})
        shown = -(-damage * 100 // wars.FORT_HEALTH)            # as the slot's health bar shows it
        rewards = [{"user_id": str(self.uid), "gold": gold, "sp": 0, "item": self._fight_drop(achievements.WAR, won),
                    "war_points": points, "slot_damage": shown}]
        reply = self._ok(params)
        reply.update(self._player_parts(params, ("user_data",)))
        reply["battle_data"] = session.turn_data(battle.turns, rewards, end_time=now)
        reply.update(self._war_parts(self._my_guild()))
        target = "the core" if slot == wars.CORE else f"slot {slot}"
        how = (f"war fight on {enemy['name']}'s {target} vs {foe_name}'s defense deck"
               + (f" with {len(theirs)} of their fortresses" if theirs else "")
               + (f" and {len(mine)} of ours" if mine else "")
               + f": {'won' if won else 'lost'} in {battle.turn} turns, {points} war points"
               + (f", {shown}% damage" if damage else "") + f", {gold} gold; "
               + f"war attacks {me['energy']}/{wars.ATTACKS}; {guild['name']} {m['points'][gid]} - "
               + f"{m['points'][str(enemy['id'])]} {enemy['name']}")
        if battle.ignored_skills:
            how += "; skills not built yet: " + ", ".join(sorted(battle.ignored_skills))
        return reply, how

    def _war_claim(self, params: dict, event, state: dict, guild: dict) -> tuple[dict, str]:
        """claimFactionWarRewards: every tier the guild's rank and the player's points
        reached, opened into cards (the reply is read like a pack purchase's)."""
        now = int(self.clock())
        row = state["members"].get(str(self.uid))
        rank = wars.rank_of(state, guild["id"])
        tiers = wars.earned_tiers(event, rank, row["points"]) if row else []
        words = ("The war's rewards can be claimed a little after it ends." if not wars.claimable(state, now)
                 else "You already claimed your war rewards." if row and row["claimed"]
                 else "You didn't earn a reward in this war." if not tiers else None)
        if words:
            return self._refuse(params, words), f"refused claimFactionWarRewards: {words}"
        reply, got, gains = self._pay_claim(params, tiers, row, lambda: self.war_store.save(state))
        reply.update(self._war_parts(guild))
        return reply, (f"war rewards (rank {rank}, {row['points']} points): {len(tiers)} tiers, "
                       f"{self._describe_gains(got, gains)}")
