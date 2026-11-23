"""The store as the game gets it: what state/content.toml puts on offer (content.py), in
the recorded store's shape, and the permanent upgrades. game_api/store.py answers the
store's messages with this; packs.py and boxes.py open what's bought. docs/protocol.md
("Packs", "Store upgrades") has the evidence.

- **The offers** (apply_offers): the store's replies (store_packs, store_promos,
  store_banner) start from the recorded ones. The content file's packs, mini pack
  promos, single cards, bundles and event boxes go in; a recorded promo we sell is
  replaced by ours, and the other recorded ones stay if recorded_extras is on.
- **Single cards** (`[store] promos` entries `{ card = N }`): one copy of the card for
  war bonds, dearer with each purchase by the player. The prices are the pack file's
  <scaling_pack> table for the card's rarity (epic 10, 15, 20, 25, then +5; legendary
  30-60, +10; vindicator 50-110, +20; mythic 80-170, +30). The recorded store sold
  Tabitha's Trooper (a mythic, 70584) this way at 80 war bonds, as entry 13835 with
  `type` 13, the card and its `scaling_pricing`; it keeps that id, and other cards get
  CARD_ITEM_BASE + the card id (ours: how the real server numbered them isn't known).
  The single-card mini packs (type 13, `{ mini_pack = 1001 }`) carry their own
  scaling_pricing.
- **Bundles** (`{ bundle = N, gold = X }` or `war_bonds = X`): the four recorded
  real-money bundles, with what their descriptions say they hold. The game bought them
  through Kongregate's payment API (`kongregate.mtx.purchaseItems`, then
  `verifyPurchase`), which doesn't exist offline, so they're sold like any promo
  instead, for whatever the content file asks (ours). Their entries keep `item_type` 7
  but leave out `web_bundle`, so the client buys them with buyStorePromoGold /
  buyStorePromoTokens. The one-time ones ("for one time only") can be bought once per
  player.
- **Upgrades** (apply_upgrades, buy_upgrade): the store sells "+50 Max Energy" (item 1,
  250 war bonds) and "+10 Card Slots" (item 351, 10 war bonds; item 300, 1000 gold) as
  store_promos with item_type 4, bought with buyStorePromoTokens / buyStorePromoGold
  item_type=4 (the game's code: StoreManager.BuyPromoItem). The Extra Deck Slot
  (store_upgrades["2"], id 101, 100 war bonds) is bought from the deck screen with
  buyUpgrade upgrade_id (BuyDeckSlotPopup); the reply is read for user_data and
  store_upgrades["2"]. max_decks is 1, and max_decks_available 5. The prices after the
  first are the fan wiki's (Store page): +50 Max Energy 250, 350, 500 (three times);
  +10 Card Slots 10 war bonds, then 5 more each time; or 1000 gold, 5000, then 5000
  more each time, six times at most. The deck slot stays at 100 (ours), up to
  max_decks_available.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass

import packs
from content import PACK_IDS, Content, Promo

BOX_DAYS_SHOWN = 7                              # the store shows a box with this long left (ours)
BOX_ITEM_TYPE = 8                               # store_promos item_type the game shows as an event box
TIERED_ITEM_TYPE = 10                           # ...and as a tiered box (a row of tiers that open in turn)
BOX_BANNER_TEXT = "New Event Box!"              # the banner's ribbon for a box (ours; "" shows "0")


# ---- the offers: packs, promos and boxes ----------------------------------------------------------

def promo_entry(mp_id, mp: dict, promo: Promo, available_cards: list[int] | None) -> dict:
    """A store_promos entry for a mini pack, in the recorded entries' shape (item_type 3)."""
    entry = {"id": str(mp_id), "name": mp["name"], "type": mp.get("type", ""), "desc": mp.get("desc", "")}
    for k in ("picture", "banner_picture", "icon"):
        if mp.get(k):
            entry[k] = mp[k]
    if promo.gold is not None:
        entry["gold_cost"] = str(promo.gold)
    else:
        entry["wb_cost"] = str(promo.war_bonds)
    entry.update({"item_type": 3, "item_id": int(mp_id), "sub_item_id": "gold" if promo.gold is not None else "wb"})
    if available_cards:
        entry["available_cards"] = [str(c) for c in available_cards]
    return entry


def box_entry(box, state: dict, now: int, next_deal=None) -> dict:
    """A store_promos entry for an event box, in the shape the game reads (found in its
    code, ServicePromoItemData/ServiceGachaData, and checked in the game): item_type 8
    shows the event box screen; `card` lists the box's cards with their copies, and
    `cards_received` the copies bought this round, by card id, as strings (the game
    reads a number there as 0); `box_discount` is the deal on offer. Buying sends
    buyStorePromoTokens item_type=8 (with box_discount_id for the deal)."""
    entry = _box_base(box, now, BOX_ITEM_TYPE)
    got = state.get("received") or {}
    entry["card"] = [{"id": str(cid), "count": n} for cid, n in box.cards]
    entry["cards_received"] = {str(cid): str(int(got[str(cid)])) for cid, _ in box.cards if got.get(str(cid))}
    if next_deal is not None:
        entry["box_discount"] = {"box_discount_id": next_deal.id, "name": next_deal.name,
                                 "desc": next_deal.desc, "wb_cost": str(next_deal.wb_cost)}
    return entry


