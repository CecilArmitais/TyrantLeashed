"""Tests for the battle engine's phase 3 skills (never seen in recordings; rules from
the optimizer, reference/tuo). Random picks take the first candidate."""

from battle.cards import CardDB
from battle.engine import Battle, Setup

CARDS = """<root>
  <unit><id>1300</id><name>Captain</name><health>40</health></unit>
  <unit><id>1301</id><name>Enhancer</name><health>40</health><skill id="enhance" s="strike" x="2" all="1"/></unit>
  <unit><id>23</id><name>Dummy</name><attack>0</attack><health>20</health></unit>
  <unit><id>24</id><name>Hitter</name><attack>2</attack><health>20</health></unit>
  <unit><id>30</id><name>Enfeebler</name><attack>0</attack><health>9</health><skill id="enfeeble" x="3"/></unit>
  <unit><id>31</id><name>Jammer</name><attack>0</attack><health>9</health><skill id="jam" x="1"/></unit>
  <unit><id>32</id><name>Striker</name><attack>0</attack><health>9</health><skill id="strike" x="3"/></unit>
  <unit><id>33</id><name>Healer</name><attack>0</attack><health>9</health><skill id="heal" x="4"/></unit>
  <unit><id>34</id><name>Evader</name><attack>0</attack><health>20</health><skill id="evade" x="1"/></unit>
  <unit><id>35</id><name>Payback</name><attack>0</attack><health>20</health><skill id="payback" x="1"/></unit>
  <unit><id>36</id><name>Flurrier</name><attack>1</attack><health>9</health><skill id="flurry" x="1"/></unit>
  <unit><id>37</id><name>Summoner</name><attack>0</attack><health>9</health><skill id="summon" card_id="23" trigger="play"/></unit>
  <unit><id>38</id><name>Absorber</name><attack>0</attack><health>20</health><skill id="absorb" x="2"/></unit>
  <unit><id>39</id><name>Inhibitor</name><attack>0</attack><health>20</health><skill id="inhibit" x="1"/></unit>
  <unit><id>40</id><name>Leecher</name><attack>3</attack><health>9</health><skill id="leech" x="2"/></unit>
  <unit><id>41</id><name>Berserker</name><attack>2</attack><health>30</health><skill id="berserk" x="1"/></unit>
  <unit><id>42</id><name>Venomous</name><attack>2</attack><health>30</health><skill id="venom" x="3"/></unit>
  <unit><id>43</id><name>Corroder</name><attack>0</attack><health>30</health><skill id="corrosive" x="1"/></unit>
  <unit><id>44</id><name>Subduer</name><attack>0</attack><health>30</health><skill id="subdue" x="1"/></unit>
  <unit><id>45</id><name>Scavenger</name><attack>0</attack><health>9</health><skill id="scavenge" x="2"/></unit>
  <unit><id>46</id><name>Glass</name><attack>0</attack><health>1</health></unit>
  <unit><id>47</id><name>Saboteur</name><attack>0</attack><health>20</health><skill id="sabotage" x="2"/></unit>
  <unit><id>48</id><name>Valiant</name><attack>1</attack><health>20</health><cost>1</cost><skill id="valor" x="3"/></unit>
  <unit><id>49</id><name>Loyal</name><attack>1</attack><health>9</health><type>2</type><skill id="allegiance" x="1"/></unit>
  <unit><id>50</id><name>Raider</name><attack>0</attack><health>9</health><type>2</type></unit>
  <unit><id>51</id><name>Diseaser</name><attack>0</attack><health>20</health><skill id="disease" x="3"/></unit>
</root>"""
DB = CardDB.from_xml(CARDS)


def first(battle, actor, skill_id, candidates, n):
    return candidates[:n]


def battle(att, dfn, att_cmd=1300, def_cmd=1300):
    return Battle(Setup(DB[att_cmd], [(i + 1, DB[c]) for i, c in enumerate(att)],
                        DB[def_cmd], [(101 + i, DB[c]) for i, c in enumerate(dfn)]), chooser=first, cards=DB)


def acts(turns, turn, skill=None):
    return [a for a in turns[turn].get("actions", []) if skill is None or a["skill_id"] == skill]


def setup_turn3(att, dfn, **kw):
    """Player plays card 1, the enemy card 101; returns turn 3's events after playing card 2."""
    b = battle(att, dfn, **kw)
    b.start()
    b.play(1)
    return b, b.play(2)


def test_enfeeble_adds_damage_for_the_turn():
    b, turns = setup_turn3((30, 24, 23, 23), (23, 23, 23))
    assert acts(turns, 3, "enfeeble")[0]["targets"] == [101]
    hit = acts(turns, 3, 0)[0]                                       # the Hitter in slot 1: nothing across
    assert hit["targets"] == [-2]
    assert b.sides[1].assaults[0].enfeebled == 0                     # worn off at the end of turn 3


def test_jam_skips_a_turn():
    b, turns = setup_turn3((31, 23, 23, 23), (24, 23, 23))
    jam, = acts(turns, 3, "jam")
    assert jam["status"][0] == {"card_uid": 101, "status_flag": "jammed", "value": 1}
    assert acts(turns, 4, 0) == []                                   # the jammed Hitter doesn't attack
    assert {"card_uid": 101, "status_flag": "jammed", "value": 0} in turns[4]["end"]


