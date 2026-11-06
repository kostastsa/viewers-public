#!/usr/bin/env python3
"""mdview: read and comment on Markdown files in the browser.

The Markdown file is the master copy. Comments live inside it as CriticMarkup:

    {==highlighted text==}{>>a comment<<}{>>@claude: a reply<<}
    {>>a general comment: alone on its own line<<}

Suggested changes, which the viewer shows as tracked changes to accept or reject:

    {~~old text~>new text~~}   {++inserted text++}   {--deleted text--}

Usage:
    mdview notes.md            # open a file (starts the shared server if needed)
    mdview                     # browse the current folder
    mdview notes.md --list     # print the comment threads (--json for JSON)
    mdview note TEXT           # a note to mdview itself: a problem or a wish (a GitHub issue). "-": stdin.
    mdview try N [notes.md]    # try the proposal of note N in a sandbox (port 8900+N, copies of the files)
    mdview --status | --stop | --restart

One server runs in the background on 127.0.0.1:8765 and serves every file under
your home folder (MDVIEW_ROOT and MDVIEW_PORT override these). It restarts itself
when this file or a .py file of ../viewkit changes. Requires Python 3.9+, pandoc, and
the folder viewkit next to this one (the code that mdview and pyview share).

Security: documents are rendered inert (safe.lua strips raw HTML, event handlers and
javascript: links; the viewer's Content-Security-Policy blocks any script but its own).
Every request needs the access key in ~/.cache/mdview/token-PORT (readable only by you),
which `mdview` passes in the link it opens and the browser then keeps as a cookie.
The server opens only the files and folders you have passed to `mdview` (plus the images
and media those documents refer to). Hidden files and folders (~/.ssh, ~/.claude, ...) are
never read, listed or saved, and /files/ serves only images, audio, video and PDFs.
"""
import argparse
import bisect
import hashlib
import hmac
import html
import json
import os
import posixpath
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
ART = HERE / "art"  # watermark images, kept out of git (see .gitignore)
ART_SCORES = ART / "figures" / "scores.json"  # {figure id: score}: +1 per click on a figure in the viewer, -1 per double click
ART_ID_RE = re.compile(r"p\d{3}-\d+|o\d{3}-\d{2}")  # a cut-out figure, or one organism of a Kunstformen plate
art_lock = threading.Lock()
FILTER = HERE / "safe.lua"
ROOT = Path(os.environ.get("MDVIEW_ROOT") or Path.home()).expanduser().resolve()
PORT = int(os.environ.get("MDVIEW_PORT") or 8765)
STATE_DIR = Path.home() / ".cache" / "mdview"
LOG = STATE_DIR / "server.log"
MD_SUFFIXES = (".md", ".markdown")
CLAUDE_DIR = STATE_DIR / "claude"  # per document: the run's lock, log, exit code and start time
# What an Ask Claude run may do without asking: read, edit, and list the comments.
CLAUDE_TOOLS = ["Read", "Edit", "Grep", "Glob", "Bash(mdview:*)", "Bash(grep:*)", "WebSearch", "WebFetch"]
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "Library"}
STARTED = None
TOKEN = None  # the access key, set when the server starts

sys.path.insert(0, str(HERE.parent / "viewkit"))  # the code that mdview and pyview share
try:
    import claudekit
    import improvekit
    import serverkit
except ImportError:
    sys.exit(f"mdview: the folder viewkit is missing. It must be next to mdview, at {HERE.parent / 'viewkit'}.")
DAEMON = serverkit.Daemon("mdview", STATE_DIR, __file__, "MDVIEW_PORT")
token_file, grants_file, write_private = DAEMON.token_file, DAEMON.grants_file, DAEMON.write_private
new_token, read_token = DAEMON.new_token, DAEMON.read_token
ping, port_in_use, ensure_server, stop_server = DAEMON.ping, DAEMON.port_in_use, DAEMON.ensure_server, DAEMON.stop_server

# What /files/ will serve: media for documents to embed or link to, nothing else.
MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".svg": "image/svg+xml", ".bmp": "image/bmp",
    ".ico": "image/x-icon", ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime", ".ogv": "video/ogg", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".wav": "audio/wav", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".flac": "audio/flac",
    ".aac": "audio/aac", ".pdf": "application/pdf",
}
# Text files that links can open in mdview's read-only text view, with line numbers.
TEXT_TYPES = {".tex", ".bib", ".sty", ".cls", ".bst", ".txt", ".log", ".py", ".r", ".jl", ".m", ".js",
              ".ts", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".sh", ".zsh", ".bash", ".lua",
              ".css", ".xml", ".ini", ".cfg", ".c", ".h", ".cpp", ".hpp", ".rs", ".go", ".java", ".sql"}
TEXT_MAX = 4 * 1024 * 1024
ART_TYPES = {".webp": "image/webp", ".png": "image/png", ".json": "application/json"}
VENDOR_TYPES = {".js": "text/javascript; charset=utf-8", ".woff": "font/woff", ".woff2": "font/woff2"}

# Content-Security-Policy per kind of response. The viewer may run only its own scripts
# (viewer.js and the local MathJax) and talk only to this server; documents may show
# images and media from the web. Everything else gets nothing.
CSP_VIEWER = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' https: data:; media-src 'self' https:; font-src 'self'; "
              "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
CSP_INDEX = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
CSP_NONE = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
CSP_SVG = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"  # SVGs can carry scripts

def art_scores():
    """{figure id: score}. Older files kept {"up": n, "down": n}; that reads as up - down."""
    try:
        raw = json.loads(ART_SCORES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v["up"] - v["down"] if isinstance(v, dict) else v for k, v in raw.items()}


def art_vote(fig, vote):
    """Add 1 (vote > 0) or -1 to a figure's score. Returns the new score, or None if there is no such figure."""
    if not ART_ID_RE.fullmatch(fig) or not any((ART / "figures" / f"{fig}-{k}").is_file() for k in ("mask.png", "line.webp")):
        return None
    with art_lock:
        scores = art_scores()
        scores[fig] = scores.get(fig, 0) + (1 if vote > 0 else -1)
        tmp = ART_SCORES.with_suffix(".tmp")
        tmp.write_text(json.dumps(scores), encoding="utf-8")
        os.replace(tmp, ART_SCORES)  # atomic: a crash never leaves a half-written file
        return scores[fig]


# --------------------------------------------------------------------------
# CriticMarkup parsing
# --------------------------------------------------------------------------

FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
AUTHOR_RE = re.compile(r"\s*@([\w.-]+):\s*")
BLANK_LINE_RE = re.compile(r"\n[ \t>]*\n")
BLANK_LINES_RE = re.compile(r"(?:[ \t]*\n)*")
SUGGESTION_CLOSE = {"{++": "++}", "{--": "--}", "{~~": "~~}"}


