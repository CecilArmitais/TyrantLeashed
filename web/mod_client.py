"""A host's modded client: files that replace the web client's own, such as a data package
with new skill effects in it.

They sit in `game/mods/client/`, laid out as the client is (`Release/<file>`), with a
`built_from.json` that the tool building them writes last:

    {"client": "tyu-web-2.30.80",
     "built_from": {"Release/tyu-web-2_30-80.data": {"size": 36009886, "sha256": "..."},
                    "Release/UnityLoader.js": {"size": 36726, "sha256": "..."}},
     "files": ["Release/tyu-web-2_30-80.data", "Release/UnityLoader.js"]}

The web server serves each file in `files` instead of the client's own. They only work as
a set, and only with the client they were made from: a data package goes with the
UnityLoader.js that lists where each file in it starts, and the rest of the client (its
code) stays the original's. So the server refuses a set with a file missing, and one whose
`built_from` files no longer match the client's own (rebuild it then).

With no `built_from.json`, the client is served as it is. The server reads it once, at
the start: restart it after rebuilding.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

FOLDER = "client"
MANIFEST = "built_from.json"
_REL = re.compile(r"^Release/[A-Za-z0-9_.-]+$")


class ModClientError(ValueError):
    """The modded client can't be used: the message says why."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load(mods_dir: Path, client_dir: Path) -> dict[str, Path]:
    """The client files the mods replace: {"Release/<file>": the file to serve instead}
    (none if there's no `mods_dir/client/built_from.json`)."""
    folder = Path(mods_dir) / FOLDER
    manifest = folder / MANIFEST
    if not manifest.is_file():
        return {}
    try:
        data = json.loads(manifest.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ModClientError(f"{manifest}: not readable JSON ({e})") from None
    files = data.get("files") if isinstance(data, dict) else None
    built_from = data.get("built_from") if isinstance(data, dict) else None
    if (not isinstance(files, list) or not files or not all(isinstance(f, str) and _REL.match(f) for f in files)
            or not isinstance(built_from, dict) or not built_from):
        raise ModClientError(f'{manifest}: expected "files" (["Release/<file>", ...]) and '
                             '"built_from" ({"Release/<file>": {"size", "sha256"}})')
    replaced = {}
    for rel in files:
        path = folder / rel
        if not path.is_file():
            raise ModClientError(f"{manifest}: {path} is missing; the files only work together "
                                 "(rebuild them)")
        replaced[rel] = path
    for rel, mark in built_from.items():
        original = Path(client_dir) / rel
        if not (isinstance(rel, str) and _REL.match(rel) and isinstance(mark, dict)):
            raise ModClientError(f"{manifest}: built_from: {rel!r} isn't a client file with its size and sha256")
        if (not original.is_file() or original.stat().st_size != mark.get("size")
                or _sha256(original) != mark.get("sha256")):
            raise ModClientError(f"{manifest}: built from another {rel} than {original}; rebuild it for this client")
    return replaced
