"""Test of the automatic loop of the self-improving apps, from the dashboard to the merge, in a real browser.
One app at a time: pdfview, mdview or pyview (APPS below).

It works on copies: for the app and for viewkit, a bare repository as origin and a clone of it as the live checkout,
with the files of the real folders (also the files that are not committed). The notes go to a stand-in gh, and Claude
is the stand-in of viewkit (tests/fake_claude.py): it asks one question first, then it adds a line to README.md in
the app (committed) and in viewkit (not committed: the run commits it). The test runs no real Claude, makes no real
issue, and never changes the real repositories. Needs Playwright and its Chromium. Run, in viewkit:
    uv run --with playwright python tests/improve_test.py pdfview      (or mdview, or pyview)
With --mono, it tests the layout of the public repository viewers with the local tracker: one repository with the app
and viewkit as folders (one worktree and one branch for a proposal), the notes in localgh.py, and no push.
It deletes its folder in tests/tmp at the end. Set VIEWKIT_KEEP_TMP=1 to keep it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
# For each app: the document of the note (a file of the repository, copied as `doc`), the element that shows that the
# page is ready, the element to click before the hotkey, and the sample to open from the card of the preview.
APPS = {
    "pdfview": {"src": "tests/fixture.pdf", "doc": "paper.pdf", "ready": '.page[data-n="1"] .textLayer span',
                "focus": "#pages", "sample": "scan.pdf", "port": "8812"},
    "mdview": {"src": "samples/review.md", "doc": "notes.md", "ready": "#doc p", "focus": "#doc", "sample": "maths.md",
               "port": "8816"},
    "pyview": {"src": "tests/fixture.py", "doc": "notes.py", "ready": ".cell", "focus": "h1, header", "sample": "cells.py",
               "port": "8817"},
}
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
MONO = "--mono" in sys.argv
APP = ARGS[0] if ARGS else "pdfview"
if APP not in APPS:
    sys.exit(f"usage: improve_test.py {'|'.join(APPS)} [--mono]")
CFG = APPS[APP]
REPO = KIT.parent / APP
MAN = json.loads((REPO / "improve.json").read_text())
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix=f"improve-{APP}-", dir=TMP_ROOT))
PORT = CFG["port"]
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def sh(*a, cwd=None, env=None):
    return subprocess.run(a, cwd=cwd, env=env, capture_output=True, text=True)


def copy_repo(src, name):
    """origin (bare) and a live clone of it with the files of src (committed or not, but not ignored)."""
    origin, live = tmp / f"{name}.git", tmp / "tools" / name
    sh("git", "init", "-q", "--bare", "-b", "main", str(origin))
    sh("git", "clone", "-q", str(origin), str(live))
    for f in sh("git", "ls-files", "--cached", "--others", "--exclude-standard", cwd=src).stdout.splitlines():
        if (src / f).is_file():
            (live / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / f, live / f)
    sh("git", "-C", str(live), "add", "-A")
    sh("git", "-C", str(live), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    sh("git", "-C", str(live), "push", "-q", "-u", "origin", "main")
    return origin, live


def copy_mono():
    """The repository viewers: origin (bare) and a live clone, with the app and viewkit as folders."""
    origin, top = tmp / "viewers.git", tmp / "tools" / "viewers"
    sh("git", "init", "-q", "--bare", "-b", "main", str(origin))
    sh("git", "clone", "-q", str(origin), str(top))
    for src, name in ((REPO, APP), (KIT, "viewkit")):
        for f in sh("git", "ls-files", "--cached", "--others", "--exclude-standard", cwd=src).stdout.splitlines():
            if (src / f).is_file():
                (top / name / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / f, top / name / f)
    sh("git", "-C", str(top), "add", "-A")
    sh("git", "-C", str(top), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    sh("git", "-C", str(top), "push", "-q", "-u", "origin", "main")
    return origin, top


if MONO:
    origin, top = copy_mono()
    kit_origin, live, kit = origin, top / APP, top / "viewkit"
else:
    origin, live = copy_repo(REPO, APP)
    kit_origin, kit = copy_repo(KIT, "viewkit")
tools = tmp / "tools"
issues = tmp / "issues"
paper = tmp / "docs" / CFG["doc"]
paper.parent.mkdir()
shutil.copy2(REPO / CFG["src"], paper)

gh_log, state = tmp / "gh.jsonl", tmp / "state"
# The stand-in claude, executable and first in PATH: the test must never start the real claude.
bindir = tmp / "bin"
bindir.mkdir()
(bindir / "claude").write_text('#!/bin/sh\nexec python3 "%s" "$@"\n' % (kit / "tests" / "fake_claude.py"))
(bindir / "claude").chmod(0o755)
env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", **{MAN["port_env"]: PORT},
           **({MAN["claude_env"]: str(bindir / "claude")} if MAN.get("claude_env") else {}),
           VIEWKIT_GH=str(kit / "tests" / "fake_gh.py"), FAKE_GH_LOG=str(gh_log),
           VIEWKIT_NOTES_QUEUE=str(tmp / "queue.jsonl"), VIEWKIT_REPO=f"owner/{APP}", VIEWKIT_IMPROVE_DIR=str(state),
           VIEWKIT_ACCOUNT_FILE=str(tmp / "accounts.json"),
           VIEWKIT_CLAUDE=str(bindir / "claude"), VIEWKIT_NO_OPEN="1", VIEWKIT_AUTO_IMPROVE="1",
           VIEWKIT_IMPROVE_TESTS=json.dumps(["test -f README.md", f"test -f {tmp / 'pass'}"]),  # fails until "pass" exists
           FAKE_CLAUDE_LOG=str(tmp / "claude.jsonl"),
           VIEWKIT_IMPROVE_APPS="{}", FAKE_IMPROVE_KIT="1", FAKE_IMPROVE_QUESTION_FILE=str(tmp / "asked"),
           VIEWKIT_IMPROVE_WAIT="2", VIEWKIT_SANDBOX_BASE="9300", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t")
env.pop("VIEWKIT_WT", None)
if MONO:  # the local tracker: the stand-in gh must never be used
    env.update(VIEWKIT_NOTES="local", VIEWKIT_ISSUES_DIR=str(issues), VIEWKIT_REPO="owner/viewers",
               VIEWKIT_GH=str(tmp / "no-gh"), FAKE_IMPROVE_GH_FILE=str(tmp / "gh-title"))
LIVE_PY = str(live / f"{APP}.py")


def status(n):
    try:
        return json.loads((state / f"{APP}-{n}.json").read_text())
    except (OSError, ValueError):
        return {}


def wait_state(n, states, secs=90):
    end = time.time() + secs
    while time.time() < end:
        st = status(n)
        if st.get("state") in states:
            return st
        time.sleep(0.5)
    return status(n)


def gh_calls(*head):
    calls = [json.loads(x) for x in gh_log.read_text().splitlines()] if gh_log.exists() else []
    return [c for c in calls if c["argv"][:len(head)] == list(head)]


def local_issues():
    d = issues / "owner__viewers"
    return {int(p.stem): json.loads(p.read_text()) for p in d.glob("*.json")} if d.is_dir() else {}


def bodies_of_notes():
    if MONO:
        return {i["title"]: i["body"] for i in local_issues().values()}
    return {c["stdin"].split("\n")[0]: c["stdin"] for c in gh_calls("issue", "create")}


def commented(text, n=None):
    if MONO:
        return any(text in c["body"] for k, i in local_issues().items() if n in (None, k) for c in i["comments"])
    return any(text in c["stdin"] for c in gh_calls("issue", "comment") if n is None or c["argv"][2] == str(n))


def closed(n, reason=None):
    if MONO:
        i = local_issues().get(n, {})
        return i.get("state") == "CLOSED" and (reason is None or i.get("stateReason") == reason.upper().replace(" ", "_"))
    return any(c["argv"][2] == str(n) and (reason is None or reason in c["argv"]) for c in gh_calls("issue", "close"))


def tree_of(n):
    """The folder of the proposal of note n, and the folders of the app and of viewkit in it."""
    w = tools / "wt" / f"note-{APP}-{n}"
    return (w, w / "viewers" / APP, w / "viewers" / "viewkit") if MONO else (w, w / APP, w / "viewkit")


def rev(d, ref):
    return sh("git", "-C", str(d), "rev-parse", "--verify", "--quiet", ref).stdout.strip()


def log_of(n):
    try:
        return (state / f"{APP}-{n}.log").read_text()[-1500:]
    except OSError:
        return ""


def send_notes(pg, notes):
    """Write the notes in the dashboard ((text, scope) pairs), then Send all. Returns the issue numbers (the stand-in
    gh numbers an issue by its line in its log)."""
    pg.locator(CFG["focus"]).first.click()
    pg.keyboard.press("Control+Shift+Period")
    for text, scope in notes:
        pg.locator(".kitDashOpt", has_text="All viewers" if scope == "all" else f"Only {APP}").click()
        pg.locator(".kitDashNew textarea").fill(text)
        pg.locator(".kitDashNew textarea").press("Enter")
    pg.wait_for_timeout(500)
    before = len(gh_log.read_text().splitlines()) if gh_log.exists() else 0
    known = set(local_issues())
    pg.locator(".kitDashSend").click()
    pg.wait_for_function("() => document.querySelectorAll('.kitDashNote').length === 0", timeout=20000)
    if MONO:
        return sorted(set(local_issues()) - known)
    lines = [json.loads(x) for x in gh_log.read_text().splitlines()]
    return [i + 1 for i, c in enumerate(lines) if i >= before and c["argv"][:2] == ["issue", "create"]]


try:
    url = sh(sys.executable, LIVE_PY, str(paper), "--no-open", env=env).stdout.strip().split()[-1]
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1300, "height": 900})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(url)
        pg.wait_for_selector(CFG["ready"], timeout=20000)
        live_head, kit_head = rev(live, "HEAD"), rev(kit, "HEAD")

        # --- 1. two notes, one for all the viewers and one only for the app; one Send all
        n, m = send_notes(pg, [("Make the chat text larger.", "all"), ("Hide the header.", "app")])
        bodies = bodies_of_notes()
        if MONO:
            check("the notes are issues of the local tracker, and gh is never called", len(bodies) == 2
                  and not gh_log.exists(), bodies)
        check("each issue tells the scope of its note", "Scope: all the viewers" in bodies.get("Make the chat text larger.", "")
              and f"Scope: only {APP}" in bodies.get("Hide the header.", ""), bodies)
        check("the page tells that the notes go to Claude", "2 notes sent to Claude" in pg.inner_text("#toast"),
              pg.inner_text("#toast"))

        # --- 2. the first run asks a question; the tests of the second note fail (no "pass" file yet)
        st = wait_state(n, ("question", "preview", "failed"))
        check("Claude asks a question on the first note", st.get("state") == "question" and "Which color" in st.get("error", ""),
              (st, log_of(n)))
        pg.wait_for_timeout(3500)
        stm = wait_state(m, ("failed", "preview"))
        check("a failed test stops the second note", stm.get("state") == "failed" and "pass" in (stm.get("error") or ""),
              (stm, log_of(m)))
        pg.wait_for_timeout(3500)
        ask = pg.locator(".kitDashAsk", has_text="Which color")
        check("the dashboard shows the question with an answer box", ask.count() == 1
              and ask.locator("textarea").count() == 1, pg.inner_text(".kitDashRuns"))
        check("the dashboard shows the failure with Try again", pg.locator(".kitDashAsk button", has_text="Try again").count() == 1,
              pg.inner_text(".kitDashRuns"))
        (tmp / "pass").touch()
        ask.locator("textarea").fill("2, blue")
        ask.locator("button", has_text="Answer").click()
        pg.wait_for_timeout(1000)
        check("the answer goes to the issue", commented("2, blue", n))
        st = wait_state(n, ("preview", "failed"))
        check("after the answer, the run makes a proposal and a preview", st.get("state") == "preview" and st.get("url"),
              (st, log_of(n)))
        calls = len((tmp / "claude.jsonl").read_text().splitlines())
        pg.wait_for_timeout(3500)
        pg.locator(".kitDashAsk button", has_text="Try again").click()
        pg.wait_for_timeout(1500)
        check("Try again makes the preview of the second note, without a new Claude run",
              wait_state(m, ("preview", "failed")).get("state") == "preview"
              and len((tmp / "claude.jsonl").read_text().splitlines()) == calls, log_of(m))

        wt, wapp, wkit = tree_of(n)
        if MONO:
            check("the proposal changes the app and viewkit on one branch of one worktree",
                  "fake Claude" in (wapp / "README.md").read_text() and "kit implement" in (wkit / "README.md").read_text()
                  and rev(live, f"improve/note-{n}") and not rev(live, f"improve/{APP}-note-{n}"), log_of(n))
            check("Claude's gh in the run is the local tracker", f"Make the chat text larger." in
                  (tmp / "gh-title").read_text() if (tmp / "gh-title").exists() else False, log_of(n))
        else:
            check("the proposal changes the app and viewkit, each on its branch",
                  "fake Claude" in (wapp / "README.md").read_text() and "kit implement" in (wkit / "README.md").read_text()
                  and rev(live, f"improve/note-{n}") and rev(kit, f"improve/{APP}-note-{n}"), log_of(n))
        check("the run commits the kit part, and the status says that all the viewers change",
              not sh("git", "-C", str(wkit), "status", "--porcelain").stdout and st.get("kit") is True)
        check("the live checkouts do not change before Accept", rev(live, "HEAD") == live_head and rev(kit, "HEAD") == kit_head)
        if MONO:
            check("with the local tracker, no branch is pushed", not rev(origin, f"improve/note-{n}"))
        else:
            check("both branches are pushed", rev(origin, f"improve/note-{n}") and rev(kit_origin, f"improve/{APP}-note-{n}"))
        check("the sandbox has a copy of the document that was open", (wt / f"sandbox-note-{n}" / CFG["doc"]).is_file())
        pg.wait_for_timeout(3500)
        check("the status shows the previews with Open", pg.locator(".kitDashRun.preview .kitDashOpen").count() == 2,
              pg.inner_text(".kitDashRuns"))
        pg.keyboard.press("Escape")

        pv = ctx.new_page()
        pv.on("pageerror", lambda e: errs.append("preview: " + str(e)))
        pv.goto(st["url"])
        pv.wait_for_selector(".kitPreview", timeout=20000)
        check("the preview tab shows the band with the note and the summary",
              f"note #{n}" in pv.inner_text(".kitPreview") and "fake Claude" in pv.inner_text(".kitPreviewSum"),
              pv.inner_text(".kitPreview"))
        check("the feedback form is hidden until Change", not pv.locator(".kitPreviewForm").is_visible())
        names = [f["name"] for f in st.get("files") or []]
        k = 1 + len(MAN.get("samples", []))
        check("the preview has the document of the note and the samples",
              names[:1] == [CFG["doc"]] and f"sample: {CFG['sample']}" in names and len(names) == k, names)
        links = pv.locator(".kitPreviewFiles a").all_inner_texts()
        check("the preview card links to all the files, and marks the open one",
              len(links) == k and pv.locator(".kitPreviewFiles a.here").inner_text() == CFG["doc"], links)
        pv.locator(".kitPreviewFiles a", has_text=CFG["sample"]).click()
        pv.wait_for_selector(".kitPreview", timeout=20000)
        check("a file link opens that sample in the preview, with the card", "sandbox-note-" in pv.url
              and f"samples/{CFG['sample']}" in pv.url and pv.locator(".kitPreviewFiles a.here").inner_text() == CFG["sample"],
              pv.url)

        acc = json.loads((tmp / "accounts.json").read_text()) if (tmp / "accounts.json").exists() else {}
        check("the copy of the document in the sandbox has the account of the document",
              (acc.get("docs") or {}).get(str((wt / f"sandbox-note-{n}" / CFG["doc"]).resolve())), acc)

        # --- 3. Change: feedback from the preview; Claude changes the proposal and the page reloads
        # main moves first: Change rebases the proposal on it (improverun.catch_up)
        (live / "UPSTREAM.txt").write_text("a commit on main after the proposal\n")
        sh("git", "-C", str(live), "add", "UPSTREAM.txt")
        sh("git", "-C", str(live), "commit", "-q", "-m", "a commit on main")
        pv.locator(".kitPreview button", has_text="Change").click()
        pv.locator(".kitPreviewForm textarea").fill("Also make it blue.")
        pv.locator(".kitPreviewForm textarea").press("Enter")
        end = time.time() + 60
        while time.time() < end and status(n).get("rev", 0) < 2:
            time.sleep(0.5)
        st2 = status(n)
        check("Change makes a second version of the proposal", st2.get("rev") == 2 and st2.get("state") == "preview"
              and "kit change" in (wkit / "README.md").read_text(), (st2, log_of(n)))
        check("Change rebases the proposal on the new main", (wapp / "UPSTREAM.txt").is_file(), log_of(n))
        pv.wait_for_timeout(3000)
        pv.wait_for_selector(".kitPreview", timeout=20000)
        check("the preview reloads with the new version", "change" in pv.inner_text(".kitPreviewSum"),
              pv.inner_text(".kitPreviewSum"))

        # --- 4. Accept: merge viewkit and the app, push both, close; the tab goes back to the live document
        pv.locator(".kitPreview button", has_text="Accept").click()
        st3 = wait_state(n, ("shipped", "failed"), 60)
        check("Accept merges the proposal into main of the app and of viewkit", st3.get("state") == "shipped"
              and "A change by the fake Claude" in (live / "README.md").read_text()
              and "kit change" in (kit / "README.md").read_text(), (st3, log_of(n)))
        if MONO:
            check("with the local tracker, Accept does not push", rev(origin, "main") != rev(live, "main"))
        else:
            check("Accept pushes both", rev(origin, "main") == rev(live, "main") and rev(kit_origin, "main") == rev(kit, "main"))
        check("Accept closes the issue", closed(n))
        pv.wait_for_timeout(3000)
        check("the preview tab goes back to the live document", f":{PORT}/view" in pv.url, pv.url)
        pv.wait_for_timeout(3000)
        check("the worktrees, the sandbox and the branches are removed", not wt.exists()
              and not rev(live, f"improve/note-{n}") and not rev(kit, f"improve/{APP}-note-{n}")
              and not rev(origin, f"improve/note-{n}") and not rev(kit_origin, f"improve/{APP}-note-{n}"), log_of(n))
        if MONO:
            check("git has no worktree of the accepted note",
                  f"note-{APP}-{n}" not in sh("git", "-C", str(live), "worktree", "list").stdout)

        # --- 5. Reject: the second note; Reject closes it and removes the proposal
        head, khead = rev(live, "HEAD"), rev(kit, "HEAD")
        st = status(m)
        pv2 = ctx.new_page()
        pv2.goto(st["url"])
        pv2.wait_for_selector(".kitPreview", timeout=20000)
        pv2.locator(".kitPreview button", has_text="Reject").click()
        pv2.locator(".kitPreviewForm textarea").fill("I need the header.")
        pv2.locator(".kitPreviewForm textarea").press("Enter")
        st = wait_state(m, ("rejected", "failed"), 30)
        check("Reject closes the note with the reason", st.get("state") == "rejected"
              and commented("I need the header.", m) and closed(m, "not planned"), st)
        pv2.wait_for_timeout(5000)
        check("Reject does not change the live checkouts", rev(live, "HEAD") == head and rev(kit, "HEAD") == khead)
        check("Reject removes the worktrees and the kit branch", not (tools / "wt" / f"note-{APP}-{m}").exists()
              and not rev(kit, f"improve/{APP}-note-{m}"), log_of(m))
        check("no errors on the pages", not [e for e in errs if "Failed to fetch" not in e], errs)
        b.close()
finally:
    for k in range(1, 40):
        _, wapp_k, _ = tree_of(k)
        if wapp_k.exists():
            sh(sys.executable, str(wapp_k / f"{APP}.py"), "--stop", env={**env, MAN["port_env"]: str(9300 + k)})
    sh(sys.executable, LIVE_PY, "--stop", env=env)
    nums = [x for x in (globals().get("n"), globals().get("m")) if x]
    real = [b for k in nums for b in [f"improve/{APP}-note-{k}"] if rev(KIT, b)]
    check("the real viewkit has no branch of the test", not real, real)
    if os.environ.get("VIEWKIT_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
