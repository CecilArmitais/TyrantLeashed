"""Missions from the mission file the game loads (missions_basic.xml): the enemy's
commander and deck, the energy cost and the rewards."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Mission:
    id: str
    name: str
    commander: int
    deck: tuple[int, ...]
    energy: int = 0
    gold: int = 0
    reward_cards: tuple[int, ...] = field(default=())
    star_wins: tuple[tuple[str, int], ...] = field(default=())   # (star id, wins it needs), e.g. ("2", 5)
    location: int = -1                    # <location_id>; 0 is the prologue (Asphodel, missions 1-5)
    mutant: bool = False                  # <is_mutant>: a mutant mission (item drops can need one)


# <req type='5' compare='great_equal' amount='N'/>: won the mission at least N times. It's
# the only star condition in the data (every star 2 and 3 of both mission files).
STAR_REQ_WINS = "5"


def _int(text, default=0) -> int:
    return int(text) if text is not None and text.strip().lstrip("-").isdigit() else default


def _star_wins(rewards: ET.Element | None) -> tuple[tuple[str, int], ...]:
    out = []
    for star in (rewards.findall("star") if rewards is not None else ()):
        sid, req = (star.findtext("id") or "").strip(), star.find("req")
        if sid and req is not None and req.get("type") == STAR_REQ_WINS and req.get("compare") == "great_equal":
            out.append((sid, _int(req.get("amount"))))
    return tuple(out)


def parse_missions(root: ET.Element) -> dict[str, Mission]:
    out = {}
    for m in root.findall("mission"):
        mid = (m.findtext("id") or "").strip()
        if not mid:
            continue
        deck = m.find("deck")
        rewards = m.find("rewards")
        out[mid] = Mission(
            id=mid, name=(m.findtext("name") or "").strip(), commander=_int(m.findtext("commander")),
            deck=tuple(_int(c.text) for c in (deck.findall("card") if deck is not None else ())),
            energy=_int(m.findtext("energy")),
            gold=_int(rewards.findtext("gold")) if rewards is not None else 0,
            reward_cards=tuple(_int(c.text) for c in (rewards.findall("card") if rewards is not None else ())),
            star_wins=_star_wins(rewards), location=_int(m.findtext("location_id"), -1),
            mutant=(m.findtext("is_mutant") or "").strip() == "1")
    return out


def load_missions(asset_dir: Path, file_name: str | None) -> dict[str, Mission]:
    """{} if there's no such file."""
    if not file_name:
        return {}
    f = Path(asset_dir) / file_name
    return parse_missions(ET.parse(f).getroot()) if f.is_file() else {}
