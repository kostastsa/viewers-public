#!/usr/bin/env python3
"""pyview: run and read Python cells in the browser, built to work with Claude Code.

The master copy is a plain .py file. Cells start at a line `# %%`; a markdown cell starts at
`# %% [markdown]` and holds `# `-prefixed text. Comments are lines that start with `#>`:

    # %% Load data
    #> why is this slow?
    #> @claude: read_csv parses the dates here.
    df = pd.read_csv("big.csv", parse_dates=["t"])

Outputs are not in the file. They are saved next to it in .pyview/ (JSON and PNG files).

Usage:
    pyview notes.py                  open a file in the browser (and allow it to run code)
    pyview run notes.py --cell 3     run cell 3 and print its output (--all, --from N, --to N)
    pyview out notes.py [--cell N]   print the saved output without running
    pyview notes.py --list [--json]  print the #> comment threads
    pyview restart|interrupt notes.py
    pyview --remote HOST notes.py    start the viewer on another computer (over SSH) and open it here
    pyview note TEXT                 a note to pyview itself: a problem or a wish (a GitHub issue). "-": stdin.
    pyview try N [notes.py]          try the proposal of note N in a sandbox (port 8900+N, copies of the files)
    pyview --setup                   build the shared interpreter, ~/tools/viewers/pyview/.venv
    pyview --status | --stop | --restart

One server runs in the background on 127.0.0.1:8766 (PYVIEW_PORT) and serves files under your
home folder (PYVIEW_ROOT). It restarts itself when this file or a .py file of ../viewkit changes. Requires Python 3.9+
and the folder viewkit next to this one (the code that mdview and pyview share).

To use it from another computer, set PYVIEW_BIND=tailscale (or an IP address) where the server runs: it
then listens on that address only, and the link that `pyview FILE --no-open` prints uses it (or the first
name in PYVIEW_NAMES, for example mac-mini). Never use 0.0.0.0: a cell runs code, and the network
would see the port.

Security: running a cell runs arbitrary code, so only a file you pass to `pyview` on the command
line can run (or one you trust with the button in the viewer). Every request needs the access
key in ~/.cache/pyview/token-PORT, which `pyview` passes in the link it opens. Hidden files and
folders are never read, listed or saved.
"""
import argparse
import base64
import copy
import difflib
import hashlib
import hmac
import html
import json
import os
import queue
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

HERE = Path(__file__).resolve().parent
VIEWER = HERE / "viewer.html"
VIEWER_JS = HERE / "viewer.js"
VENDOR = HERE / "vendor"
RUNNER = HERE / "runner.py"
SHARED_VENV = HERE / ".venv"
ROOT = Path(os.environ.get("PYVIEW_ROOT") or Path.home()).expanduser().resolve()
PORT = int(os.environ.get("PYVIEW_PORT") or 8766)

STATE_DIR = Path.home() / ".cache" / "pyview"
LOG = STATE_DIR / "server.log"
IDLE_STOP = float(os.environ.get("PYVIEW_IDLE") or 4 * 3600)  # seconds without a run before the runner stops
STREAM_CAP = 200_000    # characters of stdout/stderr kept per cell; the rest goes to a file
STREAM_HARD = 20_000_000
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "Library"}
SETUP_PACKAGES = ["numpy", "pandas", "matplotlib", "scipy", "seaborn", "sympy", "scikit-learn"]
USER = "you"
CLAUDE_TOOLS = ["Read", "Edit", "Grep", "Glob", "Bash(pyview:*)", "Bash(grep:*)"]

CSP_VIEWER = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'self'; "
              "base-uri 'none'; frame-ancestors 'none'")
CSP_INDEX = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
CSP_NONE = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
VENDOR_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".woff": "font/woff", ".woff2": "font/woff2"}
STARTED = None
TOKEN = None

sys.path.insert(0, str(HERE.parent / "viewkit"))  # the code that mdview and pyview share
try:
    import claudekit
    import improvekit
    import serverkit
except ImportError:
    sys.exit(f"pyview: the folder viewkit is missing. It must be next to pyview, at {HERE.parent / 'viewkit'}.")
NET = serverkit.Net("pyview", "PYVIEW", why="a cell runs code")  # PYVIEW_BIND, PYVIEW_NAMES (see viewkit/serverkit.py)
tailscale_ip = serverkit.tailscale_ip
bind_addr, link_host, allowed_hosts = NET.bind, NET.link_host, NET.hosts
DAEMON = serverkit.Daemon("pyview", STATE_DIR, __file__, "PYVIEW_PORT", bind=bind_addr, stop_wait=5.0)
token_file, grants_file, write_private = DAEMON.token_file, DAEMON.grants_file, DAEMON.write_private
new_token, read_token = DAEMON.new_token, DAEMON.read_token
ping, port_in_use, ensure_server, stop_server = DAEMON.ping, DAEMON.port_in_use, DAEMON.ensure_server, DAEMON.stop_server


# --------------------------------------------------------------------------
# Cells and comments
# --------------------------------------------------------------------------

MARK_RE = re.compile(r"^#\s?%%(?:[ \t]+(.*?))?[ \t]*$")
MD_TAG_RE = re.compile(r"\[markdown\]", re.I)
HEADER_RE = re.compile(r"^#\s*pyview:\s*python\s*=\s*(.+?)\s*$")
AUTHOR_RE = re.compile(r"^@([A-Za-z][\w-]*):[ \t]?(.*)$", re.S)


class Cell:
    """A cell of the file. `marker` is the line of `# %%` (None for the text before the first marker,
    which is cell 0). The body is lines[bs:be]; the cell, with its blank lines, ends at fe."""
    __slots__ = ("idx", "kind", "title", "marker", "bs", "be", "fe")

    def __init__(self, idx, kind, title, marker, bs, fe):
        self.idx, self.kind, self.title, self.marker, self.bs, self.fe = idx, kind, title, marker, bs, fe
        self.be = fe


def split_lines(text):
    """Lines of the file, with no trailing blank lines (the text is rewritten with one newline)."""
    return text.rstrip("\n").split("\n") if text.strip() else [""]


def parse_cells(lines):
    marks = []
    for i, line in enumerate(lines):
        m = MARK_RE.match(line)
        if m:
            rest = m.group(1) or ""
            kind = "markdown" if MD_TAG_RE.search(rest) else "code"
            marks.append((i, kind, MD_TAG_RE.sub("", rest).strip()))
    cells = []
    first = marks[0][0] if marks else len(lines)
    cells.append(Cell(0, "code", "", None, 0, first))
    for k, (i, kind, title) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        cells.append(Cell(k + 1, kind, title, i, i + 1, end))
    for c in cells:
        be = c.fe
        while be > c.bs and not lines[be - 1].strip():
            be -= 1
        c.be = be
    return cells


def is_comment(line):
    return line.startswith("#>")


def body(lines, c):
    return lines[c.bs:c.be]


def code_lines(lines, c):
    """(absolute line numbers, texts) of the code a cell shows: its body without #> lines."""
    idx = [i for i in range(c.bs, c.be) if not is_comment(lines[i])]
    return idx, [lines[i] for i in idx]


def exec_source(lines, c):
    """What the runner executes. #> lines become blank so that tracebacks keep the editor's line numbers."""
    return "\n".join("" if is_comment(l) else l for l in body(lines, c))


def cell_hash(lines, c):
    text = "\n".join(l for l in body(lines, c) if not is_comment(l)).strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def is_visible(lines, c):
    """Cell 0 is hidden while it holds only comments for the whole file and the interpreter header."""
    if c.idx:
        return True
    return any(l.strip() and not is_comment(l) and not HEADER_RE.match(l) for l in body(lines, c))


def is_runnable(lines, c):
    return c.kind == "code" and any(l.strip() and not is_comment(l) for l in body(lines, c)) and is_visible(lines, c)


def decode_md(ls):
    out = []
    for l in ls:
        if is_comment(l):
            out.append(l)
        elif l.startswith("# "):
            out.append(l[2:])
        elif l.strip() in ("", "#"):
            out.append("")
        else:
            out.append(l)
    return out


def encode_md(ls):
    return [l if is_comment(l) else ("# " + l if l.strip() else "#") for l in ls]


def md_text(lines, c):
    return "\n".join(l for l in decode_md(body(lines, c)) if not is_comment(l))


def editor_text(lines, c):
    ls = body(lines, c)
    return "\n".join(decode_md(ls) if c.kind == "markdown" else ls)


def marker_line(kind, title):
    return "# %%" + (" [markdown]" if kind == "markdown" else "") + (" " + title if title else "")


