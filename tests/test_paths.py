"""Tests for where things are (paths.py): the state folder is the one paths.STATE_DIR names
when it's needed, so the tests' temporary one (conftest.py) reaches the API and player.py."""

import sys

import pytest

import paths
import player as cli
from game_api import GameApi


def test_the_game_fixtures_save_in_the_state_folder(tmp_path):
    assert paths.STATE_DIR != paths.ROOT_DIR / "state"                     # conftest's temporary one
    assert paths.state_dir_for(paths.FIXTURE_DIR) == paths.STATE_DIR
    assert paths.state_dir_for(tmp_path / "fixtures") == tmp_path / "state"   # a made-up game's


def test_the_api_saves_where_paths_says(tmp_path):
    assert GameApi()._state == paths.STATE_DIR
    assert GameApi(fixture_dir=tmp_path / "fixtures")._state == tmp_path / "state"
    assert GameApi(state_dir=tmp_path / "mine")._state == tmp_path / "mine"


def test_player_py_saves_where_paths_says(monkeypatch):
    seen = {}

    class Stop(Exception):
        pass

    def built(**kwargs):
        seen.update(kwargs)
        raise Stop
    monkeypatch.setattr(cli, "GameApi", built)
    monkeypatch.setattr(sys, "argv", ["player.py"])                        # no --state
    with pytest.raises(Stop):
        cli.main()
    assert seen["state_dir"] == paths.STATE_DIR
