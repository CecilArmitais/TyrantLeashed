"""The Upgrade screen (upgrades.py): upgrades, salvage, locks and fusion.
Part of GameApi (core.py), as UpgradeCalls."""

from __future__ import annotations

import achievements
import upgrades

from .base import NotSupported
from .messages import LOCK, SALVAGE, SALVAGE_ALL, UPGRADE


class UpgradeCalls:
    """GameApi's part for the Upgrade screen (upgrades.py): upgrades, salvage, locks and fusion."""

    def _recipes(self) -> dict:
        if getattr(self, "_upgrade_data", None) is None:
            self._upgrade_data = (upgrades.load_levels(self._assets), upgrades.load_recipes(self._assets))
        return self._upgrade_data[1]

    # ---- the Upgrade screen ---------------------------------------------------------------------

    def _upgrade_rules(self) -> upgrades.Rules:
        recipes = self._recipes()
        levels = self._upgrade_data[0]
        return upgrades.Rules(self._engine_data()[0], levels, recipes,
                              commander_mult=int(self._start.get("com_sp_mult") or upgrades.DEFAULT_COMMANDER_MULT),
                              max_salvage=self._cap_sp())

    def _upgrade_screen(self, message: str, params: dict) -> tuple[dict, str]:
        """upgradeCard card_id in_deck, salvageCard card_id, salvageL1CommonCards,
        salvageL1RareCards, setCardLock card_id locked, fuseCard card_id (upgrades.py).
        The replies have the shape the game reads (UpgradeCardResponse and
        SalvageCardResponse): user_data, user_cards, user_decks, and new_card_id for an
        upgrade or fusion; salvage replies also carry buyback_data (empty, as recorded)."""
        player = self.player()
        if player is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep cards in.")
        rules = self._upgrade_rules()
        cid = params.get("card_id")
        before = player.salvage or 0
        new_id = None
        try:
            if message == UPGRADE:
                name = rules.card(cid).name
                new_id, cost = upgrades.upgrade(rules, player, cid, str(params.get("in_deck")) == "1",
                                                self._active_deck_id())
                how = f"upgraded {name} to level {rules.cards[new_id].level} for {cost} SP"
                achievements.count(player, "upgrade_level", rules.cards[new_id].level, "max")
            elif message == SALVAGE:
                name = rules.card(cid).name
                gained = upgrades.salvage(rules, player, cid)
                how = f"salvaged {name} for {gained} SP"
                achievements.count(player, "salvage", gained)
            elif message in SALVAGE_ALL:
                n, gained = upgrades.bulk_salvage(rules, player, SALVAGE_ALL[message])
                how = f"salvaged {n} level-1 {upgrades.RARITY_NAMES[SALVAGE_ALL[message]]} cards for {gained} SP"
                achievements.count(player, "salvage", gained)
            elif message == LOCK:
                locked = str(params.get("locked")) in ("1", "true", "True")
                upgrades.set_lock(player, cid, locked)
                how = f"{'locked' if locked else 'unlocked'} {rules.card(cid).name}"
            else:
                new_id, used = upgrades.fuse(rules, player, cid)
                achievements.count(player, "fusions", 1)
                how = (f"fused {rules.cards[new_id].name} from "
                       + " + ".join(f"{rules.cards[r].name}{f' x{n}' if n > 1 else ''}" for r, n in used))
        except upgrades.UpgradeError as e:
            reply = self._refuse(params, str(e))
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused {message}: {e}"
        if message != LOCK:
            self._track(player)              # "Rise to Power", "Scavenger", "Fusion Master"
        self.player_store.save(player)
        start = {"time": self._start.get("time"), "user_data": self._start.get("user_data") or {},
                 "user_cards": {}, "user_decks": {}}
        reply = self.envelope(params)
        reply["result"] = True
        reply.update({k: v for k, v in self._prepare(start, params).items()
                      if k in ("user_data", "user_cards", "user_decks")})
        if new_id is not None:
            reply["new_card_id"] = str(new_id)
        else:
            reply["buyback_data"] = []
        reply["new_cards_data"] = []
        if player.salvage != before:
            how += f" (SP {before} -> {player.salvage})"
        return reply, how
