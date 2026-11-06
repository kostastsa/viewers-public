#!/usr/bin/env python3
"""The background runs of the self-improving apps (docs/self-improving-apps.md). improvekit.spawn starts them:

    improverun.py implement APP LIVE N        Claude implements note N in a worktree; then the tests, the push, the
                                              comment on the issue, and the preview tab (the sandbox server)
    improverun.py change APP LIVE N TEXT      Claude changes the proposal from the feedback TEXT of the preview tab
    improverun.py accept APP LIVE N           merge into main of the live checkouts (viewkit first), push, close
    improverun.py reject APP LIVE N [REASON]  close the issue, remove the proposal
    improverun.py retest APP LIVE N           Try again after failed tests: the tests and the next steps, no Claude

A proposal has two worktrees in ~/tools/wt/note-APP-N: APP (branch improve/note-N) and viewkit (branch
improve/APP-note-N). Claude may edit both. When viewkit changes, the run also tests the other apps (viewkit/improve.json,
"apps") in worktrees next to the viewkit worktree, so they use the kit of the proposal. Accept merges viewkit first.

One run at a time for each app (a lock file). The run writes its status for the pages (improvekit.write_status). The
run does not depend on the server: a restart of the server does not stop it. Its log is STATUS_DIR/APP-N.log.
Claude runs with `claude -p` and may edit files only in the worktrees of the note. It may not push, rebase or close
issues: the run does those steps itself, after its own test run.
"""
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

KIT = Path(__file__).resolve().parent  # the live viewkit: its repository gets the kit part of a proposal
sys.path.insert(0, str(KIT))
import claudekit  # noqa: E402
import improvekit as ik  # noqa: E402


