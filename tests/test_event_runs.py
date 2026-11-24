"""Tests for what the timed events share (event_runs.py): runs, reward tiers, effects by
day and battle energy. Made-up data only (the events' own tests use the game files)."""

import random
import xml.etree.ElementTree as ET

from rules import event_runs as er

DAY = 86400
RUN = {"event": 1, "start": 1000, "end": 1000 + 3 * DAY, "decay": 1000 + 6 * DAY}


def test_a_run_is_on_then_claimable_then_gone():
    state = {"run": RUN}
    assert er.phase(state, 999, "brawl") is None
    assert er.phase(state, 1000, "brawl") == "brawl"
    assert er.phase(state, RUN["end"], "war") == "war"
    assert er.phase(state, RUN["end"] + 1, "war") == "claim"
    assert er.phase(state, RUN["decay"], "war") == "claim"
    assert er.phase(state, RUN["decay"] + 1, "war") is None
    assert er.phase({"run": None}, 5000, "war") is None


def test_rewards_wait_after_the_end():
    state = {"run": RUN}
    assert not er.claimable(state, RUN["end"] + 599, 600)
    assert er.claimable(state, RUN["end"] + 600, 600)
    assert er.claimable(state, RUN["decay"], 600)
    assert not er.claimable(state, RUN["decay"] + 1, 600)
    assert not er.claimable({}, RUN["end"] + 600, 600)


REWARDS = ET.fromstring("""<rewards>
  <reward><id>1</id><rank>1</rank><min_points> 500 </min_points><card id="7" amount="2"/><item id="3"/></reward>
  <reward><id>2</id><rank>10</rank><mini_pack id="40"/></reward>
  <reward><id>3</id><rank> </rank><pack id="5"/></reward>
</rewards>""")


def test_tiers_from_the_event_file():
    tiers = er.tiers_of(REWARDS)
    assert tiers == [er.Tier(1, 1, 500, (("card", 7, 2), ("item", 3, 1))),
                     er.Tier(2, 10, None, (("mini_pack", 40, 1),)),
                     er.Tier(3, 1, None, (("pack", 5, 1),))]                  # an empty rank is 1
    assert [t.min_points for t in er.tiers_of(REWARDS, 0)] == [500, 0, 0]
    assert er.tiers_of(None) == []


def test_earned_tiers_need_the_rank_and_the_points():
    tiers = er.tiers_of(REWARDS)
    assert [t.id for t in er.earned_tiers(tiers, 1, 500)] == [1, 2, 3]
    assert [t.id for t in er.earned_tiers(tiers, 1, 499)] == [2, 3]
    assert [t.id for t in er.earned_tiers(tiers, 5, 900)] == [2]
    assert er.earned_tiers(tiers, 11, 900) == []
    assert er.earned_tiers(tiers, 0, 900) == []                          # no rank


def test_tier_json_is_all_strings():
    first, second, _ = er.tiers_of(REWARDS)
    assert er.tier_json(first, {}) == {"id": "1", "rank": "1", "min_points": "500",
                                       "card": {"id": "7", "amount": "2"}, "item": {"id": "3", "amount": "1"}}
    packs = {"40": {"name": "Box", "desc": "Cards", "picture": "box.png"}}
    assert er.tier_json(second, packs) == {"id": "2", "rank": "10", "mini_pack": {
        "id": "40", "amount": "1", "name": "Box", "desc": "Cards", "web_picture": "box.png"}}


def test_the_effect_of_the_day():
    config = ET.fromstring(f"""<battleground_config>
      <effect id="9" start_time="{1000 + 2 * DAY}"/><effect id="8"/><effect id="7" start_time="{1000 + DAY + 5}"/>
    </battleground_config>""")
    effects = er.effects_of(config, 1000)
    assert effects == ((0, 8), (1, 7), (2, 9))
    run = {"start": 50_000}                                  # a run's days count from its own start
    assert er.effect_on(effects, run, 50_000) == 8
    assert er.effect_on(effects, run, 50_000 + DAY) == 7
    assert er.effect_on(effects, run, 50_000 + 10 * DAY) == 9
    assert er.effect_on((), run, 50_000) is None
    assert er.effect_on(((1, 7),), run, 50_000) is None    # nothing yet on day 0


ENERGY = er.Energy(most=25, every=1800, stops=6 * 3600)


def test_energy_comes_back_one_at_a_time_until_the_stop():
    row = {"energy": 20, "energy_updated": RUN["start"]}
    assert ENERGY.current(RUN, None, 5000) == 25                         # no row yet: full
    assert ENERGY.current(RUN, row, RUN["start"] + 1799) == 20
    assert ENERGY.current(RUN, row, RUN["start"] + 3 * 1800) == 23
    assert ENERGY.current(RUN, row, RUN["start"] + 100 * 1800) == 25
    row = {"energy": 0, "energy_updated": RUN["end"] - 6 * 3600 - 1800}
    assert ENERGY.current(RUN, row, RUN["end"]) == 1                     # nothing after the stop
    assert ENERGY.recharging(RUN, row, RUN["end"] - 6 * 3600 - 1)
    assert not ENERGY.recharging(RUN, row, RUN["end"] - 6 * 3600)


def test_spending_keeps_the_part_way_progress():
    row = {"energy": 10, "energy_updated": RUN["start"]}
    ENERGY.spend(RUN, row, RUN["start"] + 2 * 1800 + 900)               # 2 back, half way to a 3rd
    assert row == {"energy": 11, "energy_updated": RUN["start"] + 2 * 1800}
    assert ENERGY.current(RUN, row, RUN["start"] + 3 * 1800) == 12
    full = {"energy": 25, "energy_updated": RUN["start"]}
    ENERGY.spend(RUN, full, RUN["start"] + 5000)                         # full: the clock starts now
    assert full == {"energy": 24, "energy_updated": RUN["start"] + 5000}


def test_a_run_and_its_claim_window():
    assert er.new_run(7, 1000, 5000, 3, rounds=0) == {"event": 7, "start": 1000, "end": 5000,
                                                      "decay": 5000 + 3 * DAY, "rounds": 0}


def test_the_state_file_keeps_the_defaults_under_what_it_has(tmp_path):
    store = er.StateStore(tmp_path / "brawls.json", lambda: {"run": None, "players": {}})
    assert store.load() == {"run": None, "players": {}}                      # no file yet
    store.save({"run": RUN})
    assert store.load() == {"run": RUN, "players": {}}


def test_an_opponent_among_the_nearest():
    candidates = [("10", 100), ("9", 120), ("11", 300), ("2", 90)]
    picks = {er.pick_nearest(candidates, 100, random.Random(seed), 2) for seed in range(30)}
    assert picks == {"10", "2"}                                              # 0 and 10 away; 120 is 20
    assert er.pick_nearest([], 100, random.Random(1), 2) is None
    ties = [("10", 110), ("9", 90)]                                          # both 10 away
    assert er.pick_nearest(ties, 100, random.Random(1), 1) == "10"           # "10" < "9" as text
    assert er.pick_nearest(ties, 100, random.Random(1), 1, order=int) == "9"  # 9 < 10 as numbers
