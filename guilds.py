"""Guilds (the game's "factions"), kept by the server apart from any player's save, so
that a private server's players can share them.

One file per guild, state/guilds/<faction_id>.json, each written whole and atomically,
so changes to one guild never touch another's (and the layout maps onto one database
row per guild later). A guild holds its settings, its members by user id (rank, the
guild points they brought in, when they joined), invitations, its news and chat, its
guild points and fortress cards, and the raid it's fighting (raids.py). A member's
name, level and commander come from their own save whenever the guild is shown.

The client's formats and what each call needs back are in docs/protocol.md, "Guilds".
Standard library only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonfile

FIRST_GUILD_ID = 900001
MAX_MEMBERS = 50          # the client's default max_members (it doesn't enforce it)
MAX_LEVEL = 30            # the client's default max_level: the settings' minimum level runs 0..30
MEMBER, OFFICER, LEADER = 1, 21, 31
OPEN, INVITE_ONLY, CLOSED = 0, 1, 2
RECRUITMENT = {OPEN: "open", INVITE_ONLY: "invite-only", CLOSED: "closed"}
NAME_MIN, NAME_MAX = 3, 16
MESSAGE_MAX = 200         # the settings popup's limit
CHAT_MAX = 125            # the chat box's limit
NEWS_KEPT = 300           # news and chat lines a guild keeps
NEWS_BACKLOG = 50         # sent to a client that has none yet (last_activity_id 0)
CORE_SLOT = 1             # holds the guild's battleground effect, never a card
DEFENSE_SLOTS, SIEGE_SLOTS = (11, 12), (21, 22)
CARD_SLOTS = DEFENSE_SLOTS + SIEGE_SLOTS
SLOT_HEALTH = 100         # the client's default max_slot_health

# News and chat types, as the client shows them (docs/protocol.md, "Guilds")
JOINED, KICKED, LEFT, NEW_LEADER, CHAT = 1, 2, 3, 4, 5
CARD_BUY, CARD_UPGRADE, CARD_REMOVE = 21, 22, 23
RAID_STARTED, RAID_WON, RAID_LOST = 31, 32, 33
DONATED = 41


class GuildError(Exception):
    """Why a guild action can't happen, in words the player can read."""


# ---- the store -----------------------------------------------------------------------------------

