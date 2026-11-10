#!/usr/bin/env python3
"""pageview: rich pages in the browser, made from Markdown and data files, with comments and Claude.

A page is a Markdown file with a few extra blocks (pandoc fenced divs): tables and charts from CSV or JSON files,
figures, tabs, toggles, columns, callouts, stat cards, maths, code and small sandboxed widgets. SYNTAX.md lists them.
pageview also opens a CSV, TSV or JSON file directly, as one table that fills the window.

pageview never changes the page or its data. The comments are in a JSON file next to the page,
.pageview/NAME.json:

    {"threads": [{"id": 1, "anchor": {"quote": "the bound is tight", "prefix": "We show that ", "suffix": " for all n",
                                      "line": 12},
                  "msgs": [{"author": "you", "text": "Why?", "ts": 1730000000}]},
                 {"id": 2, "anchor": {"table": "runs.csv", "row": "AdamW", "col": "acc"}, "msgs": [...]}]}

A thread without an anchor is a general comment about the whole page. A text anchor holds the selected words and a few
words before and after them, and the source line of the block. A cell anchor names a table (its data file), a row (the
value in its first column) and a column.

Usage:
    pageview page.md                     open a page (starts the shared server if needed)
    pageview data.csv                    open a data file as a table
    pageview                             browse the current folder
    pageview list FILE [--json]          print the comment threads
    pageview reply FILE ID TEXT          add a reply to thread ID (as claude). TEXT "-" reads it from stdin.
    pageview add FILE TEXT [--quote Q | --cell TABLE ROW COL]   add a comment (as claude). TEXT "-" reads stdin.
    pageview deps FILE                   print the data and image files that the page uses
    pageview note TEXT                   a note to pageview itself (the dashboard of the notes, viewkit/improvekit.py)
    pageview try N [FILE ...]            try the proposal of note N in a sandbox, with copies of the files
    pageview --status | --stop | --restart

One server runs in the background on 127.0.0.1:8768 and serves files under your home folder (PAGEVIEW_ROOT and
PAGEVIEW_PORT override these). It restarts itself when this file or a .py file of ../viewkit changes. Requires Python
3.9+, pandoc, and the folder viewkit next to this one (the code that the viewers share).

Security: the page is inert. Raw HTML in the Markdown shows as text, links and images may only go to safe places, and
the Content-Security-Policy allows only the scripts of the server. A widget block runs in a sandboxed frame with no
network and no access to the server. Every request needs the access key in ~/.cache/pageview/token-PORT, which
`pageview` passes in the link it opens and the browser then keeps as a cookie. The server opens only the files and
folders that you passed to `pageview`, plus the data and image files that such a page uses from its own folder or its
Git project. Hidden files and folders are never read, listed or served.
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
PAGE_FILES = {"viewer.js": "text/javascript; charset=utf-8"}
FILTER = HERE / "page.lua"
ROOT = Path(os.environ.get("PAGEVIEW_ROOT") or Path.home()).expanduser().resolve()
PORT = int(os.environ.get("PAGEVIEW_PORT") or 8768)
STATE_DIR = Path.home() / ".cache" / "pageview"
LOG = STATE_DIR / "server.log"
LOCK_DIR = STATE_DIR / "locks"
FIG_CACHE = STATE_DIR / "fig"
SIDECAR_DIR = ".pageview"  # next to the page
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "Library"}
STARTED = None
TOKEN = None  # the access key, set when the server starts
TEXT_MAX = 20000   # the longest comment
CONTEXT = 40       # the characters of an anchor before and after the quote

PAGE_EXT = {".md", ".markdown"}

sys.path.insert(0, str(HERE.parent / "viewkit"))  # the code that the viewers share
try:
    import artkit
    import claudekit
    import datakit
    import improvekit
    import serverkit
except ImportError:
    sys.exit(f"pageview: the folder viewkit is missing. It must be next to pageview, at {HERE.parent / 'viewkit'}.")
DATA_EXT, MEDIA_TYPES = datakit.DATA_EXT, datakit.MEDIA_TYPES
DOC_EXT = PAGE_EXT | DATA_EXT
DAEMON = serverkit.Daemon("pageview", STATE_DIR, __file__, "PAGEVIEW_PORT")
grants_file, write_private = DAEMON.grants_file, DAEMON.write_private
new_token, read_token = DAEMON.new_token, DAEMON.read_token
ping, ensure_server, stop_server = DAEMON.ping, DAEMON.ensure_server, DAEMON.stop_server

# Content-Security-Policy per kind of response. The viewer may run only its own scripts and the kit (with MathJax), talk
# only to this server, and frame only the widget route of this server.
CSP_VIEWER = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
              "media-src 'self'; font-src 'self' data:; connect-src 'self'; frame-src 'self'; base-uri 'none'; "
              "frame-ancestors 'none'; form-action 'none'")
# A widget: its own inline script and styles, in a sandbox (an opaque origin: no cookies, no server data), no network.
CSP_WIDGET = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
              "img-src data: blob:; font-src data:; base-uri 'none'; frame-ancestors 'self'; form-action 'none'")
CSP_INDEX = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
CSP_SVG = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
CSP_NONE = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"


# --------------------------------------------------------------------------
# The comments: a JSON file next to the page
# --------------------------------------------------------------------------

class OpError(Exception):
    pass


def sidecar(p):
    return p.parent / SIDECAR_DIR / (p.name + ".json")


def read_comments(p):
    """(data, version) of the comments of document p. The version is a hash of the file, "" if there is no file."""
    try:
        raw = sidecar(p).read_bytes()
    except FileNotFoundError:
        return {"threads": []}, ""
    version = hashlib.sha1(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("threads"), list):
            raise ValueError("no threads list")
    except (ValueError, UnicodeDecodeError) as e:
        raise OpError(f"{sidecar(p)} is not a valid comments file: {e}")
    return data, version


def write_comments(p, data):
    """Write the comments of document p. With no threads, remove the file (and the folder, if it is empty).
    Returns the new version."""
    f = sidecar(p)
    if not data["threads"]:
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


def comments_text(p):
    try:
        return sidecar(p).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


class file_lock:
    """A lock per document, for the server and the command line, so two writers never mix their changes."""
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


def one_line(v, n):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else "")).strip()[:n]


def clean_anchor(a):
    """A text anchor or a cell anchor, or None for a general comment."""
    if not a:
        return None
    if not isinstance(a, dict):
        raise OpError("Bad anchor.")
    if a.get("table") is not None or a.get("col") is not None:
        out = {k: one_line(a.get(k), 300) for k in ("table", "row", "col")}
        if not out["table"] or not out["col"]:
            raise OpError("A cell comment needs a table and a column.")
        return out
    q = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(a.get("quote") or "")).strip()[:2000]
    if not q:
        raise OpError("Select some text first.")
    out = {"quote": q}
    for k in ("prefix", "suffix"):
        v = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(a.get(k) or ""))
        if v:
            out[k] = v[-CONTEXT:] if k == "prefix" else v[:CONTEXT]
    try:
        line = int(a.get("line"))
        if line > 0:
            out["line"] = line
    except (TypeError, ValueError):
        pass
    return out


def find_thread(data, tid):
    for t in data["threads"]:
        if t.get("id") == tid:
            return t
    raise OpError(f"There is no comment #{tid}.")


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
    """Apply one operation to the comments of document p, under the lock. With `version`, refuse if the file changed
    since. Returns (result, before_text_or_None, new_version)."""
    with file_lock(p):
        data, v = read_comments(p)
        if version is not None and version != v:
            raise OpError("The comments changed. They have been reloaded; please try again.")
        before = comments_text(p)
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


# --------------------------------------------------------------------------
# The page: Markdown to HTML with pandoc
# --------------------------------------------------------------------------

FRONT_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S)


def front_matter(src):
    """The simple keys of the YAML front matter: `key: value`, `key: [a, b]`, or `key:` and `- item` lines.
    pageview uses title, subtitle, date, author, tags, view and toc. Other keys are kept as text."""
    m = FRONT_RE.match(src)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$|^-\s+(.*)$", line)
        if item and key:
            v = (item.group(1) or item.group(2) or "").strip().strip("'\"")
            if not isinstance(out.get(key), list):
                out[key] = []
            out[key].append(v)
            continue
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not kv:
            continue
        key, v = kv.group(1), kv.group(2).strip()
        if v.startswith("[") and v.endswith("]"):
            out[key] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            out[key] = v.strip("'\"") if v else []
    return {k: v for k, v in out.items() if v != []}


def pandoc(args, data):
    try:
        p = subprocess.run(["pandoc", *args], input=data, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError("pandoc is not installed (https://pandoc.org/installing.html).")
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip())
    return p.stdout


def _attr_get(kv, name):
    for k, v in kv:
        if k == name:
            return v
    return None


def walk_ast(node, fn):
    """Call fn on every dict node of the pandoc JSON tree, in document order (parents before children)."""
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            walk_ast(v, fn)
    elif isinstance(node, list):
        for v in node:
            walk_ast(v, fn)


def is_relative(u):
    return bool(u) and not re.match(r"^[A-Za-z][\w+.-]*:", u) and not u.startswith(("/", "#"))


_render_cache = {}


def render_page(src, rel):
    """(html, widgets, deps) for the Markdown text `src` of the page at `rel` (relative to the root).
    widgets: the code of each widget block, in order. deps: the relative paths of the data and image files it uses."""
    key = (rel, hashlib.sha1(src.encode("utf-8")).hexdigest())
    if key in _render_cache:
        return _render_cache[key]
    ast = json.loads(pandoc(["-f", "commonmark_x-raw_attribute+sourcepos", "-t", "json"], src.encode("utf-8")))
    widgets, deps = [], []
    file_url = lambda s: f"/file?f={quote(rel)}&src={quote(s)}"

    def fix(n):
        t = n.get("t")
        if t == "Image":  # [attr, alt, [src, title]]
            target = n["c"][2]
            if is_relative(target[0]):
                src = unquote(target[0].split("#")[0])
                deps.append(src)
                target[0] = file_url(src) + ("&as=png" if src.lower().endswith(".pdf") else "")
        elif t == "Link":
            target = n["c"][2]
            if is_relative(target[0]):  # another page, a data file, or an image: the server decides (/go)
                path, _, frag = target[0].partition("#")
                target[0] = f"/go?f={quote(rel)}&src={quote(unquote(path))}" + (f"#{frag}" if frag else "")
        elif t == "Div":
            a = _attr_get(n["c"][0][2], "src")
            if a and is_relative(a):
                deps.append(a)
        elif t == "CodeBlock":  # [[id, classes, kv], text]
            attr = n["c"][0]
            if "widget" in attr[1]:
                attr[2] = [kv for kv in attr[2] if kv[0] != "i"] + [["i", str(len(widgets))]]
                widgets.append(n["c"][1])

    walk_ast(ast["blocks"], fix)
    out = pandoc(["-f", "json", "-t", "html", "--math-method=mathjax", "--wrap=none", "--syntax-highlighting=none",
                  "--lua-filter", str(FILTER)], json.dumps(ast).encode("utf-8")).decode("utf-8")
    res = (out, widgets, sorted(set(deps)))
    if len(_render_cache) > 32:
        _render_cache.clear()
    _render_cache[key] = res
    return res


def read_page(p):
    raw = p.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# --------------------------------------------------------------------------
# Data files: CSV, TSV, JSON and JSON Lines, as columns and rows
# --------------------------------------------------------------------------

def read_table(p):
    """{"columns": [...], "rows": [[...], ...]} of a data file (viewkit/datakit.py). Raises OpError."""
    try:
        return datakit.read_table(p)
    except datakit.DataError as e:
        raise OpError(str(e))


def view_settings(p):
    """The optional settings of a data file that is opened alone: NAME.view.json next to it."""
    f = p.with_name(p.stem + ".view.json")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def pdf_figure(p):
    """A PNG of the first page of the PDF p (viewkit/datakit.py), or None."""
    return datakit.pdf_figure(p, FIG_CACHE)


# --------------------------------------------------------------------------
# Chat: a conversation with Claude about one page (the panel at the bottom right)
# --------------------------------------------------------------------------
# One `claude -p` process per message, all in one session per page. The page polls for the answer as it grows.

CHAT_DIR = STATE_DIR / "chat"
CHAT_TOOLS = ["Read", "Grep", "Glob", "Edit", "Write", "Bash(pageview:*)", "WebSearch", "WebFetch"]
CHAT_TOOLS_ASK = ["Read", "Grep", "Glob", "Bash(pageview list:*)", "WebSearch", "WebFetch"]
CHAT_DENY_ASK = ["Edit", "Write", "NotebookEdit", "Bash(pageview reply:*)", "Bash(pageview add:*)"]
CHAT_SYSTEM = """You are in the chat panel of pageview, a viewer for rich pages made from Markdown and data files. \
The document is the file <FILE>.
The user sees your reply in a narrow panel, so keep it short and plain. Maths in $...$ and $$...$$ is typeset there.
- A page is Markdown with extra blocks (tables and charts from CSV or JSON files, figures, tabs, toggles, columns, \
callouts, stat cards, widgets). The syntax is in ~/tools/viewers/pageview/SYNTAX.md. Read it before you change a page.
- The comments of the user are not in the page. `pageview list <FILE>` prints them. Answer a comment with
  `pageview reply <FILE> ID - <<'EOF'` and the text on the next lines, then EOF (the quoted EOF keeps $ and quotes as \
they are). Add a comment with `pageview add <FILE> - --quote 'exact words of the page' <<'EOF'` in the same way, or \
with --cell TABLE ROW COL for a table cell. Do this only when the user asks you to answer or add comments.
- Never edit the comments file. Change the page or its data files only when the user asks you to.
- The user may tell you which passage or which table cell they selected. Use it to see what "this" means.
- Every message starts with a line in brackets. It tells you the mode of that message. Follow the last one."""
# The mode goes into every message, not into the system prompt: a resumed session keeps the system prompt of its first turn.
CHAT_MODE_EDIT = ("(Mode: you may change the page and its data files, and answer or add comments, when the user asks. "
                  "Answer questions without changing anything.)")
CHAT_MODE_ASK = "(Mode: Ask only. Do not change any file. Do not add or answer comments. Read and answer.)"
CHAT_QUOTE_MAX = 1500


def claude_exe():
    exe = os.environ.get("PAGEVIEW_CLAUDE") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    if not os.access(exe, os.X_OK):
        raise RuntimeError("Can't find the claude command. Set PAGEVIEW_CLAUDE to its path and restart pageview.")
    return exe


# The Oracle: a background `claude -p "/answer-page FILE"` run that answers the open comments (claudekit.Oracle).
ORACLE_TOOLS = ["Read", "Grep", "Glob", "Bash(pageview:*)", "WebSearch", "WebFetch"]
ORACLE = claudekit.Oracle(STATE_DIR / "claude", claude_exe, lambda p: f'/answer-page "{p}"', ORACLE_TOOLS,
                          disallowed=["Edit", "Write", "NotebookEdit"])


def file_state(p):
    try:
        st = p.stat()
        return f"{st.st_mtime_ns}-{st.st_size}"
    except OSError:
        return ""


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

    def send(self, text, quote_, where, ask_only):
        """Start one turn. Returns None, or a message that says why it cannot start."""
        with self.lock:
            if self.cur is not None:
                return "Claude is still answering."
            if ORACLE.busy(self.path):
                return "The Oracle is answering the comments of this page. Wait for it, or stop it."
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
            threading.Thread(target=self.run, args=(exe, text, quote_, where, ask_only), daemon=True).start()
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

    def run(self, exe, text, quote_, where, ask_only):
        p = self.path
        before, page_before = comments_text(p), file_state(p)
        ctx = ""
        quote_ = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", quote_ or "").strip()[:CHAT_QUOTE_MAX]
        if quote_:
            ctx += "(The user has selected this: " + json.dumps(quote_, ensure_ascii=False) + ")\n"
        if where:
            ctx += f"(The user looks at the section: {one_line(where, 200)})\n"
        mode = (CHAT_MODE_ASK if ask_only else CHAT_MODE_EDIT) + "\n"
        with self.lock:
            sid, started = self.data["session"], self.data.get("started")
        if ask_only:
            allowed, denied, perm = CHAT_TOOLS_ASK, CHAT_DENY_ASK, None
        else:
            allowed, denied, perm = CHAT_TOOLS, ["NotebookEdit"], "acceptEdits"
        cmd = claudekit.build_command(exe, mode + ctx + ("\n" if ctx else "") + text,
                                      CHAT_SYSTEM.replace("<FILE>", str(p)), sid, started, allowed, denied, perm)
        err, stderr, code = claudekit.run_stream(cmd, p.parent, claudekit.clean_env(doc=p), self.event, self.set_proc)
        after = comments_text(p)
        changed = after != before
        if changed and Handler.app is not None:  # one Undo takes back the comments that this turn changed
            Handler.app.push_undo(p, before)
        with self.lock:
            cur = self.cur
            cur["changed"] = changed
            cur["pageChanged"] = file_state(p) != page_before
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
    return any(part.startswith(".") for part in Path(rel).parts)


def project_of(folder):
    """The folder of the Git repo that holds `folder`, or None. It looks up to the home folder."""
    home = Path.home()
    for d in [folder, *folder.parents]:
        if (d / ".git").exists():
            return d
        if d == home or d == d.parent:
            return None
    return None


NOT_GRANTED = "pageview can only open files and folders you have passed to it. To open this one, run: pageview {}"


class App:
    def __init__(self, root, grants_file=None):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        self.undo = {}  # path -> list of (version after the change, comments text before it, or None)
        self.oracle_before = {}  # path -> the comments text when the Oracle started, until its end is seen
        self.grants_file = grants_file
        self.granted = set()  # files and folders passed to the pageview command
        self.opened = set()   # the documents that pages opened: same names get longer labels
        self.deps = {}        # page path -> the relative paths of the files it uses (from its last render)
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
        """True if p was passed to pageview, or is inside a folder that was."""
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

    def dep(self, doc, src):
        """A file that the page `doc` uses (a data file or an image), by its path relative to the page, or None.
        It must be under the root and not hidden, and in the folder of the page, its Git project, or a granted folder."""
        if not src or src.startswith("/") or "\x00" in src:
            return None
        p = (doc.parent / src).resolve()
        if p != self.root and self.root not in p.parents:
            return None
        if is_hidden(p.relative_to(self.root)) or not p.is_file():
            return None
        home = project_of(doc.parent) or doc.parent
        if p.parent == doc.parent or doc.parent in p.parents or home in p.parents or self.allowed(p):
            return p
        return None

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
    server_version = "pageview"
    app: App = None

    def log_message(self, *args):
        pass

    # ----- helpers -----
    def host_ok(self):
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host.lower() in serverkit.local_hosts("pageview")

    def cookie_name(self):
        return f"pageview{self.server.server_address[1]}"  # cookies ignore ports, so name it per port

    def authorized(self):
        """The request carries the access key, as the cookie set when the link was opened."""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.cookie_name() and TOKEN and hmac.compare_digest(value, TOKEN):
                return True
        return False

    def deny(self, api):
        msg = "This page's access key is missing or out of date. Reopen the file with the pageview command."
        if api:
            return self.send_json(403, {"error": msg})
        return self.send(403, f"<!doctype html><meta charset=utf-8><title>pageview</title>"
                              f"<p style='font:15px system-ui,sans-serif;margin:32px'>{msg}</p>", csp=CSP_INDEX)

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
        if frame == "DENY":
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, obj):
        self.send(code, json.dumps(obj, ensure_ascii=False, allow_nan=False, default=str),
                  "application/json; charset=utf-8")

    def doc_path(self, q):
        """(path, rel, None) for a document that may be opened, else (None, rel, (code, error))."""
        rel = (q.get("f") or [""])[0]
        p = self.app.resolve(rel)
        if not p or p.suffix.lower() not in DOC_EXT:
            return None, rel, (404, f"File not found: {rel}")
        if not self.app.allowed(p):
            return None, rel, (403, NOT_GRANTED.format(p))
        return p, self.app.rel(p), None

    def payload(self, p, rel):
        data, version = read_comments(p)
        label = serverkit.doc_label(p, self.app.opened)  # a short name for the tab and the header
        self.app.opened.add(p)
        out = {"name": p.name, "label": label, "rel": rel, "path": str(p), "dir": self.app.rel(p.parent),
               "dirOk": self.app.allowed(p.parent), "threads": threads_json(data), "version": version,
               "doc": file_state(p), "canUndo": self.can_undo(p, version)}
        if p.suffix.lower() in PAGE_EXT:
            src = read_page(p)
            page, widgets, deps = render_page(src, rel)
            self.app.deps[str(p)] = deps
            out.update(kind="page", html=page, meta=front_matter(src), src=src, widgets=len(widgets))
        else:
            self.app.deps[str(p)] = []
            out.update(kind="data", settings=view_settings(p))
        out["deps"] = self.deps_state(p)
        return out

    def deps_state(self, p):
        """One value that changes when a file that the page uses changes."""
        parts = []
        for src in self.app.deps.get(str(p), []):
            d = self.app.dep(p, src)
            parts.append(f"{src}:{file_state(d) if d else ''}")
        if p.suffix.lower() in DATA_EXT:
            parts.append(file_state(p.with_name(p.stem + ".view.json")))
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]

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
            return self.send_json(200, {"app": "pageview", "pid": os.getpid(), "root": str(app.root),
                                        "started": STARTED, "code": str(Path(__file__).resolve())})

        # A link from the pageview command carries the key as ?t=. Keep it as a cookie and reload without it, so it
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
        if url.path[1:] in PAGE_FILES:
            return self.send(200, (HERE / url.path[1:]).read_bytes(), PAGE_FILES[url.path[1:]])
        if url.path.startswith("/kit/"):  # the page code that the viewers share
            a = serverkit.kit_asset(unquote(url.path[len("/kit/"):]))
            return self.send(200, a[0], a[1]) if a else self.send(404, "Not found")
        if url.path == "/api/art/scores":  # the watermark (viewkit/artkit.py)
            return self.send_json(200, artkit.scores())
        if url.path.startswith("/art/"):  # a watermark image, or the manifest
            a = artkit.file(unquote(url.path[len("/art/"):]))
            return self.send(200, a[0], a[1]) if a else self.send(404, "Not found")

        if url.path in ("/api/doc", "/api/version", "/api/chat", "/api/claude", "/api/data", "/file", "/go",
                        "/api/account", "/widget", "/api/improve/dash", "/api/improve/preview"):
            p, rel, err = self.doc_path(q)
            if err:
                return self.send_json(err[0], {"error": err[1]})
            if url.path == "/api/version":
                try:
                    return self.send_json(200, {"version": read_comments(p)[1], "doc": file_state(p),
                                                "deps": self.deps_state(p)})
                except OpError as e:
                    return self.send_json(500, {"error": str(e)})
            if url.path == "/api/data":  # a data file of the page, or the document itself (src empty)
                src = (q.get("src") or [""])[0]
                d = app.dep(p, src) if src else p
                if not d or d.suffix.lower() not in DATA_EXT:
                    return self.send_json(404, {"error": f"Data file not found: {src or rel}"})
                try:
                    return self.send_json(200, {"src": src or p.name, **read_table(d)})
                except OpError as e:
                    return self.send_json(400, {"error": str(e)})
                except (OSError, ValueError) as e:
                    return self.send_json(500, {"error": f"Cannot read {src}: {e}"})
            if url.path == "/file":  # an image, a video or a PDF figure of the page
                src = (q.get("src") or [""])[0]
                d = app.dep(p, src)
                ext = d.suffix.lower() if d else ""
                if not d or ext not in MEDIA_TYPES:
                    return self.send(404, "Not found", "text/plain; charset=utf-8")
                if ext == ".pdf" and (q.get("as") or [""])[0] == "png":
                    png = pdf_figure(d)
                    return self.send(200, png.read_bytes(), "image/png") if png else self.send(404, "No figure")
                return self.send(200, d.read_bytes(), MEDIA_TYPES[ext], csp=CSP_SVG if ext == ".svg" else CSP_NONE)
            if url.path == "/go":  # a link in the page to another file: open it in the right way
                src = (q.get("src") or [""])[0]
                d = app.dep(p, src)
                if d and d.suffix.lower() in DOC_EXT:
                    if not app.allowed(d):
                        app.grant(d)  # a page or data file that a page you opened links to, in its own project
                    return self.send(303, "", extra={"Location": "/view?f=" + quote(app.rel(d))})
                if d and d.suffix.lower() in MEDIA_TYPES:
                    return self.send(303, "", extra={"Location": f"/file?f={quote(rel)}&src={quote(src)}"})
                return self.send(404, f"pageview cannot open this link: {src}", "text/plain; charset=utf-8")
            if url.path == "/widget":  # one widget block of the page, alone, for a sandboxed frame
                return self.widget(p, (q.get("i") or [""])[0], (q.get("theme") or [""])[0])
            if url.path == "/api/improve/dash":  # the dashboard of the notes (self-improving apps)
                return self.send_json(*improvekit.dash_api("pageview", HERE, "GET"))
            if url.path == "/api/improve/preview":  # in the sandbox of a proposal: the card with Accept and Reject
                return self.send_json(*improvekit.preview_api("GET"))
            if url.path == "/api/claude":
                st = ORACLE.status(p)
                app.oracle_seen(p, st)
                return self.send_json(200, st)
            if url.path == "/api/account":  # the Claude Code account of the chat and the Oracle (claudekit)
                return self.send_json(*claudekit.account_api("GET", doc=p))
            if url.path == "/api/chat":
                since = (q.get("since") or [""])[0]
                return self.send_json(200, chat_of(p).snapshot(int(since) if since.isdigit() else None))
            try:
                return self.send_json(200, self.payload(p, rel))
            except (OpError, RuntimeError) as e:
                return self.send_json(500, {"error": str(e)})

        self.send(404, "Not found")

    def widget(self, p, i, theme):
        if p.suffix.lower() not in PAGE_EXT or not i.isdigit():
            return self.send(404, "Not found")
        try:
            widgets = render_page(read_page(p), self.app.rel(p))[1]
        except RuntimeError as e:
            return self.send(500, str(e), "text/plain; charset=utf-8")
        if int(i) >= len(widgets):
            return self.send(404, "Not found")
        dark = theme == "dark"
        base = (f"<!doctype html><html><head><meta charset='utf-8'><style>:root{{color-scheme:{'dark' if dark else 'light'};"
                f"--bg:{'#181b21' if dark else '#ffffff'};--fg:{'#dde0e6' if dark else '#1c1f25'};"
                f"--muted:{'#8d939d' if dark else '#646a74'};--line:{'#2b3038' if dark else '#dcdbd4'};"
                f"--accent:{'#8fa9f6' if dark else '#2c56c9'}}}"
                "html,body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,sans-serif}"
                "body{padding:2px}</style>"
                # tell the page the height of the widget, so the frame fits it
                "<script>addEventListener('load',()=>{const s=()=>parent.postMessage({pvWidgetHeight:"
                "document.documentElement.scrollHeight},'*');new ResizeObserver(s).observe(document.body);s();});"
                "</script></head><body>")
        return self.send(200, base + widgets[int(i)] + "</body></html>", csp=CSP_WIDGET, frame="SAMEORIGIN")

    def do_POST(self):
        # The custom header forces a CORS preflight, so other websites can't post here.
        if not self.host_ok() or self.headers.get("X-PageView") != "1":
            return self.send_json(403, {"error": "Forbidden"})
        if not self.authorized():
            return self.deny(True)
        path = urlparse(self.path).path
        if path not in ("/api/op", "/api/grant", "/api/chat", "/api/account", "/api/claude", "/api/improve/dash",
                        "/api/improve/preview", "/api/art/vote"):
            return self.send_json(404, {"error": "Not found"})
        try:
            op = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if not isinstance(op, dict):
                raise ValueError
        except (ValueError, TypeError):
            return self.send_json(400, {"error": "Bad request"})

        if path == "/api/art/vote":  # a click (+1) or double click (-1) on a watermark figure (viewkit/artkit.py)
            return self.send_json(*artkit.vote_api(op))

        if path == "/api/grant":  # the pageview command: allow a file or folder you passed to it
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

        p, rel, err = self.doc_path({"f": [str(op.get("f", ""))]})
        if err:
            return self.send_json(err[0], {"error": err[1]})

        if path == "/api/improve/dash":  # the dashboard: add, edit or delete a waiting note, or send them all
            if improvekit.preview_of():
                return self.send_json(409, {"error": "This is a preview. Write notes in the live pageview tab."})
            port = self.server.server_address[1]
            back = f"http://{serverkit.app_host('pageview')}:{port}/view?f={quote(rel)}"
            return self.send_json(*improvekit.dash_api("pageview", HERE, "POST", op, p, back))
        if path == "/api/improve/preview":  # the decision in the preview tab: accept, change or reject
            return self.send_json(*improvekit.preview_api("POST", op))

        if path == "/api/account":  # the Claude Code account of the chat and the Oracle (claudekit)
            return self.send_json(*claudekit.account_api("POST", op, p))
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
                err = chat.send(text, str(op.get("quote") or ""), str(op.get("where") or ""), bool(op.get("ask_only")))
            return self.send_json(409 if err else 200, {"error": err} if err else {"ok": True})

        if path == "/api/claude":  # the Oracle: start or stop the run of this page, or forget its session
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
        try:
            out = self.payload(p, rel)
        except (OpError, RuntimeError) as e:
            return self.send_json(500, {"error": str(e)})
        if op.get("op") == "add":
            out["added"] = res
        self.send_json(200, out)

    def index_page(self, d):
        """A folder page: subfolders, then pages and data files with their open-comment counts, then the images."""
        rows, imgs = [], []
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
            if e.name.startswith(".") or not e.is_file():
                continue
            ext = e.suffix.lower()
            if ext in DOC_EXT and not e.name.endswith(".view.json"):
                try:
                    n_open = sum(status_of(t) == "open" for t in read_comments(e)[0]["threads"])
                except OpError:
                    n_open = 0
                badge = f'<span class="b">{n_open} open</span>' if n_open else ""
                icon = "📄" if ext in PAGE_EXT else "▦"
                rows.append(f'<li><a href="/view?f={quote(self.app.rel(e))}">{icon} {html.escape(e.name)}</a>{badge}</li>')
            elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp") and len(imgs) < 60:
                imgs.append(e)
        grid = ""
        if imgs:
            # an image of the folder is served as a file of the folder's first page or data file, if there is one
            host = next((e for e in entries if e.is_file() and e.suffix.lower() in DOC_EXT
                         and not e.name.startswith(".")), None)
            if host:
                grid = '<h2>Images</h2><div class="g">' + "".join(
                    f'<figure><img loading="lazy" src="/file?f={quote(self.app.rel(host))}&src={quote(e.name)}" '
                    f'alt=""><figcaption>{html.escape(e.name)}</figcaption></figure>' for e in imgs) + "</div>"
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(d.name or str(d))}/ · pageview</title>
<link rel="icon" type="image/png" href="/kit/icons/page.png">
<style>
:root{{color-scheme:light;--bg:#f6f6f3;--fg:#1c1f25;--muted:#646a74;--accent:#2c56c9;--hl:#fbe7a1;--line:#dcdbd4;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--bg:#111317;--fg:#dde0e6;--muted:#8d939d;--accent:#8fa9f6;--hl:#4b4320;--line:#2b3038;--card:#181b21}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,sans-serif;padding:32px 16px}}
main{{max-width:860px;margin:0 auto}} h1{{font-size:18px;overflow-wrap:anywhere}} h2{{font-size:14px;color:var(--muted);margin-top:28px}}
ul{{padding:0;list-style:none}} li{{padding:5px 0;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center}}
a{{color:var(--accent);text-decoration:none;overflow-wrap:anywhere}} .b{{background:var(--hl);border-radius:10px;padding:0 8px;font-size:12px}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}}
figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px}}
figure img{{width:100%;height:130px;object-fit:contain;display:block}} figcaption{{font-size:12px;color:var(--muted);overflow-wrap:anywhere}}
</style></head><body><main><h1>{html.escape(str(d))}</h1><ul>{"".join(rows)}</ul>{grid}</main></body></html>"""


