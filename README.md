# Tyrant Unleashed offline server

> **Made with AI.** This server was written by Claude, an AI model made by Anthropic,
> working at the direction of this repository's owner. That covers the code, its tests,
> its documentation, and the reverse engineering of the game's protocol from recordings
> the owner made. The owner decided what to build and made the calls along the way.
> Every commit says so with a `Co-Authored-By: Claude` line. If you'd rather not use
> AI-written software, this project isn't for you.

Plays the Kongregate web version of Tyrant Unleashed (client 2.30.80) with no
connection to Kongregate or Synapse Games: it serves the game's own client and files
from your machine and answers its API calls itself, including real battles from a
battle engine. Several players can play on one server. Python 3.11 or later; the
server is standard library only (the tests need pytest).

## What you need

This repository holds no game files. You need your own copy of them, beside it:

```
<any folder>/
  server/                      this repository
  game/                        the game's own files
    client/tyu-web-2.30.80/    the web client: index.html, Release/...
    assets/<host>/<path>       the game's files: mobile.tyrantonline.com/assets/*.xml,
                               cdn.synapsegames.com/unleashed/... (bundles, images),
                               with file names as archive_layout.py makes them safe
    fixtures/                  getUserAccount.json, init.json, replay.json: a recorded
                               login and the calls after it (see "Making the fixtures")
  state/                       the server's own data, made as it's needed (see "State")
```

Without the fixtures the game can't log in; without the client and assets there is
nothing to play.

## Playing

```
python server/run.py --engine --open   # http://127.0.0.1:8770/, opened in the browser
python server/run.py --engine --host 0.0.0.0   # let other machines connect too
```

- `--engine`: campaign missions and the arena are real battles from the battle engine.
  Without it they only replay the recorded battles, so after the first few missions
  the game says the mission "hasn't been recorded". Practice fights, raids, guild wars,
  brawls and conquest always use the engine.
- `--port N` listens on another port (8770 by default); `--quiet` logs only problems,
  not every file served; `--single-player` makes every browser the one offline player,
  saved in `state/player.json`.

Stop the server with Ctrl+C; a summary lists anything that was missing, blocked or
unsupported.

## Players and accounts

Each browser that opens the game gets its own account and save on its first login
(`state/players/<user_id>.json`), and several players can play at once. They meet in
guilds, raids, guild wars, brawls and conquest, and as each other's arena rivals (their
defense decks, played by the AI).

- An account lives in the browser's site data. Another browser or profile, another
  address for the same server (`localhost` and `127.0.0.1` are two), or clearing site
  data means a new player; there's no way to move a save to another browser.
- With `--host 0.0.0.0`, anyone who can reach the port can make an account, and the
  game talks to the server over plain HTTP. Only open it on a network you trust.

`python server/player.py players` lists the players. `python server/player.py --user <id>`
shows a player's save and changes it: gold, war bonds, SP, energy and its cap, cards,
items, music and sound, the tutorial, the name, or `reset --yes` to start it over.
`python server/player.py --help` has every command.

## Setting up the server

`state/content.toml`, written with comments on every setting the first time the server
starts, sets what's on offer: what the store sells, event boxes, the raid, brawls,
conquest, the objective challenge, guild wars, guild donations, how often other players
turn up as arena rivals, and the daily crate. The server re-reads it when it changes,
and the game shows the change after a reload.

- A setting with an unusable value is left at its default and reported when the server
  starts (not when the file is re-read later).
- A misspelt setting name is ignored without a word, so check the spelling against the
  comments.
- A file that isn't valid TOML is ignored as a whole: everything goes back to the
  defaults, which turns off a raid or war you had set.

The gold bonuses (missions pay their gold x 7 and AI rivals 25 x 3 by default) are set
with `player.py mission-gold` and `player.py arena-gold`, kept in `state/settings.json`.

## State

`state/` is the whole server, made as it's needed:

| File | What it holds |
|---|---|
| `accounts.json` | every account (the password as a hash); made at the first login |
| `players/<user_id>.json` | each player's save |
| `content.toml` | what's on offer (above); written at the first start |
| `settings.json` | the gold bonuses, once `player.py` changes them |
| `guilds/<id>.json`, `guilds/counters.json` | each guild, and the next guild and news ids |
| `wars.json`, `brawls.json`, `conquest.json` | the server's guild war, brawl and conquest runs |
| `challenge.json` | the objective challenge's current run |

Copy the folder to back the server up. Deleting a file starts that part over at the next
start or request (an event's run, the challenge), but deleting `accounts.json` or a
player's save loses those players, and a guild file its guild.

## Making the fixtures

`make_fixtures.py` builds `game/fixtures/` from a recorded session: a recorded login and
the calls after it, with every account identifier (user id, password, syncode, name,
device id) replaced by the offline player's, checked before anything is written.

```
python server/make_fixtures.py                       # the newest suitable session
python server/make_fixtures.py --session <folder>    # one session
```

It reads sessions from `captures/api/` (raw recordings) or `captures/api-sanitized/`
beside the server (`--captures` picks another folder, `--out` another place to write),
each a folder with an `index.jsonl` and one JSON file per call, as this project's
capture tools record them. Those tools aren't in this repository. Sessions signed in to
Kongregate are refused by default: that's a real account.

## Checking it

```
python -m pytest server -q            # without game/ beside it, tests that need it are skipped
python server/battle_check.py         # replays the recorded battles through the engine
```

`battle_tuo_check.py` runs the optimizer's own test decks through the engine as a crash
test; it needs the optimizer's source in `reference/tuo/` beside the server.

## License and credits

This project is under the MIT license (`LICENSE`). The battle engine follows the rules
of the Tyrant Unleashed Optimizer, rewritten in Python; the optimizer's own MIT notice
is in `battle/LICENSE-tuo.md`. Tyrant Unleashed belongs to Synapse Games; none of its
files are in this repository, and the license covers none of them.
