#!/usr/bin/env python3
"""Test of the tracker of the notes (improvekit.tracker) and of the local tracker (localgh.py). No network, no server.

A user who may push to the repository sends the note as a GitHub issue (the stand-in gh). A user who may not push
(a clone of the public repository) gets an issue of the local tracker, and gh makes no issue. When gh cannot answer
(no network), the note waits in the queue. The local tracker knows the gh commands of the loop.
Run: python3 tests/notes_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="notes-test-"))
os.environ.update(VIEWKIT_GH=str(KIT / "tests" / "fake_gh.py"), VIEWKIT_NOTES_QUEUE=str(tmp / "queue.jsonl"),
                  VIEWKIT_REPO="owner/app", FAKE_GH_LOG=str(tmp / "gh.log"), VIEWKIT_ISSUES_DIR=str(tmp / "issues"))
os.environ.pop("VIEWKIT_NOTES", None)
sys.path.insert(0, str(KIT))
import improvekit  # noqa: E402


def calls():
    log = tmp / "gh.log"
    return [json.loads(x)["argv"] for x in log.read_text().splitlines()] if log.exists() else []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        sys.exit(1)


def local(*a, stdin=None, cwd=None):
    r = subprocess.run([sys.executable, str(KIT / "localgh.py"), *a], capture_output=True, text=True, input=stdin,
                       cwd=cwd, env=os.environ)
    return r.stdout.strip()


# 1. The user may not push: the note is an issue of the local tracker, and gh makes no issue.
os.environ["FAKE_GH_NOPUSH"] = "1"
res = improvekit.add("app", str(KIT), "a wish", source="cli")
check(res.get("url") == "local://owner/app/issues/1", f"no push: the note is a local issue ({res})")
check(not any(a[:2] == ["issue", "create"] for a in calls()), "no push: gh makes no issue")
issue = json.loads((tmp / "issues" / "owner__app" / "1.json").read_text())
check(issue["title"] == "a wish" and issue["labels"] == [{"name": "note"}] and "Source: cli" in issue["body"],
      "no push: the local issue has the title, the label and the body")
check(improvekit.tracker_of_url(res["url"]) == "local", "the URL tells the tracker")

# 2. The user may push (a new process asks gh again): the note is a GitHub issue.
improvekit._trackers.clear()
os.environ.pop("FAKE_GH_NOPUSH")
res = improvekit.add("app", str(KIT), "another wish", source="cli")
check(res.get("url", "").startswith("https://github.com/owner/app/issues/"), "push: the note is a GitHub issue")
check(improvekit.tracker_of_url(res["url"]) == "github", "the URL tells the tracker")

# 3. gh cannot answer (no network): the note waits in the queue, and goes with the next note.
improvekit._trackers.clear()
os.environ["FAKE_GH_FAIL"] = "1"
res = improvekit.add("app", str(KIT), "a wish without network", source="cli")
check(res.get("queued") and len(improvekit._queued()) == 1, "no network: the note is queued")
os.environ.pop("FAKE_GH_FAIL")
res = improvekit.add("app", str(KIT), "a wish with network", source="cli")
check(res.get("url", "").startswith("https://") and improvekit._queued() == [], "the next note sends the queue first")

# 4. VIEWKIT_NOTES sets the tracker, and a checkout that is not on GitHub uses the local tracker.
improvekit._trackers.clear()
os.environ["VIEWKIT_NOTES"] = "local"
check(improvekit.tracker("owner/app") == "local", "VIEWKIT_NOTES=local sets the tracker")
os.environ.pop("VIEWKIT_NOTES")
check(improvekit.tracker("local/viewers") == "local", "a repository local/NAME uses the local tracker")

# 5. The commands of the local tracker that the loop and the improve skill use.
check(local("issue", "view", "1", "-R", "owner/app", "--json", "title", "-q", ".title") == "a wish", "view -q .title")
local("issue", "comment", "1", "-R", "owner/app", "--body-file", "-", stdin="**Answer from the dashboard:**\n\nblue")
local("issue", "edit", "1", "-R", "owner/app", "--add-label", "question")
local("issue", "edit", "1", "-R", "owner/app", "--add-label", "proposed", "--remove-label", "question")
v = json.loads(local("issue", "view", "1", "-R", "owner/app", "--json", "title,body,comments,labels"))
check(v["comments"][0]["body"].endswith("blue") and [x["name"] for x in v["labels"]] == ["note", "proposed"],
      "comment and edit (labels)")
os.environ["VIEWKIT_NOTES_REPO"] = "owner/app"  # as in a run of the loop: no -R
check("blue" in local("issue", "view", "1", "--comments"), "view without -R uses VIEWKIT_NOTES_REPO")
os.environ.pop("VIEWKIT_NOTES_REPO")
listed = json.loads(local("issue", "list", "-R", "owner/app", "--label", "note", "--state", "open", "--json",
                          "number,title"))
check([x["number"] for x in listed] == [1], "list --label note --state open")
local("issue", "close", "1", "-R", "owner/app", "--reason", "not planned")
v = json.loads(local("issue", "view", "1", "-R", "owner/app", "--json", "state,stateReason"))
check(v == {"state": "CLOSED", "stateReason": "NOT_PLANNED"}, "close --reason")
check(local("issue", "list", "-R", "owner/app", "--json", "number") == "[]", "a closed issue is not in the open list")
check(local("label", "create", "note", "-R", "owner/app", "--force") == "", "label create has no effect")
gh = subprocess.run([str(KIT / "bin" / "gh"), "issue", "view", "1", "-R", "owner/app", "--json", "number", "-q",
                     ".number"], capture_output=True, text=True, env=os.environ)
check(gh.stdout.strip() == "1", "bin/gh is the local tracker")
print("all passed")
