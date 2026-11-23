"""One battle, a turn at a time.

The rules follow the optimizer's play() in reference/tuo/sim.cpp (with its default
rule switches: enhance_early, poison_after_attacked, counter_without_damage,
subdue_before_attack, corrosive_protect_armor, leech_increase_max_hp,
barrier_each_turn, reduce_delay_summoned_by_tower). A turn for the side whose turn
it is:

  1. begin: last turn's Protect wears off; the commander's cooldowns tick and it
     Enhances; each structure, then assault: Enhance, Absorb, Barrier, Bravery,
     then its delay counts down (reaching 0: Valor, Summon) or its cooldowns tick;
     the hand is topped up to 3 (the "draws")
  2. play a card from the hand: the player's choice, or the oldest card in hand for
     the enemy (in all 17 recorded battles the enemy played its oldest card: 57 of 57);
     then each assault's Inhibit, Sabotage and Disease hit the card across
  3. the commander's skills, then each structure's, then each assault left to right:
     its skills, then its attack (Flurry repeats both)
  4. end: Refresh and poison; Rally, Weaken, Jam... wear off; cards that died are
     removed and the rest slide left

The attacker (the player, in a mission) has the odd turns, the defender the even
ones. After turn 50 the battle is a stall, which the attacker loses.

Random picks (which card a Strike hits...) go through `chooser`, so a recorded
battle can be replayed with the picks the recording made.

Each turn is recorded as the game client expects it (see protocol.py and the
Battles section of docs/protocol.md). The 16 skills seen in recordings are checked
against them exactly; the events for the other skills follow the client's own
names (skills_set.xml and its status flags) but haven't been seen from the real
server.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, replace

from .cards import ASSAULT, COMMANDER, STRUCTURE, Card, SkillSpec
from .effects import ARRIVAL, FILTERS

# Skill groups, as in the optimizer's tyrant.h
HOSTILE = ("enfeeble", "jam", "besiege", "siege", "strike", "sunder", "weaken", "mimic")
HELPFUL = ("heal", "mend", "overload", "protect", "rally", "fortify", "enrage", "entrap", "rush", "evolve")
ACTIVATION = HOSTILE + HELPFUL
X_ACTIVATION = {"enfeeble", "besiege", "siege", "strike", "sunder", "weaken", "mimic"}   # Sabotage lowers these
DEFENSIVE = {"armored", "avenge", "scavenge", "corrosive", "counter", "evade", "subdue", "absorb", "flying",
             "payback", "revenge", "tribute", "refresh", "wall", "barrier"}
INSTANT_DEBUFF = {"inhibit", "sabotage", "disease"}
TRIGGERED = {"allegiance", "flurry", "valor", "stasis", "summon", "bravery", "enhance"}
BUILT = set(ACTIVATION) | DEFENSIVE | INSTANT_DEBUFF | TRIGGERED | {
    "pierce", "swipe", "hunt", "poison", "legion", "coalition", "rupture", "venom", "leech", "drain",
    "berserk", "mark"}

ATTACKER, DEFENDER = 0, 1
COMMANDER_UID = (-1, -2)            # how actions name the attacking / defending commander
FIRST_UID = (1, 101)                # the attacker's cards are 1.., the defender's 101..
HAND_SIZE = 3
TURN_LIMIT = 50
SECTIONS = ("begin", "upkeep", "draws", "plays", "actions", "attack_kill", "defend_kill", "end")
PROGENITOR = 6                      # counts as every faction for faction-limited skills


class BattleError(Exception):
    """A move that isn't allowed (e.g. a card that isn't in the hand)."""


class Unit:
    """A card on the field (or a commander): the optimizer's CardStatus."""

    def __init__(self, uid: int, card: Card, side: int, summoned: bool = False):
        self.uid, self.card, self.side = uid, card, side
        self.summoned = summoned
        self.hp = card.health
        self.delay = card.delay
        self.perm_attack = 0         # Avenge, Berserk, Valor...
        self.perm_health = 0         # Fortify, Avenge, Leech...
        self.temp_attack = 0         # Rally (+), Weaken (-); until the end of the turn
        self.protected = 0           # Protect / Barrier; until the start of its next turn
        self.protected_stasis = 0    # Stasis: until it first acts
        self.absorption = 0          # Absorb: soaks skill damage
        self.poisoned = 0
        self.poisoned_by: "Unit | None" = None
        self.step = "none"           # "attacking" during its action, "attacked" after
        self.enfeebled = 0           # extra damage it takes; until the end of the turn
        self.jammed = False          # skips its next turn
        self.sundered = False        # no attack bonuses; until the end of its turn
        self.overloaded = False      # its skills get past Evade, Inhibit and Protect
        self.subdued = 0             # less permanent attack and max health
        self.corroded_rate = 0       # Corrosive taken: lowers its attack each time it attacks
        self.corroded_weakened = 0
        self.marked = 0              # extra damage from attacks
        self.diseased = 0            # soaks healing
        self.inhibited = 0           # blocks this many helpful skills aimed at it
        self.sabotaged = 0           # lowers its amount-based active skills
        self.enraged = 0             # extra Berserk
        self.entrapped = 0           # extra Counter
        self.evaded = 0              # Evade used this turn
        self.paybacked = 0           # Payback / Revenge used this turn
        self.tributed = 0
        self.rush_attempted = False
        self.skill_cd: dict[str, int] = {}   # skill -> turns of cooldown left
        self.enhanced: dict[str, int] = {}   # Enhance: skill -> extra; until the end of the opponent's turn
        self.evolved: dict[str, str] = {}    # Evolve: skill -> skill it acts as, for the same time

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def max_hp(self) -> int:
        return self.card.health + max(0, self.perm_health - self.subdued)

    @property
    def is_commander(self) -> bool:
        return self.card.type == COMMANDER

    @property
    def is_structure(self) -> bool:
        return self.card.type == STRUCTURE

    @property
    def is_assault(self) -> bool:
        return self.card.type == ASSAULT

    @property
    def can_act(self) -> bool:
        return self.alive and not self.jammed

    @property
    def active(self) -> bool:
        return self.can_act and self.delay == 0

    @property
    def active_next_turn(self) -> bool:
        return self.can_act and self.delay <= 1

    @property
    def protection(self) -> int:
        return self.protected + self.protected_stasis

    def base_skill(self, skill_id: str) -> int:
        """The card's value for a skill (x, else n, else 1 for a skill with no amount,
        as the optimizer's m_skill_value), plus Enrage on Berserk and Entrap on Counter."""
        s = self.card.skill(skill_id)
        value = (s.x or s.n or s.card_id or 1) if s else 0
        if skill_id == "berserk":
            value += self.enraged
        elif skill_id == "counter":
            value += self.entrapped
        return value

    def skill(self, skill_id: str) -> int:
        """The skill's current value: Sabotage lowers amount-based active skills,
        Enhance raises any."""
        value = self.base_skill(skill_id)
        if skill_id in X_ACTIVATION:
            value = max(0, value - self.sabotaged)
        return value + self.enhanced.get(skill_id, 0)

    def has(self, skill_id: str) -> bool:
        """Has the skill at all (some, like Wall, come with no value)."""
        return self.base_skill(skill_id) > 0

    def attack_power(self) -> int:
        """Base + permanent (less Subdue) + temporary, less Corrosion (the optimizer's
        corrosive_protect_armor rules)."""
        return max(0, self.card.attack + max(0, self.perm_attack - self.subdued) + self.temp_attack
                   - self.corroded_weakened)

    def __repr__(self) -> str:
        return f"<{self.uid} {self.card.name} {self.hp}/{self.max_hp}hp d{self.delay}>"


class Side:
    def __init__(self, index: int, commander: Card, deck: list[tuple[int, Card]]):
        self.index = index
        self.commander = Unit(COMMANDER_UID[index], commander, index)
        self.cards = dict(deck)                      # uid -> card, the whole deck
        self.remaining = [uid for uid, _ in deck]    # not played yet, in draw order
        self.shown: set[int] = set()                 # already sent as draws
        self.assaults: list[Unit] = []
        self.structures: list[Unit] = []
        self.played: list[int] = []
        self.next_uid = FIRST_UID[index] + len(deck)  # for summoned cards
        self.stasis_factions: set[int] = set()

    @property
    def hand(self) -> list[int]:
        return self.remaining[:HAND_SIZE]