def tiered_box_entry(box, state: dict, now: int, open_tier: int) -> dict:
    """A store_promos entry for a tiered box, in the shape the game reads (its code,
    ServiceMultiGachaData; checked in the game): item_type 10 shows the tiered box
    screen; `subbox` lists the tiers, each {"card": [{"id", "count"}]}, and
    `cards_received` the copies bought, by tier number (from 1) and card id, as strings.
    The game opens the tier after the last one bought out, so the finished tiers are
    sent full. `cards_received` must be there (the game reads it unchecked). Buying
    sends buyStorePromoTokens item_type=10."""
    entry = _box_base(box, now, TIERED_ITEM_TYPE)
    got = state.get("received") or {}
    entry["subbox"] = [{"card": [{"id": str(cid), "count": n} for cid, n in t]} for t in box.tiers]
    received = {str(i + 1): {str(cid): str(n) for cid, n in t} for i, t in enumerate(box.tiers[:open_tier])}
    current = {str(cid): str(int(got[str(cid)])) for cid, _ in box.tiers[open_tier] if got.get(str(cid))}
    if current:
        received[str(open_tier + 1)] = current
    entry["cards_received"] = received
    return entry


def _box_base(box, now: int, item_type: int) -> dict:
    """What every box entry has: the name, price, time left (always 7 days) and pictures."""
    entry = {"item_type": item_type, "item_id": box.id, "id": box.id, "name": box.name, "desc": box.desc,
             "wb_cost": str(box.wb_cost), "sub_item_id": "wb", "picture": "tyrantpack",
             "start_time": now - 3600, "end_time": now + BOX_DAYS_SHOWN * 86400}
    for src, key in (("store_banner", "icon"), ("banner_picture", "banner_picture"),
                     ("anim_picture", "anim_picture"), ("web_banner", "web_banner")):
        if box.picture(src):
            entry[key] = box.picture(src)
    return entry


def apply_offers(body: dict, content: Content, promo_entries: list[dict], box_entries: list[dict],
                   mini_pack_ids: set[str]) -> dict:
    """The store parts of a reply (store_packs, store_promos, store_banner), in place, from
    the content file. Recorded mini pack promos are replaced by the content file's; the
    other recorded items stay if recorded_extras is on."""
    if isinstance(body.get("store_packs"), dict):
        keep = {PACK_IDS[p] for p in content.packs}
        body["store_packs"] = {k: v for k, v in body["store_packs"].items() if k in keep}
    if isinstance(body.get("store_promos"), list):
        # The recorded order, with each recorded promo we sell replaced by ours (a mini
        # pack's by its id; a single card's, like Tabitha's Trooper, by its card; a
        # bundle's by its id), and the recorded mini packs we don't sell dropped; new
        # promos follow the last one, boxes come first.
        fresh = {str(e["item_id"]): e for e in promo_entries}
        by_card = {str(e["card"]): str(e["item_id"]) for e in promo_entries if str(e.get("type")) == "13"}
        promos, last = [], 0
        for p in body["store_promos"]:
            if not isinstance(p, dict):
                continue
            key, kind = str(p.get("item_id")), str(p.get("item_type"))
            if kind == "3" and str(p.get("type")) == "13" and str(p.get("card")) in by_card:
                key = by_card[str(p.get("card"))]
            if key in fresh and kind == str(fresh[key]["item_type"]):
                promos.append(fresh.pop(key))
                last = len(promos)
            elif kind == "3" and key in mini_pack_ids:
                continue
            elif kind == "7" and key in {str(x.bundle) for x in content.promos if x.bundle is not None}:
                continue                                      # ours, bought once: gone for good
            elif content.recorded_extras:
                promos.append(p)
        rest = [e for e in promo_entries if str(e["item_id"]) in fresh]
        body["store_promos"] = box_entries + promos[:last] + rest + promos[last:]
        banner = body.get("store_banner")
        offered = {str(p.get("item_id")) for p in body["store_promos"]}
        lead = (box_entries + promo_entries + [None])[0]
        if box_entries or not isinstance(banner, dict) or str(banner.get("promo_id")) not in offered:
            if lead is None:
                body.pop("store_banner", None)
            else:
                body["store_banner"] = {"name": lead["name"], "picture": lead.get("icon", ""),
                                        "promo_id": lead["item_id"],
                                        "discount": BOX_BANNER_TEXT if lead in box_entries
                                        else (banner or {}).get("discount", ""),
                                        "end_time": lead.get("end_time", 1)}
    return body


# ---- single cards and bundles ---------------------------------------------------------------------

