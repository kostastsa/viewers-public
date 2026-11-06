#!/usr/bin/env python3
"""plateview: plates in the browser. A plate is an HTML page that Claude writes, like a claude.ai Artifact, that shows
live data files (tables, grids, charts, figures) without a copy of the data in the page.

A plate runs in a sandboxed frame. It cannot reach the network, the server or your files. It asks the frame for a data
file or an image by its path, and the server gives only files in the folders that the plate may read (--allow). The
server adds the fonts, the stylesheet, the components (viewkit/web/components.js) and the pv library (runtime.js).

Plates are temporary. A plate stays while the Claude session that made it runs, or while a tab shows it, and the server
deletes it GRACE seconds (default 600) after both end. Keep makes a plate permanent. Export saves one HTML file with the
data inside, which opens without the server.

Usage:
    plateview new FILE.html [--allow DIR ...] [--title T]   make a plate from FILE ("-": stdin) and open it
    plateview FILE.html                                      the same, and the plate may read the folder of FILE
    plateview update ID FILE.html                            change the HTML of a plate (the open tab reloads it)
    plateview list [--json]                                  the plates, with their state
    plateview open ID                                        open a plate in the browser
    plateview keep ID | delete ID                            make a plate permanent, or delete it now
    plateview export ID [OUT.html]                           save one HTML file with the data inside
    plateview                                                the list of plates in the browser
    plateview --status | --stop | --restart

One server runs in the background on 127.0.0.1:8769 (PLATEVIEW_PORT). PLATEVIEW_BIND=tailscale serves it on the
Tailscale address of this computer, for your other devices (PLATEVIEW_NAMES: the names of this computer on the tailnet).
It restarts itself when this file or a .py file of ../viewkit changes. Python 3.9+, standard library only.
"""
import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

HERE = Path(__file__).resolve().parent
KIT = HERE.parent / "viewkit"
SHELL = HERE / "shell.html"
PAGE_FILES = {"shell.js": "text/javascript; charset=utf-8"}
PORT = int(os.environ.get("PLATEVIEW_PORT") or 8769)
STATE_DIR = Path(os.environ.get("PLATEVIEW_STATE") or Path.home() / ".cache" / "plateview").expanduser()
PLATES = STATE_DIR / "plates"
FIG_CACHE = STATE_DIR / "fig"
LOG = STATE_DIR / "server.log"
GRACE = float(os.environ.get("PLATEVIEW_GRACE") or 600)   # seconds after the session and the last tab end
REAP_EVERY = float(os.environ.get("PLATEVIEW_REAP") or 60)
HTML_MAX = 5_000_000
FILE_MAX = 50_000_000
EXPORT_MAX = 100_000_000
ID_RE = re.compile(r"^[a-z0-9]{8,32}$")
STARTED = None
TOKEN = None

sys.path.insert(0, str(KIT))  # the code that the viewers share
try:
    import datakit
    import serverkit
except ImportError:
    sys.exit(f"plateview: the folder viewkit is missing. It must be next to plateview, at {KIT}.")
NET = serverkit.Net("plateview", "PLATEVIEW")
DAEMON = serverkit.Daemon("plateview", STATE_DIR, __file__, "PLATEVIEW_PORT", bind=NET.bind)
read_token, new_token = DAEMON.read_token, DAEMON.new_token
ping, ensure_server, stop_server = DAEMON.ping, DAEMON.ensure_server, DAEMON.stop_server

# The frame (shell) may run only its own scripts and the kit, talk only to this server, and frame only plates.
CSP_SHELL = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
             "font-src 'self' data:; connect-src 'self'; frame-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
             "form-action 'none'")
# A plate: a sandbox (an opaque origin: no cookies, no server data), its own inline code, and no network at all.
CSP_PLATE = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
             "img-src data: blob:; media-src data: blob:; font-src data:; base-uri 'none'; frame-ancestors 'self'; "
             "form-action 'none'")
CSP_NONE = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
# The same rules in an exported file, which opens from the disk without the server.
CSP_EXPORT = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
              "media-src data: blob:; font-src data:; base-uri 'none'; form-action 'none'")