def fenced_ranges(src):
    """Return [start, end) offsets of fenced code blocks (markup inside is literal)."""
    ranges, pos, opened = [], 0, None
    for line in src.splitlines(keepends=True):
        m = FENCE_RE.match(line)
        if opened is None:
            if m and not (m.group(1)[0] == "`" and "`" in line[m.end():]):
                opened = (pos, m.group(1)[0], len(m.group(1)))
        elif m and m.group(1)[0] == opened[1] and len(m.group(1)) >= opened[2] \
                and not line[m.end():].strip():
            ranges.append((opened[0], pos + len(line)))
            opened = None
        pos += len(line)
    if opened:
        ranges.append((opened[0], len(src)))
    return ranges


class Doc:
    """A parsed Markdown file.

    clean        the text with all markup removed (what pandoc renders). A suggested change
                 keeps both its old and its new text, so the viewer can show both
    cmap         cmap[k] = offset in the original of clean character k
    threads      comment threads, in document order
    suggestions  suggested changes, in document order
    """

    def __init__(self, src):
        self.src = src
        self.fences = fenced_ranges(src)
        self._parse()

    def _parse(self):
        src, n = self.src, len(self.src)
        clean, cmap, threads, suggestions = [], [], [], []
        fences, fi, i = self.fences, 0, 0

        def emit(a, b):
            for k in range(a, b):
                clean.append(src[k])
                cmap.append(k)

        def suggestion(k, limit, thread=None):
            """Parse a suggested change at k into clean; return its end, or None if there isn't one."""
            close = SUGGESTION_CLOSE.get(src[k:k + 3])
            j = src.find(close, k + 3, limit) if close else -1
            if j < 0:
                return None
            a, b = k + 3, j
            if src[k + 1] == "~":
                m = src.find("~>", a, b)
                if m < 0:
                    return None
                old, new = (a, m), (m + 2, b)
            else:
                old, new = ((a, b), None) if src[k + 1] == "-" else (None, (a, b))
            g = {"start": k, "end": j + 3, "old": old, "new": new, "thread": thread}
            g["ds"] = len(clean)
            if old:
                emit(*old)
            g["de"] = len(clean)
            if old and new:  # keep the two apart: a space, or a paragraph break for block edits
                o, nw = src[old[0]:old[1]], src[new[0]:new[1]]
                sep = ("\n\n" if BLANK_LINE_RE.search(o) or BLANK_LINE_RE.search(nw)
                       else "" if o[-1:].isspace() or nw[:1].isspace() else " ")
                for ch in sep:
                    clean.append(ch)
                    cmap.append(new[0] - 2)
            g["is"] = len(clean)
            if new:
                emit(*new)
            g["ie"] = len(clean)
            suggestions.append(g)
            return j + 3

        def emit_highlight(a, b):
            """Highlighted text, which may contain suggested changes."""
            k = a
            while k < b:
                end = suggestion(k, b, thread=len(threads))
                if end is None:
                    emit(k, k + 1)
                    k += 1
                else:
                    k = end

        def comments_at(k, limit):
            msgs = []
            while src.startswith("{>>", k):
                j = src.find("<<}", k + 3, limit)
                if j < 0:
                    break
                msgs.append((k, j + 3))
                k = j + 3
            return msgs, k

        while i < n:
            while fi < len(fences) and fences[fi][1] <= i:
                fi += 1
            if fi < len(fences) and fences[fi][0] <= i:
                emit(i, fences[fi][1])
                i = fences[fi][1]
                continue
            limit = fences[fi][0] if fi < len(fences) else n

            if src[i] == "`":  # inline code: copy verbatim
                run = len(src[i:limit]) - len(src[i:limit].lstrip("`"))
                m = re.compile(r"(?<!`)" + "`" * run + r"(?!`)").search(src, i + run, limit)
                end = m.end() if m else i + run
                emit(i, end)
                i = end
                continue

            end = suggestion(i, limit)
            if end is not None:
                i = end
                continue

            if src.startswith("{==", i):
                j = src.find("==}", i + 3, limit)
                if j >= 0:
                    cs = len(clean)
                    emit_highlight(i + 3, j)
                    msgs, k = comments_at(j + 3, limit)
                    threads.append(self._thread(i, (i + 3, j), k, cs, len(clean), msgs))
                    i = k
                    continue

            if src.startswith("{>>", i):
                msgs, k = comments_at(i, limit)
                if msgs:
                    t = self._thread(i, None, k, len(clean), len(clean), msgs)
                    ls = src.rfind("\n", 0, i) + 1
                    le = src.find("\n", k)
                    le = n if le < 0 else le + 1
                    if not src[ls:i].strip() and not src[k:le].strip():
                        # Alone on its line: a general comment. Drop the whole line from
                        # the clean text so it can't split the paragraph around it.
                        while cmap and cmap[-1] >= ls:
                            clean.pop()
                            cmap.pop()
                        t.update(general=True, line=(ls, le), cs=len(clean), ce=len(clean))
                        k = le
                    threads.append(t)
                    i = k
                    continue

            emit(i, i + 1)
            i += 1

        cmap.append(n)
        self.clean = "".join(clean)
        self.cmap = cmap
        self.threads = threads
        self.suggestions = suggestions
        # UTF-16 offsets, because the browser counts in UTF-16 code units
        self.u16 = [0]
        for ch in self.clean:
            self.u16.append(self.u16[-1] + (2 if ord(ch) > 0xFFFF else 1))

    def _thread(self, start, hl, end, cs, ce, msg_spans):
        msgs = []
        for a, b in msg_spans:
            body = self.src[a + 3:b - 3]
            m = AUTHOR_RE.match(body)
            author, text = (m.group(1), body[m.end():]) if m else (None, body)
            msgs.append({"span": (a, b), "author": author, "text": text.strip()})
        status = "answered" if msgs and (msgs[-1]["author"] or "").lower() == "claude" else "open"
        return {"start": start, "hl": hl, "end": end, "cs": cs, "ce": ce,
                "msgs": msgs, "status": status, "general": False}

    # ----- offset helpers -----
    def to16(self, k):
        return self.u16[k]

    def from16(self, x):
        k = bisect.bisect_left(self.u16, x)
        if k >= len(self.u16) or self.u16[k] != x:
            raise OpError("Offset falls inside a character.")
        return k

    def orig_start(self, k):
        """Original offset where clean char k begins (after any markup before it)."""
        return self.cmap[k]

    def orig_end(self, k):
        """Original offset just after clean char k-1 (before any markup after it)."""
        return self.cmap[k - 1] + 1 if k > 0 else 0

    def line_of(self, offset):
        return self.src.count("\n", 0, offset) + 1

    def in_fence(self, a, b):
        return any(fa < b and a < fb for fa, fb in self.fences)

    def text_of(self, part):
        return self.src[part[0]:part[1]] if part else ""

    # ----- JSON for the viewer / --list -----
    def threads_json(self):
        out = []
        for idx, t in enumerate(self.threads):
            quote_ = accept_all(self.src[t["hl"][0]:t["hl"][1]]) if t["hl"] else ""
            out.append({
                "id": idx,
                "s": self.to16(t["cs"]),
                "e": self.to16(t["ce"]),
                "line": self.line_of(t["start"]),
                "quote": quote_,
                "status": t["status"],
                "general": t["general"],
                "msgs": [{"author": m["author"], "text": m["text"]} for m in t["msgs"]],
            })
        return out

    def suggestions_json(self):
        return [{
            "id": idx,
            "kind": "replace" if g["old"] and g["new"] else "delete" if g["old"] else "insert",
            "line": self.line_of(g["start"]),
            "old": self.text_of(g["old"]),
            "new": self.text_of(g["new"]),
            "ds": self.to16(g["ds"]), "de": self.to16(g["de"]),
            "is": self.to16(g["is"]), "ie": self.to16(g["ie"]),
            "thread": g["thread"],
        } for idx, g in enumerate(self.suggestions)]


