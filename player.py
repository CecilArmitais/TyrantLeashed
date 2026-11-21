"""View or edit a player's save and the server's gold bonuses (state/settings.json).

On a multi-player server (run.py's default: state/accounts.json and state/players/),
choose the player with --user; with only one player it's that one. A single-player
server's save is state/player.json.

    python server/player.py players            # list the players: id, name, level, gold, guild
    python server/player.py --user 1000002 gold 500
    python server/player.py name Night Hawk    # rename the player (the game has no way to)

    python server/player.py                    # show gold, war bonds, energy, tutorial, missions,
                                               # arena, cards, deck, and the gold bonuses
    python server/player.py gold 500           # set gold
    python server/player.py bonds 100          # set war bonds
    python server/player.py sp 500             # set salvage points (SP, for upgrades)
    python server/player.py energy 120         # set energy (it refills from now on)
    python server/player.py max-energy 300     # set the cap that refilling stops at
    python server/player.py card 1001 +2       # add 2 copies of card 1001
    python server/player.py card 1001 -1       # remove one copy
    python server/player.py card 1001 =3       # set the count (0 removes the card)
    python server/player.py music off          # music on/off (the game's own toggle saves it too)
    python server/player.py sound off          # sound effects on/off
    python server/player.py tutorial skip      # mark the tutorial finished (and mission 1 won,
                                               # as finishing it does); the next login goes
                                               # straight to the map
    python server/player.py reset --yes        # delete the save; the next login starts it
                                               # again from the starting data
    python server/player.py mission-gold 7     # missions pay their listed gold x 7 (the default)
    python server/player.py mission-gold off   # ... x 1; "on" brings the multiplier back
    python server/player.py arena-gold 3       # AI rivals pay 25 x 3 (the default); also off/on

Works while the server runs: it re-reads the save on change, and the game shows the
new values after its next server call (a reload, or opening any screen that asks
the server). Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import achievements  # noqa: E402
from game_api import ASSET_DIR, FIXTURE_DIR, STATE_DIR, GameApi  # noqa: E402
from player_state import level_info  # noqa: E402

CARD_XML_DIR = ASSET_DIR
CLOCK = time.time  # replaced in tests


def card_names(ids: set[str], xml_dir: Path | None = None) -> dict[str, str]:
    """id -> "Name" or "Name (level N)" for the given card ids, from cards_section_*.xml."""
    xml_dir = CARD_XML_DIR if xml_dir is None else xml_dir
    wanted, found = set(ids), {}
    files = sorted(xml_dir.glob("cards_section_*.xml"), key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
    for f in files:
        if not wanted - found.keys():
            break
        for _, unit in ET.iterparse(f, events=("end",)):
            if unit.tag != "unit":
                continue
            uid, name = unit.findtext("id"), unit.findtext("name") or "?"
            if uid in wanted:
                found[uid] = name
            for up in unit.findall("upgrade"):
                cid = up.findtext("card_id")
                if cid in wanted:
                    found[cid] = f"{name} (level {up.findtext('level')})"
            unit.clear()
    return found


def fmt_wait(seconds: int) -> str:
    h, rem = divmod(max(0, seconds), 3600)
    return f"{h}h {rem // 60}m" if h else f"{rem // 60}m {rem % 60}s"


def on_off(flag) -> str:
    """A "1"/"0" flag in words."""
    return "on" if str(flag) == "1" else "off"


def tutorial_status(flag, names: list[str]) -> str:
    """The tutorial flag in words: it's the index of the current part, -1 when done."""
    try:
        step = int(str(flag))
    except ValueError:
        return f"unknown ({flag!r})"
    if step == -1:
        return "finished"
    if step < -1:
        return f"switched off ({step}; the game does this when it loads with mission 1 already won)"
    name = f": {names[step]}" if 0 <= step < len(names) else ""
    return f"part {step + 1} of {len(names) or '?'}{name}"


