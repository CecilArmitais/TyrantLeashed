"""Items (items.py): the inventory and using items.
Part of GameApi (core.py), as ItemCalls."""

from __future__ import annotations

import items
import packs


class ItemCalls:
    """GameApi's part for items (items.py): the inventory and using items."""

    def _item_defs(self) -> dict:
        if self._items is None:
            self._items = items.load(self._assets)
        return self._items

    def _item_message(self, message: str, params: dict) -> tuple[dict, str]:
        """getUserItems (the inventory) and consumeItem item_id (the inventory's Use)."""
        player = self.player()
        if message == "getUserItems":
            reply = self._ok(params)
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
                cap = self._card_cap(player)
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
            shown[items.SALVAGE_POINTS] = gained = self._add_sp(player, item.sp)    # shown as "Salvage Points"
            did.append(f"{gained} SP")
        if item.refill:
            self._refill_energy(player)
            did.append(f"+{player.max_energy} energy")
        self.player_store.save(player)
        reply = self._ok(params)
        reply.update(self._player_parts(params, ("user_data", "user_cards")))
        reply["new_cards"] = [str(c) for c in got]
        reply["new_items"] = items.new_items_json(shown)
        reply["user_items"] = items.user_items_json(player.items)
        return reply, (f"used {item.cost} {item.name} ({items.owned(player.items, item.id)} left): "
                       + "; ".join(did))