def python_header(lines):
    for l in lines[:30]:
        m = HEADER_RE.match(l)
        if m:
            return m.group(1)
    return None


def parse_threads(lines, cells):
    """The #> comment threads. A block of #> lines is one thread. Directly under a `# %%` line it
    comments on the whole cell; before the first cell, and followed by a blank line, it is general;
    anywhere else it comments on the line below it."""
    n = len(lines)
    cell_of = [0] * n
    for c in cells:
        for k in range(c.marker if c.marker is not None else 0, c.fe):
            cell_of[k] = c.idx
    threads, i = [], 0
    while i < n:
        if not is_comment(lines[i]):
            i += 1
            continue
        j = i
        while j < n and is_comment(lines[j]):
            j += 1
        msgs = []
        for k in range(i, j):
            text = lines[k][2:]
            text = text[1:] if text.startswith(" ") else text
            m = AUTHOR_RE.match(text)
            if m:
                msgs.append({"author": m.group(1), "text": m.group(2), "line": k, "end": k + 1})
            elif not msgs:
                msgs.append({"author": USER, "text": text, "line": k, "end": k + 1})
            else:
                msgs[-1]["text"] += "\n" + text
                msgs[-1]["end"] = k + 1
        c = cells[cell_of[i]]
        if c.marker is not None and i == c.marker + 1:
            scope, target = "cell", c.marker
        elif c.marker is None and (j >= n or not lines[j].strip() or MARK_RE.match(lines[j])):
            scope, target = "general", j
        else:
            scope, target = "line", j
        threads.append({
            "id": i, "end": j, "cell": c.idx, "scope": scope, "line": target,
            "target": lines[target].strip()[:80] if target < n else "",
            "msgs": msgs, "status": "answered" if msgs[-1]["author"] == "claude" else "open"})
        i = j
    return threads


def message_lines(author, text, first=False):
    parts = text.rstrip("\n").split("\n") or [""]
    head = parts[0] if (first and author == USER) else f"@{author}: {parts[0]}"
    return ["#> " + head] + ["#> " + p for p in parts[1:]]


def drop_thread(lines, th):
    """Remove a thread's lines. A general comment is followed by a blank line; that goes too."""
    end = th["end"]
    if th["scope"] == "general" and end < len(lines) and not lines[end].strip():
        end += 1
    lines[th["id"]:end] = []


def apply_op(text, op):
    """The file's new text after one edit. A pure function, so every edit can be tested without a server."""
    lines = split_lines(text)
    cells = parse_cells(lines)
    kind = op.get("type")

    def cell():
        i = op.get("cell")
        if not isinstance(i, int) or not 0 <= i < len(cells):
            raise ValueError("no such cell")
        return cells[i]

    if kind == "edit-cell":
        c = cell()
        new = str(op.get("text", "")).rstrip("\n").split("\n")
        new = encode_md(new) if c.kind == "markdown" else new
        if c.be == c.bs:
            new = [l for l in new if l.strip()] or new[:0]
        lines[c.bs:c.be] = new
        if c.fe == c.be and c.idx < len(cells) - 1 and new:  # keep a blank line before the next marker
            lines.insert(c.bs + len(new), "")
    elif kind == "add-cell":
        after = op.get("after", len(cells) - 1)
        if not isinstance(after, int) or not 0 <= after < len(cells):
            raise ValueError("no such cell")
        k = "markdown" if op.get("kind") == "markdown" else "code"
        new_body = str(op.get("text", "")).rstrip("\n").split("\n") if op.get("text") else []
        new_body = encode_md(new_body) if k == "markdown" else new_body
        pos = cells[after].fe
        block = [marker_line(k, "")] + new_body
        if pos >= len(lines):
            lines.extend([""] + block)
        else:
            if lines[pos - 1].strip():
                lines.insert(pos, "")
                pos += 1
            lines[pos:pos] = block + [""]
    elif kind == "delete-cell":
        c = cell()
        if c.marker is None:
            lines[c.bs:c.fe] = []
        else:
            lines[c.marker:c.fe] = []
    elif kind == "move-cell":
        c = cell()
        j = c.idx + (1 if op.get("dir", 1) > 0 else -1)
        if c.marker is None or not 1 <= j < len(cells):
            raise ValueError("cannot move this cell")
        a, b = (c, cells[j]) if j > c.idx else (cells[j], c)
        tail = lines[b.be:b.fe] or ([""] if b.fe < len(lines) else [])
        region = lines[b.marker:b.be] + [""] + lines[a.marker:a.be] + tail
        lines[a.marker:b.fe] = region
    elif kind == "split-cell":
        c = cell()
        k = op.get("line")
        if not isinstance(k, int) or not 0 < k < c.be - c.bs:
            raise ValueError("cannot split here")
        pos = c.bs + k
        lines[pos:pos] = ["", marker_line(c.kind, "")] if lines[pos - 1].strip() else [marker_line(c.kind, "")]
    elif kind == "set-type":
        c = cell()
        k = "markdown" if op.get("kind") == "markdown" else "code"
        if c.marker is None:
            raise ValueError("cell 0 has no marker, so it stays code")
        if k != c.kind:
            ls = body(lines, c)
            lines[c.bs:c.be] = encode_md(ls) if k == "markdown" else decode_md(ls)
            lines[c.marker] = marker_line(k, c.title)
    elif kind == "set-title":
        c = cell()
        if c.marker is None:
            raise ValueError("cell 0 has no marker, so it has no title")
        lines[c.marker] = marker_line(c.kind, " ".join(str(op.get("title", "")).split()))
    elif kind == "comment-add":
        target = op.get("line")
        if not isinstance(target, int) or not 0 <= target <= len(lines):
            raise ValueError("bad line")
        scope = op.get("scope", "line")
        block = message_lines(USER, str(op.get("text", "")), first=True)
        if scope == "cell":
            c = cell()
            target = c.marker + 1 if c.marker is not None else 0
        if 0 < target <= len(lines) and is_comment(lines[target - 1]):  # a thread is already there: reply
            block = message_lines(USER, str(op.get("text", "")))
        if scope == "cell":
            c = cell()
            target = c.marker + 1 if c.marker is not None else 0
        elif scope == "general":
            target = 0
            block += [""] if lines and lines[0].strip() else []
        lines[target:target] = block
    elif kind == "comment-resolve-all":
        for th in sorted(parse_threads(lines, cells), key=lambda t: -t["id"]):  # from the bottom, so line numbers hold
            drop_thread(lines, th)
    elif kind in ("comment-reply", "comment-edit", "comment-delete", "comment-resolve"):
        threads = parse_threads(lines, cells)
        key = op.get("thread") if kind != "comment-edit" and kind != "comment-delete" else op.get("line")
        th = next((t for t in threads if (t["id"] == key if kind in ("comment-reply", "comment-resolve")
                                          else any(m["line"] == key for m in t["msgs"]))), None)
        if th is None:
            raise ValueError("no such comment")
        if kind == "comment-reply":
            lines[th["end"]:th["end"]] = message_lines(op.get("author") or USER, str(op.get("text", "")))
        elif kind == "comment-resolve":
            drop_thread(lines, th)
        else:
            m = next(m for m in th["msgs"] if m["line"] == key)
            first = m is th["msgs"][0]
            lines[m["line"]:m["end"]] = message_lines(m["author"], str(op.get("text", "")), first) if kind == "comment-edit" else []
    else:
        raise ValueError(f"unknown edit: {kind}")
    return "\n".join(lines).rstrip("\n") + "\n"


# --------------------------------------------------------------------------
# Files and the sidecar (.pyview/NAME.json and .pyview/NAME/)
# --------------------------------------------------------------------------

def read_text(path):
    data = path.read_bytes()
    return data.decode("utf-8"), hashlib.sha1(data).hexdigest()


def write_text(path, text):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def is_hidden(rel):
    return any(part.startswith(".") for part in Path(rel).parts)


def sidecar_json(path):
    return path.parent / ".pyview" / (path.name + ".json")


def sidecar_dir(path):
    return path.parent / ".pyview" / path.name


def ensure_sidecar_dir(path):
    d = sidecar_dir(path)
    d.mkdir(parents=True, exist_ok=True)
    ignore = d.parent / ".gitignore"  # the folder ignores itself, so no repository file changes
    if not ignore.exists():
        ignore.write_text("*\n")
    return d


def load_sidecar(path):
    try:
        data = json.loads(sidecar_json(path).read_text(encoding="utf-8"))
        return data["cells"] if isinstance(data.get("cells"), list) else []
    except (OSError, ValueError, KeyError, AttributeError):
        return []