def run_server(port):
    """Serve in the foreground. Restarts itself when this file changes."""
    global STARTED, TOKEN
    STARTED = time.strftime("%Y-%m-%d %H:%M:%S")
    TOKEN = read_token(port) or new_token(port)  # kept across code-change restarts, so open tabs keep working
    Handler.app = App(ROOT, grants_file(port))  # grants survive code-change restarts
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        sys.exit(f"pageview: can't listen on port {port}: {e}")
    DAEMON.serve(server, port, f"pageview: serving {ROOT} at http://127.0.0.1:{port}/ (pid {os.getpid()}, {STARTED})")


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def open_target(target, port, open_browser):
    p = Path(target).expanduser().resolve()
    if not p.exists():
        sys.exit(f"pageview: not found: {target}")
    if p.is_file() and p.suffix.lower() not in DOC_EXT:
        sys.exit(f"pageview: not a page or a data file (.md, .csv, .tsv, .json, .jsonl): {target}")
    root = Path(ensure_server(port)["root"])  # the running server's root, which may predate PAGEVIEW_ROOT
    if p != root and root not in p.parents:
        sys.exit(f"pageview: {p} is outside {root}, the folder the server can open.\n"
                 f"Restart it with a wider root, e.g.: PAGEVIEW_ROOT=/ pageview --restart")
    if is_hidden(p.relative_to(root)):
        sys.exit(f"pageview: {p} is in a hidden folder (or is a hidden file). pageview never opens those, to keep\n"
                 f"folders like ~/.ssh and ~/.claude out of reach. Copy the file somewhere else to read it.")
    rel = p.relative_to(root).as_posix() if p != root else ""
    token = read_token(port)
    if not token:
        sys.exit("pageview: the server's access key is missing. Restart it: pageview --restart")
    grant(port, token, p)
    url = f"http://{serverkit.app_host('pageview')}:{port}/" + (f"view?f={quote(rel)}" if p.is_file() else f"?d={quote(rel)}")
    url += "&t=" + token
    print(url)
    if open_browser:
        webbrowser.open(url)


