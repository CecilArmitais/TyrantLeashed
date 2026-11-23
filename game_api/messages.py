"""The client's message names the API answers, and the groups the dispatcher routes."""

from __future__ import annotations


LOGIN = ("getUserAccount", "init")
SET_FLAG = "setUserFlag"
ENERGY_REFILL = "buyEnergyRefillTokens"
SET_DECK = "setDeckCards"
FORFEIT = "forfeitBattle"           # the Surrender button
TARGETS = "getHuntingTargets"       # the arena's rivals list
RIVALS_REFRESH = "buyRivalsRefresh"  # the refresh button on the Rivals screen
STAMINA_REFILL = "buyStaminaRefillTokens"   # the + by stamina ("attacks")
UNLOCK = "unlockCommander"          # Promote
REPORTS = "getBattleReports"        # the Reports tab
PACK_GOLD = "buyPackGold"           # the store's Basic Pack (pack_id 1)
PACK_WB = "buyPackTokens"           # the store's Epic Pack (pack_id 1000), for war bonds
PROMO_GOLD = "buyStorePromoGold"    # store promos for gold: mini packs (the Basic Pack bundles), bundles,
                                    # +10 Card Slots (item 300)
PROMO_WB = "buyStorePromoTokens"    # store promos for war bonds: mini packs, single cards, bundles, boxes,
                                    # +50 Max Energy and +10 Card Slots (items 1, 351)
UPGRADE = "upgradeCard"             # the Upgrade screen: a card one level up, for SP
SALVAGE = "salvageCard"             # ...one card for SP
SALVAGE_ALL = {"salvageL1CommonCards": 1, "salvageL1RareCards": 2}   # every spare level-1 common / rare
LOCK = "setCardLock"                # the salvage lock on a card
FUSE = "fuseCard"                   # a fusion recipe's cards into the fused card
CLAIM = "completeAchievement"       # the Complete button of a finished daily objective
CRATE = "useDailyBonus"             # the crate on the home screen
PRACTICE = "fightPracticeBattle"    # a guild member profile's Battle / Surge buttons
GUILD_MESSAGES = {"createFaction", "joinFaction", "acceptFactionInvite", "leaveFaction", "sendFactionInvite",
                  "kickFactionMember", "setMemberRank", "setAllFactionSettings", "sendFactionMessage", "updateFaction",
                  "getFactionNews", "getRecruitingFactions", "getTopFactions", "getUsersLFG", "getProfileData",
                  "donateFactionGold", "buyFactionCard", "removeFactionCard", "updateFactionWar"}
CONQUEST_MESSAGES = {"getConquestUpdate", "fightConquestBattle", "claimConquestReward", "setConquestMarker",
                     "getConquestLeaderboard", "getConquestTopLeaderboard", "getGuildInfluenceLeaderboard",
                     "getConquestZoneRankings", "getConquestZoneTopLeaderboard", "getGuildZoneInfluenceLeaderboard",
                     "getPreviousConquestTopLeaderboard"}
BRAWL_MESSAGES = {"fightBrawlBattle", "claimBrawlRewards", "getBrawlLeaderboard", "getBrawlMemberLeaderboard",
                  "getPreviousBrawlTopLeaderboard"}
WAR_MESSAGES = {"fightFactionWar", "startFactionWarBattle", "claimFactionWarRewards", "getFactionWarLeaderboard",
                "getFactionWarMatchLeaderboard", "getPreviousFactionWarTopLeaderboard"}
ITEM_MESSAGES = {"getUserItems", "consumeItem"}
RAID_MESSAGES = {"getRaidInfo", "getCurrentRaids", "fightRaidBattle", "claimRaidReward", "refillEventBattleEnergy",
                 "getRaidLeaderboard", "getPreviousRaidTopLeaderboard"}
BUY_UPGRADE = "buyUpgrade"          # the deck screen's Extra Deck Slot
STORE_MESSAGES = (PACK_GOLD, PACK_WB, PROMO_GOLD, PROMO_WB, BUY_UPGRADE)   # the store's (StoreCalls)
SET_NAME = "setUserName"
SET_ACTIVE, SET_DEFENSE = "setActiveDeck", "setDefenseDeck"
REFRESH = {"getUserData": ("user_data",), "getUserCards": ("user_data", "user_cards"),
           "getUserDecks": ("user_decks",), "getCardData": ("card_data",), "getVersionNumber": ()}


ARENA_START = "startHuntingBattle"
REVENGE_START = "startRevengeBattle"    # a report's Revenge button: target_user_id, reported_time


BATTLE_STARTS = frozenset({"startMission", "startHuntingBattle", "startRevengeBattle"})  # replies with a new battle_data
BATTLE_TURN = "playCard"
