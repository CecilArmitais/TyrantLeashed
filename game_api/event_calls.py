"""What the events' reward claims share: paying reward tiers into the save, opening a
reward's packs, the claim's reply and its log line. Brawls, conquest and wars claim with
_pay_claim; raids pay their tiers with _pay_tiers (their reply differs).
Part of GameApi (core.py), as EventCalls."""

from __future__ import annotations

import dataclasses

from rules import items, packs, raids


class EventCalls:
    """GameApi's part for the events' reward claims: tiers paid into the save, the reply."""

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
                    got += self._open_reward(kind, rid, amount, cards, rng)
        for cid in got:
            player.add_card(cid)                      # counted in cards_earned
        for iid, n in gains.items():
            items.add(player.items, iid, n)
        return got, gains

    def _pay_claim(self, params: dict, tiers, row: dict, save_state) -> tuple[dict, list[int], dict]:
        """A claim paid: the tiers into the save, the row marked claimed, both saved
        (save_state saves the event's own state): (the reply, as _reward_reply makes it,
        the cards opened, {item id: how many})."""
        player = self.player()
        got, gains = self._pay_tiers(player, tiers)
        row["claimed"] = 1
        self.player_store.save(player)
        save_state()
        return self._reward_reply(params, player, got, gains), got, gains

    def _reward_reply(self, params: dict, player, got: list[int], gains: dict) -> dict:
        """A claim's reply, read like a pack purchase: the save's data, new_cards and
        new_items (shown together), and the inventory."""
        reply = self._ok(params)
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

    def _open_reward(self, kind, rid, amount, cards, rng) -> list[int]:
        """A reward (a card, a pack or a mini pack, `amount` times) opened into cards."""
        if kind == "card":
            return [rid] * amount if cards.get(rid) else []
        if kind == "pack":
            draws = packs.STORE_PACK_DRAWS.get(rid)
            pack = packs.Pack(f"pack {rid}", draws) if draws else None
        else:
            mp = self._mini_packs().get(str(rid))
            pack = packs.reward_pack(mp) if mp else None
            if pack is not None and not all(packs.rates(d, packs.base_pool(cards, pack.faction, pack.set_id))
                                            for d, _ in pack.draws):
                pack = dataclasses.replace(pack, set_id=raids.FALLBACK_REWARD_SET)   # an emptied reward set
        if pack is None:
            return []
        return [c for _ in range(amount) for c in packs.open_pack(pack, cards, rng)]
