"""Battleground effects (battleground_effects.xml): skills that a side of a battle gets
from the battlefield. docs/protocol.md, "Battleground effects", has the rules.

- Each effect's <effect> lists skills (activation skills like Strike or Rally, or
  Enhance of a skill). As the optimizer has it, they're cast once per turn by the side's
  commander, after the played card's Inhibit/Sabotage/Disease and before the commander's
  own skills, each as its own action; helpful ones on the side's cards, hostile ones on
  the other side's. A "global" effect works for both sides, an "attacker" or "defender"
  one for that side only (a raid's enemy_battleground_effect is the boss's).
- <kv_mult>raid_level</kv_mult> (Silus' Pact, Octane's Cybernetics, Ballroom Blitz):
  each x is multiplied by the raid's level and rounded up (the optimizer's rule).
- Ballroom Blitz's perm_max_attack and perm_max_health aren't cast: every card of its
  side that its force_filter takes (FILTERS) gets them for good as it arrives.
- Effects without an <effect> are rules written into the real server (Bloodlust,
  Virulence...) or just notes ("This Raid features the new skill Overload"). Those in
  PASSIVE are built (the optimizer's passive BGEs, battle/engine.py); the rest are shown
  but do nothing here yet.
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

# force_filter (the real server's code, not in the data): which cards a skill is for.
# filterBallroomBlitz (raid 25, "Blitzplates are ready to rock"): its Blitz Plates.
FILTERS = {"filterBallroomBlitz": lambda card: "Blitz" in card.name}
ARRIVAL = ("perm_max_attack", "perm_max_health")      # given to cards as they arrive, not cast

# Rule effects the engine plays (the optimizer's passive BGEs), by name -> (rule, the
# value when the file gives none). The values follow the effects' own texts where the
# optimizer's default (1) would disagree: "X/4" for Counterflux; Devour's file value is 4.
PASSIVE = {
    "Counterflux": ("counterflux", 4),        # Counter also heals and berserks itself for X/4
    "Fortification": ("fortification", 1),    # armor: the best among it and the cards 1 away
    "Virulence": ("virulence", 1),            # a poisoned assault's death spreads its poison
    "Devour": ("devour", 4),                  # Leech/Refresh X hitting an assault: +X/4 attack and max health
    "Oath of Loyalty": ("oath_of_loyalty", 1),   # Allegiance on play, once per other card of its faction
    # brawls' and wars' (values from the texts where the file has none)
    "Scent of Blood": ("bloodlust", 2),       # +X attack damage for each assault that dealt some this turn
    "Taste of Blood": ("bloodlust", 3),
    "Bathe in Blood": ("bloodlust", 4),
    "Furious Revenge": ("revenge", 1),        # a death: Heal and Rally all of its allies by X
    "Spiteful Revenge": ("revenge", 2),
    "Fervorous Revenge": ("revenge", 3),
    "Brigade": ("brigade", 1),                # Legion's extra damage also heals the attacker
    "Cold Sleep": ("cold_sleep", 2),          # a card played: allies under Stasis heal Stasis/X
    "Critical Reach": ("critical_reach", 1),  # Swipe, Drain, Mend, Fortify reach X more cards each way
    "Devotion": ("devotion", 4),              # a card of its commander's faction played: +health/X max health
    "Enduring Rage": ("enduring_rage", 2),    # Berserk X also heals and protects for X/2
    "Heroism": ("heroism", 2),                # Protect (Valor+Bravery)/X each turn; kills give attack
    "Iron Will": ("iron_will", 4),            # cards with Armor can't be Jammed, Weakened or Sundered
    "Megamorphosis": ("megamorphosis", 1),    # no faction restrictions, also for Allegiance and Stasis
    "Metamorphosis": ("metamorphosis", 1),    # skills lose their faction restrictions
    "Temporal Backlash": ("temporal_backlash", 2),   # Counter X adds X/2 to its faction's Stasis
    "Turning Tides": ("turning_tides", 1),    # Weaken/Sunder: Rally an ally by the attack taken away
    "Zealot's Preservation": ("zealots_preservation", 2),   # an assault's Heal X also Protects X/2
    # Blood Vengeance ("Avenge triggers at half strength whenever any non-adjacent allied
    # assault dies") needs nothing either: that's how Avenge works here already, as in
    # the optimizer.
    # Halted Orders ("Inactive Assaults with Inhibit will Inhibit the Assault across from
    # them") needs nothing: every live assault inhibits the card across already, active or
    # not, as in the optimizer, whose own Halted Orders rule is commented out.
}


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
    filter: str = ""                          # force_filter: which cards (Ballroom Blitz's)


@dataclass(frozen=True)
class Effect:
    id: int
    name: str
    desc: str
    picture: str
    skills: tuple[RawSkill, ...] = ()
    kv_mult: str | None = None
    value: int = 0                            # <value>: a rule effect's number (Devour: 4)

    @property
    def passive(self) -> tuple[str, int] | None:
        """(rule, value) for a rule effect the engine plays, else None."""
        rule = PASSIVE.get(self.name)
        return (rule[0], self.value or rule[1]) if rule else None

    def specs(self, level: int = 1) -> tuple[SkillSpec, ...]:
        """The skills at a level: x times the level, rounded up, for kv_mult effects."""
        mult = max(1, level) if self.kv_mult == "raid_level" else 1
        return tuple(SkillSpec(r.id, math.ceil(r.x * mult - 1e-9) if r.x else 0, r.y, r.n, r.c, r.s, r.s2, r.all,
                               filter=r.filter)
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
                                s.get("s") or "", s.get("s2") or "", s.get("all") == "1", s.get("force_filter") or "")
                       for s in b.findall("effect/skill") if s.get("id"))
        out[bid] = Effect(bid, (b.findtext("name") or "").strip(), (b.findtext("desc") or "").strip(),
                          (b.findtext("web_picture") or "").strip(), skills,
                          (b.findtext("kv_mult") or "").strip() or None,
                          num((b.findtext("value") or "").strip()))
    return out
