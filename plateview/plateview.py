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
    plateview show ID                                        the HTML file, the folders and the files of a plate
    plateview comments ID [--json]                           the comments of a plate
    plateview reply ID N TEXT|-                              a reply to comment N ("-": the text from stdin)
    plateview add ID TEXT|- [--quote W | --cell T R C]       a new comment
    plateview note TEXT | try N [FILE]                       a note to the app, or try a proposal (self-improving apps)
    plateview                                                the list of plates in the browser
    plateview --status | --stop | --restart

Comments, chat and the Oracle: a plate has comments (plates/ID/comments.json), a chat with Claude (one session for each
plate), and the Oracle, a background Claude run that answers the open comments (/answer-plate). ⌘/Ctrl+Shift+. opens the
dashboard of the notes to the app.

One server runs in the background on 127.0.0.1:8769 (PLATEVIEW_PORT). PLATEVIEW_BIND=tailscale serves it on the
Tailscale address of this computer, for your other devices (PLATEVIEW_NAMES: the names of this computer on the tailnet).
It restarts itself when this file or a .py file of ../viewkit changes. Python 3.9+, standard library only.
"""
import argparse
import base64
import fcntl
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
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
PREVIEW = re.sub(r"[^\w-]", "-", os.environ.get("VIEWKIT_PREVIEW", ""))  # the sandbox of a proposal (improvekit)
# A preview server keeps its own plates, so it never shows or deletes the plates of the live server.
STATE_DIR = Path(os.environ.get("PLATEVIEW_STATE") or Path.home() / ".cache" / "plateview" /
                 (f"preview/{PREVIEW}" if PREVIEW else "")).expanduser()
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
TEXT_MAX = 20000   # the longest comment
CONTEXT = 40       # the characters of an anchor before and after the quote

sys.path.insert(0, str(KIT))  # the code that the viewers share
try:
    import claudekit
    import datakit
    import improvekit
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
# The print view of a plate (⌘/Ctrl+P): the export in its own tab, a sandbox with no network. It may only open the
# print dialog (allow-modals), so that the browser saves the plate as a PDF.
CSP_PRINT = ("sandbox allow-scripts allow-modals; default-src 'none'; script-src 'unsafe-inline'; "
             "style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; font-src data:; base-uri 'none'; "
             "frame-ancestors 'none'; form-action 'none'")
# The script of the print view: when the plate, its fonts and its images are ready, open the print dialog.
PRINT_JS = ("<script>addEventListener('load', async () => {"
            " if (document.fonts) await document.fonts.ready;"
            " await new Promise(r => setTimeout(r, 600));"
            " for (let i = 0; i < 50 && ![...document.images].every(im => im.complete); i++)"
            " await new Promise(r => setTimeout(r, 100));"
            " document.documentElement.dataset.pvPrint = 'ready'; print(); });</script>\n")

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
# The comments of a plate: plates/ID/comments.json. They go when the plate goes.
# --------------------------------------------------------------------------
# A thread: {"id", "anchor"?, "msgs": [{"author": "you" | "claude", "text", "ts", "edited"?}]}. An anchor is the
# selected words of the plate ({"quote", "prefix", "suffix"}) or a cell of a table or a grid ({"table", "row", "col"}).
# A thread without an anchor is about the whole plate.

def comments_file(pid):
    return plate_dir(pid) / "comments.json"


def read_comments(pid):
    """(data, version) of the comments of plate pid. The version is a hash of the file, "" if there is no file."""
    try:
        raw = comments_file(pid).read_bytes()
    except FileNotFoundError:
        return {"threads": []}, ""
    version = hashlib.sha1(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("threads"), list):
            raise ValueError("no threads list")
    except (ValueError, UnicodeDecodeError) as e:
        raise PlateError(f"The comments file of plate {pid} is not valid: {e}")
    return data, version


def comments_text(pid):
    try:
        return comments_file(pid).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def write_comments_text(pid, text):
    """Write the comments file of plate pid (None: remove it). Returns the new version."""
    f = comments_file(pid)
    if text is None:
        f.unlink(missing_ok=True)
        return ""
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=".comments.")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
    os.replace(tmp, f)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def write_comments(pid, data):
    return write_comments_text(pid, json.dumps(data, indent=1, ensure_ascii=False) + "\n" if data["threads"] else None)


class comments_lock:
    """A lock for each plate, for the server and the command line, so two writers never mix their changes."""
    def __init__(self, pid):
        (STATE_DIR / "locks").mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = STATE_DIR / "locks" / f"{plate_dir(pid).name}.lock"

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)  # also releases the lock


def clean_text(text):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or "")).replace("\r\n", "\n").strip()
    if not text:
        raise PlateError("Write a comment first.")
    if len(text) > TEXT_MAX:
        raise PlateError(f"The comment is too long (more than {TEXT_MAX} characters).")
    return text


def one_line(v, n):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else "")).strip()[:n]


def clean_anchor(a):
    """A text anchor or a cell anchor, or None for a comment about the whole plate."""
    if not a:
        return None
    if not isinstance(a, dict):
        raise PlateError("Bad anchor.")
    if a.get("table") is not None or a.get("col") is not None:
        out = {k: one_line(a.get(k), 300) for k in ("table", "row", "col")}
        if not out["table"] or not out["col"]:
            raise PlateError("A cell comment needs a table and a column.")
        return out
    q = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(a.get("quote") or "")).strip()[:2000]
    if not q:
        raise PlateError("Select some text first.")
    out = {"quote": q}
    for k in ("prefix", "suffix"):
        v = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(a.get(k) or ""))
        if v:
            out[k] = v[-CONTEXT:] if k == "prefix" else v[:CONTEXT]
    return out


def apply_op(data, op, author="you"):
    """Change the comments. op: {"op": "add" | "reply" | "edit" | "delete" | "resolve" | "resolve-all", ...}."""
    kind, now = op.get("op"), int(time.time())
    ts = data["threads"]
    if kind == "add":
        tid = max([t.get("id", 0) for t in ts] + [0]) + 1
        t = {"id": tid, "anchor": clean_anchor(op.get("anchor")),
             "msgs": [{"author": author, "text": clean_text(op.get("text")), "ts": now}]}
        if t["anchor"] is None:
            del t["anchor"]
        ts.append(t)
        return tid
    if kind == "resolve-all":
        if not ts:
            raise PlateError("There are no comments.")
        data["threads"] = []
        return None
    try:
        tid = int(op.get("thread"))
    except (TypeError, ValueError):
        raise PlateError("Which comment?")
    t = next((x for x in ts if x.get("id") == tid), None)
    if t is None:
        raise PlateError(f"There is no comment #{tid}.")
    if kind == "reply":
        t["msgs"].append({"author": author, "text": clean_text(op.get("text")), "ts": now})
    elif kind == "resolve":
        ts.remove(t)
    elif kind in ("edit", "delete"):
        try:
            m = t["msgs"][int(op.get("msg"))]
        except (TypeError, ValueError, IndexError):
            raise PlateError("Which message?")
        if kind == "edit":
            m["text"], m["edited"] = clean_text(op.get("text")), now
        else:
            t["msgs"].remove(m)
            if not t["msgs"]:
                ts.remove(t)
    else:
        raise PlateError(f"Unknown operation: {kind}")
    return tid


def change(pid, op, author="you", version=None):
    """Apply one operation to the comments of plate pid, under the lock. With `version`, refuse if the file changed
    since. Returns (result, text before or None, new version)."""
    with comments_lock(pid):
        data, v = read_comments(pid)
        if version is not None and version != v:
            raise PlateError("The comments changed. They have been reloaded; please try again.")
        before = comments_text(pid)
        res = apply_op(data, op, author)
        return res, before, write_comments(pid, data)


def open_count(pid):
    try:
        return sum(status_of(t) == "open" for t in read_comments(pid)[0]["threads"])
    except PlateError:
        return 0


def status_of(t):
    return "answered" if t["msgs"] and t["msgs"][-1].get("author") == "claude" else "open"


def threads_json(data):
    return [{"id": t["id"], "anchor": t.get("anchor"), "status": status_of(t),
             "msgs": [{"author": m.get("author") or "you", "text": m.get("text", ""), "ts": m.get("ts"),
                       "edited": m.get("edited")} for m in t["msgs"]]} for t in data["threads"]]


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


def export_plate(pid, printing=False):
    """One HTML file with the plate, the kit and the data and images that the plate used, for the disk. With `printing`,
    the file opens the print dialog when it is ready (the print view)."""
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
    bundle = f"<script>window.__PV_BUNDLE = {data};</script>\n" + (PRINT_JS if printing else "")
    return compose(text, plate_head(export=True, bundle=bundle)), meta


# --------------------------------------------------------------------------
# Chat: a conversation with Claude about one plate (the panel at the bottom right)
# --------------------------------------------------------------------------
# One `claude -p` process per message, all in one session per plate. The frame polls for the answer as it grows.
# Claude runs in the base folder of the plate (its project, with its CLAUDE.md), and may edit the HTML of the plate.

CHAT_TOOLS = ["Read", "Grep", "Glob", "Edit", "Write", "Bash(plateview:*)", "WebSearch", "WebFetch"]
CHAT_TOOLS_ASK = ["Read", "Grep", "Glob", "Bash(plateview comments:*)", "Bash(plateview show:*)", "WebSearch", "WebFetch"]
CHAT_DENY_ASK = ["Edit", "Write", "NotebookEdit", "Bash(plateview reply:*)", "Bash(plateview add:*)",
                 "Bash(plateview update:*)", "Bash(plateview delete:*)"]
CHAT_SYSTEM = """You are in the chat panel of plateview, a viewer of plates: HTML pages that show live data files \
(tables, grids, charts, figures) in a sandbox. The plate is the file <FILE>. Its id is <ID>.
The user sees your reply in a narrow panel, so keep it short and plain. Maths in $...$ and $$...$$ is typeset there.
- The plate reads its data files with the pv library. Relative paths start at <BASE>. The plate may read only these \
folders: <ALLOW>. The pv functions and the classes are in the section "Write a plate" of \
~/tools/viewers/plateview/README.md. Read it before you change the plate.
- To change the plate, edit the file <FILE>. The open tab shows the change at once.
- The comments of the user are not in the plate. `plateview comments <ID>` prints them. Answer a comment with
  `plateview reply <ID> N - <<'EOF'` and the text on the next lines, then EOF (the quoted EOF keeps $ and quotes as \
they are). Add a comment with `plateview add <ID> - --quote 'exact words of the plate' <<'EOF'` in the same way, or \
with --cell TABLE ROW COL for a table cell. Do this only when the user asks you to answer or add comments.
- Never edit the comments file. Change the plate or the data files only when the user asks you to.
- The user may tell you which words or which table cell they selected. Use it to see what "this" means.
- Every message starts with a line in brackets. It tells you the mode of that message. Follow the last one."""
# The mode goes into every message, not into the system prompt: a resumed session keeps the system prompt of its first turn.
CHAT_MODE_EDIT = ("(Mode: you may change the plate, and answer or add comments, when the user asks. "
                  "Answer questions without changing anything.)")
CHAT_MODE_ASK = "(Mode: Ask only. Do not change any file. Do not add or answer comments. Read and answer.)"
CHAT_QUOTE_MAX = 1500


def claude_exe():
    exe = os.environ.get("PLATEVIEW_CLAUDE") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    if not os.access(exe, os.X_OK):
        raise RuntimeError("Can't find the claude command. Set PLATEVIEW_CLAUDE to its path and restart plateview.")
    return exe


# The Oracle: a background `claude -p "/answer-plate ID"` run that answers the open comments (claudekit.Oracle). Its
# document is the HTML file of the plate, so it runs in the folder of the plate.
ORACLE_TOOLS = ["Read", "Grep", "Glob", "Bash(plateview:*)", "WebSearch", "WebFetch"]
ORACLE = claudekit.Oracle(STATE_DIR / "claude", claude_exe, lambda p: f"/answer-plate {p.parent.name}", ORACLE_TOOLS,
                          disallowed=["Edit", "Write", "NotebookEdit"])


def plate_html(pid):
    return plate_dir(pid) / "plate.html"


def work_dir(meta):
    """The folder where Claude runs for a plate: its base folder, or the folder of the plate if the base is gone."""
    base = Path(meta.get("base") or "")
    return base if base.is_absolute() and base.is_dir() else plate_dir(meta["id"])


class Chat:
    def __init__(self, pid):
        self.pid = pid
        self.file = plate_dir(pid) / "chat.json"  # it goes with the plate
        self.lock = threading.Lock()
        self.rev = 0       # counts the changes, so the frame can ask for news only
        self.cur = None    # the answer that is being written, or None
        self.proc = None
        self.stop_flag = False
        self.data = self.load()

    def load(self):
        try:
            d = json.loads(self.file.read_text(encoding="utf-8"))
            if isinstance(d.get("messages"), list) and d.get("session"):
                d.setdefault("started", False)
                return d
        except (OSError, ValueError, AttributeError):
            pass
        return {"session": str(uuid.uuid4()), "started": False, "messages": []}

    def save(self):
        if not self.file.parent.is_dir():
            return  # the plate was deleted
        fd, tmp = tempfile.mkstemp(dir=self.file.parent, prefix=".chat.")  # created 0600
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.file)

    def snapshot(self, since=None):
        with self.lock:
            if since == self.rev:
                return {"rev": self.rev, "same": True}
            msgs = json.loads(json.dumps(self.data["messages"] + ([self.cur] if self.cur else [])))
            return {"rev": self.rev, "busy": self.cur is not None, "messages": msgs}

    def busy(self):
        with self.lock:
            return self.cur is not None

    def send(self, text, quote_, ask_only):
        """Start one turn. Returns None, or a message that says why it cannot start."""
        with self.lock:
            if self.cur is not None:
                return "Claude is still answering."
            if ORACLE.busy(plate_html(self.pid)):
                return "The Oracle is answering the comments of this plate. Wait for it, or stop it."
            try:
                exe = claude_exe()
            except RuntimeError as e:
                return str(e)
            now = int(time.time())
            user = {"role": "user", "text": text, "ts": now}
            if quote_:
                user["quote"] = quote_
            self.data["messages"].append(user)
            self.cur = {"role": "assistant", "parts": [], "ts": now, "streaming": True}
            self.stop_flag = False
            self.rev += 1
            self.save()
            threading.Thread(target=self.run, args=(exe, text, quote_, ask_only), daemon=True).start()
        return None

    def event(self, ev):
        """One line of what `claude --output-format stream-json` prints."""
        with self.lock:
            if self.cur is None:
                return
            started, _ = claudekit.apply_event(self.cur, ev)
            if started:
                self.data["started"] = True
            self.rev += 1

    def run(self, exe, text, quote_, ask_only):
        pid, f = self.pid, plate_html(self.pid)
        try:
            meta = read_meta(pid)
        except PlateError:
            meta = {"id": pid, "base": "", "allow": []}
        before, plate_before = comments_text(pid), file_state(f)
        quote_ = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", quote_ or "").strip()[:CHAT_QUOTE_MAX]
        ctx = ("(The user has selected this: " + json.dumps(quote_, ensure_ascii=False) + ")\n") if quote_ else ""
        mode = (CHAT_MODE_ASK if ask_only else CHAT_MODE_EDIT) + "\n"
        with self.lock:
            sid, started = self.data["session"], self.data.get("started")
        if ask_only:
            allowed, denied, perm = CHAT_TOOLS_ASK, CHAT_DENY_ASK, None
        else:
            allowed, denied, perm = CHAT_TOOLS, ["NotebookEdit"], "acceptEdits"
        system = (CHAT_SYSTEM.replace("<FILE>", str(f)).replace("<ID>", pid).replace("<BASE>", meta.get("base") or "?")
                  .replace("<ALLOW>", ", ".join(meta.get("allow") or []) or "none"))
        cmd = claudekit.build_command(exe, mode + ctx + ("\n" if ctx else "") + text, system, sid, started, allowed,
                                      denied, perm) + ["--add-dir", str(plate_dir(pid))]
        err, stderr, code = claudekit.run_stream(cmd, work_dir(meta), claudekit.clean_env(), self.event, self.set_proc)
        changed = comments_text(pid) != before
        if changed and Handler.app is not None:  # one Undo takes back the comments that this turn changed
            Handler.app.push_undo(pid, before)
        with self.lock:
            cur = self.cur
            cur["changed"] = changed
            cur["plateChanged"] = file_state(f) != plate_before
            claudekit.finish(cur, err, stderr, code, self.stop_flag)
            self.data["messages"].append(cur)
            self.cur, self.proc = None, None
            self.rev += 1
            self.save()

    def set_proc(self, proc):
        self.proc = proc

    def stop(self):
        with self.lock:
            self.stop_flag = True
            proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def new(self):
        """A new conversation. Returns None, or a message that says why it cannot."""
        with self.lock:
            if self.cur is not None:
                return "Claude is still answering."
            self.data = {"session": str(uuid.uuid4()), "started": False, "messages": []}
            self.rev += 1
            self.save()
        return None


chats = {}
chats_lock = threading.Lock()


def chat_of(pid):
    with chats_lock:
        return chats.setdefault(pid, Chat(pid))


# --------------------------------------------------------------------------
# Notes to the app (viewkit/improvekit.py): the preview of a note opens a copy of the plate
# --------------------------------------------------------------------------
# When you add a note in a plate, the server keeps a copy of the plate in STATE_DIR/notes/ID: plate.html, and
# plate.html.plate.json with its title, its folders and its comments. The preview server of the proposal (VIEWKIT_PREVIEW)
# makes its plate from these copies, so the preview shows your plate with its data. The data files stay where they are:
# a plate only reads them.

def note_snapshot(pid):
    meta = read_meta(pid)
    d = STATE_DIR / "notes" / pid
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(plate_html(pid), d / "plate.html")
    info = {"title": meta["title"], "allow": meta.get("allow", []), "base": meta.get("base"),
            "threads": read_comments(pid)[0]["threads"]}
    (d / "plate.html.plate.json").write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
    return d / "plate.html"


def preview_settings(src):
    """In a preview server only: the settings of a plate copy (note_snapshot), or None."""
    f = Path(str(src) + ".plate.json")
    if not PREVIEW or not f.is_file():
        return None
    try:
        info = json.loads(f.read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.seen = {}  # plate id -> the last time that a tab asked about it
        self.undo = {}  # plate id -> list of (version after the change, comments text before it, or None)
        self.oracle_before = {}  # plate id -> the comments text when the Oracle started, until its end is seen

    def push_undo(self, pid, before):
        try:
            version = read_comments(pid)[1]
        except PlateError:
            return
        with self.lock:
            stack = self.undo.setdefault(pid, [])
            stack.append((version, before))
            del stack[:-20]

    def can_undo(self, pid, version):
        stack = self.undo.get(pid) or []
        return bool(stack) and stack[-1][0] == version

    def oracle_seen(self, pid, status):
        """When an Oracle run has ended, one Undo takes back what it changed."""
        if status.get("running"):
            return
        with self.lock:
            if pid not in self.oracle_before:
                return
            before = self.oracle_before.pop(pid)
        if before != comments_text(pid):
            self.push_undo(pid, before)

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

    def payload(self, meta):
        """What the frame needs about a plate: its settings, its state, the version of its HTML, and its comments."""
        pid = meta["id"]
        try:  # an edit of the HTML (by the chat) can change the title
            t = title_of(plate_html(pid).read_text(encoding="utf-8"))
            if t and t != meta["title"]:
                with self.app.lock:
                    meta = read_meta(pid)
                    meta["title"] = t
                    write_meta(pid, meta)
        except OSError:
            pass
        data, cversion = read_comments(pid)
        return {**{k: v for k, v in meta.items() if k not in ("owner", "used")}, "session": bool(meta.get("owner")),
                "state": state_of(meta, self.app.seen), "grace": GRACE, "version": file_state(plate_html(pid)),
                "threads": threads_json(data), "cversion": cversion, "canUndo": self.app.can_undo(pid, cversion)}

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
        if url.path.startswith("/p/") and url.path.endswith("/print"):
            # The print view (⌘/Ctrl+P in the frame): the export of the plate in a tab of its own, with no network.
            try:
                out, _ = export_plate(url.path[3:-len("/print")], printing=True)
            except (OSError, PlateError) as e:
                return self.send(404, f"Cannot print the plate: {e}", "text/plain; charset=utf-8")
            return self.send(200, out, csp=CSP_PRINT)
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
                                                        "state": state_of(m, seen), "session": bool(m.get("owner")),
                                                        "open": open_count(m["id"])}
                                                       for m in all_plates()], "grace": GRACE})
            if url.path == "/api/improve/dash":  # the dashboard of the notes (self-improving apps)
                return self.send_json(*improvekit.dash_api("plateview", HERE, "GET"))
            if url.path == "/api/improve/preview":  # in the sandbox of a proposal: the band with Accept and Reject
                return self.send_json(*improvekit.preview_api("GET"))
            meta = self.meta_of(q)
            pid = meta["id"]
            if url.path == "/api/plate":
                self.app.touch(pid)
                return self.send_json(200, self.payload(meta))
            if url.path == "/api/version":  # also the sign that a tab shows the plate
                self.app.touch(pid)
                files = {}
                for path in meta.get("used", []):
                    try:
                        files[path] = file_state(used_file(meta, path))
                    except PlateError:
                        files[path] = ""
                return self.send_json(200, {"plate": file_state(plate_html(pid)), "files": files,
                                            "comments": read_comments(pid)[1]})
            if url.path == "/api/chat":
                since = (q.get("since") or [""])[0]
                return self.send_json(200, chat_of(pid).snapshot(int(since) if since.isdigit() else None))
            if url.path == "/api/claude":
                st = ORACLE.status(plate_html(pid))
                self.app.oracle_seen(pid, st)
                return self.send_json(200, st)
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
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, TypeError):
            return self.send_json(400, {"error": "Bad request"})
        if path == "/api/improve/preview":  # the decision in the preview tab: accept, change or reject
            return self.send_json(*improvekit.preview_api("POST", body))
        if path == "/api/improve/dash":  # the dashboard: add, edit or delete a waiting note, or send them all
            if improvekit.preview_of():
                return self.send_json(409, {"error": "This is a preview. Write notes in the live plateview tab."})
            doc = back = None
            if body.get("action") == "add" and body.get("p"):  # a note from a plate: keep a copy for its preview
                try:
                    pid = read_meta(str(body["p"]))["id"]
                    doc = note_snapshot(pid)
                    back = f"http://{NET.link_host()}:{self.server.server_address[1]}/p/{pid}"
                except (PlateError, OSError):
                    pass
            return self.send_json(*improvekit.dash_api("plateview", HERE, "POST", body, doc, back))
        try:
            meta = read_meta(str(body.get("p", "")))
        except PlateError as e:
            return self.send_json(404, {"error": str(e)})
        pid = meta["id"]
        if path == "/api/op":
            return self.comment_op(meta, body)
        if path == "/api/chat":  # the chat panel: send a message, stop the answer, or start a new conversation
            chat = chat_of(pid)
            action = body.get("action", "send")
            if action == "stop":
                chat.stop()
                return self.send_json(200, {"ok": True})
            if action == "new":
                err = chat.new()
            else:
                text = str(body.get("text", "")).strip()
                if not text:
                    return self.send_json(400, {"error": "Write a message first."})
                err = chat.send(text, str(body.get("quote") or ""), bool(body.get("ask_only")))
            return self.send_json(409 if err else 200, {"error": err} if err else {"ok": True})
        if path == "/api/claude":  # the Oracle: start or stop the run of this plate, or forget its session
            f, action = plate_html(pid), body.get("action")
            if action == "new":
                err = ORACLE.forget(f)
                return self.send_json(409, {"error": err}) if err else self.send_json(200, ORACLE.status(f))
            if action == "stop":
                ORACLE.stop(f)
                st = ORACLE.status(f)
                self.app.oracle_seen(pid, st)
                return self.send_json(200, st)
            if chat_of(pid).busy():
                return self.send_json(409, {"error": "Claude is answering in the chat. Wait for it, or stop it."})
            before = comments_text(pid)
            try:
                started = ORACLE.start(f)
            except (RuntimeError, OSError) as e:
                return self.send_json(500, {"error": str(e)})
            if started:
                with self.app.lock:
                    self.app.oracle_before[pid] = before
            return self.send_json(200, {**ORACLE.status(f), "already": not started})
        if path == "/api/keep":
            meta["kept"] = bool(body.get("keep", True))
            with self.app.lock:
                write_meta(pid, meta)
            return self.send_json(200, {"kept": meta["kept"]})
        if path == "/api/delete":
            shutil.rmtree(plate_dir(pid), ignore_errors=True)
            return self.send_json(200, {"deleted": pid})
        self.send_json(404, {"error": "Not found"})

    def comment_op(self, meta, op):
        """/api/op: a change to the comments, or Undo. Returns the payload of the plate."""
        pid, res = meta["id"], None
        try:
            with self.app.lock:
                stack = self.app.undo.setdefault(pid, [])
                if op.get("op") == "undo":
                    with comments_lock(pid):
                        version = read_comments(pid)[1]
                        if op.get("version") != version or not stack or stack[-1][0] != version:
                            raise PlateError("Nothing to undo.")
                        write_comments_text(pid, stack.pop()[1])
                else:
                    res, before, version = change(pid, op, "you", op.get("version"))
                    stack.append((version, before))
                    del stack[:-20]
        except PlateError as e:
            return self.send_json(409 if "changed" in str(e) else 400, {"error": str(e)})
        out = self.payload(read_meta(pid))
        if op.get("op") == "add":
            out["added"] = res
        return self.send_json(200, out)


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


def plate_id(arg):
    """The id of a plate from its id, its folder, or the path of its HTML file."""
    if ID_RE.match(arg or ""):
        return arg
    p = Path(arg).expanduser().resolve()
    p = p.parent if p.name == "plate.html" else p
    if p.parent == PLATES.resolve() and ID_RE.match(p.name):
        return p.name
    raise PlateError(f"There is no such plate: {arg}")


def text_arg(t):
    return sys.stdin.read() if t == "-" else t


def cmd_comments(pid, as_json):
    threads = threads_json(read_comments(pid)[0])
    if as_json:
        print(json.dumps(threads, indent=2, ensure_ascii=False))
        return
    if not threads:
        print("No comments.")
    for t in threads:
        a = t["anchor"]
        if a and "col" in a:
            where = f'cell {a["table"]} [{a.get("row") or "?"}, {a["col"]}]'
        elif a:
            q = a["quote"].replace("\n", " ")
            where = f'"{q[:70] + "…" if len(q) > 70 else q}"'
        else:
            where = "general"
        print(f'#{t["id"]}  {where}  [{t["status"]}]')
        for m in t["msgs"]:
            print(f'    {m["author"]}: {m["text"]}')


def main():
    ap = argparse.ArgumentParser(prog="plateview", description="Plates: HTML pages with live data, in a sandbox.",
                                 epilog=f"One shared server runs on port {PORT}. Its log is {LOG}.")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("new", help="make a plate from an HTML file and open it")
    s.add_argument("file", nargs="+", help='the HTML of the plate, or "-" for stdin; more files make more plates')
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
    sub.add_parser("show", help="the HTML file, the folders and the files of a plate").add_argument("id")
    s = sub.add_parser("comments", help="the comments of a plate")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("reply", help="a reply to a comment (as Claude)")
    s.add_argument("id")
    s.add_argument("thread", type=int)
    s.add_argument("text", help='the text, or "-" for stdin')
    s.add_argument("--author", default="claude", choices=["claude", "you"])
    s = sub.add_parser("add", help="a new comment (as Claude)")
    s.add_argument("id")
    s.add_argument("text", help='the text, or "-" for stdin')
    g = s.add_mutually_exclusive_group()
    g.add_argument("--quote", help="the exact words of the plate")
    g.add_argument("--cell", nargs=3, metavar=("TABLE", "ROW", "COL"), help="a cell of a table or a grid")
    s.add_argument("--author", default="claude", choices=["claude", "you"])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--no-open", action="store_true", dest="no_open_top")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=PORT, help=argparse.SUPPRESS)
    argv = sys.argv[1:]
    if argv and argv[0] == "note":  # a note to the app, not about a plate (viewkit/improvekit.py)
        return improvekit.note_main("plateview", HERE, argv[1:])
    if argv and argv[0] == "try":  # try a proposal in a sandbox, with its own plates
        n = argv[1] if len(argv) > 1 and argv[1].isdigit() else "x"
        os.environ.setdefault("PLATEVIEW_STATE", str(Path.home() / ".cache" / "plateview" / "preview" / f"plateview-{n}"))
        return improvekit.try_main("plateview", HERE, argv[1:])
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
            if "-" in a.file and len(a.file) > 1:
                sys.exit('plateview: "-" (stdin) must be the only file.')
            for src in a.file:
                text, folder = read_source(src)
                base = Path(a.base).expanduser() if a.base else (folder or Path.cwd())
                allow, title = a.allow or [str(base)], a.title
                pre = preview_settings(Path(src).expanduser().resolve()) if src != "-" else None
                if pre:  # a copy of a plate for the preview of a note: its own folders and comments
                    allow = a.allow or [x for x in pre.get("allow") or [] if isinstance(x, str)] or allow
                    base = Path(pre["base"]) if not a.base and isinstance(pre.get("base"), str) else base
                    title = title or pre.get("title")
                meta = make_plate(text, allow, title, base, the_owner(), a.keep)
                if pre and isinstance(pre.get("threads"), list) and pre["threads"]:
                    write_comments(meta["id"], {"threads": pre["threads"]})
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
        elif a.cmd == "show":
            m = read_meta(plate_id(a.id))
            print(f"plate {m['id']}  {m['title']}")
            print(f"html: {plate_html(m['id'])}")
            print(f"base: {m.get('base')}")
            for d in m.get("allow", []):
                print(f"allow: {d}")
            for f in m.get("used", []):
                print(f"reads: {f[5:] if f.startswith('json:') else f}")
        elif a.cmd == "comments":
            cmd_comments(plate_id(a.id), a.json)
        elif a.cmd == "reply":
            pid = plate_id(a.id)
            change(pid, {"op": "reply", "thread": a.thread, "text": text_arg(a.text)}, a.author)
            print(f"plateview: replied to comment #{a.thread} of plate {pid}.")
        elif a.cmd == "add":
            pid = plate_id(a.id)
            anchor = ({"quote": a.quote} if a.quote else
                      {"table": a.cell[0], "row": a.cell[1], "col": a.cell[2]} if a.cell else None)
            tid = change(pid, {"op": "add", "anchor": anchor, "text": text_arg(a.text)}, a.author)[0]
            print(f"plateview: added comment #{tid} to plate {pid}.")
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
