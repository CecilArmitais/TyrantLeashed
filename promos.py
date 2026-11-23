"""Store promos beyond plain mini packs: single cards with rising prices, and the
real-money bundles sold for gold or war bonds instead. docs/protocol.md, "Packs", has
the evidence.

- Single cards (`[store] promos` entries `{ card = N }`): one copy of the card for war
  bonds, dearer with each purchase by the player. The prices are the pack file's
  <scaling_pack> table for the card's rarity (epic 10, 15, 20, 25, then +5; legendary
  30-60, +10; vindicator 50-110, +20; mythic 80-170, +30). The recorded store sold
  Tabitha's Trooper (a mythic, 70584) this way at 80 war bonds, as entry 13835 with
  `type` 13, the card and its `scaling_pricing`; it keeps that id, and other cards get
  CARD_ITEM_BASE + the card id (ours: how the real server numbered them isn't known). The single-card
  mini packs (type 13, `{ mini_pack = 1001 }`) carry their own scaling_pricing.
- Bundles (`{ bundle = N, gold = X }` or `war_bonds = X`): the four recorded real-money
  bundles, with what their descriptions say they hold. The game bought them through
  Kongregate's payment API (`kongregate.mtx.purchaseItems`, then `verifyPurchase`),
  which doesn't exist offline, so they're sold like any promo instead, for whatever the
  content file asks (ours). Their entries keep `item_type` 7 but leave out `web_bundle`,
  so the client buys them with buyStorePromoGold / buyStorePromoTokens. The one-time ones
  ("for one time only") can be bought once per player.
"""

from __future__ import annotations

from dataclasses import dataclass

import packs

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
