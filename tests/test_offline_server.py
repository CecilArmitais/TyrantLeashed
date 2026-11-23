"""Tests for the offline server (real HTTP on a free local port) and the shim (in Node)."""

import http.client
import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

import offline_server as srv

REAL_INDEX = srv.CLIENT_DIR / "index.html"
PAGE = """<!doctype html>
<html><head>
    <meta charset="utf-8">
    <script src='https://cdn1.kongregate.com/javascripts/kongregate_api.js'></script>
  </head><body><script src="Release/UnityLoader.js"></script></body></html>"""


@pytest.fixture
def server(tmp_path):
    client = tmp_path / "client"
    (client / "Release").mkdir(parents=True)
    (client / "index.html").write_text(PAGE, "utf-8")
    (client / "Release" / "UnityLoader.js").write_bytes(b"var UnityLoader = 1;")
    archive = tmp_path / "assets"
    img = archive / "cdn.synapsegames.com" / "unleashed" / "images"
    img.mkdir(parents=True)
    (img / "a.png").write_bytes(b"\x89PNG fake")
    xml = archive / "mobile.tyrantonline.com" / "assets"
    xml.mkdir(parents=True)
    (xml / "cards_section_1.xml").write_text("<root/>", "utf-8")
    (tmp_path / "secret.txt").write_text("do not serve", "utf-8")

    fx = tmp_path / "fixtures"
    fx.mkdir()
    for name, body in {"getUserAccount": {"time": 1_795_000_000, "result": True, "new_user": 1000001,
                                          "link": "https://example.com/a"},
                       "init": {"time": 1_795_000_000, "version": "80.560", "daily_bonus_time": "1795003600",
                                "user_data": {"user_id": "1000001", "last_update_time": "1795000000"}}}.items():
        (fx / f"{name}.json").write_text(json.dumps({"source": {}, "body": body}), "utf-8")

    logs = []
    s = srv.OfflineServer(("127.0.0.1", 0), client_dir=client, archive_dir=archive, log=logs.append,
                          api=srv.GameApi(fixture_dir=fx))
    s.logs = logs
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def get(server, path, method="GET", body=None, headers=None):
    host, port = server.server_address[:2]
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})   # raw path: no client-side normalising
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, dict(r.getheaders()), data


def test_page_is_edited_and_locked_down(server):
    status, headers, body = get(server, "/")
    html = body.decode()
    assert status == 200
    assert "connect-src 'self'" in headers["Content-Security-Policy"]
    assert "report-uri /__csp" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"
    assert html.index(srv.SHIM_TAG) < html.index("<meta charset")      # shim loads first
    assert "kongregate_api.js'></script>" not in html
    assert "kongregate_api.js removed" in html
    assert 'src="Release/UnityLoader.js"' in html


def test_real_client_page_edit():
    html = srv.build_index(REAL_INDEX.read_text("utf-8"))
    assert html.count(srv.SHIM_TAG) == 1
    assert not __import__("re").search(r"<script[^>]*kongregate_api\.js", html)
    for kept in ("Release/UnityLoader.js", "tyu-web-2_30-80.data", "function UnityProgress"):
        assert kept in html


def test_client_files(server):
    status, headers, body = get(server, "/Release/UnityLoader.js")
    assert (status, body) == (200, b"var UnityLoader = 1;")
    assert headers["Content-Type"] == "application/javascript"
    status, headers, body = get(server, "/Release/UnityLoader.js", method="HEAD")
    assert (status, headers["Content-Length"], body) == (200, "20", b"")


def test_archive_files(server):
    status, headers, body = get(server, "/tu/cdn.synapsegames.com/unleashed/images/a.png?v=123")
    assert (status, body, headers["Content-Type"]) == (200, b"\x89PNG fake", "image/png")
    status, _, body = get(server, "/tu/mobile.tyrantonline.com/assets/cards_section_1.xml?p=999")
    assert (status, body) == (200, b"<root/>")


def test_missing_archive_file_is_reported(server):
    status, _, _ = get(server, "/tu/cdn.synapsegames.com/unleashed/images/nope.png")
    assert status == 404
    assert server.not_found["cdn.synapsegames.com/unleashed/images/nope.png"] == 1
    assert any("NOT IN ARCHIVE" in l for l in server.logs)


@pytest.mark.parametrize("path", [
    "/Release/../../secret.txt",
    "/Release/..%2F..%2Fsecret.txt",
    "/tu-offline/../offline_server.py",
    "/tu/cdn.synapsegames.com/../../secret.txt",
    "/tu/evil.example.com/x.png",
    "/secret.txt",
])
def test_nothing_outside_the_served_folders(server, path):
    status, _, body = get(server, path)
    assert status == 404 and b"do not serve" not in body and b"import" not in body


FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def test_login_calls_are_answered(server):
    status, _, body = get(server, "/tu/mobile.tyrantonline.com/api.php?message=getUserAccount&user_id=",
                          method="POST", body=b"udid=x&syncode=&user_id=", headers=FORM)
    r = json.loads(body)
    assert status == 200 and r["result"] is True and r["new_user"] == 1000001
    assert r["request"]["udid"] == "x"
    assert b'": ' not in body and b'", "' not in body            # compact, like the real server
    assert b'"https:\\/\\/example.com\\/a"' in body                # slashes escaped, like PHP
    status, _, body = get(server, "/tu/mobile.tyrantonline.com/api.php?message=init&user_id=1000001",
                          method="POST", body=b"password=p&user_id=1000001", headers=FORM)
    r = json.loads(body)
    assert r["user_data"]["user_id"] == "1000001"
    assert abs(r["time"] - __import__("time").time()) < 5                     # shifted to now
    assert int(r["user_data"]["last_update_time"]) == r["time"]
    assert server.answered == {"getUserAccount": 1, "init": 1}
    assert "API messages answered: getUserAccount x1, init x1" in server.summary()