# --------------------------------------------------------------------------
# Edits to the Markdown source
# --------------------------------------------------------------------------

class OpError(Exception):
    pass


def clean_comment(text, author=None):
    text = re.sub(r"\s*\n\s*", " ", str(text or "")).strip()
    if not text:
        raise OpError("The comment is empty.")
    text = text.replace("<<}", "<< }")
    return f"@{author}: {text}" if author else text


def settle(src, g, accept):
    """src with suggested change g accepted (its new text kept) or rejected (its old text kept)."""
    part = g["new"] if accept else g["old"]
    text = src[part[0]:part[1]] if part else ""
    before, after = src[:g["start"]], src[g["end"]:]
    if not text:  # tidy the gap: a doubled space, a space before punctuation, extra blank lines
        if before.endswith(" ") and after[:1] == " ":
            after = after[1:]
        elif before.endswith(" ") and after[:1] and after[0] in ".,;:!?)":
            before = before.rstrip(" ")
        n1 = len(before) - len(before.rstrip("\n"))
        n2 = len(after) - len(after.lstrip("\n"))
        if n1 and n2:
            before, after = before.rstrip("\n"), "\n" * max(n1, n2) + after.lstrip("\n")
            if not before.strip():
                after = after.lstrip("\n")
    return before + text + after


SUGGESTION_RE = re.compile(r"\{~~(.*?)~>(.*?)~~\}|\{\+\+(.*?)\+\+\}|\{--(.*?)--\}", re.S)


def accept_all(text):
    """text as it reads with every suggested change accepted (for quotes in the sidebar)."""
    return SUGGESTION_RE.sub(lambda m: m.group(2) if m.group(1) is not None else m.group(3) or "", text)


def block_span(doc, op):
    """The lines of a block in the original: op s, e = its span in the clean text, ending at the
    start of the next line. Returns (a, b), from a line start to just after the block's last line."""
    src = doc.src
    s, e = doc.from16(int(op["s"])), doc.from16(int(op["e"]))
    if not 0 <= s < e <= len(doc.clean):
        raise OpError("That block no longer exists.")
    a, b = src.rfind("\n", 0, doc.orig_start(s)) + 1, doc.orig_end(e)
    if b < len(src) and src[b - 1] != "\n":  # markup after the last word, on the same line
        b = src.find("\n", b) + 1 or len(src)
    if any(x["start"] < a < x["end"] or x["start"] < b < x["end"] for x in doc.threads + doc.suggestions):
        raise OpError("A comment or suggested change crosses the edge of this block.")
    return a, b


def resolve_thread(src, t):
    """Remove thread t from src, keeping the text it highlighted."""
    if t["general"]:
        a, b = t["line"]
        out = src[:a] + src[b:]
        if not src[b:].strip():  # last line(s) of the file: drop the blank line left behind
            out = out.rstrip() + "\n" if out.strip() else ""
        return out
    keep = src[t["hl"][0]:t["hl"][1]] if t["hl"] else ""
    return src[:t["start"]] + keep + src[t["end"]:]


def apply_op(doc, op):
    src, kind = doc.src, op.get("op")

    if kind in ("accept", "reject"):
        try:
            g = doc.suggestions[int(op["id"])]
        except (KeyError, ValueError, IndexError):
            raise OpError("That suggested change no longer exists.")
        return settle(src, g, kind == "accept")

    if kind in ("accept-all", "reject-all"):
        if not doc.suggestions:
            raise OpError("There are no suggested changes.")
        for g in reversed(doc.suggestions):
            src = settle(src, g, kind == "accept-all")
        return src

    if kind == "delete-block":
        a, b = block_span(doc, op)
        b = BLANK_LINES_RE.match(src, b).end()
        if not src[b:].strip():  # the block was last: drop the blank lines before it instead
            a = len(src[:a].rstrip("\n")) + 1 if src[:a].strip() else 0
            b = len(src)
        return src[:a] + src[b:]

    if kind == "insert-block":  # new text after the block (s, e), before it, or at the end of the file
        where, text = op.get("where"), str(op.get("text", "")).replace("\r\n", "\n").strip("\n")
        if not text.strip():
            raise OpError("Write something first.")
        if where == "end":
            pos = len(src)
        elif where in ("before", "after"):
            a, b = block_span(doc, op)
            pos = a if where == "before" else b
        else:
            raise OpError("Unknown position.")
        head, tail = src[:pos], src[pos:]
        if head and not head.endswith("\n"):
            head += "\n"
        if head.strip() and not head.endswith("\n\n"):
            head += "\n"  # a blank line before the new block
        text += "\n"
        if tail.strip() and not tail.startswith("\n"):
            text += "\n"  # and after it
        return head + text + tail

    if kind == "replace-block":
        a, b = block_span(doc, op)
        text = str(op.get("text", "")).replace("\r\n", "\n").strip("\n")
        if not text.strip():
            raise OpError("The block is empty. Use the × to delete it.")
        return src[:a] + text + "\n" + src[b:]

    def thread():
        try:
            return doc.threads[int(op["id"])]
        except (KeyError, ValueError, IndexError):
            raise OpError("That comment no longer exists.")

    if kind == "add":
        s, e = doc.from16(int(op["s"])), doc.from16(int(op["e"]))
        if not 0 <= s < e <= len(doc.clean) or not doc.clean[s:e].strip():
            raise OpError("Select some text first.")
        for t in doc.threads:
            if t["general"]:
                continue
            if (t["cs"] < e and s < t["ce"]) or (t["cs"] == t["ce"] and s < t["cs"] < e):
                raise OpError("That text overlaps an existing comment. Reply to it instead.")
        a, b = doc.orig_start(s), doc.orig_end(e)
        if any(g["start"] < b and a < g["end"] for g in doc.suggestions):
            raise OpError("That text has a suggested change. Accept or reject it first.")
        if doc.in_fence(a, b):
            raise OpError("Comments inside code blocks aren't supported.")
        if BLANK_LINE_RE.search(src, a, b):
            raise OpError("A comment can't span more than one paragraph.")
        body = clean_comment(op.get("text"))
        return src[:a] + "{==" + src[a:b] + "==}{>>" + body + "<<}" + src[b:]

    if kind == "general":
        body = clean_comment(op.get("text"))
        out = src if not src or src.endswith("\n") else src + "\n"
        last = doc.threads[-1] if doc.threads else None
        if out.strip() and not (last and last["general"] and not src[last["line"][1]:].strip()):
            out += "\n"  # blank line before the first general comment at the end
        return out + "{>>" + body + "<<}\n"

    if kind == "reply":
        t = thread()
        return src[:t["end"]] + "{>>" + clean_comment(op.get("text"), op.get("author")) + "<<}" + src[t["end"]:]

    if kind in ("edit", "delete"):
        t = thread()
        try:
            m = t["msgs"][int(op["msg"])]
        except (KeyError, ValueError, IndexError):
            raise OpError("That comment no longer exists.")
        a, b = m["span"]
        if kind == "edit":
            return src[:a] + "{>>" + clean_comment(op.get("text"), m["author"]) + "<<}" + src[b:]
        if len(t["msgs"]) > 1:
            return src[:a] + src[b:]
        kind = "resolve"  # deleting the last message resolves the thread

    if kind == "resolve":
        return resolve_thread(src, thread())

    if kind == "resolve-all":
        if not doc.threads:
            raise OpError("There are no comments.")
        for t in sorted(doc.threads, key=lambda t: t["start"], reverse=True):
            src = resolve_thread(src, t)
        return src

    raise OpError(f"Unknown operation: {kind!r}")


