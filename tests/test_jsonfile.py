"""Tests for the state files' reading and writing (jsonfile.py)."""

import json

import pytest

import jsonfile


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
