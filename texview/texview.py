#!/usr/bin/env python3
"""texview: read, edit and comment on a LaTeX document in the browser, next to its PDF, and ask Claude Code about it.

texview compiles the document with latexmk (and SyncTeX) into ~/.cache/texview/build/, so the folder of the document
stays clean. It compiles again when a source file changes. A double-click on the PDF opens or closes the source. A ⌘/Ctrl+click
on the PDF opens the source at that line, and a ⌘/Ctrl+click in the source shows the place in the PDF.

The comments and the suggested changes are in a JSON file next to the main file, .texview/MAIN.tex.json:

    {"threads": [{"id": 1, "anchor": {"file": "sections/intro.tex", "line": 12, "quote": "the bound is tight",
                                      "prefix": "We show that ", "suffix": " for all n."},
                  "msgs": [{"author": "you", "text": "Why?", "ts": 1730000000}]},
                 {"id": 2, "anchor": {...}, "change": {"old": "is tight", "new": "is sharp"},
                  "msgs": [{"author": "claude", "text": "A shorter word.", "ts": 1730000100}]}]}

A thread with "change" is a suggested change: Accept puts the new text into the source file, Reject removes it. The
source files change only when you save in the editor or accept a change. Claude never edits them.

Usage:
    texview main.tex                     open a document (starts the shared server if needed)
    texview                              browse the current folder
    texview list main.tex [--json]       print the comments and the suggested changes
    texview reply main.tex ID TEXT       add a reply to thread ID (as claude). TEXT "-" reads it from stdin.
    texview add main.tex TEXT [--file F --line N [--quote Q]]   add a comment (as claude). TEXT "-": stdin.
    texview suggest main.tex --file F [--line N] < CHANGES   suggest changes (as claude), see `texview suggest -h`
    texview log main.tex                 print the errors and warnings of the last compile
    texview text main.tex [--page N | --pages A-B]   print the text of the compiled PDF (needs pdftotext)
    texview build main.tex               compile now and print the errors
    texview note TEXT                    a note to texview itself: a problem or a wish (a GitHub issue). "-": stdin.
    texview try N [main.tex]             try the proposal of note N in a sandbox (port 8900+N, copies of the files)
    texview --status | --stop | --restart

One server runs in the background on 127.0.0.1:8770 and serves files under your home folder (TEXVIEW_ROOT and
TEXVIEW_PORT override these). It restarts itself when this file or a .py file of ../viewkit changes. Requires Python
3.9+, latexmk and synctex (TeX Live or MacTeX), and the folder viewkit next to this one.

Security: every request needs the access key in ~/.cache/texview/token-PORT (readable only by you), which `texview`
passes in the link it opens and the browser then keeps as a cookie. The server opens only the folders of the documents
that you passed to `texview`. It reads and writes only text sources (.tex, .bib, .sty, ...) in the folder of the main
file. Hidden files and folders are never read, listed or served.
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
ROOT = Path(os.environ.get("TEXVIEW_ROOT") or Path.home()).expanduser().resolve()
PORT = int(os.environ.get("TEXVIEW_PORT") or 8770)
STATE_DIR = Path.home() / ".cache" / "texview"
BUILD_DIR = Path(os.environ.get("TEXVIEW_BUILD") or STATE_DIR / "build")
LOG = STATE_DIR / "server.log"
LOCK_DIR = STATE_DIR / "locks"
SIDECAR_DIR = ".texview"  # next to the main file
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "Library", "build", "_minted"}
SRC_EXT = {".tex", ".bib", ".sty", ".cls", ".ltx", ".def", ".cfg", ".bbx", ".cbx", ".bst", ".txt"}  # sources to edit
SRC_MAX = 4 * 1024 * 1024  # the largest source file
BUILD_TIMEOUT = 600
STARTED = None
TOKEN = None  # the access key, set when the server starts
TEXT_MAX = 20000  # the longest comment
CHANGE_MAX = 20000  # the longest text of a suggested change
CONTEXT = 40      # the characters of an anchor before and after the quote
TEX_DIRS = ["/Library/TeX/texbin", "/usr/local/texlive/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]

sys.path.insert(0, str(HERE.parent / "viewkit"))  # the code that the viewers share
try:
    import claudekit
    import improvekit
    import serverkit
except ImportError:
    sys.exit(f"texview: the folder viewkit is missing. It must be next to texview, at {HERE.parent / 'viewkit'}.")
DAEMON = serverkit.Daemon("texview", STATE_DIR, __file__, "TEXVIEW_PORT")
grants_file, write_private = DAEMON.grants_file, DAEMON.write_private
new_token, read_token = DAEMON.new_token, DAEMON.read_token
ping, ensure_server, stop_server = DAEMON.ping, DAEMON.ensure_server, DAEMON.stop_server

VENDOR_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}

# Content-Security-Policy per kind of response. The viewer may run only its own scripts (viewer.js, the kit, PDF.js and
# its worker, CodeMirror, MathJax) and talk only to this server.
CSP_VIEWER = ("default-src 'none'; script-src 'self'; worker-src 'self'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; base-uri 'none'; "
              "frame-ancestors 'none'; form-action 'none'")
CSP_INDEX = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
CSP_NONE = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"


class OpError(Exception):
    pass


def tool(name):
    """The path of a TeX program. The server can start from Finder, without the PATH of the shell."""
    exe = shutil.which(name)
    if exe:
        return exe
    for d in TEX_DIRS + sorted(map(str, Path("/usr/local/texlive").glob("*/bin/*")), reverse=True):
        p = Path(d) / name
        if os.access(p, os.X_OK):
            return str(p)
    return None


def is_hidden(rel):
    """True if any part of a relative path starts with a dot (.ssh, .claude, .., .env)."""
    return any(part.startswith(".") for part in Path(rel).parts)


def key_of(p):
    return hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:16]


def text_version(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class file_lock:
    """A lock per main file, for the server and the command line, so two writers never mix their changes."""
    def __init__(self, p):
        LOCK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = LOCK_DIR / (key_of(p) + ".lock")

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)  # also releases the lock


# --------------------------------------------------------------------------
# The source files of a document
# --------------------------------------------------------------------------

def source_path(main, rel):
    """The source file `rel` (relative to the folder of the main file), or OpError. Only text sources in that folder."""
    rel = str(rel or "").strip()
    if not rel or rel.startswith("/") or is_hidden(rel):
        raise OpError(f"texview opens only source files in the folder of {main.name}: {rel or '(no file)'}")
    p = (main.parent / rel).resolve()
    if main.parent not in p.parents or is_hidden(p.relative_to(main.parent)):
        raise OpError(f"texview opens only source files in the folder of {main.name}: {rel}")
    if p.suffix.lower() not in SRC_EXT:
        raise OpError(f"texview does not open {p.suffix or 'this'} files as sources: {rel}")
    return p


def rel_of(main, p):
    return Path(p).resolve().relative_to(main.parent).as_posix()


def read_source(p):
    """(text, encoding). A file that is not UTF-8 is read as Latin-1 and saved back as Latin-1."""
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raise OpError(f"There is no file {p.name}.")
    if len(raw) > SRC_MAX:
        raise OpError(f"{p.name} is too large to edit here (more than {SRC_MAX // 1048576} MB).")
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def write_source(p, text, encoding="utf-8"):
    """Write a source file in one step, with its old line ends (CRLF stays CRLF) and its old permissions."""
    try:
        old = p.read_bytes()
        mode = p.stat().st_mode & 0o777
    except FileNotFoundError:
        old, mode = b"", 0o644
    text = text.replace("\r\n", "\n")
    if b"\r\n" in old:
        text = text.replace("\n", "\r\n")
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix="." + p.name + ".")
    with os.fdopen(fd, "wb") as out:
        out.write(text.encode(encoding))
    os.chmod(tmp, mode)
    os.replace(tmp, p)


def is_main_tex(p):
    """True for a .tex file with \\documentclass: the main file of a document."""
    if p.suffix.lower() != ".tex":
        return False
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            head = f.read(20000)
    except OSError:
        return False
    return bool(re.search(r"^[^%\n]*\\documentclass", head, re.M))


def list_sources(main, inputs=()):
    """The source files of the document: the main file, then the files that the compile read (in its order), then the
    other sources in the folder. [{"path": rel, "input": bool}]. The main file of another document in the folder has
    "doc": true, so the page can open that document with its PDF."""
    root, seen, out = main.parent, set(), []
    for p in [main] + [Path(x) for x in inputs]:
        try:
            rel = rel_of(main, p)
        except ValueError:
            continue
        if rel not in seen and p.suffix.lower() in SRC_EXT and not is_hidden(rel) and p.is_file():
            seen.add(rel)
            out.append({"path": rel, "input": True})
    others, visited = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        visited += 1
        if visited > 1500:  # a main file in a large folder (the home folder): stop the walk early
            break
        d = Path(dirpath)
        depth = len(d.relative_to(root).parts)
        dirnames[:] = sorted(x for x in dirnames if not x.startswith(".") and x not in SKIP_DIRS and depth < 4)
        for f in filenames:
            if f.startswith(".") or Path(f).suffix.lower() not in SRC_EXT:
                continue
            rel = (d / f).relative_to(root).as_posix()
            if rel not in seen:
                others.append(rel)
        if len(others) > 400:
            break
    out += [{"path": r, "input": False, **({"doc": True} if is_main_tex(root / r) else {})} for r in sorted(others)[:400]]
    return out


# The data of the suggestions in the source editor. viewer.js has the same patterns for the open buffers.
TEX_COMMENT = re.compile(r"(?<!\\)((?:\\\\)*)%.*$", re.M)
TEX_COMMAND_DEF = re.compile(r"\\(?:(?:renew|new|provide)command\*?\s*\{?\s*|def\s*)\\([a-zA-Z@]+)")
TEX_ENV_DEF = re.compile(r"\\newenvironment\*?\s*\{([^{}\s]+)\}")
AUX_LABEL = re.compile(r"\\newlabel\{([^{}]+)\}\{\{")
BIB_ENTRY = re.compile(r"^[ \t]*@([a-zA-Z]+)\s*[{(]\s*([^\s,{}()\"#%~=\\]+)\s*,", re.M)


def tex_defs(text):
    """The commands and the environments that a source file defines, without the lines in comments. A name with @ is
    internal to a package, so it is left out."""
    text = TEX_COMMENT.sub(r"\1", text)
    return {"commands": sorted({c for c in TEX_COMMAND_DEF.findall(text) if "@" not in c}),
            "envs": sorted(set(TEX_ENV_DEF.findall(text)))}


def aux_labels(text):
    """The labels of an .aux file: {name: number}. Not the second entries that cleveref writes (name@cref)."""
    out = {}
    for m in AUX_LABEL.finditer(text):
        if m.group(1).endswith("@cref"):
            continue
        i, depth = m.end(), 1
        while i < len(text) and depth and text[i] != "\n":
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out[m.group(1)] = re.sub(r"\\[a-zA-Z@]+\s*|[{}]", "", text[m.end():i - 1]).strip()[:20] if not depth else ""
    return out


def bib_keys(text):
    """The citation keys of a .bib file."""
    return sorted({k for kind, k in BIB_ENTRY.findall(text) if kind.lower() not in ("string", "preamble", "comment")})


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def offset_of_line(text, line):
    """The offset of the start of line `line` (from 1)."""
    pos = 0
    for _ in range(max(0, line - 1)):
        k = text.find("\n", pos)
        if k < 0:
            return len(text)
        pos = k + 1
    return pos


def locate(text, anchor):
    """(start, end) of the words of an anchor in the text, or None. With more than one place, the best fit of the words
    before and after wins, then the place nearest to the line of the anchor."""
    q = anchor.get("quote") or ""
    if not q:
        return None
    pre, suf = anchor.get("prefix") or "", anchor.get("suffix") or ""
    line = anchor.get("line") or 0
    best = None
    i = text.find(q)
    while i >= 0:
        before, after = text[max(0, i - CONTEXT):i], text[i + len(q):i + len(q) + CONTEXT]
        k = 0
        while k < len(before) and k < len(pre) and before[-1 - k] == pre[-1 - k]:
            k += 1
        j = 0
        while j < len(after) and j < len(suf) and after[j] == suf[j]:
            j += 1
        score = (k + j, -abs(line_of(text, i) - line) if line else 0)
        if best is None or score > best[0]:
            best = (score, i)
        i = text.find(q, i + 1)
    return (best[1], best[1] + len(q)) if best else None


def make_anchor(text, start, end):
    return {"line": line_of(text, start), "quote": text[start:end],
            "prefix": text[max(0, start - CONTEXT):start], "suffix": text[end:end + CONTEXT]}


# --------------------------------------------------------------------------
# The comments and the suggested changes: a JSON file next to the main file
# --------------------------------------------------------------------------

def sidecar(main):
    return main.parent / SIDECAR_DIR / (main.name + ".json")


def read_comments(main):
    """(data, version) of the comments of a document. The version is a hash of the file, "" if there is no file."""
    try:
        raw = sidecar(main).read_bytes()
    except FileNotFoundError:
        return {"threads": []}, ""
    version = hashlib.sha1(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("threads"), list):
            raise ValueError("no threads list")
    except (ValueError, UnicodeDecodeError) as e:
        raise OpError(f"{sidecar(main)} is not a valid comments file: {e}")
    return data, version


def comments_text(main):
    try:
        return sidecar(main).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def restore_comments(main, before):
    f = sidecar(main)
    if before is None:
        f.unlink(missing_ok=True)
        try:
            f.parent.rmdir()
        except OSError:
            pass
    else:
        f.parent.mkdir(exist_ok=True)
        f.write_text(before, encoding="utf-8")


def write_comments(main, data):
    """Write the comments. With no threads, remove the file (and the folder, if it is empty). Returns the new version."""
    f = sidecar(main)
    if not data["threads"]:
        restore_comments(main, None)
        return ""
    f.parent.mkdir(exist_ok=True)
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix="." + f.name + ".")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, f)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def clean_text(text, what="comment"):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or "")).replace("\r\n", "\n").strip()
    if not text:
        raise OpError(f"Write a {what} first.")
    if len(text) > TEXT_MAX:
        raise OpError(f"The {what} is too long (more than {TEXT_MAX} characters).")
    return text


def clean_anchor(main, a):
    """A source anchor {file, line, quote, prefix, suffix}, or None for a general comment. The quote must be in the file
    (the line alone is enough: then the quote is the text of that line)."""
    if not a:
        return None
    if not isinstance(a, dict):
        raise OpError("Bad anchor.")
    p = source_path(main, a.get("file"))
    text = read_source(p)[0]
    quote_ = str(a.get("quote") or "").replace("\r\n", "\n")[:CHANGE_MAX]
    try:
        line = int(a.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    if not quote_.strip():
        if line < 1 or line > text.count("\n") + 1:
            raise OpError("Select some text, or give a line.")
        start = offset_of_line(text, line)
        end = text.find("\n", start)
        end = len(text) if end < 0 else end
        while start < end and text[start] in " \t":
            start += 1
        if start == end:
            raise OpError(f"Line {line} of {a.get('file')} is empty.")
        return {"file": rel_of(main, p), **make_anchor(text, start, end)}
    loc = locate(text, {"quote": quote_, "prefix": a.get("prefix"), "suffix": a.get("suffix"), "line": line})
    if not loc:
        raise OpError(f"These words are not in {a.get('file')}: {quote_[:80]!r}")
    return {"file": rel_of(main, p), **make_anchor(text, *loc)}


def find_thread(data, tid):
    for t in data["threads"]:
        if t.get("id") == tid:
            return t
    raise OpError(f"There is no comment #{tid}.")


def new_id(data):
    return max([t.get("id", 0) for t in data["threads"]] + [0]) + 1


def apply_op(main, data, op, author="you"):
    """Change the comments. op: {"op": "add" | "reply" | "edit" | "delete" | "resolve" | "resolve-all" | "suggest" |
    "reject" | "reject-all", ...}. Returns the id of the thread (or None). Accept is in `accept`: it changes a source."""
    kind, now = op.get("op"), int(time.time())
    ts = data["threads"]
    if kind == "add":
        tid = new_id(data)
        t = {"id": tid, "anchor": clean_anchor(main, op.get("anchor")),
             "msgs": [{"author": author, "text": clean_text(op.get("text")), "ts": now}]}
        if t["anchor"] is None:
            del t["anchor"]
        ts.append(t)
        return tid
    if kind == "suggest":  # a suggested change: the old text must be in the file, exactly
        a = dict(op.get("anchor") or {})
        old, new = str(op.get("old") or "").replace("\r\n", "\n"), str(op.get("new") or "").replace("\r\n", "\n")
        if not old:
            raise OpError("A suggested change needs the old text (copy it exactly from the file).")
        if old == new:
            raise OpError("The new text is the same as the old text.")
        if len(old) > CHANGE_MAX or len(new) > CHANGE_MAX:
            raise OpError(f"A suggested change is too long (more than {CHANGE_MAX} characters).")
        p = source_path(main, a.get("file"))
        text = read_source(p)[0]
        n = text.count(old)
        if not n:
            raise OpError(f"The old text is not in {a.get('file')} exactly as given: {old[:80]!r}. Copy it from the file "
                          f"(the same spaces and line ends).")
        if n > 1 and not a.get("line") and not a.get("prefix"):
            raise OpError(f"The old text occurs {n} times in {a.get('file')}. Give --line N (the line where it starts), "
                          f"or make the old text longer.")
        a["quote"] = old
        anchor = clean_anchor(main, a)
        tid = new_id(data)
        note = str(op.get("text") or "").strip()
        ts.append({"id": tid, "anchor": anchor, "change": {"old": old, "new": new},
                   "msgs": [{"author": author, "text": clean_text(note, "note") if note else "Suggested change.",
                             "ts": now}]})
        return tid
    if kind in ("resolve-all", "reject-all"):
        keep = [t for t in ts if not t.get("change")] if kind == "reject-all" else []
        if len(keep) == len(ts):
            raise OpError("There are no suggested changes." if kind == "reject-all" else "There are no comments.")
        data["threads"] = keep
        return None
    try:
        tid = int(op.get("thread"))
    except (TypeError, ValueError):
        raise OpError("Which comment?")
    t = find_thread(data, tid)
    if kind == "reply":
        t["msgs"].append({"author": author, "text": clean_text(op.get("text")), "ts": now})
    elif kind in ("resolve", "reject"):
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


def change(main, op, author="you", version=None):
    """Apply one operation to the comments, under the lock. With `version`, refuse if the file changed since.
    Returns (result, comments text before, new version)."""
    with file_lock(main):
        data, v = read_comments(main)
        if version is not None and version != v:
            raise OpError("The comments changed. They have been reloaded; please try again.")
        before = comments_text(main)
        res = apply_op(main, data, op, author)
        return res, before, write_comments(main, data)


def accept(main, tids, version=None):
    """Accept suggested changes: put the new text of each into its source file, and remove the threads. Changes that no
    longer fit (the old text is not in the file) stay, and their ids come back in `skipped`.
    Returns (accepted ids, skipped ids, comments text before, {file: text before}, new version)."""
    with file_lock(main):
        data, v = read_comments(main)
        if version is not None and version != v:
            raise OpError("The comments changed. They have been reloaded; please try again.")
        before = comments_text(main)
        files, texts, done, skipped = {}, {}, [], []
        for tid in tids:
            t = find_thread(data, tid)
            if not t.get("change") or not t.get("anchor"):
                raise OpError(f"#{tid} is not a suggested change.")
            p = source_path(main, t["anchor"]["file"])
            if p not in texts:
                texts[p] = read_source(p)
                files[str(p)] = texts[p][0]
            text, enc = texts[p]
            loc = locate(text, {**t["anchor"], "quote": t["change"]["old"]})
            if not loc:
                skipped.append(tid)
                continue
            texts[p] = (text[:loc[0]] + t["change"]["new"] + text[loc[1]:], enc)
            data["threads"].remove(t)
            done.append(tid)
        for p, (text, enc) in texts.items():
            if text != files[str(p)]:
                write_source(p, text, enc)
        files = {k: x for k, x in files.items() if texts[Path(k)][0] != x}
        return done, skipped, before, files, write_comments(main, data)


def status_of(t):
    if t.get("change"):
        return "change"
    return "answered" if t["msgs"] and t["msgs"][-1].get("author") == "claude" else "open"


def threads_json(main, data):
    """The threads for the page and `list`, with the line of each anchor now (the file can change)."""
    out, texts = [], {}
    for t in data["threads"]:
        a = t.get("anchor")
        cur = None
        if a:
            try:
                p = source_path(main, a.get("file"))
                if p not in texts:
                    texts[p] = read_source(p)[0]
                loc = locate(texts[p], {**a, "quote": (t.get("change") or {}).get("old") or a.get("quote")})
                cur = {**a, "line": line_of(texts[p], loc[0]), "found": True} if loc else {**a, "found": False}
            except OpError:
                cur = {**a, "found": False}
        out.append({"id": t["id"], "anchor": cur, "status": status_of(t), "change": t.get("change"),
                    "msgs": [{"author": m.get("author") or "you", "text": m.get("text", ""), "ts": m.get("ts"),
                              "edited": m.get("edited")} for m in t["msgs"]]})
    return out


# --------------------------------------------------------------------------
# The compile: latexmk into ~/.cache/texview/build/KEY, and its log
# --------------------------------------------------------------------------

ENGINES = {"pdflatex": "-pdf", "lualatex": "-lualatex", "xelatex": "-xelatex", "latex": "-pdfdvi",
           "luatex": "-lualatex", "xetex": "-xelatex"}
FILE_LINE_RE = re.compile(r"^(.*?\.(?:tex|sty|cls|ltx|def|cfg|bbx|cbx|clo|dtx)):(\d+): (.*)$")
WARN_RE = re.compile(r"^(?:LaTeX|Package (\S+)|Class (\S+)) Warning: (.*)$")
INPUT_LINE_RE = re.compile(r"on input line (\d+)\.?")


def engine_flag(main):
    """The latexmk option of the engine: the magic comment "% !TEX program = lualatex" in the first lines, or -pdf.
    With a latexmkrc file in the folder and no magic comment, latexmk decides."""
    try:
        head = main.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        head = ""
    m = re.search(r"^\s*%\s*!\s*TEX\s+(?:TS-)?program\s*=\s*(\w+)", head, re.I | re.M)
    if m and m.group(1).lower() in ENGINES:
        return [ENGINES[m.group(1).lower()]]
    if any((main.parent / f).is_file() for f in ("latexmkrc", ".latexmkrc")):
        return []
    return ["-pdf"]


def parse_log(main, log_text, blg_text=""):
    """(errors, warnings) of a compile: [{"file": rel or None, "line": n or None, "msg": text}]."""
    errors, warnings, stack = [], [], []
    lines = log_text.splitlines()

    def rel(f):
        try:
            p = (main.parent / f).resolve()
            return rel_of(main, p) if main.parent in p.parents else None
        except (OSError, ValueError):
            return None

    for i, l in enumerate(lines):
        # the files that TeX opens and closes: "(./sections/intro.tex" ... ")" (a rough count, for warnings)
        for m in re.finditer(r"\((\.?/?[^\s()]+\.(?:tex|sty|cls|ltx|bbl|aux|def|cfg|clo))|\)", l):
            if m.group(1):
                stack.append(m.group(1))
            elif stack:
                stack.pop()
        m = FILE_LINE_RE.match(l)
        if m:
            msg = m.group(3)
            ctx = next((x for x in lines[i + 1:i + 8] if re.match(r"^l\.\d+ ", x)), "")
            if ctx:
                msg += "  (" + ctx.strip()[:160] + ")"
            errors.append({"file": rel(m.group(1)), "line": int(m.group(2)), "msg": msg[:400]})
            continue
        if l.startswith("! ") and not any(e["msg"].startswith(l[2:40]) for e in errors):
            errors.append({"file": None, "line": None, "msg": l[2:400]})
            continue
        m = WARN_RE.match(l)
        if m and len(warnings) < 200:
            text = m.group(3)
            k = i + 1
            while k < len(lines) and lines[k].startswith(" ") and lines[k].strip() and k < i + 4:  # a long warning
                text += " " + lines[k].strip()
                k += 1
            who = m.group(1) or m.group(2)
            ln = INPUT_LINE_RE.search(text)
            here = next((rel(f) for f in reversed(stack) if f.endswith(".tex") and rel(f)), None)
            warnings.append({"file": here if ln else None, "line": int(ln.group(1)) if ln else None,
                             "msg": (f"{who}: " if who else "") + text[:300]})
    for m in re.finditer(r"^(.*)\n?---line (\d+) of file (\S+)", blg_text, re.M):  # BibTeX errors
        errors.append({"file": rel(m.group(3)), "line": int(m.group(2)), "msg": "BibTeX: " + m.group(1).strip()[:300]})
    for m in re.finditer(r"^ERROR - (.*)$", blg_text, re.M):  # biber errors
        errors.append({"file": None, "line": None, "msg": "biber: " + m.group(1)[:300]})
    return errors, warnings


class Project:
    """One main file: its compile, its watcher, and its state for the page."""

    def __init__(self, main):
        self.main = main
        self.build = BUILD_DIR / (key_of(main) + "-" + re.sub(r"[^\w.-]", "_", main.stem)[:40])
        self.lock = threading.Lock()
        self.proc = None
        self.pending = False
        self.seen = 0.0      # the last time a page asked for news: the watcher stops when nobody looks
        self.snap = {}       # the times of the source files at the start of the last compile
        self.state = {"status": "idle", "seq": 0}
        self.parsed_files = {}  # path -> (modification time, result): the data of the suggestions in the editor
        if self.pdf().is_file():  # a compile of an earlier server
            self.state.update(self.read_logs(), status="old", pdf=self.pdf_version())

    def pdf(self):
        return self.build / (self.main.stem + ".pdf")

    def pdf_version(self):
        try:
            st = self.pdf().stat()
            return f"{st.st_mtime_ns}-{st.st_size}"
        except OSError:
            return ""

    def inputs(self):
        """The source files that the last compile read: the files of <stem>.fls and of <stem>.fdb_latexmk (BibTeX
        files too) in the folder of the main file."""
        out = []
        for name, rx in ((self.main.stem + ".fdb_latexmk", r'^\s+"([^"]+)"'), (self.main.stem + ".fls", r"^INPUT (.+)$")):
            try:
                text = (self.build / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(rx, text, re.M):
                f = m.group(1).strip()
                p = Path(f) if os.path.isabs(f) else (self.main.parent / f)
                try:
                    p = p.resolve()
                except OSError:
                    continue
                if self.main.parent in p.parents and p.suffix.lower() in SRC_EXT and p not in out \
                        and not is_hidden(p.relative_to(self.main.parent)):
                    out.append(p)
        return out

    def parsed(self, f, parse):
        """parse(text of f). The result stays in memory, and texview reads f again only when its modification time changes."""
        mtime = f.stat().st_mtime_ns
        hit = self.parsed_files.get(f)
        if not hit or hit[0] != mtime:
            hit = self.parsed_files[f] = (mtime, parse(read_source(f)[0]))
        return hit[1]

    def completions(self):
        """The data of the suggestions that the page cannot find in its open buffers: the labels of the last compile, the
        citation keys of the .bib files, and the definitions in each file of the document (the page drops the open files)."""
        labels, cites, sources = {}, set(), {}
        for f in sorted(self.build.rglob("*.aux"))[:500]:
            try:
                labels.update(self.parsed(f, aux_labels))
            except (OpError, OSError):
                continue
        for s in list_sources(self.main, self.inputs()):
            try:
                f = source_path(self.main, s["path"])  # the access check of /api/src
                if f.suffix.lower() == ".bib":
                    cites.update(self.parsed(f, bib_keys))
                elif s["input"]:
                    sources[s["path"]] = self.parsed(f, tex_defs)
            except (OpError, OSError):
                continue
        return {"labels": labels, "cites": sorted(cites), "sources": sources}

    def mtimes(self):
        out = {}
        for p in [self.main] + self.inputs():
            try:
                out[str(p)] = p.stat().st_mtime_ns
            except OSError:
                out[str(p)] = None
        return out

    def snapshot(self):
        with self.lock:
            s = json.loads(json.dumps(self.state))
        return s

    def request(self):
        """Compile now, or again after the compile that runs."""
        with self.lock:
            if self.state["status"] == "running":
                self.pending = True
                return
            self.state.update(status="running", started=time.strftime("%H:%M:%S"))
            self.state["seq"] += 1
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        with self.lock:
            self.pending = False
            proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def read_logs(self):
        def read(name):
            try:
                return (self.build / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
        errors, warnings = parse_log(self.main, read(self.main.stem + ".log"), read(self.main.stem + ".blg"))
        return {"errors": errors[:100], "warnings": warnings}

    def run(self):
        while True:
            self.snap = self.mtimes()
            t0 = time.time()
            exe = tool("latexmk")
            if not exe:
                result = {"status": "failed", "errors": [{"file": None, "line": None, "msg":
                          "latexmk is missing. Install TeX Live or MacTeX (brew install --cask mactex-no-gui)."}],
                          "warnings": [], "output": ""}
            else:
                result = self.compile(exe)
            result["ms"] = int((time.time() - t0) * 1000)
            with self.lock:
                again, self.pending = self.pending, False
                self.state.update(result)
                self.state["pdf"] = self.pdf_version()
                self.state["seq"] += 1
                if not again:
                    # the files that this compile found for the first time (\input files, .bib files) join the snapshot
                    self.snap = {**{k: v for k, v in self.mtimes().items() if k not in self.snap}, **self.snap}
                    return
                self.state.update(status="running", started=time.strftime("%H:%M:%S"))  # a change came in meanwhile

    def compile(self, exe):
        root = self.main.parent
        self.build.mkdir(parents=True, exist_ok=True)
        # \include{sec/a} writes sec/a.aux in the build folder: make the folders of the .tex files there
        for s in list_sources(self.main, self.inputs()):
            d = Path(s["path"]).parent
            if s["path"].endswith(".tex") and str(d) != ".":
                (self.build / d).mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([str(Path(exe).parent), env.get("PATH", ""), *TEX_DIRS])
        env["max_print_line"] = "10000"  # one log line for each message, so the log is easy to read
        env["error_line"], env["half_error_line"] = "254", "238"
        # -f: go on after errors, so a PDF comes out when TeX can make one (the errors show in the page)
        cmd = [exe, *engine_flag(self.main), "-synctex=1", "-interaction=nonstopmode", "-file-line-error", "-f",
               f"-outdir={self.build}", self.main.name]
        try:
            proc = subprocess.Popen(cmd, cwd=str(root), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True)
        except OSError as e:
            return {"status": "failed", "errors": [{"file": None, "line": None, "msg": f"Cannot start latexmk: {e}"}],
                    "warnings": [], "output": ""}
        with self.lock:
            self.proc = proc
        try:
            out, _ = proc.communicate(timeout=BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            out = proc.communicate()[0]
            out += f"\ntexview: stopped after {BUILD_TIMEOUT} s."
        with self.lock:
            self.proc = None
        logs = self.read_logs()
        code = proc.returncode
        status = "ok" if code == 0 else ("error" if self.pdf().is_file() else "failed")
        if code != 0 and not logs["errors"]:
            tail = [x for x in out.splitlines() if x.strip()][-6:]
            logs["errors"] = [{"file": None, "line": None, "msg": "latexmk: " + " / ".join(tail)[-600:]}]
        return {"status": status, "output": out[-8000:], "code": code, **logs}

    def tick(self):
        """The watcher: compile when a source file has changed since the last compile."""
        if time.time() - self.seen > 90:
            return
        with self.lock:
            if self.state["status"] == "running":
                return
        now = self.mtimes()
        if now != self.snap:
            self.request()

    def synctex(self, args):
        exe = tool("synctex")
        if not exe or not self.pdf().is_file():
            return None
        try:
            r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=20, cwd=str(self.main.parent))
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout

    def edit_at(self, page, x, y):
        """The source place of a point of the PDF: {file, line, column}, or None."""
        out = self.synctex(["edit", "-o", f"{page}:{x:.2f}:{y:.2f}:{self.pdf()}"])
        if not out:
            return None
        m, ln = re.search(r"^Input:(.+)$", out, re.M), re.search(r"^Line:(-?\d+)$", out, re.M)
        if not m or not ln:
            return None
        p = Path(m.group(1).strip())
        p = (p if p.is_absolute() else self.main.parent / p).resolve()
        col = re.search(r"^Column:(-?\d+)$", out, re.M)
        try:
            rel = rel_of(self.main, p)
        except ValueError:
            return {"file": None, "path": str(p), "line": int(ln.group(1))}
        return {"file": rel, "line": max(1, int(ln.group(1))), "column": int(col.group(1)) if col else -1}

    def view_at(self, rel, line):
        """The places of a source line in the PDF: {page, boxes: [[x0, y0, x1, y1], ...]} on its first page, or None."""
        p = source_path(self.main, rel)
        out = self.synctex(["view", "-i", f"{line}:0:{p}", "-o", str(self.pdf())])
        if not out:
            return None
        recs = []
        for block in out.split("Output:")[1:]:
            v = dict(re.findall(r"^(\w+):(.*)$", block, re.M))
            try:
                recs.append((int(v["Page"]), float(v["h"]), float(v["v"]), float(v["W"]), float(v["H"])))
            except (KeyError, ValueError):
                continue
        if not recs:
            return None
        page = recs[0][0]
        boxes = [[round(h, 1), round(vv - H - 1, 1), round(h + W, 1), round(vv + 2, 1)]
                 for pg, h, vv, W, H in recs if pg == page and W > 0]
        return {"page": page, "boxes": boxes[:40]}


projects = {}
projects_lock = threading.Lock()


def project_of(main):
    with projects_lock:
        return projects.setdefault(str(main), Project(main))


def watch_projects():
    while True:
        time.sleep(1)
        with projects_lock:
            ps = list(projects.values())
        for pr in ps:
            try:
                pr.tick()
            except Exception as e:  # never stop the watcher
                print(f"texview: watcher: {e}", flush=True)


# --------------------------------------------------------------------------
# Chat: a conversation with Claude about one document (the panel at the bottom right)
# --------------------------------------------------------------------------
# One `claude -p` process per message, all in one session per document. The page polls for the answer as it grows.

CHAT_DIR = STATE_DIR / "chat"
CHAT_TOOLS = ["Read", "Grep", "Glob", "Bash(texview:*)", "Bash(pdftotext:*)", "Bash(pdfinfo:*)", "WebSearch", "WebFetch"]
CHAT_TOOLS_ASK = ["Read", "Grep", "Glob", "Bash(texview list:*)", "Bash(texview log:*)", "Bash(texview text:*)",
                  "Bash(pdftotext:*)", "Bash(pdfinfo:*)", "WebSearch", "WebFetch"]
CHAT_DENY = ["Edit", "Write", "NotebookEdit", "Bash(texview build:*)"]  # the sources change only by Accept or Save
CHAT_DENY_ASK = CHAT_DENY + ["Bash(texview reply:*)", "Bash(texview add:*)", "Bash(texview suggest:*)"]
CHAT_SYSTEM = """You are in the chat panel of texview, a viewer and editor for LaTeX documents. The document is the file <FILE>.
The user sees the PDF, the source in an editor, and your reply in a narrow panel: keep it short and plain. Maths in
$...$ and $$...$$ is typeset there.
- Read the sources with the Read tool (they are in the folder of the main file). `texview text <FILE> --page N` prints
  the text of a page of the compiled PDF, and `texview log <FILE>` prints the errors and warnings of the last compile.