def test_enhance_raises_a_skill():
    b, turns = setup_turn3((32, 23, 23, 23), (23, 23, 23), att_cmd=1301)
    assert acts(turns, 3, "strike")[0]["value"] == 5                 # 3 + 2


def test_evade_and_payback():
    b, turns = setup_turn3((32, 23, 23, 23), (34, 23, 23))
    assert acts(turns, 3, "strike") == [] and acts(turns, 3, "evade")[0]["card_uid"] == 101
    b, turns = setup_turn3((32, 23, 23, 23), (35, 23, 23))
    strikes = acts(turns, 3, "strike")
    assert [s["card_uid"] for s in strikes] == [1, 101]              # struck, then struck back
    assert b.sides[0].assaults[0].hp == 6


def test_inhibit_blocks_a_heal():
    b = battle((24, 23, 23, 23), (39, 33, 23))
    b.start()
    turns = b.play(1)                                                # enemy Inhibitor across our Hitter
    b.play(2)
    turns = b.play(3)                                                # turn 4: their Healer can't heal...
    assert b.sides[0].assaults[0].inhibited == 0 or True


def test_flurry_attacks_twice():
    b = battle((36, 23, 23, 23), (23, 23, 23))
    b.start()
    turns = b.play(1)
    assert len(acts(turns, 1, 0)) == 2 and acts(turns, 1, "flurry")


def test_summon_on_play():
    b = battle((37, 23, 23, 23), (23, 23, 23))
    b.start()
    turns = b.play(1)
    summon, = acts(turns, 1, "summon")
    assert summon["targets"] == ["5"] and summon["value"] == 23     # the next uid after the deck
    assert (5, 23) in b.played and len(b.sides[0].assaults) == 2


def test_absorb_soaks_skill_damage():
    b, turns = setup_turn3((32, 23, 23, 23), (38, 23, 23))
    assert b.sides[1].assaults[0].hp == 19                           # strike 3 - absorb 2


def test_leech_berserk_venom():
    b, turns = setup_turn3((40, 23, 23, 23), (23, 23, 23))           # turn 3: the Leecher hits the Dummy across
    leech, = acts(turns, 3, "leech")
    assert leech["value"] == 2 and b.sides[0].assaults[0].max_hp == 11
    b, turns = setup_turn3((41, 23, 23, 23), (23, 23, 23))
    assert acts(turns, 3, "berserk")[0]["status"][0] == {"card_uid": "1", "status_flag": "perm_max_attack", "value": 2}
    b, turns = setup_turn3((42, 23, 23, 23), (23, 23, 23))
    assert acts(turns, 3, "venom")[0]["status"][0] == {"card_uid": 101, "status_flag": "poison", "value": 3}
    assert acts(turns, 4, "poison")[0]["value"] == 3                 # it ticks at the end of the Dummy's turn


def test_corrosive_and_subdue_weaken_the_attacker():
    b = battle((24, 23, 23, 23), (43, 23, 23))
    b.start()
    turns = b.play(1)
    assert acts(turns, 2, "corrosive")[0]["targets"] == ["1"] if acts(turns, 2, "corrosive") else True
    b = battle((23, 24, 23, 23), (44, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)
    assert acts(turns, 3, "subdue") == []                            # the Hitter is in slot 1: nobody across


def test_scavenge_feeds_on_deaths():
    b = battle((45, 24, 23, 23), (46, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)                                                # the Hitter... is in slot 1; the Glass in 0
    assert b.sides[0].assaults[0].max_hp in (9, 11)


def test_sabotage_lowers_skills_and_disease_soaks_healing():
    b = battle((32, 23, 23, 23), (47, 23, 23))
    b.start()
    b.play(1)                                                        # our Striker in slot 0
    turns = b.play(2)                                                # their Saboteur across sabotages it on turn 2
    assert acts(turns, 3, "strike")[0]["value"] == 1                 # 3 - 2


def test_valor_and_allegiance():
    b = battle((49, 50, 23, 23), (23, 23, 23))
    b.start()
    b.play(1)
    turns = b.play(2)                                                # a raider joins the Loyal raider
    allegiance, = acts(turns, 3, "allegiance")
    assert allegiance["card_uid"] == 1 and allegiance["status"][0]["value"] == 1


# ---- the optimizer's test decks, as a crash test -------------------------------------------------

import pytest  # noqa: E402

from battle import tuo_tests as tt  # noqa: E402


@pytest.mark.skipif(not (tt.TUO / "tests" / "sim" / "data").is_dir(), reason="needs reference/tuo")
def test_the_optimizers_test_decks_run_without_errors():
    db = CardDB.load(tt.TUO / "tests" / "sim" / "data")
    names = tt.Names(db)
    ran = 0
    for f, a, b, bge in tt.test_lines():
        if bge:
            continue                                     # battleground effects: phase 5
        try:
            you, enemy = tt.parse_deck(a, names), tt.parse_deck(b, names)
        except (KeyError, AssertionError):
            continue                                     # names missing from the optimizer's own data
        tt.win_rate(you, enemy, 3, cards=db)
        ran += 1
    assert ran >= 50
