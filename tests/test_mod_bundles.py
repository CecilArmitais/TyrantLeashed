"""Tests for mod bundles (rules/mod_bundles.py): the list in game/mods/bundles.json, the
login's asset_bundles and bundle_index, and the web server sending the files."""

import http.client
import json
import threading

import pytest

from rules import mod_bundles
from rules.mod_bundles import ModBundleError
from web import offline_server as srv

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def make_mods(root, bundles, files=None):
    """mods/bundles.json listing `bundles`, and each listed file (or `files`) with some bytes."""
    folder = root / "mods" / "bundles"
    folder.mkdir(parents=True, exist_ok=True)
    for name in files if files is not None else [b["file"] for b in bundles]:
        (folder / name).write_bytes(b"UnityFS fake " + name.encode())
    (root / "mods" / "bundles.json").write_text(json.dumps({"bundles": bundles}), "utf-8")
    return root / "mods"


def test_no_list_no_bundles(tmp_path):
    assert mod_bundles.load(tmp_path / "mods") == []


def test_the_list_is_read(tmp_path):
    mods = make_mods(tmp_path, [{"file": "icons1_Unity5_4_2_WebGL.unity3d", "sprites": ["shock16", "shock32"]}])
    b, = mod_bundles.load(mods)
    assert b.file == "icons1_Unity5_4_2_WebGL.unity3d" and b.sprites == ("shock16", "shock32")
    assert isinstance(b.version, int) and b.version > 0


def test_the_version_follows_the_contents(tmp_path):
    mods = make_mods(tmp_path, [{"file": "a.unity3d", "sprites": ["x16"]}])
    first = mod_bundles.load(mods)[0].version
    assert mod_bundles.load(mods)[0].version == first                      # the same file: the same
    (mods / "bundles" / "a.unity3d").write_bytes(b"UnityFS changed")
    assert mod_bundles.load(mods)[0].version != first                      # so the game fetches it again


@pytest.mark.parametrize("bundles, files, message", [
    ([{"file": "../a.unity3d", "sprites": ["x16"]}], [], "plain file name"),
    ([{"file": "a.png", "sprites": ["x16"]}], ["a.png"], "plain file name"),
    ([{"file": "a.unity3d", "sprites": ["x16"]}], [], "doesn't exist"),
    ([{"file": "a.unity3d", "sprites": ["Shock16"]}], None, "lower case"),
    ([{"file": "a.unity3d", "sprites": []}], None, "lower case"),
    ([{"file": "a.unity3d", "sprites": ["x16"]}, {"file": "a.unity3d", "sprites": ["y16"]}], None, "listed twice"),
    ([{"file": "a.unity3d", "sprites": ["x16"]}, {"file": "b.unity3d", "sprites": ["x16"]}], None, "also in"),
])
def test_a_broken_list_says_why(tmp_path, bundles, files, message):
    mods = make_mods(tmp_path, bundles, files)
    with pytest.raises(ModBundleError, match=message):
        mod_bundles.load(mods)


def test_unreadable_list(tmp_path):
    (tmp_path / "mods").mkdir()
    (tmp_path / "mods" / "bundles.json").write_text("{nope", "utf-8")
    with pytest.raises(ModBundleError, match="JSON"):
        mod_bundles.load(tmp_path / "mods")


def test_the_login_gets_the_bundles_and_their_sprites():
    b1 = mod_bundles.ModBundle("icons1.unity3d", 77, ("shock16", "shock32"))
    b2 = mod_bundles.ModBundle("icons2.unity3d", 5, ("repair16",))
    body = {"asset_bundles": {"1": {"id": 1, "version": "1", "preload": 0, "bundle_name": "5_4_2/maps.unity3d"},
                              "3000": {"id": 3000, "version": "2", "preload": 0, "bundle_name": "5_4_2/x.unity3d"}}}
    mod_bundles.apply_to_login(body, [b1, b2])
    assert body["asset_bundles"]["3001"] == {"id": 3001, "version": "77", "preload": 0,
                                             "bundle_name": "mods/icons1.unity3d"}
    assert body["asset_bundles"]["3002"]["bundle_name"] == "mods/icons2.unity3d"
    assert body["bundle_index"] == {"": [{"name": "shock16", "bundle": 3001}, {"name": "shock32", "bundle": 3001},
                                         {"name": "repair16", "bundle": 3002}]}


