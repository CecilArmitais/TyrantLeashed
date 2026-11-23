"""The server's JSON state files (state/: accounts, saves, guilds, events, settings).

A file is always written whole: to <name>.part beside it, then swapped in (os.replace),
so a crash or a full disk mid-write leaves the old file, never half of one. Text is
UTF-8 with non-ASCII characters as they are (names stay readable when editing a file
by hand), indented one space.

Standard library only."""

from __future__ import annotations

import json
import os
from pathlib import Path


def read(path: Path, default=None):
    """The file's data, or `default` if there's no file. A file that isn't JSON raises
    ValueError: state isn't thrown away silently."""
    path = Path(path)
    if not path.is_file():
        return default
    return json.loads(path.read_text("utf-8"))


def write(path: Path, data, *, sort_keys: bool = False) -> None:
    """Writes `data` to `path` (making its folder), replacing the file in one step."""
    path = Path(path)
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=sort_keys)   # fails before any file is touched
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(text, "utf-8")
    os.replace(part, path)
