#!/usr/bin/env python3
"""pdfview: read and comment on PDF files in the browser.

The PDF is never changed. The comments are in a JSON file next to it, .pdfview/NAME.pdf.json:

    {"threads": [{"id": 1, "anchor": {"page": 4, "quote": "the bound is tight", "prefix": "we show that ",
                                      "suffix": " for all n"},
                  "msgs": [{"author": "you", "text": "Why?", "ts": 1730000000}]}]}

A thread without an anchor is a general comment about the whole file. A text anchor is the page (from 1), the selected
words, and a few words before and after them, so the viewer can find the words again.

Usage:
    pdfview paper.pdf                    open a file (starts the shared server if needed)
    pdfview                              browse the current folder
    pdfview list paper.pdf [--json]      print the comment threads
    pdfview reply paper.pdf ID TEXT      add a reply to thread ID (as claude). TEXT "-" reads it from stdin.
    pdfview add paper.pdf TEXT [--page N --quote Q]   add a comment (as claude). TEXT "-" reads it from stdin.
    pdfview text paper.pdf [--page N | --pages A-B]   print the text of the PDF (needs pdftotext)
    pdfview add paper.pdf TEXT --page N --box x0,y0,x1,y1    a comment on an area of a page (a figure, an equation)
    pdfview crop paper.pdf --page N --box x0,y0,x1,y1        a PNG of that area, to look at it (needs pdftoppm)
    pdfview note TEXT                    a note to pdfview itself: a problem or a wish (a GitHub issue). "-": stdin.
    pdfview try N [paper.pdf]            try the proposal of note N in a sandbox (port 8900+N, copies of the files)
    pdfview try --stop N                 stop that sandbox
    pdfview --status | --stop | --restart

One server runs in the background on 127.0.0.1:8767 and serves files under your home folder (PDFVIEW_ROOT and
PDFVIEW_PORT override these). It restarts itself when this file or a .py file of ../viewkit changes. Requires Python
3.9+ and the folder viewkit next to this one (the code that mdview, pyview and pdfview share).

Security: the viewer runs PDF.js without the scripts of the PDF (no scripting, no eval), and its Content-Security-Policy
allows only its own scripts. Every request needs the access key in ~/.cache/pdfview/token-PORT (readable only by you),
which `pdfview` passes in the link it opens and the browser then keeps as a cookie. The server opens only the files and
folders that you passed to `pdfview`. Hidden files and folders are never read, listed or served.
"""
import argparse
import fcntl
import hashlib
import hmac
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

HERE = Path(__file__).resolve().parent
VIEWER = HERE / "viewer.html"
VIEWER_JS = HERE / "viewer.js"
VENDOR = HERE / "vendor"
ROOT = Path(os.environ.get("PDFVIEW_ROOT") or Path.home()).expanduser().resolve()
PORT = int(os.environ.get("PDFVIEW_PORT") or 8767)
STATE_DIR = Path.home() / ".cache" / "pdfview"
LOG = STATE_DIR / "server.log"
LOCK_DIR = STATE_DIR / "locks"
SIDECAR_DIR = ".pdfview"  # next to the PDF
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "Library"}
STARTED = None
TOKEN = None  # the access key, set when the server starts
TEXT_MAX = 20000  # the longest comment
CONTEXT = 40      # the characters of an anchor before and after the quote

sys.path.insert(0, str(HERE.parent / "viewkit"))  # the code that the viewers share
try:
    import claudekit
    import improvekit
    import serverkit
except ImportError:
    sys.exit(f"pdfview: the folder viewkit is missing. It must be next to pdfview, at {HERE.parent / 'viewkit'}.")
DAEMON = serverkit.Daemon("pdfview", STATE_DIR, __file__, "PDFVIEW_PORT")
grants_file, write_private = DAEMON.grants_file, DAEMON.write_private
new_token, read_token = DAEMON.new_token, DAEMON.read_token
ping, ensure_server, stop_server = DAEMON.ping, DAEMON.ensure_server, DAEMON.stop_server

VENDOR_TYPES = {".mjs": "text/javascript; charset=utf-8", ".bcmap": "application/octet-stream",
                ".pfb": "application/octet-stream", ".ttf": "font/ttf"}

# Content-Security-Policy per kind of response. The viewer may run only its own scripts (viewer.js, the kit, PDF.js and
# its worker, MathJax) and talk only to this server. PDF.js makes fonts from data and draws on canvases, so it needs
# nothing else. No 'unsafe-eval': PDF.js runs with isEvalSupported off.
CSP_VIEWER = ("default-src 'none'; script-src 'self'; worker-src 'self'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; base-uri 'none'; "
              "frame-ancestors 'none'; form-action 'none'")
CSP_INDEX = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
CSP_NONE = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"


# --------------------------------------------------------------------------
# The comments: a JSON file next to the PDF
# --------------------------------------------------------------------------

class OpError(Exception):
    pass


def sidecar(p):
    return p.parent / SIDECAR_DIR / (p.name + ".json")


def read_comments(p):
    """(data, version) of the comments of PDF p. The version is a hash of the file, "" if there is no file."""
    try:
        raw = sidecar(p).read_bytes()
    except FileNotFoundError:
        return {"threads": []}, ""
    version = hashlib.sha1(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("threads"), list):
            raise ValueError("no threads list")
        if not isinstance(data.setdefault("highlights", []), list):
            raise ValueError("the highlights are not a list")
    except (ValueError, UnicodeDecodeError) as e:
        raise OpError(f"{sidecar(p)} is not a valid comments file: {e}")
    return data, version


