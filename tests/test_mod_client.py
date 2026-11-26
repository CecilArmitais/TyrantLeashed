"""Tests for a host's modded client (web/mod_client.py): files served in place of the web
client's own, and only as the set they were built as, for the client they were built from."""

import hashlib
import http.client
import json
import socket
import threading

import pytest

from web import mod_client
from web import offline_server as srv
from web.mod_client import ModClientError

DATA, LOADER = "Release/game.data", "Release/UnityLoader.js"


def make_client(root):
    client = root / "client"
    (client / "Release").mkdir(parents=True)
    (client / "index.html").write_text("<html></html>", "utf-8")
    (client / DATA).write_bytes(b"original package")
    (client / LOADER).write_bytes(b"var loader = 'original';")
    (client / "Release" / "game.js").write_bytes(b"the code")
    return client


def make_mods(root, client, files=(DATA, LOADER), built_from=(DATA, LOADER), write=(DATA, LOADER)):
    folder = root / "mods" / "client"
    (folder / "Release").mkdir(parents=True)
    for rel in write:
        (folder / rel).write_bytes(b"modded " + rel.encode())
    marks = {rel: {"size": len((client / rel).read_bytes()),
                   "sha256": hashlib.sha256((client / rel).read_bytes()).hexdigest()} for rel in built_from}
    (folder / "built_from.json").write_text(json.dumps({"client": "test", "built_from": marks, "files": list(files)}),
                                            "utf-8")
    return root / "mods"


def test_no_build_nothing_replaced(tmp_path):
    client = make_client(tmp_path)
    assert mod_client.load(tmp_path / "mods", client) == {}
    (tmp_path / "mods" / "client" / "Release").mkdir(parents=True)       # files without the manifest: not a build
    (tmp_path / "mods" / "client" / DATA).write_bytes(b"half a build")
    assert mod_client.load(tmp_path / "mods", client) == {}


def test_a_build_replaces_its_files(tmp_path):
    client = make_client(tmp_path)
    mods = make_mods(tmp_path, client)
    assert mod_client.load(mods, client) == {DATA: mods / "client" / DATA, LOADER: mods / "client" / LOADER}


def test_a_set_with_a_file_missing_is_refused(tmp_path):
    client = make_client(tmp_path)
    mods = make_mods(tmp_path, client, write=(DATA,))
    with pytest.raises(ModClientError, match="UnityLoader.js is missing"):
        mod_client.load(mods, client)


def test_a_build_for_another_client_is_refused(tmp_path):
    client = make_client(tmp_path)
    mods = make_mods(tmp_path, client)
    (client / DATA).write_bytes(b"another package")                         # the same size, other bytes
    with pytest.raises(ModClientError, match="rebuild it for this client"):
        mod_client.load(mods, client)
    (client / DATA).write_bytes(b"a package of another size")
    with pytest.raises(ModClientError, match="rebuild it for this client"):
        mod_client.load(mods, client)


@pytest.mark.parametrize("manifest, message", [
    ("{not json", "not readable JSON"),
    ('{"files": [], "built_from": {"Release/x": {}}}', 'expected "files"'),
    ('{"files": ["../secret.txt"], "built_from": {"Release/x": {}}}', 'expected "files"'),
    ('{"files": ["Release/x"]}', 'expected "files"'),
])
def test_a_broken_manifest_says_why(tmp_path, manifest, message):
    client = make_client(tmp_path)
    (tmp_path / "mods" / "client").mkdir(parents=True)
    (tmp_path / "mods" / "client" / "built_from.json").write_text(manifest, "utf-8")
    with pytest.raises(ModClientError, match=message):
        mod_client.load(tmp_path / "mods", client)


# ---- through the server ----------------------------------------------------------------------

def start(tmp_path, client):
    fx = tmp_path / "fixtures"
    fx.mkdir(exist_ok=True)
    s = srv.OfflineServer(("127.0.0.1", 0), client_dir=client, archive_dir=tmp_path / "assets", log=lambda _: None,
                          api=srv.GameApi(fixture_dir=fx))               # its mods: tmp_path/mods
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def get(server, path, headers=None):
    host, port = server.server_address[:2]
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, dict(r.getheaders()), data


@pytest.fixture
def modded(tmp_path):
    client = make_client(tmp_path)
    make_mods(tmp_path, client)
    s = start(tmp_path, client)
    yield s
    s.shutdown()
    s.server_close()


def test_the_server_sends_the_modded_files_and_the_rest_of_the_client(modded):
    assert get(modded, "/" + DATA)[2] == b"modded " + DATA.encode()
    assert get(modded, "/" + LOADER)[2] == b"modded " + LOADER.encode()
    assert get(modded, "/Release/game.js")[2] == b"the code"                # not replaced: the client's own


def test_browsers_check_back_and_get_a_304_for_what_they_have(modded):
    status, headers, _ = get(modded, "/" + DATA)
    assert status == 200 and headers["Cache-Control"] == "no-cache" and headers["ETag"].startswith('"')
    status, headers2, body = get(modded, "/" + DATA, {"If-None-Match": headers["ETag"]})
    assert status == 304 and body == b"" and headers2["ETag"] == headers["ETag"]
    status, _, body = get(modded, "/" + DATA, {"If-None-Match": '"something-else"'})
    assert status == 200 and body.startswith(b"modded")


def test_without_mods_the_client_is_its_own_and_still_checked_back(tmp_path):
    s = start(tmp_path, make_client(tmp_path))
    try:
        status, headers, body = get(s, "/" + DATA)
        assert status == 200 and body == b"original package" and headers["Cache-Control"] == "no-cache"
        assert s.client_files == {}
    finally:
        s.shutdown()
        s.server_close()


def test_a_build_for_another_client_stops_the_start_and_frees_the_port(tmp_path):
    client = make_client(tmp_path)
    make_mods(tmp_path, client)
    (client / DATA).write_bytes(b"a newer client's package")
    (tmp_path / "fixtures").mkdir()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    with pytest.raises(ModClientError):
        srv.OfflineServer(("127.0.0.1", port), client_dir=client, archive_dir=tmp_path / "assets",
                          log=lambda _: None, api=srv.GameApi(fixture_dir=tmp_path / "fixtures"))
    again = socket.socket()
    again.bind(("127.0.0.1", port))                                          # refused before the port was taken
    again.close()
