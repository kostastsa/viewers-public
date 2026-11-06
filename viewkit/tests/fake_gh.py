#!/usr/bin/env python3
"""A stand-in for `gh` in the tests of the notes (improvekit.py). It writes each call to FAKE_GH_LOG as one JSON line:
{"argv": [...], "stdin": "..."}, for the calls that work. `gh issue create` prints a made-up issue URL. With FAKE_GH_FAIL=1 it fails like gh
without a network."""
import json
import os
import sys

argv = sys.argv[1:]
# The push check of improvekit (`gh api repos/OWNER/NAME --jq .permissions.push`). It is not in the log, so the issue
# numbers do not change. With FAKE_GH_NOPUSH=1 the user may not push.
if argv[:1] == ["api"] and len(argv) > 1 and argv[1].startswith("repos/") and os.environ.get("FAKE_GH_FAIL") != "1":
    print("false" if os.environ.get("FAKE_GH_NOPUSH") == "1" else "true")
    sys.exit(0)
stdin = sys.stdin.read() if "-" in argv else ""
log = os.environ.get("FAKE_GH_LOG")
if log and os.environ.get("FAKE_GH_FAIL") != "1":  # only the calls that work
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")
if os.environ.get("FAKE_GH_FAIL") == "1":
    sys.exit("error connecting to api.github.com")
if argv[:2] == ["issue", "create"]:
    repo = argv[argv.index("-R") + 1] if "-R" in argv else "owner/repo"
    n = sum(1 for _ in open(log, encoding="utf-8")) if log else 1
    print(f"https://github.com/{repo}/issues/{n}")
