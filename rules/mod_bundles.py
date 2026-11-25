"""Mod bundles: asset bundles a host adds to the game, such as icons for skills the client
has no picture for.

`game/mods/bundles.json` lists them, and each file sits in `game/mods/bundles/`:

    {"bundles": [{"file": "wmticons1_Unity5_4_2_WebGL.unity3d",
                  "sprites": ["shock16", "shock32"]}]}

The login's `asset_bundles` gets an entry for each, numbered after the recorded ones and
named `mods/<file>`, so the game asks for it at `.../asset_bundles/mods/<file>` (the web
server answers from `game/mods/bundles/`). Its version comes from the file's contents:
the game keeps downloaded bundles by address and version, so a changed file is fetched
again. And a `bundle_index` names each sprite's bundle: the game looks a sprite name up
there before its own pictures, and loads the bundle the first time one is needed. A
skill's icon is its id plus `16` (small cards) or `32` (battle); the sprites must be in a
2D Toolkit sprite collection in the bundle, built for Unity 5.4.2 (WebGL).

The server reads the list once, at the start: restart it after changing mods.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

PREFIX = "mods/"                              # in bundle names: asset_bundles/mods/<file>
MANIFEST = "bundles.json"
FOLDER = "bundles"
_FILE = re.compile(r"^[A-Za-z0-9_.-]+\.unity3d$")
_SPRITE = re.compile(r"^[a-z0-9_]+$")


class ModBundleError(ValueError):
    """The mods' bundle list can't be used: the message says why."""


@dataclass(frozen=True)
class ModBundle:
    file: str
    version: int
    sprites: tuple[str, ...]


def load(mods_dir: Path) -> list[ModBundle]:
    """The bundles `mods_dir/bundles.json` lists (none if there's no such file)."""
    manifest = Path(mods_dir) / MANIFEST
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ModBundleError(f"{manifest}: not readable JSON ({e})") from None
    entries = data.get("bundles") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ModBundleError(f'{manifest}: expected {{"bundles": [...]}}')
    bundles, files, sprites_seen = [], set(), {}
    for i, entry in enumerate(entries, 1):
        where = f"{manifest}, bundle {i}"
        if not isinstance(entry, dict):
            raise ModBundleError(f"{where}: expected an object")
        name = entry.get("file")
        if not isinstance(name, str) or not _FILE.match(name) or ".." in name:
            raise ModBundleError(f'{where}: "file" must be a plain file name ending in .unity3d')
        if name in files:
            raise ModBundleError(f"{where}: {name} is listed twice")
        path = Path(mods_dir) / FOLDER / name
        if not path.is_file():
            raise ModBundleError(f"{where}: {path} doesn't exist")
        sprites = entry.get("sprites")
        if (not isinstance(sprites, list) or not sprites
                or not all(isinstance(s, str) and _SPRITE.match(s) for s in sprites)):
            raise ModBundleError(f'{where}: "sprites" must list the sprite names, in lower case '
                                 "(the game looks them up lower-cased)")
        for s in sprites:
            if s in sprites_seen:
                raise ModBundleError(f"{where}: sprite {s} is also in {sprites_seen[s]}")
            sprites_seen[s] = name
        files.add(name)
        version = int(hashlib.sha256(path.read_bytes()).hexdigest()[:7], 16) or 1
        bundles.append(ModBundle(name, version, tuple(sprites)))
    return bundles


def apply_to_login(body: dict, bundles: list[ModBundle]) -> None:
    """Add the bundles to a login reply's `asset_bundles`, and their sprites to its
    `bundle_index` (`{"": [{"name", "bundle"}, ...]}`, the shape the game reads)."""
    listed = body.get("asset_bundles")
    if not bundles or not isinstance(listed, (dict, list)):
        return
    rows = list(listed.values()) if isinstance(listed, dict) else listed
    next_id = max((int(r.get("id", 0)) for r in rows if isinstance(r, dict)), default=0) + 1
    index = body.setdefault("bundle_index", {}).setdefault("", [])
    for bundle in bundles:
        row = {"id": next_id, "version": str(bundle.version), "preload": 0, "bundle_name": PREFIX + bundle.file}
        if isinstance(listed, dict):
            listed[str(next_id)] = row
        else:
            listed.append(row)
        index.extend({"name": s, "bundle": next_id} for s in bundle.sprites)
        next_id += 1


def file_path(mods_dir: Path, name: str) -> Path | None:
    """The file to send for `asset_bundles/mods/<name>`, if it's a plain bundle file name."""
    if not _FILE.match(name) or ".." in name:
        return None
    return Path(mods_dir) / FOLDER / name
