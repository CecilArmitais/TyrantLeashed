"""The player's own data: the crate, name, decks and flags, refreshes, energy refills, the SP cap.
Part of GameApi (core.py), as PlayerDataCalls."""

from __future__ import annotations

import json
from collections import defaultdict

import daily_crate
import items
import upgrades
from accounts import name_problem
from rewards import DECK_SIZE

from .base import DEFAULT_REFILL_COST, INVALID_FLAG, NAME_LENGTH, NotSupported, flag_value
from .messages import CRATE, REFRESH, SET_ACTIVE, SET_DECK, SET_NAME


class PlayerDataCalls:
    """GameApi's part for the player's own data: the crate, name, decks and flags,
    refreshes, energy refills, the SP cap."""

    def _open_crate(self, params: dict) -> tuple[dict, str]:
        """useDailyBonus (no parameters): the home screen crate (daily_crate.py)."""
        player = self.player()
        c = self.content()
        try:
            reward, streak = daily_crate.open_crate(player.daily_bonus, c.crate_rewards, c.crate_every_hours,
                                                    int(self.clock()))
        except daily_crate.NotReady as e:
            reply = self._refuse(params, str(e))
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused {CRATE}: {e}"
        daily_crate.pay(reward, player, self._cap_sp())
        self.player_store.save(player)
        reply = self._ok(params)                      # daily_bonus_time: the next crate
        reply["daily_bonus"] = streak
        reply["bonus_result"] = daily_crate.bonus_result(reward)
        reply.update(self._player_parts(params, ("user_data", "user_cards") if reward.card else ("user_data",)))
        reply["new_cards"] = [str(reward.card)] if reward.card else []
        card = self._engine_data()[0].get(reward.card) if reward.card else None
        return reply, (f"opened the crate (day {streak} in a row): {reward.describe(card.name if card else None)}; "
                       f"the next one in {c.crate_every_hours}h")

    def _set_name(self, params: dict) -> tuple[dict, str]:
        """setUserName name: the player's name (the reply is read for user_data)."""
        player = self.player()
        name = " ".join(str(params.get("name") or "").split())
        if not name or len(name) > NAME_LENGTH:
            return self._refuse(params, f"A name needs 1 to {NAME_LENGTH} characters."), f"refused {SET_NAME}: {name!r}"
        if not self.single_player:                       # the name is also the one the player signs in with
            problem = name_problem(name)
            if problem is None and self.accounts.find(name) not in (None, self.uid):
                problem = f"The name {name} is taken."
            if problem:
                return self._refuse(params, problem), f"refused {SET_NAME}: {name!r}"
            self.accounts.set_name(self.uid, name)
        before, player.name = player.name, name
        player.name_changes += 1
        self.player_store.save(player)
        reply = self._ok(params)
        reply.update(self._player_parts(params, ("user_data",)))
        return reply, f"name: {before!r} -> {name!r}"

    def _choose_deck(self, message: str, params: dict) -> tuple[dict, str]:
        """setActiveDeck / setDefenseDeck deck_id (read back from user_data and user_decks)."""
        player = self.player()
        did = str(params.get("deck_id"))
        deck = (player.decks or {}).get(did)
        if deck is None or not did.isdigit() or not 1 <= int(did) <= (player.max_decks or 1):
            return self._refuse(params, f"Deck {did} isn't one of your decks."), f"refused {message}: deck {did}"
        if not deck.get("commander_id"):
            return self._refuse(params, f"Deck {did} has no commander yet."), f"refused {message}: deck {did} empty"
        which = "active_deck" if message == SET_ACTIVE else "defense_deck"
        setattr(player, which, did)
        self.player_store.save(player)
        reply = self._ok(params)
        reply.update(self._player_parts(params, ("user_data", "user_decks")))
        return reply, f"{'active' if message == SET_ACTIVE else 'defence'} deck: {did}"

    def _refresh(self, message: str, params: dict) -> tuple[dict, str]:
        """getUserData, getUserCards, getUserDecks, getCardData, getVersionNumber: what the
        game asks to see again, from the save."""
        reply = self._ok(params)
        keys = REFRESH[message]
        reply.update(self._player_parts(params, [k for k in keys if k != "card_data"]))
        if "card_data" in keys:
            reply["card_data"] = []                    # as the recorded login sent it
        return reply, f"answered from the save ({', '.join(keys) or 'the envelope'})"

    def _card_cap(self, player) -> int:
        """How many cards the player can hold: theirs (card slots raise it), else the login's."""
        return player.max_cards or int(((self._start.get("user_data") or {}).get("caps") or {})
                                       .get("max_cards") or 1600)

    def _add_sp(self, player, sp: int) -> int:
        """Gives the player SP, up to the cap: what they got."""
        before = player.salvage or 0
        player.salvage = min(self._cap_sp(), before + sp)
        return player.salvage - before

    def _cap_sp(self) -> int:
        """The SP cap: the player's (an Elite Pack raises it), else the login's."""
        player = self.player() if self.player_store is not None else None
        if player is not None and player.max_salvage:
            return player.max_salvage
        caps = (self._start.get("user_data") or {}).get("caps") or {}
        return int(caps.get("max_salvage") or upgrades.DEFAULT_MAX_SALVAGE)

    def _buy_energy_refill(self, params: dict) -> tuple[dict, str]:
        """buyEnergyRefillTokens: max energy added on top of the current energy (over the
        cap if need be: the game's own dialog says so, and a level-up does the same) for
        `wb_energy_cost` war bonds. The game shows exactly the user_data that comes back
        (missing fields read as 0), so the reply always carries all of it."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep energy in.")
        if items.owned(player.items, items.ENERGY_REFILL):
            # The energy bar's + sends this without asking when the player owns an Energy
            # Refill (EnergyBar.BuyRefillTapped), so the item is used, not war bonds.
            before = player.energy
            self._refill_energy(player)
            items.take(player.items, items.ENERGY_REFILL, 1)
            self.player_store.save(player)
            reply = self._ok(params)
            reply["user_data"] = self.current_user_data(params)
            reply["user_items"] = items.user_items_json(player.items)
            return reply, (f"energy refill: +{player.max_energy} ({before} -> {player.energy}), an Energy Refill "
                           f"({items.owned(player.items, items.ENERGY_REFILL)} left)")
        cost = int(self._start.get("wb_energy_cost") or DEFAULT_REFILL_COST)
        if self._short(player, "war_bonds", cost):
            # The game checks its own copy first ("You do not have enough war bonds to buy
            # a refill..."), so this only happens when the two disagree.
            reply = self._refuse(params, f"Not enough war bonds: a refill costs {cost}, and you have "
                                         f"{player.war_bonds}.")
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused: a refill costs {cost} war bonds, has {player.war_bonds}"
        before = (player.energy, player.war_bonds)
        self._refill_energy(player)
        unpaid = self._spend(player, "war_bonds", cost)
        self.player_store.save(player)
        reply = self._ok(params)
        reply["user_data"] = self.current_user_data(params)
        return reply, (f"energy refill: +{player.max_energy} ({before[0]} -> {player.energy}), "
                       f"{cost} war bonds ({before[1]} -> {player.war_bonds}){unpaid}")

    def _refill_energy(self, player) -> None:
        player.energy += player.max_energy
        player.energy_updated = int(self.clock())   # at or over the max: the refill clock waits from now

    def _set_deck(self, params: dict) -> tuple[dict, str]:
        """setDeckCards deck_id, commander_id, cards (JSON {card_id: count}), dominion_id:
        saves the deck. The reply has the recorded setDeckCards reply's shape, with the
        save's data. The refusals are worded by us (no recorded ones)."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep decks in.")
        try:
            deck_id = str(int(params.get("deck_id")))
            commander = int(params.get("commander_id"))
            raw = params.get("cards") or "{}"
            counts = json.loads(raw) if isinstance(raw, str) else raw
            cards = {str(int(k)): int(v) for k, v in counts.items() if int(v) > 0}
            dominion = int(params.get("dominion_id") or 0)
        except (TypeError, ValueError, AttributeError):
            return self._refuse(params, "Invalid deck."), "refused setDeckCards: couldn't read the deck"
        saved = self.player()
        max_decks = (saved.max_decks if saved is not None and saved.max_decks else None) or int(self._start.get("max_decks") or 1)
        need = defaultdict(int)
        need[str(commander)] += 1
        for cid, n in cards.items():
            need[cid] += n
        if dominion:
            need[str(dominion)] += 1
        short = [cid for cid, n in need.items() if (player.cards.get(cid) or {}).get("num_owned", 0) < n]
        problem = (f"deck {deck_id} isn't one of your decks" if not 1 <= int(deck_id) <= max_decks
                   else f"a deck holds at most {DECK_SIZE} cards" if sum(cards.values()) > DECK_SIZE
                   else f"you don't own enough of card {', '.join(short)}" if short else None)
        if problem:
            return self._refuse(params, f"Can't save that deck: {problem}."), f"refused setDeckCards: {problem}"
        old = (player.decks or {}).get(deck_id) or {}
        player.decks = player.decks or {}
        player.decks[deck_id] = {"name": old.get("name"), "commander_id": commander, "cards": cards,
                                 "dominion_id": dominion}
        self.player_store.save(player)
        recorded = next((bodies[0] for (m, _), bodies in self.replays.items() if m == SET_DECK), None)
        if recorded is not None:
            reply = self._prepare(recorded, params)
        else:
            keys = ("user_data", "user_cards", "user_decks")
            reply = self._ok(params)
            reply.update(self._start_parts(params, keys))
        return reply, f"saved deck {deck_id}: commander {commander}, {sum(cards.values())} cards"

    def _set_flag(self, params: dict) -> tuple[dict, str]:
        """setUserFlag flag=<name> value=<value>: keeps the flag in the save. The reply is
        the player's user_data with the new flag, like the real server's."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep flags in.")
        flag, raw = params.get("flag"), params.get("value")
        if not flag or raw is None:
            return self._refuse(params, INVALID_FLAG), "refused: no flag or no value"
        if flag == "tutorial":
            # The flag is the index of the current tutorial part. Negative values are the
            # game's own markers: -1 all done, -2 switched off (the game sends that when it
            # loads with the tutorial unfinished but mission 1 already won). The real server
            # refused tutorial=5 for this 5-part tutorial, and the game then sent -1.
            n = len(self.tutorials)
            try:
                step = int(str(raw))
            except ValueError:
                step = None
            if step is None or (n and step >= n):
                return self._refuse(params, INVALID_FLAG), (f"refused tutorial={raw} (it has parts 0 to {n - 1}; "
                                                            f"-1 is finished), like the real server")
        known = flag in player.flags
        player.flags[flag] = flag_value(raw)
        self.player_store.save(player)
        reply = self._ok(params)
        reply["user_data"] = self.current_user_data(params)
        how = f"saved {flag}={raw}"
        if flag == "tutorial":
            how += (" (tutorial finished)" if step == -1 else " (tutorial switched off by the game)" if step < -1
                    else f" (tutorial part {step + 1} of {len(self.tutorials) or '?'})")
        if not known:
            how += " (a flag the starting data didn't have)"
        return reply, how

    def _in_tutorial(self, player) -> bool:
        """The tutorial is under way: its flag is a part number, not -1 (done) or -2 (off)."""
        raw = str((player.flags or {}).get("tutorial"))
        return raw.isdigit() and (not self.tutorials or int(raw) < len(self.tutorials))