# --------------------------------------------------------------------------
# Files, rendering
# --------------------------------------------------------------------------

TITLE_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S)
_render_cache = {}


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


def render(clean):
    if clean in _render_cache:
        return _render_cache[clean]
    # raw_attribute (`<b>`{=html}) is turned off; safe.lua neutralises raw HTML and the rest.
    base = ["pandoc", "-f", "commonmark_x-raw_attribute+sourcepos", "-t", "html", "--mathjax",
            "--wrap=none", "--lua-filter", str(FILTER)]
    for extra in (["--syntax-highlighting=none"], ["--no-highlight"]):
        try:
            p = subprocess.run(base + extra, input=clean.encode("utf-8"), capture_output=True)
        except FileNotFoundError:
            raise RuntimeError("pandoc is not installed (https://pandoc.org/installing.html).")
        if p.returncode == 0:
            break
    else:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip())
    out = p.stdout.decode("utf-8")
    if len(_render_cache) > 32:
        _render_cache.clear()
    _render_cache[clean] = out
    return out


def title_of(src):
    m = TITLE_RE.match(src)
    if not m:
        return None
    t = re.search(r"^title:[ \t]*(.+?)[ \t]*$", m.group(1), re.M)
    return t.group(1).strip("'\"") if t else None


def doc_payload(path, rel):
    src, version = read_text(path)
    doc = Doc(src)
    return {
        "name": rel,
        "dir": rel.rpartition("/")[0],
        "version": version,
        "title": title_of(src),
        "clean": doc.clean,
        "html": render(doc.clean),
        "threads": doc.threads_json(),
        "suggestions": doc.suggestions_json(),
    }


# --------------------------------------------------------------------------
# Ask Claude: one background `claude -p "/answer-comments FILE"` run per document
# --------------------------------------------------------------------------
# The run holds an flock on the document's lock file for as long as it lives, so at most one
# runs per document, even across server restarts, and "running" never outlives the process.

def claude_exe():
    exe = os.environ.get("MDVIEW_CLAUDE") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    if not os.access(exe, os.X_OK):
        raise RuntimeError("Can't find the claude command. Set MDVIEW_CLAUDE to its path and restart mdview.")
    return exe


# The run itself is in viewkit (claudekit.Oracle): a lock per document, one session per document, and a report.
ORACLE = claudekit.Oracle(CLAUDE_DIR, claude_exe, lambda p: f'/answer-comments "{p}"', CLAUDE_TOOLS,
                          permission_mode="acceptEdits")


# --------------------------------------------------------------------------
# Chat: a conversation with Claude about one document (the panel at the bottom right)
# --------------------------------------------------------------------------
# One `claude -p` process per message, all in one session per document. The page polls for the answer as it grows.

CHAT_DIR = STATE_DIR / "chat"
CHAT_TOOLS_ASK = ["Read", "Grep", "Glob", "Bash(mdview:*)", "Bash(grep:*)", "WebSearch", "WebFetch"]
CHAT_SYSTEM = """You are in the chat panel of mdview, a viewer for Markdown files. The document is the file <FILE>.
The user sees your reply in a narrow panel, so keep it short and plain. Maths in $...$ and $$...$$ is typeset there.
- Comments in the file use CriticMarkup: {==highlighted text==}{>>a comment<<}{>>@claude: a reply<<}. Keep every comment where it is.
- Suggested changes use {~~old~>new~~}, {++new++} and {--old--}. Write a change in this form only if the user asks you to suggest it.
  If the user asks you to change the text, edit the file directly with the Edit tool.
- After you edit text with formulas, run `mdview <FILE> --check-math` and fix what it reports.
  `mdview <FILE> --list` prints the comment threads.
- The user may tell you which passage they selected in the document. Use it to see what "this" means.
- Every message starts with a line in brackets. It tells you the mode of that message. Follow the last one."""
# The mode goes into every message, not into the system prompt: a resumed session keeps the system prompt of its first turn.
CHAT_MODE_EDIT = "(Mode: you may edit the file when the user asks for a change. Answer questions without changing anything.)"
CHAT_MODE_ASK = "(Mode: Ask only. Do not edit the file. Read it and answer.)"
CHAT_QUOTE_MAX = 1500


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

    def send(self, text, quote, ask_only):
        """Start one turn. Returns None, or a message that says why it cannot start."""
        with self.lock:
            if self.cur is not None:
                return "Claude is still answering."
            if ORACLE.busy(self.path):
                return "The Oracle is running on this file. Wait for it, or stop it."
            try:
                exe = claude_exe()
            except RuntimeError as e:
                return str(e)
            now = int(time.time())
            user = {"role": "user", "text": text, "ts": now}
            if quote:
                user["quote"] = quote
            self.data["messages"].append(user)
            self.cur = {"role": "assistant", "parts": [], "ts": now, "streaming": True}
            self.stop_flag = False
            self.rev += 1
            self.save()
            threading.Thread(target=self.run, args=(exe, text, quote, ask_only), daemon=True).start()
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

    def run(self, exe, text, quote, ask_only):
        p = self.path
        before = read_text(p)[0]
        ctx = ""
        quote = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", quote or "").strip()[:CHAT_QUOTE_MAX]
        if quote:
            ctx = "(The user has selected this passage in the document: " + json.dumps(quote, ensure_ascii=False) + ")\n\n"
        mode = (CHAT_MODE_ASK if ask_only else CHAT_MODE_EDIT) + "\n"
        with self.lock:
            sid, started = self.data["session"], self.data.get("started")
        if ask_only:
            allowed, disallowed, perm = CHAT_TOOLS_ASK, ["Edit", "Write", "NotebookEdit"], None
        else:
            allowed, disallowed, perm = CLAUDE_TOOLS, None, "acceptEdits"
        cmd = claudekit.build_command(exe, mode + ctx + text, CHAT_SYSTEM.replace("<FILE>", str(p)), sid, started,
                                      allowed, disallowed, perm)
        err, stderr, code = claudekit.run_stream(cmd, p.parent, claudekit.clean_env(), self.event, self.set_proc)
        try:
            after, version = read_text(p)
        except OSError:
            after, version = before, None
        changed = after != before
        if changed and Handler.app is not None:  # one Undo in the header takes back everything that this turn changed
            with Handler.app.lock:
                stack = Handler.app.undo.setdefault(str(p), [])
                stack.append((version, before))
                del stack[:-20]
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