def write_comments(p, data):
    """Write the comments of PDF p. With no threads and no highlights, remove the file (and the folder, if it is
    empty). Returns the new version."""
    f = sidecar(p)
    if not data.get("highlights"):
        data.pop("highlights", None)
    if not data["threads"] and not data.get("highlights"):
        f.unlink(missing_ok=True)
        try:
            f.parent.rmdir()
        except OSError:
            pass
        return ""
    f.parent.mkdir(exist_ok=True)
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix="." + f.name + ".")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, f)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class file_lock:
    """A lock per PDF, for the server and the command line, so two writers never mix their changes."""
    def __init__(self, p):
        LOCK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = LOCK_DIR / (hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:16] + ".lock")

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)  # also releases the lock


def clean_text(text):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or "")).replace("\r\n", "\n").strip()
    if not text:
        raise OpError("Write a comment first.")
    if len(text) > TEXT_MAX:
        raise OpError(f"The comment is too long (more than {TEXT_MAX} characters).")
    return text


def clean_box(b):
    """[x0, y0, x1, y1]: an area of a page in PDF points, from the top-left corner. From a list or "x0,y0,x1,y1"."""
    try:
        v = [float(x) for x in (b.split(",") if isinstance(b, str) else b)]
    except (TypeError, ValueError, AttributeError):
        raise OpError("A box needs four numbers: x0,y0,x1,y1.")
    if len(v) != 4 or not all(0 <= x <= 20000 for x in v):
        raise OpError("A box needs four numbers between 0 and 20000: x0,y0,x1,y1.")
    x0, y0, x1, y1 = min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3])
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise OpError("The box is too small.")
    return [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]


def clean_anchor(a, pages=None):
    """A text anchor or a box anchor of a page, or None for a general comment."""
    if not a:
        return None
    if not isinstance(a, dict):
        raise OpError("Bad anchor.")
    try:
        page = int(a.get("page"))
    except (TypeError, ValueError):
        raise OpError("The anchor has no page.")
    if page < 1 or (pages and page > pages):
        raise OpError(f"There is no page {page}.")
    quote_ = re.sub(r"[\x00-\x1f\x7f]", " ", str(a.get("quote") or "")).strip()[:2000]
    if a.get("box") is not None:  # an area of the page: [x0, y0, x1, y1] in PDF points from the top-left corner
        box = clean_box(a.get("box"))
        out = {"page": page, "box": box}
        if quote_:
            out["quote"] = quote_  # the text in the box, if there is some
        return out
    if not quote_:
        raise OpError("Select some text first.")
    out = {"page": page, "quote": quote_}
    for k in ("prefix", "suffix"):
        v = re.sub(r"[\x00-\x1f\x7f]", " ", str(a.get(k) or ""))
        if v:
            out[k] = v[-CONTEXT:] if k == "prefix" else v[:CONTEXT]
    return out


def find_thread(data, tid):
    for t in data["threads"]:
        if t.get("id") == tid:
            return t
    raise OpError(f"There is no comment #{tid}.")


def apply_op(data, op, author="you"):
    """Change the comments. op: {"op": "add" | "reply" | "edit" | "delete" | "resolve" | "resolve-all" |
    "highlight" | "unhighlight", ...}. A highlight is a marked passage with no comment, in data["highlights"].
    "add" with "from_highlight": ID makes that highlight a comment (the highlight goes)."""
    kind, now = op.get("op"), int(time.time())
    ts, hs = data["threads"], data.setdefault("highlights", [])
    if kind == "highlight":
        hid = max([h.get("id", 0) for h in hs] + [0]) + 1
        anchor = clean_anchor(op.get("anchor"))
        if anchor is None:
            raise OpError("Select some text first.")
        hs.append({"id": hid, "anchor": anchor, "ts": now})
        return hid
    if kind == "unhighlight":
        h = next((h for h in hs if h.get("id") == op.get("highlight")), None)
        if h is None:
            raise OpError("There is no such highlight.")
        hs.remove(h)
        return None
    if kind == "add":
        tid = max([t.get("id", 0) for t in ts] + [0]) + 1
        t = {"id": tid, "anchor": clean_anchor(op.get("anchor")),
             "msgs": [{"author": author, "text": clean_text(op.get("text")), "ts": now}]}
        if t["anchor"] is None:
            del t["anchor"]
        if op.get("from_highlight") is not None:
            h = next((h for h in hs if h.get("id") == op.get("from_highlight")), None)
            if h is None:
                raise OpError("There is no such highlight.")
            hs.remove(h)
        ts.append(t)
        return tid
    if kind == "resolve-all":
        if not ts:
            raise OpError("There are no comments.")
        data["threads"] = []
        return None
    try:
        tid = int(op.get("thread"))
    except (TypeError, ValueError):
        raise OpError("Which comment?")
    t = find_thread(data, tid)
    if kind == "reply":
        t["msgs"].append({"author": author, "text": clean_text(op.get("text")), "ts": now})
    elif kind == "resolve":
        ts.remove(t)
    elif kind in ("edit", "delete"):
        try:
            m = t["msgs"][int(op.get("msg"))]
        except (TypeError, ValueError, IndexError):
            raise OpError("Which message?")
        if kind == "edit":
            m["text"], m["edited"] = clean_text(op.get("text")), now
        else:
            t["msgs"].remove(m)
            if not t["msgs"]:
                ts.remove(t)
    else:
        raise OpError(f"Unknown operation: {kind}")
    return tid


def change(p, op, author="you", version=None):
    """Apply one operation to the comments of PDF p, under the lock. With `version`, refuse if the file changed since.
    Returns (result, before_text_or_None, new_version)."""
    with file_lock(p):
        data, v = read_comments(p)
        if version is not None and version != v:
            raise OpError("The comments changed. They have been reloaded; please try again.")
        try:
            before = sidecar(p).read_text(encoding="utf-8")
        except FileNotFoundError:
            before = None
        res = apply_op(data, op, author)
        return res, before, write_comments(p, data)


