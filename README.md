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

Before the game, the server shows its own sign-in page. A player types the name they
want in the game: a new name makes a new player, and a name already on the server signs
in as that player, from any browser. Several players can play at once, each with their
own save (`state/players/<user_id>.json`). They meet in guilds, raids, guild wars,
brawls and conquest, and as each other's arena rivals (their defense decks, played by
the AI).

- **No password by default:** whoever types a name plays as that player, which suits a
  server among friends. `passwords = true` under `[accounts]` in `content.toml` asks for
  one, set the first time a name signs in and kept hashed. Tell players never to reuse a
  password from another site, Kongregate's above all.
- A browser stays signed in for a year. `/signin` (the server's address plus `/signin`)
  switches players, signs out, or downloads the player's save.
- With `--host 0.0.0.0`, anyone who can reach the port can sign in, and everything goes
  over plain HTTP. Only open it on a network you trust.

**Bringing progress.** A new name can bring a save from another server like this one
(`/signin` downloads one), or a player's login data from the official game. The page
explains how to save the latter from the browser's developer tools; it reads the file
in the browser and sends only the game data, never the passwords or Kongregate session
in it. A host can also turn that data into a save with `python server/convert_login.py`.
Nothing can check either, so `imports` under `[accounts]` decides: `"off"` takes none,
`"review"` (the default) keeps each one waiting until the host accepts it, and `"on"`
loads it at once.

`python server/player.py players` lists the players. `python server/player.py --user <id or name>`
shows a player's save and changes it: gold, war bonds, SP, energy and its cap, cards,
items, music and sound, the tutorial, the name, or `reset --yes` to start it over.
`player.py imports` lists the progress waiting for an OK, with `accept-import` and
`reject-import`; `export` writes a player's save to a file; `password-reset` clears a
sign-in password. `python server/player.py --help` has every command.

## Setting up the server

`state/content.toml`, written with comments on every setting the first time the server
starts, sets what's on offer: what the store sells, event boxes, the raid, brawls,
conquest, the objective challenge, guild wars, guild donations, how often other players
turn up as arena rivals, the daily crate, and how players sign in (`[accounts]`; a file
written before that section existed lacks it, and gets the defaults: names only, imports
reviewed). The server re-reads it when it changes,
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
| `accounts.json` | every account: its name, the browsers' passwords and sessions (as hashes), a sign-in password if it has one; made at the first sign-in |
| `players/<user_id>.json` | each player's save |
| `content.toml` | what's on offer (above); written at the first start |
| `settings.json` | the gold bonuses, once `player.py` changes them |
| `guilds/<id>.json`, `guilds/counters.json` | each guild, and the next guild and news ids |
| `wars.json`, `brawls.json`, `conquest.json` | the server's guild war, brawl and conquest runs |
| `challenge.json` | the objective challenge's current run |
| `imports/<user_id>.json` | progress a player brought, waiting for the host's OK |

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
