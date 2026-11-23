"""Guilds: membership, chat and news, donations and the fortress.
Part of GameApi (core.py), as GuildCalls."""

from __future__ import annotations

import guilds
import raids
import wars
from player_state import level_info

from .base import NotSupported


class GuildCalls:
    """GameApi's part for guilds: membership, chat and news, donations and the fortress."""

    def guild_report(self) -> str:
        """The server's guilds, donations and the raid set (the startup report adds the other
        events to the line)."""
        c, n = self.content(), len(self.guild_store.all())
        raid = self._set_raid()
        return (f"Guilds (state/{self.guild_store.root.name}/): {n or 'none yet'}"
                + ("" if not n else f" guild{'s' * (n != 1)}") + "; donations "
                + (f"on ({c.gold_per_point} gold a guild point)" if c.guild_donations else "off")
                + (f"; raid: {raid.name} ({raid.id}), at {c.raid_health_percent:g}% of its health" if raid
                   else "; raid: none"))

    def _my_guild(self) -> dict | None:
        return self.guild_store.of_member(self.uid)

    def _guild_parts(self, guild, params: dict | None = None) -> dict:
        """What every guild reply carries: the player's guild (`faction`; {"faction_id": 0}
        without one, and then the invitations), its fortress, and, given the request, the
        news and chat after its last_activity_id."""
        if guild is None:
            invites = guilds.invites_for(self.guild_store, self.uid)
            return {"faction": dict(guilds.NO_GUILD), "fortress_slots": [],
                    "faction_invites": [guilds.list_json(g, self._guild_info) for g in invites]}
        out = {"faction": guilds.faction_json(guild, self._guild_info, self.content().gold_per_point),
               "fortress_slots": guilds.fortress_json(guild)}
        if params is not None:
            out["faction_news"] = guilds.news_json(guilds.news_since(guild, params.get("last_activity_id")))
        return out

    def _guild_reply(self, params: dict, guild, message: str | None = None, ok: bool = True) -> dict:
        """A guild reply. Any `result_message` shows in a "Guild" dialog, success or not.
        While a raid is set it also carries the raid, so the Wars tab shows it as soon as
        the player creates or joins a guild (and stops showing it when they leave)."""
        reply = self.envelope(params)
        reply["result"] = ok
        if message:
            reply["result_message"] = [message]
        reply.update(self._guild_parts(guild, params))
        if self._set_raid() is not None:
            state = self._raid_state(guild)
            reply["current_raids"] = raids.current_raids(state[0], state[1], self._mini_packs()) if state else []
            reply["raid_info"] = self._raid_info(state) if state else None
        reply.update(self._war_parts(guild))
        reply.update(self._conquest_parts(guild))                # the Wars tab's Conquest button
        return reply

    def _guild_rows(self, rows: list[dict], ranked: bool = False) -> list[dict]:
        rows = sorted(rows, key=lambda g: (-guilds.total_rating(g, self._guild_info), g["id"]))
        return [guilds.list_json(g, self._guild_info, i if ranked else 0) for i, g in enumerate(rows, 1)]

    def _guild_message(self, message: str, params: dict) -> tuple[dict, str]:
        """The guild calls (guilds.py): creating, joining and leaving, invitations, ranks,
        settings, chat, the guild lists, donations and the fortress."""
        store, uid, now = self.guild_store, str(self.uid), int(self.clock())
        me = self._guild_info(uid) or guilds.UNKNOWN
        guild = self._my_guild()
        try:
            if message == "createFaction":
                guild = guilds.create(store, uid, me["name"], params.get("faction_name"), now)
                return self._guild_reply(params, guild), f"created guild {guild['name']} ({guild['id']})"
            if message in ("joinFaction", "acceptFactionInvite"):
                guild = guilds.join(store, params.get("faction_id"), uid, me["name"], me["level"], now,
                                    invited=message == "acceptFactionInvite")
                return self._guild_reply(params, guild), f"joined guild {guild['name']} ({guild['id']})"
            if message == "getRecruitingFactions":
                rows = [g for g in store.all() if g["recruitment_status"] != guilds.CLOSED
                        and len(g["members"]) < guilds.MAX_MEMBERS and uid not in g["members"]]
                reply = self._guild_reply(params, guild)
                reply["factions"] = self._guild_rows(rows)
                return reply, f"guilds taking members: {len(rows)}"
            if message == "getTopFactions":
                reply = self._guild_reply(params, guild)
                reply["factions"] = self._guild_rows(store.all(), ranked=True)
                return reply, f"top guilds: {len(reply['factions'])}"
            if message == "getProfileData":
                return self._member_profile(params, guild)
            if guild is None:
                if message in ("updateFaction", "getFactionNews", "updateFactionWar"):
                    reply = self._guild_reply(params, None)
                    return reply, f"no guild; {len(reply['faction_invites'])} invitation(s)"
                if message == "getUsersLFG":
                    reply = self._guild_reply(params, None)
                    reply["user_lfg"] = []
                    return reply, "players looking for a guild: you're not in one"
                raise guilds.GuildError("You aren't in a guild.")
            if message == "leaveFaction":
                left, successor = guilds.leave(store, uid, me["name"], now, self._name_of)
                how = f"left guild {left['name']}" + ("; it had no one left, so it's disbanded" if not left["members"]
                                                    else f"; {self._name_of(successor)} leads it now" if successor else "")
                return self._guild_reply(params, None), how
            if message in ("updateFaction", "getFactionNews", "updateFactionWar"):
                self._raid_state(guild)                         # a lost raid level shows in the news
                guild = self._my_guild()
                reply = self._guild_reply(params, guild)
                return reply, (f"guild {guild['name']}: {len(guild['members'])} member(s), "
                               f"{guild['guild_points']} guild points, {len(reply['faction_news'])} news line(s)")
            if message == "getUsersLFG":
                wanted = str(params.get("name") or "").lower()
                looking = [str(u) for u in self.player_ids()                    # players without a guild
                           if str(u) != uid and store.of_member(u) is None]
                rows = [{"faction_id": "0", "user_id": u, **(self._guild_info(u) or guilds.UNKNOWN), "gp_earned": 0,
                         "member_role": 0} for u in looking if wanted in self._name_of(u).lower()]
                reply = self._guild_reply(params, guild)
                reply["user_lfg"] = rows
                return reply, f"players looking for a guild: {len(rows)}"
            if message == "sendFactionInvite":
                target = str(params.get("target_user_id"))
                if self._guild_info(target) is None:
                    raise guilds.GuildError("There's no such player on this server.")
                guilds.invite(guild, uid, target, store.of_member(target) is not None, now)
                store.save(guild)
                return self._guild_reply(params, guild, f"{self._name_of(target)} has been invited."), \
                    f"invited {self._name_of(target)} to {guild['name']}"
            if message == "kickFactionMember":
                target = str(params.get("target_user_id"))
                event, war_state = self._war_state()
                if event is not None and wars.owes_rewards(war_state, event, target, guild["id"], now):
                    raise guilds.GuildError(f"{self._name_of(target)} hasn't claimed their war rewards yet.")
                guilds.kick(guild, uid, me["name"], target, self._name_of(target), now)
                store.save(guild)
                return self._guild_reply(params, guild), f"removed {self._name_of(target)} from {guild['name']}"
            if message == "setMemberRank":
                target = str(params.get("target_user_id"))
                try:
                    rank = int(params.get("rank"))
                except (TypeError, ValueError):
                    raise guilds.GuildError("That rank change isn't possible.")
                what = guilds.set_rank(guild, uid, target, rank, self._name_of(target), now)
                store.save(guild)
                return self._guild_reply(params, guild), f"{self._name_of(target)} {what}"
            if message == "setAllFactionSettings":
                guilds.settings(guild, uid, params.get("text"), params.get("recruitment_status"), params.get("min_level"))
                store.save(guild)
                return self._guild_reply(params, guild), (
                    f"guild settings: recruitment {guilds.RECRUITMENT[guild['recruitment_status']]}, "
                    f"minimum level {guild['min_level']}")
            if message == "sendFactionMessage":
                entry = guilds.chat(guild, uid, me["name"], params.get("chat"), now)
                store.save(guild)
                return self._guild_reply(params, guild), f"guild chat: {me['name']}: {entry['data']['message']}"
            if message == "donateFactionGold":
                return self._donate(params, guild, me)
            if message in ("buyFactionCard", "removeFactionCard"):
                return self._fortress(message, params, guild, me)
        except guilds.GuildError as e:
            return self._guild_reply(params, self._my_guild(), str(e), ok=False), f"refused {message}: {e}"
        raise NotSupported(f"'{message}' is not supported yet.")

    def _member_profile(self, params: dict, guild) -> tuple[dict, str]:
        """getProfileData target_user_id: a guild member's profile (an empty user_id tells the
        client they must share a guild)."""
        target = str(params.get("target_user_id"))
        info = self._guild_info(target)
        reply = self._ok(params)
        if info is None or guild is None or target not in guild["members"]:
            reply["player_info"] = {"user_id": ""}
            return reply, f"profile of {target}: not in your guild"
        player = self._player_of(target)
        deck = self._deck_of(player)
        defense = self._defense_deck_of(player)
        as_json = lambda d: {"commander_id": d.get("commander_id", 0),                      # noqa: E731
                             "cards": [c for c, n in (d.get("cards") or {}).items() for _ in range(n)]}
        stars = sum(int(bool(m.get("complete"))) + int(bool(m.get("star2"))) + int(bool(m.get("star3")))
                    for m in (player.missions or {}).values())
        reply["player_info"] = {"user_id": target, "name": info["name"], "rating": info["rating"],
                                "level": info["level"], "level_name": level_info(info["level"])[0],
                                "stars_earned": stars,
                                "faction_war_points": -1, "last_update_time": info["last_update_time"],
                                "deck": as_json(deck), "defense_deck": as_json(defense)}
        return reply, f"profile of {info['name']}"

    def _donate(self, params: dict, guild: dict, me: dict) -> tuple[dict, str]:
        """donateFactionGold amount=<gold>: gold_per_point gold a guild point (content.toml)."""
        c = self.content()
        if not c.guild_donations:
            raise guilds.GuildError("Donations are switched off on this server.")
        player = self.player()
        try:
            gold = int(params.get("amount"))
        except (TypeError, ValueError):
            raise guilds.GuildError("How much gold?")
        cost = gold // c.gold_per_point * c.gold_per_point
        if cost > player.gold:
            raise guilds.GuildError(f"Not enough gold: that's {cost}, and you have {player.gold}.")
        points = guilds.donate(guild, self.uid, me["name"], cost, c.gold_per_point, int(self.clock()))
        player.gold -= cost
        self.player_store.save(player)
        self.guild_store.save(guild)
        reply = self._guild_reply(params, guild)
        reply.update(self._player_parts(params, ("user_data",)))
        return reply, f"donated {cost} gold to {guild['name']} for {points} guild points ({guild['guild_points']} now)"

    def _fortress(self, message: str, params: dict, guild: dict, me: dict) -> tuple[dict, str]:
        """buyFactionCard slot_id card_id / removeFactionCard slot_id (guilds.py). Not
        while the guild's war is past its preparation (the client's own words)."""
        cards, _ = self._engine_data()
        now = int(self.clock())
        _, war_state = self._war_state()
        m = wars.match_of(war_state, guild["id"])
        if m is not None and wars.battle_phase(m, now):
            raise guilds.GuildError("You can't edit fortress cards while at war.")
        if message == "removeFactionCard":
            name = guilds.remove_card(guild, self.uid, me["name"], params.get("slot_id"), cards, now)
            self.guild_store.save(guild)
            return self._guild_reply(params, guild), f"fortress: removed {name} from slot {params.get('slot_id')}"
        card = cards.get(params.get("card_id")) if str(params.get("card_id", "")).isdigit() else None
        what, cost = guilds.buy_card(guild, self.uid, me["name"], params.get("slot_id"), card, cards, now)
        self.guild_store.save(guild)
        return self._guild_reply(params, guild), (
            f"fortress: {what} {card.name} (level {card.level}) in slot {params.get('slot_id')}"
            + (f" for {cost} guild points ({guild['guild_points']} left)" if cost else ""))
