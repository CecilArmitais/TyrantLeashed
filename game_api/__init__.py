"""The offline server's game API: answers api.php messages.

Replies start from fixtures built by make_fixtures.py (recorded responses with every
account identifier replaced). The messages below are answered by the server's own rules;
anything else is replayed from the recordings:

  getUserAccount, init   the recorded login. With several players (run.py's default),
                         getUserAccount logs in the account the browser signed in as on
                         the server's page (account_calls.py), giving the browser its own
                         credentials for it (accounts.py, session_calls.py)
  startMission/playCard  recorded battles, replayed as scripts: a mission starts the
                         next recorded battle of that mission, and each card played
                         must be the card the recording played at that point. Starting
                         a mission costs its energy (from the mission file). With
                         --engine, missions are real battles instead (battle/).
                         Either way, a finished mission pays its rewards into the save:
                         gold, its card on the first win, stars (rewards.py).
  setDeckCards           real logic: the deck is saved (cards you own, up to 10)
  forfeitBattle          real logic: Surrender; the battle is lost and paid as a loss
  getHuntingTargets,     with --engine, real logic: the arena's AI rivals and other
  startHuntingBattle,    players, fights for stamina, rating and level-ups, and the
  buyRivalsRefresh       refresh button (arena.py)
  buyStaminaRefillTokens real logic: stamina up to the max, for war bonds
  unlockCommander        real logic: Promote, a commander and bonus cards for a
                         promotion point
  getBattleReports       real logic: the Reports tab, the player's recent arena fights
                         and other players' fights against their defense deck
  startRevengeBattle     with --engine, real logic: a report's Revenge button, a fight
                         against the attacker's defense deck (arena.py)
  buyPackGold,           real logic: the store as state/content.toml sets it (store.py,
  buyPackTokens,         packs.py, boxes.py): the Basic and Epic Pack tiles, mini pack
  buyStorePromoGold,     promos (the Basic Pack bundles, faction packs such as the Blight
  buyStorePromoTokens    Pack, any mini pack that opens), single cards at rising prices,
                         the recorded real-money bundles sold for gold or war bonds, and
                         event boxes and their deals, with the game's drop rates; a promo
                         the server doesn't sell is replayed
  buyUpgrade, store      real logic: +50 Max Energy, +10 Card Slots (store promos of
  promos item_type 4     item_type 4) and the deck screen's Extra Deck Slot (store.py)
  upgradeCard,           real logic: the Upgrade screen. Upgrades cost salvage points
  salvageCard,           (SP), salvage gives them, locks keep cards from salvage, and
  salvageL1CommonCards,  fusion turns a recipe's cards into the fused card, with the
  salvageL1RareCards,    game's own numbers (upgrades.py)
  setCardLock,
  fuseCard
  completeAchievement    real logic: objectives (achievements.py). The permanent ones
                         finish by themselves and pay at once (completed_achievements in
                         that reply); daily objectives, and the PvP event objectives
                         (content.toml's challenge among them), are claimed with this
  useDailyBonus          real logic: the home screen crate (daily_crate.py; its rewards
                         are set in content.toml)
  setUserName,           real logic: the player's name, active deck and defence deck
  setActiveDeck,
  setDefenseDeck
  getUserData,           real logic: the save's user_data, cards and decks; card_data
  getUserCards,          is empty, as recorded; getVersionNumber is the envelope
  getUserDecks,
  getCardData,
  getVersionNumber
  getUserItems,          real logic: the inventory, and using an item: a pack, SP or an
  consumeItem            energy refill (items.py)
  buyEnergyRefillTokens  real logic: max energy on top of the current energy, with an
                         Energy Refill item if the player has one, else for
                         wb_energy_cost (30) war bonds
  setUserFlag            real logic: the flag is saved (tutorial progress, guides seen,
                         battle speed, autopilot, music...), so it survives a reload
  createFaction, joinFaction, leaveFaction, acceptFactionInvite, sendFactionInvite,
  kickFactionMember, setMemberRank, setAllFactionSettings, sendFactionMessage,
  updateFaction, getFactionNews, getRecruitingFactions, getTopFactions, getUsersLFG,
  getProfileData, donateFactionGold, buyFactionCard, removeFactionCard, updateFactionWar
                         real logic: guilds (guilds.py), kept by the server in
                         state/guilds/, apart from the players' saves
  fightPracticeBattle    real logic: a guild member profile's Battle / Surge buttons, an
                         engine fight against the member's deck, for nothing
  getRaidInfo,           real logic: raids (raids.py), set in content.toml: each guild's
  getCurrentRaids,       run of the boss (levels, health), each member's raid energy,
  fightRaidBattle,       fights resolved by the battle engine in one reply, the reward
  claimRaidReward,       tiers; a guild is needed
  refillEventBattleEnergy,
  getRaidLeaderboard,
  getPreviousRaidTopLeaderboard
  fightFactionWar, startFactionWarBattle, claimFactionWarRewards,
  getFactionWarLeaderboard, getFactionWarMatchLeaderboard,
  getPreviousFactionWarTopLeaderboard
                         real logic: guild wars (wars.py), set in content.toml: guilds
                         queue and are matched, members attack the other guild's
                         fortresses, and the event's reward tiers are claimed
  fightBrawlBattle, claimBrawlRewards, getBrawlLeaderboard, getBrawlMemberLeaderboard,
  getPreviousBrawlTopLeaderboard
                         real logic: brawls (brawls.py), set in content.toml: fights
                         against other players' defense decks for points, brawl energy,
                         and reward tiers by rank
  getConquestUpdate, fightConquestBattle, setConquestMarker, claimConquestReward,
  getConquestLeaderboard, getConquestTopLeaderboard, getConquestZoneRankings,
  getConquestZoneTopLeaderboard, getGuildInfluenceLeaderboard,
  getGuildZoneInfluenceLeaderboard, getPreviousConquestTopLeaderboard
                         real logic: conquest (conquest.py), set in content.toml: guilds
                         fight in a map's zones for influence, scoring rounds rank them,
                         and the zones give fortresses and the reward tiers
  other messages         replayed when the client asks exactly what was recorded
                         (same message and same details, e.g. getStoreData)
  recordEvent            analytics; answered with an empty reply, like the real server

Every reply gets its timestamps moved forward so the recording looks current, and
a fresh `request` echo. Where a reply carries the player's data, the saved card
inventory, gold, war bonds, energy, flags and mission progress (player_state.py)
replace the recorded ones.
Anything else gets the real server's error shape,
{"result": false, "result_message": [...]}, which the game shows in a dialog.

Standard library only.

The package: core.py holds GameApi and the dispatcher, joining one part per feature, each
an XCalls class in x_calls.py (session_calls.py, store_calls.py, war_calls.py, ...: the
calls; the feature's rules are in rules/, store.py, wars.py, ...); base.py,
messages.py and replay.py hold what they share.
"""

from __future__ import annotations

# GameApi, and the few names the tools and tests use; the rest stays in its own file
# (game_api.messages, game_api.base...).
from .base import (NOT_LOGGED_IN, NOT_SIGNED_IN, OFFLINE_NAME, OFFLINE_PASSWORD, OFFLINE_SYNCODE,  # noqa: F401
                   OFFLINE_USER_ID)
from .core import GameApi  # noqa: F401
from .replay import NOT_REPLAYED, VOLATILE_PARAMS, encode, personalize, shift_times  # noqa: F401