class GuildStore:
    """state/guilds/<faction_id>.json, one guild per file, and counters.json: the next
    guild id and the next news id, server-wide. Guild ids are never reused (a disbanded
    guild's file goes, its id doesn't come back), and news ids only grow across all
    guilds: the client remembers the ids it has shown and skips them, even after
    switching guilds."""

    COUNTERS = "counters.json"

    def __init__(self, root: Path):
        self.root = Path(root)

    def _counters(self) -> dict:
        counters = jsonfile.read(self.root / self.COUNTERS)
        if counters is not None:
            return counters
        guilds = self.all()                             # a store from before counters.json
        return {"next_guild_id": max([g["id"] + 1 for g in guilds], default=FIRST_GUILD_ID),
                "next_activity_id": max([e["activity_id"] + 1 for g in guilds for e in g.get("news", [])
                                         if e.get("activity_id")], default=1)}

    def _take(self, key: str, n: int = 1) -> int:
        """The next n values of a counter (the first one returned)."""
        counters = self._counters()
        first = int(counters[key])
        counters[key] = first + n
        jsonfile.write(self.root / self.COUNTERS, counters)
        return first

    def path(self, guild_id) -> Path:
        return self.root / f"{int(guild_id)}.json"

    def load(self, guild_id) -> dict | None:
        try:
            gid = int(guild_id)
        except (TypeError, ValueError):
            return None
        return jsonfile.read(self.path(gid))

    def save(self, guild: dict) -> None:
        """Writes the guild, first giving its new news entries their ids."""
        fresh = [e for e in guild.get("news", []) if not e.get("activity_id")]
        if fresh:
            first = self._take("next_activity_id", len(fresh))
            for i, e in enumerate(fresh):
                e["activity_id"] = first + i
        jsonfile.write(self.path(guild["id"]), guild)

    def delete(self, guild_id) -> None:
        f = self.path(guild_id)
        if f.is_file():
            f.unlink()

    def all(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        out = []
        for f in sorted(self.root.glob("*.json")):
            if f.stem.isdigit():
                out.append(jsonfile.read(f))
        return sorted(out, key=lambda g: g["id"])

    def of_member(self, user_id) -> dict | None:
        uid = str(user_id)
        return next((g for g in self.all() if uid in g["members"]), None)

    def by_name(self, name: str) -> dict | None:
        key = name.lower()
        return next((g for g in self.all() if g["name"].lower() == key), None)

    def next_id(self) -> int:
        return self._take("next_guild_id")


# ---- rules -----------------------------------------------------------------------------------------

def name_problem(name: str) -> str | None:
    """The client's own checks and texts (CreateGuildPopup), or None if the name is fine."""
    if len(name) < NAME_MIN:
        return f"Invalid name! Must be at least {NAME_MIN} characters."
    if len(name) > NAME_MAX:
        return f"Invalid name! Must be at most {NAME_MAX} characters."
    if not re.fullmatch(r"[A-Za-z0-9]+", name):
        return "Invalid name! Only letters and numbers are allowed. Please try again."
    return None


def role(guild: dict, user_id) -> int:
    m = guild["members"].get(str(user_id))
    return int(m["role"]) if m else 0


def clean_text(text: str, limit: int) -> str:
    """Text the client can show back: it strips every backslash from a news entry
    before parsing it, so quotes and backslashes can't survive the JSON. [ ] are its
    own colour codes (the chat box removes them too)."""
    text = str(text).replace("\\", "").replace('"', "'").replace("[", "").replace("]", "")
    return " ".join(text.split())[:limit]


def add_news(guild: dict, kind: int, data: dict, now: int) -> dict:
    """A news or chat line. Its activity_id comes when the guild is saved (GuildStore)."""
    entry = {"activity_id": None, "type": kind, "time": int(now),
             "data": {k: clean_text(v, 200) if isinstance(v, str) else v for k, v in data.items()}}
    guild.setdefault("news", []).append(entry)
    del guild["news"][:-NEWS_KEPT]
    return entry


def news_since(guild: dict, last_id) -> list[dict]:
    """The lines the client hasn't got, oldest first (it appends them in reply order and
    sends the last one back as last_activity_id). If that id isn't one of this guild's
    (a new screen, another guild, or older than what's kept), the recent backlog; the
    client skips any line it has already shown."""
    try:
        last = int(last_id or 0)
    except (TypeError, ValueError):
        last = 0
    news = [e for e in guild.get("news", []) if e.get("activity_id")]
    ids = [e["activity_id"] for e in news]
    if last not in ids:
        return news[-NEWS_BACKLOG:]
    return news[ids.index(last) + 1:]


def new_guild(guild_id: int, name: str, leader_id, now: int) -> dict:
    return {"id": int(guild_id), "name": name, "created": int(now), "leader_id": str(leader_id),
            "message": "", "recruitment_status": OPEN, "min_level": 0,
            "members": {str(leader_id): {"role": LEADER, "joined": int(now), "gp_earned": 0}},
            "invites": {}, "guild_points": 0, "cards": {}, "slots": {}, "news": [], "raid": None}


def create(store: GuildStore, user_id, player_name: str, name: str, now: int) -> dict:
    name = str(name or "").strip()
    if store.of_member(user_id):
        raise GuildError("You're already in a guild.")
    problem = name_problem(name)
    if problem:
        raise GuildError(problem)
    if store.by_name(name):
        raise GuildError(f"There's already a guild called {store.by_name(name)['name']}.")
    guild = new_guild(store.next_id(), name, user_id, now)
    add_news(guild, JOINED, {"name": player_name}, now)
    for other in store.all():                       # a guild member can't still be invited elsewhere
        if other["invites"].pop(str(user_id), None) is not None:
            store.save(other)
    store.save(guild)
    return guild


def join(store: GuildStore, guild_id, user_id, player_name: str, level: int, now: int, invited: bool) -> dict:
    """joinFaction (invited False) or acceptFactionInvite (True)."""
    uid = str(user_id)
    if store.of_member(uid):
        raise GuildError("You're already in a guild. Leave it first.")
    guild = store.load(guild_id)
    if guild is None:
        raise GuildError("That guild doesn't exist anymore.")
    was_invited = uid in guild["invites"]
    if invited and not was_invited:
        raise GuildError(f"{guild['name']} hasn't invited you.")
    if len(guild["members"]) >= MAX_MEMBERS:
        raise GuildError(f"{guild['name']} is full.")
    if not was_invited:
        if guild["recruitment_status"] == CLOSED:
            raise GuildError(f"{guild['name']} isn't recruiting.")
        if guild["recruitment_status"] == INVITE_ONLY:
            raise GuildError(f"{guild['name']} takes new members by invitation only.")
        if level < guild["min_level"]:
            raise GuildError(f"{guild['name']} takes members from level {guild['min_level']}.")
    guild["members"][uid] = {"role": MEMBER, "joined": int(now), "gp_earned": 0}
    guild["invites"].pop(uid, None)
    add_news(guild, JOINED, {"name": player_name}, now)
    store.save(guild)
    for other in store.all():
        if other["id"] != guild["id"] and other["invites"].pop(uid, None) is not None:
            store.save(other)
    return guild


def leave(store: GuildStore, user_id, player_name: str, now: int, name_of=lambda uid: "Unknown")         -> tuple[dict, str | None]:
    """The guild left, and who leads it now (a leader leaving hands over to the
    longest-serving officer, else member; the last member leaving disbands it).
    name_of(user_id) names the new leader in the news."""
    uid = str(user_id)
    guild = store.of_member(uid)
    if guild is None:
        raise GuildError("You aren't in a guild.")
    was_leader = guild["members"][uid]["role"] == LEADER
    del guild["members"][uid]
    if not guild["members"]:
        store.delete(guild["id"])
        return guild, None
    add_news(guild, LEFT, {"name": player_name}, now)
    successor = None
    if was_leader:
        successor = min(guild["members"], key=lambda m: (-guild["members"][m]["role"], guild["members"][m]["joined"]))
        guild["members"][successor]["role"] = LEADER
        guild["leader_id"] = successor
        add_news(guild, NEW_LEADER, {"name": name_of(successor)}, now)
    store.save(guild)
    return guild, successor


def invite(guild: dict, by, target_id, target_in_guild: bool, now: int) -> None:
    if role(guild, by) < OFFICER:
        raise GuildError("You do not have Recruitment privileges.")
    tid = str(target_id)
    if tid in guild["members"]:
        raise GuildError("They're already in your guild.")
    if target_in_guild:
        raise GuildError("They're already in a guild.")
    guild["invites"][tid] = {"by": str(by), "time": int(now)}


def kick(guild: dict, by, by_name: str, target_id, target_name: str, now: int) -> None:
    tid = str(target_id)
    if role(guild, by) != LEADER:
        raise GuildError("Only the Guild Leader can remove members.")
    if tid not in guild["members"]:
        raise GuildError("They aren't in your guild.")
    if role(guild, tid) != MEMBER:
        raise GuildError("Demote an officer to member before removing them.")
    del guild["members"][tid]
    add_news(guild, KICKED, {"kicker_name": by_name, "name": target_name}, now)


def set_rank(guild: dict, by, target_id, rank: int, target_name: str, now: int) -> str:
    """setMemberRank: promote a member to officer (21), demote an officer (1), or make an
    officer the leader (31; the old leader becomes an officer). Returns what changed."""
    tid = str(target_id)
    if role(guild, by) != LEADER:
        raise GuildError("Only the Guild Leader can change ranks.")
    if tid not in guild["members"] or tid == str(by):
        raise GuildError("Choose another member of your guild.")
    current = role(guild, tid)
    if rank == OFFICER and current == MEMBER:
        guild["members"][tid]["role"] = OFFICER
        return "promoted to officer"
    if rank == MEMBER and current == OFFICER:
        guild["members"][tid]["role"] = MEMBER
        return "demoted to member"
    if rank == LEADER and current == OFFICER:
        guild["members"][tid]["role"] = LEADER
        guild["members"][str(by)]["role"] = OFFICER
        guild["leader_id"] = tid
        add_news(guild, NEW_LEADER, {"name": target_name}, now)
        return "made the guild leader"
    raise GuildError("That rank change isn't possible.")


def settings(guild: dict, by, text: str, recruitment_status, min_level) -> None:
    if guild["leader_id"] != str(by):
        raise GuildError("Only the Guild Leader can change the settings.")
    try:
        status, level = int(recruitment_status), int(min_level)
    except (TypeError, ValueError):
        raise GuildError("Those settings aren't valid.")
    if status not in RECRUITMENT or not 0 <= level <= MAX_LEVEL:
        raise GuildError("Those settings aren't valid.")
    guild["message"] = clean_text(text or "", MESSAGE_MAX)
    guild["recruitment_status"], guild["min_level"] = status, level


def chat(guild: dict, by, player_name: str, text: str, now: int) -> dict:
    if str(by) not in guild["members"]:
        raise GuildError("You aren't in this guild.")
    message = clean_text(text or "", CHAT_MAX)
    if not message:
        raise GuildError("Type a message first.")
    return add_news(guild, CHAT, {"name": player_name, "message": message}, now)


def donate(guild: dict, by, player_name: str, gold: int, gold_per_point: int, now: int) -> int:
    """donateFactionGold amount=<gold>: amount // gold_per_point guild points. Returns the
    points (the caller takes points × gold_per_point gold)."""
    if str(by) not in guild["members"]:
        raise GuildError("You aren't in this guild.")
    points = int(gold) // max(1, gold_per_point)
    if points < 1:
        raise GuildError(f"A guild point costs {gold_per_point} gold.")
    guild["guild_points"] += points
    guild["members"][str(by)]["gp_earned"] = guild["members"][str(by)].get("gp_earned", 0) + points
    add_news(guild, DONATED, {"name": player_name, "points": points}, now)
    return points


def slot_type(slot: int) -> int:
    return 1 if slot in DEFENSE_SLOTS else 2


def spare(guild: dict, card_id: int) -> int:
    """Copies of a fortress card the guild owns that no slot holds."""
    owned = int(guild["cards"].get(str(card_id), 0))
    return owned - sum(1 for c in guild["slots"].values() if int(c) == int(card_id))


def buy_card(guild: dict, by, player_name: str, slot, card, cards, now: int) -> tuple[str, int]:
    """buyFactionCard slot_id card_id: equip a spare copy for free; else, if the slot holds a
    lower level of the same card, upgrade it (that level's guild points; the lower copy
    becomes the new one); else buy a copy (its guild points) and equip it. Officers and
    the leader only (ours). Returns (what happened, guild points spent)."""
    if role(guild, by) < OFFICER:
        raise GuildError("Only officers and the Guild Leader can change the fortress.")
    try:
        slot = int(slot)
    except (TypeError, ValueError):
        raise GuildError("That isn't a fortress slot.")
    if slot not in CARD_SLOTS:
        raise GuildError("That isn't a fortress slot.")
    if card is None or not card.fortress_type:
        raise GuildError("That isn't a fortress card.")
    if card.fortress_type != slot_type(slot):
        raise GuildError(f"That card goes in a {'defense' if card.fortress_type == 1 else 'siege'} slot.")
    current = cards.get(guild["slots"].get(str(slot), 0)) if guild["slots"].get(str(slot)) else None
    if current is not None and current.id == card.id:
        raise GuildError("That card is already in this slot.")
    if spare(guild, card.id) > 0:
        guild["slots"][str(slot)] = card.id
        add_news(guild, CARD_BUY, {"name": player_name, "card_name": card.name}, now)
        return "equipped", 0
    if guild["guild_points"] < card.gp_cost:
        raise GuildError("You need more Guild Points for that. Earn more by completing specific Objectives "
                         "for your Guild.")
    guild["guild_points"] -= card.gp_cost
    upgrade = current is not None and current.base_id == card.base_id and current.level < card.level
    if upgrade:                                     # the slot's copy becomes the higher level
        guild["cards"][str(current.id)] = int(guild["cards"].get(str(current.id), 1)) - 1
        if guild["cards"][str(current.id)] <= 0:
            del guild["cards"][str(current.id)]
    guild["cards"][str(card.id)] = int(guild["cards"].get(str(card.id), 0)) + 1
    guild["slots"][str(slot)] = card.id
    add_news(guild, CARD_UPGRADE if upgrade else CARD_BUY, {"name": player_name, "card_name": card.name}, now)
    return ("upgraded" if upgrade else "bought"), card.gp_cost


def remove_card(guild: dict, by, player_name: str, slot, cards, now: int) -> str:
    """removeFactionCard slot_id: the slot is emptied; the guild keeps the copy."""
    if role(guild, by) < OFFICER:
        raise GuildError("Only officers and the Guild Leader can change the fortress.")
    key = str(slot)
    if not guild["slots"].get(key):
        raise GuildError("That slot is empty.")
    card = cards.get(guild["slots"].pop(key))
    name = card.name if card else "a card"
    add_news(guild, CARD_REMOVE, {"name": player_name, "card_name": name}, now)
    return name


def invites_for(store: GuildStore, user_id) -> list[dict]:
    uid = str(user_id)
    return [g for g in store.all() if uid in g["invites"]]


# ---- what the client reads ------------------------------------------------------------------------
# `info(user_id)` gives a player's public details: {name, level, rating, commander_id,
# last_update_time}, or None for a player the server doesn't know.

UNKNOWN = {"name": "Unknown", "level": 1, "rating": 0, "commander_id": 1000, "last_update_time": 0}


def _info(info, uid) -> dict:
    return info(uid) or UNKNOWN


def total_rating(guild: dict, info) -> int:
    return sum(int(_info(info, uid)["rating"]) for uid in guild["members"])


def members_json(guild: dict, info) -> list[dict]:
    out = []
    for uid, m in guild["members"].items():
        p = _info(info, uid)
        out.append({"faction_id": str(guild["id"]), "user_id": uid, "name": p["name"], "rating": p["rating"],
                    "level": p["level"], "commander_id": p["commander_id"], "gp_earned": m.get("gp_earned", 0),
                    "member_role": m["role"], "last_update_time": p["last_update_time"]})
    return out


def faction_json(guild: dict, info, gold_per_point: int) -> dict:
    """The player's own guild (`faction`). No faction_war_points / raid_level /
    conquest_points here: any of those replaces the rating the client shows."""
    leader = _info(info, guild["leader_id"])
    return {"faction_id": str(guild["id"]), "name": guild["name"], "leader_id": guild["leader_id"],
            "leader_name": leader["name"], "num_members": len(guild["members"]), "max_members": MAX_MEMBERS,
            "recruitment_status": guild["recruitment_status"], "min_level": guild["min_level"],
            "max_level": MAX_LEVEL, "total_rating": total_rating(guild, info),
            "guild_points": guild["guild_points"], "gp_cost": gold_per_point, "max_slot_health": SLOT_HEALTH,
            "guild_supplies_item": 0, "members": members_json(guild, info),
            "faction_cards": {cid: {"num_owned": n} for cid, n in sorted(guild["cards"].items())},
            "faction_items": {}, "battleground_data": [],
            **({"message": guild["message"]} if guild["message"] else {})}   # an empty one shows as "0"


NO_GUILD = {"faction_id": 0}


def list_json(guild: dict, info, rank: int = 0) -> dict:
    """A row of a guild list (Join a Guild, Top Guilds, invites)."""
    out = {"faction_id": str(guild["id"]), "name": guild["name"],
           "leader_name": _info(info, guild["leader_id"])["name"], "num_members": len(guild["members"]),
           "max_members": MAX_MEMBERS, "total_rating": total_rating(guild, info)}
    if rank:
        out["rank"] = rank
    return out


def fortress_json(guild: dict) -> list[dict]:
    return [{"faction_id": str(guild["id"]), "slot_id": s, "card_id": int(guild["slots"].get(str(s), 0)),
             "health": SLOT_HEALTH} for s in CARD_SLOTS]


def news_json(entries: list[dict]) -> list[dict]:
    """`data` must be a JSON object written as a string (the client parses it after
    stripping backslashes, so the text has none: clean_text)."""
    return [{"type": e["type"], "data": json.dumps(e["data"], ensure_ascii=False, separators=(",", ":")),
             "time": e["time"], "activity_id": e["activity_id"]} for e in entries]
