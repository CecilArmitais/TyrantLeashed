"""The offline server's game API: answers api.php messages.

Everything comes from fixtures built by make_fixtures.py (recorded responses with
every account identifier replaced):

  getUserAccount, init   the recorded login of a new account
  startMission/playCard  recorded battles, replayed as scripts: a mission starts the
                         next recorded battle of that mission, and each card played
                         must be the card the recording played at that point. Starting
                         a mission costs its energy (from the mission file). With
                         --engine, missions are real battles instead (battle/).
                         Either way, a finished mission pays its rewards into the save:
                         gold, its card on the first win, stars (rewards.py).
  setDeckCards           real logic: the deck is saved (cards you own, up to 10)
  forfeitBattle          real logic: Surrender; the battle is lost and paid as a loss
  getHuntingTargets,     with --engine, real logic: the arena's AI rivals, fights for
  startHuntingBattle,    stamina, rating and level-ups, and the refresh button
  buyRivalsRefresh       (arena.py)
  buyStaminaRefillTokens real logic: stamina up to the max, for war bonds
  unlockCommander        real logic: Promote, a commander and bonus cards for a
                         promotion point
  getBattleReports       real logic: the Reports tab, the player's recent arena fights
                         and other players' fights against their defense deck
  startRevengeBattle     with --engine, real logic: a report's Revenge button, a fight
                         against the attacker's defense deck (arena.py)
  buyPackGold,           real logic: the Basic Pack, the Epic Pack, the gold Basic Pack
  buyPackTokens,         bundles and base-set faction packs (the Blight Pack), with the
  buyStorePromoGold,     game's published drop rates (packs.py), and event boxes
  buyStorePromoTokens    (boxes.py) and their deals; other promos are replayed. What the
                         store offers comes from state/content.toml (content.py),
                         in the store's replies (store.py)
  upgradeCard,           real logic: the Upgrade screen. Upgrades cost salvage points
  salvageCard,           (SP), salvage gives them, locks keep cards from salvage, and
  salvageL1CommonCards,  fusion turns a recipe's cards into the fused card, with the
  salvageL1RareCards,    game's own numbers (upgrades.py)
  setCardLock,
  fuseCard
  completeAchievement    real logic: achievements (achievements.py). The permanent ones
                         finish by themselves and pay at once (completed_achievements in
                         that reply); daily battle objectives count arena fights and are
                         claimed with this message
  useDailyBonus          real logic: the home screen crate (daily_crate.py; its rewards
                         are set in content.toml)
  buyUpgrade, store      real logic: +50 Max Energy, +10 Card Slots (store promos of
  promos item_type 4     item_type 4) and the deck screen's Extra Deck Slot
                         (store.py)
  setUserName,           real logic: the player's name, active deck and defence deck
  setActiveDeck,
  setDefenseDeck
  getUserData,           real logic: the save's user_data, cards and decks; card_data
  getUserCards,          is empty, as recorded; getVersionNumber is the envelope
  getUserDecks,
  getCardData,
  getVersionNumber
  createFaction, joinFaction, leaveFaction, acceptFactionInvite, sendFactionInvite,
  kickFactionMember, setMemberRank, setAllFactionSettings, sendFactionMessage,
  updateFaction, getRecruitingFactions, getTopFactions, getUsersLFG, getProfileData,
  donateFactionGold, buyFactionCard, removeFactionCard, updateFactionWar,
  fightPracticeBattle    (a guild member's deck, for nothing)
                         real logic: guilds (guilds.py), kept by the server in
                         state/guilds/, apart from the player's save
  getRaidInfo,           real logic: raids (raids.py), set in content.toml: each guild's
  getCurrentRaids,       run of the boss (levels, health), each member's raid energy,
  fightRaidBattle,       fights resolved by the battle engine in one reply, the reward
  claimRaidReward,       tiers; a guild is needed
  refillEventBattleEnergy,
  getRaidLeaderboard,
  getPreviousRaidTopLeaderboard
  buyEnergyRefillTokens  real logic: max energy on top of the current energy, for
                         wb_energy_cost (30) war bonds
  setUserFlag            real logic: the flag is saved (tutorial progress, guides seen,
                         battle speed, autopilot, music...), so it survives a reload
  other messages         replayed when the client asks exactly what was recorded
                         (same message and same details, e.g. buyPackGold pack_id=1)
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
calls; the feature's rules are the top-level module, store.py, wars.py, ...); base.py,
messages.py and replay.py hold what they share.
"""

from __future__ import annotations

# GameApi, and the few names the tools and tests use; the rest stays in its own file
# (game_api.messages, game_api.base...).
from .base import NOT_LOGGED_IN, OFFLINE_NAME, OFFLINE_PASSWORD, OFFLINE_SYNCODE, OFFLINE_USER_ID  # noqa: F401
from .core import GameApi  # noqa: F401
from .replay import NOT_REPLAYED, VOLATILE_PARAMS, encode, personalize, shift_times  # noqa: F401