def test_a_listed_asset_bundles_works_too_and_nothing_changes_without_mods():
    body = {"asset_bundles": [{"id": 4, "version": "1", "preload": 0, "bundle_name": "a"}]}
    mod_bundles.apply_to_login(body, [mod_bundles.ModBundle("m.unity3d", 1, ("z32",))])
    assert body["asset_bundles"][-1]["id"] == 5 and body["bundle_index"] == {"": [{"name": "z32", "bundle": 5}]}
    plain = {"asset_bundles": {"1": {"id": 1}}}
    mod_bundles.apply_to_login(plain, [])
    assert plain == {"asset_bundles": {"1": {"id": 1}}}                    # no bundle_index without mods
    other = {"time": 1}
    mod_bundles.apply_to_login(other, [mod_bundles.ModBundle("m.unity3d", 1, ("z32",))])
    assert other == {"time": 1}                                           # only the login has asset_bundles


# ---- through the server ----------------------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    (client / "index.html").write_text("<html></html>", "utf-8")
    archive = tmp_path / "assets"
    (archive / "cdn.synapsegames.com" / "unleashed" / "asset_bundles" / "5_4_2").mkdir(parents=True)
    fx = tmp_path / "fixtures"
    fx.mkdir()
    init = {"time": 1_795_000_000, "user_data": {"user_id": "1000001", "last_update_time": "1795000000"},
            "asset_bundles": {"1": {"id": 1, "version": "1", "preload": 0, "bundle_name": "5_4_2/maps.unity3d"}}}
    (fx / "init.json").write_text(json.dumps({"source": {}, "body": init}), "utf-8")
    make_mods(tmp_path, [{"file": "icons1_Unity5_4_2_WebGL.unity3d", "sprites": ["shock16", "shock32"]}],
              files=["icons1_Unity5_4_2_WebGL.unity3d", "notes.txt"])
    (tmp_path / "secret.unity3d").write_bytes(b"not a mod")
    logs = []
    s = srv.OfflineServer(("127.0.0.1", 0), client_dir=client, archive_dir=archive, log=logs.append,
                          api=srv.GameApi(fixture_dir=fx))          # its mods: tmp_path/mods, beside the fixtures
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def get(server, path, method="GET", body=None, headers=None):
    host, port = server.server_address[:2]
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def test_the_login_names_the_mod_bundle_and_the_server_sends_it(server):
    status, body = get(server, "/tu/mobile.tyrantonline.com/api.php?message=init&user_id=1000001",
                       method="POST", body=b"password=p&user_id=1000001", headers=FORM)
    r = json.loads(body)
    row = r["asset_bundles"]["2"]
    assert status == 200 and row["bundle_name"] == "mods/icons1_Unity5_4_2_WebGL.unity3d"
    assert r["bundle_index"] == {"": [{"name": "shock16", "bundle": 2}, {"name": "shock32", "bundle": 2}]}
    # the game asks for it at the asset URL + its name
    status, data = get(server, "/tu/cdn.synapsegames.com/unleashed/asset_bundles/" + row["bundle_name"])
    assert status == 200 and data == b"UnityFS fake icons1_Unity5_4_2_WebGL.unity3d"


@pytest.mark.parametrize("name", ["notes.txt", "..%2Fsecret.unity3d", "../secret.unity3d", "missing.unity3d"])
def test_only_bundle_files_in_the_mods_folder_are_sent(server, name):
    status, _ = get(server, "/tu/cdn.synapsegames.com/unleashed/asset_bundles/mods/" + name)
    assert status == 404


def test_a_broken_list_stops_the_start(tmp_path):
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "mods").mkdir()
    (tmp_path / "mods" / "bundles.json").write_text('{"bundles": [{"file": "x.unity3d", "sprites": ["a16"]}]}', "utf-8")
    with pytest.raises(ModBundleError, match="doesn't exist"):
        srv.GameApi(fixture_dir=tmp_path / "fixtures")
