"""Permanent upgrades: +50 Max Energy and +10 Card Slots in the store, and the deck
screen's Extra Deck Slot. docs/protocol.md, "Store upgrades", has the evidence.

- The store sells them as store_promos with item_type 4: "+50 Max Energy" (item 1, 250
  war bonds), "+10 Card Slots" (item 351, 10 war bonds; item 300, 1000 gold). The game
  buys them with buyStorePromoTokens / buyStorePromoGold item_type=4 (its code:
  StoreManager.BuyPromoItem).
- The Extra Deck Slot (store_upgrades["2"], id 101, 100 war bonds) is bought from the
  deck screen with buyUpgrade upgrade_id (BuyDeckSlotPopup); the reply is read for
  user_data and store_upgrades["2"]. max_decks is 1, and max_decks_available 5.
- The prices after the first are the fan wiki's (Store page): +50 Max Energy 250, 350,
  500 (three times); +10 Card Slots 10 war bonds, then 5 more each time; or 1000 gold,
  5000, then 5000 more each time, six times at most. The deck slot stays at 100 (ours),
  up to max_decks_available.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Upgrade:
    item_id: str
    kind: str             # what the save counts: energy, slots_wb, slots_gold, decks
    name: str
    currency: str         # "wb" or "gold"
    value: int            # +50 energy, +10 slots, +1 deck
    prices: tuple = ()    # by purchase; empty: first + step * n
    first: int = 0
    step: int = 0
    limit: int | None = None

    def price(self, bought: int) -> int | None:
        """The price of the next one, or None when no more can be bought."""
        if self.limit is not None and bought >= self.limit:
            return None
        if self.prices:
            return self.prices[bought] if bought < len(self.prices) else None
        return self.first + self.step * bought


UPGRADES = {
    "1": Upgrade("1", "energy", "+50 Max Energy", "wb", 50, prices=(250, 350, 500)),
    "351": Upgrade("351", "slots_wb", "+10 Card Slots", "wb", 10, first=10, step=5),
    "300": Upgrade("300", "slots_gold", "+10 Card Slots", "gold", 10, prices=(1000, 5000, 10000, 15000, 20000, 25000)),
    "101": Upgrade("101", "decks", "Extra Deck Slot", "wb", 1, first=100, step=0),
}
STORE_TYPE = {"energy": "1", "decks": "2", "slots_wb": "5", "slots_gold": "5"}     # store_upgrades' keys


class UpgradeError(Exception):
    pass


def buy(player, upgrade: Upgrade, expected_cost, decks_limit: int) -> int:
    """Buys one: the price paid. Raises UpgradeError with words for the player."""
    bought = player.upgrades.get(upgrade.kind, 0)
    price = upgrade.price(bought)
    if upgrade.kind == "decks" and (player.max_decks or 1) >= decks_limit:
        price = None
    if price is None:
        raise UpgradeError(f"You already have every {upgrade.name} upgrade.")
    if expected_cost not in (None, "") and str(expected_cost).isdigit() and int(expected_cost) != price:
        raise UpgradeError(f"The price has changed: {upgrade.name} costs {price} "
                           f"{'war bonds' if upgrade.currency == 'wb' else 'gold'} now.")
    have = player.war_bonds if upgrade.currency == "wb" else player.gold
    if have < price:
        unit = "war bonds" if upgrade.currency == "wb" else "gold"
        raise UpgradeError(f"Not enough {unit}: {upgrade.name} costs {price}, and you have {have}.")
    if upgrade.currency == "wb":
        player.war_bonds -= price
    else:
        player.gold -= price
    player.upgrades[upgrade.kind] = bought + 1
    if upgrade.kind == "energy":
        player.max_energy += upgrade.value              # the cap only (ours: no energy now)
    elif upgrade.kind in ("slots_wb", "slots_gold"):
        player.max_cards = (player.max_cards or 1600) + upgrade.value
    else:
        player.max_decks = (player.max_decks or 1) + upgrade.value
    return price


def apply_to_store(body: dict, player, decks_limit: int) -> dict:
    """The store's upgrade offers at the player's next prices: the item_type 4 promos
    and store_upgrades. One that can't be bought any more leaves the store."""
    def next_price(up: Upgrade):
        price = up.price(player.upgrades.get(up.kind, 0))
        if up.kind == "decks" and (player.max_decks or 1) >= decks_limit:
            return None
        return price

    if isinstance(body.get("store_promos"), list):
        kept = []
        for p in body["store_promos"]:
            up = UPGRADES.get(str(p.get("item_id"))) if str(p.get("item_type")) == "4" else None
            if up is None:
                kept.append(p)
                continue
            price = next_price(up)
            if price is None:
                continue
            p = dict(p)
            p["wb_cost" if up.currency == "wb" else "gold_cost"] = str(price)
            kept.append(p)
        body["store_promos"] = kept
    su = body.get("store_upgrades")
    if isinstance(su, dict):
        for up in UPGRADES.values():
            entry = su.get(STORE_TYPE[up.kind])
            if not isinstance(entry, dict) or not isinstance(entry.get(up.currency), dict) \
                    or str(entry[up.currency].get("id")) != up.item_id:
                continue
            price = next_price(up)
            if price is None:
                del entry[up.currency]
            else:
                entry[up.currency] = {**entry[up.currency], f"{up.currency}_cost": str(price)}
        for key, entry in list(su.items()):
            if entry == {}:
                su[key] = []
    return body