@dataclass
class Setup:
    """What a battle starts from: each side's commander and its deck in draw order."""
    attacker_commander: Card
    attacker_deck: list[tuple[int, Card]]
    defender_commander: Card
    defender_deck: list[tuple[int, Card]]
    attacker_dominion: Card | None = None       # a structure each side starts with (the optimizer's
    defender_dominion: Card | None = None       # tests use these; missions don't have them)
    defender_fortresses: tuple = ()             # a raid boss's fortress cards (structures), put in
                                                # play at the start of its first turn
    attacker_fortresses: tuple = ()             # the same for the attacker (a guild war's fortresses)
    attacker_wins_stall: bool = False           # a stall (turn 50) goes to the attacker: a guild war
                                                # fight, sent with the enemy as the attacker (surge)


def shuffled_deck(cards: list[Card], first_uid: int, rng: random.Random) -> list[tuple[int, Card]]:
    """The deck shuffled; uids are given in draw order."""
    order = list(cards)
    rng.shuffle(order)
    return [(first_uid + i, card) for i, card in enumerate(order)]


class RandomChooser:
    """Picks targets the way the optimizer does: n random candidates (in slot order)."""

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()

    def __call__(self, battle: "Battle", actor: Unit, skill_id: str, candidates: list[Unit], n: int) -> list[Unit]:
        pool = list(candidates)
        for i in range(n):
            j = self.rng.randint(i, len(pool) - 1)
            pool[i], pool[j] = pool[j], pool[i]
        picked = pool[:n]
        return sorted(picked, key=battle.slot) if n > 1 else picked


