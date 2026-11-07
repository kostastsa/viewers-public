"""Notes for self-improving apps (docs/self-improving-apps.md): a note is an issue with the label `note`.

A note is about the app, not about a document. It never holds the text or the path of a document, unless the user writes
it. The app gives its name, its folder (a git checkout) and the context. The kit adds the commit and makes the issue in
the tracker of the repository (`tracker`): a GitHub issue with `gh` when the gh user may push to the repository, else an
issue of the local tracker (localgh.py, JSON files on this computer). When `gh` fails (no network), the note waits in a
local queue, and the next note sends the queue first. `note_main` is the `APP note` subcommand.

The layout (`mono_root`): each app is a repository of its own next to viewkit, or all the apps and viewkit are folders
of one repository (the public repository viewers). The worktrees of a proposal follow the layout (`worktree_of`).

The automatic loop (version 2): a note from the note box starts a background run (improverun.py) that makes a proposal,
tests it, and opens a preview tab: the sandbox server of the proposal, with copies of the document. The preview tab has
Accept, Change and Reject (`preview_api`). The status of each run is a JSON file in STATUS_DIR. An app joins with the
file improve.json in its folder (`manifest`).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

LABEL = "note"
# The labels of the protocol: name, colour, description.
LABELS = [(LABEL, "1d76db", "A note to the app: a problem or a wish (self-improving apps)"),
          ("proposed", "0e8a16", "Claude made a proposal on the branch improve/note-N"),
          ("question", "d876e3", "Claude asks the user before a proposal"),
          ("rejected", "b60205", "The user rejected the proposal")]
SOURCES = ("user", "agent", "cli")
TEXT_MAX = 4000
# The tests set VIEWKIT_GH (a stand-in for gh, tests/fake_gh.py) and VIEWKIT_NOTES_QUEUE.
GH = os.environ.get("VIEWKIT_GH") or "gh"
LOCALGH = Path(__file__).resolve().parent / "localgh.py"
QUEUE = Path(os.environ.get("VIEWKIT_NOTES_QUEUE") or Path.home() / ".cache" / "viewkit" / "notes-queue.jsonl")
_lock = threading.Lock()
_labels_done = set()  # the repositories that have the labels, in this process


def _run(cmd, cwd=None, timeout=30, stdin=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=stdin)


def repo_of(app_dir):
    """OWNER/NAME of the GitHub repository of a checkout, from the URL of `origin`. None if it is not on GitHub.
    The tests set VIEWKIT_REPO."""
    if os.environ.get("VIEWKIT_REPO"):
        return os.environ["VIEWKIT_REPO"]
    try:
        url = _run(["git", "remote", "get-url", "origin"], cwd=app_dir, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    for head in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if url.startswith(head):
            name = url[len(head):]
            return name[:-4] if name.endswith(".git") else name
    return None


def toplevel(app_dir):
    """The top folder of the git checkout of app_dir, or None."""
    try:
        out = _run(["git", "rev-parse", "--show-toplevel"], cwd=app_dir, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out).resolve() if out else None


def mono_root(app_dir):
    """The top folder of the repository when the app is a folder of a larger repository (the repository viewers, with
    all the apps and viewkit), else None (the app is a repository of its own)."""
    top = toplevel(app_dir)
    return top if top and top != Path(app_dir).resolve() else None


def notes_repo(app_dir):
    """The name of the repository of the notes: OWNER/NAME of the GitHub repository of origin, or local/FOLDER (the top
    folder of the checkout) when the checkout is not on GitHub."""
    repo = repo_of(app_dir)
    if repo:
        return repo
    top = toplevel(app_dir) or Path(app_dir).resolve()
    return f"local/{top.name}"


_trackers = {}  # repository -> "github" or "local", in this process


def tracker(repo):
    """Where the notes of the repository go: "github" when the gh user may push to it (the owner, or a fork), else
    "local" (the local tracker): no push access, no gh, or no gh login. VIEWKIT_NOTES=github|local sets it. Raises
    RuntimeError when gh cannot answer (no network): then the note waits in the queue."""
    env = os.environ.get("VIEWKIT_NOTES")
    if env in ("github", "local"):
        return env
    if repo.startswith("local/"):
        return "local"
    if repo not in _trackers:
        try:
            r = _run([GH, "api", f"repos/{repo}", "--jq", ".permissions.push"])
        except FileNotFoundError:
            _trackers[repo] = "local"  # no gh
        except subprocess.SubprocessError as e:
            raise RuntimeError(f"gh did not answer: {e}")
        else:
            err = (r.stderr + r.stdout).lower()
            if r.returncode == 0:
                _trackers[repo] = "github" if r.stdout.strip() == "true" else "local"
            elif "auth login" in err or "not logged" in err or "not found" in err or "404" in err:
                _trackers[repo] = "local"  # no login, or the repository is not visible to this user
            else:
                raise RuntimeError((r.stderr or r.stdout).strip() or "gh failed.")
    return _trackers[repo]


def tracker_of_url(url):
    """The tracker of an issue from its URL: "local" for local://..., "github" for https://..., else None."""
    url = str(url or "")
    return "local" if url.startswith("local://") else "github" if url.startswith("http") else None