def show(api: GameApi) -> None:
    p = api.player()
    store = api.player_store
    who = "Offline player" if api.single_player else f"Player {api.uid}"
    print(f"{who} save: {store.path}")
    print(f"  gold       {p.gold} ({p.gold_earned or 0} earned in all)")
    print(f"  war bonds  {p.war_bonds}")
    print(f"  SP         {p.salvage or 0} (salvage points)")
    if p.energy >= p.max_energy:
        energy = "full" if p.energy == p.max_energy else "over the max; refilling resumes below it"
    else:
        missing = p.max_energy - p.energy
        next_in = api.energy_every - (int(api.clock()) - p.energy_updated)
        energy = f"next point in {fmt_wait(next_in)}, full in {fmt_wait(next_in + (missing - 1) * api.energy_every)}"
    print(f"  energy     {p.energy} / {p.max_energy} ({energy})")
    print(f"  settings   music {on_off(p.flags.get('music'))}, sound {on_off(p.flags.get('sound'))}, "
          f"battle speed {p.flags.get('speed') or '1'}x, autopilot {on_off(p.flags.get('autopilot'))}")
    print(f"  tutorial   {tutorial_status(p.flags.get('tutorial'), api.tutorials)}")
    a = p.arena
    name, _, nxt = level_info(a["level"])
    print(f"  arena      level {a['level']} {name}, rating {a['rating']}/{nxt}, {a['hunting_wins']} wins "
          f"of {a['hunts_fought']} fights, stamina {a['stamina']}/{a['max_stamina']}, "
          f"{a['promotion_points']} promotion points")
    won = sorted(p.missions, key=lambda m: int(m) if m.isdigit() else 0)
    print(f"  missions   {', '.join(won) + ' won' if won else 'none won yet'} "
          f"({p.mission_wins} wins, {p.stars} stars)")
    st = p.achievements or {}
    daily = st.get("daily") or {}
    defs = achievements.load(api._assets)
    goals = ", ".join(f"{defs[i].desc.replace('Battle: ', '')} "
                      + ("(done, tap Complete)" if i in daily.get("ready", []) else
                         f"({daily.get('progress', {}).get(str(i), 0)}/{defs[i].goal})")
                      for i in daily.get("ids", []) if i in defs and i not in daily.get("claimed", []))
    left = int(daily.get("end_time", 0)) - int(api.clock())
    crate = p.daily_bonus or {}
    wait = int(crate.get("next", 0)) - int(api.clock())
    print(f"  crate      {'ready' if wait <= 0 else 'opens in ' + fmt_wait(wait)}"
          f" ({crate.get('streak', 0)} day{'' if crate.get('streak', 0) == 1 else 's'} in a row)")
    print(f"  upgrades   max energy {p.max_energy}, {p.max_cards or 1600} card slots, {p.max_decks or 1} deck slots"
          + (f", name {p.name!r}" if p.name else ""))
    print(f"  objectives {len(st.get('done', []))} done; today: {goals or 'none yet'}"
          + (f" (new ones in {fmt_wait(left)})" if goals and left > 0 else ""))
    print(f"  cards      {p.card_count} ({len(p.cards)} different)")
    names = card_names(set(p.cards))
    for cid in sorted(p.cards, key=lambda c: (names.get(c, "~"), int(c) if c.isdigit() else 0)):
        c = p.cards[cid]
        used = f", {c['num_used']} used" if c["num_used"] else ""
        used += ", locked" if c.get("is_locked") else ""
        print(f"    {cid:>6}  x{c['num_owned']:<3} {names.get(cid, '(not in the card data)')}{used}")
    for did, d in sorted((p.decks or {}).items()):
        n = sum(d["cards"].values())
        cards = ", ".join(f"{names.get(c) or card_names({c}).get(c, c)}" + (f" x{k}" if k > 1 else "")
                          for c, k in sorted(d["cards"].items(), key=lambda kv: int(kv[0])))
        cmd = card_names({str(d["commander_id"])}).get(str(d["commander_id"]), d["commander_id"])
        print(f"  deck {did}     {cmd}, {n} cards: {cards}")
    print(f"  gold bonus {api.settings().describe()}")
    if p.created_from:
        print(f"  started from recorded session {p.created_from.get('session')} (account {p.created_from.get('account')})")