- You never edit a file. To change the text, suggest the change: the user sees it in the editor and accepts or rejects
  it. Write it like this, with the old text copied exactly from the file (the same spaces and line ends):
    texview suggest <FILE> --file sections/intro.tex --line 12 <<'EOF'
    <<<<<<< old
    the exact old text
    =======
    the new text
    >>>>>>> new
    A short reason (optional, one line or more).
    EOF
  One call can hold more blocks. Keep each block small: a sentence or a paragraph, not a whole section. --line is the
  line where the old text starts (it picks the place when the old text occurs more than once).
- The comments of the user are not in the sources. `texview list <FILE>` prints them. Answer a comment with
  `texview reply <FILE> ID - <<'EOF'` and the text on the next lines, then EOF. Add a comment with
  `texview add <FILE> - --file F --line N <<'EOF'` in the same way.
- Suggest changes, add comments or answer comments only when the user asks for it. Answer questions without changes.
- The user may tell you which passage they selected (in the source or in the PDF), the line of the cursor, and the page
  they look at. Use it to see what "this" means.
- Every message starts with a line in brackets. It tells you the mode of that message. Follow the last one."""
# The mode goes into every message, not into the system prompt: a resumed session keeps the system prompt of its first turn.
CHAT_MODE_EDIT = "(Mode: you may suggest changes, and add or answer comments, when the user asks. Answer questions without changing anything.)"
CHAT_MODE_ASK = "(Mode: Ask only. Do not suggest changes, and do not add or answer comments. Read and answer.)"
CHAT_QUOTE_MAX = 3000


def claude_exe():
    exe = os.environ.get("TEXVIEW_CLAUDE") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    if not os.access(exe, os.X_OK):
        raise RuntimeError("Can't find the claude command. Set TEXVIEW_CLAUDE to its path and restart texview.")
    return exe


# The Oracle: a background `claude -p "/answer-tex FILE"` run that answers the open comments (claudekit.Oracle).
ORACLE_TOOLS = ["Read", "Grep", "Glob", "Bash(texview:*)", "Bash(pdftotext:*)", "Bash(pdfinfo:*)", "WebSearch", "WebFetch"]
ORACLE = claudekit.Oracle(STATE_DIR / "claude", claude_exe, lambda p: f'/answer-tex "{p}"', ORACLE_TOOLS,
                          disallowed=CHAT_DENY)


def clean_ctx(s, n=CHAT_QUOTE_MAX):
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(s or "")).strip()[:n]


class Chat:
    def __init__(self, main):
        self.path = main
        self.file = CHAT_DIR / (key_of(main) + ".json")
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

    def send(self, text, ctx, ask_only):
        """Start one turn. ctx: {quote, where, cursor, page}. Returns None, or a message that says why it cannot start."""
        with self.lock:
            if self.cur is not None:
                return "Claude is still answering."
            if ORACLE.busy(self.path):
                return "The Oracle is answering the comments of this document. Wait for it, or stop it."
            try:
                exe = claude_exe()
            except RuntimeError as e:
                return str(e)
            now = int(time.time())
            user = {"role": "user", "text": text, "ts": now}
            if ctx.get("quote"):
                user["quote"] = clean_ctx(ctx["quote"], 1500)
                if ctx.get("where"):
                    user["where"] = clean_ctx(ctx["where"], 200)
            self.data["messages"].append(user)
            self.cur = {"role": "assistant", "parts": [], "ts": now, "streaming": True}
            self.stop_flag = False
            self.rev += 1
            self.save()
            threading.Thread(target=self.run, args=(exe, text, ctx, ask_only), daemon=True).start()
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

    def run(self, exe, text, ctx, ask_only):
        p = self.path
        before = comments_text(p)
        lines = []
        q = clean_ctx(ctx.get("quote"))
        if q:
            where = clean_ctx(ctx.get("where"), 200)
            lines.append(f"(The user has selected this passage{' in ' + where if where else ''}: "
                         + json.dumps(q, ensure_ascii=False) + ")")
        if ctx.get("cursor"):
            lines.append(f"(The cursor of the editor is at {clean_ctx(ctx['cursor'], 200)}.)")
        if ctx.get("page"):
            lines.append(f"(The user looks at page {int(ctx['page'])} of the PDF.)")
        mode = (CHAT_MODE_ASK if ask_only else CHAT_MODE_EDIT) + "\n"
        with self.lock:
            sid, started = self.data["session"], self.data.get("started")
        allowed, denied = (CHAT_TOOLS_ASK, CHAT_DENY_ASK) if ask_only else (CHAT_TOOLS, CHAT_DENY)
        prompt = mode + "".join(x + "\n" for x in lines) + ("\n" if lines else "") + text
        cmd = claudekit.build_command(exe, prompt, CHAT_SYSTEM.replace("<FILE>", str(p)), sid, started, allowed, denied)
        err, stderr, code = claudekit.run_stream(cmd, p.parent, claudekit.clean_env(doc=p), self.event, self.set_proc)
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


def chat_of(main):
    with chats_lock:
        return chats.setdefault(str(main), Chat(main))


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

NOT_GRANTED = "texview can only open documents in folders you have passed to it. To open this one, run: texview {}"


class App:
    def __init__(self, root, grants_file=None):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        # main -> list of {"version": comments version after, "comments": text before, "files": {path: text before},
        #                  "after": {path: text after}}
        self.undo = {}
        self.oracle_before = {}  # main -> the comments text when the Oracle started, until its end is seen
        self.grants_file = grants_file
        self.granted = set()  # files and folders passed to the texview command
        self.opened = set()   # the documents that pages opened: same names get longer labels
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
        """True if p was passed to texview, or is inside a folder that was."""
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

    def push_undo(self, p, before, files=None):
        try:
            version = read_comments(p)[1]
        except OpError:
            return
        after = {}
        for f in files or {}:
            try:
                after[f] = read_source(Path(f))[0]
            except OpError:
                after[f] = None
        with self.lock:
            stack = self.undo.setdefault(str(p), [])
            stack.append({"version": version, "comments": before, "files": files or {}, "after": after})
            del stack[:-20]

    def can_undo(self, p, version):
        stack = self.undo.get(str(p)) or []
        return bool(stack) and stack[-1]["version"] == version

    def undo_last(self, p, version):
        """Take back the last change: the comments, and the source files that an Accept changed."""
        with self.lock:
            stack = self.undo.setdefault(str(p), [])
            with file_lock(p):
                cur = read_comments(p)[1]
                if version != cur or not stack or stack[-1]["version"] != cur:
                    raise OpError("Nothing to undo.")
                u = stack[-1]
                for f, text in u["after"].items():
                    try:
                        now = read_source(Path(f))[0]
                    except OpError:
                        now = None
                    if now != text:
                        raise OpError(f"{Path(f).name} changed after that change. Undo it in the editor (⌘/Ctrl+Z).")
                stack.pop()
                for f, text in u["files"].items():
                    write_source(Path(f), text, read_source(Path(f))[1])
                restore_comments(p, u["comments"])
        return bool(u["files"])


class Handler(BaseHTTPRequestHandler):
    server_version = "texview"
    app: App = None

    def log_message(self, *args):
        pass

    # ----- helpers -----
    def host_ok(self):
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host.lower() in serverkit.local_hosts("texview")

    def cookie_name(self):
        return f"texview{self.server.server_address[1]}"  # cookies ignore ports, so name it per port

    def authorized(self):
        """The request carries the access key, as the cookie set when the link was opened."""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.cookie_name() and TOKEN and hmac.compare_digest(value, TOKEN):
                return True
        return False

    def deny(self, api):
        msg = "This page's access key is missing or out of date. Reopen the file with the texview command."
        if api:
            return self.send_json(403, {"error": msg})
        return self.send(403, f"<!doctype html><meta charset=utf-8><title>texview</title>"
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

    def main_path(self, q):
        """(path, rel, None) for a main file that may be opened, else (None, rel, (code, error))."""
        rel = (q.get("f") or [""])[0]
        p = self.app.resolve(rel)
        if not p or p.suffix.lower() != ".tex":
            return None, rel, (404, f"File not found: {rel}")
        if not self.app.allowed(p):
            return None, rel, (403, NOT_GRANTED.format(p))
        return p, self.app.rel(p), None

    def payload(self, p, rel):
        data, version = read_comments(p)
        pr = project_of(p)
        label = serverkit.doc_label(p, self.app.opened)  # a short name for the tab and the header
        self.app.opened.add(p)
        return {"name": p.name, "label": label, "rel": rel, "path": str(p), "dir": self.app.rel(p.parent),
                "dirOk": self.app.allowed(p.parent), "threads": threads_json(p, data), "version": version,
                "sources": list_sources(p, pr.inputs()), "up": self.parent_docs(p), "build": pr.snapshot(),
                "canUndo": self.app.can_undo(p, version)}

    def parent_docs(self, p):
        """The documents in the folder above the folder of p, if that folder may be opened (the same rule as the
        "↑ .." row of the folder page). [{"path": rel to the root, "name": "../name.tex"}]"""
        d = p.parent
        if d == self.app.root or not self.app.allowed(d.parent):
            return []
        try:
            entries = sorted(d.parent.iterdir(), key=lambda e: e.name.lower())
        except OSError:
            return []
        return [{"path": self.app.rel(e), "name": "../" + e.name} for e in entries
                if not e.name.startswith(".") and e.is_file() and is_main_tex(e)][:200]

    # ----- routes -----
    def do_GET(self):
        if not self.host_ok():
            return self.send(403, "Forbidden")
        url = urlparse(self.path)
        q = parse_qs(url.query)
        app = self.app

        if url.path == "/api/ping":  # open to all: the command line uses it to find the server
            return self.send_json(200, {"app": "texview", "pid": os.getpid(), "root": str(app.root),
                                        "started": STARTED, "code": str(Path(__file__).resolve())})

        # The tab icons are open to all, like /api/ping: Safari loads the tab icon without the cookie.
        if url.path == "/favicon.ico" or url.path.startswith("/kit/icons/"):
            name = "icons/tex.png" if url.path == "/favicon.ico" else unquote(url.path[len("/kit/"):])
            a = serverkit.kit_asset(name)
            return self.send(200, a[0], a[1]) if a else self.send(404, "Not found")

        # A link from the texview command carries the key as ?t=. Keep it as a cookie and reload without it.
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
        if url.path.startswith("/vendor/"):  # the local copy of CodeMirror
            f = (VENDOR / unquote(url.path[len("/vendor/"):])).resolve()
            if VENDOR.resolve() not in f.parents or not f.is_file() or f.suffix not in VENDOR_TYPES:
                return self.send(404, "Not found")
            return self.send(200, f.read_bytes(), VENDOR_TYPES[f.suffix])

        if url.path in ("/pdf", "/api/doc", "/api/state", "/api/src", "/api/complete", "/api/sync/edit", "/api/sync/view",
                        "/api/chat", "/api/account", "/api/claude", "/api/improve/dash", "/api/improve/preview"):
            p, rel, err = self.main_path(q)
            if err:
                return self.send_json(err[0], {"error": err[1]})
            pr = project_of(p)
            arg = lambda k: (q.get(k) or [""])[0]
            try:
                if url.path == "/pdf":  # the compiled PDF, for PDF.js
                    f = pr.pdf()
                    if not f.is_file():
                        return self.send(404, "Not compiled yet", "text/plain; charset=utf-8")
                    return self.send(200, f.read_bytes(), "application/pdf")
                if url.path == "/api/state":  # what the page polls: the compile, the comments, the open source
                    pr.seen = time.time()
                    out = {"build": pr.snapshot(), "version": read_comments(p)[1]}
                    if arg("src"):
                        try:
                            out["src"] = text_version(read_source(source_path(p, arg("src")))[0])
                        except OpError:
                            out["src"] = None
                    return self.send_json(200, out)
                if url.path == "/api/src":
                    f = source_path(p, arg("path"))
                    text, enc = read_source(f)
                    return self.send_json(200, {"path": rel_of(p, f), "text": text, "version": text_version(text),
                                                "encoding": enc})
                if url.path == "/api/complete":  # the suggestions in the source editor: only names and numbers
                    return self.send_json(200, pr.completions())
                if url.path == "/api/sync/edit":
                    r = pr.edit_at(int(arg("page")), float(arg("x")), float(arg("y")))
                    return self.send_json(200 if r else 404, r or {"error": "No source place here. Compile first."})
                if url.path == "/api/sync/view":
                    r = pr.view_at(arg("file"), int(arg("line") or 1))
                    return self.send_json(200 if r else 404, r or {"error": "No place in the PDF for this line."})
            except OpError as e:
                return self.send_json(400, {"error": str(e)})
            except ValueError:
                return self.send_json(400, {"error": "Bad request"})
            if url.path == "/api/improve/dash":  # the dashboard of the notes (self-improving apps)
                return self.send_json(*improvekit.dash_api("texview", HERE, "GET"))
            if url.path == "/api/improve/preview":  # in the sandbox of a proposal: the band with Accept and Reject
                return self.send_json(*improvekit.preview_api("GET"))
            if url.path == "/api/claude":
                st = ORACLE.status(p)
                app.oracle_seen(p, st)
                return self.send_json(200, st)
            if url.path == "/api/account":  # the Claude Code account of the chat and the Oracle (claudekit)
                return self.send_json(*claudekit.account_api("GET", doc=p))
            if url.path == "/api/chat":
                since = arg("since")
                return self.send_json(200, chat_of(p).snapshot(int(since) if since.isdigit() else None))
            try:
                pr.seen = time.time()
                if pr.state["status"] in ("idle", "old"):  # the first page of this document since the server started
                    pr.request()
                return self.send_json(200, self.payload(p, rel))
            except OpError as e:
                return self.send_json(500, {"error": str(e)})

        self.send(404, "Not found")

    def do_POST(self):
        # The custom header forces a CORS preflight, so other websites can't post here.
        if not self.host_ok() or self.headers.get("X-TeXView") != "1":
            return self.send_json(403, {"error": "Forbidden"})
        if not self.authorized():
            return self.deny(True)
        path = urlparse(self.path).path
        if path not in ("/api/op", "/api/save", "/api/build", "/api/grant", "/api/chat", "/api/account", "/api/claude",
                        "/api/improve/dash", "/api/improve/preview"):
            return self.send_json(404, {"error": "Not found"})
        try:
            op = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if not isinstance(op, dict):
                raise ValueError
        except (ValueError, TypeError):
            return self.send_json(400, {"error": "Bad request"})

        if path == "/api/grant":  # the texview command: allow a file or folder you passed to it
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

        p, rel, err = self.main_path({"f": [str(op.get("f", ""))]})
        if err:
            return self.send_json(err[0], {"error": err[1]})
        pr = project_of(p)

        if path == "/api/improve/dash":  # the dashboard: add, edit or delete a waiting note, or send them all
            if improvekit.preview_of():
                return self.send_json(409, {"error": "This is a preview. Write notes in the live texview tab."})
            port = self.server.server_address[1]
            back = f"http://{serverkit.app_host('texview')}:{port}/view?f={quote(rel)}"
            return self.send_json(*improvekit.dash_api("texview", HERE, "POST", op, p, back))

        if path == "/api/improve/preview":  # the decision in the preview tab: accept, change or reject
            return self.send_json(*improvekit.preview_api("POST", op))

        if path == "/api/build":  # compile now, or stop the compile
            if op.get("action") == "stop":
                pr.stop()
            else:
                pr.request()
            return self.send_json(200, {"build": pr.snapshot()})

        if path == "/api/save":  # the editor: write a source file, then compile
            try:
                f = source_path(p, op.get("path"))
                text = op.get("text")
                if not isinstance(text, str) or len(text) > SRC_MAX:
                    raise OpError("Nothing to save.")
                with file_lock(p):
                    try:
                        cur, enc = read_source(f)
                    except OpError:
                        cur, enc = None, "utf-8"
                    if cur is not None and op.get("base") and text_version(cur) != op.get("base"):
                        return self.send_json(409, {"error": f"{f.name} changed on disk since you opened it.",
                                                    "text": cur, "version": text_version(cur)})
                    try:
                        write_source(f, text, enc)
                    except UnicodeEncodeError:
                        raise OpError(f"{f.name} is a Latin-1 file, and the text has characters that Latin-1 cannot hold.")
            except OpError as e:
                return self.send_json(400, {"error": str(e)})
            pr.request()
            data, version = read_comments(p)
            return self.send_json(200, {"version": text_version(read_source(f)[0]), "threads": threads_json(p, data),
                                        "commentsVersion": version, "build": pr.snapshot()})

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
                ctx = {k: op.get(k) for k in ("quote", "where", "cursor")}
                ctx["page"] = op.get("view_page") if isinstance(op.get("view_page"), int) else None
                err = chat.send(text, ctx, bool(op.get("ask_only")))
            return self.send_json(409 if err else 200, {"error": err} if err else {"ok": True})

        if path == "/api/claude":  # the Oracle: start or stop the run of this document, or forget its session
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

        # /api/op: a change to the comments (and Accept, which changes a source file too)
        res, extra = None, {}
        try:
            kind = op.get("op")
            if kind == "undo":
                if self.app.undo_last(p, op.get("version")):
                    pr.request()
            elif kind in ("accept", "accept-all"):
                data, _ = read_comments(p)
                tids = [t["id"] for t in data["threads"] if t.get("change")] if kind == "accept-all" \
                    else [int(op.get("thread") or 0)]
                done, skipped, before, files, _ = accept(p, tids, op.get("version"))
                if done:
                    self.app.push_undo(p, before, files)
                    pr.request()
                extra = {"accepted": done, "skipped": skipped}
            else:
                res, before, version = change(p, op, "you", op.get("version"))
                self.app.push_undo(p, before)
        except OpError as e:
            return self.send_json(409 if "changed" in str(e) else 400, {"error": str(e)})
        out = {**self.payload(p, rel), **extra}
        if op.get("op") == "add":
            out["added"] = res
        self.send_json(200, out)

    def index_page(self, d):
        """A folder browser: subfolders, then the LaTeX documents (.tex files with \\documentclass)."""
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
            if e.name.startswith(".") or not (e.is_file() and e.suffix.lower() == ".tex"):
                continue
            if not is_main_tex(e):
                continue
            try:
                n_open = sum(status_of(t) in ("open", "change") for t in read_comments(e)[0]["threads"])
            except OpError:
                n_open = 0
            badge = f'<span class="b">{n_open} open</span>' if n_open else ""
            rows.append(f'<li><a href="/view?f={quote(self.app.rel(e))}">📄 {html.escape(e.name)}</a>{badge}</li>')
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(d.name or str(d))}/ · texview</title>
<link rel="icon" type="image/png" href="/kit/icons/tex.png">
<style>
:root{{color-scheme:dark;--bg:#111214;--fg:#d6d9de;--muted:#80858d;--accent:#8a9bd6;--hl:#3a3420}}
@media (prefers-color-scheme:light){{:root{{color-scheme:light;--bg:#fafaf7;--fg:#222;--muted:#666;--accent:#2f6fde;--hl:#ffe58a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,sans-serif;padding:32px 16px}}
main{{max-width:680px;margin:0 auto}} h1{{font-size:18px;overflow-wrap:anywhere}} ul{{padding:0;list-style:none}}
li{{padding:5px 0;border-bottom:1px solid rgba(128,128,128,.2);display:flex;gap:10px;align-items:center}}
a{{color:var(--accent);text-decoration:none;overflow-wrap:anywhere}} .b{{background:var(--hl);border-radius:10px;padding:0 8px;font-size:12px}}
</style></head><body><main><h1>{html.escape(str(d))}</h1><ul>{"".join(rows) or "<li>No LaTeX documents here.</li>"}</ul></main></body></html>"""


