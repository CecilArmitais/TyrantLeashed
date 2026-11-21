"""Battleground effects (battleground_effects.xml): skills that a side of a battle gets
from the battlefield. docs/battle-engine.md, "Battleground effects", has the rules.

- Each effect's <effect> lists skills (activation skills like Strike or Rally, or
  Enhance of a skill). As the optimizer has it, they're cast once per turn by the side's
  commander, after the played card's Inhibit/Sabotage/Disease and before the commander's
  own skills, each as its own action; helpful ones on the side's cards, hostile ones on
  the other side's. A "global" effect works for both sides, an "attacker" or "defender"
  one for that side only (a raid's enemy_battleground_effect is the boss's).
- <kv_mult>raid_level</kv_mult> (Silus' Pact, Octane's Cybernetics, Ballroom Blitz):
  each x is multiplied by the raid's level and rounded up (the optimizer's rule).
- Effects without an <effect> are rules written into the real server (Bloodlust,
  Virulence...) or just notes ("This Raid features the new skill Overload"); they're
  shown but do nothing here yet.
- The client: battle_data.battleground_effects {"global", "attacker", "defender"}, each
  a list whose first entry's name, desc and web_picture (an icon) are shown, and whose
  card_uid is the actor of that effect's actions (0 or -3 and below; the icon glows when
  it acts). It never looks up an effect by id.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from battle.cards import COMMANDER, Card, SkillSpec

SCOPE_UID = {"global": -3, "attacker": -4, "defender": -5}       # the actors' uids (ours; 0 or <= -3)


@dataclass(frozen=True)
class RawSkill:
    id: str
    x: float = 0.0
    y: int = 0
    n: int = 0
    c: int = 0
    s: str = ""
    s2: str = ""
    all: bool = False


@dataclass(frozen=True)
class Effect:
    id: int
    name: str
    desc: str
    picture: str
    skills: tuple[RawSkill, ...] = ()
    kv_mult: str | None = None

    def specs(self, level: int = 1) -> tuple[SkillSpec, ...]:
        """The skills at a level: x times the level, rounded up, for kv_mult effects."""
        mult = max(1, level) if self.kv_mult == "raid_level" else 1
        return tuple(SkillSpec(r.id, math.ceil(r.x * mult - 1e-9) if r.x else 0, r.y, r.n, r.c, r.s, r.s2, r.all)
                     for r in self.skills)

    def entry(self, scope: str) -> dict:
        """A battleground_effects entry, as the client reads it."""
        return {"id": self.id, "name": self.name, "desc": self.desc, "web_picture": self.picture,
                "card_uid": SCOPE_UID[scope]}

    def caster(self, level: int = 1) -> Card:
        """A stand-in commander that holds the effect's skills."""
        return Card(id=0, base_id=0, name=self.name, type=COMMANDER, faction=0, rarity=0, set=0, level=1,
                    fusion_level=0, attack=0, health=1, delay=0, skills=self.specs(level))


def load(asset_dir: Path) -> dict[int, Effect]:
    f = Path(asset_dir) / "battleground_effects.xml"
    if not f.is_file():
        return {}
    out = {}
    num = lambda v, kind=int: kind(v) if v not in (None, "") else 0
    for b in ET.parse(f).getroot().findall("battleground"):
        try:
            bid = int((b.findtext("id") or "").strip())
        except ValueError:
            continue
        skills = tuple(RawSkill(s.get("id"), num(s.get("x"), float), num(s.get("y")), num(s.get("n")), num(s.get("c")),
                                s.get("s") or "", s.get("s2") or "", s.get("all") == "1")
                       for s in b.findall("effect/skill") if s.get("id"))
        out[bid] = Effect(bid, (b.findtext("name") or "").strip(), (b.findtext("desc") or "").strip(),
                          (b.findtext("web_picture") or "").strip(), skills,
                          (b.findtext("kv_mult") or "").strip() or None)
    return out
