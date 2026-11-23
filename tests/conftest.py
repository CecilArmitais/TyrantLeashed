"""pytest setup: the server's modules import as they do at runtime (the server folder on
the path), and the shared helpers (helpers.py) beside the tests.

Most tests need the game's own files beside the server (../game: the recorded login and
the game data), which aren't in this repo: each player extracts them. Without them, a
test that needs them is skipped (it hit a missing file under game/, or it's marked
needs_game) instead of failing, so a fresh clone still runs the rest."""

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SERVER = TESTS.parent
for folder in (SERVER, TESTS):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import paths  # noqa: E402

GAME_FILES = (paths.FIXTURE_DIR / "init.json").is_file() and paths.ASSET_DIR.is_dir()
SKIP_REASON = f"needs the game files (not found at {paths.GAME_DIR})"


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_game: skipped when the game files aren't beside the server")


def pytest_report_header(config):
    return f"game files: {'found' if GAME_FILES else 'missing, so tests that need them are skipped'} " \
           f"({paths.GAME_DIR})"


@pytest.fixture(autouse=True)
def _never_the_real_state(monkeypatch, tmp_path_factory):
    """A server built without a state folder would use the real one beside the server
    (the player's own saves): every test gets a temporary one instead."""
    monkeypatch.setattr(paths, "STATE_DIR", tmp_path_factory.mktemp("state"))


def pytest_runtest_setup(item):
    if not GAME_FILES and item.get_closest_marker("needs_game"):
        pytest.skip(SKIP_REASON)


def _missing_game_file(e: BaseException) -> bool:
    name = getattr(e, "filename", None)
    if not isinstance(e, FileNotFoundError) or not name:
        return False
    try:
        return Path(name).resolve().is_relative_to(paths.GAME_DIR.resolve())
    except (OSError, ValueError):
        return False


def _skip_if_game_missing(outcome):
    try:
        return (yield)
    except FileNotFoundError as e:
        if not GAME_FILES and _missing_game_file(e):
            pytest.skip(SKIP_REASON)
        raise


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    return (yield from _skip_if_game_missing(None))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    return (yield from _skip_if_game_missing(None))