def run_server(port):
    """Serve in the foreground. Restarts itself when this file changes."""
    global STARTED, TOKEN
    STARTED = time.strftime("%Y-%m-%d %H:%M:%S")
    TOKEN = read_token(port) or new_token(port)  # kept across code-change restarts, so open tabs keep working
    Handler.app = App(ROOT, grants_file(port))  # grants survive code-change restarts
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        sys.exit(f"texview: can't listen on port {port}: {e}")
    threading.Thread(target=watch_projects, daemon=True).start()

    def stop_builds():
        with projects_lock:
            for pr in projects.values():
                pr.stop()
    DAEMON.serve(server, port, f"texview: serving {ROOT} at http://127.0.0.1:{port}/ (pid {os.getpid()}, {STARTED})",
                 on_stop=stop_builds)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def open_target(target, port, open_browser):
    p = Path(target).expanduser().resolve()
    if not p.exists():
        sys.exit(f"texview: not found: {target}")
    if p.is_file() and p.suffix.lower() != ".tex":
        sys.exit(f"texview: not a .tex file: {target}")
    root = Path(ensure_server(port)["root"])  # the running server's root, which may predate TEXVIEW_ROOT
    if p != root and root not in p.parents:
        sys.exit(f"texview: {p} is outside {root}, the folder the server can open.\n"
                 f"Restart it with a wider root, e.g.: TEXVIEW_ROOT=/ texview --restart")
    if is_hidden(p.relative_to(root)):
        sys.exit(f"texview: {p} is in a hidden folder (or is a hidden file). texview never opens those, to keep\n"
                 f"folders like ~/.ssh and ~/.claude out of reach. Copy the files somewhere else to read them.")
    rel = p.relative_to(root).as_posix() if p != root else ""
    token = read_token(port)
    if not token:
        sys.exit("texview: the server's access key is missing. Restart it: texview --restart")
    grant(port, token, p.parent if p.is_file() else p)  # a document needs the sources in its folder
    url = f"http://{serverkit.app_host('texview')}:{port}/" + (f"view?f={quote(rel)}" if p.is_file() else f"?d={quote(rel)}")
    url += "&t=" + token
    print(url)
    if open_browser:
        webbrowser.open(url)