FONTS = [("IBM Plex Sans", 400, "IBMPlexSans-Regular"), ("IBM Plex Sans", 500, "IBMPlexSans-Medium"),
         ("IBM Plex Sans", 600, "IBMPlexSans-SemiBold"), ("IBM Plex Sans Condensed", 500, "IBMPlexSansCondensed-Medium"),
         ("IBM Plex Sans Condensed", 600, "IBMPlexSansCondensed-SemiBold"), ("IBM Plex Mono", 400, "IBMPlexMono-Regular"),
         ("IBM Plex Mono", 500, "IBMPlexMono-Medium")]


# --------------------------------------------------------------------------
# Plates: a folder each in STATE_DIR/plates: plate.html and meta.json
# --------------------------------------------------------------------------

class PlateError(Exception):
    pass


def plate_dir(pid):
    if not ID_RE.match(pid or ""):
        raise PlateError("There is no such plate.")
    return PLATES / pid


def read_meta(pid):
    try:
        return json.loads((plate_dir(pid) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PlateError(f"There is no plate {pid}.")


def write_meta(pid, meta):
    d = plate_dir(pid)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".meta.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    os.replace(tmp, d / "meta.json")


def write_html(pid, text):
    d = plate_dir(pid)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".plate.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, d / "plate.html")


def all_plates():
    out = []
    try:
        names = sorted(PLATES.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return out
    for d in names:
        if d.is_dir() and ID_RE.match(d.name):
            try:
                out.append(read_meta(d.name))
            except PlateError:
                pass
    return out


def title_of(text):
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()[:200] if m else None


def proc_start(pid):
    """The start time of process pid, as `ps` prints it, or None if it does not run."""
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True, timeout=5)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def owner_alive(owner):
    """True while the Claude session that made the plate runs: its process exists and started at the same time."""
    if not owner or not owner.get("pid"):
        return False
    try:
        os.kill(int(owner["pid"]), 0)
    except (OSError, ValueError):
        return False
    return proc_start(owner["pid"]) == owner.get("start")


def the_owner():
    """The Claude Code session that runs this command, from its environment, or None."""
    pid = os.environ.get("CLAUDE_PID")
    if not pid or not pid.isdigit():
        return None
    start = proc_start(pid)
    if not start:
        return None
    return {"pid": int(pid), "start": start, "session": os.environ.get("CLAUDE_CODE_SESSION_ID") or None}


def state_of(meta, seen, now=None):
    """Why a plate stays: kept, session, tab, new; or None: it can go."""
    now = now or time.time()
    if meta.get("kept"):
        return "kept"
    if owner_alive(meta.get("owner")):
        return "session"
    last = max(seen.get(meta["id"], 0), meta.get("seen", 0))
    if now - last < GRACE:
        return "tab"
    if now - meta.get("created", 0) < GRACE:
        return "new"
    return None


def reap(seen):
    """Delete the plates that can go. Returns their ids."""
    gone = []
    for meta in all_plates():
        if state_of(meta, seen) is None:
            shutil.rmtree(plate_dir(meta["id"]), ignore_errors=True)
            gone.append(meta["id"])
    return gone


def make_plate(text, allow, title=None, base=None, owner=None, keep=False):
    if len(text.encode("utf-8")) > HTML_MAX:
        raise PlateError(f"The plate is larger than {HTML_MAX // 1_000_000} MB.")
    pid = secrets.token_hex(6)
    d = plate_dir(pid)
    d.mkdir(parents=True, mode=0o700)
    now = time.time()
    meta = {"id": pid, "title": title or title_of(text) or "Plate", "created": now, "seen": 0,
            "allow": [str(Path(a).expanduser().resolve()) for a in allow],
            "base": str(Path(base or os.getcwd()).resolve()), "owner": owner, "kept": bool(keep), "used": []}
    write_html(pid, text)
    write_meta(pid, meta)
    return meta


# --------------------------------------------------------------------------
# The files that a plate may read
# --------------------------------------------------------------------------

def is_hidden_path(p):
    """True if a part of p below the home folder (or of the whole path, outside it) starts with a dot."""
    try:
        parts = p.relative_to(Path.home()).parts
    except ValueError:
        parts = p.parts
    return any(x.startswith(".") for x in parts)


def plate_file(meta, path):
    """The file at `path` (absolute, ~/…, or relative to the folder of the plate's source) if the plate may read it."""
    if not path or "\x00" in path:
        raise PlateError("No path.")
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(meta["base"]) / p
    p = p.resolve()
    if is_hidden_path(p):
        raise PlateError(f"plateview never reads hidden files or folders: {path}")
    if not any(p == Path(a) or Path(a) in p.parents for a in meta.get("allow", [])):
        raise PlateError(f"The plate may not read {path}. Allow its folder: plateview new … --allow FOLDER")
    if not p.is_file():
        raise PlateError(f"File not found: {path}")
    if p.stat().st_size > FILE_MAX:
        raise PlateError(f"{path} is larger than {FILE_MAX // 1_000_000} MB.")
    return p


def read_json(p):
    """The content of a JSON file, or of a JSON Lines file as a list. NaN and infinity become null."""
    if p.suffix.lower() not in (".json", ".jsonl"):
        raise PlateError(f"Not a JSON file (.json, .jsonl): {p.name}")
    text = p.read_text(encoding="utf-8")
    bad = lambda _: None  # NaN, Infinity
    try:
        if p.suffix.lower() == ".jsonl":
            return [json.loads(l, parse_constant=bad) for l in text.splitlines() if l.strip()]
        return json.loads(text, parse_constant=bad)
    except ValueError as e:
        raise PlateError(f"{p.name} is not valid JSON: {e}")


def used_file(meta, key):
    """The file of a "used" entry: a path, or "json:" and a path."""
    return plate_file(meta, key[5:] if key.startswith("json:") else key)


def file_state(p):
    try:
        st = p.stat()
        return f"{st.st_mtime_ns}-{st.st_size}"
    except OSError:
        return ""


# --------------------------------------------------------------------------
# The page of a plate: the kit, the fonts, the stylesheet and the pv library, then the HTML of the plate
# --------------------------------------------------------------------------

_head_cache = {}


def plate_head(export=False, bundle=""):
    """The <style> and <script> blocks that go before the HTML of a plate. An export gets its CSP and its data (the
    script `bundle`) first, so the pv library finds the data when it starts."""
    files = [KIT / "web" / "components.css", HERE / "plate.css", KIT / "web" / "components.js", HERE / "runtime.js"]
    key = tuple(file_state(f) for f in files)
    meta = f'<meta http-equiv="Content-Security-Policy" content="{CSP_EXPORT}">' if export else ""
    first = f'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{meta}{bundle}'
    if key in _head_cache:
        return first + _head_cache[key]
    fonts = []
    for fam, w, name in FONTS:
        f = KIT / "web" / "vendor" / "fonts" / f"{name}.woff2"
        try:
            b64 = base64.b64encode(f.read_bytes()).decode("ascii")
        except OSError:
            continue  # the fallback fonts of the stylesheet
        fonts.append(f"@font-face{{font-family:'{fam}';font-weight:{w};font-style:normal;font-display:swap;"
                     f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")
    css = "\n".join(fonts) + "\n" + files[0].read_text(encoding="utf-8") + "\n" + files[1].read_text(encoding="utf-8")
    js = files[2].read_text(encoding="utf-8") + "\n" + files[3].read_text(encoding="utf-8")
    js = js.replace("</script", "<\\/script")
    out = f"<style>\n{css}\n</style>\n<script>\n{js}\n</script>\n"
    _head_cache.clear()
    _head_cache[key] = out
    return first + out


def compose(text, head):
    """The HTML of a plate with `head` in it: after <head>, or before everything if the plate has no <head>."""
    m = re.search(r"<head[^>]*>", text, re.I)
    if m:
        return text[:m.end()] + head + text[m.end():]
    m = re.search(r"<html[^>]*>", text, re.I)
    if m:
        return text[:m.end()] + "<head>" + head + "</head>" + text[m.end():]
    return "<!doctype html><html><head>" + head + "</head><body>" + text + "</body></html>"


def export_plate(pid):
    """One HTML file with the plate, the kit and the data and images that the plate used, for the disk."""
    meta = read_meta(pid)
    text = (plate_dir(pid) / "plate.html").read_text(encoding="utf-8")
    bundle, total = {"data": {}, "files": {}, "json": {}}, 0
    for path in meta.get("used", []):
        try:
            p = used_file(meta, path)
        except PlateError:
            continue
        ext = p.suffix.lower()
        if path.startswith("json:"):
            try:
                bundle["json"][path[5:]] = {"json": read_json(p)}
            except PlateError as e:
                bundle["json"][path[5:]] = {"error": str(e)}
        elif ext in datakit.DATA_EXT:
            try:
                bundle["data"][path] = datakit.read_table(p)
            except datakit.DataError as e:
                bundle["data"][path] = {"error": str(e)}
        elif ext in datakit.MEDIA_TYPES:
            src = datakit.pdf_figure(p, FIG_CACHE) if ext == ".pdf" else p
            if not src:
                continue
            raw = src.read_bytes()
            total += len(raw)
            mime = "image/png" if ext == ".pdf" else datakit.MEDIA_TYPES[ext]
            bundle["files"][path] = {"type": mime, "b64": base64.b64encode(raw).decode("ascii")}
        if total > EXPORT_MAX:
            raise PlateError(f"The export is larger than {EXPORT_MAX // 1_000_000} MB.")
    data = json.dumps(bundle, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return compose(text, plate_head(export=True, bundle=f"<script>window.__PV_BUNDLE = {data};</script>\n")), meta


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.seen = {}  # plate id -> the last time that a tab asked about it

    def touch(self, pid):
        with self.lock:
            self.seen[pid] = time.time()

    def use(self, meta, path):
        """Remember that the plate read `path`, for Export."""
        if path in meta.get("used", []):
            return
        with self.lock:
            m = read_meta(meta["id"])
            if path not in m["used"]:
                m["used"].append(path)
                write_meta(meta["id"], m)

    def reaper(self):
        while True:
            try:
                with self.lock:
                    seen = dict(self.seen)
                for pid in reap(seen):
                    print(f"plateview: deleted plate {pid}", flush=True)
                for pid, t in seen.items():  # keep the last visit on disk, for a restart of the server
                    try:
                        m = read_meta(pid)
                        if t - m.get("seen", 0) > 30:
                            m["seen"] = t
                            write_meta(pid, m)
                    except PlateError:
                        pass
            except Exception as e:  # never stop the reaper
                print(f"plateview: reaper: {e}", flush=True)
            time.sleep(REAP_EVERY)


class Handler(BaseHTTPRequestHandler):
    server_version = "plateview"
    app: App = None

    def log_message(self, *args):
        pass

    def host_ok(self):
        host = self.headers.get("Host", "").rsplit(":", 1)[0].lower()
        return host in {h.lower() for h in NET.hosts()}

    def cookie_name(self):
        return f"plateview{self.server.server_address[1]}"

    def authorized(self):
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.cookie_name() and TOKEN and hmac.compare_digest(value, TOKEN):
                return True
        return False

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None, csp=CSP_NONE, frame="DENY"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Frame-Options", frame)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, obj):
        self.send(code, json.dumps(obj, ensure_ascii=False, allow_nan=False, default=str), "application/json; charset=utf-8")

    def meta_of(self, q):
        pid = (q.get("p") or [""])[0]
        return read_meta(pid)

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, "Forbidden")
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/api/ping":
            return self.send_json(200, {"app": "plateview", "pid": os.getpid(), "started": STARTED,
                                        "code": str(Path(__file__).resolve())})
        t = q.pop("t", [""])[0]
        if t and TOKEN and hmac.compare_digest(t, TOKEN):
            rest = urlencode(q, doseq=True)
            return self.send(303, "", extra={"Location": url.path + ("?" + rest if rest else ""),
                                             "Set-Cookie": f"{self.cookie_name()}={TOKEN}; Path=/; HttpOnly; SameSite=Strict"})
        if not self.authorized():
            msg = "This page's access key is missing or out of date. Open the plate again with the plateview command."
            if url.path.startswith("/api/"):
                return self.send_json(403, {"error": msg})
            return self.send(403, f"<!doctype html><meta charset=utf-8><title>plateview</title>"
                                  f"<p style='font:15px system-ui,sans-serif;margin:32px'>{msg}</p>")
        if url.path.startswith("/p/") and url.path.endswith("/plate"):
            # The plate itself, for the sandboxed frame of the shell. The CSP makes it a sandbox with no network: it
            # gets its data only from the shell, by messages.
            pid = url.path[3:-len("/plate")]
            try:
                text = (plate_dir(pid) / "plate.html").read_text(encoding="utf-8")
            except (OSError, PlateError):
                return self.send(404, "Not found", "text/plain; charset=utf-8")
            return self.send(200, compose(text, plate_head()), csp=CSP_PLATE, frame="SAMEORIGIN")
        if url.path == "/" or (url.path.startswith("/p/") and "/" not in url.path[3:]):
            return self.send(200, SHELL.read_text(encoding="utf-8"), csp=CSP_SHELL)
        if url.path[1:] in PAGE_FILES:
            return self.send(200, (HERE / url.path[1:]).read_bytes(), PAGE_FILES[url.path[1:]])
        if url.path.startswith("/kit/"):
            a = serverkit.kit_asset(url.path[len("/kit/"):])
            return self.send(200, a[0], a[1]) if a else self.send(404, "Not found")
        try:
            if url.path == "/api/plates":
                with self.app.lock:
                    seen = dict(self.app.seen)
                return self.send_json(200, {"plates": [{**{k: m.get(k) for k in ("id", "title", "created", "kept", "allow")},
                                                        "state": state_of(m, seen), "session": bool(m.get("owner"))}
                                                       for m in all_plates()], "grace": GRACE})
            meta = self.meta_of(q)
            pid = meta["id"]
            if url.path == "/api/plate":
                self.app.touch(pid)
                return self.send_json(200, {**meta, "state": state_of(meta, self.app.seen), "grace": GRACE,
                                            "version": file_state(plate_dir(pid) / "plate.html")})
            if url.path == "/api/version":  # also the sign that a tab shows the plate
                self.app.touch(pid)
                files = {}
                for path in meta.get("used", []):
                    try:
                        files[path] = file_state(used_file(meta, path))
                    except PlateError:
                        files[path] = ""
                return self.send_json(200, {"plate": file_state(plate_dir(pid) / "plate.html"), "files": files})
            if url.path == "/api/data":
                path = (q.get("path") or [""])[0]
                p = plate_file(meta, path)
                if p.suffix.lower() not in datakit.DATA_EXT:
                    raise PlateError(f"Not a data file (.csv, .tsv, .json, .jsonl): {path}")
                d = datakit.read_table(p)
                self.app.use(meta, path)
                return self.send_json(200, {"path": path, "state": file_state(p), **d})
            if url.path == "/api/json":  # any JSON file (or JSON Lines, as a list), as it is
                path = (q.get("path") or [""])[0]
                p = plate_file(meta, path)
                obj = read_json(p)
                self.app.use(meta, "json:" + path)
                return self.send_json(200, {"path": path, "state": file_state(p), "json": obj})
            if url.path == "/api/file":
                path = (q.get("path") or [""])[0]
                p = plate_file(meta, path)
                ext = p.suffix.lower()
                if ext not in datakit.MEDIA_TYPES:
                    raise PlateError(f"Not an image, a video or a PDF figure: {path}")
                self.app.use(meta, path)
                if ext == ".pdf":
                    png = datakit.pdf_figure(p, FIG_CACHE)
                    if not png:
                        raise PlateError(f"Cannot make an image of {path} (install poppler: brew install poppler).")
                    return self.send(200, png.read_bytes(), "image/png")
                return self.send(200, p.read_bytes(), datakit.MEDIA_TYPES[ext])
            if url.path == "/api/export":
                out, m = export_plate(pid)
                name = re.sub(r"[^\w .-]+", "", m["title"]).strip() or "plate"
                return self.send(200, out, extra={"Content-Disposition": f'attachment; filename="{name}.html"'})
        except PlateError as e:
            return self.send_json(404 if "no plate" in str(e).lower() or "no such" in str(e).lower() else 400,
                                  {"error": str(e)})
        except datakit.DataError as e:
            return self.send_json(400, {"error": str(e)})
        self.send(404, "Not found")

    def do_POST(self):
        if not self.host_ok() or self.headers.get("X-PlateView") != "1":
            return self.send_json(403, {"error": "Forbidden"})
        if not self.authorized():
            return self.send_json(403, {"error": "The access key is missing or out of date."})
        path = urlparse(self.path).path
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            meta = read_meta(str(body.get("p", "")))
        except (ValueError, TypeError, AttributeError):
            return self.send_json(400, {"error": "Bad request"})
        except PlateError as e:
            return self.send_json(404, {"error": str(e)})
        pid = meta["id"]
        if path == "/api/keep":
            meta["kept"] = bool(body.get("keep", True))
            with self.app.lock:
                write_meta(pid, meta)
            return self.send_json(200, {"kept": meta["kept"]})
        if path == "/api/delete":
            shutil.rmtree(plate_dir(pid), ignore_errors=True)
            return self.send_json(200, {"deleted": pid})
        self.send_json(404, {"error": "Not found"})


def run_server(port):
    global STARTED, TOKEN
    STARTED = time.strftime("%Y-%m-%d %H:%M:%S")
    TOKEN = read_token(port) or new_token(port)
    PLATES.mkdir(parents=True, mode=0o700, exist_ok=True)
    Handler.app = App()
    threading.Thread(target=Handler.app.reaper, daemon=True).start()
    try:
        server = ThreadingHTTPServer((NET.bind(), port), Handler)
    except OSError as e:
        sys.exit(f"plateview: can't listen on {NET.bind()}:{port}: {e}")
    DAEMON.serve(server, port, f"plateview: serving at http://{NET.bind()}:{port}/ (pid {os.getpid()}, {STARTED})")


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def link(port, path):
    ensure_server(port)
    token = read_token(port)
    if not token:
        sys.exit("plateview: the server's access key is missing. Restart it: plateview --restart")
    return f"http://{NET.link_host()}:{port}{path}" + ("&" if "?" in path else "?") + "t=" + token


def read_source(src):
    if src == "-":
        return sys.stdin.read(), None
    p = Path(src).expanduser().resolve()
    if not p.is_file():
        sys.exit(f"plateview: not found: {src}")
    return p.read_text(encoding="utf-8"), p.parent


def post(port, path, body):
    token = read_token(port)
    req = urllib.request.Request(f"http://{NET.bind()}:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-PlateView": "1",
                                          "Cookie": f"plateview{port}={token}", "Host": f"127.0.0.1:{port}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"plateview: {json.load(e).get('error', e.reason)}")


def main():
    ap = argparse.ArgumentParser(prog="plateview", description="Plates: HTML pages with live data, in a sandbox.",
                                 epilog=f"One shared server runs on port {PORT}. Its log is {LOG}.")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("new", help="make a plate from an HTML file and open it")
    s.add_argument("file", help='the HTML of the plate, or "-" for stdin')
    s.add_argument("--allow", action="append", default=[], metavar="DIR",
                   help="a folder (or file) that the plate may read; give it more than once for more")
    s.add_argument("--title")
    s.add_argument("--base", help="the folder for relative paths in the plate (default: the folder of FILE)")
    s.add_argument("--keep", action="store_true", help="make the plate permanent")
    s.add_argument("--no-open", action="store_true")
    s = sub.add_parser("update", help="change the HTML of a plate")
    s.add_argument("id")
    s.add_argument("file")
    s = sub.add_parser("list", help="the plates")
    s.add_argument("--json", action="store_true")
    for name in ("open", "keep", "delete"):
        sub.add_parser(name).add_argument("id")
    s = sub.add_parser("export", help="save one HTML file with the data inside")
    s.add_argument("id")
    s.add_argument("out", nargs="?")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--no-open", action="store_true", dest="no_open_top")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=PORT, help=argparse.SUPPRESS)
    argv = sys.argv[1:]
    if argv and argv[0].lower().endswith((".html", ".htm")) and Path(argv[0]).expanduser().is_file():
        argv = ["new"] + argv  # plateview FILE.html (also from Finder): a new plate, which may read the folder of FILE
    a = ap.parse_args(argv)
    port = a.port
    if a.serve:
        return run_server(port)
    if a.status:
        info = ping(port)
        print(f"plateview: running on http://{NET.bind()}:{port}/ (pid {info['pid']}, since {info['started']})"
              if info else "plateview: not running.")
        return
    if a.stop or a.restart:
        stop_server(port)
        if a.restart:
            print(f"plateview: running (pid {ensure_server(port)['pid']}).")
        return
    try:
        if a.cmd == "new":
            text, folder = read_source(a.file)
            base = Path(a.base).expanduser() if a.base else (folder or Path.cwd())
            allow = a.allow or [str(base)]
            meta = make_plate(text, allow, a.title, base, the_owner(), a.keep)
            url = link(port, f"/p/{meta['id']}")
            print(f"plateview: plate {meta['id']}  {meta['title']}")
            print(url)
            if not a.no_open:
                webbrowser.open(url)
        elif a.cmd == "update":
            read_meta(a.id)
            text, _ = read_source(a.file)
            if len(text.encode("utf-8")) > HTML_MAX:
                sys.exit(f"plateview: the plate is larger than {HTML_MAX // 1_000_000} MB.")
            write_html(a.id, text)
            m = read_meta(a.id)
            t = title_of(text)
            if t and t != m["title"]:
                m["title"] = t
                write_meta(a.id, m)
            print(f"plateview: updated plate {a.id}. An open tab shows the change.")
        elif a.cmd == "list":
            info = ping(port) or {}
            seen = {}
            plates = [{**m, "state": state_of(m, seen)} for m in all_plates()]
            if a.json:
                print(json.dumps(plates, indent=1, ensure_ascii=False, default=str))
            for m in plates if not a.json else []:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["created"]))
                print(f"{m['id']}  {when}  [{m['state'] or 'closed'}]  {m['title']}")
            if not plates and not a.json:
                print("No plates.")
            if not info and not a.json:
                print("(the server is not running: the tab states are not known)")
        elif a.cmd == "open":
            read_meta(a.id)
            url = link(port, f"/p/{a.id}")
            print(url)
            webbrowser.open(url)
        elif a.cmd == "keep":
            m = read_meta(a.id)
            m["kept"] = True
            write_meta(a.id, m)
            print(f"plateview: plate {a.id} is kept. It is not deleted. Delete it with: plateview delete {a.id}")
        elif a.cmd == "delete":
            read_meta(a.id)
            shutil.rmtree(plate_dir(a.id))
            print(f"plateview: deleted plate {a.id}.")
        elif a.cmd == "export":
            out, m = export_plate(a.id)
            dest = Path(a.out).expanduser() if a.out else Path.cwd() / (re.sub(r"[^\w .-]+", "", m["title"]).strip() + ".html")
            dest.write_text(out, encoding="utf-8")
            print(f"plateview: exported plate {a.id} to {dest}")
        else:
            url = link(port, "/")
            print(url)
            if not a.no_open_top:
                webbrowser.open(url)
    except PlateError as e:
        sys.exit(f"plateview: {e}")


if __name__ == "__main__":
    main()
