"""Items (items.py): the inventory, using items, and the item tiers rewards pay.
Part of GameApi (core.py), as ItemCalls."""

from __future__ import annotations

import items
import packs


class ItemCalls:
    """GameApi's part for items (items.py): the inventory, using items, and the item tiers rewards pay."""

    # ---- items (items.py) ----------------------------------------------------------------------------

    def _item_defs(self) -> dict:
        if getattr(self, "_items", None) is None:
            self._items = items.load(self._assets)
        return self._items

    def _pay_tiers(self, player, tiers) -> tuple[list[int], dict[int, int]]:
        """Pays reward tiers into the save: (the cards opened, {item id: how many})."""
        cards, _ = self._engine_data()
        rng = self.rng_factory()
        got, gains = [], {}
        for tier in tiers:
            for kind, rid, amount in tier.rewards:
                if kind == "item":
                    gains[int(rid)] = gains.get(int(rid), 0) + amount
                else:
                    got += self._open_raid_reward(kind, rid, amount, cards, rng)
        for cid in got:
            player.add_card(cid)                      # counted in cards_earned
        for iid, n in gains.items():
            items.add(player.items, iid, n)
        return got, gains

    def _reward_reply(self, params: dict, player, got: list[int], gains: dict) -> dict:
        """A claim's reply, read like a pack purchase: the save's data, new_cards and
        new_items (shown together), and the inventory."""
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._player_parts(params, ("user_data", "user_cards")))
        reply["new_cards"] = [str(c) for c in got]
        reply["new_items"] = items.new_items_json(gains)
        reply["user_items"] = items.user_items_json(player.items)
        return reply

    def _describe_gains(self, got: list[int], gains: dict) -> str:
        cards, _ = self._engine_data()
        defs = self._item_defs()
        names = ", ".join(cards[c].name for c in got[:8]) + (f" and {len(got) - 8} more" if len(got) > 8 else "")
        out = f"{len(got)} cards ({names or 'none'})"
        if gains:
            out += "; items: " + ", ".join(f"{defs[i].name if i in defs else i} x{n}" for i, n in gains.items())
        return out

    def _item_message(self, message: str, params: dict) -> tuple[dict, str]:
        """getUserItems (the inventory) and consumeItem item_id (the inventory's Use)."""
        player = self.player()
        if message == "getUserItems":
            reply = self.envelope(params)
            reply["result"] = True
            reply["user_items"] = items.user_items_json(player.items)
            return reply, f"items: {sum(player.items.values())} in {len(player.items)} kind(s)"
        defs = self._item_defs()
        try:
            item = defs.get(int(params.get("item_id")))
        except (TypeError, ValueError):
            item = None
        cards, _ = self._engine_data()
        pack = items.item_pack(item) if item else None
        try:
            items.check_use(item, player.items)
            if pack is not None:
                n = sum(count for _, count in pack.draws)
                cap = player.max_cards or int(((self._start.get("user_data") or {}).get("caps") or {})
                                              .get("max_cards") or 1600)
                if player.card_count + n > cap:
                    raise items.ItemError(f"Your inventory is full: {player.card_count} of {cap} cards.")
            if item.sp and (player.salvage or 0) >= self._cap_sp():
                raise items.ItemError(f"Your SP is full: {player.salvage} of {self._cap_sp()}.")
        except items.ItemError as e:
            reply = self._refuse(params, str(e))
            reply["user_items"] = items.user_items_json(player.items)
            return reply, f"refused consumeItem {params.get('item_id')}: {e}"
        items.take(player.items, item.id, item.cost)
        got, did, shown = [], [], {}
        if pack is not None:
            got = packs.open_pack(pack, cards, self.rng_factory())
            for cid in got:
                player.add_card(cid)
            did.append(", ".join(cards[c].name for c in got))
        if item.sp:
            before = player.salvage or 0
            player.salvage = min(self._cap_sp(), before + item.sp)
            shown[items.SALVAGE_POINTS] = player.salvage - before          # shown as "Salvage Points"
            did.append(f"{player.salvage - before} SP")
        if item.refill:
            self._refill_energy(player)
            did.append(f"+{player.max_energy} energy")
        self.player_store.save(player)
        reply = self.envelope(params)
        reply["result"] = True
        reply.update(self._player_parts(params, ("user_data", "user_cards")))
        reply["new_cards"] = [str(c) for c in got]
        reply["new_items"] = items.new_items_json(shown)
        reply["user_items"] = items.user_items_json(player.items)
        return reply, (f"used {item.cost} {item.name} ({items.owned(player.items, item.id)} left): "
                       + "; ".join(did))