def test_other_messages_are_unsupported_with_the_real_error_shape(server):
    status, _, body = get(server, "/tu/mobile.tyrantonline.com/api.php?message=getGuildWarInfo&user_id=1",
                          method="POST", body=b"war_id=1", headers=FORM)
    r = json.loads(body)
    assert status == 200 and r["result"] is False
    assert r["result_message"] == ["Offline server: 'getGuildWarInfo' is not supported yet."]
    assert "time" in r and "daily_bonus_time" in r
    status, _, body = get(server, "/tu/mobile.tyrantonline.com/api.php?message=startMission&user_id=1",
                          method="POST", body=b"mission_id=1", headers=FORM)
    assert "mission 1 hasn't been recorded" in json.loads(body)["result_message"][0]
    get(server, "/tu/mobile.tyrantonline.com/api.php", method="POST", body=b"message=recordEvent&user_id=1", headers=FORM)
    assert server.unsupported == {"getGuildWarInfo": 1, "startMission": 1}
    assert server.answered == {"recordEvent": 1}
    assert any("API startMission: UNSUPPORTED: mission 1 hasn't been recorded" in l for l in server.logs)


def test_blocked_requests_are_reported(server):
    report = {"csp-report": {"blocked-uri": "https://connect.facebook.net/sdk.js", "violated-directive": "script-src"}}
    status, _, _ = get(server, "/__csp", method="POST", body=json.dumps(report).encode(),
                       headers={"Content-Type": "application/csp-report"})
    assert status == 204
    assert server.blocked[0]["blocked-uri"] == "https://connect.facebook.net/sdk.js"
    assert "connect.facebook.net" in server.summary()


def test_favicon_is_not_a_problem(server):
    status, _, _ = get(server, "/favicon.ico")
    assert status == 204 and not server.not_found


def test_dropped_connections_are_quiet(server, capfd):
    import socket
    import time
    host, port = server.server_address[:2]
    for _ in range(3):  # open, send half a request, then reset the connection
        s = socket.create_connection((host, port))
        s.sendall(b"GET /Release/UnityLoader.js HTTP/1.1\r\nHost: x\r\n")
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        s.close()
    status, _, _ = get(server, "/Release/UnityLoader.js")   # still serving
    assert status == 200
    time.sleep(0.3)  # let the handler threads finish
    assert not [l for l in server.logs if "ERROR" in l]
    assert "Traceback" not in capfd.readouterr().err        # the stock server would print one per reset


def test_summary_when_clean(server):
    get(server, "/")
    assert "Nothing was blocked" in server.summary()


# ---- shim (runs in Node) ----------------------------------------------------------------

SHIM_TEST = r"""
globalThis.window = globalThis;
class FakeXHR { open(method, url) { this.url = url; } }
globalThis.XMLHttpRequest = FakeXHR;
require(process.argv[2]);  // argv: [node, this test, shim.js]
const cases = {
  "https://mobile.tyrantonline.com/api.php?message=init&user_id=1": "/tu/mobile.tyrantonline.com/api.php?message=init&user_id=1",
  "http://mobile.tyrantonline.com/assets/cards_section_1.xml?p=123": "/tu/mobile.tyrantonline.com/assets/cards_section_1.xml?p=123",
  "https://mobile-staging.tyrantonline.com/api.php?message=init": "/tu/mobile.tyrantonline.com/api.php?message=init",
  "https://mobile-dev.tyrantonline.com/api.php": "/tu/mobile.tyrantonline.com/api.php",
  "https://cdn.synapsegames.com/unleashed/asset_bundles/5_4_2/maps7_Unity5_4_2_WebGL.unity3d": "/tu/cdn.synapsegames.com/unleashed/asset_bundles/5_4_2/maps7_Unity5_4_2_WebGL.unity3d",
  "//cdn.synapsegames.com/unleashed/images/a.png": "/tu/cdn.synapsegames.com/unleashed/images/a.png",
  "HTTPS://CDN.SYNAPSEGAMES.COM/x.png": "/tu/cdn.synapsegames.com/x.png",
  "Release/tyu-web-2_30-80.data": "Release/tyu-web-2_30-80.data",
  "https://graph.facebook.com/me": "https://graph.facebook.com/me",
  "https://cdn.synapsegames.com.evil.net/x": "https://cdn.synapsegames.com.evil.net/x",
  "https://evil.net/?u=https://mobile.tyrantonline.com/": "https://evil.net/?u=https://mobile.tyrantonline.com/",
};
const out = {};
for (const [input, expected] of Object.entries(cases)) {
  const x = new XMLHttpRequest();
  x.open("GET", input);
  out[input] = [x.url, expected];
}
out.__rewritten = window.__tuOffline.rewritten;
console.log(JSON.stringify(out));
"""


def test_shim_rewrites_only_game_addresses(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    test = tmp_path / "shim_test.js"
    test.write_text(SHIM_TEST, "utf-8")
    r = subprocess.run([node, str(test), str(srv.WEB_DIR / "shim.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    rewritten = out.pop("__rewritten")
    for url, (got, expected) in out.items():
        assert got == expected, url
    assert rewritten == 7