class Fail(Exception):
    """A step failed. The message is for the user."""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def git(cwd, *a, check=True, timeout=300):
    r = subprocess.run(["git", *a], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise Fail(f"git {' '.join(a)} failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout.strip() if check else r


def gh(*a, stdin=None):
    try:
        r = subprocess.run([ik.GH, *a], capture_output=True, text=True, input=stdin, timeout=60)
        if r.returncode != 0:
            log("gh failed:", " ".join(a[:3]), (r.stderr or r.stdout).strip()[-200:])
        return r
    except (OSError, subprocess.SubprocessError) as e:
        log("gh failed:", e)
        return None


def comment(repo, n, text):
    gh("issue", "comment", str(n), "-R", repo, "--body-file", "-", stdin=text)


@contextlib.contextmanager
def app_lock(app):
    """One run at a time for each app. The next run waits."""
    ik.STATUS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(ik.STATUS_DIR / f"{app}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def claude_exe(m):
    """The claude command. A path in VIEWKIT_CLAUDE or in the variable of the app (manifest "claude_env") is used as it
    is: if it is not executable, the run stops (it never falls back to another claude)."""
    for var in ("VIEWKIT_CLAUDE", m.get("claude_env")):
        exe = os.environ.get(var or "_")
        if exe:
            if not os.access(exe, os.X_OK):
                raise Fail(f"{var}={exe} is not an executable file.")
            return exe
    for exe in (shutil.which("claude"), str(Path.home() / ".local/bin/claude"), "/opt/homebrew/bin/claude",
                "/usr/local/bin/claude"):
        if exe and os.access(exe, os.X_OK):
            return exe
    raise Fail("Cannot find the claude command. Set VIEWKIT_CLAUDE to its path.")


def run_claude(m, prompt, cwd, tools, disallowed=(), add_dirs=()):
    """One `claude -p` run. Returns its last text (the result)."""
    cmd = [claude_exe(m), "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits"]
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    cmd += ["--allowedTools", ",".join(tools)]
    if disallowed:
        cmd += ["--disallowedTools", ",".join(disallowed)]
    out = {"text": "", "error": False}

    def on_event(ev):
        if ev.get("type") == "result":
            out.update(text=str(ev.get("result") or ""), error=bool(ev.get("is_error")), cost=ev.get("total_cost_usd"))
        elif ev.get("type") == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "tool_use":
                    log("claude:", b.get("name"), claudekit.tool_summary(b))
    log("claude -p", prompt)
    err, stderr, rc = claudekit.run_stream(cmd, cwd, claudekit.clean_env(), on_event)
    if err:
        raise Fail(err)
    if rc != 0 and not out["text"]:
        raise Fail(f"Claude stopped with an error: {stderr.strip()[-300:]}")
    log("claude: done, cost", out.get("cost"))
    return out["text"].strip()


def code_tools(wt, kwt):
    """What Claude may do: read, edit in the two worktrees only, commit in the app, run the tests, read the issue."""
    tools = ["Read", "Grep", "Glob", f"Edit(/{wt}/**)", f"Write(/{wt}/**)", f"Edit(/{kwt}/**)", f"Write(/{kwt}/**)",
             "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)", "Bash(git add:*)",
             "Bash(git commit:*)", "Bash(gh issue view:*)", "Bash(gh issue comment:*)", "Bash(python3:*)",
             "Bash(uv run:*)", "Bash(ls:*)"]
    deny = ["Bash(git push:*)", "Bash(git checkout:*)", "Bash(git switch:*)", "Bash(git reset:*)",
            "Bash(git rebase:*)", "Bash(git merge:*)", "Bash(git -C:*)", "Bash(gh issue close:*)",
            "Bash(gh issue edit:*)", "WebFetch"]
    return tools, deny


def kit_branch(app, n):
    return f"improve/{app}-note-{n}"


def other_apps():
    """{app: [test commands]} of the apps, for a change of the kit. The tests set VIEWKIT_IMPROVE_APPS."""
    if os.environ.get("VIEWKIT_IMPROVE_APPS"):
        return json.loads(os.environ["VIEWKIT_IMPROVE_APPS"])
    try:
        return json.loads((KIT / "improve.json").read_text(encoding="utf-8")).get("apps", {})
    except (OSError, ValueError):
        return {}


def run_cmds(cmds, cwd, m=None):
    """Run test commands in a folder. Returns (passed, the end of the output)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIEWKIT_PREVIEW") and k != (m or {}).get("port_env")}
    for cmd in cmds:
        log("test:", cwd.name, cmd)
        try:
            r = subprocess.run(cmd, shell=True, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            return False, f"{cwd.name}: {cmd}: no end after 30 minutes"
        if r.returncode != 0:
            return False, f"{cwd.name}: {cmd}:\n{(r.stdout + r.stderr).strip()[-1500:]}"
    return True, ""


def run_tests(app, m, w, wt, kit_changed):
    """The tests of the app, and for a change of the kit also the tests of the other apps, each in a worktree of its
    main next to the kit worktree (so it uses the kit of the proposal)."""
    ok, out = run_cmds(m.get("tests", []), wt, m)
    if not ok or not kit_changed:
        return ok, out
    for name, cmds in other_apps().items():
        repo = KIT.parent / name
        if name == app or not (repo / ".git").exists():
            continue
        owt = w / name
        if not owt.exists():
            add_worktree(repo, owt)
        else:
            git(owt, "checkout", "-q", "--detach", "main", check=False)
            link_ignored(repo, owt)
        ok, out = run_cmds(cmds, owt)
        if not ok:
            return ok, out
    return True, ""


def sandbox_up(n):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{ik.sandbox_port(n)}/api/ping", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def issue_title(repo, n):
    r = gh("issue", "view", str(n), "-R", repo, "--json", "title", "-q", ".title")
    return r.stdout.strip() if r and r.returncode == 0 else f"note #{n}"


SKIP_LINKS = {"__pycache__", "node_modules", "venv", ".venv"}


def link_ignored(repo, path):
    """Link the top-level folders and files that git ignores but that the live checkout has (for example the art/
    of mdview), so the worktree works like the live checkout. Hidden names and caches are not linked."""
    out = git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", check=False).stdout
    for name in {x.split("/")[0] for x in out.splitlines() if x}:
        if name.startswith(".") or name in SKIP_LINKS or name.endswith(".pyc"):
            continue
        src, dst = Path(repo) / name, Path(path) / name
        if src.exists() and not dst.exists() and not dst.is_symlink():
            dst.symlink_to(src)


def add_worktree(repo, path, branch=None):
    """A worktree of repo at path: on branch (made from main if it does not exist), or detached at main."""
    if branch is None:
        git(repo, "worktree", "add", "-q", "--detach", str(path), "main")
    else:
        has = git(repo, "rev-parse", "--verify", "--quiet", branch, check=False).returncode == 0
        # A proposal starts from the local main of the live checkout, so Accept is a fast-forward merge.
        git(repo, "worktree", "add", "-q", *([str(path), branch] if has else ["-b", branch, str(path), "main"]))
    link_ignored(repo, path)


def make_worktree(app, live, n):
    w, wt = ik.worktree_of(app, live, n)
    kwt = w / "viewkit"
    w.mkdir(parents=True, exist_ok=True)
    if not wt.exists():
        add_worktree(live, wt, f"improve/note-{n}")
    if kwt.is_symlink():  # a proposal of version 1 had a link to the live kit
        kwt.unlink()
    if not kwt.exists():
        add_worktree(KIT, kwt, kit_branch(app, n))
    return w, wt, kwt


def commits(repo_dir, rng):
    return int(git(repo_dir, "rev-list", "--count", rng) or 0)


def commit_left(d, msg):
    if git(d, "status", "--porcelain"):
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", msg)


def after_claude(app, live, n, m, w, wt, kwt, bases, text, change):
    """The steps after Claude: commit what is left, test, push, comment, preview. For a change, a failure keeps the
    proposal as it was before."""
    st = ik.read_status(app, n) or {}
    repo = ik.repo_of(live)
    line = (text.strip().splitlines() or [f"Changes for note #{n}"])[0][:72]
    commit_left(wt, f"{line}\n\nNote: #{n}")
    commit_left(kwt, f"{line}\n\nThe kit part of {app}#{n}.")
    new = commits(wt, f"{bases[0]}..HEAD") + commits(kwt, f"{bases[1]}..HEAD")
    kit_changed = commits(kwt, "main..HEAD") > 0
    back = (lambda msg, err: ik.write_status(app, n, state="preview", message=msg, error=err)) if change else None

    def undo():
        git(wt, "reset", "-q", "--hard", bases[0])
        git(kwt, "reset", "-q", "--hard", bases[1])
    if not new:
        if text.upper().startswith("QUESTION"):
            gh("issue", "edit", str(n), "-R", repo, "--add-label", "question")
            if change:
                return back("Claude asks a question (see below).", text[:2000])
            return ik.write_status(app, n, state="question", message="Claude asks a question.", error=text[:2000])
        if change:
            return back("Claude made no change.", text[:1500])
        return ik.write_status(app, n, state="failed", message="Claude made no change.", error=text[:1500])
    ik.write_status(app, n, state="testing", kit=kit_changed, summary=text[:2000],
                    message="The tests run" + (" (all the viewers: the change is in viewkit)." if kit_changed else "."))
    ok, out = run_tests(app, m, w, wt, kit_changed)
    if not ok:
        if change:
            undo()
            return back("The change failed the tests. The proposal is as before.", out)
        comment(repo, n, "**Automatic proposal: the tests failed.** The proposal did not go to the preview.\n\n```\n"
                + out[-1200:] + "\n```\n\nAnswer in the dashboard (Try again), or run `/improve " + app + "`.")
        gh("issue", "edit", str(n), "-R", repo, "--add-label", "question")
        return ik.write_status(app, n, state="failed", message="The tests failed.", error=out)
    branch = f"improve/note-{n}"
    pushed = git(wt, "push", "-q", "--force-with-lease", "-u", "origin", branch, check=False).returncode == 0
    if kit_changed:
        pushed = git(kwt, "push", "-q", "--force-with-lease", "-u", "origin", kit_branch(app, n),
                     check=False).returncode == 0 and pushed
    head = git(wt, "rev-parse", "--short", "HEAD")
    khead = git(kwt, "rev-parse", "--short", "HEAD") if kit_changed else None
    where = f"`{branch}` (commit `{head}`)" + (f" and viewkit `{kit_branch(app, n)}` (commit `{khead}`, all the "
                                                f"viewers)" if kit_changed else "")
    comment(repo, n, (f"**Proposal changed**, from the feedback in the preview: {where}.\n\n" if change else
                      f"**Automatic proposal** on {where}.\n\n")
            + text[:2000] + "\n\nThe tests passed" + (" in all the viewers" if kit_changed else "")
            + ". Decide in the preview tab: Accept, Change or Reject."
            + ("" if pushed else "\n\n(A branch is not on GitHub: the push failed.)"))
    gh("issue", "edit", str(n), "-R", repo, "--add-label", "proposed", "--remove-label", "question")
    link = st.get("url")
    first = not link or not sandbox_up(n)
    if first:
        doc = Path(st["doc"]) if st.get("doc") else None
        files = ik.start_sandbox(app, live, n, [doc] if doc and doc.is_file() else [], {"VIEWKIT_PREVIEW": f"{app}:{n}"})
        link = files[0]["url"]
        ik.write_status(app, n, files=files)
    st = ik.write_status(app, n, state="preview", message="The preview is ready.", url=link, summary=text[:2000],
                         title=st.get("title") or issue_title(repo, n), rev=int(st.get("rev") or 0) + 1, error=None,
                         commit=head, kit=kit_changed)
    if first and os.environ.get("VIEWKIT_NO_OPEN") != "1":
        webbrowser.open(link)
    return st


def implement(app, live, n):
    m = ik.manifest(live)
    with app_lock(app):
        ik.write_status(app, n, state="working", message="Claude implements the note.", error=None)
        git(live, "fetch", "-q", "origin", check=False)
        w, wt, kwt = make_worktree(app, live, n)
        bases = (git(wt, "rev-parse", "HEAD"), git(kwt, "rev-parse", "HEAD"))
        tools, deny = code_tools(wt, kwt)
        text = run_claude(m, f"/improve implement {app} {n}", wt, tools, deny, [w])
        after_claude(app, live, n, m, w, wt, kwt, bases, text, change=False)


def retest(app, live, n):
    """Try again after failed tests, without Claude: the tests and the next steps again on the commits that exist.
    If the proposal has no commit, this is an implement."""
    m = ik.manifest(live)
    with app_lock(app):
        w, wt, kwt = make_worktree(app, live, n)
        if commits(wt, "main..HEAD") + commits(kwt, "main..HEAD") == 0:
            retry = True
        else:
            retry = False
            ik.write_status(app, n, state="testing", message="The tests run again.", error=None)
            st = ik.read_status(app, n) or {}
            msg = git(wt, "log", "-1", "--format=%B").splitlines()
            msg = "\n".join(x for x in msg if not x.startswith(("Co-Authored-By:", "Note: #", "Signed-off-by:"))).strip()
            text = st.get("summary") or msg or f"The proposal for note #{n}."
            after_claude(app, live, n, m, w, wt, kwt, ("main", "main"), text, change=False)
    if retry:
        implement(app, live, n)


def change(app, live, n, feedback):
    m = ik.manifest(live)
    with app_lock(app):
        repo = ik.repo_of(live)
        comment(repo, n, "**Feedback from the preview:**\n\n" + feedback)
        ik.write_status(app, n, state="changing", message="Claude changes the proposal.",
                        feedback=int((ik.read_status(app, n) or {}).get("feedback") or 0) + 1)
        w, wt, kwt = make_worktree(app, live, n)
        bases = (git(wt, "rev-parse", "HEAD"), git(kwt, "rev-parse", "HEAD"))
        tools, deny = code_tools(wt, kwt)
        text = run_claude(m, f"/improve change {app} {n}", wt, tools, deny, [w])
        after_claude(app, live, n, m, w, wt, kwt, bases, text, change=True)


def cleanup(app, live, n):
    """Stop the sandbox, and remove the worktrees, the sandbox copies and the branches (here and on GitHub)."""
    w, wt = ik.worktree_of(app, live, n)
    kwt = w / "viewkit"
    try:
        ik.stop_sandbox(app, live, n)
    except Exception as e:  # noqa: BLE001 - the cleanup goes on
        log("stop sandbox:", e)
    for name in other_apps():
        if name != app and (w / name).exists():
            git(KIT.parent / name, "worktree", "remove", "--force", str(w / name), check=False)
    if kwt.is_symlink():
        kwt.unlink()
    for repo, path, branch in ((live, wt, f"improve/note-{n}"), (KIT, kwt, kit_branch(app, n))):
        git(repo, "worktree", "remove", "--force", str(path), check=False)
        git(repo, "worktree", "prune", check=False)
        if git(repo, "rev-parse", "--verify", "--quiet", branch, check=False).returncode == 0:
            git(repo, "branch", "-D", branch, check=False)
            git(repo, "push", "-q", "origin", "--delete", branch, check=False)
    shutil.rmtree(w, ignore_errors=True)


def ready_to_merge(repo, name):
    if git(repo, "branch", "--show-current") != "main":
        raise Fail(f"The live checkout of {name} is not on main. Switch it to main, then accept again.")
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise Fail(f"The live checkout of {name} has changes that are not committed. Commit them, then accept again.")


def accept(app, live, n):
    m = ik.manifest(live)
    with app_lock(app):
        repo = ik.repo_of(live)
        w, wt = ik.worktree_of(app, live, n)
        kwt = w / "viewkit"
        branch, kb = f"improve/note-{n}", kit_branch(app, n)
        kit_changed = kwt.exists() and not kwt.is_symlink() and commits(kwt, "main..HEAD") > 0
        try:
            ready_to_merge(live, app)
            if kit_changed:
                ready_to_merge(KIT, "viewkit")
            moved = False
            for repo_dir, d, b in ((live, wt, branch),) + (((KIT, kwt, kb),) if kit_changed else ()):
                if git(repo_dir, "merge-base", "--is-ancestor", "main", b, check=False).returncode != 0:
                    ik.write_status(app, n, message="main changed: the run rebases the proposal.")
                    if git(d, "rebase", "main", check=False).returncode != 0:
                        git(d, "rebase", "--abort", check=False)
                        raise Fail("main changed, and the proposal does not rebase on it. Use Change, or run /improve.")
                    moved = True
            if moved:
                ik.write_status(app, n, state="accepting", message="The tests run again, after the rebase.")
                ok, out = run_tests(app, m, w, wt, kit_changed)
                if not ok:
                    raise Fail("After the rebase on main, the tests fail:\n" + out[-800:])
            if kit_changed:  # the kit first: the app can need the new kit
                git(KIT, "merge", "-q", "--ff-only", kb)
            git(live, "merge", "-q", "--ff-only", branch)
        except Fail as e:
            ik.write_status(app, n, state="preview", message="Not merged.", error=str(e))
            return
        commit = git(live, "rev-parse", "--short", "HEAD")
        kcommit = git(KIT, "rev-parse", "--short", "HEAD") if kit_changed else None
        pushed = git(live, "push", "-q", "origin", "main", check=False).returncode == 0
        if kit_changed:
            pushed = git(KIT, "push", "-q", "origin", "main", check=False).returncode == 0 and pushed
        what = f"`{commit}`" + (f" and viewkit `{kcommit}` (all the viewers)" if kit_changed else "")
        comment(repo, n, f"Shipped in {what} (accepted in the preview)."
                + ("" if pushed else " A push to GitHub failed: run `git push` in the folder."))
        gh("issue", "close", str(n), "-R", repo, "--reason", "completed")
        gh("issue", "edit", str(n), "-R", repo, "--remove-label", "proposed")
        st = ik.write_status(app, n, state="shipped", commit=commit, error=None,
                             message=f"Shipped in {commit}" + (f" and viewkit {kcommit}." if kit_changed else ".")
                             + ("" if pushed else " A push failed."))
    time.sleep(float(os.environ.get("VIEWKIT_IMPROVE_WAIT", "4")))  # the preview page sees "shipped" and goes back
    cleanup(app, live, n)
    if st.get("feedback"):
        learn(app, live, n, "accepted after feedback")


def reject(app, live, n, reason=""):
    with app_lock(app):
        repo = ik.repo_of(live)
        comment(repo, n, "**Rejected in the preview.**" + (f"\n\nReason: {reason}" if reason else ""))
        gh("issue", "close", str(n), "-R", repo, "--reason", "not planned")
        gh("issue", "edit", str(n), "-R", repo, "--remove-label", "proposed", "--add-label", "rejected")
        ik.write_status(app, n, state="rejected", message="Rejected. The note is closed.", error=None)
    time.sleep(float(os.environ.get("VIEWKIT_IMPROVE_WAIT", "4")))
    cleanup(app, live, n)
    if reason:
        learn(app, live, n, "rejected")


def learn(app, live, n, decision):
    """Claude adds or changes a rule in the principles from the decision. The run commits and pushes the file."""
    m = ik.manifest(live)
    files = [KIT / "PRINCIPLES.md", Path(live) / "PRINCIPLES.md"]
    tools = ["Read", "Grep", "Glob", "Bash(gh issue view:*)"] + [f"{t}(/{f})" for f in files for t in ("Edit", "Write")]
    try:
        run_claude(m, f"/improve learn {app} {n} {ik.repo_of(live)} {decision}", KIT, tools, ["Bash(git:*)", "WebFetch"],
                   [live])
    except Fail as e:
        log("learn:", e)
        return
    for f in files:
        repo_dir = f.parent
        if not f.exists() or not git(repo_dir, "status", "--porcelain", "--", f.name):
            continue
        if git(repo_dir, "branch", "--show-current") != "main":
            log("learn: not on main, the change stays in", f)
            continue
        git(repo_dir, "add", "--", f.name)
        git(repo_dir, "commit", "-q", "-m", f"PRINCIPLES: from {app}#{n} ({decision})\n\nCo-Authored-By: Claude "
            f"<noreply@anthropic.com>", "--", f.name, check=False)
        git(repo_dir, "push", "-q", "origin", "main", check=False)
        log("learn: committed", f)


def main(argv):
    action, app, live, n = argv[0], argv[1], Path(argv[2]), int(argv[3])
    rest = argv[4] if len(argv) > 4 else ""
    log("start", action, app, n)
    try:
        if action == "implement":
            implement(app, live, n)
        elif action == "change":
            change(app, live, n, rest)
        elif action == "retest":
            retest(app, live, n)
        elif action == "accept":
            accept(app, live, n)
        elif action == "reject":
            reject(app, live, n, rest)
        else:
            raise Fail(f"Unknown action {action}.")
    except Exception as e:  # noqa: BLE001 - every failure goes into the status, for the page
        log(traceback.format_exc())
        st = ik.read_status(app, n) or {}
        keep = action == "change" and st.get("url")
        ik.write_status(app, n, state="preview" if keep else "failed", error=str(e)[-1500:],
                        message="The change failed." if keep else "The run failed.")
    log("end", action, app, n)


if __name__ == "__main__":
    main(sys.argv[1:])
