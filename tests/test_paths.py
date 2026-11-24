"""Tests for where things are (common/paths.py): the folders are found from the server
folder, and the state folder is the one paths.STATE_DIR names when it's needed, so the
tests' temporary one (conftest.py) reaches the API and player.py."""

import importlib.util
import sys

import pytest

from common import paths
from game_api import GameApi
from tools import player as cli


def test_the_folders_are_found_from_the_server_folder():
    """paths.py is in server/common/, two levels below the server folder. A wrong count
    would make the game files look missing, and every test that needs them would skip."""
    assert (paths.SERVER_DIR / "run.py").is_file() and paths.ROOT_DIR == paths.SERVER_DIR.parent
    assert (paths.WEB_DIR / "shim.js").is_file() and (paths.WEB_DIR / "signin.html").is_file()
    assert importlib.util.find_spec("paths") is None     # one copy only, common.paths, which conftest patches


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