def save_sidecar(path, entries):
    ensure_sidecar_dir(path)
    tmp = sidecar_json(path).with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "cells": entries}), encoding="utf-8")
    os.replace(tmp, sidecar_json(path))
    used = {o["file"] for e in entries if e for o in e["outputs"] if o.get("file")}
    used |= {e["full"] for e in entries if e and e.get("full")}
    for f in sidecar_dir(path).glob("*"):  # delete the files no saved output refers to
        if f.name not in used:
            f.unlink(missing_ok=True)


def align(stored, hashes):
    """Match the saved entries to the current code cells: an identical hash keeps its output;
    an edited cell takes the output at its place in the diff (state 'edited'); a new cell gets none."""
    sh = [e["hash"] if e else None for e in stored]
    out = [(None, None)] * len(hashes)
    sm = difflib.SequenceMatcher(None, sh, hashes, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                if stored[i1 + k]:
                    out[j1 + k] = (stored[i1 + k], "same")
        elif tag == "replace":
            for k in range(min(i2 - i1, j2 - j1)):
                if stored[i1 + k]:
                    out[j1 + k] = (stored[i1 + k], "edited")
    return out


def shorten(text, limit):
    """The first and last part of a long text, so that Claude's context is not flooded."""
    if limit <= 0 or len(text) <= limit:
        return text
    half = limit // 2
    return (text[:half] + f"\n[... {len(text) - limit:,} characters left out. Use --max-chars 0 for all of it. ...]\n"
            + text[-half:])


def outputs_text(outputs, path, limit=0):
    """The text form of a cell's output, for people and for Claude."""
    parts = []
    for o in outputs:
        t = o["type"]
        if t == "stream":
            parts.append(shorten(o["text"], limit))
            if not o["text"].endswith("\n"):
                parts.append("\n")
        elif t == "result":
            parts.append("Out: " + shorten(o["text"], limit) + "\n")
        elif t == "image":
            parts.append(f"[image {o['w']}x{o['h']}: {sidecar_dir(path) / o['file']}]  {o.get('text', '')}\n")
        elif t == "error":
            parts.append(shorten(o["text"], limit) if o["text"].endswith("\n") else shorten(o["text"], limit) + "\n")
        elif t == "note":
            parts.append(f"[{o['text']}]\n")
    return "".join(parts)


# --------------------------------------------------------------------------
# Finding the interpreter
# --------------------------------------------------------------------------

def find_python(path, lines, override=None):
    """(interpreter, where it came from), or (None, message). Order: PYVIEW_PYTHON, the file's
    `# pyview: python=` line, the nearest .venv above the file, the shared ~/tools/viewers/pyview/.venv."""
    def ok(p):
        p = Path(p).expanduser()
        return p if p.is_file() and os.access(p, os.X_OK) else None

    if os.environ.get("PYVIEW_PYTHON"):
        p = ok(os.environ["PYVIEW_PYTHON"])
        return (p, "PYVIEW_PYTHON") if p else (None, f"PYVIEW_PYTHON={os.environ['PYVIEW_PYTHON']} is not an interpreter")
    for src, name in ((override, "--python"), (python_header(lines), "the # pyview: line")):
        if src:
            p = ok(src)
            return (p, name) if p else (None, f"{src} (from {name}) is not an interpreter")
    for d in [path.parent, *path.parent.parents]:
        p = ok(d / ".venv" / "bin" / "python")
        if p:
            return p, f"{d}/.venv"
    p = ok(SHARED_VENV / "bin" / "python")
    if p:
        return p, "the shared pyview .venv"
    return None, "no interpreter: there is no .venv above the file and no shared one. Run: pyview --setup"


# --------------------------------------------------------------------------
# Chat: a conversation with Claude about one notebook (the panel at the bottom right)
# --------------------------------------------------------------------------

CHAT_TOOLS_EDIT = ["Read", "Edit", "Grep", "Glob", "Bash(pyview:*)", "Bash(grep:*)"]
CHAT_TOOLS_ASK = ["Read", "Grep", "Glob", "Bash(pyview out:*)", "Bash(grep:*)"]
CHAT_SYSTEM = """You are in the chat panel of pyview, a viewer for Python cells. The notebook is the file {file} (a .py file).
The user sees your reply in a small panel, so keep it short and plain.
- A cell starts at a line `# %%`. A markdown cell starts at `# %% [markdown]` and its text lines start with `# `.
- A line that starts with `#>` is a comment. Keep every `#>` line where it is. Reply to a comment with a line `#> @claude: ...`.
- Do not change or delete the `# %%` line of a cell that exists. Edit the file directly with the Edit tool.
- `pyview out {file}` prints the saved output of the cells. `pyview run {file} --cell N` runs one cell and prints its output.
  Run the cells that you changed, and look at the result before you tell the user that it works.
- The cells share one namespace in one runner process. Outputs are not in the file.
- Every message starts with a line in brackets. It tells you the mode of that message. Follow the last one."""
# The mode goes into every message, not into the system prompt: a resumed session keeps the system prompt of its first turn.
CHAT_MODE_EDIT = "(Mode: you may edit the file and run cells when the user asks for a change. Answer questions without changing anything.)"
CHAT_MODE_ASK = "(Mode: Ask only. Do not edit the file and do not run cells. Read the file and the saved output, and answer.)"


def chat_file(path):
    return path.parent / ".pyview" / (path.name + ".chat.json")


def new_chat():
    return {"session": str(uuid.uuid4()), "started": False, "messages": []}


def load_chat(path):
    try:
        d = json.loads(chat_file(path).read_text(encoding="utf-8"))
        if isinstance(d.get("messages"), list) and d.get("session"):
            d.setdefault("started", False)
            return d
    except (OSError, ValueError, AttributeError):
        pass
    return new_chat()


def save_chat(path, data):
    ensure_sidecar_dir(path)
    f = chat_file(path)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, f)


# --------------------------------------------------------------------------
# A session: the runner of one file, and its queue of runs
# --------------------------------------------------------------------------

def rss_mb(pid):
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=2).stdout
        return round(int(out.strip()) / 1024)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