class Battle:
    def __init__(self, setup: Setup, chooser=None, cards=None, effects=()):
        """cards: card id -> Card lookup, for Summon (without it, Summon does nothing).
        effects: battleground effects, as (uid, scope, card[, passive]): scope "global",
        "attacker" or "defender", a stand-in commander card holding the effect's skills
        (battle/effects.py), and for a rule effect its (rule, value). Each side's skills
        are cast in its turns (_bge_phase); the rules work where they apply (_passive)."""
        self.sides = (Side(ATTACKER, setup.attacker_commander, setup.attacker_deck),
                      Side(DEFENDER, setup.defender_commander, setup.defender_deck))
        self.choose = chooser or RandomChooser()
        self.rng = getattr(self.choose, "rng", None) or random.Random()
        self.cards = cards
        self.turn = 0
        self.winner: int | None = None              # 1: the attacker won, 0: the defender did
        self.turns: dict[int, dict[str, list]] = {}
        self.ignored_skills: set[str] = set()       # skills on cards in play that aren't built yet
        self.played: list[tuple[int, int]] = []     # (uid, card id), both sides, in play order
        self._killed: tuple[list[int], list[int]] = ([], [])
        self._hits: Counter = Counter()             # Barrier: hits taken during the current action
        self._pending_deaths: list[Unit] = []
        self._shown: set = set()                    # cards whose health this reply has reported (see _own_health)
        self._section_name = "actions"
        self._in_bge = False                        # during a battleground effect's skills
        self._bloodlust = 0                         # Bloodlust: extra attack damage this assault phase
        self._bloodlusted: set = set()              # the assaults that have added to it
        self.bge_casters: dict[int, list[Unit]] = {ATTACKER: [], DEFENDER: []}
        self.passive: dict[int, dict[str, tuple[int, Unit]]] = {ATTACKER: {}, DEFENDER: {}}
        self.arrival: dict[int, list] = {ATTACKER: [], DEFENDER: []}   # (caster, spec): Ballroom Blitz
        for uid, scope, card, *rest in effects:
            passive = rest[0] if rest else None
            for index in ((ATTACKER, DEFENDER) if scope == "global" else
                          (ATTACKER,) if scope == "attacker" else (DEFENDER,)):
                caster = Unit(uid, card, index)
                self.bge_casters[index].append(caster)
                if passive:
                    self.passive[index][passive[0]] = (passive[1], caster)
                self.arrival[index] += [(caster, spec) for spec in card.skills if spec.id in ARRIVAL]
        for side, dominion in zip(self.sides, (setup.attacker_dominion, setup.defender_dominion)):
            if dominion is not None:
                side.structures.append(Unit(FIRST_UID[side.index] - 1, dominion, side.index))
        self.attacker_wins_stall = setup.attacker_wins_stall
        self._fortresses: dict[int, list] = {}
        for side, forts in zip(self.sides, (setup.attacker_fortresses, setup.defender_fortresses)):
            self._fortresses[side.index] = [(side.next_uid + i, card) for i, card in enumerate(forts)]
            side.next_uid += len(forts)
        self._fortress_uids = {uid for forts in self._fortresses.values() for uid, _ in forts}

    # ---- the outside -------------------------------------------------------------

    @property
    def over(self) -> bool:
        return self.winner is not None

    @property
    def active(self) -> Side:
        return self.sides[(self.turn - 1) % 2]

    def start(self) -> dict[int, dict]:
        """Turn 1 begins: the attacker draws. Returns that turn."""
        if self.turn:
            raise BattleError("the battle has already started")
        self._shown = set()
        self._begin_turn()
        return self._since(1)

    def play(self, uid, new_reply: bool = True) -> dict[int, dict]:
        """The attacker plays a card from their hand. Runs everything up to the
        attacker's next choice (or the end) and returns each turn that changed, from
        this one on. A last single card is played for the attacker, and with an empty
        hand the turns just run on until the battle ends. new_reply=False: these events
        go in the same reply as the previous play's (see play_out)."""
        if self.over:
            raise BattleError("the battle is over")
        if not self.turn or self.active.index != ATTACKER:
            raise BattleError("it isn't the attacker's turn")
        if uid is None:                              # only with nothing in the hand: the turn runs on
            if self.active.hand:
                raise BattleError("a card from the hand has to be played")
        else:
            uid = int(uid)
            if uid not in self.active.hand:
                raise BattleError(f"card {uid} isn't in the hand")
        first = self.turn
        if new_reply:
            self._shown = set()                      # a new reply
        self._finish_turn(uid)
        while not self.over:
            if self.turn >= TURN_LIMIT:                  # a stall: the attacker didn't win
                self.winner = 1 if self.attacker_wins_stall else 0
                break
            self._begin_turn()
            side = self.active
            if side.index == ATTACKER and len(side.hand) > 1:
                break                                # the player's move
            # The enemy plays its oldest card. So does the player when only one card is
            # left (no choice to make: the real server plays it without asking), and
            # with none left the turns just run on.
            self._finish_turn(side.hand[0] if side.hand else None)
        return self._since(first)

    def forfeit(self) -> None:
        """The attacker surrenders (the game's Surrender button): the battle is lost."""
        if self.over:
            raise BattleError("the battle is over")
        self.winner = 0

    def play_out(self, uid=None, oldest: bool = False) -> dict[int, dict]:
        """The game's Skip button: the attacker plays `uid` (if it's in the hand; else a
        random card), and every later choice is a random card from the hand, the
        optimizer's auto play, to the end of the battle. All of it is one reply.
        oldest: the attacker plays its oldest card instead, as the defender always does
        (a guild war fight, where the attacker is the AI-played defense deck)."""
        if self.over:
            raise BattleError("the battle is over")
        if not self.turn or self.active.index != ATTACKER:
            raise BattleError("it isn't the attacker's turn")
        first, new_reply = self.turn, True
        hand = self.active.hand
        pick = int(uid) if uid is not None and str(uid).lstrip("-").isdigit() and int(uid) in hand else None
        while not self.over:
            hand = self.active.hand
            if pick is None and hand:
                pick = hand[0] if oldest else self.rng.choice(hand)
            self.play(pick, new_reply=new_reply)
            pick, new_reply = None, False
        return self._since(first)

    def slot(self, unit: Unit) -> int:
        side = self.sides[unit.side]
        group = side.structures if unit.is_structure else side.assaults
        return group.index(unit) if unit in group else -1

    # ---- a turn ------------------------------------------------------------------

    def _begin_turn(self) -> None:
        self.turn += 1
        side = self.active
        self._killed = ([], [])
        self._section_name = "begin"
        for unit in side.structures + side.assaults:      # last turn's Protect wears off
            if unit.protected:
                unit.protected = 0
                self._section("begin").append(self._status(unit, "protect", 0))
        self._begin_action("upkeep")
        self._cool_down(side.commander)
        self._enhance(side.commander)
        for unit in list(side.structures) + list(side.assaults):
            self._enhance(unit)
            if unit.has("absorb"):
                unit.absorption = unit.base_skill("absorb")
            if unit.skill("barrier"):
                self._barrier(unit, unit.skill("barrier"))
            self._bravery(unit)
            if unit.delay > 0:
                unit.delay -= 1
                if unit.delay == 0:
                    self._valor(unit)
                    self._summon_on(unit, "activate")
            else:
                self._cool_down(unit)
        self._end_action()
        drawn = [uid for uid in side.hand if uid not in side.shown]
        side.shown.update(drawn)
        if drawn:
            self._section("draws").extend(drawn)

    def _finish_turn(self, uid: int | None) -> None:
        side = self.active
        if self._fortresses.get(side.index):
            self._place_fortresses(side)
        if uid is not None:
            self._begin_action()
            side.remaining.remove(uid)
            self._place(side, side.cards[uid], uid, played=True)
            self._end_action()
        if self.over:
            return self._end_turn()
        self._begin_action()
        foe = self.sides[1 - side.index]
        for i, unit in enumerate(side.assaults):             # Inhibit, Sabotage, Disease on the card across
            across = foe.assaults[i] if i < len(foe.assaults) else None
            if unit.alive and across is not None and across.alive:
                self._instant_debuffs(unit, across)
        self._end_action()
        self._heroism_protect(side)
        self._bge_phase(side)
        if not self.over:
            self._act(side.commander)
        for unit in list(side.structures):
            if self.over:
                break
            if unit.active:
                self._act(unit)
        self._bloodlust, self._bloodlusted = 0, set()
        i = 0
        while i < len(side.assaults) and not self.over:
            unit = side.assaults[i]
            if unit.active:
                if unit.protected_stasis:                   # Stasis ends when it first acts
                    unit.protected_stasis = 0
                    self._section("begin").append(self._status(unit, "stasis", 0))
                self._act(unit)
            i += 1
        self._end_turn()

    def _bge_phase(self, side: Side) -> None:
        """Battleground effects: each of the side's effect skills, one action each, cast
        by its stand-in commander (the optimizer's BGE phase). Cards that die in it set
        off only their Summon, and cards it brings in get no on-play skills
        (death_from_bge)."""
        self._in_bge = True
        try:
            for caster in self.bge_casters[side.index]:
                for spec in caster.card.skills:
                    if self.over:
                        return
                    self._begin_action()
                    if spec.id == "enhance" and spec.s:
                        self._enhance_spec(caster, spec, from_field=False)
                    elif spec.id in ARRIVAL:
                        pass                                    # given on arrival (_arrive)
                    elif spec.id in ACTIVATION:
                        self._use(caster, spec)
                    else:
                        self.ignored_skills.add(f"{spec.id} (battleground effect)")
                    self._end_action()
        finally:
            self._in_bge = False

    def _place_fortresses(self, side: Side) -> None:
        """A side's fortresses (a raid boss's, a guild's in a war) enter the field, shown
        as plays before its first card: the client places a turn's plays in the side
        whose turn it is, and there's no field for them in the battle data, so they come
        in the side's first turn (the optimizer puts them in before turn 1). Their delay
        starts counting down the side's next turn, as the client shows it (it counts
        delays itself, at upkeep, before the plays)."""
        fortresses, self._fortresses[side.index] = self._fortresses[side.index], []
        for uid, card in fortresses:
            self._begin_action()
            self._place(side, card, uid, fortress=True)
            self._end_action()
            if self.over:
                return

    def _place(self, side: Side, card: Card, uid: int, played: bool = False, summoner: Unit | None = None,
               fortress: bool = False) -> Unit:
        """A card enters the field: played from the hand, summoned, or a raid fortress."""
        unit = Unit(uid, card, side.index, summoned=not (played or fortress))
        if summoner is not None and summoner.is_structure and unit.delay > 0:
            unit.delay -= 1                                  # reduce_delay_summoned_by_tower
        (side.structures if card.type == STRUCTURE else side.assaults).append(unit)
        self.played.append((uid, card.id))
        if played:
            side.played.append(uid)
        if played or fortress:
            self._section("plays").append({"card_uid": self._ref(uid, "play"), "status": []})
        self._note_skills(unit)
        self._arrive(unit)
        if unit.skill("barrier"):
            self._barrier(unit, unit.skill("barrier"))
        if unit.delay == 0:
            self._bravery(unit)
            self._valor(unit)
        if unit.has("absorb"):
            unit.absorption = unit.base_skill("absorb")
        self._cold_sleep(side, unit)
        if not unit.is_structure:
            self._allegiance(side, unit)
            self._stasis(side, unit)
        if played:
            self._devotion(side, unit)
        for spec in card.skills:
            if spec.trigger == "play" and not self._in_bge:
                self._triggered(unit, spec)
        if unit.delay == 0 and summoner is None and card.skill("summon") is None:
            pass
        self._summon_on(unit, "play")
        self._after_damage()
        return unit

    def _act(self, unit: Unit) -> None:
        """One card's (or commander's) action: its activation skills, then (assaults)
        its attack. Flurry gives it more actions in the same turn."""
        actions = 1
        n = 0
        while n < actions and not self.over and unit.alive:
            self._begin_action()
            self._note_skills(unit)
            if unit.is_assault:
                unit.step = "attacking"
            for spec in unit.card.skills:
                if self.over or not unit.alive:
                    break
                sid = unit.evolved.get(spec.id, spec.id)
                if sid in ACTIVATION and spec.trigger == "activate" and not unit.skill_cd.get(spec.id) \
                        and not unit.jammed:
                    if self._use(unit, replace(spec, id=sid)) and spec.c:
                        unit.skill_cd[spec.id] = spec.c             # used: wait c turns
            if not self.over and unit.is_assault:
                if unit.can_act and self._attack(unit):
                    if unit.corroded_rate:                          # Corrosion bites each time it attacks
                        bite = min(unit.corroded_rate, unit.attack_power(),
                                   unit.card.attack + unit.perm_attack - unit.corroded_weakened)
                        unit.corroded_weakened += max(0, bite)
                elif unit.corroded_rate:
                    unit.corroded_rate = unit.corroded_weakened = 0  # and it wears off when it can't attack
            self._end_action()
            n += 1
            flurry = unit.card.skill("flurry")
            if flurry and unit.can_act and not unit.skill_cd.get("flurry") and not self.over:
                actions += unit.skill("flurry")
                unit.skill_cd["flurry"] = flurry.c or 1
                self._action(unit, "flurry", [unit], unit.skill("flurry"), self._own_health(unit))
        if unit.is_assault:
            unit.step = "attacked"

    def _use(self, unit: Unit, spec) -> bool:
        """An active skill at its current value. True if it did something."""
        x = spec.x
        if spec.id in X_ACTIVATION:
            x = max(0, x - unit.sabotaged)
        x += unit.enhanced.get(spec.id, 0)
        if spec.id in X_ACTIVATION and not x:
            return False
        return getattr(self, "_skill_" + spec.id)(unit, replace(spec, x=x))

    def _triggered(self, unit: Unit, spec) -> None:
        """An active skill set off by an event (on play, when attacked, on death)."""
        if spec.id in ACTIVATION:
            self._use(unit, spec)
        elif spec.id != "summon":
            self.ignored_skills.add(f"{spec.id} (on {spec.trigger})")

    def _cool_down(self, unit: Unit) -> None:
        for sid, left in list(unit.skill_cd.items()):
            if left > 0:
                unit.skill_cd[sid] = left - 1

    def _end_turn(self) -> None:
        side = self.active
        self._section_name = "actions"
        if not self.over:
            for unit in side.assaults:                     # Refresh heals, then poison ticks
                refresh = unit.skill("refresh")
                if refresh and unit.alive and unit.hp < unit.max_hp:
                    self._heal_hp(unit, refresh)
                    self._action(unit, "refresh", [unit], refresh, [self._health(unit)] + self._own_health(unit))
                if unit.alive and unit.poisoned > 0:
                    dealt = unit.poisoned
                    self._damage(unit, dealt, counts_for_barrier=False)
                    unit.poisoned -= (dealt + 1) // 2
                    source = unit.poisoned_by or unit
                    self._action(source, "poison", [unit], dealt,
                                 [self._status(unit, "poison", unit.poisoned), self._health(unit)]
                                 + self._own_health(source))
            self._after_damage()
        if self._killed[ATTACKER]:
            self._section("attack_kill").extend(self._killed[ATTACKER])
        if self._killed[DEFENDER]:
            self._section("defend_kill").extend(self._killed[DEFENDER])
        for unit in side.assaults:                          # the active side's effects wear off
            if unit.alive:
                for flag, value, cleared in (("attack_boost", unit.temp_attack, 0), ("jammed", unit.jammed, 0),
                                             ("inhibited", unit.inhibited, 0), ("sabotaged", unit.sabotaged, 0),
                                             ("overloaded", unit.overloaded, 0)):
                    if value:
                        self._section("end").append(self._status(unit, flag, cleared))
            unit.temp_attack = 0
            unit.jammed = unit.sundered = unit.overloaded = False
            unit.enraged = unit.inhibited = unit.sabotaged = unit.tributed = 0
            unit.step = "none"
        for unit in self.sides[1 - side.index].assaults:   # the other side's wear off
            unit.enfeebled = unit.evaded = unit.paybacked = unit.entrapped = 0
            unit.enhanced, unit.evolved = {}, {}
        for unit in self.sides[1 - side.index].structures:
            unit.evaded = 0
        for s in self.sides:                                # the dead go, the rest slide left
            s.assaults = [u for u in s.assaults if u.alive]
            s.structures = [u for u in s.structures if u.alive]

    # ---- attacks -------------------------------------------------------------------

    def _attack(self, att: Unit) -> bool:
        """One attack (the optimizer's attack_phase + PerformAttack). True if it attacked."""
        if att.attack_power() <= 0:
            return False
        foe = self.sides[1 - att.side]
        slot = self.slot(att)
        across = foe.assaults[slot] if slot < len(foe.assaults) else None
        if across is not None and across.alive:
            target = across
        else:
            target = next((s for s in foe.structures if s.alive and s.has("wall")), None) or foe.commander
        if target.has("flying") and self.rng.random() < 0.5:       # flies away: the attack misses
            # shown as a 0-damage attack: the client's skill table has no animation for Flying
            if target.is_commander:
                self._action(att, 0, [target], 0, self._own_health(att))
            else:
                self._action(att, 0, [target], 0, [self._health(target)] + self._own_health(att), block=[])
            return True
        if target.has("subdue"):                                       # before the damage is worked out
            target_subdue = target.skill("subdue")
            att.subdued += target_subdue
            if att.attack_power() < 0:
                att.temp_attack -= att.attack_power()
            att.hp = min(att.hp, att.max_hp)
            self._action(target, "subdue", [att], target_subdue,
                         self._shown_health([att]) + self._own_health(target))
            if att.attack_power() == 0:
                return True
        damage = att.attack_power()
        on_assault = target.is_assault
        brigade = 0
        if not att.sundered:                                            # extra damage
            damage += target.marked
            legion = att.skill("legion")
            if legion:
                side = self.sides[att.side]
                brigade = legion * sum(1 for u in side.assaults if u is not att and u.alive
                                       and u.card.faction == att.card.faction)
                damage += brigade
            damage += self._bloodlust
            coalition = att.skill("coalition")
            if coalition:
                damage += coalition * len({u.card.faction for u in self.sides[att.side].assaults if u.alive})
            damage += att.skill("rupture")
            if att.skill("venom") and target.poisoned > 0:
                damage += att.skill("venom")
            damage += target.enfeebled
        rule = self._passive("brigade")
        if rule and brigade and att.alive and att.hp < att.max_hp:    # Legion's bonus heals it too
            self._heal_hp(att, brigade)
            self._action(rule[1], "heal", [att], brigade, [self._health(att)])
        armor = target.skill("armored") if on_assault else 0
        fort = self._passive("fortification")
        if fort and on_assault:                             # the best Armor among it and its neighbours
            row, i = foe.assaults, self.slot(target)
            armor = max([armor] + [u.skill("armored") for u in row[max(0, i - fort[0]):i + fort[0] + 1]
                                   if u is not target and u.alive])
        protect = target.protection
        cancel = att.skill("pierce") + att.skill("rupture") + target.corroded_rate
        if att.skill("pierce") and armor + protect:
            self._action(att, "pierce", [target], att.skill("pierce"),
                         ([self._health(target)] if target in self._shown else []) + self._own_health(att))
        protect_left = max(0, protect - cancel)
        armor_left = max(0, armor - max(0, cancel - protect))
        dealt = max(0, damage - protect_left - armor_left)
        rule = self._passive("bloodlust")
        if rule and dealt and att not in self._bloodlusted:               # once per assault a turn
            self._bloodlust += rule[0]
            self._bloodlusted.add(att)
        if target.is_commander:                         # the rest of the attack still follows
            target.hp -= dealt
            self._action(att, 0, [target], dealt, self._own_health(att))
            if not target.alive:
                self.winner = 1 if target.side == DEFENDER else 0
                return True
        else:
            block = []
            if target.has("wall") and target.is_structure:
                block.append({"card_uid": self._ref(target.uid, "status"), "skill": "wall", "value": dealt})
            if protect_left:
                block.append({"card_uid": self._ref(target.uid, "status"), "skill": "protect", "value": protect_left})
            if armor_left:
                block.append({"card_uid": self._ref(target.uid, "status"), "skill": "armored", "value": armor_left})
            self._damage(target, dealt)
            self._action(att, 0, [target], dealt, [self._health(target)] + self._own_health(att), block=block)
        venom = att.skill("venom")
        if venom and dealt and on_assault and target.alive:      # Venom poisons what it hits (stacking)
            target.poisoned += venom
            target.poisoned_by = att
            self._action(att, "venom", [target], venom, [self._status(target, "poison", target.poisoned)]
                         + self._own_health(att))
        self._after_damage()
        if self.over:
            return True
        for spec in target.card.skills:                    # the target's "when attacked" skills
            if spec.trigger == "attacked" and not self.over:
                self._triggered(target, spec)
        if self.over:
            return True
        counter = target.skill("counter")                   # Counter, even if the attack was blocked
        if counter and att.alive:
            back = max(0, self._absorb(att, counter + att.enfeebled) - att.protection)
            self._damage(att, back)
            self._action(target, "counter", [att], back, [self._health(att)] + self._own_health(target), block=[])
            self._after_damage()
            flux = self._passive("counterflux")
            if flux and on_assault and target.alive and not self.over:    # heals and berserks for X/4
                value = -(-counter // flux[0])
                self._heal_hp(target, value)
                if not target.sundered:
                    target.perm_attack += value
                self._action(flux[1], "berserk", [target], value,
                             [self._status(target, "perm_max_attack", target.perm_attack), self._health(target)])
        corrosive = target.skill("corrosive")
        if corrosive and att.alive and corrosive > att.corroded_rate:
            att.corroded_rate = corrosive
            # no "corrosive" status: the client rejected the similar table-listed "subdue" and "mark"
            self._action(target, "corrosive", [att], corrosive, self._shown_health([att]) + self._own_health(target))
        leech = min(dealt, att.skill("leech"))
        if leech and att.alive and on_assault:                              # raises max health too (leech_increase_max_hp)
            gain = self._ext_hp(att, leech)
            self._action(att, "leech", [att], leech, [self._status(att, "perm_max_health", att.perm_health),
                                                      self._health(att)] + self._own_health(att))
        swipe, drain = att.skill("swipe"), att.skill("drain")
        if (swipe or drain) and on_assault:
            i, reach = self.slot(target), self._reach()
            hit = [u for j, u in enumerate(foe.assaults) if j != i and abs(j - i) <= reach and u.alive]
            drained = dealt
            for u in hit:
                d = max(0, self._absorb(u, swipe + drain + u.enfeebled) - u.protection)
                self._damage(u, d)
                drained += d
                # One "swipe" action per card hit: the client crashed on a two-target "drain"
                # (tested 2026-11-20), and the real server shows Hunt's hit as "swipe" too.
                self._action(att, "swipe", [u], swipe + drain, [self._health(u)] + self._own_health(att), block=[])
            if drain and att.alive and att.hp < att.max_hp:
                # The heal, with no targets: the client gives each target of a "drain" its
                # Drain_Target animation, which threw NullReferenceExceptions every frame
                # whether the target was the card itself or the cards hit (2026-11-20,
                # 2026-11-22). The health status carries the heal.
                self._heal_hp(att, drained)
                self._action(att, "drain", [], drained, [self._health(att)] + self._own_health(att))
            self._after_damage()
        berserk = att.skill("berserk")
        if berserk and dealt and att.alive and not att.sundered:
            att.perm_attack += berserk
            self._action(att, "berserk", [att], berserk,
                         [self._status(att, "perm_max_attack", att.perm_attack)] + self._own_health(att))
            rule = self._passive("enduring_rage")
            if rule:                                                    # heals and protects for X/2
                value = -(-berserk // rule[0])
                self._heal_hp(att, value)
                att.protected += value
                self._action(rule[1], "protect", [att], value,
                             [self._status(att, "protect", att.protected), self._health(att)])
        poison = target.skill("poison")
        if poison and att.alive:
            att.poisoned += poison
            att.poisoned_by = target
        mark = att.skill("mark")
        if mark and dealt and att.alive and on_assault:
            target.marked += mark
            self._action(att, "mark", [target], mark,          # no "mark" status: the client rejects it
                         self._shown_health([target]) + self._own_health(att))
        devour = self._passive("devour")
        feed = att.skill("leech") + att.skill("refresh")
        if devour and feed and dealt and on_assault and att.alive:   # +X/4 attack and max health
            value = -(-feed // devour[0])
            if not att.sundered:
                att.perm_attack += value
            self._ext_hp(att, value)
            self._action(devour[1], "berserk", [att], value,
                         [self._status(att, "perm_max_attack", att.perm_attack),
                          self._status(att, "perm_max_health", att.perm_health), self._health(att)])
        rule = self._passive("heroism")
        valor = att.skill("valor") + att.skill("bravery")
        if rule and valor and dealt and on_assault and not target.alive and att.alive and not att.sundered:
            att.perm_attack += valor                                    # a kill: +Valor attack
            self._action(rule[1], "berserk", [att], valor, [self._status(att, "perm_max_attack", att.perm_attack)])
        hunt = att.skill("hunt")
        if hunt and not target.is_commander:      # not after hitting the commander (recorded)
            prey = next((u for u in foe.assaults if u.alive), None)
            if prey is not None:
                self._damage(prey, max(0, self._absorb(prey, hunt + prey.enfeebled) - prey.protection))
                # recorded as "swipe": the server shows Hunt with Swipe's animation
                self._action(att, "swipe", [prey], hunt, [self._health(prey)] + self._own_health(att), block=[])
                self._after_damage()
        return True

    def _instant_debuffs(self, unit: Unit, across: Unit) -> None:
        inhibit = unit.skill("inhibit")
        if inhibit > across.inhibited:
            across.inhibited = inhibit
            self._action(unit, "inhibit", [across], inhibit, [self._status(across, "inhibited", inhibit)]
                         + self._shown_health([across]) + self._own_health(unit))
        sabotage = unit.skill("sabotage")
        if sabotage > across.sabotaged:
            across.sabotaged = sabotage
            self._action(unit, "sabotage", [across], sabotage, [self._status(across, "sabotaged", sabotage)]
                         + self._shown_health([across]) + self._own_health(unit))
        disease = unit.skill("disease")
        if disease and unit.is_assault:
            across.diseased += disease
            self._action(unit, "disease", [across], disease, [self._status(across, "disease", across.diseased)]
                         + self._shown_health([across]) + self._own_health(unit))

    # ---- active skills ----------------------------------------------------------------

    def _candidates(self, unit: Unit, spec, hostile: bool, kind: str = "assaults") -> list[Unit]:
        side = self.sides[1 - unit.side if hostile else unit.side]
        units = side.structures if kind == "structures" else side.assaults
        if spec.y and not self._no_factions():
            units = [u for u in units if u.card.faction in (spec.y, PROGENITOR)]
        return units

    def _pick(self, unit: Unit, spec, candidates: list[Unit]) -> list[Unit]:
        if not candidates:
            return []
        n = spec.n or 1
        if spec.all or n >= len(candidates):
            return list(candidates)
        return self.choose(self, unit, spec.id, candidates, n)

    def _from_active_side(self, unit: Unit) -> bool:
        return self.turn and unit.side == self.active.index

    def _will_act(self, u: Unit, source: Unit) -> bool:
        """Weaken / Jam / Sunder targets: cards that will act on their next turn
        (the active side's skills), or still this turn (a skill set off on the other side)."""
        if self._from_active_side(source):
            return u.active_next_turn
        return u.active and u.step == "none"

    # hostile: evaded (Evade) and paid back (Payback / Revenge)

    def _hostile(self, unit: Unit, spec, cands: list[Unit], apply, statuses, damage: bool = False,
                 after_hit=None) -> bool:
        picked = self._pick(unit, spec, cands)
        if not picked:
            return False
        hit, evaded = [], []
        for t in picked:
            if not unit.overloaded and t.evaded < t.skill("evade"):
                t.evaded += 1
                evaded.append(t)
            else:
                hit.append(t)
        block = [] if damage else None
        for t in hit:
            entry = apply(unit, t, spec.x)
            if damage and entry:
                block.append(entry)
        if hit:
            self._action(unit, spec.id, hit, spec.x, statuses(hit) + self._own_health(unit),
                         block=(block or None) if damage else None)
        for t in evaded:
            self._action(t, "evade", [t], 1, [])
        self._after_damage()
        if after_hit is not None and hit:
            after_hit()
        result = bool(hit)                                   # all evaded: no cooldown
        if unit.is_assault:                                  # Payback / Revenge send it back
            for t in hit:
                back = t.skill("payback") + t.skill("revenge")
                if t.paybacked >= back:
                    continue
                victims = [unit]
                if t.skill("revenge"):
                    side, i = self.sides[unit.side], self.slot(unit)
                    victims = [u for u in (side.assaults[i - 1] if i > 0 else None, unit,
                                           side.assaults[i + 1] if i + 1 < len(side.assaults) else None) if u]
                victims = [v for v in victims if v.alive]
                if victims:
                    t.paybacked += 1
                    for v in victims:
                        apply(t, v, spec.x)
                    self._action(t, spec.id, victims, spec.x, statuses(victims) + self._own_health(t),
                                 block=[] if damage else None)
                    self._after_damage()
        return result

    def _skill_damage(self, unit: Unit, spec, cands: list[Unit], halve_on_assaults: bool = False) -> bool:
        def apply(src, t, x):
            amount = (x + 1) // 2 if halve_on_assaults and not t.is_structure else x
            hit = amount + (0 if t.is_structure else t.enfeebled)
            hit = self._absorb(t, hit)
            protect = 0 if src.overloaded else t.protection
            self._damage(t, max(0, hit - protect))
            if protect:
                return {"card_uid": self._ref(t.uid, "status"), "skill": "protect", "value": min(protect, hit)}
            return None
        value = spec.x
        if halve_on_assaults and cands and not cands[0].is_structure:
            value = (spec.x + 1) // 2
        return self._hostile(unit, replace(spec, x=value) if value != spec.x else spec, cands,
                             (lambda src, t, x: apply(src, t, spec.x)),
                             lambda ts: [self._health(t) for t in ts], damage=True)

    def _skill_strike(self, unit: Unit, spec) -> bool:
        return self._skill_damage(unit, spec, [u for u in self._candidates(unit, spec, True) if u.alive])

    def _skill_besiege(self, unit: Unit, spec) -> bool:
        """Mortar: structures if there are any (full value), else assaults (half, rounded up)."""
        structures = [u for u in self._candidates(unit, spec, True, "structures") if u.alive]
        if structures:
            return self._skill_damage(unit, spec, structures)
        return self._skill_damage(unit, spec, [u for u in self._candidates(unit, spec, True) if u.alive],
                                  halve_on_assaults=True)

    def _skill_siege(self, unit: Unit, spec) -> bool:
        return self._skill_damage(unit, spec, [u for u in self._candidates(unit, spec, True, "structures") if u.alive])

    def _skill_enfeeble(self, unit: Unit, spec) -> bool:
        def apply(src, t, x):
            t.enfeebled += x
        return self._hostile(unit, spec, [u for u in self._candidates(unit, spec, True) if u.alive], apply,
                             lambda ts: self._shown_health(ts))

    def _skill_jam(self, unit: Unit, spec) -> bool:
        def apply(src, t, x):
            t.jammed = True
        cands = [u for u in self._candidates(unit, spec, True) if self._will_act(u, unit) and not self._iron_will(u)]
        return self._hostile(unit, spec, cands, apply,
                             lambda ts: [self._status(t, "jammed", 1) for t in ts] + self._shown_health(ts))

    def _skill_weaken(self, unit: Unit, spec) -> bool:
        return self._weaken(unit, spec, sunder=False)

    def _skill_sunder(self, unit: Unit, spec) -> bool:
        return self._weaken(unit, spec, sunder=True)

    def _weaken(self, unit: Unit, spec, sunder: bool) -> bool:
        """Weaken, or Sunder (Weaken that also stops attack bonuses). With Turning Tides,
        the most attack taken from one card Rallies the caster's side."""
        taken = [0]

        def apply(src, t, x):
            before = t.attack_power()
            if sunder:
                t.sundered = True
            t.temp_attack -= min(x, before)
            taken[0] = max(taken[0], before - t.attack_power())
        cands = [u for u in self._candidates(unit, spec, True) if u.attack_power() > 0 and self._will_act(u, unit)
                 and not self._iron_will(u)]
        return self._hostile(unit, spec, cands, apply,
                             lambda ts: [self._status(t, "attack_boost", t.temp_attack) for t in ts]
                             + self._shown_health(ts), after_hit=lambda: self._turning_tides(unit, spec, taken[0]))

    def _skill_mimic(self, unit: Unit, spec) -> bool:
        """Mimic X (the optimizer's): an enemy assault with an activation skill (its own or
        "when attacked"; not Mimic, nor Mend/Fortify for a non-assault) is picked, then one
        of those skills at random is used as the mimic's own, at X (plus the mimic's
        Enhance of it), for any faction. It can't be evaded or paid back."""
        def copyable(t: Unit) -> list:
            return [s for s in t.card.skills if s.trigger in ("activate", "attacked") and s.id in ACTIVATION
                    and s.id != "mimic" and (unit.is_assault or s.id not in ("mend", "fortify"))]
        picked = self._pick(unit, spec, [u for u in self._candidates(unit, spec, True) if u.alive and copyable(u)])
        if not picked:
            return False
        for t in picked:
            if self.over or not unit.alive:
                break
            options = copyable(t)
            chosen = options[self.rng.randrange(len(options))] if len(options) > 1 else options[0]
            self._action(unit, "mimic", [t], spec.x, self._shown_health([t]) + self._own_health(unit))
            copy = SkillSpec(chosen.id, spec.x + unit.enhanced.get(chosen.id, 0), 0, chosen.n, 0, chosen.s,
                             chosen.s2, chosen.all, chosen.card_id)
            getattr(self, "_skill_" + chosen.id)(unit, copy)
        return True

    # helpful: blocked by Inhibit, shared by Tribute

    def _helpful(self, unit: Unit, spec, targets: list[Unit], apply, statuses, check) -> bool:
        """`targets` already picked; `check(t)`: may it apply to t (for Tribute)."""
        done = []
        for t in targets:
            if self._inhibits(unit, t):
                continue
            apply(unit, t, spec.x)
            done.append(t)
            if (unit.is_assault and t is not unit and t.tributed < t.skill("tribute") and unit.alive
                    and check(unit) and spec.id not in ("evolve", "rush")):
                t.tributed += 1
                apply(unit, unit, spec.x)
                done.append(unit)
        if not done:
            return False
        self._action(unit, spec.id, done, spec.x, statuses(done) + self._own_health(unit))
        return True

    def _skill_heal(self, unit: Unit, spec) -> bool:
        ok = lambda u: u.alive and u.hp < u.max_hp                    # noqa: E731
        targets = self._pick(unit, spec, [u for u in self._candidates(unit, spec, False) if ok(u)])
        zeal = self._passive("zealots_preservation") if unit.is_assault else None

        def apply(s, t, x):
            self._heal_hp(t, x)
            if zeal:                                     # an assault's Heal X also Protects X/2
                t.protected += -(-x // zeal[0])
        return self._helpful(unit, spec, targets, apply,
                             lambda ts: [self._health(t) for t in ts]
                             + ([self._status(t, "protect", t.protected) for t in ts] if zeal else []), ok)

    def _skill_rally(self, unit: Unit, spec) -> bool:
        def ok(u):
            if u.sundered:
                return False
            return (u.active and u.step != "attacked") if self._from_active_side(u) else u.active_next_turn
        targets = self._pick(unit, spec, [u for u in self._candidates(unit, spec, False) if ok(u)])

        def apply(s, t, x):
            t.temp_attack += x
        return self._helpful(unit, spec, targets, apply,
                             lambda ts: [self._status(t, "attack_boost", t.temp_attack) for t in ts]
                             + [self._health(t) for t in ts if t in self._shown], ok)

    def _skill_protect(self, unit: Unit, spec) -> bool:
        ok = lambda u: u.alive                                           # noqa: E731
        targets = self._pick(unit, spec, [u for u in self._candidates(unit, spec, False) if ok(u)])

        def apply(s, t, x):
            t.protected += x
        return self._helpful(unit, spec, targets, apply,
                             lambda ts: [self._status(t, "protect", t.protected) for t in ts]
                             + [self._health(t) for t in ts if t in self._shown], ok)

    def _adjacent_allies(self, unit: Unit, spec, ok) -> list[Unit]:
        if not unit.is_assault:
            return []
        side, i, reach = self.sides[unit.side], self.slot(unit), self._reach()
        return [u for j, u in enumerate(side.assaults) if j != i and abs(j - i) <= reach and ok(u)
                and (not spec.y or self._no_factions() or u.card.faction in (spec.y, PROGENITOR))]

    def _skill_fortify(self, unit: Unit, spec) -> bool:
        """Adjacent allies: +x max health (and health), no random pick."""
        ok = lambda u: u.alive                                           # noqa: E731
        return self._helpful(unit, spec, self._adjacent_allies(unit, spec, ok),
                             lambda s, t, x: self._ext_hp(t, x),
                             lambda ts: [self._status(t, "perm_max_health", t.perm_health) for t in ts]
                             + [self._health(t) for t in ts], ok)

    def _skill_mend(self, unit: Unit, spec) -> bool:
        """Adjacent damaged allies: +x health, no random pick."""
        ok = lambda u: u.alive and u.hp < u.max_hp                    # noqa: E731
        return self._helpful(unit, spec, self._adjacent_allies(unit, spec, ok),
                             lambda s, t, x: self._heal_hp(t, x), lambda ts: [self._health(t) for t in ts], ok)

    def _skill_enrage(self, unit: Unit, spec) -> bool:
        def ok(u):
            if not u.attack_power():
                return False
            return (u.active and u.step == "none") if self._from_active_side(u) else u.active_next_turn
        targets = self._pick(unit, spec, [u for u in self._candidates(unit, spec, False) if ok(u)])

        def apply(s, t, x):
            t.enraged += x
        return self._helpful(unit, spec, targets, apply, lambda ts: self._shown_health(ts), ok)

    def _skill_entrap(self, unit: Unit, spec) -> bool:
        ok = lambda u: u.alive                                           # noqa: E731
        targets = self._pick(unit, spec, [u for u in self._candidates(unit, spec, False) if ok(u)])

        def apply(s, t, x):
            t.entrapped += x
        return self._helpful(unit, spec, targets, apply, lambda ts: self._shown_health(ts), ok)

    def _skill_rush(self, unit: Unit, spec) -> bool:
        if unit.rush_attempted and not unit.is_commander:
            return False
        i = self.slot(unit)
        def ok(u):
            need = 2 if unit.is_assault and self.slot(u) < i else 1
            return u.delay >= need
        targets = self._pick(unit, spec, [u for u in self._candidates(unit, spec, False) if ok(u)])
        if not unit.is_commander:
            unit.rush_attempted = True

        def apply(s, t, x):
            t.delay -= min(max(x, 1), t.delay)
            if t.delay == 0:
                self._valor(t)
                self._summon_on(t, "activate")
        return self._helpful(unit, spec, targets, apply, lambda ts: self._shown_health(ts), ok)

    def _skill_overload(self, unit: Unit, spec) -> bool:
        def ok(u):
            if not (u.active and not u.overloaded and u.step != "attacked"):
                return False
            names = [u.evolved.get(s.id, s.id) for s in u.card.skills if not u.skill_cd.get(s.id)]
            if any(n in HOSTILE for n in names):
                return True
            helpful = [n for n in names if n in HELPFUL and n not in ("mend", "fortify")]
            return bool(helpful) and any(a.alive and a.inhibited for a in self.sides[u.side].assaults)
        targets = self._pick(unit, spec, [u for u in self._candidates(unit, spec, False) if ok(u)])

        def apply(s, t, x):
            t.overloaded = True
        return self._helpful(unit, spec, targets, apply,
                             lambda ts: [self._status(t, "overloaded", 1) for t in ts] + self._shown_health(ts), ok)

    def _skill_evolve(self, unit: Unit, spec) -> bool:
        if not spec.s or not spec.s2:
            return False
        def ok(u):
            return (u.alive and u.has(spec.s) and not u.has(spec.s2)
                    and (u.active or spec.s2 in DEFENSIVE))
        targets = self._pick(unit, spec, [u for u in self._candidates(unit, spec, False) if ok(u)])

        def apply(s, t, x):
            t.evolved[spec.s] = spec.s2
        return self._helpful(unit, spec, targets, apply, lambda ts: self._shown_health(ts), ok)

    def _enhance(self, unit: Unit) -> None:
        """Enhance: at the start of its owner's turn, an active card raises one of an
        ally's skills (`s`) by x."""
        if not unit.active:
            return
        for spec in unit.card.skills:
            if spec.id == "enhance" and spec.s:
                self._enhance_spec(unit, spec)

    def _enhance_spec(self, unit: Unit, spec, from_field: bool = True) -> None:
        """One Enhance: an ally's skill `s` up by x until the end of the next turn. A
        battleground effect's (from_field False) works the same way."""
        cands = [u for u in self._candidates(unit, spec, False) if u.alive and u.has(spec.s)
                 and (u.active or spec.s in DEFENSIVE or spec.s in INSTANT_DEBUFF
                      or (spec.s in TRIGGERED and spec.s != "valor"))]
        targets = [t for t in self._pick(unit, spec, cands) if not self._inhibits(unit, t)]
        if not targets:
            return
        for t in targets:
            t.enhanced[spec.s] = t.enhanced.get(spec.s, 0) + spec.x
        self._action(unit, "enhance", targets, spec.x, self._shown_health(targets) + self._own_health(unit))

    def _inhibits(self, source: Unit, target: Unit) -> bool:
        """Inhibit: a helpful skill aimed at an inhibited card is used up instead."""
        if target.inhibited > 0 and not source.overloaded:
            target.inhibited -= 1
            return True
        return False

    # ---- triggered skills ---------------------------------------------------------------

    def _barrier(self, unit: Unit, amount: int) -> None:
        unit.protected += amount
        statuses = [self._status(unit, "protect", unit.protected)]
        if unit in self._shown:
            statuses += [self._health(unit), self._health(unit)]
        self._action(unit, "barrier", [unit], amount, statuses, block=[])

    def _valor(self, unit: Unit) -> None:
        """Valor (when it becomes active): +x attack if the card across is stronger."""
        self._attack_up_if_outmatched(unit, "valor")

    def _bravery(self, unit: Unit) -> None:
        """Bravery (each turn start, and on arrival): the same, again each time."""
        self._attack_up_if_outmatched(unit, "bravery")

    def _attack_up_if_outmatched(self, unit: Unit, skill_id: str) -> None:
        value = unit.skill(skill_id)
        if not value or unit.sundered or not unit.is_assault or not unit.alive:
            return
        foe, i = self.sides[1 - unit.side], self.slot(unit)
        across = foe.assaults[i] if 0 <= i < len(foe.assaults) else None
        if across is None or not across.alive or across.attack_power() <= unit.attack_power():
            return
        unit.perm_attack += value
        self._action(unit, skill_id, [unit], value, [self._status(unit, "perm_max_attack", unit.perm_attack)]
                     + self._own_health(unit))

    def _allegiance(self, side: Side, played: Unit) -> None:
        """A card joins: allies of its faction with Allegiance gain attack and health. With
        Oath of Loyalty (the optimizer's), the card itself, if it has Allegiance X, gains X
        for each other card of its faction on its side: assaults, and structures other
        than the dominion and fortresses. Summoned cards get the Oath, not the allies'."""
        mega = self._passive("megamorphosis", side.index)
        for ally in side.assaults if not played.summoned else ():
            value = ally.skill("allegiance")
            if ally is played or not ally.alive or not value or not (mega or ally.card.faction == played.card.faction):
                continue
            if not ally.sundered:
                ally.perm_attack += value
            self._ext_hp(ally, value)
            self._action(ally, "allegiance", [ally], value,
                         [self._status(ally, "allegiance_attack", ally.perm_attack),
                          self._status(ally, "perm_max_health", ally.perm_health), self._health(ally)]
                         + self._own_health(ally))
        oath = self._passive("oath_of_loyalty", side.index)        # the played card's side
        value = played.skill("allegiance")
        if oath and value and played.alive:
            faction = played.card.faction
            same = sum(1 for u in side.assaults if u is not played and u.alive and (mega or u.card.faction == faction))
            same += sum(1 for u in side.structures if u.alive and (mega or u.card.faction == faction)
                        and u.uid != FIRST_UID[side.index] - 1 and u.uid not in self._fortress_uids)
            if same:                                       # not the dominion or fortresses
                gain = value * same
                played.perm_attack += gain
                self._ext_hp(played, gain)
                self._action(played, "allegiance", [played], gain,
                             [self._status(played, "allegiance_attack", played.perm_attack),
                              self._status(played, "perm_max_health", played.perm_health), self._health(played)]
                             + self._own_health(played))

    def _stasis(self, side: Side, played: Unit) -> None:
        """A delayed assault of a faction with Stasis in play is protected until it acts.
        Temporal Backlash: Counter X counts as Stasis X/2. Megamorphosis: every faction's."""
        faction = played.card.faction
        backlash = self._passive("temporal_backlash", side.index)
        mega = self._passive("megamorphosis", side.index)
        if played.skill("stasis") or (backlash and played.skill("counter")):
            side.stasis_factions.add(faction)
        if played.delay <= 0 or not played.is_assault or not (mega or faction in side.stasis_factions):
            return
        same = lambda u: mega or u.card.faction == faction           # noqa: E731
        total = side.commander.skill("stasis") if same(side.commander) else 0
        total += sum(u.skill("stasis") for u in side.structures + side.assaults if u.alive and same(u))
        if backlash:
            total += sum(-(-u.skill("counter") // backlash[0]) for u in side.assaults if u.alive and same(u))
        played.protected_stasis = total
        if total:
            self._action(played, "stasis", [played], total, [self._status(played, "stasis", total)])
        elif not mega:
            side.stasis_factions.discard(faction)

    def _summon_on(self, unit: Unit, trigger: str) -> None:
        spec = unit.card.skill("summon")
        if spec is None or spec.trigger != trigger:
            return
        card = self.cards.get(spec.card_id) if self.cards is not None else None
        if card is None:
            self.ignored_skills.add("summon (no card data)")
            return
        side = self.sides[unit.side]
        uid = side.next_uid
        side.next_uid += 1
        summoned = self._place(side, card, uid, summoner=unit)
        self._action(unit, "summon", [summoned], card.id, [])

    def _after_damage(self) -> None:
        """Deaths from the last hit: Scavenge, Avenge, Virulence, and the dead card's own
        "on death" skills."""
        spread = {"side": None, "last": None, "stack": 0, "left": None}    # Virulence across a run of deaths
        while self._pending_deaths:
            dead = self._pending_deaths.pop(0)
            if self._in_bge:                                     # death_from_bge: only Summon goes off
                self._revenge(dead)                              # (and Revenge, a rule, as in the optimizer)
                for spec in dead.card.skills:
                    if spec.trigger == "death" and spec.id == "summon" and not self.over:
                        self._summon_on(dead, "death")
                continue
            for s in self.sides:                                 # Scavenge: every death feeds them
                for u in s.assaults + s.structures:
                    value = u.skill("scavenge")
                    if value and u.alive:
                        self._ext_hp(u, value)
                        self._action(u, "scavenge", [u], value,
                                     [self._status(u, "perm_max_health", u.perm_health), self._health(u)]
                                     + self._own_health(u))
            side = self.sides[dead.side]
            if dead in side.assaults:
                i = self.slot(dead)
                for j, ally in enumerate(side.assaults):
                    value = ally.skill("avenge")
                    if j == i or not ally.alive or not value:
                        continue
                    if abs(j - i) > 1:
                        value = (value + 1) // 2
                    if not ally.sundered:
                        ally.perm_attack += value
                    self._ext_hp(ally, value)
                    self._action(ally, "avenge", [ally], value,
                                 [self._status(ally, "avenge_attack", ally.perm_attack),
                                  self._status(ally, "perm_max_health", ally.perm_health),
                                  self._health(ally)] + self._own_health(ally), block=[])
            self._virulence(dead, spread)
            self._revenge(dead)
            for spec in dead.card.skills:
                if spec.trigger == "death" and not self.over:
                    if spec.id == "summon":
                        self._summon_on(dead, "death")
                    else:
                        self._triggered(dead, spec)

    def _virulence(self, dead: Unit, spread: dict) -> None:
        """Virulence (the optimizer's): a dead assault's poison goes to the live assault on
        its left, and the poison of a run of dead neighbours, added up, to the live one on
        the right of each."""
        vir = self._passive("virulence")
        side = self.sides[dead.side]
        if not vir or dead not in side.assaults:
            return
        row, i = side.assaults, self.slot(dead)
        if spread["side"] != dead.side or spread["last"] != i - 1:
            spread.update(side=dead.side, stack=0,
                          left=row[i - 1] if i > 0 and row[i - 1].alive else None)
        if dead.poisoned > 0:
            if spread["left"] is not None:
                self._spread_poison(vir[1], spread["left"], dead.poisoned)
            spread["stack"] += dead.poisoned
        right = row[i + 1] if i + 1 < len(row) and row[i + 1].alive else None
        if right is not None and spread["stack"]:
            self._spread_poison(vir[1], right, spread["stack"])
        spread["last"] = i

    def _spread_poison(self, caster: Unit, unit: Unit, amount: int) -> None:
        unit.poisoned += amount
        self._action(caster, "venom", [unit], amount, [self._status(unit, "poison", unit.poisoned)])

    def _arrive(self, unit: Unit) -> None:
        """Ballroom Blitz: the cards its filter takes gain attack and max health for good
        as they arrive, shown as one action by the effect's stand-in."""
        gains = {}
        for caster, spec in self.arrival[unit.side]:
            if spec.x and FILTERS.get(spec.filter, lambda card: not spec.filter)(unit.card):
                gains[spec.id] = gains.get(spec.id, 0) + spec.x
                actor = caster
        if not gains:
            return
        unit.perm_attack += gains.get("perm_max_attack", 0)
        if gains.get("perm_max_health"):
            self._ext_hp(unit, gains["perm_max_health"])
        self._action(actor, "berserk", [unit], gains.get("perm_max_attack", 0),
                     [self._status(unit, "perm_max_attack", unit.perm_attack),
                      self._status(unit, "perm_max_health", unit.perm_health), self._health(unit)])

    def _caster_for(self, rule: str, side: int) -> Unit | None:
        """The effect's stand-in acting for a side (a global effect has one each)."""
        own = self.passive[side].get(rule)
        if own:
            return own[1]
        other = self.passive[1 - side].get(rule)
        return Unit(other[1].uid, other[1].card, side) if other else None

    def _no_factions(self) -> bool:
        """Metamorphosis / Megamorphosis: skills ignore their faction restrictions."""
        return bool(self._passive("metamorphosis") or self._passive("megamorphosis"))

    def _reach(self) -> int:
        """How far Swipe, Drain, Mend and Fortify reach: 1, more with Critical Reach."""
        rule = self._passive("critical_reach")
        return 1 + (rule[0] if rule else 0)

    def _iron_will(self, unit: Unit) -> bool:
        """Iron Will: a card with Armor can't be Jammed, Weakened or Sundered."""
        return bool(self._passive("iron_will")) and unit.has("armored")

    def _turning_tides(self, unit: Unit, spec, taken: int) -> None:
        """Turning Tides: Weaken / Sunder Rally the caster's side by the most attack taken."""
        if not taken or self.over or not self._passive("turning_tides"):
            return
        self._skill_rally(self._caster_for("turning_tides", unit.side), SkillSpec("rally", taken, all=spec.all))

    def _revenge(self, dead: Unit) -> None:
        """Revenge (Furious, Spiteful, Fervorous): a death Heals and Rallies all its allies."""
        rule = self._passive("revenge")
        if not rule or self.over:
            return
        caster = self._caster_for("revenge", dead.side)
        self._skill_heal(caster, SkillSpec("heal", rule[0], all=True))
        self._skill_rally(caster, SkillSpec("rally", rule[0], all=True))

    def _heroism_protect(self, side: Side) -> None:
        """Heroism: each assault Protects itself for (Valor + Bravery) / 2, rounded up,
        after the instant debuffs, Inhibit permitting."""
        rule = self._passive("heroism")
        if not rule:
            return
        for u in list(side.assaults):
            value = -(-(u.skill("valor") + u.skill("bravery")) // rule[0])
            if self.over or not value or not u.alive:
                continue
            self._begin_action()
            if not self._inhibits(rule[1], u):
                u.protected += value
                self._action(rule[1], "protect", [u], value,
                             [self._status(u, "protect", u.protected)] + self._shown_health([u]))
            self._end_action()

    def _cold_sleep(self, side: Side, played: Unit) -> None:
        """Cold Sleep: a card played; each ally under Stasis heals half its Stasis."""
        rule = self._passive("cold_sleep", side.index)
        if not rule:
            return
        for u in side.assaults:
            if u is not played and u.alive and u.protected_stasis and u.hp < u.max_hp:
                value = -(-u.protected_stasis // rule[0])
                self._heal_hp(u, value)
                self._action(rule[1], "heal", [u], value, [self._health(u)])

    def _devotion(self, side: Side, played: Unit) -> None:
        """Devotion: a card played of its commander's faction gains a quarter of its
        health as max health (rounded up)."""
        rule = self._passive("devotion", side.index)
        if not rule or played.card.faction != side.commander.card.faction or not played.alive:
            return
        value = -(-played.card.health // rule[0])
        self._ext_hp(played, value)
        self._action(rule[1], "heal", [played], value,
                     [self._status(played, "perm_max_health", played.perm_health), self._health(played)])

    def _passive(self, rule: str, side: int | None = None):
        """(value, caster) for a rule effect working for a side (the side whose turn it is,
        as the optimizer's fd->tapi), else None."""
        return self.passive[self.active.index if side is None else side].get(rule)

    # ---- bookkeeping ---------------------------------------------------------------------

    def _begin_action(self, section: str = "actions") -> None:
        self._section_name = section
        self._hits = Counter()

    def _end_action(self) -> None:
        """Barrier: protection for each hit taken during the action."""
        for unit, times in list(self._hits.items()):
            if unit.alive and unit.skill("barrier"):
                self._barrier(unit, unit.skill("barrier") * times)
        self._hits = Counter()
        self._section_name = "actions"

    def _damage(self, unit: Unit, amount: int, counts_for_barrier: bool = True) -> None:
        if amount <= 0:
            return
        was_alive = unit.alive
        unit.hp -= amount
        if counts_for_barrier:
            self._hits[unit] += 1
        if was_alive and not unit.alive:
            if unit.is_commander:
                self.winner = 1 if unit.side == DEFENDER else 0
            else:
                self._killed[unit.side].append(unit.uid)
                self._pending_deaths.append(unit)

    def _absorb(self, unit: Unit, amount: int) -> int:
        """Absorb soaks skill damage first."""
        soaked = min(unit.absorption, amount)
        unit.absorption -= soaked
        return amount - soaked

    def _cure(self, unit: Unit, amount: int) -> int:
        """Disease soaks healing first."""
        soaked = min(unit.diseased, amount)
        unit.diseased -= soaked
        return amount - soaked

    def _heal_hp(self, unit: Unit, amount: int) -> int:
        amount = self._cure(unit, amount)
        before = unit.hp
        unit.hp = min(unit.max_hp, unit.hp + amount)
        return unit.hp - before

    def _ext_hp(self, unit: Unit, amount: int) -> int:
        """More max health, and as much health."""
        amount = self._cure(unit, amount)
        unit.perm_health += amount
        return self._heal_hp(unit, amount) if amount else 0

    def _note_skills(self, unit: Unit) -> None:
        for s in unit.card.skills:
            if s.id not in BUILT:
                self.ignored_skills.add(s.id)

    # ---- describing it -------------------------------------------------------------

    def _section(self, name: str) -> list:
        return self.turns.setdefault(self.turn, {}).setdefault(name, [])

    def _since(self, first: int) -> dict[int, dict]:
        """Turns from `first` on, each with its sections in the order the client gets them."""
        return {t: {name: self.turns[t][name] for name in SECTIONS if name in self.turns[t]}
                for t in range(first, self.turn + 1) if self.turns.get(t)}

    def _action(self, actor: Unit, skill_id, targets: list[Unit], value: int, status: list,
                block: list | None = None) -> None:
        action = {"card_uid": self._ref(actor.uid, "actor"), "skill_id": skill_id,
                  "targets": [self._ref(t.uid, "target") for t in targets], "value": value}
        if block is not None:
            action["block"] = block
        action["status"] = status
        self._section(self._section_name).append(action)

    def _ref(self, uid: int, where: str):
        """A uid written the way the real server mostly writes it: the attacker's cards
        as strings in targets, statuses and plays (they arrive as strings from the
        game), numbers everywhere else; the defender's cards and commanders always
        numbers. (The server isn't fully consistent; the client reads both.)"""
        if 0 < uid < FIRST_UID[DEFENDER] and where in ("target", "status", "play"):
            return str(uid)
        return uid

    def _status(self, unit: Unit, flag: str, value) -> dict:
        return {"card_uid": self._ref(unit.uid, "status"), "status_flag": flag, "value": value}

    def _health(self, unit: Unit) -> dict:
        if not unit.is_commander:
            self._shown.add(unit)
        return self._status(unit, "h", unit.hp)

    def _shown_health(self, units: list[Unit]) -> list:
        """Health of these cards, for those this reply has already shown."""
        return [self._health(u) for u in units if u in self._shown]

    def _own_health(self, actor: Unit) -> list:
        """The real server repeats the acting card's health once this reply has shown it
        (it was hit, healed... earlier in the same request, even if the number didn't
        change). Found from 430 recorded actions; it fits all of them."""
        if actor.is_commander or actor not in self._shown:
            return []
        return [self._health(actor)]