def grant(port, token, path):
    """Allow the server to open `path` (a file or a folder and everything in it)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/grant", data=json.dumps({"path": str(path)}).encode(),
        headers={"Content-Type": "application/json", "X-TeXView": "1", "Cookie": f"texview{port}={token}"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except ValueError:
            msg = e.reason
        sys.exit(f"texview: the server won't open {path}: {msg}")


def main_arg(s):
    p = Path(s).expanduser().resolve()
    if not p.is_file() or p.suffix.lower() != ".tex":
        sys.exit(f"texview: not a .tex file: {s}")
    return p


def cmd_list(p, as_json):
    data = read_comments(p)[0]
    threads = threads_json(p, data)
    if as_json:
        print(json.dumps(threads, indent=2, ensure_ascii=False))
        return
    if not threads:
        print("No comments and no suggested changes.")
    for t in threads:
        a = t["anchor"]
        if a:
            q = a["quote"].replace("\n", " ")
            where = f'{a["file"]}:{a["line"]}' + ("" if a.get("found") else " (text not found)") + \
                    ("" if t["change"] else f'  "{q[:70] + "…" if len(q) > 70 else q}"')
        else:
            where = "general"
        print(f'#{t["id"]}  {where}  [{t["status"]}]')
        if t["change"]:
            for k in ("old", "new"):
                print(f"    {k}: " + t["change"][k].replace("\n", "\n         "))
        for m in t["msgs"]:
            print(f'    {m["author"]}: {m["text"]}')


def cmd_change(p, op, author):
    try:
        res = change(p, op, author)[0]
    except OpError as e:
        sys.exit(f"texview: {e}")
    print(f"texview: {'added comment #' + str(res) if op['op'] == 'add' else 'replied to #' + str(res)}.")


SUGGEST_HELP = """The changes come on stdin, as one or more blocks:

<<<<<<< old
the exact old text, copied from the file
=======
the new text (empty: delete the old text)
>>>>>>> new
A reason (optional): the lines after a block, up to the next block.

Use a quoted heredoc, so the shell keeps backslashes and dollars: texview suggest main.tex --file intro.tex <<'EOF'"""


def parse_changes(text):
    """[(old, new, note)] from blocks of <<<<<<< old / ======= / >>>>>>> new, each with an optional note after it."""
    out = []
    rx = re.compile(r"^<{7}[ \t]*old[ \t]*\n(.*?)^={7}[ \t]*\n(.*?)^>{7}[ \t]*new[ \t]*(?:\n|$)", re.S | re.M)
    ms = list(rx.finditer(text))
    for i, m in enumerate(ms):
        old, new = m.group(1), m.group(2)
        old = old[:-1] if old.endswith("\n") else old
        new = new[:-1] if new.endswith("\n") else new
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out.append((old, new, text[m.end():end].strip()))
    return out


def cmd_suggest(p, rel, line, text, author):
    changes = parse_changes(text)
    if not changes:
        sys.exit("texview: no change found. " + SUGGEST_HELP)
    errors = 0
    for old, new, note in changes:
        try:
            tid = change(p, {"op": "suggest", "anchor": {"file": rel, "line": line}, "old": old, "new": new,
                             "text": note}, author)[0]
            print(f"texview: suggested change #{tid} in {rel}.")
        except OpError as e:
            errors += 1
            print(f"texview: {e}", file=sys.stderr)
    if errors:
        sys.exit(1)


def cmd_log(p):
    pr = Project(p)
    if not pr.pdf().is_file() and not (pr.build / (p.stem + ".log")).is_file():
        sys.exit("texview: not compiled yet. Open it with texview, or run: texview build " + str(p))
    print_log(pr.read_logs())


def print_log(logs, output=""):
    errs, warns = logs["errors"], logs["warnings"]
    print(f"{len(errs)} error{'s' if len(errs) != 1 else ''}, {len(warns)} warning{'s' if len(warns) != 1 else ''}.")
    for kind, xs in (("error", errs), ("warning", warns)):
        for e in xs:
            where = f'{e["file"] or "?"}:{e["line"]}' if e.get("line") else (e.get("file") or "")
            print(f"{kind}: {where + ': ' if where else ''}{e['msg']}")


def cmd_build(p):
    pr = Project(p)
    exe = tool("latexmk")
    if not exe:
        sys.exit("texview: latexmk is missing. Install TeX Live or MacTeX.")
    r = pr.compile(exe)
    print(f"texview: {r['status']} ({pr.pdf()})")
    print_log(r)
    if r["status"] == "failed":
        sys.exit(1)


def cmd_text(p, page, pages):
    exe = shutil.which("pdftotext")
    pdf = Project(p).pdf()
    if not exe:
        sys.exit("texview: `text` needs pdftotext (brew install poppler). Or read the sources with the Read tool.")
    if not pdf.is_file():
        sys.exit("texview: not compiled yet. Open it with texview, or run: texview build " + str(p))
    if page:
        first = last = page
    elif pages:
        m = re.fullmatch(r"(\d+)-(\d+)", pages)
        if not m:
            sys.exit("texview: --pages needs the form A-B, e.g. 3-5.")
        first, last = int(m.group(1)), int(m.group(2))
    else:
        out = subprocess.run([exe, "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True)
        sys.stdout.write(out.stdout)
        sys.exit(out.returncode)
    for n in range(first, last + 1):
        out = subprocess.run([exe, "-enc", "UTF-8", "-f", str(n), "-l", str(n), str(pdf), "-"],
                             capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f"texview: pdftotext failed on page {n}: {out.stderr.strip()}")
        print(f"=== page {n} ===")
        print(out.stdout.replace("\f", "").rstrip())


def sub_main(argv):
    """The subcommands for Claude and scripts."""
    ap = argparse.ArgumentParser(prog="texview")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list", help="print the comments and the suggested changes")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("reply", help="add a reply to a thread")
    s.add_argument("file")
    s.add_argument("id", type=int)
    s.add_argument("text", help='the reply, or "-" to read it from stdin')
    s.add_argument("--author", default="claude")
    s = sub.add_parser("add", help="add a comment, general or on a line of a source file")
    s.add_argument("file", help="the main .tex file")
    s.add_argument("text", help='the comment, or "-" to read it from stdin')
    s.add_argument("--file", dest="src", help="the source file of the comment (relative to the folder of the main file)")
    s.add_argument("--line", type=int, help="the line of the comment")
    s.add_argument("--quote", help="the exact words in that file (optional: the default is the whole line)")
    s.add_argument("--author", default="claude")
    s = sub.add_parser("suggest", help="suggest changes to a source file (they wait for Accept or Reject)",
                       description=SUGGEST_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("file", help="the main .tex file")
    s.add_argument("--file", dest="src", required=True, help="the source file to change (relative to the main folder)")
    s.add_argument("--line", type=int, help="the line where the old text starts (if it occurs more than once)")
    s.add_argument("--author", default="claude")
    s = sub.add_parser("log", help="print the errors and warnings of the last compile")
    s.add_argument("file")
    s = sub.add_parser("build", help="compile now (latexmk) and print the errors")
    s.add_argument("file")
    s = sub.add_parser("text", help="print the text of the compiled PDF, page by page (needs pdftotext)")
    s.add_argument("file")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--page", type=int)
    g.add_argument("--pages", help="A-B")
    a = ap.parse_args(argv)
    p = main_arg(a.file)
    if getattr(a, "text", None) == "-":  # the text from stdin: safe for $, quotes and new lines
        a.text = sys.stdin.read()
    if a.cmd == "list":
        cmd_list(p, a.json)
    elif a.cmd == "reply":
        cmd_change(p, {"op": "reply", "thread": a.id, "text": a.text}, a.author)
    elif a.cmd == "add":
        if a.line and not a.src:
            sys.exit("texview: --line needs --file.")
        anchor = {"file": a.src, "line": a.line, "quote": a.quote} if a.src else None
        if anchor and not a.line and not a.quote:
            sys.exit("texview: give --line or --quote with --file.")
        cmd_change(p, {"op": "add", "anchor": anchor, "text": a.text}, a.author)
    elif a.cmd == "suggest":
        if sys.stdin.isatty():
            sys.exit("texview: the changes come on stdin. " + SUGGEST_HELP)
        cmd_suggest(p, a.src, a.line, sys.stdin.read(), a.author)
    elif a.cmd == "log":
        cmd_log(p)
    elif a.cmd == "build":
        cmd_build(p)
    else:
        cmd_text(p, a.page, a.pages)


SUBCOMMANDS = ("list", "reply", "add", "suggest", "log", "build", "text")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "note":  # a note to the app, not about a document (viewkit/improvekit.py)
        return improvekit.note_main("texview", HERE, sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "try":  # try a proposal in a sandbox: copies of the document
        return improvekit.try_main("texview", HERE, sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS:
        return sub_main(sys.argv[1:])
    ap = argparse.ArgumentParser(
        prog="texview", description="Read, edit and comment on LaTeX documents in the browser, next to the PDF.",
        epilog=f"Subcommands: texview {{{','.join(SUBCOMMANDS)}}} FILE ..., texview note TEXT, and texview try N [FILE] "
               f"(see texview list -h). One shared server runs in the background on port {PORT} and can open any file "
               f"under {ROOT}. Its log is {LOG}.")
    ap.add_argument("target", nargs="*", help="the main .tex files or folders to open, each in its own browser tab "
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
        print(f"texview: running on http://127.0.0.1:{args.port}/ (pid {info['pid']}, since {info['started']}, "
              f"root {info['root']})" if info else "texview: not running.")
        return
    if args.stop or args.restart:
        stop_server(args.port)
        if args.stop:
            return
        info = ensure_server(args.port)
        print(f"texview: running (pid {info['pid']}).")
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