CARD_ITEM_BASE = 1_000_000          # a single-card promo's item_id: this + the card id (ours)...
RECORDED_IDS = {70584: 13835}       # ...or the recorded store's id for it (Tabitha's Trooper)
BUNDLE_ITEM_TYPE = 7                # store_promos item_type of the real-money bundles


@dataclass(frozen=True)
class Bundle:
    id: int
    name: str
    desc: str
    web_picture: str
    banner_picture: str = ""
    war_bonds: int = 0
    card: int = 0                   # a card it gives (the Platinum Crate's "exclusive new Epic": Kronus)
    sp: int = 0
    max_energy: int = 0             # store-upgrade-like gains
    card_slots: int = 0
    max_stamina: int = 0
    max_salvage: int = 0
    once: bool = False              # "for one time only"


# The recorded store's bundles (item_type 7), their contents from their descriptions.
BUNDLES = {b.id: b for b in (
    Bundle(191, "Platinum Crate", "250 War Bonds, exclusive new Epic card and a 50 SP bonus!",
           "graphic_newbuyerpackage2.png", "platinumcrate_halfsize_banner.png", war_bonds=250, card=224, sp=50),
    Bundle(200080, "Elite Pack", "Includes 100 War Bonds, +50 Max energy, +150 inventory slots, +10 Max "
           "stamina, and +350 Max SP for one time only.", "elite_pack_bundle.png", war_bonds=100, max_energy=50,
           card_slots=150, max_stamina=10, max_salvage=350, once=True),
    Bundle(201607, "New Buyer Surge", "400 War Bonds for one time only. New Buyer Special!",
           "wb_package_centered2.png", "genericpack_halfsize_banner.png", war_bonds=400, once=True),
    Bundle(202122, "550 WB Surge Pack", "550 War Bonds for one-time only.", "wb_package_centered2.png",
           "genericpack_halfsize_banner.png", war_bonds=550, once=True),
)}


def card_key(card: int) -> str:
    return f"card:{card}"


def pack_key(mini_pack: int) -> str:
    return f"pack:{mini_pack}"


def bundle_key(bundle: int) -> str:
    return f"bundle:{bundle}"


def card_entry(card, scaling: dict, bought: int) -> dict:
    """A store_promos entry for one copy of a card, in the recorded Tabitha's Trooper's
    shape (item_type 3, type 13), its price the player's next one."""
    cid = card.id
    item_id = RECORDED_IDS.get(cid, CARD_ITEM_BASE + cid)
    return {"id": item_id, "type": 13, "card": str(cid), "name": card.name,
            "desc": f"Includes a single copy of the {card.name} card. Price increases with each purchase.",
            "web_picture": "card_package_d.png", "banner_picture": "genericpack_halfsize_banner.png",
            "picture": "tyrantpack", "icon": "banner_graphic_plaguewyrm", "scaling_pricing": dict(scaling),
            "item_type": 3, "item_id": item_id, "sub_item_id": "wb",
            "wb_cost": str(packs.scaling_price(scaling, bought))}


def bundle_entry(bundle: Bundle, gold: int | None, war_bonds: int | None) -> dict:
    """A store_promos entry for a bundle, in the recorded ones' shape but without
    web_bundle (so it's bought like any promo) and with its price."""
    entry = {"name": bundle.name, "desc": bundle.desc, "item_type": BUNDLE_ITEM_TYPE, "item_id": bundle.id,
             "web_picture": bundle.web_picture}
    if bundle.banner_picture:
        entry["banner_picture"] = bundle.banner_picture
    if bundle.card:
        entry["card"] = bundle.card
    if gold is not None:
        entry.update(gold_cost=str(gold), sub_item_id="gold")
    else:
        entry.update(wb_cost=str(war_bonds), sub_item_id="wb")
    return entry


# ---- upgrades: +50 Max Energy, +10 Card Slots, the Extra Deck Slot --------------------------------

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


class StoreError(Exception):
    """Why the store can't sell something, in words the player can read."""


def buy_upgrade(player, upgrade: Upgrade, expected_cost, decks_limit: int) -> int:
    """Buys one: the price paid. Raises StoreError with words for the player."""
    bought = player.upgrades.get(upgrade.kind, 0)
    price = upgrade.price(bought)
    if upgrade.kind == "decks" and (player.max_decks or 1) >= decks_limit:
        price = None
    if price is None:
        raise StoreError(f"You already have every {upgrade.name} upgrade.")
    if expected_cost not in (None, "") and str(expected_cost).isdigit() and int(expected_cost) != price:
        raise StoreError(f"The price has changed: {upgrade.name} costs {price} "
                           f"{'war bonds' if upgrade.currency == 'wb' else 'gold'} now.")
    have = player.war_bonds if upgrade.currency == "wb" else player.gold
    if have < price:
        unit = "war bonds" if upgrade.currency == "wb" else "gold"
        raise StoreError(f"Not enough {unit}: {upgrade.name} costs {price}, and you have {have}.")
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


def apply_upgrades(body: dict, player, decks_limit: int) -> dict:
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