def gh_cmd(mode):
    """The command for the tracker: gh, or the local tracker."""
    return [GH] if mode == "github" else [sys.executable, str(LOCALGH)]


def commit_of(app_dir):
    """The short commit and the branch of a checkout, and whether it has changes that are not committed."""
    def git(*a):
        try:
            return _run(["git", *a], cwd=app_dir, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"commit": git("rev-parse", "--short", "HEAD"), "branch": git("branch", "--show-current"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


SCOPES = ("all", "app")  # the change is for all the viewers (in viewkit), or only for this app


def make(app, app_dir, text, source="user", context=None, scope=None):
    """A note as a dict: the app, the text, the source, the scope, and the context with the commit of the app."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("Write the note first.")
    if len(text) > TEXT_MAX:
        raise ValueError(f"The note is too long (more than {TEXT_MAX} characters).")
    if source not in SOURCES:
        raise ValueError(f"The source must be one of {', '.join(SOURCES)}.")
    ctx = {**commit_of(app_dir), **(context or {})}
    return {"app": app, "repo": notes_repo(app_dir), "text": text, "source": source, "context": ctx,
            "scope": scope if scope in SCOPES else None,
            "time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def title_of(text):
    line = " ".join(text.split())
    return line if len(line) <= 72 else line[:71].rstrip() + "…"


def body_of(note):
    ctx = json.dumps(note["context"], indent=2, ensure_ascii=False)
    scope = {"all": "Scope: all the viewers (the change goes into viewkit if it can)",
             "app": f"Scope: only {note['app']} (the other viewers do not change)"}.get(note.get("scope"))
    return (f"{note['text']}\n\n---\n" + (f"**{scope}**\n\n" if scope else "") +
            f"Source: {note['source']} · App: {note['app']} · Time: {note['time']}\n\n"
            f"<details><summary>Context</summary>\n\n```json\n{ctx}\n```\n</details>\n")


def _ensure_labels(repo):
    if repo in _labels_done or tracker(repo) == "local":
        return
    ok = True
    for name, color, desc in LABELS:
        r = _run([GH, "label", "create", name, "-R", repo, "--color", color, "--description", desc, "--force"])
        ok = ok and r.returncode == 0
    if ok:
        _labels_done.add(repo)


def _send(note):
    """Make the issue in the tracker of the repository. Returns its URL. Raises RuntimeError if gh fails."""
    repo = note.get("repo")
    if not repo:
        raise RuntimeError("The note has no repository.")
    try:
        mode = tracker(repo)
        _ensure_labels(repo)
        out = _run(gh_cmd(mode) + ["issue", "create", "-R", repo, "--title", title_of(note["text"]),
                                   "--label", LABEL, "--body-file", "-"], stdin=body_of(note))
    except FileNotFoundError:
        raise RuntimeError("The command gh is not installed.")
    except subprocess.SubprocessError as e:
        raise RuntimeError(f"gh did not answer: {e}")
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip() or "gh failed.")
    return out.stdout.strip().splitlines()[-1]


def _queued():
    try:
        lines = QUEUE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _write_queue(notes):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    if not notes:
        QUEUE.unlink(missing_ok=True)
        return
    tmp = QUEUE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in notes))
    os.replace(tmp, QUEUE)


def flush():
    """Send the notes in the queue. Returns the number that are still in the queue."""
    with _lock:
        left = []
        for n in _queued():
            try:
                _send(n)
            except RuntimeError:
                left.append(n)
        _write_queue(left)
        return len(left)


def add(app, app_dir, text, source="user", context=None, scope=None):
    """Make a note. Returns {"url": ...} when the issue exists, or {"queued": True, "why": ...} when gh failed and
    the note waits in the queue. Raises ValueError for a bad note."""
    note = make(app, app_dir, text, source, context, scope)
    flush()
    try:
        return {"url": _send(note)}
    except RuntimeError as e:
        with _lock:
            _write_queue(_queued() + [note])
        return {"queued": True, "why": str(e)}


def clean_context(raw, keys=None, max_items=20):
    """The context that a page sent, made safe: short scalar values and short lists of short strings only.
    With `keys`, only those keys."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in list(raw.items())[:max_items]:
        k = str(k)[:40]
        if keys is not None and k not in keys:
            continue
        if isinstance(v, (bool, int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:200]
        elif isinstance(v, list):
            out[k] = [str(x)[:80] for x in v[:12]]
    return out


def note_main(app, app_dir, argv):
    """The `APP note` subcommand: make a note from the command line (Claude, scripts, the user)."""
    ap = argparse.ArgumentParser(prog=f"{app} note", description=f"Write a note to {app}: a problem or a wish about "
                                 f"the app itself (not about a document). The note becomes a GitHub issue.")
    ap.add_argument("text", help='the note, or "-" to read it from stdin')
    ap.add_argument("--source", choices=SOURCES, default="agent",
                    help="who writes the note (default: agent, for Claude)")
    ap.add_argument("--flush", action="store_true", help="only send the notes in the local queue")
    a = ap.parse_args(argv)
    if a.flush:
        left = flush()
        print(f"{app}: {left} note(s) still in the queue." if left else f"{app}: the queue is empty.")
        return
    text = sys.stdin.read() if a.text == "-" else a.text
    try:
        res = add(app, app_dir, text, a.source, {"via": "command line"})
    except ValueError as e:
        sys.exit(f"{app}: {e}")
    if res.get("url", "").startswith("local://"):
        print(f"{app}: note saved in the local tracker: {res['url']}")
    elif res.get("url"):
        print(f"{app}: note sent: {res['url']}")
    else:
        print(f"{app}: note kept in {QUEUE}; it goes to GitHub with the next note ({res['why']})")


# ---------------------------------------------------------------------------------------------------------- the manifest
def manifest(app_dir):
    """The improve.json of an app: {"app", "port_env", "claude_env", "tests": [...], "companions": [...],
    "samples": [...], "default_file"}. "samples" are representative documents in the repository: every preview opens
    copies of them, next to the document of the note. The tests set VIEWKIT_IMPROVE_TESTS (a JSON list) in place of
    "tests"."""
    m = json.loads((Path(app_dir) / "improve.json").read_text(encoding="utf-8"))
    if os.environ.get("VIEWKIT_IMPROVE_TESTS"):
        m["tests"] = json.loads(os.environ["VIEWKIT_IMPROVE_TESTS"])
    return m


def companions_of(m, p):
    """The files that go with document p, for example its comments file (manifest "companions", with {name})."""
    return [p.parent / c.format(name=p.name) for c in m.get("companions", [])]


# ---------------------------------------------------------------------------------------------------------- the sandbox
# The user tries a proposal before Accept. The sandbox is the server of the proposal (its worktree), on its own port,
# with copies of the documents. The real documents and their comments do not change. It is not a process sandbox: the
# code of the proposal runs as the user, but it sees only the copies (and the files that the user grants to it).

def worktree_of(app, app_dir, n):
    """The folder ~/tools/wt/note-APP-N and the folder of the app in it: the worktree of the app (W/APP), or in the
    repository viewers the folder of the app in the worktree of the whole repository (W/viewers/APP). The wt folder is
    next to the app repositories, or next to the repository viewers. The tests set VIEWKIT_WT in place of it."""
    mono = mono_root(app_dir)
    base = mono.parent if mono else Path(app_dir).resolve().parent
    root = Path(os.environ["VIEWKIT_WT"]) if os.environ.get("VIEWKIT_WT") else base / "wt"
    w = root / f"note-{app}-{n}"
    return (w, w / mono.name / app) if mono else (w, w / app)


def kit_worktree_of(app, app_dir, n):
    """The folder of viewkit for the proposal of note n: W/viewkit (a worktree of viewkit), or W/viewers/viewkit."""
    w, wt = worktree_of(app, app_dir, n)
    return wt.parent / "viewkit" if mono_root(app_dir) else w / "viewkit"


def sandbox_port(n):
    """The port of the sandbox of note n: 8900+N. The tests set VIEWKIT_SANDBOX_BASE, so they never take the port of a
    real preview."""
    return int(os.environ.get("VIEWKIT_SANDBOX_BASE") or 8900) + int(n) % 100


def sandbox_env(m, n, extra=None):
    return {**os.environ, m["port_env"]: str(sandbox_port(n)), **(extra or {})}


def copy_docs(m, box, srcs):
    """Copy the documents and their companions into the sandbox folder. Returns the paths of the copies."""
    import shutil
    box.mkdir(parents=True, exist_ok=True)
    copies = []
    for src in srcs:
        if not src.is_file():
            raise ValueError(f"{src} is not a file.")
        shutil.copy2(src, box / src.name)
        for c in companions_of(m, src):
            if c.is_file():
                rel = c.relative_to(src.parent)
                (box / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(c, box / rel)
        copies.append(box / src.name)
    return copies


def start_sandbox(app, app_dir, n, srcs, extra_env=None):
    """Start the sandbox server of the proposal of note n (no browser), with copies of srcs (the document of the note)
    and of the samples of the proposal (manifest "samples", in sandbox-note-N/samples/). Returns [{name, url}], the
    documents first, then the samples. Without documents and samples, it opens the "default_file"."""
    m = manifest(app_dir)
    w, wt = worktree_of(app, app_dir, n)
    script = wt / f"{app}.py"
    if not script.is_file():
        raise ValueError(f"There is no proposal for note {n} ({wt} does not exist).")
    box = w / f"sandbox-note-{n}"
    samples = [wt / f for f in m.get("samples", []) if (wt / f).is_file()]
    if not srcs and not samples and m.get("default_file"):
        srcs = [wt / m["default_file"]]
    copies = copy_docs(m, box, srcs) + copy_docs(m, box / "samples", samples)
    r = subprocess.run([sys.executable, str(script), *map(str, copies), "--no-open"], env=sandbox_env(m, n, extra_env),
                       capture_output=True, text=True, timeout=60)
    links = [x for x in r.stdout.split() if x.startswith("http")]
    if not links:
        raise RuntimeError(f"The sandbox server did not start: {(r.stderr or r.stdout).strip()[-400:]}")
    names = [("sample: " if c.parent.name == "samples" else "") + c.name for c in copies]
    return [{"name": nm, "url": u} for nm, u in zip(names, links)]


def stop_sandbox(app, app_dir, n):
    m = manifest(app_dir)
    _, wt = worktree_of(app, app_dir, n)
    if (wt / f"{app}.py").is_file():
        subprocess.run([sys.executable, str(wt / f"{app}.py"), "--stop"], env=sandbox_env(m, n),
                       capture_output=True, text=True, timeout=30)


def try_main(app, app_dir, argv):
    """The `APP try N [FILE ...]` subcommand: open copies of FILE in the server of the proposal of note N."""
    import webbrowser
    ap = argparse.ArgumentParser(prog=f"{app} try", description=f"Try the proposal of note N in a sandbox: the {app} of "
                                 f"the branch improve/note-N, on port 8900+N, with copies of the files. Your files "
                                 f"and their comments do not change.")
    ap.add_argument("n", type=int, help="the number of the note (the GitHub issue)")
    ap.add_argument("files", nargs="*", help="the documents to try it on (copies); default: the test file of the app")
    ap.add_argument("--stop", action="store_true", help="stop the sandbox server")
    ap.add_argument("--no-open", action="store_true", help="print the link without opening a browser")
    a = ap.parse_args(argv)
    if a.stop:
        stop_sandbox(app, app_dir, a.n)
        print(f"{app}: stopped the sandbox of note {a.n}.")
        return
    try:
        files = start_sandbox(app, app_dir, a.n, [Path(f).expanduser().resolve() for f in a.files])
    except (ValueError, RuntimeError) as e:
        sys.exit(f"{app}: {e}")
    w, _ = worktree_of(app, app_dir, a.n)
    print(f"{app}: sandbox of note {a.n} on port {sandbox_port(a.n)}, with copies in {w / f'sandbox-note-{a.n}'}")
    for f in files:
        print(f"  {f['name']}: {f['url']}", flush=True)
    if not a.no_open:
        webbrowser.open(files[0]["url"])
    print(f"{app}: stop it with: {app} try --stop {a.n}")


# ---------------------------------------------------------------------------------------------------------- runs
# A run is a background process (improverun.py): implement, change, accept or reject. It writes its status to
# STATUS_DIR/APP-N.json (only for this user). The states: queued, working, testing, preview, changing, accepting,
# shipped, rejecting, rejected, question, failed. The status has the path of the document, so it stays on this computer.
STATUS_DIR = Path(os.environ.get("VIEWKIT_IMPROVE_DIR") or Path.home() / ".cache" / "viewkit" / "improve")
RUNNER = Path(__file__).resolve().parent / "improverun.py"
BUSY = ("queued", "working", "testing", "changing", "accepting", "rejecting")


def status_file(app, n):
    return STATUS_DIR / f"{app}-{int(n)}.json"


def read_status(app, n):
    try:
        return json.loads(status_file(app, n).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_status(app, n, **changes):
    """Change the status of the run of note n (an atomic write). Returns the new status."""
    STATUS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = {**(read_status(app, n) or {"app": app, "n": int(n), "rev": 0}), **changes,
          "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    tmp = status_file(app, n).with_suffix(f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, status_file(app, n))
    return st


def auto_on():
    """The automatic loop is on, unless VIEWKIT_AUTO_IMPROVE=0."""
    return os.environ.get("VIEWKIT_AUTO_IMPROVE", "1") != "0"


def spawn(action, app, app_dir, n, *args, kitdir=None):
    """Start a run of improverun.py in the background. It does not stop when the server stops or restarts.
    `kitdir` is the live viewkit: a sandbox server imports the kit of its proposal, but its decisions must start the
    runner of the live kit (the runner merges into the repository of its own folder)."""
    STATUS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = STATUS_DIR / f"{app}-{int(n)}.log"
    runner = Path(kitdir) / "improverun.py" if kitdir else RUNNER
    cmd = [sys.executable, str(runner), action, app, str(Path(app_dir).resolve()), str(int(n)), *map(str, args)]
    with open(log, "ab") as f:
        sh = subprocess.Popen(["/bin/sh", "-c", '"$@" </dev/null &', "sh", *cmd], stdout=f, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, start_new_session=True)
    sh.wait()


def number_of(url):
    """The issue number at the end of an issue URL, or None."""
    tail = str(url or "").rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def start_auto(app, app_dir, res, doc, back, text=""):
    """After a note from the user: start the run that implements it. `res` is the result of add(). Changes res."""
    n = number_of(res.get("url"))
    if not n or not auto_on():
        return res
    write_status(app, n, state="queued", message="The note waits for Claude.", doc=str(doc or ""), back=back or "",
                 live=str(Path(app_dir).resolve()), issue=res.get("url"), kitdir=str(RUNNER.parent),
                 tracker=tracker_of_url(res.get("url")),
                 title=title_of(text) if text else None, summary=None, url=None, error=None, rev=0)
    spawn("implement", app, app_dir, n)
    res.update(n=n, run=True)
    return res


def public_status(st):
    """The status for a page: never the path of the document."""
    if not st:
        return {"state": "none"}
    return {k: st.get(k) for k in ("n", "state", "message", "title", "summary", "url", "rev", "error", "back", "commit",
                                   "issue", "kit", "files")}


# ---------------------------------------------------------------------------------------------------------- the preview
# The sandbox server of a proposal gets VIEWKIT_PREVIEW=APP:N from the run. Its page shows the band of the kit
# (kit.previewBand) with Accept, Change and Reject. The decisions start runs of the live viewkit (improverun.py), which
# change the live checkout and then stop this server.
def preview_of():
    """(app, n) when this server is the sandbox of a proposal, else None."""
    v = os.environ.get("VIEWKIT_PREVIEW", "")
    app, _, n = v.partition(":")
    return (app, int(n)) if app and n.isdigit() else None


def preview_api(method, op=None):
    """The /api/improve/preview route of a sandbox server. GET: the status (or {"preview": False}). POST: a decision,
    {"action": "accept" | "change" | "reject", "text": feedback or reason}. Returns (http code, JSON)."""
    pv = preview_of()
    if not pv:
        return (200, {"preview": False}) if method == "GET" else (404, {"error": "This is not a preview."})
    app, n = pv
    st = read_status(app, n)
    if method == "GET":
        return 200, {"preview": True, **public_status(st)}
    action, text = str((op or {}).get("action", "")), str((op or {}).get("text") or "").strip()[:TEXT_MAX]
    if not st or st.get("state") != "preview":
        return 409, {"error": "Wait: Claude works on this proposal." if st and st.get("state") in BUSY
                     else "This proposal is closed."}
    if action not in ("accept", "change", "reject"):
        return 400, {"error": "Unknown action."}
    if action == "change" and not text:
        return 400, {"error": "Write what to change first."}
    msg = {"accept": "Claude merges the change.", "change": "Claude changes the proposal.",
           "reject": "Claude closes the note."}[action]
    write_status(app, n, state={"accept": "accepting", "change": "changing", "reject": "rejecting"}[action],
                 message=msg, error=None)
    live = Path(st["live"]) if st.get("live") else None
    if not live:
        return 500, {"error": "The status has no live checkout."}
    spawn(action, app, live, n, *([text] if text else []), kitdir=st.get("kitdir"))
    return 200, {"preview": True, **public_status(read_status(app, n))}


# ---------------------------------------------------------------------------------------------------------- the dashboard
# ⌘/Ctrl+Shift+. opens the dashboard of the app (kit.improveDash). A new note waits in the list of the dashboard, with
# the context of the page and the open document, until the user clicks Send all. Then each note becomes an issue and
# starts the automatic loop. The waiting notes are in STATUS_DIR/APP-drafts.json, on this computer only.
_drafts_lock = threading.Lock()


def _drafts_file(app):
    return STATUS_DIR / f"{app}-drafts.json"


def drafts(app):
    try:
        d = json.loads(_drafts_file(app).read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _write_drafts(app, items):
    STATUS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = _drafts_file(app).with_suffix(f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _drafts_file(app))


def runs(app, limit=15):
    """The runs of the app, the newest first, for the dashboard (public_status, never the path of a document)."""
    out = []
    for f in STATUS_DIR.glob(f"{app}-*.json"):
        n = f.stem[len(app) + 1:]
        if n.isdigit():
            st = read_status(app, n)
            if st:
                out.append(st)
    out.sort(key=lambda s: s.get("updated") or "", reverse=True)
    return [public_status(s) for s in out[:limit]]


def dash_api(app, app_dir, method, op=None, doc=None, back=None):
    """The /api/improve/dash route. GET: {drafts, runs, auto}. POST {action}: "add" {text, context}, "edit" {id, text},
    "delete" {id}, "send" (all the waiting notes). Returns (http code, JSON). `doc` and `back` are the open document
    and its live link: they stay on this computer, for the preview of the note."""
    def view():
        return {"drafts": [{k: d.get(k) for k in ("id", "text", "time", "scope")} for d in drafts(app)],
                "runs": runs(app), "auto": auto_on()}
    if method == "GET":
        return 200, view()
    op = op or {}
    action = str(op.get("action", ""))
    if action == "answer":  # an answer to a question of Claude, or Try again after a failure: the run starts again
        n = op.get("n")
        st = read_status(app, n) if isinstance(n, int) else None
        if not st or st.get("state") not in ("question", "failed"):
            return 409, {"error": "This note does not wait for an answer."}
        text = str(op.get("text") or "").strip()[:TEXT_MAX]
        if st.get("state") == "question" and not text:
            return 400, {"error": "Write your answer first."}
        repo = notes_repo(app_dir)
        body = ("**Answer from the dashboard:**\n\n" + text) if text else "**Try again** (from the dashboard)."
        try:
            cmd = gh_cmd(st.get("tracker") or tracker_of_url(st.get("issue")) or tracker(repo))
            _run(cmd + ["issue", "comment", str(n), "-R", repo, "--body-file", "-"], stdin=body)
            _run(cmd + ["issue", "edit", str(n), "-R", repo, "--remove-label", "question"])
        except (OSError, subprocess.SubprocessError, RuntimeError) as e:
            return 502, {"error": f"gh failed: {e}"}
        write_status(app, n, state="queued", message="Claude works on your answer." if text else "Claude tries again.",
                     error=None, live=st.get("live") or str(Path(app_dir).resolve()), kitdir=str(RUNNER.parent))
        # Try again with no text after a failure: the tests again, without Claude (retest falls back to implement).
        spawn("retest" if st.get("state") == "failed" and not text else "implement", app, st.get("live") or app_dir, n)
        return 200, view()
    with _drafts_lock:
        items = drafts(app)
        if action == "add":
            text = str(op.get("text") or "").strip()
            if not text:
                return 400, {"error": "Write the note first."}
            if len(text) > TEXT_MAX:
                return 400, {"error": f"The note is too long (more than {TEXT_MAX} characters)."}
            items.append({"id": max([d.get("id", 0) for d in items] + [0]) + 1, "text": text,
                          "scope": op.get("scope") if op.get("scope") in SCOPES else "all",
                          "context": clean_context(op.get("context")), "doc": str(doc or ""), "back": back or "",
                          "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})
        elif action in ("edit", "delete"):
            i = next((k for k, d in enumerate(items) if d.get("id") == op.get("id")), None)
            if i is None:
                return 404, {"error": "This note is not in the list."}
            if action == "delete":
                items.pop(i)
            else:
                text = str(op.get("text") or "").strip()
                if not text:
                    return 400, {"error": "A note cannot be empty. Delete it."}
                items[i]["text"] = text[:TEXT_MAX]
        elif action == "send":
            sent, left = [], []
            for d in items:
                ctx = {**d.get("context", {}), "doc_type": Path(d["doc"]).suffix if d.get("doc") else None}
                try:
                    res = add(app, app_dir, d["text"], "user", ctx, d.get("scope") or "all")
                except ValueError as e:
                    left.append({**d, "error": str(e)})
                    continue
                if res.get("url"):
                    res = start_auto(app, app_dir, res, d.get("doc"), d.get("back"), d["text"])
                sent.append({"id": d["id"], **{k: res.get(k) for k in ("url", "n", "run", "queued", "why")}})
            items = left
            _write_drafts(app, items)
            return 200, {**view(), "sent": sent}
        else:
            return 400, {"error": "Unknown action."}
        _write_drafts(app, items)
    return 200, view()
