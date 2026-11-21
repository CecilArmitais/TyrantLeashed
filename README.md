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
  state/                       your save and settings, made on first start
```

## Playing

```
python server/run.py --open           # http://127.0.0.1:8770/
python server/run.py --engine         # missions and the arena as real battles
```

The game logs in as the offline player. Your progress is kept in `state/player.json`;
`python server/player.py` shows it and can change it (gold, war bonds, cards, music
and sound, the tutorial...). `state/content.toml`, written with comments on the first
start, sets what the store sells, event boxes, the raid and the daily crate.

## Checking it

```
python -m pytest server -q            # needs game/ beside it
python server/battle_check.py         # replays the recorded battles through the engine
```

## Credits

The battle engine follows the rules of the Tyrant Unleashed Optimizer, rewritten in
Python (MIT license: `battle/LICENSE-tuo.md`). Tyrant Unleashed belongs to Synapse
Games; none of its files are in this repository.