def set_bonus(api: GameApi, cmd: str, setting: str) -> None:
    """mission-gold / arena-gold: a whole-number multiplier (which also turns it on), or on/off."""
    settings = api.settings()
    bonus = settings.mission_gold if cmd == "mission-gold" else settings.arena_gold
    if setting in ("on", "off"):
        bonus.on = setting == "on"
    elif setting.isdigit() and int(setting) >= 1:
        bonus.multiplier, bonus.on = int(setting), True
    else:
        sys.exit("Give a whole number, 1 or more, or on/off.")
    api.settings_store.save(settings)
    what = "Missions pay their listed gold" if cmd == "mission-gold" else "AI rivals pay 25 gold"
    print(f"{what} x{bonus.effective}" + ("" if bonus.on else f" (the bonus, x{bonus.multiplier}, is off)") + ".")
    if cmd == "mission-gold":
        print("Rewards change at once; the game's own mission popup shows it after the next login (reload).")


def list_players(api: GameApi) -> None:
    ids = api.player_ids()
    if not ids:
        print("No players yet: each browser that logs in gets an account.")
        return
    print(f"{len(ids)} player{'s' * (len(ids) != 1)}:")
    for uid in ids:
        p = api._player_of(uid)
        guild = api.guild_store.of_member(uid)
        seen = (api.accounts.get(uid) or {}).get("last_seen")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(seen)) if seen else "never"
        if p is None:
            print(f"  {uid}  (no save yet)  last seen {when}")
            continue
        level = (p.arena or {}).get("level", 1)
        print(f"  {uid}  {p.name or '?':20}  level {level:<3} {p.gold:>7} gold  "
              f"guild {guild['name'] if guild else '-':16}  last seen {when}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", default=str(FIXTURE_DIR), help=argparse.SUPPRESS)
    ap.add_argument("--state", help=argparse.SUPPRESS)
    ap.add_argument("--user", help="the player's user id (multi-player servers; `players` lists them)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("players", help="list the players")
    nm = sub.add_parser("name", help="rename the player")
    nm.add_argument("new_name", nargs="+")
    for name, what in (("gold", "gold"), ("bonds", "war bonds"), ("sp", "salvage points (SP)"),
                       ("energy", "energy"), ("max-energy", "the energy cap that refilling stops at")):
        s = sub.add_parser(name, help=f"set {what}")
        s.add_argument("value", type=int)
    c = sub.add_parser("card", help="change how many copies of a card you own")
    c.add_argument("card_id")
    c.add_argument("change", help="+N to add, -N to remove, =N to set")
    c.add_argument("--any", action="store_true", help="allow an id that isn't in the card data")
    for name, what in (("music", "music"), ("sound", "sound effects")):
        s = sub.add_parser(name, help=f"turn {what} on or off")
        s.add_argument("switch", choices=["on", "off"])
    t = sub.add_parser("tutorial", help="skip the tutorial")
    t.add_argument("action", choices=["skip"])
    for name, what in (("mission-gold", "missions' gold multiplier"), ("arena-gold", "AI rivals' gold multiplier")):
        s = sub.add_parser(name, help=f"set the {what} (a whole number, 1 or more), or turn it on/off")
        s.add_argument("setting")
    r = sub.add_parser("reset", help="delete the save")
    r.add_argument("--yes", action="store_true", help="really delete it")
    args = ap.parse_args()

    fixtures = Path(args.fixtures)
    state = Path(args.state) if args.state else (STATE_DIR if fixtures.resolve() == FIXTURE_DIR.resolve()
                                                 else fixtures.parent / "state")
    multi = (state / "accounts.json").is_file() or (state / "players").is_dir()
    api = GameApi(fixture_dir=fixtures, state_dir=state, clock=lambda: CLOCK(), single_player=not multi)
    if args.cmd == "players":
        return list_players(api)
    if multi:
        ids = api.player_ids()
        if args.user:
            if not str(args.user).isdigit() or int(args.user) not in ids:
                sys.exit(f"There's no player {args.user}. `python server/player.py players` lists them.")
            api.use_player(int(args.user))
        elif len(ids) == 1:
            api.use_player(ids[0])
        elif not ids:
            sys.exit("No players yet: each browser that logs in gets an account.")
        else:
            sys.exit(f"This server has {len(ids)} players: choose one with --user "
                     "(`python server/player.py players` lists them).")
    elif args.user and int(args.user) != api.uid:
        sys.exit("This is a single-player server: its one player is 1000001.")
    if api.player_store is None:
        sys.exit("No starting data (game/fixtures/init.json). Build it with: python server/make_fixtures.py")
    store = api.player_store

    if args.cmd in (None, "show"):
        return show(api)
    if args.cmd in ("mission-gold", "arena-gold"):
        return set_bonus(api, args.cmd, args.setting)
    if args.cmd == "reset":
        if not args.yes:
            sys.exit("This deletes the saved gold, war bonds, energy and cards. Run again with --yes to do it.")
        store.delete()
        print(f"Deleted {store.path}. The next login starts a new save from the starting data.")
        return

    p = api.player()
    if args.cmd == "name":
        new = " ".join(args.new_name).strip()[:20]
        if not new:
            sys.exit("Give a name.")
        old, p.name = p.name, new
        store.save(p)
        print(f"Renamed {old or 'the player'} to {new}. The game shows it after the next login (reload).")
        return
    if args.cmd in ("music", "sound"):
        p.flags[args.cmd] = "1" if args.switch == "on" else "0"   # as the game's own toggle stores it
        store.save(p)
        print(f"{args.cmd.capitalize()} {args.switch}. The game picks it up at the next login (reload).")
        return
    if args.cmd == "tutorial":
        if str(p.flags.get("tutorial")).startswith("-"):
            print("The tutorial is already finished.")
            return
        # Finishing the tutorial means winning mission 1 (it ends as mission 2 starts), with
        # its reward paid as if it had been played: gold, and its card into the deck.
        p.flags["tutorial"] = "-1"
        store.save(p)
        paid = ""
        if "1" not in p.missions:
            result = api._finish_mission("1", True)
            if result is None:
                p = api.player()
                p.won_mission("1")
                store.save(p)
            else:
                paid = f": {result.summary()}"
        print(f"Tutorial marked finished, and mission 1 won{paid}. The next login (reload the game) goes "
              "straight to the map, with mission 2 next.")
        return
    if args.cmd in ("gold", "bonds", "sp", "energy", "max-energy"):
        if args.value < 0 or (args.cmd == "max-energy" and args.value < 1):
            sys.exit("That value is too low.")
        if args.cmd == "gold":
            p.gold = args.value
        elif args.cmd == "sp":
            p.salvage = args.value
        elif args.cmd == "bonds":
            p.war_bonds = args.value
        elif args.cmd == "max-energy":
            p.max_energy = args.value
        else:
            p.energy, p.energy_updated = args.value, int(api.clock())
        store.save(p)
        print(f"{args.cmd} set to {args.value}.")
        return

    m = re.fullmatch(r"([+=-])(\d+)", args.change)
    if not m:
        sys.exit("The change must look like +2, -1 or =3.")
    cid, op, n = args.card_id, m.group(1), int(m.group(2))
    name = card_names({cid}).get(cid)
    if name is None and not args.any:
        sys.exit(f"Card {cid} isn't in the card data. Use --any to add it anyway.")
    have = p.cards.get(cid, {"num_owned": 0, "num_used": 0})
    count = have["num_owned"] + n if op == "+" else have["num_owned"] - n if op == "-" else n
    if count < 0:
        sys.exit(f"You only have {have['num_owned']} of card {cid}.")
    if count == 0:
        p.cards.pop(cid, None)
    else:
        p.cards[cid] = {"num_owned": count, "num_used": min(have["num_used"], count)}
    trimmed = []
    for did, d in (p.decks or {}).items():            # a deck can't use more copies than you own
        if d["cards"].get(cid, 0) > count:
            trimmed.append(did)
            if count:
                d["cards"][cid] = count
            else:
                del d["cards"][cid]
    store.save(p)
    print(f"Card {cid} ({name or 'unknown'}): {have['num_owned']} -> {count}.")
    if trimmed:
        print(f"  Deck {', '.join(trimmed)} used more than that, so it now has {count}.")


if __name__ == "__main__":
    main()