REF_RE = re.compile(r'\s(?:src|href)="([^"]*)"')
MDLINK_RE = re.compile(r"\]\((<[^<>\n]*>|[^()\s]+)\)")  # [text](url) or [text](<url with spaces>)
NOT_GRANTED = "mdview can only open files and folders you have passed to it. To open this one, run: mdview {}"


class App:
    def __init__(self, root, grants_file=None):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        self.undo = {}  # path -> list of (version_after_write, previous_text)
        self.grants_file = grants_file
        self.granted = set()  # files and folders passed to the mdview command
        self.media = set()    # images and media that granted documents refer to
        self.linked = set()   # Markdown and text files that granted documents link to
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
        """True if p was passed to mdview, or is inside a folder that was."""
        return any(g == p or g in p.parents for g in self.granted)

    def can_open(self, p):
        """Passed to mdview, inside a folder that was, or linked from a document that can be opened."""
        return self.allowed(p) or p in self.linked

    def add_links(self, page_html, rel, more=()):
        """Let mdview open what a granted document refers to: images and media (/files/), and the
        Markdown and text files it links to. `more` adds raw link targets, e.g. from comments."""
        base = posixpath.dirname(rel)
        for u in [html.unescape(u) for u in REF_RE.findall(page_html)] + list(more):
            u = u.strip("<>").split("#")[0].split("?")[0]
            if not u or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:|/", u):
                continue
            p = self.resolve(quote(posixpath.normpath(posixpath.join(base, unquote(u)))))
            ext = p.suffix.lower() if p else ""
            if ext in MEDIA_TYPES:
                self.media.add(p)
            elif ext in MD_SUFFIXES or ext in TEXT_TYPES:
                self.linked.add(p)

    def resolve(self, rel, want_dir=False):
        """The file (or folder) at `rel` under the root, or None. Hidden paths are refused,
        whether named directly or reached through a link."""
        rel = unquote(rel or "")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            return None
        if is_hidden(Path(rel)) or is_hidden(p.relative_to(self.root)):
            return None
        return p if (p.is_dir() if want_dir else p.is_file()) else None

    def rel(self, p):
        return p.relative_to(self.root).as_posix() if p != self.root else ""


