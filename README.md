# Tyrant Unleashed offline server

Plays the Kongregate web version of Tyrant Unleashed (client 2.30.80) with no
connection to Kongregate or Synapse Games: it serves the game's own client and files
from your machine and answers its API calls itself, including real battles from a
battle engine. Python 3.11 or later, standard library only.

This repository holds no game files. You need your own copy of them, beside it:

```
<any folder>/
  server/                      this repository
  game/                        the game's own files
    client/tyu-web-2.30.80/    the web client: index.html, Release/...
    assets/<host>/<path>       the game's files: mobile.tyrantonline.com/assets/*.xml,
                               cdn.synapsegames.com/unleashed/... (bundles, images)
    fixtures/                  getUserAccount.json, init.json, replay.json: a recorded
                               login, built by make_fixtures.py from a recording
  state/                       made on first start: the players' accounts and saves
                               (accounts.json, players/), the settings, and the
                               server's guilds (guilds/)
```

## Playing

```
python server/run.py --open           # http://127.0.0.1:8770/
python server/run.py --engine         # missions and the arena as real battles
python server/run.py --host 0.0.0.0   # let other machines connect too
```

Each browser that opens the game gets its own account and save on its first login
(`state/players/<user_id>.json`), and several players can play at once. They meet in
guilds and raids, and as each other's arena rivals (their defense decks, played by the
AI). `python server/player.py players` lists them; `python server/player.py --user
<id>` shows a player's save and can change it (gold, war bonds, cards, music and
sound, the tutorial, the name...). `--single-player` makes every browser the one
offline player, saved in `state/player.json`.

`state/content.toml`, written with comments on the first start, sets what the store
sells, event boxes, the raid, guild donations, how often other players turn up as arena
rivals, and the daily crate. Guilds belong to the server, not to a player's save, so
players can share them; a raid is fought by a guild.

## Checking it

```
python -m pytest server -q            # needs game/ beside it
python server/battle_check.py         # replays the recorded battles through the engine
```

## Credits

The battle engine follows the rules of the Tyrant Unleashed Optimizer, rewritten in
Python (MIT license: `battle/LICENSE-tuo.md`). Tyrant Unleashed belongs to Synapse
Games; none of its files are in this repository.
