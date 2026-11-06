#!/usr/bin/env python3
"""Test of the push check of the notes (improvekit._check_push), with the stand-in gh. No network, no server.

A user who may push to the repository sends the note as an issue. A user who may not push (a clone of the public
repository of someone else) keeps the note in the local queue, and gh makes no issue.
Run: python3 tests/notes_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="notes-test-"))
os.environ.update(VIEWKIT_GH=str(KIT / "tests" / "fake_gh.py"), VIEWKIT_NOTES_QUEUE=str(tmp / "queue.jsonl"),
                  VIEWKIT_REPO="owner/app", FAKE_GH_LOG=str(tmp / "gh.log"))
sys.path.insert(0, str(KIT))
import improvekit  # noqa: E402


def calls():
    log = tmp / "gh.log"
    return [json.loads(x)["argv"] for x in log.read_text().splitlines()] if log.exists() else []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        sys.exit(1)


# 1. The user may not push: the note waits in the queue, and gh makes no issue.
os.environ["FAKE_GH_NOPUSH"] = "1"
res = improvekit.add("app", str(KIT), "a wish", source="cli")
check(res.get("queued") and "cannot push" in res["why"], "no push: the note is queued")
check(not any(a[:2] == ["issue", "create"] for a in calls()), "no push: no issue")
check(len(improvekit._queued()) == 1, "no push: one note in the queue")

# 2. The user may push (a new process asks gh again): the queue goes first, then the new note.
improvekit._push.clear()
os.environ.pop("FAKE_GH_NOPUSH")
res = improvekit.add("app", str(KIT), "another wish", source="cli")
check(res.get("url", "").startswith("https://github.com/owner/app/issues/"), "push: the note is an issue")
check(sum(a[:2] == ["issue", "create"] for a in calls()) == 2, "push: the queued note and the new note are issues")
check(improvekit._queued() == [], "push: the queue is empty")
print("all passed")
