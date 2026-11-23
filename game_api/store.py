"""The store: packs, promos, event boxes and store upgrades, as content.toml sets them.
Part of GameApi (core.py), as StoreCalls."""

from __future__ import annotations

import copy
import dataclasses

import boxes
import items
import packs
import store
from content import PACK_IDS, Content, Promo
from player_state import Player, decks_json
from rewards import DECK_SIZE

from .base import NotSupported
from .messages import BUY_UPGRADE, PACK_GOLD, PROMO_GOLD, PROMO_WB


class StoreCalls:
    """GameApi's part for the store: packs, promos, event boxes and store upgrades, as
    content.toml sets them."""

    # ---- what's on offer -------------------------------------------------------------------------

    def _store(self, player: Player | None = None, from_save: bool = True) \
            -> tuple[Content, list[dict], list[dict], list[str]]:
        """The store as the content file sets it: (content, mini pack promo entries, event
        box entries, problems: what in the file can't be offered, and why). The boxes'
        progress is `player`'s, or the save's (from_save) or none."""
        c = self.content()
        if player is None and from_save:
            player = self.player()
        offers, problems = self._promo_offers(player)
        problems = list(c.problems) + problems
        promo_entries = [o["entry"] for o in offers]
        now = int(self.clock())
        box_entries = []
        for bid in c.boxes:
            box = self._boxes().get(bid)
            problem = self._box_problem(bid, box)
            if problem:
                problems.append(problem)
                continue
            state = ((player.boxes if player is not None else {}).get(str(bid))) or boxes.new_state()
            if boxes.sold_out(box, state):
                continue                                  # bought out for good: it leaves the store
            box_entries.append(store.tiered_box_entry(box, state, now, boxes.tier(box, state))
                               if box.tiered else
                               store.box_entry(box, state, now, boxes.next_deal(box, state)))
        return c, promo_entries, box_entries, problems

    def _promo_offers(self, player) -> tuple[list[dict], list[str]]:
        """What the content file's promos offer `player` now (their entries' prices are the
        player's next ones): [{"entry", "pack", "key", "bundle"}], and the problems."""
        cards = self._engine_data()[0]
        mini, recipes = self._mini_packs(), self._recipes()
        scaling_table, _ = self._scaling()
        bought = (lambda key: player.promo_buys.get(key, 0)) if player is not None else (lambda key: 0)
        offers, problems = [], []
        for promo in self.content().promos:
            if promo.kind == "card":
                card = cards.get(promo.card)
                table = scaling_table.get(card.rarity) if card is not None else None
                if card is None:
                    problems.append(f"store.promos: there's no card {promo.card}")
                    continue
                if table is None and promo.gold is None and promo.war_bonds is None:
                    problems.append(f"store.promos: card {promo.card} ({card.name}) needs a price: the scaling "
                                    "table only prices Epic to Mythic cards")
                    continue
                key = store.card_key(card.id)
                entry = store.card_entry(card, table or {}, bought(key))
                if promo.gold is not None or promo.war_bonds is not None:     # a fixed price instead
                    entry.pop("wb_cost")
                    entry.pop("scaling_pricing")
                    entry.update({"gold_cost": str(promo.gold), "sub_item_id": "gold"} if promo.gold is not None
                                 else {"wb_cost": str(promo.war_bonds)})
                pack = packs.Pack(card.name, (), gold=promo.gold, fixed=(card.id,),
                                  war_bonds=int(entry["wb_cost"]) if "wb_cost" in entry else None)
                offers.append({"entry": entry, "pack": pack, "key": key, "bundle": None})
            elif promo.kind == "bundle":
                bundle = store.BUNDLES.get(promo.bundle)
                if bundle is None:
                    problems.append(f"store.promos: there's no bundle {promo.bundle} (the recorded ones: "
                                    + ", ".join(map(str, store.BUNDLES)) + ")")
                    continue
                key = store.bundle_key(bundle.id)
                if bundle.once and bought(key):
                    continue                                        # bought once: it leaves the store
                pack = packs.Pack(bundle.name, (), gold=promo.gold, war_bonds=promo.war_bonds,
                                  fixed=(bundle.card,) if bundle.card else (), sp=bundle.sp)
                offers.append({"entry": store.bundle_entry(bundle, promo.gold, promo.war_bonds), "pack": pack,
                               "key": key, "bundle": bundle})
            else:
                mp = mini.get(str(promo.mini_pack))
                pack = packs.mini_pack(mp, gold=promo.gold, war_bonds=promo.war_bonds) if mp else None
                if mp is None or not packs.openable(pack, cards, recipes):
                    problems.append(f"store.promos: mini pack {promo.mini_pack} " + (
                        "doesn't exist" if mp is None else "can't be opened: the card files lack its cards"
                        if pack is not None else "has data we don't know how to open"))
                    continue
                key = store.pack_key(promo.mini_pack)
                scaling = mp.get("scaling_pricing")
                if scaling and promo.gold is None and promo.war_bonds is None:
                    pack = dataclasses.replace(pack, war_bonds=packs.scaling_price(scaling, bought(key)))
                if pack.gold is None and pack.war_bonds is None:
                    problems.append(f"store.promos: mini pack {promo.mini_pack} ({mp['name']}) has no price in "
                                    "the file: give it one, gold or war_bonds")
                    continue
                entry = store.promo_entry(promo.mini_pack, mp, Promo(
                    promo.mini_pack, gold=pack.gold, war_bonds=pack.war_bonds if pack.gold is None else None),
                    packs.preview_cards(cards, mp))
                if pack.fixed:
                    entry["card"] = str(pack.fixed[0])
                if scaling:
                    entry["scaling_pricing"] = dict(scaling)
                offers.append({"entry": entry, "pack": pack, "key": key, "bundle": None})
        return offers, problems

    def _scaling(self):
        if getattr(self, "_scaling_pack", None) is None:
            self._scaling_pack = packs.load_scaling_pack(self._assets)
        return self._scaling_pack

    def _mini_packs(self) -> dict:
        if getattr(self, "_mini_pack_data", None) is None:
            self._mini_pack_data = packs.load_mini_packs(self._assets)
        return self._mini_pack_data

    def store_report(self) -> str:
        """One line on what the store offers, then any problems in the content file."""
        saved = self.player_store.load() if self.player_store is not None else None
        c, promos, box_entries, problems = self._store(saved, from_save=False)
        packs_line = ", ".join(c.packs) or "none"
        line = (f"Store ({self.content_store.path.name}): packs {packs_line}; "
                f"{len(promos)} promo{'s' * (len(promos) != 1)}; boxes: "
                + (", ".join(f"{e['name']} ({e['item_id']})" for e in box_entries) or "none"))
        return "\n".join([line] + [f"  {self.content_store.path.name}: {p}" for p in problems])

    # ---- buying packs and promos -----------------------------------------------------------------

    def _buy_pack(self, message: str, params: dict) -> tuple[dict, str]:
        """buyPackGold / buyPackTokens pack_id: a `store_packs` pack (packs.py)."""
        if self.player() is None:
            raise NotSupported("there is no starting data (game/fixtures/init.json) to keep cards in.")
        if str(params.get("pack_id")) not in {PACK_IDS[p] for p in self.content().packs}:
            return self._refuse(params, "That pack isn't in the store."), \
                f"refused {message}: pack {params.get('pack_id')} isn't in {self.content_store.path.name}"
        pack = packs.store_pack(self._start, params.get("pack_id"))
        if pack is None:
            raise NotSupported(f"pack {params.get('pack_id')} can't be opened offline yet.")
        currency = "gold" if message == PACK_GOLD else "war bonds"
        price = pack.gold if message == PACK_GOLD else pack.war_bonds
        if price is None:
            return self._refuse(params, f"The {pack.name} isn't sold for {currency}."), \
                f"refused {message}: no {currency} price"
        return self._sell_pack(params, pack, price, currency, PACK_GOLD)

    def _sell_pack(self, params: dict, pack, price: int, currency: str, template: str,
                   with_data: bool = False, open_cards=None, n: int | None = None,
                   count_key: str | None = None, bundle=None) -> tuple[dict, str]:
        """Takes the price, opens the pack into the inventory (and, during the tutorial,
        the deck), and answers in the recorded reply's shape: the recorded reply for
        `template` with the save's data, plus new_cards (and new_cards_data for promos).
        open_cards(player) -> card ids replaces opening `pack` (an event box: then `pack`
        only needs a name); n is how many cards it gives. count_key: the purchase is
        counted (rising prices, one-time bundles); bundle: its other contents are added
        (war bonds, upgrades). A pack's SP shows in new_items as "Salvage Points"."""
        player = self.player()
        have = player.gold if currency == "gold" else player.war_bonds
        problem = None
        expected = params.get("expected_cost")
        cap = player.max_cards or int(((self._start.get("user_data") or {}).get("caps") or {}).get("max_cards") or 1600)
        n = pack.size if n is None else n
        if expected not in (None, "") and str(expected).isdigit() and int(expected) != price:
            problem = f"The price has changed: the {pack.name} costs {price} {currency} now."
        elif have < price:
            problem = f"Not enough {currency}: the {pack.name} costs {price}, and you have {have}."
        elif player.card_count + n > cap:
            problem = f"Your inventory is full: {player.card_count} of {cap} cards."
        elif getattr(pack, "sp", 0) and not pack.draws and not pack.fixed and (player.salvage or 0) >= self._cap_sp():
            problem = f"Your SP is full: {player.salvage} of {self._cap_sp()}."
        cards, _ = self._engine_data()
        if not problem and open_cards is not None:
            try:
                new = open_cards(player)
            except boxes.BoxError as e:
                problem = str(e)
        elif not problem:
            new = packs.open_pack(pack, cards, self.rng_factory(), self._recipes())
        if problem:
            reply = self._refuse(params, problem)
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused {pack.name}: {problem}"
        if currency == "gold":
            player.gold -= price
        else:
            player.war_bonds -= price
        for cid in new:
            player.add_card(cid)
        extras, shown = [], {}
        if getattr(pack, "sp", 0):
            before = player.salvage or 0
            player.salvage = min(self._cap_sp(), before + pack.sp)
            shown[items.SALVAGE_POINTS] = player.salvage - before
            extras.append(f"{player.salvage - before} SP")
        if bundle is not None:
            player.war_bonds += bundle.war_bonds
            player.max_energy += bundle.max_energy
            player.max_cards = (player.max_cards or cap) + bundle.card_slots
            player.max_salvage = (player.max_salvage or self._cap_sp()) + bundle.max_salvage
            if bundle.max_stamina and player.arena is not None:
                player.arena["max_stamina"] += bundle.max_stamina
            extras += [f"{v} {what}" for v, what in ((bundle.war_bonds, "war bonds"), (bundle.max_energy, "max energy"),
                                                     (bundle.card_slots, "card slots"),
                                                     (bundle.max_stamina, "max stamina"),
                                                     (bundle.max_salvage, "max SP")) if v]
        if count_key:
            player.promo_buys[count_key] = player.promo_buys.get(count_key, 0) + 1
        into_deck = []
        deck = (player.decks or {}).get(self._active_deck_id())
        if self._in_tutorial(player) and deck is not None:
            for cid in new:
                if sum(deck["cards"].values()) < DECK_SIZE:
                    deck["cards"][str(cid)] = deck["cards"].get(str(cid), 0) + 1
                    into_deck.append(cid)
        self.player_store.save(player)
        recorded = [b for (m, _), bodies in self.replays.items() if m == template for b in bodies]
        plain = [b for b in recorded if "user_decks" not in b] or recorded
        if plain:
            reply = self._prepare(plain[-1], params)
        else:
            reply = self.envelope(params)
            reply["result"] = True
            reply.update({k: v for k, v in self._prepare({"time": self._start.get("time"), **{
                k: self._start[k] for k in ("user_data", "user_cards") if k in self._start}}, params).items()
                if k in ("user_data", "user_cards")})
        reply["result"] = True
        reply["new_cards"] = [str(c) for c in new]
        if with_data:
            reply["new_cards_data"] = packs.new_cards_data(new)
        else:
            reply.pop("new_cards_data", None)
        if into_deck:
            reply["user_decks"] = decks_json(player.decks)
        if shown:
            reply["new_items"] = items.new_items_json(shown)
        if bundle is not None:
            reply.update(self._player_parts(params, ("user_data",)))
            if bundle.max_energy:
                reply["max_energy"] = player.max_energy           # where the client reads the cap
            if bundle.max_stamina and player.arena is not None:
                reply["max_stamina"] = player.arena["max_stamina"]
        if count_key or bundle is not None:                   # the next price, and a one-time bundle gone
            parts = self._prepare({"time": self._start.get("time"),
                                   "store_promos": copy.deepcopy(self._start.get("store_promos") or [])}, params)
            reply["store_promos"] = parts.get("store_promos", [])
        names = ", ".join(f"{cards[c].name} ({'?CRELVM'[cards[c].rarity] if 0 < cards[c].rarity < 7 else '?'})"
                          for c in new)
        return reply, (f"bought {pack.name} for {price} {currency} ({have} -> {have - price}): "
                       + "; ".join(x for x in (names, *extras) if x)
                       + ("; into the deck (tutorial)" if into_deck else ""))

    def _buy_promo(self, message: str, params: dict):
        """buyStorePromoGold / buyStorePromoTokens item_type=3 or 7 item_id: a promo the
        content file offers (a mini pack, a single card, a bundle), or None for others."""
        player = self.player()
        offers, _ = self._promo_offers(player)
        item_id, item_type = str(params.get("item_id")), str(params.get("item_type"))
        offer = next((o for o in offers if str(o["entry"]["item_id"]) == item_id
                      and str(o["entry"]["item_type"]) == item_type), None)
        if offer is None:
            bundle = store.BUNDLES.get(int(item_id)) if item_id.isdigit() else None
            if bundle is not None and item_type == str(store.BUNDLE_ITEM_TYPE) \
                    and player.promo_buys.get(store.bundle_key(bundle.id)):
                return self._refuse(params, f"You already bought the {bundle.name}: it's one time only."), \
                    f"refused {message}: {bundle.name} already bought"
            return None
        pack = offer["pack"]
        currency = "gold" if message == PROMO_GOLD else "war bonds"
        price = pack.gold if message == PROMO_GOLD else pack.war_bonds
        if price is None:
            return self._refuse(params, f"The {pack.name} isn't sold for {currency}."), \
                f"refused {message}: no {currency} price"
        return self._sell_pack(params, pack, price, currency, PROMO_GOLD, with_data=True, count_key=offer["key"],
                               bundle=offer["bundle"])

    # ---- event boxes -----------------------------------------------------------------------------

    def _boxes(self) -> dict:
        if getattr(self, "_box_data", None) is None:
            self._box_data = boxes.load_boxes(self._assets)
        return self._box_data

    def _rarity_of(self, cid: int) -> int:
        return self._engine_data()[0][cid].rarity

    def _box_problem(self, bid: int, box) -> str | None:
        """Why a box in the content file can't be sold, or None."""
        if box is None:
            return f"boxes.on_offer: there's no box {bid} in event_box.xml"
        cards = self._engine_data()[0]
        every = [cid for t in (box.tiers or (box.cards,)) for cid, _ in t]
        missing = [cid for cid in every if cid not in cards.cards]
        if missing or not every:
            return f"boxes.on_offer: the {box.name} ({bid}) has cards the card files don't: {missing[:5]}"
        return None

    def _offered_box(self, params: dict):
        """(box, None) for a box the store has now, or (None, the refusal)."""
        try:
            bid = int(params.get("item_id"))
        except (TypeError, ValueError):
            bid = None
        box = self._boxes().get(bid) if bid is not None else None
        if box is None or bid not in self.content().boxes or self._box_problem(bid, box):
            return None, (self._refuse(params, "That box isn't in the store."),
                          f"refused: box {params.get('item_id')} isn't on offer ({self.content_store.path.name})")
        return box, None

    def _buy_box(self, params: dict) -> tuple[dict, str]:
        """buyStorePromoTokens item_type=8 (10 for a tiered box) item_id=<box> expected_cost:
        one card from an event box (its open tier), for its war bond price (boxes.py)."""
        box, refusal = self._offered_box(params)
        if refusal:
            return refusal

        def pull(player):
            state = player.boxes.setdefault(str(box.id), boxes.new_state())
            return [boxes.pull(box, state, self._rarity_of, self.rng_factory())]

        reply, how = self._sell_pack(params, packs.Pack(box.name, ()), box.wb_cost, "war bonds", PROMO_GOLD,
                                     with_data=True, open_cards=pull, n=1)
        return reply, how + self._box_progress(box)

    def _buy_box_deal(self, params: dict) -> tuple[dict, str]:
        """buyStorePromoTokens item_type=8 item_id=<box> box_discount_id=<deal>: the box's
        deal, several cards at a discount, once per round of the box (boxes.py). Found
        offline: the deal's Buy sends this, with expected_cost the box's price (60), not
        the deal's, so either is accepted."""
        box, refusal = self._offered_box(params)
        if refusal:
            return refusal
        raw = params.get("box_discount_id")
        deal = next((d for d in box.deals if str(d.id) == str(raw)), None)
        if deal is None:
            return self._refuse(params, "That deal isn't on offer."), f"refused {PROMO_WB}: no deal {raw!r}"
        player = self.player()
        state = player.boxes.get(str(box.id)) or boxes.new_state()
        offered = boxes.next_deal(box, state)
        if offered is None or offered.id != deal.id:
            return self._refuse(params, f"The {deal.name} can be bought once per box."), \
                f"refused {PROMO_WB}: {deal.name} isn't the deal on offer"

        def pull(player):
            state = player.boxes.setdefault(str(box.id), boxes.new_state())
            return boxes.buy_deal(box, deal, state, self._rarity_of, self.rng_factory())

        if str(params.get("expected_cost")) == str(box.wb_cost):
            params = {**params, "expected_cost": str(deal.wb_cost)}      # the game sends the box's price
        reply, how = self._sell_pack(params, packs.Pack(f"{box.name}: {deal.name}", ()), deal.wb_cost, "war bonds",
                                     PROMO_GOLD, with_data=True, open_cards=pull, n=len(deal.min_rarities))
        return reply, how + self._box_progress(box)

    def _box_progress(self, box) -> str:
        player = self.player()
        state = (player.boxes if player is not None else {}).get(str(box.id)) or boxes.new_state()
        limit = f" of {box.max_completes}" if box.max_completes is not None else ""
        where = f", tier {boxes.tier(box, state) + 1} of {len(box.tiers)}" if box.tiered else ""
        return (f"; {box.name} round {int(state.get('completes', 0)) + 1}{limit}{where}: "
                f"{boxes.bought(box, state)}/{box.size} bought")

    # ---- store upgrades --------------------------------------------------------------------------

    def _decks_limit(self) -> int:
        return int(self._start.get("max_decks_available") or 5)

    def _buy_store_upgrade(self, message: str, params: dict) -> tuple[dict, str]:
        """A store upgrade (store.py): buyStorePromoTokens / buyStorePromoGold
        item_type=4 item_id (+50 Max Energy, +10 Card Slots), or buyUpgrade upgrade_id
        (the Extra Deck Slot)."""
        player = self.player()
        uid = str(params.get("upgrade_id") if message == BUY_UPGRADE else params.get("item_id"))
        up = store.UPGRADES.get(uid)
        currency = "gold" if message == PROMO_GOLD else "wb"
        if up is None or (message != BUY_UPGRADE and up.currency != currency):
            return self._refuse(params, "That upgrade isn't for sale."), f"refused {message}: upgrade {uid!r}"
        try:
            price = store.buy_upgrade(player, up, params.get("expected_cost"), self._decks_limit())
        except store.StoreError as e:
            reply = self._refuse(params, str(e))
            reply["user_data"] = self.current_user_data(params)
            return reply, f"refused {up.name}: {e}"
        self.player_store.save(player)
        if message == BUY_UPGRADE:
            reply = self.envelope(params)
            reply["result"] = True
            reply.update(self._player_parts(params, ("user_data", "user_decks")))
            reply["store_upgrades"] = copy.deepcopy(self._start.get("store_upgrades") or {})
            reply["max_decks"] = player.max_decks
            store.apply_upgrades(reply, player, self._decks_limit())
        else:
            recorded = [b for (m, _), bodies in self.replays.items() if m == PROMO_GOLD for b in bodies]
            plain = [b for b in recorded if "user_decks" not in b] or recorded
            reply = self._prepare(plain[-1], params) if plain else self.envelope(params)
            reply["result"] = True
            for k in ("new_cards", "new_cards_data"):
                reply.pop(k, None)
            if up.kind == "energy":
                reply["max_energy"] = player.max_energy    # the client takes the cap from here
            reply.update(self._player_parts(params, ("user_data",)))
        unit = "war bonds" if up.currency == "wb" else "gold"
        now = {"energy": f"max energy {player.max_energy}", "decks": f"{player.max_decks} deck slots"}.get(
            up.kind, f"{player.max_cards} card slots")
        return reply, f"bought {up.name} for {price} {unit}; {now}"