def status_of(t):
    return "answered" if t["msgs"] and t["msgs"][-1].get("author") == "claude" else "open"


def threads_json(data):
    out = []
    for t in data["threads"]:
        out.append({"id": t["id"], "anchor": t.get("anchor"), "status": status_of(t),
                    "msgs": [{"author": m.get("author") or "you", "text": m.get("text", ""), "ts": m.get("ts"),
                              "edited": m.get("edited")} for m in t["msgs"]]})
    return out


def pdf_version(p):
    st = p.stat()
    return f"{st.st_mtime_ns}-{st.st_size}"


def page_count(p):
    """The number of pages, from pdfinfo if it is there. None if it is not known."""
    exe = shutil.which("pdfinfo")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, str(p)], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------
# Chat: a conversation with Claude about one PDF (the panel at the bottom right)
# --------------------------------------------------------------------------
# One `claude -p` process per message, all in one session per PDF. The page polls for the answer as it grows.

CHAT_DIR = STATE_DIR / "chat"
CHAT_TOOLS = ["Read", "Grep", "Glob", "Bash(pdfview:*)", "Bash(pdftotext:*)", "Bash(pdfinfo:*)", "WebSearch", "WebFetch"]
CHAT_TOOLS_ASK = ["Read", "Grep", "Glob", "Bash(pdfview list:*)", "Bash(pdfview text:*)", "Bash(pdftotext:*)",
                  "Bash(pdfinfo:*)", "WebSearch", "WebFetch"]
CHAT_DENY = ["Edit", "Write", "NotebookEdit"]  # the PDF is never edited
CHAT_DENY_ASK = CHAT_DENY + ["Bash(pdfview reply:*)", "Bash(pdfview add:*)"]
CHAT_SYSTEM = """You are in the chat panel of pdfview, a viewer for PDF files. The document is the file <FILE>.
The user sees your reply in a narrow panel, so keep it short and plain. Maths in $...$ and $$...$$ is typeset there.
- Read the PDF with `pdfview text <FILE> --page N` (or --pages A-B), or with the Read tool and its pages parameter.
- The comments of the user are not in the PDF. `pdfview list <FILE>` prints them. Answer a comment with
  `pdfview reply <FILE> ID - <<'EOF'` and the text on the next lines, then EOF (the quoted EOF keeps $ and quotes as they
  are). Add a comment with `pdfview add <FILE> - --page N --quote 'exact words from the page' <<'EOF'` in the same way.
  Do this only when the user asks you to answer or add comments. Never change the PDF. Never edit the comments file.
- A comment can be on an area of a page (a box: "box": [x0, y0, x1, y1] in PDF points from the top-left corner),
  for example a figure or an equation. Look at the area with `pdfview crop <FILE> --page N --box x0,y0,x1,y1` (it
  prints the path of a PNG; read it with the Read tool), and read its text with `pdfview text <FILE> --page N --box ...`.
- The user may tell you which passage they selected, and which page they look at. Use it to see what "this" means.
- Every message starts with a line in brackets. It tells you the mode of that message. Follow the last one."""
# The mode goes into every message, not into the system prompt: a resumed session keeps the system prompt of its first turn.
CHAT_MODE_EDIT = "(Mode: you may answer and add comments when the user asks. Answer questions without changing anything.)"
CHAT_MODE_ASK = "(Mode: Ask only. Do not add or answer comments. Read and answer.)"
CHAT_QUOTE_MAX = 1500


def claude_exe():
    exe = os.environ.get("PDFVIEW_CLAUDE") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    if not os.access(exe, os.X_OK):
        raise RuntimeError("Can't find the claude command. Set PDFVIEW_CLAUDE to its path and restart pdfview.")
    return exe


# The Oracle: a background `claude -p "/answer-pdf FILE"` run that answers the open comments (claudekit.Oracle).
ORACLE_TOOLS = ["Read", "Grep", "Glob", "Bash(pdfview:*)", "Bash(pdftotext:*)", "Bash(pdfinfo:*)", "WebSearch", "WebFetch"]
ORACLE = claudekit.Oracle(STATE_DIR / "claude", claude_exe, lambda p: f'/answer-pdf "{p}"', ORACLE_TOOLS,
                          disallowed=CHAT_DENY)


def comments_text(p):
    try:
        return sidecar(p).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


class Chat:
    def __init__(self, p):
        self.path = p
        self.file = CHAT_DIR / (hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:16] + ".json")
        self.lock = threading.Lock()
        self.rev = 0       # counts the changes, so the page can ask for news only
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
        CHAT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CHAT_DIR, prefix=".chat.")  # created 0600
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

    def send(self, text, quote_, page, view_page, ask_only):
        """Start one turn. Returns None, or a message that says why it cannot start."""
        with self.lock:
            if self.cur is not None:
                return "Claude is still answering."
            if ORACLE.busy(self.path):
                return "The Oracle is answering the comments of this file. Wait for it, or stop it."
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
            threading.Thread(target=self.run, args=(exe, text, quote_, page, view_page, ask_only), daemon=True).start()
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

    def run(self, exe, text, quote_, page, view_page, ask_only):
        p = self.path
        before = comments_text(p)
        ctx = ""
        quote_ = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", quote_ or "").strip()[:CHAT_QUOTE_MAX]
        if quote_:
            where = f" on page {page}" if page else ""
            ctx += f"(The user has selected this passage{where}: " + json.dumps(quote_, ensure_ascii=False) + ")\n"
        if view_page:
            ctx += f"(The user looks at page {view_page}.)\n"
        mode = (CHAT_MODE_ASK if ask_only else CHAT_MODE_EDIT) + "\n"
        with self.lock:
            sid, started = self.data["session"], self.data.get("started")
        allowed, denied = (CHAT_TOOLS_ASK, CHAT_DENY_ASK) if ask_only else (CHAT_TOOLS, CHAT_DENY)
        cmd = claudekit.build_command(exe, mode + ctx + ("\n" if ctx else "") + text,
                                      CHAT_SYSTEM.replace("<FILE>", str(p)), sid, started, allowed, denied)
        err, stderr, code = claudekit.run_stream(cmd, p.parent, claudekit.clean_env(), self.event, self.set_proc)
        after = comments_text(p)
        changed = after != before
        if changed and Handler.app is not None:  # one Undo takes back everything that this turn changed
            Handler.app.push_undo(p, before)
        with self.lock:
            cur = self.cur
            cur["changed"] = changed
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


