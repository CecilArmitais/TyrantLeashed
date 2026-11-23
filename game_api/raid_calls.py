"""Raids (raids.py): each guild's run of the raid content.toml sets.
Part of GameApi (core.py), as RaidCalls."""

from __future__ import annotations

import json

import achievements
import guilds
import raids
from battle import effects as bge
from battle.engine import ATTACKER, Battle, DEFENDER, FIRST_UID, RandomChooser, Setup, shuffled_deck
from battle.protocol import BattleSession


class RaidCalls:
    """GameApi's part for raids (raids.py): each guild fights its own run of the raid
    content.toml sets."""

    def _raid_defs(self) -> dict:
        if self._raids is None:
            self._raids = raids.load(self._assets)
        return self._raids

    def _set_raid(self):
        c = self.content()
        return self._raid_defs().get(c.raid) if c.raid else None

    def _raid_state(self, guild):
        """(raid, run, health scale) for the guild's run of the raid content.toml sets,
        brought up to now (a new run when there's none or the last one is over), or None
        when no raid is set or there's no guild. Saves the guild if anything changed."""
        raid = self._set_raid()
        if raid is None or guild is None:
            return None
        c = self.content()
        now, scale = int(self.clock()), c.raid_health_percent / 100
        run = guild.get("raid")
        before = json.dumps(run, sort_keys=True)
        if not run or run.get("id") != raid.id or now >= run.get("decay", 0) or "members" not in run:
            run = guild["raid"] = raids.new_run(raid, now, c.raid_days, scale)
        lost = raids.refresh(run, raid, now, scale)
        if lost:
            guilds.add_news(guild, guilds.RAID_LOST, {"raid_level": lost, "raid_name": raid.name}, now)
        for m in run["members"].values():
            raids.refresh_member(run, m, now)
        if json.dumps(run, sort_keys=True) != before or lost:
            self.guild_store.save(guild)
        return raid, run, scale

    def _raid_replay(self, params) -> dict | None:
        """The last raid fight's reply, if `params` name its battle: the client may send
        forfeitBattle while it plays the fight back (the Skip/Surrender button)."""
        reply = self._last_raid_reply
        if reply is None or str(params.get("battle_id")) != str(reply["battle_data"]["battle_id"]):
            return None
        return {**reply, **self.envelope(params), "result": True}

    def _raid_info(self, state) -> list:
        raid, run, scale = state
        names = {uid: self._name_of(uid) for uid in run["members"]}
        names[str(self.uid)] = self._name_of(self.uid)
        return raids.raid_info(raid, run, self.uid, names, int(self.clock()), scale)

    def _raid_message(self, message: str, params: dict) -> tuple[dict, str]:
        raid = self._set_raid()
        if raid is None:
            return self._refuse(params, "There's no raid on."), f"refused {message}: no raid set in content.toml"
        if message == "getRaidLeaderboard":
            reply = self._ok(params)
            runs = [g for g in self.guild_store.all() if (g.get("raid") or {}).get("id") == raid.id]
            runs.sort(key=lambda g: (-g["raid"]["level"], g["id"]))
            reply["raid_leaderboard"] = [{"faction_id": str(g["id"]), "name": g["name"], "raid_level": g["raid"]["level"]}
                                         for g in runs]
            return reply, f"raid leaderboard: {len(runs)} guild(s)"
        if message == "getPreviousRaidTopLeaderboard":
            reply = self._ok(params)
            reply["previous_raid_top_leaderboard"] = []
            return reply, "previous raid leaderboard: none"
        guild = self._my_guild()
        if guild is None:
            return self._refuse(params, "You need to be in a guild to raid."), f"refused {message}: not in a guild"
        state = self._raid_state(guild)
        raid, run, scale = state
        m = raids.member(run, self.uid, int(self.clock()))
        if message == "fightRaidBattle":
            return self._raid_fight(params, guild, raid, run, m, scale)
        if message == "claimRaidReward":
            return self._raid_claim(params, guild, raid, run, m, scale)
        if message == "refillEventBattleEnergy":
            return self._raid_refill(params, guild, raid, run, m, scale)
        self.guild_store.save(guild)                                   # the member's part, if new
        reply = self._ok(params)
        reply.update(self._player_parts(params, ("user_data",)))
        reply["current_raids"] = raids.current_raids(raid, run, self._mini_packs())
        reply["raid_info"] = self._raid_info(state)
        reply.update(self._guild_parts(guild))
        return reply, (f"{raid.name}: {guild['name']} at level {run['level']}, health {max(0, run['hp'])}/"
                       f"{raid.max_health(run['level'], scale)}, raid energy {m['energy']}/{raids.ENERGY_MAX}, "
                       f"your damage {m['damage']}")

    def _raid_fight(self, params, guild, raid, run, m, scale) -> tuple[dict, str]:
        """fightRaidBattle raid_id raid_level: a whole fight in one reply, played on auto
        (the client shows it as a replay), for one of the player's raid energy. The damage
        goes to the guild's boss."""
        now = int(self.clock())
        try:
            raids.can_fight(run, m, now)
        except raids.RaidError as e:
            return self._refuse(params, str(e)), f"refused fightRaidBattle: {e}"
        cards, _ = self._engine_data()
        commander, deck = self._player_deck(cards)
        rng = self.rng_factory()
        level = run["level"]
        boss, forts, boss_cards = raids.boss_deck(raid, level, cards, rng)
        effects, entries = [], {"global": [], "attacker": [], "defender": []}
        for eid, scope in ((raid.battleground_effect, "global"), (raid.enemy_battleground_effect, "defender")):
            effect = self._bge_defs().get(eid) if eid else None
            if effect is not None:
                effects.append((bge.SCOPE_UID[scope], scope, effect.caster(level), effect.passive))
                entries[scope].append(effect.entry(scope))
        battle = Battle(Setup(commander, shuffled_deck(deck, FIRST_UID[ATTACKER], rng), boss,
                              shuffled_deck(boss_cards, FIRST_UID[DEFENDER], rng), defender_fortresses=tuple(forts)),
                        chooser=RandomChooser(rng), cards=cards, effects=effects)
        battle.start()
        battle.play_out()
        last_card = FIRST_UID[DEFENDER] + len(boss_cards)
        destroyed = sum(1 for t in battle.turns.values() for uid in t.get("defend_kill") or []
                        if FIRST_UID[DEFENDER] <= int(uid) < last_card)
        bc = battle.sides[DEFENDER].commander
        damage = raids.fight_damage(destroyed, bc.hp, bc.max_hp)
        started, cleared = raids.record_fight(run, m, raid, damage, now, scale)
        name = self._name_of(self.uid)
        if started:
            guilds.add_news(guild, guilds.RAID_STARTED, {"name": name, "raid_level": level, "raid_name": raid.name}, now)
        if cleared:
            guilds.add_news(guild, guilds.RAID_WON, {"raid_level": level, "raid_name": raid.name, "name": name}, now)
        self.guild_store.save(guild)
        battle_id = int(self.clock() * 1000)
        session = BattleSession(battle, battle_id, str(self.uid),
                                extra={"enemy_name": raid.name, "enemy_id": "0", "battleground_effects": entries})
        rewards = [{"user_id": str(self.uid), "gold": 0, "card": [], "raid_damage": damage,
                    "item": self._fight_drop(achievements.RAID, battle.winner == 1, level=level)}]
        reply = self._ok(params)
        reply.update(self._player_parts(params, ("user_data",)))
        reply["battle_data"] = session.turn_data(battle.turns, rewards, end_time=now)
        self._last_raid_reply = reply
        won = battle.winner == 1
        how = (f"raid fight vs {raid.name} level {level}: {'won' if won else 'lost'} in {battle.turn} turns, "
               f"{destroyed} cards destroyed, damage {damage}; {guild['name']}'s boss {max(0, run['hp'])}/"
               f"{raid.max_health(run['level'], scale)}" + (f"; level {level} cleared" if cleared else "")
               + f"; raid energy {m['energy']}")
        if battle.ignored_skills:
            how += "; skills not built yet: " + ", ".join(sorted(battle.ignored_skills))
        return reply, how

    def _raid_refill(self, params, guild, raid, run, m, scale) -> tuple[dict, str]:
        """refillEventBattleEnergy event_type=2 event_id: the player's raid energy to full, 5
        war bonds a point."""
        player = self.player()
        missing = raids.ENERGY_MAX - m["energy"]
        cost = missing * raids.REFILL_WB
        if missing <= 0:
            return self._refuse(params, "Your raid energy is already full."), "refused raid refill: full"
        if player.war_bonds < cost:
            reply = self._refuse(params, f"Not enough war bonds: a refill costs {cost}, and you have {player.war_bonds}.")
            return reply, "refused raid refill: war bonds"
        player.war_bonds -= cost
        m["energy"], m["energy_updated"] = raids.ENERGY_MAX, int(self.clock())
        self.player_store.save(player)
        self.guild_store.save(guild)
        reply = self._ok(params)
        reply.update(self._player_parts(params, ("user_data",)))
        reply["raid_info"] = self._raid_info((raid, run, scale))
        return reply, f"raid energy refilled: +{missing} for {cost} war bonds"

    def _raid_claim(self, params, guild, raid, run, m, scale) -> tuple[dict, str]:
        """claimRaidReward raid_id: every reward tier the player's damage and the guild's
        level reached, opened into cards."""
        now = int(self.clock())
        if m["claimed"]:
            return self._refuse(params, "You already claimed this raid's reward."), "refused raid claim: claimed"
        if not raids.claimable(run, m, now):
            return self._refuse(params, "The raid's reward can be claimed once it's over."), "refused raid claim: early"
        player = self.player()
        tiers = raids.earned_tiers(raid, m["damage"], run["level"])
        got, _ = self._pay_tiers(player, tiers)                      # raid tiers pay cards and packs, no items
        m["claimed"] = 1
        self.player_store.save(player)
        self.guild_store.save(guild)
        reply = self._ok(params)
        reply.update(self._player_parts(params, ("user_data", "user_cards")))
        reply["new_cards"] = [str(c) for c in got]
        reply["raid_info"] = self._raid_info((raid, run, scale))
        return reply, f"raid reward: {len(tiers)} tiers, {self._describe_gains(got, {})}"
