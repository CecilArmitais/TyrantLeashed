"""Battles from the engine: the campaign files, decks, missions and their pay, surrender, practice fights.
Part of GameApi (core.py), as BattleCalls."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import achievements
from battle import effects as bge
from battle.cards import CardDB
from battle.engine import (ATTACKER, Battle, BattleError, DEFENDER, FIRST_UID, RandomChooser, Setup,
                           shuffled_deck)
from battle.missions import load_missions
from battle.protocol import BattleSession
from player_state import deck_from_json
from rewards import MissionResult, finish_mission

from .base import ARENA_FIELDS, BASIC_LAST, BASIC_MISSIONS, FULL_MISSIONS, NotSupported, mission_costs
from .replay import battle_won


class BattleCalls:
    """GameApi's part for battles from the engine: the campaign files, decks, missions and
    their pay, surrender, practice fights."""

    def _mission_file(self) -> str | None:
        """The mission file the player's game loads (`mission_file` in the login): the
        recorded new account's, missions_basic.xml (the new-player campaign: locations 0-4,
        missions 1-56), until its last mission is won; then missions.xml, the full one,
        whose mission 57 carries the story on at location 5 and which has the side and
        mutant missions. Recorded: a new account had the basic file, a returning one (8
        missions in) the full one; when a new account moved over isn't recorded (ours)."""
        recorded = self._login_value("mission_file")
        player = self.player_store.load() if self.player_store is not None else None   # as saved: no refill
        if recorded == BASIC_MISSIONS and player is not None \
                and ((player.missions or {}).get(BASIC_LAST) or {}).get("complete") \
                and (Path(self._assets) / FULL_MISSIONS).is_file():
            return FULL_MISSIONS
        return recorded

    def _mission_costs(self) -> dict[str, int]:
        name = self._mission_file()
        if name not in self._costs_by_file:
            self._costs_by_file[name] = mission_costs(self._assets, name)
        return self._costs_by_file[name]

    def _mission_data(self) -> dict:
        """The missions (enemy decks, energy, rewards) of the player's mission file, loaded
        on first use."""
        name = self._mission_file()
        if name not in self._missions_by_file:
            self._missions_by_file[name] = load_missions(self._assets, name)
        return self._missions_by_file[name]

    def _engine_data(self) -> tuple[CardDB, dict]:
        """All cards and missions, loaded on first use (about a second)."""
        if self._cards is None:
            self._cards = CardDB.load(self._assets)
        return self._cards, self._mission_data()

    def _active_deck_id(self) -> str:
        """The active deck: the save's (setActiveDeck), else user_data.active_deck from the
        starting data."""
        saved = self.player_store.load() if self.player_store is not None else None
        if saved is not None and saved.active_deck and (saved.decks or {}).get(saved.active_deck):
            return saved.active_deck
        decks = self._start.get("user_decks") or {}
        return str((self._start.get("user_data") or {}).get("active_deck") or next(iter(decks), ""))

    def _player_deck(self, cards: CardDB):
        """The player's active deck from the save: (commander, cards)."""
        player = self.player()
        decks = player.decks if player is not None else {
            str(k): deck_from_json(v) for k, v in (self._start.get("user_decks") or {}).items()}
        deck = (decks or {}).get(self._active_deck_id())
        if deck is None:
            raise NotSupported("there is no active deck.")
        return self._deck_cards(deck, cards, "your deck")

    @staticmethod
    def _deck_cards(deck: dict, cards: CardDB, whose: str):
        """A saved deck ({"commander_id", "cards": {id: count}}) as (commander, cards)."""
        if not deck or not deck.get("commander_id"):
            raise NotSupported(f"{whose} has no commander.")
        ids = [cid for cid, n in (deck.get("cards") or {}).items() for _ in range(int(n))]
        missing = [i for i in [deck["commander_id"], *ids] if cards.get(i) is None]
        if missing:
            raise NotSupported(f"cards {', '.join(map(str, missing))} of {whose} aren't in the card data.")
        return cards[deck["commander_id"]], [cards[i] for i in ids]

    def _start_engine_battle(self, params: dict) -> tuple[dict, str]:
        mid = str(params.get("mission_id"))
        cards, missions = self._engine_data()
        mission = missions.get(mid)
        if mission is None:
            raise NotSupported(f"mission {mid} isn't in the mission data.")
        missing = [i for i in [mission.commander, *mission.deck] if cards.get(i) is None]
        if missing:
            raise NotSupported(f"mission {mid}'s cards {', '.join(map(str, missing))} aren't in the card data.")
        commander, deck = self._player_deck(cards)
        refusal, charged = self._charge_mission(params)
        if refusal is not None:
            return refusal
        rng = self.rng_factory()
        battle = Battle(Setup(commander, shuffled_deck(deck, FIRST_UID[ATTACKER], rng), cards[mission.commander],
                              shuffled_deck([cards[i] for i in mission.deck], FIRST_UID[DEFENDER], rng)),
                        cards=cards)                                   # for Summon
        battle_id = int(self.clock() * 1000)
        session = BattleSession(battle, battle_id, str(self.uid), mid)
        turns = battle.start()
        self.battle = {"id": str(battle_id), "name": f"mission {mid}", "mission_id": mid, "engine": session}
        reply = self._ok(params)
        reply["user_data"] = self.current_user_data(params)
        reply["battle_data"] = session.start_data(turns)
        return reply, f"engine: started mission {mid} ({len(deck)} cards against {len(mission.deck)})" + charged

    def _play_engine_card(self, params: dict) -> tuple[dict, str]:
        b = self.battle
        session: BattleSession = b["engine"]
        battle = session.battle
        skip = str(params.get("skip", "0")) not in ("0", "")
        try:
            # skip=1 is the game's Skip button ("Skip animations tapped"): the rest of
            # the battle is played out on auto and sent in one reply.
            turns = battle.play_out(params.get("card_uid")) if skip else battle.play(params.get("card_uid"))
        except (BattleError, ValueError, TypeError) as e:
            raise NotSupported(f"the battle engine can't do that: {e}.") from None
        how = f"engine: {b['name']}, turns {min(turns)}-{max(turns)}" if turns else f"engine: {b['name']}"
        if skip:
            how += " (skipped to the end)"
        rewards, result, won = None, None, False
        if battle.over:
            won = battle.winner == 1
            how += " (you won)" if won else " (you lost)"
            if b.get("rival"):
                result = self._finish_hunt(b["rival"], won, b["id"], battle, revenge=bool(b.get("revenge")))
                rewards = result.rewards
            elif b.get("practice"):
                rewards = [{"user_id": str(self.uid), "gold": 0, "sp": 0}]
            else:
                result = self._finish_mission(b["mission_id"], won)
                rewards = [result.reward if result else {"user_id": str(self.uid), "gold": 0}]
            if result:
                how += "; paid " + result.summary()
            self.battle = None
        if battle.ignored_skills:
            how += "; skills not built yet: " + ", ".join(sorted(battle.ignored_skills))
        reply = self._ok(params)
        reply["battle_data"] = session.turn_data(turns, rewards, end_time=int(self.clock()))
        if battle.over:                      # the last reply carries the player's data, as recorded
            reply.update(self._arena_parts(params) if b.get("rival")
                         else self._player_parts(params, ("user_data",)) if b.get("practice")
                         else self._final_parts(result, won, params))
        return reply, how

    def _forfeit(self, params: dict) -> tuple[dict, str]:
        """forfeitBattle battle_id: the Surrender button. The battle counts as lost (a
        mission pays its loss gold), and the reply is shaped like the last playCard's:
        battle_data with winner 0 and the rewards, plus the player's data."""
        b = self.battle
        if b is None:
            raise NotSupported("there is no battle in progress.")
        if params.get("battle_id") and str(params["battle_id"]) != b["id"] and not b.get("practice"):
            raise NotSupported("that isn't the battle in progress.")
        self.battle = None
        if b.get("rival"):
            result = self._finish_hunt(b["rival"], False, b["id"], b["engine"].battle if "engine" in b else None,
                                       revenge=bool(b.get("revenge")))
            rewards = result.rewards
        elif b.get("practice"):
            result, rewards = None, [{"user_id": str(self.uid), "gold": 0, "sp": 0}]
        else:
            result = self._finish_mission(b["mission_id"], False) if b.get("mission_id") else None
            rewards = [result.reward if result else {"user_id": str(self.uid), "gold": 0}]
        reply = self._ok(params)
        if "engine" in b:
            session: BattleSession = b["engine"]
            session.battle.forfeit()
            reply["battle_data"] = session.turn_data({}, rewards, end_time=int(self.clock()))
        else:
            reply["battle_data"] = {"battle_id": b["id"], "host_id": str(self.uid), "turn": {},
                                    "end_time": int(self.clock()), "winner": 0, "rewards": rewards,
                                    **({"mission_id": b["mission_id"]} if b.get("mission_id") else {})}
        reply.update(self._arena_parts(params) if b.get("rival")
                     else self._player_parts(params, ("user_data",)) if b.get("practice")
                     else self._final_parts(result, False, params))
        how = f"surrendered {b['name']}"
        return reply, how + (f"; paid {result.summary()}" if result else "")

    def _practice_fight(self, params: dict) -> tuple[dict, str]:
        """fightPracticeBattle target_user_id fight_attack_deck is_surge: a fight against a
        guild member's deck (their attack deck with fight_attack_deck 1, the deck tab the
        profile shows; else their defense deck), played by the AI, for nothing: no energy,
        no rewards, no rating. Battle (is_surge 0): the player moves first, card by card,
        as in the arena. Surge: the member's deck moves first, the whole fight in one
        reply, as in guild wars. The rules are ours (nothing was recorded)."""
        target = str(params.get("target_user_id") or "")
        guild = self._my_guild()
        other = self._player_of(target) if target else None
        words = ("You must be in a guild to practice." if guild is None
                 else "You can't practice against yourself." if target == str(self.uid)
                 else "You can only practice against a member of your guild."
                 if other is None or target not in [str(m) for m in guild["members"]] else None)
        if words:
            return self._refuse(params, words), f"refused practice fight: {words}"
        attack_deck = str(params.get("fight_attack_deck", "0")) not in ("0", "", "false", "False")
        surge = str(params.get("is_surge", "0")) not in ("0", "", "false", "False")
        name = self._name_of(target)
        whose = f"{name}'s {'attack' if attack_deck else 'defense'} deck"
        cards, _ = self._engine_data()
        commander, deck = self._player_deck(cards)
        foe_commander, foe_deck = self._deck_cards(self._deck_of(other) if attack_deck else self._defense_deck_of(other),
                                                   cards, whose)
        rng = self.rng_factory()
        battle_id = int(self.clock() * 1000)
        reply = self._ok(params)
        if surge:
            battle = Battle(Setup(foe_commander, shuffled_deck(foe_deck, FIRST_UID[ATTACKER], rng), commander,
                                  shuffled_deck(deck, FIRST_UID[DEFENDER], rng), attacker_wins_stall=True),
                            chooser=RandomChooser(rng), cards=cards)
            battle.start()
            battle.play_out(oldest=True)
            won = battle.winner == 0                   # the player is the battle's defender
            session = BattleSession(battle, battle_id, str(self.uid), host_is_attacker=False,
                                    extra={"enemy_name": name, "enemy_id": target})
            reply.update(self._player_parts(params, ("user_data",)))
            reply["battle_data"] = session.turn_data(battle.turns, [{"user_id": str(self.uid), "gold": 0, "sp": 0}],
                                                     end_time=int(self.clock()))
            how = f"practice surge vs {whose}: {'won' if won else 'lost'} in {battle.turn} turns"
        else:
            battle = Battle(Setup(commander, shuffled_deck(deck, FIRST_UID[ATTACKER], rng), foe_commander,
                                  shuffled_deck(foe_deck, FIRST_UID[DEFENDER], rng)),
                            chooser=RandomChooser(rng), cards=cards)
            session = BattleSession(battle, battle_id, str(self.uid),
                                    extra={**ARENA_FIELDS, "enemy_id": target, "enemy_name": name})
            turns = battle.start()
            self.battle = {"id": str(battle_id), "name": f"practice vs {name}", "mission_id": None,
                           "engine": session, "practice": True}
            reply["user_data"] = self.current_user_data(params)
            reply["battle_data"] = session.start_data(turns)
            how = f"engine: started a practice fight with {whose}: {len(deck)} cards against {len(foe_deck)}"
        if battle.ignored_skills:
            how += "; skills not built yet: " + ", ".join(sorted(battle.ignored_skills))
        return reply, how

    def _finish_mission(self, mission_id, won: bool) -> MissionResult | None:
        """Pays a finished mission into the save (rewards.py). None without a save or
        without the mission in the mission data."""
        player = self.player()
        mission = self._mission_data().get(str(mission_id))
        if player is None:
            return None
        if mission is None:                 # only a replay can get here: the win counts, nothing to pay
            if won:
                player.won_mission(str(mission_id), ["1"])
                self._track(player)
                self.player_store.save(player)
            return None
        rules = dataclasses.replace(self.reward_rules, gold_multiplier=self.settings().mission_gold.effective)
        result = finish_mission(player, mission, won, rules, int(self.clock()), self.uid,
                                self._active_deck_id())
        gains = self._event_drops(player, achievements.MISSION, won, mutant=mission.mutant)
        if gains:
            result.reward["item"] = achievements.reward_item(gains)
        self._track(player)                 # "Complete Mission #5"...
        self.player_store.save(player)
        return result

    def _final_parts(self, result: MissionResult | None, won: bool, params: dict) -> dict:
        """The player's data in a mission's last reply, as recorded: user_data always,
        user_cards when a card was won, user_decks when it went into the deck,
        mission_completions for a win, and current_campaigns."""
        keys = ["user_data"]
        if result is not None and result.cards:
            keys.append("user_cards")
        if result is not None and result.deck_changed:
            keys.append("user_decks")
        if won:
            keys.append("mission_completions")
        keys.append("current_campaigns")
        return self._start_parts(params, keys)

    def _with_result(self, reply: dict, result: MissionResult | None, params: dict) -> dict:
        """A replayed mission's last reply with our rewards in place of the recorded ones
        (they follow the save: a recorded first win may be a repeat here)."""
        bd = reply.get("battle_data")
        if result is None or not isinstance(bd, dict):
            return reply
        others = [r for r in bd.get("rewards") or [] if isinstance(r, dict)
                  and str(r.get("user_id")) != str(self.uid)]
        bd["rewards"] = [result.reward, *others]
        for k in ("user_cards", "user_decks", "mission_completions",
                  "new_cards_data", "completed_achievements", "user_achievements"):   # achievements aren't kept
            reply.pop(k, None)
        for k, v in self._final_parts(result, battle_won(reply), params).items():
            reply.setdefault(k, v)          # a recorded user_data stays, with the save's values already in
        return reply

    def _charge_mission(self, params: dict) -> tuple[tuple[dict, str] | None, str]:
        """Takes the mission's energy cost from the save: (refusal, None) if there isn't
        enough, else (None, a note for the log).

        The energy has already been refilled up to now. Below the maximum, the sync point
        stays put, so the part-way progress towards the next point is kept; from full, the
        refill has just set it to now. Both are what the recorded starts show.
        """
        mid = str(params.get("mission_id"))
        player = self.player()
        costs = self._mission_costs()
        cost = costs.get(mid)
        if player is None:
            return None, ""
        if cost is None:
            return None, "; no energy cost in the mission data" if costs else ""
        if player.energy < cost:
            # The game checks its own copy first and only asks when it thinks there's
            # enough, so this means the two disagree. The refusal carries the real
            # user_data, which the game adopts (tested offline); the wording is ours.
            reply = self._refuse(params, f"Not enough energy: this mission needs {cost}, and you have "
                                         f"{player.energy}.")
            reply["user_data"] = self.current_user_data(params)
            return (reply, f"refused mission {mid}: needs {cost} energy, has {player.energy}"), ""
        before = player.energy
        player.energy -= cost
        self.player_store.save(player)
        return None, f"; {cost} energy ({before} -> {player.energy})"

    def _bge_defs(self) -> dict:
        if self._bges is None:
            self._bges = bge.load(self._assets)
        return self._bges

    def _in_tutorial(self, player) -> bool:
        """The tutorial is under way: its flag is a part number, not -1 (done) or -2 (off)."""
        raw = str((player.flags or {}).get("tutorial"))
        return raw.isdigit() and (not self.tutorials or int(raw) < len(self.tutorials))

    def _battle_finished(self, body: dict) -> tuple[str, MissionResult | None]:
        """A replayed mission's end: pays its rewards into the save by the same rules as
        the battle engine. (Note for the log, what was paid.) Arena fights aren't paid yet."""
        b = self.battle
        if b is None or not b.get("mission_id"):
            return "", None
        won = battle_won(body)
        result = self._finish_mission(b["mission_id"], won)
        if result is None:
            known = self.player() is not None and won
            return (f"; mission {b['mission_id']} won, saved (not in the mission data, so nothing paid)"
                    if known else ""), None
        return f"; mission {b['mission_id']} {'won' if won else 'lost'}, paid {result.summary()}", result