def chat_of(p):
    with chats_lock:
        return chats.setdefault(str(p), Chat(p))


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

def is_hidden(rel):
    """True if any part of a relative path starts with a dot (.ssh, .claude, .., .env)."""
    return any(part.startswith(".") for part in rel.parts)


NOT_GRANTED = "pdfview can only open files and folders you have passed to it. To open this one, run: pdfview {}"


class App:
    def __init__(self, root, grants_file=None):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        self.undo = {}  # path -> list of (version after the change, comments text before it, or None)
        self.oracle_before = {}  # path -> the comments text when the Oracle started, until its end is seen
        self.grants_file = grants_file
        self.granted = set()  # files and folders passed to the pdfview command
        self.opened = set()   # the PDFs that pages opened: same names get longer labels
        if grants_file:
            try:
                self.granted = {Path(g) for g in json.loads(grants_file.read_text())}
            except (OSError, ValueError):
                pass

    def grant(self, p):
        with self.lock:
            self.granted.add(p)
            if self.grants_file:
                write_private(self.grants_file, json.dumps(sorted(map(str, self.granted)), indent=1))

    def allowed(self, p):
        """True if p was passed to pdfview, or is inside a folder that was."""
        return any(g == p or g in p.parents for g in self.granted)

    def resolve(self, rel, want_dir=False):
        """The file (or folder) at `rel` under the root, or None. Hidden paths are refused."""
        rel = unquote(rel or "")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            return None
        if is_hidden(Path(rel)) or is_hidden(p.relative_to(self.root)):
            return None
        return p if (p.is_dir() if want_dir else p.is_file()) else None

    def rel(self, p):
        return p.relative_to(self.root).as_posix() if p != self.root else ""

    def oracle_seen(self, p, status):
        """When an Oracle run has ended, one Undo takes back what it changed."""
        if status.get("running"):
            return
        with self.lock:
            if str(p) not in self.oracle_before:
                return
            before = self.oracle_before.pop(str(p))
        if before != comments_text(p):
            self.push_undo(p, before)

    def push_undo(self, p, before):
        try:
            version = read_comments(p)[1]
        except OpError:
            return
        with self.lock:
            stack = self.undo.setdefault(str(p), [])
            stack.append((version, before))
            del stack[:-20]