def grant(port, token, path):
    """Allow the server to open `path` (a file or a folder and everything in it)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/grant", data=json.dumps({"path": str(path)}).encode(),
        headers={"Content-Type": "application/json", "X-PageView": "1", "Cookie": f"pageview{port}={token}"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except ValueError:
            msg = e.reason
        sys.exit(f"pageview: the server won't open {path}: {msg}")


def doc_arg(s):
    p = Path(s).expanduser().resolve()
    if not p.is_file() or p.suffix.lower() not in DOC_EXT:
        sys.exit(f"pageview: not a page or a data file: {s}")
    return p


def cmd_list(p, as_json):
    threads = threads_json(read_comments(p)[0])
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
            where = (f'line {a["line"]}  ' if a.get("line") else "") + f'"{q[:70] + "…" if len(q) > 70 else q}"'
        else:
            where = "general"
        print(f'#{t["id"]}  {where}  [{t["status"]}]')
        for m in t["msgs"]:
            print(f'    {m["author"]}: {m["text"]}')


def cmd_change(p, op, author):
    try:
        res = change(p, op, author)[0]
    except OpError as e:
        sys.exit(f"pageview: {e}")
    print(f"pageview: {'added comment #' + str(res) if op['op'] == 'add' else 'replied to #' + str(res)}.")


def sub_main(argv):
    """The subcommands for Claude and scripts: list, reply, add."""
    ap = argparse.ArgumentParser(prog="pageview")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list", help="print the comment threads")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("reply", help="add a reply to a thread")
    s.add_argument("file")
    s.add_argument("id", type=int)
    s.add_argument("text", help='the reply, or "-" to read it from stdin')
    s.add_argument("--author", default="claude")
    s = sub.add_parser("add", help="add a comment: general, on words of the page, or on a table cell")
    s.add_argument("file")
    s.add_argument("text", help='the comment, or "-" to read it from stdin')
    g = s.add_mutually_exclusive_group()
    g.add_argument("--quote", help="the exact words of the page that the comment is about")
    g.add_argument("--cell", nargs=3, metavar=("TABLE", "ROW", "COL"),
                   help="a table cell: the data file of the table, the value in its first column, and the column name")
    s.add_argument("--line", type=int, help="with --quote: the source line of the block, if the words occur more than once")
    s.add_argument("--author", default="claude")
    a = ap.parse_args(argv)
    p = doc_arg(a.file)
    if getattr(a, "text", None) == "-":  # the text from stdin: safe for $, quotes and new lines
        a.text = sys.stdin.read()
    if a.cmd == "list":
        cmd_list(p, a.json)
    elif a.cmd == "reply":
        cmd_change(p, {"op": "reply", "thread": a.id, "text": a.text}, a.author)
    else:
        anchor = None
        if a.quote:
            anchor = {"quote": a.quote, "line": a.line}
        elif a.cell:
            anchor = {"table": a.cell[0], "row": a.cell[1], "col": a.cell[2]}
        cmd_change(p, {"op": "add", "anchor": anchor, "text": a.text}, a.author)


SUBCOMMANDS = ("list", "reply", "add")


def cmd_deps(argv):
    """`pageview deps FILE`: the files that the page uses (its data files and images), one path for each line, relative
    to the folder of the page. For a data file: its NAME.view.json. The preview of a note copies them (improve.json)."""
    if len(argv) != 1:
        sys.exit("usage: pageview deps FILE")
    p = Path(argv[0]).expanduser().resolve()
    if not p.is_file() or p.suffix.lower() not in DOC_EXT:
        sys.exit(f"pageview: not a page or a data file: {argv[0]}")
    if p.suffix.lower() in PAGE_EXT:
        try:
            deps = render_page(read_page(p), p.name)[2]
        except RuntimeError as e:
            sys.exit(f"pageview: {e}")
    else:
        deps = [p.stem + ".view.json"]
    for d in deps:
        if not is_hidden(Path(d)) and (p.parent / d).is_file():
            print(d)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "note":  # a note to the app, not about a page (viewkit/improvekit.py)
        return improvekit.note_main("pageview", HERE, sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "try":  # try a proposal in a sandbox: copies of the page and its data
        return improvekit.try_main("pageview", HERE, sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "deps":  # the files that a page uses (for the preview of a note)
        return cmd_deps(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS:
        return sub_main(sys.argv[1:])
    ap = argparse.ArgumentParser(
        prog="pageview", description="Rich pages from Markdown and data files, in the browser.",
        epilog=f"Subcommands: pageview {{list,reply,add}} FILE ... (see pageview add -h), pageview note TEXT, and "
               f"pageview try N [FILE]. One shared server runs in "
               f"the background on port {PORT} and can open any file under {ROOT}. Its log is {LOG}.")
    ap.add_argument("target", nargs="*", help="the pages, data files or folders to open, each in its own browser tab "
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
        print(f"pageview: running on http://127.0.0.1:{args.port}/ (pid {info['pid']}, since {info['started']}, "
              f"root {info['root']})" if info else "pageview: not running.")
        return
    if args.stop or args.restart:
        stop_server(args.port)
        if args.stop:
            return
        info = ensure_server(args.port)
        print(f"pageview: running (pid {info['pid']}).")
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
