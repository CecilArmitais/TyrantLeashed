"""Debug options for a host testing the server, all off unless turned on in debug.toml,
in the server folder (beside run.py). That file is the host's own: .gitignore keeps it
out of the repository, and without it every option is off.

    unlimited_gold = true          # each true or false; a missing one is false
    unlimited_war_bonds = true
    unlimited_energy = true
    unlimited_stamina = true

An option that's on applies to every player on the server. Whatever costs that currency
still happens (a pack, a promo, an event box, a store upgrade, a refill, a refresh, a
guild donation, a mission, an arena fight), but nothing is taken: the save keeps the
same amount, so turning the option off leaves every account as it was. The server's own
"not enough" refusals are skipped too. The game is shown the real amounts, so its own
checks still apply: it won't ask to start a mission the energy it knows of can't pay
for, or to buy what its gold or war bonds can't.

The server re-reads the file when it changes, so an option can be turned on or off
while it runs. Standard library only.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CURRENCIES = ("gold", "war_bonds", "energy", "stamina")


@dataclass(frozen=True)
class DebugOptions:
    gold: bool = False
    war_bonds: bool = False
    energy: bool = False
    stamina: bool = False
    problems: tuple[str, ...] = field(default=(), compare=False)

    def free(self, currency: str) -> bool:
        """Whether spending this currency costs nothing now."""
        return bool(getattr(self, currency))

    def on(self) -> list[str]:
        return [c for c in CURRENCIES if self.free(c)]


def parse(text: str) -> DebugOptions:
    """The file's options; anything unusable is off and noted in `problems`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return DebugOptions(problems=(f"debug.toml isn't valid TOML ({e}); every option is off",))
    values, problems = {}, []
    for currency in CURRENCIES:
        value = data.pop(f"unlimited_{currency}", False)
        if isinstance(value, bool):
            values[currency] = value
        else:
            problems.append(f"debug.toml: unlimited_{currency} needs true or false")
    problems += [f"debug.toml: {key} isn't an option this server has" for key in data]
    return DebugOptions(**values, problems=tuple(problems))


class DebugStore:
    """Reads debug.toml, re-reading it when it changes; no file: every option off."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._options = DebugOptions()
        self._mtime: float | None = None

    def load(self) -> DebugOptions:
        if not self.path.is_file():
            self._options, self._mtime = DebugOptions(), None
            return self._options
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._options, self._mtime = parse(self.path.read_text("utf-8")), mtime
        return self._options
