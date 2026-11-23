"""Conquest (conquest.py): the map, zones, fights and rewards.
Part of GameApi (core.py), as ConquestCalls."""

from __future__ import annotations

import conquest
import guilds
from battle import effects as bge
from battle.engine import ATTACKER, Battle, DEFENDER, FIRST_UID, RandomChooser, Setup, shuffled_deck
from battle.protocol import BattleSession


class ConquestCalls:
    """GameApi's part for conquest (conquest.py): the map, zones, fights and rewards."""

    def _conquest_report(self) -> str:
        c = self.content()
        event = self._conquest_defs()[0].get(c.conquest_event) if c.conquest_event else None
        if event is None:
            return "; conquest: none" + (f" (there's no conquest {c.conquest_event})" if c.conquest_event else "")
        return f"; conquest: {event.name} ({event.id}), {event.length // 3600} hours"

    # ---- conquest (conquest.py; the server's, across guilds) -----------------------------------------

    def _conquest_defs(self):
        if getattr(self, "_conquests", None) is None:
            self._conquests = conquest.load(self._assets)
        return self._conquests

    def _conquest_state(self):
        """(the conquest content.toml sets or None, the state), a new run started when the
        last one is over (or another is set), and the scoring rounds that have come scored."""
        c, now = self.content(), int(self.clock())
        events, zones = self._conquest_defs()
        event = events.get(c.conquest_event) if c.conquest_event else None
        state = self.conquest_store.load()
        if event is None:
            return None, state
        run, changed = state.get("run"), False
        if not run or run["event"] != event.id or now > run["decay"]:
            conquest.start_run(state, event, now)
            changed = True
        if conquest.score_rounds(state, zones, now):
            changed = True
        if changed:
            self.conquest_store.save(state)
        return event, state

    def _conquest_parts(self, guild) -> dict:
        """conquest_data while a conquest is on or being claimed (any reply carrying it
        replaces the client's conquest)."""
        event, state = self._conquest_state()
        now = int(self.clock())
        if event is None or conquest.phase(state, now) is None:
            return {}
        _, zones = self._conquest_defs()
        return {"conquest_data": conquest.conquest_json(event, zones, state, guild["id"] if guild else None, self.uid,
                                                        self._bge_defs(), self._mini_packs(), now)}

    def _conquest_board_rows(self, state, which: str, guild, zone_id=None):
        """(rows, the player's own first row) for a board: guilds by points, guilds by a
        zone's influence, or the guild's members by influence (all, or in a zone)."""
        gid = str(guild["id"]) if guild else None
        if which == "guilds":
            rows = [{"id": r["id"], "name": r["name"], "stat": r["points"], "rank": r["rank"]}
                    for r in conquest.standings(state)]
        elif which == "zone":
            rows = [{"id": g, "name": state["guilds"][g]["name"], "stat": inf, "rank": i}
                    for i, (g, inf) in enumerate(conquest.zone_ranking(state, zone_id), 1)]
        else:
            members = list(guild["members"]) if guild else []
            got = lambda u: (state["members"].get(str(u), {}).get("zones", {}).get(str(zone_id), 0)     # noqa: E731
                             if zone_id is not None else state["members"].get(str(u), {}).get("influence", 0))
            ordered = sorted(members, key=lambda u: (-got(u), int(u)))
            rows = [{"id": u, "name": self._name_of(u), "stat": got(u), "rank": i} for i, u in enumerate(ordered, 1)]
            mine = next((r for r in rows if str(r["id"]) == str(self.uid)), None)
            return rows[:conquest.BOARD_SIZE], mine
        mine = next((r for r in rows if r["id"] == gid), None)
        if mine is None and guild:
            mine = {"id": gid, "name": guild["name"], "stat": 0, "rank": 0}
        return rows[:conquest.BOARD_SIZE], mine

    def _conquest_message(self, message: str, params: dict) -> tuple[dict, str]:
        """The conquest calls: the map's update, a fight, markers, the reward claim and the boards."""
        event, state = self._conquest_state()
        _, zones = self._conquest_defs()
        guild, now = self._my_guild(), int(self.clock())
        try:
            zone_id = int(params.get("zone_id")) if params.get("zone_id") not in (None, "") else None
        except (TypeError, ValueError):
            zone_id = None
        boards = {"getConquestLeaderboard": ("conquest_leaderboard", "guilds", "Ranking"),
                  "getConquestTopLeaderboard": ("conquest_top_leaderboard", "guilds", "Top Ranks"),
                  "getGuildInfluenceLeaderboard": ("conquest_influence_leaderboard", "members", "Members"),
                  "getConquestZoneTopLeaderboard": ("conquest_zone_top_leaderboard", "zone", "Top Ranks"),
                  "getGuildZoneInfluenceLeaderboard": ("conquest_zone_influence_leaderboard", "members", "Members"),
                  "getConquestZoneRankings": ("rankings", "zone", "Ranking")}
        if message in boards:
            key, which, title = boards[message]
            in_zone = message in ("getConquestZoneTopLeaderboard", "getGuildZoneInfluenceLeaderboard",
                                  "getConquestZoneRankings")
            rows, mine = (self._conquest_board_rows(state, which, guild, zone_id if in_zone else None)
                          if event else ([], None))
            board = conquest.board_json(key, title, rows, mine)
            reply = self.envelope(params)
            reply["result"] = True
            if key == "rankings":
                reply["conquest_zone_data"] = {"id": zone_id or 0, "rankings": board}
            else:
                reply[key] = board
            return reply, f"conquest board {key}{f' (zone {zone_id})' if in_zone else ''}: {len(rows)} row(s)"
        if message == "getPreviousConquestTopLeaderboard":
            rows = []
            for r in state.get("previous") or []:
                g = self.guild_store.load(r["id"])
                row = guilds.list_json(g, self._guild_info, r["rank"]) if g else {
                    "faction_id": r["id"], "name": r["name"], "rank": r["rank"]}
                rows.append({**row, "conquest_points": r["points"]})
            reply = self._guild_reply(params, guild)
            reply["previous_conquest_top_leaderboard"] = rows
            return reply, f"last conquest's guilds: {len(rows)}"
        if message == "getConquestUpdate":
            reply = self.envelope(params)
            reply["result"] = True
            reply["conquest_data"] = self._conquest_parts(guild).get("conquest_data", [])
            return reply, ("conquest update" if event else "conquest update: none set")
        if event is None or conquest.phase(state, now) is None:
            words = "There's no conquest on right now."
            return self._refuse(params, words), f"refused {message}: {words}"
        if message == "setConquestMarker":
            return self._conquest_marker(params, state, guild, zones, zone_id)
        if message == "fightConquestBattle":
            return self._conquest_fight(params, event, state, guild, zones, zone_id)
        return self._conquest_claim(params, event, state, guild)

    def _conquest_marker(self, params, state, guild, zones, zone_id):
        """setConquestMarker zone_id marker_id: officers and the leader mark a zone (0 clears)."""
        try:
            marker = int(params.get("marker_id"))
        except (TypeError, ValueError):
            marker = -1
        words = ("You aren't in a guild." if guild is None
                 else "Only officers and the Guild Leader can set markers."
                 if guilds.role(guild, self.uid) < guilds.OFFICER
                 else "There's no such zone." if zone_id not in zones
                 else "There's no such marker." if marker not in conquest.MARKERS else None)
        if words:
            return self._refuse(params, words), f"refused setConquestMarker: {words}"
        g = conquest.guild_row(state, guild["id"], guild["name"])
        if marker:
            g["markers"][str(zone_id)] = marker
        else:
            g["markers"].pop(str(zone_id), None)
        self.conquest_store.save(state)
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._conquest_parts(guild))
        return reply, f"conquest marker {marker} on {zones[zone_id].name} for {guild['name']}"

    def _conquest_fight(self, params, event, state, guild, zones, zone_id):
        """fightConquestBattle zone_id: a whole fight in one reply, in surge mode, against a
        member of a guild near the player's in conquest points (their defense deck, played
        by the AI), each side with its guild's conquest fortresses and the zone's
        battleground effect. A win earns the guild 50 influence in the zone, a loss 5."""
        now, run = int(self.clock()), state["run"]
        me = state["members"].get(str(self.uid))
        words = ("The conquest hasn't started yet." if now < run["start"]
                 else "The conquest is over." if conquest.phase(state, now) != "conquest"
                 else "You must be in a guild to fight in Conquest." if guild is None
                 else "There's no such zone." if zone_id not in zones
                 else "You're out of conquest energy. Wait for more energy."
                 if conquest.energy_now(run, me, now) < conquest.ENERGY_COST else None)
        if words:
            return self._refuse(params, words), f"refused fightConquestBattle: {words}"
        candidates, ready = [], {}
        for g in self.guild_store.all():
            if str(g["id"]) == str(guild["id"]):
                continue
            members = [u for u in g["members"] if self._player_rival(u) is not None]
            if members:
                ready[str(g["id"])] = (g, members)
                candidates.append((str(g["id"]), state["guilds"].get(str(g["id"]), {}).get("points", 0)))
        rng = self.rng_factory()
        mine = state["guilds"].get(str(guild["id"]), {}).get("points", 0)
        foe_gid = conquest.pick_guild(candidates, mine, rng)
        if foe_gid is None:
            words = "There's no other guild to fight right now."
            return self._refuse(params, words), f"refused fightConquestBattle: {words}"
        foe_guild, members = ready[foe_gid]
        foe = rng.choice(sorted(members, key=str))
        cards, _ = self._engine_data()
        commander, deck = self._player_deck(cards)
        foe_commander, foe_deck = self._deck_cards(self._defense_deck_of(self._player_of(foe)), cards,
                                                   f"{self._name_of(foe)}'s defense deck")
        zone = zones[zone_id]
        effects, entries = [], {"global": [], "attacker": [], "defender": []}
        effect = self._bge_defs().get(zone.battleground_effect) if zone.battleground_effect else None
        if effect is not None:
            effects.append((bge.SCOPE_UID["global"], "global", effect.caster(1), effect.passive))
            entries["global"].append(effect.entry("global"))
        theirs = [cards[c] for c in conquest.fortress_cards(state, foe_gid) if cards.get(c)]
        ours = [cards[c] for c in conquest.fortress_cards(state, guild["id"]) if cards.get(c)]
        battle = Battle(Setup(foe_commander, shuffled_deck(foe_deck, FIRST_UID[ATTACKER], rng), commander,
                              shuffled_deck(deck, FIRST_UID[DEFENDER], rng), attacker_fortresses=tuple(theirs),
                              defender_fortresses=tuple(ours), attacker_wins_stall=True),
                        chooser=RandomChooser(rng), cards=cards, effects=effects)
        battle.start()
        battle.play_out(oldest=True)
        won = battle.winner == 0                       # the player is the battle's defender
        conquest.guild_row(state, guild["id"], guild["name"])
        conquest.guild_row(state, foe_gid, foe_guild["name"])
        member = conquest.member_row(state, self.uid, self._name_of(self.uid))
        conquest.spend_energy(run, member, now)
        influence = conquest.record_fight(state, member, guild["id"], zone_id, won)
        self.conquest_store.save(state)
        foe_name = self._name_of(foe)
        session = BattleSession(battle, int(self.clock() * 1000), str(self.uid), host_is_attacker=False,
                                extra={"enemy_name": foe_name, "enemy_id": str(foe), "battleground_effects": entries})
        rewards = [{"user_id": str(self.uid), "gold": 0, "sp": 0, "item": [], "conquest_influence": influence}]
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._player_parts(params, ("user_data",)))
        reply["battle_data"] = session.turn_data(battle.turns, rewards, end_time=now)
        g = state["guilds"][str(guild["id"])]
        how = (f"conquest fight in {zone.name} vs {foe_name} of {foe_guild['name']}"
               + (f" ({len(theirs)} of their fortresses, {len(ours)} of ours)" if theirs or ours else "")
               + f": {'won' if won else 'lost'} in {battle.turn} turns, +{influence} influence "
               + f"({guild['name']} {g['influence'][str(zone_id)]} there, rank {conquest.zone_rank(state, zone_id, guild['id'])}); "
               + f"conquest energy {conquest.energy_now(run, member, now)}/{conquest.ENERGY_MAX}")
        if battle.ignored_skills:
            how += "; skills not built yet: " + ", ".join(sorted(battle.ignored_skills))
        return reply, how

    def _conquest_claim(self, params, event, state, guild):
        """claimConquestReward: every tier the guild's conquest rank and the player's own
        influence reach, opened into cards (read like a pack purchase)."""
        now = int(self.clock())
        me = state["members"].get(str(self.uid))
        rank = conquest.guild_rank(state, guild["id"]) if guild else 0
        tiers = conquest.earned_tiers(event, rank, me["influence"]) if me else []
        words = ("The conquest's rewards can be claimed a little after it ends." if not conquest.claimable(state, now)
                 else "You must be in a guild to claim Conquest rewards." if guild is None
                 else "You didn't take part in this conquest." if not me or not me["battles"]
                 else "You already claimed your conquest rewards." if me["claimed"]
                 else "You didn't earn a reward in this conquest." if not tiers else None)
        if words:
            return self._refuse(params, words), f"refused claimConquestReward: {words}"
        player = self.player()
        got, gains = self._pay_tiers(player, tiers)
        me["claimed"] = 1
        self.player_store.save(player)
        self.conquest_store.save(state)
        reply = self._reward_reply(params, player, got, gains)
        return reply, (f"conquest rewards (guild rank {rank}, {me['influence']} influence): {len(tiers)} tiers, "
                       f"{self._describe_gains(got, gains)}")