class Handler(BaseHTTPRequestHandler):
    server_version = "mdview"
    app: App = None

    def log_message(self, *args):
        pass

    # ----- helpers -----
    def host_ok(self):
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host.lower() in serverkit.local_hosts("mdview")

    def cookie_name(self):
        return f"mdview{self.server.server_address[1]}"  # cookies ignore ports, so name it per port

    def authorized(self):
        """The request carries the access key, as the cookie set when the link was opened."""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.cookie_name() and TOKEN and hmac.compare_digest(value, TOKEN):
                return True
        return False

    def deny(self, api):
        msg = "This page's access key is missing or out of date. Reopen the file with the mdview command."
        if api:
            return self.send_json(403, {"error": msg})
        return self.send(403, f"<!doctype html><meta charset=utf-8><title>mdview</title>"
                              f"<p style='font:15px system-ui,sans-serif;margin:32px'>{msg}</p>", csp=CSP_INDEX)

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None, csp=CSP_NONE):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        # Keep other websites from framing the viewer or embedding anything served here.
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

    def md_path(self, q):
        """(path, rel, None) for a Markdown file that may be opened, else (None, rel, (code, error))."""
        rel = (q.get("f") or [""])[0]
        p = self.app.resolve(rel)
        if not p or p.suffix.lower() not in MD_SUFFIXES:
            return None, rel, (404, f"File not found: {rel}")
        if not self.app.can_open(p):
            return None, rel, (403, NOT_GRANTED.format(p))
        return p, self.app.rel(p), None

    def payload(self, p, rel):
        d = doc_payload(p, rel)
        d["label"] = serverkit.doc_label(p, self.app.opened)  # a short name for the tab and the header
        self.app.opened.add(p)
        d["dirOk"] = self.app.allowed(p.parent)  # whether the 📁 folder link can open
        d["textTypes"] = sorted(TEXT_TYPES)
        msgs = " ".join(m["text"] for t in d["threads"] for m in t["msgs"])
        msgs += " ".join(g["old"] + " " + g["new"] for g in d["suggestions"])
        self.app.add_links(d["html"], rel, MDLINK_RE.findall(msgs))
        return d

    # ----- routes -----
    def do_GET(self):
        if not self.host_ok():
            return self.send(403, "Forbidden")
        url = urlparse(self.path)
        q = parse_qs(url.query)
        app = self.app

        if url.path == "/api/ping":  # open to all: the command line uses it to find the server
            return self.send_json(200, {"app": "mdview", "pid": os.getpid(), "root": str(app.root),
                                        "started": STARTED, "code": str(Path(__file__).resolve())})

        # A link from the mdview command carries the key as ?t=. Keep it as a cookie and
        # reload without it, so it doesn't stay in the address bar.
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

        if url.path.startswith("/vendor/"):  # the local copy of MathJax
            p = (VENDOR / unquote(url.path[len("/vendor/"):])).resolve()
            if VENDOR.resolve() not in p.parents or not p.is_file() or p.suffix not in VENDOR_TYPES:
                return self.send(404, "Not found")
            return self.send(200, p.read_bytes(), VENDOR_TYPES[p.suffix])

        if url.path == "/api/art/scores":
            return self.send_json(200, art_scores())

        if url.path == "/art/figures/manifest.json" and not (ART / "figures" / "manifest.json").is_file():
            return self.send_json(200, [])  # no watermark art in this checkout (art/ is not in git): draw nothing

        if url.path.startswith("/art/"):  # a watermark image
            p = (ART / unquote(url.path[len("/art/"):])).resolve()
            if ART.resolve() not in p.parents or not p.is_file() or p.suffix not in ART_TYPES:
                return self.send(404, "Not found")
            return self.send(200, p.read_bytes(), ART_TYPES[p.suffix])

        if url.path == "/api/version":
            p, rel, err = self.md_path(q)
            if err:
                return self.send_json(err[0], {"error": err[1]})
            return self.send_json(200, {"version": read_text(p)[1]})

        if url.path == "/api/chat":
            p, rel, err = self.md_path(q)
            if err:
                return self.send_json(err[0], {"error": err[1]})
            since = (q.get("since") or [""])[0]
            return self.send_json(200, chat_of(p).snapshot(int(since) if since.isdigit() else None))

        if url.path == "/api/claude":
            p, rel, err = self.md_path(q)
            if err:
                return self.send_json(err[0], {"error": err[1]})
            return self.send_json(200, ORACLE.status(p))

        if url.path in ("/api/improve/dash", "/api/improve/preview"):  # the self-improving loop (viewkit/improvekit.py)
            p, rel, err = self.md_path(q)
            if err:
                return self.send_json(err[0], {"error": err[1]})
            if url.path == "/api/improve/dash":
                return self.send_json(*improvekit.dash_api("mdview", HERE, "GET"))
            return self.send_json(*improvekit.preview_api("GET"))

        if url.path == "/api/doc":
            p, rel, err = self.md_path(q)
            if err:
                return self.send_json(err[0], {"error": err[1]})
            try:
                return self.send_json(200, self.payload(p, rel))
            except Exception as e:  # pandoc errors, bad encoding
                return self.send_json(500, {"error": str(e)})

        if url.path == "/text":  # a linked text file (LaTeX, code, ...), read-only with line numbers
            rel = (q.get("f") or [""])[0]
            p = app.resolve(rel)
            if not p or p.suffix.lower() not in TEXT_TYPES:
                return self.send(404, "Not found")
            if not app.can_open(p):
                return self.send(403, NOT_GRANTED.format(p), "text/plain; charset=utf-8")
            m = re.fullmatch(r"(\d+)(?:-(\d+))?", (q.get("lines") or [""])[0])
            lines = (int(m.group(1)), int(m.group(2) or m.group(1))) if m else None
            return self.send(200, text_page(p, lines), csp=CSP_INDEX)

        if url.path.startswith("/files/"):  # images and media that documents embed or link to
            p = app.resolve(url.path[len("/files/"):])
            if not p or not (app.allowed(p) or p in app.media):
                return self.send(404, "Not found")
            ctype = MEDIA_TYPES.get(p.suffix.lower())
            if not ctype:
                return self.send(403, "mdview only serves images, audio, video and PDFs.")
            return self.send(200, p.read_bytes(), ctype, csp=CSP_SVG if p.suffix.lower() == ".svg" else None)

        self.send(404, "Not found")

    def do_POST(self):
        # The custom header forces a CORS preflight, so other websites can't post here.
        if not self.host_ok() or self.headers.get("X-MDView") != "1":
            return self.send_json(403, {"error": "Forbidden"})
        if not self.authorized():
            return self.deny(True)
        path = urlparse(self.path).path
        if path not in ("/api/op", "/api/grant", "/api/claude", "/api/chat", "/api/art/vote", "/api/improve/dash",
                        "/api/improve/preview"):
            return self.send_json(404, {"error": "Not found"})
        try:
            op = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except (ValueError, TypeError):
            return self.send_json(400, {"error": "Bad request"})

        if path == "/api/art/vote":  # a click (+1) or double click (-1) on a watermark figure
            score = art_vote(str(op.get("id", "")), 1 if op.get("vote") == 1 else -1)
            if score is None:
                return self.send_json(404, {"error": "No such figure"})
            return self.send_json(200, {"score": score})

        if path == "/api/grant":  # the mdview command: allow a file or folder you passed to it
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

        p, rel, err = self.md_path({"f": [op.get("f", "")]})
        if err:
            return self.send_json(err[0], {"error": err[1]})

        if path == "/api/improve/dash":  # the dashboard: add, edit or delete a waiting note, or send them all
            if improvekit.preview_of():
                return self.send_json(409, {"error": "This is a preview. Write notes in the live mdview tab."})
            port = self.server.server_address[1]
            back = f"http://{serverkit.app_host('mdview')}:{port}/view?f={quote(rel)}"
            return self.send_json(*improvekit.dash_api("mdview", HERE, "POST", op, p, back))
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
                err = chat.send(text, str(op.get("quote") or ""), bool(op.get("ask_only")))
            return self.send_json(409 if err else 200, {"error": err} if err else {"ok": True})

        if path == "/api/claude":  # Ask Claude: start or stop the document's run
            if op.get("action") == "new":  # forget the ongoing session
                err = ORACLE.forget(p)
                if err:
                    return self.send_json(409, {"error": err})
                return self.send_json(200, ORACLE.status(p))
            if op.get("action") == "stop":
                ORACLE.stop(p)
                return self.send_json(200, ORACLE.status(p))
            if chat_of(p).busy():
                return self.send_json(409, {"error": "Claude is answering in the chat. Wait for it, or stop it."})
            try:
                started = ORACLE.start(p)
            except (RuntimeError, OSError) as e:
                return self.send_json(500, {"error": str(e)})
            return self.send_json(200, {**ORACLE.status(p), "already": not started})

        with self.app.lock:
            src, version = read_text(p)
            if op.get("version") != version:
                return self.send_json(409, {"error": "The file changed on disk. It has been reloaded; please try again."})
            stack = self.app.undo.setdefault(str(p), [])
            if op.get("op") == "block-source":  # the Markdown of a block, to edit it
                try:
                    a, b = block_span(Doc(src), op)
                except OpError as e:
                    return self.send_json(400, {"error": str(e)})
                return self.send_json(200, {"text": src[a:b].rstrip("\n")})
            try:
                if op.get("op") == "undo":
                    if not stack or stack[-1][0] != version:
                        raise OpError("Nothing to undo.")
                    new = stack.pop()[1]
                else:
                    new = apply_op(Doc(src), op)
            except OpError as e:
                return self.send_json(400, {"error": str(e)})
            write_text(p, new)
            if op.get("op") != "undo":
                stack.append((hashlib.sha1(new.encode("utf-8")).hexdigest(), src))
                del stack[:-20]
        self.send_json(200, self.payload(p, rel))

    def index_page(self, d):
        """A folder browser: subfolders, then Markdown files with their open-comment counts."""
        rel = self.app.rel(d)
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
            if e.name.startswith(".") or not (e.is_file() and e.suffix.lower() in MD_SUFFIXES):
                continue
            try:
                threads = Doc(read_text(e)[0]).threads
            except (OSError, UnicodeDecodeError):
                continue
            n_open = sum(t["status"] == "open" for t in threads)
            badge = f'<span class="b">{n_open} open</span>' if n_open else ""
            rows.append(f'<li><a href="/view?f={quote(self.app.rel(e))}">📄 {html.escape(e.name)}</a>{badge}</li>')
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(d.name or str(d))}/ · mdview</title>
<link rel="icon" type="image/png" href="/kit/icons/md.png">
<style>
:root{{--bg:#fafaf7;--fg:#222;--muted:#666;--accent:#2f6fde;--hl:#ffe58a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1c1e;--fg:#e8e8e8;--muted:#9a9a9a;--accent:#6ea0ff;--hl:#6b5a1e}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,sans-serif;padding:32px 16px}}
main{{max-width:680px;margin:0 auto}} h1{{font-size:18px;overflow-wrap:anywhere}} ul{{padding:0;list-style:none}}
li{{padding:5px 0;border-bottom:1px solid rgba(128,128,128,.2);display:flex;gap:10px;align-items:center}}
a{{color:var(--accent);text-decoration:none}} .b{{background:var(--hl);border-radius:10px;padding:0 8px;font-size:12px}}
</style></head><body><main><h1>{html.escape(str(d))}</h1><ul>{"".join(rows)}</ul></main></body></html>"""


def text_page(p, lines=None):
    """A read-only view of a text file with line numbers; `lines` (a, b) are highlighted."""
    data = p.read_bytes()
    cut = len(data) > TEXT_MAX
    text = data[:TEXT_MAX].decode("utf-8", "replace")
    a, b = lines or (0, -1)
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        cls = ' class="hl"' if a <= n <= b else ""
        rows.append(f'<tr id="L{n}"{cls}><td class="n"><a href="#L{n}">{n}</a></td>'
                    f'<td class="c">{html.escape(line) or " "}</td></tr>')
    where = f" · lines {a}–{b}" if lines and a != b else f" · line {a}" if lines else ""
    note = '<p class="cut">The file is too large to show in full.</p>' if cut else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(p.name)} · mdview</title>
<link rel="icon" type="image/png" href="/kit/icons/md.png">
<style>
:root{{--bg:#fafaf7;--fg:#222;--muted:#888;--accent:#2f6fde;--hl:#fff3c4;--hl2:#ffe58a;--border:#ddd}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1c1e;--fg:#e8e8e8;--muted:#888;--accent:#6ea0ff;--hl:#3d3618;--hl2:#6b5a1e;--border:#444}}}}
body{{margin:0;background:var(--bg);color:var(--fg)}}
header{{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--border);padding:8px 16px;
  font:13px system-ui,sans-serif;color:var(--muted);overflow-wrap:anywhere}}
header b{{color:var(--fg)}}
table{{border-collapse:collapse;font:13px/1.5 ui-monospace,Menlo,monospace;margin:8px 0 40px}}
tr{{scroll-margin-top:30vh}} tr.hl{{background:var(--hl)}} tr:target{{background:var(--hl2)}}
td.n{{text-align:right;padding:0 12px 0 16px;user-select:none;vertical-align:top}}
td.n a{{color:var(--muted);text-decoration:none}} td.c{{white-space:pre-wrap;overflow-wrap:anywhere;padding-right:16px}}
.cut{{font:13px system-ui,sans-serif;color:var(--muted);padding:0 16px}}
</style></head><body><header><b>{html.escape(p.name)}</b>{where} · read-only · {html.escape(str(p))}</header>
<table>{"".join(rows)}</table>{note}</body></html>"""


def run_server(port):
    """Serve in the foreground. Restarts itself when this file changes."""
    global STARTED, TOKEN
    STARTED = time.strftime("%Y-%m-%d %H:%M:%S")
    TOKEN = read_token(port) or new_token(port)  # kept across code-change restarts, so open tabs keep working
    Handler.app = App(ROOT, grants_file(port))  # grants survive code-change restarts
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        sys.exit(f"mdview: can't listen on port {port}: {e}")
    DAEMON.serve(server, port, f"mdview: serving {ROOT} at http://127.0.0.1:{port}/ (pid {os.getpid()}, {STARTED})")


# --------------------------------------------------------------------------
# Command line: one shared background server
# --------------------------------------------------------------------------

def open_target(target, port, open_browser):
    p = Path(target).expanduser().resolve()
    if not p.exists():
        sys.exit(f"mdview: not found: {target}")
    if p.is_file() and p.suffix.lower() not in MD_SUFFIXES:
        sys.exit(f"mdview: not a Markdown file: {target}")
    root = Path(ensure_server(port)["root"])  # the running server's root, which may predate MDVIEW_ROOT
    if p != root and root not in p.parents:
        sys.exit(f"mdview: {p} is outside {root}, the folder the server can open.\n"
                 f"Restart it with a wider root, e.g.: MDVIEW_ROOT=/ mdview --restart")
    if is_hidden(p.relative_to(root)):
        sys.exit(f"mdview: {p} is in a hidden folder (or is a hidden file). mdview never opens those, to keep\n"
                 f"folders like ~/.ssh and ~/.claude out of reach. Copy the file somewhere else to read it.")
    rel = p.relative_to(root).as_posix() if p != root else ""
    token = read_token(port)
    if not token:
        sys.exit(f"mdview: the server's access key is missing. Restart it: mdview --restart")
    grant(port, token, p)
    # mdview.localhost: its own host name, so Safari shows the mdview tab icon (see serverkit.app_host)
    url = f"http://{serverkit.app_host('mdview')}:{port}/" + (f"view?f={quote(rel)}" if p.is_file() else f"?d={quote(rel)}")
    url += "&t=" + token
    print(url)
    if open_browser:
        webbrowser.open(url)


def grant(port, token, path):
    """Allow the server to open `path` (a file or a folder and everything in it)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/grant", data=json.dumps({"path": str(path)}).encode(),
        headers={"Content-Type": "application/json", "X-MDView": "1", "Cookie": f"mdview{port}={token}"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except ValueError:
            msg = e.reason
        sys.exit(f"mdview: the server won't open {path}: {msg}")


def list_threads(path, as_json):
    doc = Doc(read_text(path)[0])
    threads = doc.threads_json()
    if as_json:
        print(json.dumps(threads, indent=2, ensure_ascii=False))
        return
    if not threads:
        print("No comments.")
    sugg = doc.suggestions_json()
    if sugg:
        print(f"{len(sugg)} suggested change{'s' if len(sugg) > 1 else ''} waiting to be accepted or rejected:")
        for g in sugg:
            short = lambda t: (t[:60] + "…" if len(t) > 60 else t).replace("\n", " ")
            what = {"replace": f'"{short(g["old"])}" -> "{short(g["new"])}"',
                    "delete": f'delete "{short(g["old"])}"', "insert": f'insert "{short(g["new"])}"'}[g["kind"]]
            print(f'  ~{g["id"]}  line {g["line"]}  {what}')
    for t in threads:
        quote_ = t["quote"].replace("\n", " ")
        quote_ = (quote_[:70] + "…") if len(quote_) > 70 else quote_
        where = "general" if t["general"] else f'"{quote_}"' if quote_ else "point comment"
        print(f'#{t["id"]}  line {t["line"]}  [{t["status"]}]  {where}')
        for m in t["msgs"]:
            print(f'    {m["author"] or "you"}: {m["text"]}')


# The text column is 680px of 17px Georgia, and MathJax scales its font by about 1.09 to match,
# so the column holds about 36 em of maths. 28 em leaves room for narrower windows.
MATH_MAX_EM = 28
MATH_RE = re.compile(r"(?<!\\)\$\$(.+?)\$\$|(?<![\\$])\$(?![\s$])((?:\\.|[^$\\\n])+?)(?<![\s\\])\$(?!\d)", re.S)
MATH_PREAMBLE = (r"\documentclass{article}\usepackage{amsmath,amssymb,mathtools,mathrsfs,cancel,xcolor}"
                 r"\newsavebox\mb\begin{document}")


def check_math(path):
    """Typeset every formula with pdflatex and report the ones wider than MATH_MAX_EM, or that
    don't typeset (usually a macro MathJax doesn't know either)."""
    if not shutil.which("pdflatex"):
        sys.exit("mdview: --check-math needs pdflatex (TeX Live or MacTeX).")
    doc = Doc(read_text(path)[0])
    clean = doc.clean
    skip = fenced_ranges(clean) + [m.span() for m in re.finditer(r"(`+)[^`].*?\1", clean, re.S)]
    items = []  # (line, display, tex)
    for m in MATH_RE.finditer(clean):
        if any(a <= m.start() < b for a, b in skip):
            continue
        display = m.group(1) is not None
        tex = (m.group(1) if display else m.group(2)).strip()
        if display:  # measure numbered environments as their inner forms, without tags or labels
            tex = re.sub(r"\\(?:tag|label)\*?\{[^{}]*\}", "", tex)
            tex = re.sub(r"\\(begin|end)\{(align|gather|multline|equation)\*?\}",
                         lambda e: "" if e.group(2) == "equation" else
                         f"\\{e.group(1)}{{{'aligned' if e.group(2) == 'align' else 'gathered'}}}", tex)
        items.append((doc.line_of(doc.cmap[m.start()]), display, tex))
    if not items:
        print("No maths.")
        return
    lines = [MATH_PREAMBLE]
    for k, (_, display, tex) in enumerate(items):
        style = r"\displaystyle " if display else ""
        lines.append(f"\\sbox\\mb{{${style}{' '.join(tex.split())}$}}\\typeout{{MDVIEW {k} \\the\\wd\\mb}}")
    lines.append(r"\end{document}")
    with tempfile.TemporaryDirectory() as d:
        Path(d, "m.tex").write_text("\n".join(lines), encoding="utf-8")
        out = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-no-shell-escape", "m.tex"],
                             cwd=d, capture_output=True, text=True, timeout=120).stdout
    widths, errors, err = {}, {}, None
    for line in out.splitlines():
        if line.startswith("! "):
            err = line[2:].strip()
        elif err and (m := re.match(r"l\.(\d+)", line)):
            errors.setdefault(int(m.group(1)) - 2, err)  # tex line 2 holds item 0
            err = None
        elif m := re.match(r"MDVIEW (\d+) ([\d.]+)pt", line):
            widths[int(m.group(1))] = float(m.group(2)) / 10  # 10pt type: 1 em = 10pt
    short = lambda t: (t[:70] + "…") if len(t) > 70 else t
    for k, err in sorted(errors.items()):
        if 0 <= k < len(items):
            print(f"line {items[k][0]}  doesn't typeset ({err}): {short(' '.join(items[k][2].split()))}")
    wide = [(k, w) for k, w in widths.items() if w > MATH_MAX_EM and k not in errors]
    for k, w in wide:
        line, display, tex = items[k]
        print(f"line {line}  {'display' if display else 'inline'}  {w:.1f} em  {short(' '.join(tex.split()))}")
    if not errors and not wide:
        k, w = max(widths.items(), key=lambda kw: kw[1])
        print(f"All {len(items)} formulas fit (limit {MATH_MAX_EM} em; widest {w:.1f} em, line {items[k][0]}).")
    else:
        print(f"Limit {MATH_MAX_EM} em (the text column holds about 36). Split wide displays with "
              r"\begin{gathered}…\\…\end{gathered} or aligned; break long inline formulas into pieces.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "note":  # a note to mdview itself, not about a document (viewkit)
        return improvekit.note_main("mdview", HERE, sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "try":  # try a proposal of the loop in a sandbox, with copies
        return improvekit.try_main("mdview", HERE, sys.argv[2:])
    ap = argparse.ArgumentParser(
        prog="mdview", description="Read and comment on Markdown files in the browser.",
        epilog=f"One shared server runs in the background on port {PORT} and can open any file under {ROOT}. "
               f"Its log is {LOG}.")
    ap.add_argument("target", nargs="*", help="the .md files or folders to open, each in its own browser tab (default: current folder)")
    ap.add_argument("--no-open", action="store_true", help="print the URL without opening a browser")
    ap.add_argument("--list", action="store_true", help="print the comment threads in the file and exit")
    ap.add_argument("--json", action="store_true", help="with --list, print JSON")
    ap.add_argument("--check-math", action="store_true",
                    help=f"report formulas wider than the text column ({MATH_MAX_EM} em) or that don't typeset")
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
        print(f"mdview: running on http://127.0.0.1:{args.port}/ (pid {info['pid']}, since {info['started']}, "
              f"root {info['root']})" if info else "mdview: not running.")
        return
    if args.stop or args.restart:
        stop_server(args.port)
        if args.stop:
            return
        info = ensure_server(args.port)
        print(f"mdview: running (pid {info['pid']}).")
        return
    targets = args.target or ["."]
    if args.list or args.check_math:
        flag = "--list" if args.list else "--check-math"
        for i, t in enumerate(targets):
            if not Path(t).is_file():
                sys.exit(f"mdview: {flag} needs a .md file.")
            if len(targets) > 1:
                print(("\n" if i else "") + f"== {t} ==")
            list_threads(Path(t), args.json) if args.list else check_math(Path(t))
        return
    failed = False
    for t in targets:
        try:
            open_target(t, args.port, not args.no_open)
        except SystemExit as e:  # report it and carry on with the other files
            print(e.code, file=sys.stderr)
            failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