class Handler(BaseHTTPRequestHandler):
    server_version = "pdfview"
    app: App = None

    def log_message(self, *args):
        pass

    # ----- helpers -----
    def host_ok(self):
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host.lower() in serverkit.local_hosts("pdfview")

    def cookie_name(self):
        return f"pdfview{self.server.server_address[1]}"  # cookies ignore ports, so name it per port

    def authorized(self):
        """The request carries the access key, as the cookie set when the link was opened."""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.cookie_name() and TOKEN and hmac.compare_digest(value, TOKEN):
                return True
        return False

    def deny(self, api):
        msg = "This page's access key is missing or out of date. Reopen the file with the pdfview command."
        if api:
            return self.send_json(403, {"error": msg})
        return self.send(403, f"<!doctype html><meta charset=utf-8><title>pdfview</title>"
                              f"<p style='font:15px system-ui,sans-serif;margin:32px'>{msg}</p>", csp=CSP_INDEX)

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None, csp=CSP_NONE):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, obj):
        self.send(code, json.dumps(obj), "application/json; charset=utf-8")

    def pdf_path(self, q):
        """(path, rel, None) for a PDF that may be opened, else (None, rel, (code, error))."""
        rel = (q.get("f") or [""])[0]
        p = self.app.resolve(rel)
        if not p or p.suffix.lower() != ".pdf":
            return None, rel, (404, f"File not found: {rel}")
        if not self.app.allowed(p):
            return None, rel, (403, NOT_GRANTED.format(p))
        return p, self.app.rel(p), None

    def payload(self, p, rel):
        data, version = read_comments(p)
        label = serverkit.doc_label(p, self.app.opened)  # a short name for the tab and the header
        self.app.opened.add(p)
        return {"name": p.name, "label": label, "rel": rel, "path": str(p), "dir": self.app.rel(p.parent),
                "dirOk": self.app.allowed(p.parent), "threads": threads_json(data), "version": version,
                "highlights": data.get("highlights", []),
                "pdf": pdf_version(p), "canUndo": self.can_undo(p, version)}

    def can_undo(self, p, version):
        stack = self.app.undo.get(str(p)) or []
        return bool(stack) and stack[-1][0] == version

    # ----- routes -----
    def do_GET(self):
        if not self.host_ok():
            return self.send(403, "Forbidden")
        url = urlparse(self.path)
        q = parse_qs(url.query)
        app = self.app

        if url.path == "/api/ping":  # open to all: the command line uses it to find the server
            return self.send_json(200, {"app": "pdfview", "pid": os.getpid(), "root": str(app.root),
                                        "started": STARTED, "code": str(Path(__file__).resolve())})

        # The tab icons are open to all, like /api/ping: Safari loads the tab icon without the cookie, and with a 403 it
        # shows a letter (P) in place of the icon. The icons are fixed files of the kit, with no data of the user.
        if url.path == "/favicon.ico" or url.path.startswith("/kit/icons/"):
            name = "icons/pdf.png" if url.path == "/favicon.ico" else unquote(url.path[len("/kit/"):])
            a = serverkit.kit_asset(name)
            return self.send(200, a[0], a[1]) if a else self.send(404, "Not found")

        # A link from the pdfview command carries the key as ?t=. Keep it as a cookie and reload without it, so it
        # does not stay in the address bar.
        t = q.pop("t", [""])[0]
        if t and TOKEN and hmac.compare_digest(t, TOKEN):
            rest = urlencode(q, doseq=True)
            return self.send(303, "", extra={
                "Location": url.path + ("?" + rest if rest else ""),
                "Set-Cookie": f"{self.cookie_name()}={TOKEN}; Path=/; HttpOnly; SameSite=Strict"})
        if not self.authorized():
            return self.deny(url.path.startswith("/api/"))

        if url.path == "/":
            d = app.resolve((q.get("d") or [""])[0], want_dir=True)
            if not d:
                return self.send(404, "Folder not found")
            if not app.allowed(d):
                return self.send(403, NOT_GRANTED.format(d), "text/plain; charset=utf-8")
            return self.send(200, self.index_page(d), csp=CSP_INDEX)

        if url.path == "/view":
            return self.send(200, VIEWER.read_text(encoding="utf-8"), csp=CSP_VIEWER)
        if url.path == "/viewer.js":
            return self.send(200, VIEWER_JS.read_bytes(), "text/javascript; charset=utf-8")
        if url.path.startswith("/kit/"):  # the page code that the viewers share
            a = serverkit.kit_asset(unquote(url.path[len("/kit/"):]))
            return self.send(200, a[0], a[1]) if a else self.send(404, "Not found")
        if url.path.startswith("/vendor/"):  # the local copy of PDF.js, its cmaps and its fonts
            p = (VENDOR / unquote(url.path[len("/vendor/"):])).resolve()
            if VENDOR.resolve() not in p.parents or not p.is_file() or p.suffix not in VENDOR_TYPES:
                return self.send(404, "Not found")
            return self.send(200, p.read_bytes(), VENDOR_TYPES[p.suffix])

        if url.path in ("/pdf", "/api/doc", "/api/version", "/api/chat", "/api/claude", "/api/improve/dash",
                        "/api/improve/preview"):
            p, rel, err = self.pdf_path(q)
            if err:
                return self.send_json(err[0], {"error": err[1]})
            if url.path == "/pdf":  # the PDF itself, for PDF.js
                return self.send(200, p.read_bytes(), "application/pdf")
            if url.path == "/api/version":
                try:
                    return self.send_json(200, {"version": read_comments(p)[1], "pdf": pdf_version(p)})
                except OpError as e:
                    return self.send_json(500, {"error": str(e)})
            if url.path == "/api/improve/dash":  # the dashboard of the notes (self-improving apps)
                return self.send_json(*improvekit.dash_api("pdfview", HERE, "GET"))
            if url.path == "/api/improve/preview":  # in the sandbox of a proposal: the band with Accept and Reject
                return self.send_json(*improvekit.preview_api("GET"))
            if url.path == "/api/claude":
                st = ORACLE.status(p)
                app.oracle_seen(p, st)
                return self.send_json(200, st)
            if url.path == "/api/chat":
                since = (q.get("since") or [""])[0]
                return self.send_json(200, chat_of(p).snapshot(int(since) if since.isdigit() else None))
            try:
                return self.send_json(200, self.payload(p, rel))
            except OpError as e:
                return self.send_json(500, {"error": str(e)})

        self.send(404, "Not found")

    def do_POST(self):
        # The custom header forces a CORS preflight, so other websites can't post here.
        if not self.host_ok() or self.headers.get("X-PDFView") != "1":
            return self.send_json(403, {"error": "Forbidden"})
        if not self.authorized():
            return self.deny(True)
        path = urlparse(self.path).path
        if path not in ("/api/op", "/api/grant", "/api/chat", "/api/claude", "/api/improve/dash", "/api/improve/preview"):
            return self.send_json(404, {"error": "Not found"})
        try:
            op = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if not isinstance(op, dict):
                raise ValueError
        except (ValueError, TypeError):
            return self.send_json(400, {"error": "Bad request"})

        if path == "/api/grant":  # the pdfview command: allow a file or folder you passed to it
            target = Path(str(op.get("path", "")))
            try:
                rel = target.resolve().relative_to(self.app.root).as_posix()
            except (ValueError, OSError):
                return self.send_json(404, {"error": f"{target} is outside {self.app.root}"})
            p = self.app.resolve(quote(rel if rel != "." else ""), want_dir=target.is_dir())
            if not p:
                return self.send_json(404, {"error": f"Can't open {target}"})
            self.app.grant(p)
            return self.send_json(200, {"ok": True})

        p, rel, err = self.pdf_path({"f": [str(op.get("f", ""))]})
        if err:
            return self.send_json(err[0], {"error": err[1]})

        if path == "/api/improve/dash":  # the dashboard: add, edit or delete a waiting note, or send them all
            if improvekit.preview_of():
                return self.send_json(409, {"error": "This is a preview. Write notes in the live pdfview tab."})
            port = self.server.server_address[1]
            back = f"http://{serverkit.app_host('pdfview')}:{port}/view?f={quote(rel)}"
            return self.send_json(*improvekit.dash_api("pdfview", HERE, "POST", op, p, back))

        if path == "/api/improve/preview":  # the decision in the preview tab: accept, change or reject
            return self.send_json(*improvekit.preview_api("POST", op))

        if path == "/api/chat":  # the chat panel: send a message, stop the answer, or start a new conversation
            chat = chat_of(p)
            action = op.get("action", "send")
            if action == "stop":
                chat.stop()
                return self.send_json(200, {"ok": True})
            if action == "new":
                err = chat.new()
            else:
                text = str(op.get("text", "")).strip()
                if not text:
                    return self.send_json(400, {"error": "Write a message first."})
                page = op.get("page") if isinstance(op.get("page"), int) else None
                view = op.get("view_page") if isinstance(op.get("view_page"), int) else None
                err = chat.send(text, str(op.get("quote") or ""), page, view, bool(op.get("ask_only")))
            return self.send_json(409 if err else 200, {"error": err} if err else {"ok": True})

        if path == "/api/claude":  # the Oracle: start or stop the run of this PDF, or forget its session
            action = op.get("action")
            if action == "new":
                err = ORACLE.forget(p)
                return self.send_json(409, {"error": err}) if err else self.send_json(200, ORACLE.status(p))
            if action == "stop":
                ORACLE.stop(p)
                st = ORACLE.status(p)
                self.app.oracle_seen(p, st)
                return self.send_json(200, st)
            if chat_of(p).busy():
                return self.send_json(409, {"error": "Claude is answering in the chat. Wait for it, or stop it."})
            before = comments_text(p)
            try:
                started = ORACLE.start(p)
            except (RuntimeError, OSError) as e:
                return self.send_json(500, {"error": str(e)})
            if started:
                with self.app.lock:
                    self.app.oracle_before[str(p)] = before
            return self.send_json(200, {**ORACLE.status(p), "already": not started})

        # /api/op: a change to the comments
        try:
            with self.app.lock:
                stack = self.app.undo.setdefault(str(p), [])
                if op.get("op") == "undo":
                    with file_lock(p):
                        version = read_comments(p)[1]
                        if op.get("version") != version or not stack or stack[-1][0] != version:
                            raise OpError("Nothing to undo.")
                        before = stack.pop()[1]
                        f = sidecar(p)
                        if before is None:
                            f.unlink(missing_ok=True)
                            try:
                                f.parent.rmdir()
                            except OSError:
                                pass
                        else:
                            f.parent.mkdir(exist_ok=True)
                            f.write_text(before, encoding="utf-8")
                else:
                    res, before, version = change(p, op, "you", op.get("version"))
                    stack.append((version, before))
                    del stack[:-20]
        except OpError as e:
            return self.send_json(409 if "changed" in str(e) else 400, {"error": str(e)})
        out = self.payload(p, rel)
        if op.get("op") == "add":
            out["added"] = res
        self.send_json(200, out)

    def index_page(self, d):
        """A folder browser: subfolders, then PDF files with their open-comment counts."""
        rows = []
        if d != self.app.root and self.app.allowed(d.parent):
            rows.append(f'<li><a href="/?d={quote(self.app.rel(d.parent))}">↑ ..</a></li>')
        try:
            entries = sorted(d.iterdir(), key=lambda e: e.name.lower())
        except OSError as e:
            entries = []
            rows.append(f"<li>{html.escape(str(e))}</li>")
        for e in entries:
            if e.name.startswith(".") or e.name in SKIP_DIRS or not e.is_dir() \
                    or not self.app.resolve(quote(self.app.rel(e)), want_dir=True):  # e.g. a link into ~/.ssh
                continue
            rows.append(f'<li><a href="/?d={quote(self.app.rel(e))}">📁 {html.escape(e.name)}</a></li>')
        for e in entries:
            if e.name.startswith(".") or not (e.is_file() and e.suffix.lower() == ".pdf"):
                continue
            try:
                n_open = sum(status_of(t) == "open" for t in read_comments(e)[0]["threads"])
            except OpError:
                n_open = 0
            badge = f'<span class="b">{n_open} open</span>' if n_open else ""
            rows.append(f'<li><a href="/view?f={quote(self.app.rel(e))}">📄 {html.escape(e.name)}</a>{badge}</li>')
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(d.name or str(d))}/ · pdfview</title>
<link rel="icon" type="image/png" href="/kit/icons/pdf.png">
<style>
:root{{color-scheme:dark;--bg:#111214;--fg:#d6d9de;--muted:#80858d;--accent:#8a9bd6;--hl:#3a3420}}
@media (prefers-color-scheme:light){{:root{{color-scheme:light;--bg:#fafaf7;--fg:#222;--muted:#666;--accent:#2f6fde;--hl:#ffe58a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,sans-serif;padding:32px 16px}}
main{{max-width:680px;margin:0 auto}} h1{{font-size:18px;overflow-wrap:anywhere}} ul{{padding:0;list-style:none}}
li{{padding:5px 0;border-bottom:1px solid rgba(128,128,128,.2);display:flex;gap:10px;align-items:center}}
a{{color:var(--accent);text-decoration:none;overflow-wrap:anywhere}} .b{{background:var(--hl);border-radius:10px;padding:0 8px;font-size:12px}}
</style></head><body><main><h1>{html.escape(str(d))}</h1><ul>{"".join(rows)}</ul></main></body></html>"""


def run_server(port):
    """Serve in the foreground. Restarts itself when this file changes."""
    global STARTED, TOKEN
    STARTED = time.strftime("%Y-%m-%d %H:%M:%S")
    TOKEN = read_token(port) or new_token(port)  # kept across code-change restarts, so open tabs keep working
    Handler.app = App(ROOT, grants_file(port))  # grants survive code-change restarts
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        sys.exit(f"pdfview: can't listen on port {port}: {e}")
    DAEMON.serve(server, port, f"pdfview: serving {ROOT} at http://127.0.0.1:{port}/ (pid {os.getpid()}, {STARTED})")


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def open_target(target, port, open_browser):
    p = Path(target).expanduser().resolve()
    if not p.exists():
        sys.exit(f"pdfview: not found: {target}")
    if p.is_file() and p.suffix.lower() != ".pdf":
        sys.exit(f"pdfview: not a PDF file: {target}")
    root = Path(ensure_server(port)["root"])  # the running server's root, which may predate PDFVIEW_ROOT
    if p != root and root not in p.parents:
        sys.exit(f"pdfview: {p} is outside {root}, the folder the server can open.\n"
                 f"Restart it with a wider root, e.g.: PDFVIEW_ROOT=/ pdfview --restart")
    if is_hidden(p.relative_to(root)):
        sys.exit(f"pdfview: {p} is in a hidden folder (or is a hidden file). pdfview never opens those, to keep\n"
                 f"folders like ~/.ssh and ~/.claude out of reach. Copy the file somewhere else to read it.")
    rel = p.relative_to(root).as_posix() if p != root else ""
    token = read_token(port)
    if not token:
        sys.exit("pdfview: the server's access key is missing. Restart it: pdfview --restart")
    grant(port, token, p)
    # pdfview.localhost: its own host name, so Safari shows the pdfview tab icon (see serverkit.app_host)
    url = f"http://{serverkit.app_host('pdfview')}:{port}/" + (f"view?f={quote(rel)}" if p.is_file() else f"?d={quote(rel)}")
    url += "&t=" + token
    print(url)
    if open_browser:
        webbrowser.open(url)


def grant(port, token, path):
    """Allow the server to open `path` (a file or a folder and everything in it)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/grant", data=json.dumps({"path": str(path)}).encode(),
        headers={"Content-Type": "application/json", "X-PDFView": "1", "Cookie": f"pdfview{port}={token}"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except ValueError:
            msg = e.reason
        sys.exit(f"pdfview: the server won't open {path}: {msg}")


def pdf_arg(s):
    p = Path(s).expanduser().resolve()
    if not p.is_file() or p.suffix.lower() != ".pdf":
        sys.exit(f"pdfview: not a PDF file: {s}")
    return p


def cmd_list(p, as_json):
    data = read_comments(p)[0]
    threads = threads_json(data)
    if as_json:
        print(json.dumps(threads, indent=2, ensure_ascii=False))
        return
    if not threads:
        print("No comments.")
    for t in threads:
        a = t["anchor"]
        if a and a.get("box"):
            q = (a.get("quote") or "").replace("\n", " ")
            where = f'page {a["page"]}  box {",".join(f"{x:g}" for x in a["box"])}' + (f'  "{q[:50] + "…" if len(q) > 50 else q}"' if q else "")
        elif a:
            q = a["quote"].replace("\n", " ")
            where = f'page {a["page"]}  "{q[:70] + "…" if len(q) > 70 else q}"'
        else:
            where = "general"
        print(f'#{t["id"]}  {where}  [{t["status"]}]')
        for m in t["msgs"]:
            print(f'    {m["author"]}: {m["text"]}')
    hs = data.get("highlights", [])
    if hs:
        print(f"Highlights (no comment): {len(hs)}")
        for h in hs:
            a = h["anchor"]
            q = (a.get("quote") or "").replace("\n", " ")
            box = f'box {",".join(f"{x:g}" for x in a["box"])}  ' if a.get("box") else ""
            print(f'  h{h["id"]}  page {a["page"]}  {box}"{q[:70] + "…" if len(q) > 70 else q}"')


def cmd_change(p, op, author):
    try:
        res = change(p, op, author)[0]
    except OpError as e:
        sys.exit(f"pdfview: {e}")
    print(f"pdfview: {'added comment #' + str(res) if op['op'] == 'add' else 'replied to #' + str(res)}.")


def box_args(box, dpi):
    """The crop options of pdftoppm and pdftotext (-x -y -W -H, in pixels at `dpi`) for a box in PDF points."""
    k = dpi / 72
    x0, y0, x1, y1 = box
    return ["-x", str(int(x0 * k)), "-y", str(int(y0 * k)), "-W", str(max(1, round((x1 - x0) * k))),
            "-H", str(max(1, round((y1 - y0) * k)))]


def cmd_crop(p, page, box, out, dpi):
    """A PNG of one area of a page (for example a figure of a box comment), so that Claude can look at it."""
    exe = shutil.which("pdftoppm")
    if not exe:
        sys.exit("pdfview: `crop` needs pdftoppm (brew install poppler).")
    try:
        box = clean_box(box)
    except OpError as e:
        sys.exit(f"pdfview: {e}")
    if out:
        dest = Path(out).expanduser().resolve()
    else:
        crop_dir = STATE_DIR / "crop"
        crop_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = hashlib.sha1(f"{p}|{p.stat().st_mtime_ns}|{page}|{box}|{dpi}".encode()).hexdigest()[:16]
        dest = crop_dir / f"{p.stem}-p{page}-{key}.png"
    r = subprocess.run([exe, "-png", "-r", str(dpi), "-f", str(page), "-l", str(page), *box_args(box, dpi),
                        "-singlefile", str(p), str(dest.with_suffix(""))], capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        sys.exit(f"pdfview: pdftoppm failed: {r.stderr.strip() or 'no image'}")
    print(dest)


def cmd_text(p, page, pages, box=None):
    exe = shutil.which("pdftotext")
    if not exe:
        sys.exit("pdfview: `text` needs pdftotext (brew install poppler). Or read the PDF with the Read tool.")
    if box:
        if not page:
            sys.exit("pdfview: --box needs --page.")
        try:
            b = clean_box(box)
        except OpError as e:
            sys.exit(f"pdfview: {e}")
        out = subprocess.run([exe, "-enc", "UTF-8", "-r", "72", "-f", str(page), "-l", str(page), *box_args(b, 72),
                              str(p), "-"], capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f"pdfview: pdftotext failed: {out.stderr.strip()}")
        print(out.stdout.replace("\f", "").rstrip())
        return
    if page:
        first = last = page
    elif pages:
        m = re.fullmatch(r"(\d+)-(\d+)", pages)
        if not m:
            sys.exit("pdfview: --pages needs the form A-B, e.g. 3-5.")
        first, last = int(m.group(1)), int(m.group(2))
    else:
        first, last = 1, page_count(p) or 0
        if not last:  # the number of pages is not known: all of it at once
            out = subprocess.run([exe, "-enc", "UTF-8", str(p), "-"], capture_output=True, text=True)
            sys.stdout.write(out.stdout)
            sys.exit(out.returncode)
    for n in range(first, last + 1):
        out = subprocess.run([exe, "-enc", "UTF-8", "-f", str(n), "-l", str(n), str(p), "-"],
                             capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f"pdfview: pdftotext failed on page {n}: {out.stderr.strip()}")
        print(f"=== page {n} ===")
        print(out.stdout.replace("\f", "").rstrip())


def sub_main(argv):
    """The subcommands for Claude and scripts: list, reply, add, text."""
    ap = argparse.ArgumentParser(prog="pdfview")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list", help="print the comment threads")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("reply", help="add a reply to a thread")
    s.add_argument("file")
    s.add_argument("id", type=int)
    s.add_argument("text", help='the reply, or "-" to read it from stdin')
    s.add_argument("--author", default="claude")
    s = sub.add_parser("add", help="add a comment, general or on a page")
    s.add_argument("file")
    s.add_argument("text", help='the comment, or "-" to read it from stdin')
    s.add_argument("--page", type=int)
    s.add_argument("--quote", help="the exact words on the page that the comment is about")
    s.add_argument("--box", help="an area of the page: x0,y0,x1,y1 in PDF points from the top-left corner "
                                 "(with --quote: the text in it, optional)")
    s.add_argument("--author", default="claude")
    s = sub.add_parser("text", help="print the text of the PDF, page by page (needs pdftotext)")
    s.add_argument("file")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--page", type=int)
    g.add_argument("--pages", help="A-B")
    s.add_argument("--box", help="with --page: only the text in this area, x0,y0,x1,y1 in PDF points")
    s = sub.add_parser("crop", help="make a PNG of an area of a page, to look at it (needs pdftoppm)")
    s.add_argument("file")
    s.add_argument("--page", type=int, required=True)
    s.add_argument("--box", required=True, help="x0,y0,x1,y1 in PDF points from the top-left corner")
    s.add_argument("--out", help="the PNG file (default: in ~/.cache/pdfview/crop/)")
    s.add_argument("--dpi", type=int, default=150)
    a = ap.parse_args(argv)
    p = pdf_arg(a.file)
    if getattr(a, "text", None) == "-":  # the text from stdin: safe for $, quotes and new lines
        a.text = sys.stdin.read()
    if a.cmd == "list":
        cmd_list(p, a.json)
    elif a.cmd == "reply":
        cmd_change(p, {"op": "reply", "thread": a.id, "text": a.text}, a.author)
    elif a.cmd == "add":
        if a.box and not a.page:
            sys.exit("pdfview: --box needs --page.")
        if not a.box and bool(a.page) != bool(a.quote):
            sys.exit("pdfview: give --page and --quote together (or --page and --box), or neither for a general comment.")
        anchor = None
        if a.box:
            anchor = {"page": a.page, "box": a.box, "quote": a.quote}
        elif a.page:
            anchor = {"page": a.page, "quote": a.quote}
        cmd_change(p, {"op": "add", "anchor": anchor, "text": a.text}, a.author)
    elif a.cmd == "crop":
        cmd_crop(p, a.page, a.box, a.out, a.dpi)
    else:
        cmd_text(p, a.page, a.pages, a.box)


SUBCOMMANDS = ("list", "reply", "add", "text", "crop")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "note":  # a note to the app, not about a PDF (viewkit/improvekit.py)
        return improvekit.note_main("pdfview", HERE, sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "try":  # try a proposal in a sandbox: copies of the PDF and its comments
        return improvekit.try_main("pdfview", HERE, sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS:
        return sub_main(sys.argv[1:])
    ap = argparse.ArgumentParser(
        prog="pdfview", description="Read and comment on PDF files in the browser.",
        epilog=f"Subcommands: pdfview {{list,reply,add,text,crop}} FILE ..., pdfview note TEXT, and pdfview try N [FILE] (see pdfview list -h). One shared server runs in "
               f"the background on port {PORT} and can open any file under {ROOT}. Its log is {LOG}.")
    ap.add_argument("target", nargs="*", help="the PDF files or folders to open, each in its own browser tab "
                                              "(default: current folder)")
    ap.add_argument("--no-open", action="store_true", help="print the URL without opening a browser")
    ap.add_argument("--status", action="store_true", help="show whether the server is running")
    ap.add_argument("--stop", action="store_true", help="stop the server")
    ap.add_argument("--restart", action="store_true", help="restart the server")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)  # run the server in the foreground
    ap.add_argument("--port", type=int, default=PORT, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.serve:
        return run_server(args.port)
    if args.status:
        info = ping(args.port)
        print(f"pdfview: running on http://127.0.0.1:{args.port}/ (pid {info['pid']}, since {info['started']}, "
              f"root {info['root']})" if info else "pdfview: not running.")
        return
    if args.stop or args.restart:
        stop_server(args.port)
        if args.stop:
            return
        info = ensure_server(args.port)
        print(f"pdfview: running (pid {info['pid']}).")
        return
    failed = False
    for t in args.target or ["."]:
        try:
            open_target(t, args.port, not args.no_open)
        except SystemExit as e:  # report it and carry on with the other files
            print(e.code, file=sys.stderr)
            failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
