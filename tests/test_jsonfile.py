"""Tests for the state files' reading and writing (jsonfile.py)."""

import json
import os

import pytest

from common import jsonfile


def test_written_data_reads_back(tmp_path):
    f = tmp_path / "state" / "guilds" / "1.json"            # the folders are made
    jsonfile.write(f, {"name": "Bats", "members": {"1000001": 3}})
    assert jsonfile.read(f) == {"name": "Bats", "members": {"1000001": 3}}
    assert [p.name for p in f.parent.iterdir()] == ["1.json"]   # no .part left behind


def test_a_missing_file_is_the_default(tmp_path):
    assert jsonfile.read(tmp_path / "none.json") is None
    assert jsonfile.read(tmp_path / "none.json", {"accounts": {}}) == {"accounts": {}}


def test_non_ascii_is_kept_readable(tmp_path):
    f = tmp_path / "p.json"
    jsonfile.write(f, {"name": "Zoë ☆"})
    assert "Zoë ☆" in f.read_text("utf-8")
    assert jsonfile.read(f) == {"name": "Zoë ☆"}


def test_sorted_keys_when_asked(tmp_path):
    f = tmp_path / "p.json"
    jsonfile.write(f, {"b": 1, "a": 2}, sort_keys=True)
    assert list(json.loads(f.read_text("utf-8"))) == ["a", "b"]
    jsonfile.write(f, {"b": 1, "a": 2})
    assert list(json.loads(f.read_text("utf-8"))) == ["b", "a"]


def test_a_failed_write_keeps_the_old_file(tmp_path):
    f = tmp_path / "p.json"
    jsonfile.write(f, {"gold": 100})
    with pytest.raises(TypeError):
        jsonfile.write(f, {"gold": object()})                # can't be written as JSON
    assert jsonfile.read(f) == {"gold": 100}
    assert [p.name for p in tmp_path.iterdir()] == ["p.json"]


def test_a_broken_file_raises(tmp_path):
    f = tmp_path / "p.json"
    f.write_text("{half", "utf-8")
    with pytest.raises(ValueError):
        jsonfile.read(f, {})


def test_a_swap_windows_refuses_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    f = tmp_path / "p.json"
    jsonfile.write(f, {"gold": 100})
    real, refusals, waits = os.replace, [2], []

    def held_open(src, dst):                         # a virus scanner has the file for a moment
        if refusals[0]:
            refusals[0] -= 1
            raise PermissionError(13, "Access is denied")
        real(src, dst)
    monkeypatch.setattr(jsonfile.os, "replace", held_open)
    monkeypatch.setattr(jsonfile, "_pause", waits.append)
    jsonfile.write(f, {"gold": 200})
    assert jsonfile.read(f) == {"gold": 200} and waits == [0.02, 0.04]


def test_a_swap_that_stays_refused_raises_and_keeps_the_old_file(tmp_path, monkeypatch):
    f = tmp_path / "p.json"
    jsonfile.write(f, {"gold": 100})
    tries = []

    def refused(src, dst):
        tries.append(dst)
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(jsonfile.os, "replace", refused)
    monkeypatch.setattr(jsonfile, "_pause", lambda s: None)
    with pytest.raises(PermissionError):
        jsonfile.write(f, {"gold": 200})
    assert len(tries) == jsonfile.SWAP_TRIES and jsonfile.read(f) == {"gold": 100}