class Session:
    def __init__(self, path, app=None):
        self.path = path
        self.app = app
        self.chat = None        # the saved conversation (loaded when first needed)
        self.chat_cur = None    # the answer that is being written, or None
        self.chat_proc = None
        self.chat_stop_flag = False
        self.cseq = 0           # numbers the chat events, apart from the cell events
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        self.proc = None
        self.session_id = None
        self.counter = 0
        self.queue = deque()
        self.current = None   # the cell that is running: idx, hash, n, outputs so far
        self.batches = {}
        self.subs = []
        self.events = None
        self.python = None
        self.override = None
        self.last_active = time.time()
        self.worker = None
        self.seq = 0          # numbers the events; a page uses it to tell which events a document already holds

    # ----- events to the pages that watch this file -----
    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def publish(self, ev):
        with self.lock:
            self.seq += 1
            ev["seq"] = self.seq
            for q in self.subs:
                q.put(ev)

    def state(self):
        with self.lock:
            alive = self.proc is not None and self.proc.poll() is None
            st = "running" if self.current else ("idle" if alive else "stopped")
            return {"state": st, "queue": len(self.queue), "python": str(self.python) if self.python else None,
                    "mem": rss_mb(self.proc.pid) if alive else None,
                    "running": self.current["idx"] if self.current else None,
                    "queued": [it["idx"] for it in self.queue]}

    def push_state(self):
        with self.lock:  # read the state and number the event in one step, or an old state could get a newer number
            self.publish({"t": "state", **self.state()})

    # ----- the runner process -----
    def start_proc(self):
        lines = split_lines(read_text(self.path)[0])
        py, why = find_python(self.path, lines, self.override)
        if py is None:
            raise RuntimeError(why)
        r, w = os.pipe()
        env = dict(os.environ, MPLBACKEND="Agg", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        env.pop("PYTHONSTARTUP", None)
        proc = subprocess.Popen(
            [str(py), "-u", str(RUNNER), "--fd", str(w), "--file", str(self.path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(w,),
            env=env, cwd=str(self.path.parent), start_new_session=True, text=True, encoding="utf-8", bufsize=1)
        os.close(w)
        q = queue.Queue()
        self.events = q

        def read_events():
            with os.fdopen(r, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        q.put(json.loads(line))
                    except ValueError:
                        pass
            q.put({"ev": "eof"})

        def read_raw(pipe, name):  # what C code and child processes print, outside Python's streams
            try:
                while True:
                    chunk = os.read(pipe.fileno(), 65536)
                    if not chunk:
                        break
                    q.put({"ev": "stream", "name": name, "text": chunk.decode("utf-8", "replace"), "raw": True})
            except OSError:
                pass

        threading.Thread(target=read_events, daemon=True).start()
        threading.Thread(target=read_raw, args=(proc.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=read_raw, args=(proc.stderr, "stderr"), daemon=True).start()
        self.proc, self.python = proc, py
        self.session_id = uuid.uuid4().hex[:8]
        self.counter = 0
        try:
            ev = q.get(timeout=30)
        except queue.Empty:
            ev = {}
        if ev.get("ev") != "ready":
            self.stop_proc()
            raise RuntimeError("the runner did not start with " + str(py))

    def stop_proc(self):
        with self.lock:
            proc, self.proc = self.proc, None
            self.session_id = None
        if proc is None:
            return
        try:
            proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    # ----- the queue -----
    def submit(self, items):
        """Queue the runs `items` (dicts with idx, hash, source). Returns the batch id."""
        with self.lock:
            bid = uuid.uuid4().hex[:10]
            self.batches[bid] = {"id": bid, "finished": not items, "ok": True, "cells": [], "left": len(items)}
            for it in items:
                it["batch"] = bid
                self.queue.append(it)
            if len(self.batches) > 50:
                for k in list(self.batches)[:10]:
                    if self.batches[k]["finished"]:
                        del self.batches[k]
            if items and (self.worker is None or not self.worker.is_alive()):
                self.worker = threading.Thread(target=self.work, daemon=True)
                self.worker.start()
            self.wake.notify_all()
        self.push_state()
        return bid

    def finish_item(self, it, result):
        with self.lock:
            b = self.batches.get(it["batch"])
            if b is None:
                return
            b["cells"].append(result)
            b["left"] -= 1
            if not result["ok"]:
                b["ok"] = False
                dropped = [x for x in self.queue if x["batch"] == it["batch"]]  # stop at the first error
                for x in dropped:
                    self.queue.remove(x)
                    b["cells"].append({"idx": x["idx"], "ok": False, "skipped": True, "outputs": []})
                b["left"] -= len(dropped)
            if b["left"] <= 0:
                b["finished"] = True

    def work(self):
        while True:
            with self.lock:
                if not self.queue:
                    self.current = None
                    self.push_state()
                    return
                it = self.queue.popleft()
            self.run_item(it)

    def run_item(self, it):
        outputs, full, total, shown = [], [], 0, 0
        cut = False
        try:
            with self.lock:
                if not self.alive():
                    self.start_proc()
                self.counter += 1
                n = self.counter
                self.current = {"idx": it["idx"], "hash": it["hash"], "n": n, "outputs": outputs}
                sid = self.session_id
                self.publish({"t": "start", "cell": it["idx"], "hash": it["hash"], "n": n})
                self.push_state()
            self.last_active = time.time()
            self.proc.stdin.write(json.dumps({"op": "run", "id": n, "name": f"<cell {it['idx']}>",
                                              "source": it["source"]}) + "\n")
            self.proc.stdin.flush()
            ok, ms, nimg = True, 0, 0
            img_dir = ensure_sidecar_dir(self.path)
            while True:
                try:
                    ev = self.events.get(timeout=0.3)
                except queue.Empty:
                    if not self.alive():
                        raise RuntimeError("the runner stopped while the cell was running")
                    continue
                with self.lock:  # the output and its event are one step: a page snapshot sees both or neither
                    kind = ev.get("ev")
                    if kind == "stream":
                        text = ev["text"]
                        total += len(text)
                        if total <= STREAM_HARD:
                            full.append((ev["name"], text))
                        if not cut:
                            room = STREAM_CAP - shown
                            if len(text) > room:
                                text, cut = text[:max(room, 0)], True
                            shown += len(text)
                            if text:
                                if outputs and outputs[-1]["type"] == "stream" and outputs[-1]["name"] == ev["name"]:
                                    outputs[-1]["text"] += text
                                else:
                                    outputs.append({"type": "stream", "name": ev["name"], "text": text})
                                self.publish({"t": "stream", "cell": it["idx"], "hash": it["hash"],
                                              "name": ev["name"], "text": text})
                    elif kind == "result":
                        o = {"type": "result", "text": ev["text"][:STREAM_CAP]}
                        if ev.get("html") and len(ev["html"]) <= 2_000_000:
                            o["html"] = ev["html"]
                        outputs.append(o)
                        self.publish({"t": "output", "cell": it["idx"], "hash": it["hash"], "item": o})
                    elif kind == "image":
                        nimg += 1
                        name = f"s{sid}-n{n}-{nimg}.png"
                        (img_dir / name).write_bytes(base64.b64decode(ev["data"]))
                        o = {"type": "image", "file": name, "w": ev["w"], "h": ev["h"], "text": ev.get("text", "")}
                        outputs.append(o)
                        self.publish({"t": "output", "cell": it["idx"], "hash": it["hash"], "item": o})
                    elif kind == "error":
                        o = {"type": "error", "ename": ev["ename"], "evalue": ev["evalue"], "text": ev["text"]}
                        outputs.append(o)
                        self.publish({"t": "output", "cell": it["idx"], "hash": it["hash"], "item": o})
                    elif kind == "done":
                        ok, ms = ev["ok"], ev["ms"]
                        break
                    elif kind == "eof":
                        raise RuntimeError("the runner stopped while the cell was running")
            # Anything the raw pipes delivered after "done" belongs to this cell too.
            time.sleep(0.05)
            while True:
                try:
                    ev = self.events.get_nowait()
                except queue.Empty:
                    break
                if ev.get("ev") == "stream":
                    outputs.append({"type": "stream", "name": ev["name"], "text": ev["text"]})
                elif ev.get("ev") == "eof":
                    self.events.put(ev)
                    break
        except Exception as e:
            ok, ms, n, sid = False, 0, locals().get("n", 0), locals().get("sid")
            outputs.append({"type": "error", "ename": "PyviewError", "evalue": str(e), "text": f"pyview: {e}\n"})
            self.publish({"t": "output", "cell": it["idx"], "hash": it["hash"], "item": outputs[-1]})
            self.stop_proc()
        entry = {"hash": it["hash"], "count": n, "ok": ok, "session": sid, "ts": int(time.time()), "ms": ms,
                 "outputs": outputs}
        if cut:
            name = f"s{sid}-n{n}-full.txt"
            ensure_sidecar_dir(self.path)
            (sidecar_dir(self.path) / name).write_text("".join(t for _, t in full), encoding="utf-8")
            entry["full"] = name
            outputs.append({"type": "note", "text": f"output cut: {total:,} characters in all; the full text is in "
                                                    f"{sidecar_dir(self.path) / name}"})
        self.save_entry(it, entry)
        self.last_active = time.time()
        with self.lock:
            self.current = None
        self.finish_item(it, {"idx": it["idx"], "ok": ok, "count": n, "ms": ms, "outputs": outputs})
        self.publish({"t": "done", "cell": it["idx"], "hash": it["hash"], "ok": ok})
        self.push_state()

    def save_entry(self, it, entry):
        """Store the entry at the place of the cell in the saved list (which follows the code cells)."""
        with self.lock:
            lines = split_lines(read_text(self.path)[0])
            cells = [c for c in parse_cells(lines) if is_runnable(lines, c)]
            hashes = [cell_hash(lines, c) for c in cells]
            aligned = [e for e, _ in align(load_sidecar(self.path), hashes)]
            k = next((k for k, c in enumerate(cells) if c.idx == it["idx"] and hashes[k] == it["hash"]), None)
            if k is None:  # the cell moved while it ran
                k = next((k for k, h in enumerate(hashes) if h == it["hash"]), None)
            if k is None:  # the cell was edited while it ran: its output belongs to code that is gone
                return
            aligned[k] = entry
            save_sidecar(self.path, aligned)

    def interrupt(self):
        with self.lock:
            batches = {x["batch"] for x in self.queue}
            for x in list(self.queue):
                self.queue.remove(x)
                b = self.batches.get(x["batch"])
                if b:
                    b["cells"].append({"idx": x["idx"], "ok": False, "skipped": True, "outputs": []})
                    b["left"] -= 1
                    b["ok"] = False
                    b["finished"] = b["left"] <= 0
            if self.current and self.alive():
                try:
                    os.killpg(self.proc.pid, signal.SIGINT)
                except OSError:
                    pass
        self.push_state()

    def restart(self):
        with self.lock:
            for x in list(self.queue):
                self.queue.remove(x)
                b = self.batches.get(x["batch"])
                if b:
                    b["cells"].append({"idx": x["idx"], "ok": False, "skipped": True, "outputs": []})
                    b["left"] -= 1
                    b["ok"] = False
                    b["finished"] = b["left"] <= 0
        self.stop_proc()  # a cell that is running sees the runner end and reports it
        self.push_state()

    # ----- chat -----
    def chat_data(self):
        with self.lock:
            if self.chat is None:
                self.chat = load_chat(self.path)
            return self.chat

    def publish_chat(self, ev):
        with self.lock:
            self.cseq += 1
            ev["t"], ev["cseq"] = "chat", self.cseq
            for q in self.subs:
                q.put(ev)

    def chat_snapshot(self):
        with self.lock:
            d = self.chat_data()
            msgs = copy.deepcopy(d["messages"]) + ([copy.deepcopy(self.chat_cur)] if self.chat_cur else [])
            return {"cseq": self.cseq, "busy": self.chat_cur is not None, "session": d["session"], "messages": msgs}

    def chat_send(self, text, cell, ask_only):
        """Start one turn. Returns None, or a message that says why it cannot start."""
        with self.lock:
            if self.chat_cur is not None:
                return "Claude is still answering."
            if claude_status(self.path)["running"]:
                return "The Oracle is running on this file. Wait for it, or stop it."
            if not shutil.which("claude"):
                return "The claude command is not on the PATH of the pyview server (on the computer that serves this page)."
            d = self.chat_data()
            user = {"role": "user", "text": text, "cell": cell, "ts": int(time.time())}
            d["messages"].append(user)
            self.chat_cur = {"role": "assistant", "parts": [], "ts": int(time.time()), "streaming": True}
            self.chat_stop_flag = False
            save_chat(self.path, d)
            self.publish_chat({"k": "start", "user": user})
            threading.Thread(target=self.chat_run, args=(text, cell, ask_only), daemon=True).start()
        return None

    def chat_event(self, ev):
        """One line of what `claude --output-format stream-json` prints."""
        with self.lock:
            cur = self.chat_cur
            if cur is None:
                return
            started, news = claudekit.apply_event(cur, ev)
            if started:
                self.chat_data()["started"] = True
            for n in news:
                self.publish_chat(n)

    def chat_run(self, text, cell, ask_only):
        path = self.path
        before = read_text(path)[0]
        ctx = ""
        cells = parse_cells(split_lines(before))
        if cell is not None and 0 <= cell < len(cells):
            title = cells[cell].title
            ctx = f'(The user has cell {cell}' + (f' titled "{title}"' if title else "") + " selected.)\n\n"
        d = self.chat_data()
        system = CHAT_SYSTEM.format(file=path)
        mode = (CHAT_MODE_ASK if ask_only else CHAT_MODE_EDIT) + "\n"
        cmd = claudekit.build_command("claude", mode + ctx + text, system, d["session"], d.get("started"),
                                      CHAT_TOOLS_ASK if ask_only else CHAT_TOOLS_EDIT)
        err, stderr, code = claudekit.run_stream(cmd, path.parent, claudekit.clean_env("CLAUDE_CODE", path), self.chat_event, self.set_chat_proc)
        with self.lock:
            cur = self.chat_cur
            claudekit.finish(cur, err, stderr, code, self.chat_stop_flag)
            d = self.chat_data()
            d["messages"].append(cur)
            self.chat_cur, self.chat_proc = None, None
            save_chat(path, d)
        after, version = read_text(path)
        changed = after != before
        if changed and self.app is not None:  # one Undo in the header takes back everything that this turn changed
            with self.app.lock:
                stack = self.app.undo.setdefault(path, [])
                stack.append((version, before))
                del stack[:-20]
        self.publish_chat({"k": "done", "changed": changed, "error": cur.get("error"), "stopped": bool(cur.get("stopped")),
                           "cost": cur.get("cost")})

    def set_chat_proc(self, proc):
        self.chat_proc = proc

    def chat_stop(self):
        with self.lock:
            self.chat_stop_flag = True
            proc = self.chat_proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def chat_new(self):
        """A new conversation. Returns None, or a message that says why it cannot."""
        with self.lock:
            if self.chat_cur is not None:
                return "Claude is still answering."
            self.chat = new_chat()
            save_chat(self.path, self.chat)
            self.publish_chat({"k": "reset"})
        return None

    def shutdown(self):
        self.chat_stop()
        self.stop_proc()


# --------------------------------------------------------------------------
# The document the page shows
# --------------------------------------------------------------------------

def doc_payload(app, p, rel):
    text, version = read_text(p)
    lines = split_lines(text)
    cells = parse_cells(lines)
    sess = app.session(p)
    runnable = [c for c in cells if is_runnable(lines, c)]
    hashes = [cell_hash(lines, c) for c in runnable]
    aligned = {c.idx: a for c, a in zip(runnable, align(load_sidecar(p), hashes))}
    with sess.lock:  # one moment: the state, the running cell's output so far, and the number of the last event
        seq, st = sess.seq, sess.state()
        cur = copy.deepcopy(sess.current) if sess.current else None
        alive, sid = sess.alive(), sess.session_id
    out = []
    for c in cells:
        if not is_visible(lines, c):
            continue
        d = {"idx": c.idx, "kind": c.kind, "title": c.title, "line": c.bs + 1, "runnable": is_runnable(lines, c)}
        if c.kind == "markdown":
            d["md"] = md_text(lines, c)
        else:
            nums, texts = code_lines(lines, c)
            d["nums"], d["code"] = nums, texts
        d["raw"] = editor_text(lines, c)
        if d["runnable"]:
            h = cell_hash(lines, c)
            entry, how = aligned.get(c.idx, (None, None))
            d["hash"] = h
            d["state"] = "queued" if c.idx in st["queued"] else ""
            if entry:
                d.update(outputs=entry["outputs"], count=entry["count"], ok=entry["ok"], ms=entry["ms"],
                         edited=(how == "edited"), previous=(entry.get("session") != sid or not alive))
            else:
                d["outputs"] = []
            if cur and cur["idx"] == c.idx:
                d.update(state="running", outputs=cur["outputs"], count=cur["n"], edited=False, previous=False)
        out.append(d)
    py, why = find_python(p, lines, sess.override)
    label = serverkit.doc_label(p, app.opened)  # a short name for the tab and the header
    app.opened.add(p)
    return {"file": rel, "name": p.name, "label": label, "host": socket.gethostname().split(".")[0],
            "version": version, "seq": seq, "cells": out,
            "threads": parse_threads(lines, cells), "kernel": st, "python": str(py) if py else None,
            "pythonNote": why, "trusted": app.is_trusted(p), "canUndo": bool(app.undo.get(p)),
            "claude": claude_status(p)}


# --------------------------------------------------------------------------
# The server
# --------------------------------------------------------------------------

NOT_GRANTED = "pyview can only open files and folders you have passed to it. To open this one, run: pyview {}"


class App:
    def __init__(self, root, grants_file=None):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        self.undo = {}
        self.sessions = {}
        self.grants_file = grants_file
        self.granted, self.trusted = set(), set()
        self.opened = set()  # the files that pages opened: same names get longer labels
        if grants_file:
            try:
                data = json.loads(grants_file.read_text())
                self.granted = {Path(g) for g in data.get("granted", [])}
                self.trusted = {Path(g) for g in data.get("trusted", [])}
            except (OSError, ValueError, AttributeError):
                pass

    def save_grants(self):
        if self.grants_file:
            write_private(self.grants_file, json.dumps({"granted": sorted(map(str, self.granted)),
                                                        "trusted": sorted(map(str, self.trusted))}, indent=1))

    def grant(self, p, trust=False):
        with self.lock:
            self.granted.add(p)
            if trust:
                self.trusted.add(p)
            self.save_grants()

    def trust(self, p):
        with self.lock:
            self.trusted.add(p)
            self.save_grants()

    def allowed(self, p):
        return any(g == p or g in p.parents for g in self.granted)

    def is_trusted(self, p):
        return p in self.trusted

    def session(self, p):
        with self.lock:
            if p not in self.sessions:
                self.sessions[p] = Session(p, self)
            return self.sessions[p]

    def resolve(self, rel, want_dir=False):
        rel = unquote(rel or "")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            return None
        if is_hidden(Path(rel)) or is_hidden(p.relative_to(self.root)):
            return None
        return p if (p.is_dir() if want_dir else p.is_file()) else None

    def rel(self, p):
        return p.relative_to(self.root).as_posix() if p != self.root else ""

    def shutdown(self):
        for s in list(self.sessions.values()):
            s.shutdown()

    def reap_idle(self):
        while True:
            time.sleep(min(30, max(0.5, IDLE_STOP / 4)))
            for s in list(self.sessions.values()):
                if s.alive() and not s.current and not s.queue and time.time() - s.last_active > IDLE_STOP:
                    s.stop_proc()
                    s.push_state()


class Handler(BaseHTTPRequestHandler):
    server_version = "pyview"
    app: App = None

    def log_message(self, *args):
        pass

    def host_ok(self):
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host.lower() in {h.lower() for h in allowed_hosts()}

    def cookie_name(self):
        return f"pyview{self.server.server_address[1]}"

    def authorized(self):
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.cookie_name() and TOKEN and hmac.compare_digest(value, TOKEN):
                return True
        return False

    def deny(self, api):
        msg = "This page's access key is missing or out of date. Reopen the file with the pyview command."
        if api:
            return self.send_json(403, {"error": msg})
        return self.send(403, f"<!doctype html><meta charset=utf-8><title>pyview</title>"
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
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, obj):
        self.send(code, json.dumps(obj), "application/json; charset=utf-8")

    def py_path(self, q):
        """(path, rel, None) for a .py file that may be opened, else (None, rel, (code, error))."""
        rel = (q.get("f") or [""])[0]
        p = self.app.resolve(rel)
        if not p or p.suffix.lower() != ".py":
            return None, rel, (404, f"File not found: {rel}")
        if not self.app.allowed(p):
            return None, rel, (403, NOT_GRANTED.format(p))
        return p, self.app.rel(p), None

    def index_page(self, d):
        rows = []
        if d != self.app.root:
            rows.append(f'<li><a href="/?d={quote(self.app.rel(d.parent))}">../</a></li>')
        try:
            entries = sorted(d.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            entries = []
        for e in entries:
            if e.name.startswith(".") or e.name in SKIP_DIRS:
                continue
            rel = quote(self.app.rel(e))
            if e.is_dir():
                rows.append(f'<li><a href="/?d={rel}">{html.escape(e.name)}/</a></li>')
            elif e.suffix == ".py":
                rows.append(f'<li><a href="/view?f={rel}">{html.escape(e.name)}</a></li>')
        return (f"<!doctype html><html><head><meta charset=utf-8><title>{html.escape(d.name or str(d))}/ · pyview</title>"
                f'<link rel="icon" type="image/png" href="/kit/icons/py.png"><style>'
                f"body{{font:15px system-ui,sans-serif;margin:32px;background:#fafaf7;color:#222}}"
                f"@media (prefers-color-scheme:dark){{body{{background:#1c1c1e;color:#e8e8e8}}a{{color:#6ea0ff}}}}"
                f"li{{list-style:none;margin:4px 0}}ul{{padding:0}}a{{color:#2f6fde;text-decoration:none}}</style></head>"
                f"<body><h3>{html.escape(str(d))}</h3><ul>{''.join(rows)}</ul></body></html>")

    # ----- GET -----
    def do_GET(self):
        if not self.host_ok():
            return self.send(403, "Forbidden")
        url = urlparse(self.path)
        q = parse_qs(url.query)
        app = self.app

        if url.path == "/api/ping":
            return self.send_json(200, {"app": "pyview", "pid": os.getpid(), "root": str(app.root),
                                        "started": STARTED, "code": str(Path(__file__).resolve())})
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
        if url.path.startswith("/kit/"):  # the page code that mdview and pyview share
            a = serverkit.kit_asset(url.path[len("/kit/"):])
            return self.send(200, a[0], a[1]) if a else self.send(404, "Not found")
        if url.path.startswith("/vendor/"):
            p = (VENDOR / unquote(url.path[len("/vendor/"):])).resolve()
            if VENDOR.resolve() not in p.parents or not p.is_file() or p.suffix not in VENDOR_TYPES:
                return self.send(404, "Not found")
            return self.send(200, p.read_bytes(), VENDOR_TYPES[p.suffix])

        if url.path in ("/api/version", "/api/doc", "/api/events", "/api/img", "/api/batch", "/api/claude", "/api/chat",
                        "/api/account", "/api/improve/dash", "/api/improve/preview"):
            p, rel, err = self.py_path(q)
            if err:
                return self.send_json(err[0], {"error": err[1]})
            if url.path == "/api/improve/dash":  # the dashboard of the self-improving loop (viewkit/improvekit.py)
                return self.send_json(*improvekit.dash_api("pyview", HERE, "GET"))
            if url.path == "/api/improve/preview":  # in the sandbox of a proposal: the card with Accept and Reject
                return self.send_json(*improvekit.preview_api("GET"))
            if url.path == "/api/version":
                return self.send_json(200, {"version": read_text(p)[1], "claude": claude_status(p)["running"]})
            if url.path == "/api/doc":
                try:
                    return self.send_json(200, doc_payload(app, p, rel))
                except Exception as e:
                    return self.send_json(500, {"error": str(e)})
            if url.path == "/api/claude":
                return self.send_json(200, claude_status(p))
            if url.path == "/api/account":  # the Claude Code account of the chat and the Oracle (claudekit)
                return self.send_json(*claudekit.account_api("GET", doc=p))
            if url.path == "/api/chat":
                return self.send_json(200, app.session(p).chat_snapshot())
            if url.path == "/api/batch":
                sess = app.session(p)
                with sess.lock:
                    b = sess.batches.get((q.get("id") or [""])[0])
                    if b is None:
                        return self.send_json(404, {"error": "No such run"})
                    return self.send_json(200, json.loads(json.dumps(b)))
            if url.path == "/api/img":
                name = (q.get("n") or [""])[0]
                if not re.fullmatch(r"[\w.-]+\.png", name):
                    return self.send(404, "Not found")
                f = sidecar_dir(p) / name
                if not f.is_file():
                    return self.send(404, "Not found")
                return self.send(200, f.read_bytes(), "image/png", csp=CSP_NONE)
            if url.path == "/api/events":
                return self.stream_events(app.session(p))
        self.send(404, "Not found")

    def stream_events(self, sess):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = sess.subscribe()
        try:
            self.wfile.write(("data: " + json.dumps({"t": "state", **sess.state()}) + "\n\n").encode())
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            sess.unsubscribe(q)

    # ----- POST -----
    def do_POST(self):
        if not self.host_ok() or self.headers.get("X-PyView") != "1":
            return self.send_json(403, {"error": "Forbidden"})
        if not self.authorized():
            return self.deny(True)
        path = urlparse(self.path).path
        if path not in ("/api/op", "/api/grant", "/api/trust", "/api/run", "/api/interrupt", "/api/restart",
                        "/api/python", "/api/claude", "/api/chat", "/api/account", "/api/improve/dash",
                        "/api/improve/preview"):
            return self.send_json(404, {"error": "Not found"})
        try:
            op = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except (ValueError, TypeError):
            return self.send_json(400, {"error": "Bad request"})
        app = self.app

        if path == "/api/grant":  # the pyview command: allow a file or folder you passed to it
            target = Path(str(op.get("path", "")))
            try:
                rel = target.resolve().relative_to(app.root).as_posix()
            except (ValueError, OSError):
                return self.send_json(404, {"error": f"{target} is outside {app.root}"})
            p = app.resolve(quote(rel if rel != "." else ""), want_dir=target.is_dir())
            if not p:
                return self.send_json(404, {"error": f"Can't open {target}"})
            app.grant(p, trust=bool(op.get("trust")) and p.is_file())
            return self.send_json(200, {"ok": True})

        p, rel, err = self.py_path({"f": [op.get("f", "")]})
        if err:
            return self.send_json(err[0], {"error": err[1]})
        if path == "/api/improve/dash":  # the dashboard: add, edit or delete a waiting note, or send them all
            if improvekit.preview_of():
                return self.send_json(409, {"error": "This is a preview. Write notes in the live pyview tab."})
            port = self.server.server_address[1]
            back = f"http://{link_host()}:{port}/view?f={quote(rel)}"
            return self.send_json(*improvekit.dash_api("pyview", HERE, "POST", op, p, back))
        if path == "/api/improve/preview":  # the decision in the preview tab: accept, change or reject
            return self.send_json(*improvekit.preview_api("POST", op))
        sess = app.session(p)

        if path == "/api/trust":  # the button in the viewer
            app.trust(p)
            return self.send_json(200, {"ok": True})
        if path == "/api/python":
            sess.override = str(op.get("python") or "") or None
            return self.send_json(200, {"ok": True})
        if path == "/api/claude":
            if op.get("stop"):
                return self.send_json(200, stop_claude(p))
            if sess.chat_cur is not None:
                return self.send_json(409, {"error": "Claude is answering in the chat. Wait for it, or stop it."})
            return self.send_json(200, start_claude(p))
        if path == "/api/account":  # the Claude Code account of the chat and the Oracle (claudekit)
            return self.send_json(*claudekit.account_api("POST", op, p))
        if path == "/api/chat":
            action = op.get("action", "send")
            if action == "stop":
                sess.chat_stop()
                return self.send_json(200, {"ok": True})
            if action == "new":
                err = sess.chat_new()
                return self.send_json(409 if err else 200, {"error": err} if err else {"ok": True})
            if not app.is_trusted(p):  # Claude edits the file and runs cells: the same gate as running a cell
                return self.send_json(403, {"error": "This file is not trusted to run code. Press 'Trust this file' first."})
            text = str(op.get("text", "")).strip()
            if not text:
                return self.send_json(400, {"error": "Write a message first."})
            cell = op.get("cell") if isinstance(op.get("cell"), int) else None
            err = sess.chat_send(text, cell, bool(op.get("ask_only")))
            return self.send_json(409 if err else 200, {"error": err} if err else {"ok": True})
        if path in ("/api/interrupt", "/api/restart"):
            if not app.is_trusted(p):
                return self.send_json(403, {"error": "This file is not trusted to run code."})
            sess.interrupt() if path == "/api/interrupt" else sess.restart()
            return self.send_json(200, {"ok": True})

        if path == "/api/run":
            if not app.is_trusted(p):
                return self.send_json(403, {"error": "This file is not trusted to run code. Press "
                                                     "'Trust this file' in the viewer, or open it with: pyview " + str(p)})
            with app.lock:
                text, version = read_text(p)
                lines = split_lines(text)
                cells = parse_cells(lines)
                mode, n = op.get("mode", "cell"), op.get("cell")
                runnable = [c for c in cells if is_runnable(lines, c)]
                if mode == "all":
                    pick = runnable
                elif mode == "above":
                    pick = [c for c in runnable if c.idx < (n or 0)]
                elif mode == "below":
                    pick = [c for c in runnable if c.idx >= (n or 0)]
                elif mode == "range":
                    pick = [c for c in runnable if op.get("from", 0) <= c.idx <= op.get("to", 10 ** 9)]
                else:
                    pick = [c for c in runnable if c.idx == n]
                    if not pick:
                        return self.send_json(400, {"error": f"Cell {n} is not a code cell that can run."})
                items = [{"idx": c.idx, "hash": cell_hash(lines, c), "source": exec_source(lines, c)} for c in pick]
            sess.override = sess.override or (str(op["python"]) if op.get("python") else None)
            if items and not sess.alive():
                py, why = find_python(p, lines, sess.override)
                if py is None:
                    return self.send_json(400, {"error": "pyview: " + why})
            return self.send_json(200, {"batch": sess.submit(items), "cells": [it["idx"] for it in items]})

        # /api/op: one edit of the file, or undo
        with app.lock:
            text, version = read_text(p)
            if op.get("type") == "undo":
                stack = app.undo.get(p) or []
                if not stack or stack[-1][0] != version:
                    return self.send_json(409, {"error": "Nothing to undo, or the file changed since."})
                write_text(p, stack.pop()[1])
            else:
                if op.get("version") != version:
                    return self.send_json(409, {"error": "The file changed. Reload and try again."})
                try:
                    new = apply_op(text, op)
                except ValueError as e:
                    return self.send_json(400, {"error": str(e)})
                if new != text:
                    write_text(p, new)
                    stack = app.undo.setdefault(p, [])
                    stack.append((read_text(p)[1], text))
                    del stack[:-20]
        try:
            return self.send_json(200, doc_payload(app, p, rel))
        except Exception as e:
            return self.send_json(500, {"error": str(e)})


# --------------------------------------------------------------------------
# Claude: the Oracle button runs /answer-cells in the background
# --------------------------------------------------------------------------

def _claude_key(p):
    return hashlib.sha1(str(p).encode()).hexdigest()[:12]


def claude_status(p):
    d = STATE_DIR / "claude"
    key = _claude_key(p)
    pidf, logf = d / f"{key}.pid", d / f"{key}.log"
    running = False
    try:
        pid = int(pidf.read_text())
        os.kill(pid, 0)
        running = True
    except (OSError, ValueError):
        pass
    return {"running": running, "log": str(logf) if logf.exists() else None}


def start_claude(p):
    if not shutil.which("claude"):
        return {"error": "The claude command is not on the PATH of the pyview server."}
    st = claude_status(p)
    if st["running"]:
        return {"ok": True, "running": True}
    d = STATE_DIR / "claude"
    d.mkdir(parents=True, exist_ok=True)
    key = _claude_key(p)
    env = claudekit.clean_env("CLAUDE_CODE", p)
    log = open(d / f"{key}.log", "wb")
    proc = subprocess.Popen(
        ["claude", "-p", f"/answer-cells {p}", "--allowedTools", ",".join(CLAUDE_TOOLS)],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=str(p.parent), env=env,
        start_new_session=True)
    (d / f"{key}.pid").write_text(str(proc.pid))
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it, or a finished run would look alive
    return {"ok": True, "running": True}


def stop_claude(p):
    try:
        os.killpg(int((STATE_DIR / "claude" / f"{_claude_key(p)}.pid").read_text()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    return {"ok": True}


# --------------------------------------------------------------------------
# Server process and command line
# --------------------------------------------------------------------------

def run_server(port):
    """Serve in the foreground. Restarts itself when this file changes."""
    global STARTED, TOKEN
    STARTED = time.strftime("%Y-%m-%d %H:%M:%S")
    TOKEN = read_token(port) or new_token(port)
    app = Handler.app = App(ROOT, grants_file(port))
    try:
        server = ThreadingHTTPServer((bind_addr(), port), Handler)
    except OSError as e:
        sys.exit(f"pyview: can't listen on port {port}: {e}")
    threading.Thread(target=app.reap_idle, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *a: threading.Thread(target=server.shutdown, daemon=True).start())
    DAEMON.serve(server, port, f"pyview: serving {ROOT} at http://{bind_addr()}:{port}/ (pid {os.getpid()}, {STARTED})",
                 on_stop=app.shutdown)  # the runners must not outlive the server


def api(port, method, path, body=None):
    token = read_token(port)
    if not token:
        sys.exit("pyview: the server's access key is missing. Restart it: pyview --restart")
    headers = {"Content-Type": "application/json", "X-PyView": "1", "Cookie": f"pyview{port}={token}"}
    req = urllib.request.Request(f"http://{bind_addr()}:{port}{path}", method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except ValueError:
            msg = e.reason
        sys.exit(f"pyview: {msg}")


def check_target(target):
    p = Path(target).expanduser().resolve()
    if not p.exists():
        sys.exit(f"pyview: not found: {target}")
    if p.is_file() and p.suffix.lower() != ".py":
        sys.exit(f"pyview: not a .py file: {target}")
    return p


def grant_file(p, port, trust):
    root = Path(ensure_server(port)["root"])
    if p != root and root not in p.parents:
        sys.exit(f"pyview: {p} is outside {root}, the folder the server can open.\n"
                 f"Restart it with a wider root, e.g.: PYVIEW_ROOT=/ pyview --restart")
    if is_hidden(p.relative_to(root)):
        sys.exit(f"pyview: {p} is in a hidden folder (or is a hidden file). pyview never opens those.")
    api(port, "POST", "/api/grant", {"path": str(p), "trust": trust})
    return root


def open_target(target, port, open_browser):
    p = check_target(target)
    root = grant_file(p, port, trust=p.is_file())
    rel = p.relative_to(root).as_posix() if p != root else ""
    url = f"http://{link_host()}:{port}/" + (f"view?f={quote(rel)}" if p.is_file() else f"?d={quote(rel)}")
    url += "&t=" + read_token(port)
    print(url)
    if open_browser:
        webbrowser.open(url)


def rel_of(p, port):
    return p.relative_to(Path(ping(port)["root"])).as_posix()


def cmd_run(args):
    p = check_target(args.file)
    if not p.is_file():
        sys.exit("pyview: run needs a .py file.")
    root = grant_file(p, args.port, trust=True)
    body = {"f": p.relative_to(root).as_posix(), "python": args.python}
    if args.all:
        body["mode"] = "all"
    elif args.from_ is not None or args.to is not None:
        body.update(mode="range", **{"from": args.from_ or 0, "to": args.to if args.to is not None else 10 ** 9})
    elif args.cell is not None:
        body.update(mode="cell", cell=args.cell)
    else:
        sys.exit("pyview: say what to run: --cell N, --all, or --from N / --to N.")
    run = api(args.port, "POST", "/api/run", body)
    t0 = time.time()
    while True:
        b = api(args.port, "GET", f"/api/batch?f={quote(body['f'])}&id={run['batch']}")
        if b["finished"]:
            break
        if time.time() - t0 > args.timeout:
            api(args.port, "POST", "/api/interrupt", {"f": body["f"]})
            print(f"pyview: stopped after {args.timeout:.0f} s (--timeout).", file=sys.stderr)
            time.sleep(1)
        time.sleep(0.1)
    b["cells"].sort(key=lambda c: c["idx"])
    for c in b["cells"]:
        if c.get("skipped"):
            print(f"── cell {c['idx']} · skipped (an earlier cell failed)")
            continue
        print(f"── cell {c['idx']} · [{c['count']}] · {c['ms'] / 1000:.2f} s · {'ok' if c['ok'] else 'ERROR'}")
        print(outputs_text(c["outputs"], p, args.max_chars), end="")
    sys.exit(0 if b["ok"] else 1)


def cmd_out(args):
    p = check_target(args.file)
    lines = split_lines(read_text(p)[0])
    cells = parse_cells(lines)
    runnable = [c for c in cells if is_runnable(lines, c)]
    aligned = {c.idx: a for c, a in zip(runnable, align(load_sidecar(p), [cell_hash(lines, c) for c in runnable]))}
    for c in cells:
        if not is_visible(lines, c) or (args.cell is not None and c.idx != args.cell):
            continue
        title = f" {c.title}" if c.title else ""
        if c.kind == "markdown":
            print(f"── cell {c.idx} · markdown{title}")
            if args.cell is not None:
                print(md_text(lines, c))
            continue
        entry, how = aligned.get(c.idx, (None, None))
        if not entry:
            print(f"── cell {c.idx} · not run{title}")
            continue
        flag = " · EDITED since run" if how == "edited" else ""
        print(f"── cell {c.idx} · [{entry['count']}] · {entry['ms'] / 1000:.2f} s · {'ok' if entry['ok'] else 'ERROR'}{flag}{title}")
        print(outputs_text(entry["outputs"], p, args.max_chars), end="")


def cmd_remote(host, target, port, open_browser):
    """Start the viewer on another computer over SSH and open its link here. If the server there listens on
    a network address (PYVIEW_BIND), the link works as it is. If it listens on 127.0.0.1 only, open an SSH tunnel."""
    inner = f"PYVIEW_PORT={port} pyview {shlex.quote(target)} --no-open"
    r = subprocess.run(["ssh", host, "zsh -lc " + shlex.quote(inner)], capture_output=True, text=True)
    links = [l for l in r.stdout.splitlines() if l.startswith("http")]
    if r.returncode or not links:
        sys.exit(f"pyview: could not start the viewer on {host}.\n{(r.stderr or r.stdout).strip()}\n"
                 f"Check that `pyview` is on the PATH of a login shell there (put it in ~/.zprofile), and that the file exists there.")
    link = links[-1]
    u = urlparse(link)
    if u.hostname in ("127.0.0.1", "localhost", serverkit.app_host("pyview")):
        t = subprocess.run(["ssh", "-f", "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{u.port}:127.0.0.1:{u.port}", host])
        if t.returncode:
            sys.exit(f"pyview: the SSH tunnel on port {u.port} failed. Is something on this computer using that port? Try: pyview --remote {host} FILE --port 8777")
        print(f"pyview: {host} listens on 127.0.0.1 only, so pyview opened an SSH tunnel on port {u.port}.\n"
              f"        To skip the tunnel, set PYVIEW_BIND=tailscale and PYVIEW_NAMES in ~/.zprofile on {host}.", file=sys.stderr)
    print(link)
    if open_browser:
        webbrowser.open(link)


def cmd_list(path, as_json):
    p = Path(path)
    lines = split_lines(read_text(p)[0])
    cells = parse_cells(lines)
    threads = parse_threads(lines, cells)
    if as_json:
        print(json.dumps(threads, indent=2, ensure_ascii=False))
        return
    if not threads:
        print("No comments.")
    for t in threads:
        where = {"general": "general", "cell": f"cell {t['cell']}", "line": f"cell {t['cell']}, line {t['line'] + 1}"}[t["scope"]]
        print(f"[{t['status']}] {where}" + (f"  ({t['target']})" if t["scope"] == "line" else ""))
        for m in t["msgs"]:
            print(f"    {m['author']}: " + m["text"].replace("\n", "\n        "))


def cmd_setup():
    packages = SETUP_PACKAGES
    py = SHARED_VENV / "bin" / "python"
    if shutil.which("uv"):
        if not py.exists():
            subprocess.run(["uv", "venv", str(SHARED_VENV)], check=True)
        subprocess.run(["uv", "pip", "install", "--python", str(py), *packages], check=True)
    else:
        if not py.exists():
            subprocess.run([sys.executable, "-m", "venv", str(SHARED_VENV)], check=True)
        subprocess.run([str(py), "-m", "pip", "install", *packages], check=True)
    print(f"pyview: the shared interpreter is ready: {py}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "note":  # a note to pyview itself, not about a file (viewkit)
        return improvekit.note_main("pyview", HERE, sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "try":  # try a proposal of the loop in a sandbox, with copies
        return improvekit.try_main("pyview", HERE, sys.argv[2:])
    ap = argparse.ArgumentParser(
        prog="pyview", description="Run and read Python cells in the browser.",
        epilog=f"One shared server runs in the background on port {PORT} and can open any file under {ROOT}. "
               f"Its log is {LOG}.")
    ap.add_argument("target", nargs="*", help="the .py files or folders to open (default: current folder); "
                                              "or: run FILE, out FILE, restart FILE, interrupt FILE")
    ap.add_argument("--no-open", action="store_true", help="print the URL without opening a browser")
    ap.add_argument("--list", action="store_true", help="print the #> comment threads in the file and exit")
    ap.add_argument("--json", action="store_true", help="with --list, print JSON")
    ap.add_argument("--cell", type=int, help="with run or out: the cell number")
    ap.add_argument("--all", action="store_true", help="with run: every code cell")
    ap.add_argument("--from", dest="from_", type=int, help="with run: first cell")
    ap.add_argument("--to", type=int, help="with run: last cell")
    ap.add_argument("--python", help="with run: the interpreter to use (for a runner that is not started yet)")
    ap.add_argument("--max-chars", type=int, default=20000,
                    help="with run or out: longest text to print per output, 0 for all (default 20000)")
    ap.add_argument("--timeout", type=float, default=600, help="with run: seconds before the run is interrupted")
    ap.add_argument("--remote", metavar="HOST", help="start the viewer for FILE on HOST over SSH and open it here (FILE is a path on HOST)")
    ap.add_argument("--setup", action="store_true", help=f"build the shared interpreter in {SHARED_VENV}")
    ap.add_argument("--status", action="store_true", help="show whether the server is running")
    ap.add_argument("--stop", action="store_true", help="stop the server")
    ap.add_argument("--restart", action="store_true", help="restart the server")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=PORT, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.serve:
        return run_server(args.port)
    if args.remote:
        if len(args.target) != 1:
            sys.exit("pyview: --remote needs exactly one file (a path on the other computer).")
        return cmd_remote(args.remote, args.target[0], args.port, not args.no_open)
    if args.setup:
        return cmd_setup()
    if args.status:
        info = ping(args.port)
        print(f"pyview: running on http://{bind_addr()}:{args.port}/ (pid {info['pid']}, since {info['started']}, "
              f"root {info['root']})" if info else "pyview: not running.")
        return
    if args.stop or args.restart:
        stop_server(args.port)
        if args.stop:
            return
        info = ensure_server(args.port)
        print(f"pyview: running (pid {info['pid']}).")
        return
    targets = args.target
    if targets and targets[0] in ("run", "out", "restart", "interrupt"):
        verb, rest = targets[0], targets[1:]
        if len(rest) != 1:
            sys.exit(f"pyview: {verb} needs exactly one file.")
        args.file = rest[0]
        if verb == "run":
            return cmd_run(args)
        if verb == "out":
            return cmd_out(args)
        p = check_target(args.file)
        root = grant_file(p, args.port, trust=True)
        api(args.port, "POST", f"/api/{verb}", {"f": p.relative_to(root).as_posix()})
        print(f"pyview: {verb} sent.")
        return
    targets = targets or ["."]
    if args.list:
        for i, t in enumerate(targets):
            if not Path(t).is_file():
                sys.exit("pyview: --list needs a .py file.")
            if len(targets) > 1:
                print(("\n" if i else "") + f"== {t} ==")
            cmd_list(t, args.json)
        return
    failed = False
    for t in targets:
        try:
            open_target(t, args.port, not args.no_open)
        except SystemExit as e:
            print(e.code, file=sys.stderr)
            failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
