"""Where an archived game file lives: game/assets/<host>/<url path>, made safe for
Windows file names. The same layout as the capture tools' (capture/tu_mirror.py in the
workspace), which write the archive; the server only reads it. Standard library only."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

_UNSAFE_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_segment(seg: str) -> str:
    seg = _UNSAFE_CHARS.sub("_", seg).rstrip(" .")[:120] or "_"
    if seg.split(".", 1)[0].upper() in _RESERVED:
        seg = "_" + seg
    return seg


def safe_rel_path(host: str, path: str) -> Path:
    """Map host + URL path to a relative filesystem path that is safe on Windows."""
    parts = [safe_segment(host)]
    for seg in unquote(path).replace("\\", "/").split("/"):
        if seg in ("", ".", ".."):
            continue
        parts.append(safe_segment(seg))
    if len(parts) == 1 or path.endswith("/"):
        parts.append("index.html")
    return Path(*parts)
