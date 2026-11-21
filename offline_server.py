"""Offline Tyrant Unleashed server.

Serves the web client, the archived game files and (from step 4 on) the game's
API from this machine, so the game runs with no connection to Kongregate or
Synapse. Standard library only. Start it with server/run.py.

The game page is the original client page with these changes:
  - Kongregate's API script is removed (a local stand-in comes in step 3)
  - /tu-offline/shim.js loads first; it sends every request for the game's hosts
    (mobile.tyrantonline.com, cdn.synapsegames.com) to this server instead
  - a Content-Security-Policy header limits the page to this server. Anything
    the shim misses is blocked by the browser and reported here, never sent out

URL layout:
  /                                      the edited game page
  /Release/...                           client files (game/client/tyu-web-2.30.80)
  /tu-offline/...                        files from server/web (the shim etc.)
  /tu/<host>/<path>                      archived game files (game/assets/<host>/<path>)
  /tu/mobile.tyrantonline.com/api.php    the game API
  /__csp                                 where the browser reports blocked requests
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from game_api import GameApi  # noqa: E402
from game_api import encode as encode_reply  # noqa: E402
from game_api import GAME_DIR  # noqa: E402
from archive_layout import safe_rel_path  # noqa: E402  (the capture tools' archive layout)

CLIENT_DIR = GAME_DIR / "client" / "tyu-web-2.30.80"
ARCHIVE_DIR = GAME_DIR / "assets"
WEB_DIR = Path(__file__).resolve().parent / "web"
ARCHIVE_HOSTS = {"mobile.tyrantonline.com", "cdn.synapsegames.com"}
API_PATH = "/tu/mobile.tyrantonline.com/api.php"
SHIM_TAG = '<script src="/tu-offline/shim.js"></script>'

# Only this server may be contacted. Unity needs inline scripts, eval (the game runs
# JavaScript strings through it) and blob: URLs (UnityLoader starts the code from one).
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:",
    "worker-src 'self' blob:",
    "connect-src 'self'",
    "img-src 'self' data: blob:",
    "media-src 'self' data: blob:",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self' data:",
    "frame-src 'none'",
    "object-src 'none'",
    "form-action 'none'",
    "report-uri /__csp",
])

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript", ".json": "application/json",
    ".xml": "text/xml; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".ico": "image/x-icon", ".css": "text/css", ".txt": "text/plain; charset=utf-8",
}


def build_index(original: str) -> str:
    """The client's index.html with Kongregate's script removed and the shim loaded first."""
    html = re.sub(r"[ \t]*<script[^>]*kongregate_api\.js[^>]*>\s*</script>[ \t]*",
                  "<!-- kongregate_api.js removed by the offline server -->", original, flags=re.I)
    m = re.search(r"<head\b[^>]*>", html, re.I)
    pos = m.end() if m else 0
    return html[:pos] + "\n    " + SHIM_TAG + html[pos:]


def _inside(base: Path, rel: str) -> Path | None:
    """base/rel, or None if rel tries to leave base."""
    target = (base / rel).resolve()
    return target if target.is_relative_to(base.resolve()) else None


class OfflineServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, client_dir: Path = CLIENT_DIR, archive_dir: Path = ARCHIVE_DIR,
                 web_dir: Path = WEB_DIR, log=print, quiet_static: bool = False, api: GameApi | None = None):
        super().__init__(address, Handler)
        self.client_dir = Path(client_dir)
        self.archive_dir = Path(archive_dir)
        self.web_dir = Path(web_dir)
        self.log = log
        self.quiet_static = quiet_static
        self.api = api if api is not None else GameApi()
        self._lock = threading.Lock()
        self.started = time.time()
        self.requests = 0
        self.not_found: Counter[str] = Counter()
        self.blocked: list[dict] = []
        self.answered: Counter[str] = Counter()
        self.unsupported: Counter[str] = Counter()

    def handle_error(self, request, client_address):
        """Browsers drop connections all the time (reloads, closing the tab); that's not
        worth a traceback. Anything else is logged in one line."""
        err = sys.exc_info()[1]
        if isinstance(err, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        self.log(f"[offline] ERROR while handling a request from {client_address[0]}: {err!r}")

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"

    def note(self, line: str, *, static: bool = False) -> None:
        with self._lock:
            self.requests += 1
        if not (static and self.quiet_static):
            self.log(f"[offline] {line}")

    def summary(self) -> str:
        lines = [f"{self.requests} requests in {time.time() - self.started:.0f} s."]
        if self.not_found:
            lines.append(f"Not found ({sum(self.not_found.values())}):")
            lines += [f"  {n:4}x {p}" for p, n in self.not_found.most_common(30)]
        if self.blocked:
            lines.append(f"Blocked by the page's lock-down rule ({len(self.blocked)}):")
            lines += [f"  {b.get('blocked-uri')}  ({b.get('violated-directive') or b.get('effective-directive')})"
                      for b in self.blocked[:30]]
        else:
            lines.append("Nothing was blocked: the page only asked for this server.")
        if self.answered:
            lines.append("API messages answered: " + ", ".join(f"{m} x{n}" for m, n in self.answered.most_common()))
        if self.unsupported:
            lines.append("API messages not supported yet:")
            lines += [f"  {n:4}x {m}" for m, n in self.unsupported.most_common()]
        return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    server: OfflineServer
    protocol_version = "HTTP/1.1"
    server_version = "TyrantOffline/0.1"

    def log_message(self, format, *args):  # noqa: A002  (silence the default per-request log)
        pass

    # ---- routing ------------------------------------------------------------

    def do_GET(self):
        self._route(head=False)

    def do_HEAD(self):
        self._route(head=True)

    def do_POST(self):
        path = urlsplit(self.path).path
        body = self._read_body()
        if path == API_PATH:
            self._api(body)
        elif path == "/__csp":
            self._csp_report(body)
        else:
            self._not_found(path)

    def _route(self, head: bool):
        path = unquote(urlsplit(self.path).path)
        if path in ("/", "/index.html"):
            original = (self.server.client_dir / "index.html").read_text("utf-8")
            body = build_index(original).encode("utf-8")
            self.server.note(f"200 page {path}")
            return self._send(200, body, CONTENT_TYPES[".html"], head,
                              {"Content-Security-Policy": CSP, "Cache-Control": "no-store"})
        if path == API_PATH:
            return self._api(b"")
        if path.startswith("/tu-offline/"):
            return self._file(_inside(self.server.web_dir, path[len("/tu-offline/"):]), head, "web",
                              cache="no-store")
        if path == "/favicon.ico" and not (self.server.client_dir / "favicon.ico").is_file():
            return self._send(204, b"", "image/x-icon", head)  # browsers always ask; there is none
        if path.startswith("/Release/") or path == "/favicon.ico":
            return self._file(_inside(self.server.client_dir, path.lstrip("/")), head, "client")
        if path.startswith("/tu/"):
            host, _, rest = path[len("/tu/"):].partition("/")
            if host in ARCHIVE_HOSTS:
                return self._file(self.server.archive_dir / safe_rel_path(host, "/" + rest), head, "archive",
                                  label=f"{host}/{rest}")
        return self._not_found(path)

    # ---- handlers -------------------------------------------------------------

    def _file(self, target: Path | None, head: bool, kind: str, label: str | None = None,
              cache: str = "max-age=3600"):
        label = label or self.path
        if target is None or not target.is_file():
            return self._not_found(label, kind)
        size = target.stat().st_size
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if not head:
            with target.open("rb") as f:
                try:
                    shutil.copyfileobj(f, self.wfile, 1 << 20)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server.note(f"200 {kind} {label} ({size:,} bytes)", static=True)

    def _api(self, body: bytes):
        """Answer an api.php call: from the game API if it supports the message,
        otherwise with the real server's error shape."""
        params = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query, keep_blank_values=True).items()}
        if body:
            params.update({k: v[-1] for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()})
        message = params.get("message") or "unknown"
        with self.server._lock:  # the API keeps per-player state (battle in progress, replay position)
            reply, how = self.server.api.respond(message, params)
            if how.startswith("UNSUPPORTED"):
                self.server.unsupported[message] += 1
            else:
                self.server.answered[message] += 1
        self.server.note(f"API {message}: {how}")
        self._send(200, encode_reply(reply), CONTENT_TYPES[".json"], False, {"Cache-Control": "no-store"})

    def _csp_report(self, body: bytes):
        try:
            report = json.loads(body.decode("utf-8") or "{}").get("csp-report", {})
        except ValueError:
            report = {"blocked-uri": "(unreadable report)"}
        with self.server._lock:
            self.server.blocked.append(report)
        self.server.note(f"BLOCKED {report.get('blocked-uri')} "
                         f"({report.get('violated-directive') or report.get('effective-directive')})")
        self._send(204, b"", "text/plain", False)

    def _not_found(self, label: str, kind: str = ""):
        with self.server._lock:
            self.server.not_found[label] += 1
        what = "NOT IN ARCHIVE" if kind == "archive" else "not found"
        self.server.note(f"404 {what}: {label}")
        self._send(404, b"not found", "text/plain", False, {"Cache-Control": "no-store"})

    # ---- plumbing -------------------------------------------------------------------

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _send(self, status: int, body: bytes, ctype: str, head: bool, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
