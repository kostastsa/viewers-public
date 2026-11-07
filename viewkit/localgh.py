#!/usr/bin/env python3
"""The local tracker of the notes: a stand-in for the part of `gh` that the self-improving apps use, with the issues in
JSON files on this computer. The loop uses it when the user may not push to the GitHub repository of the viewers (a
clone of the public repository), or has no `gh` (improvekit.tracker). In the runs of the loop, viewkit/bin/gh calls it,
so Claude can use `gh issue view` as with GitHub.

    localgh.py issue create -R REPO --title T [--label L] --body-file -     prints the URL local://REPO/issues/N
    localgh.py issue view N [-R REPO] [--json f,g] [-q .field] [--comments]
    localgh.py issue comment N [-R REPO] --body-file - | --body TEXT
    localgh.py issue edit N [-R REPO] [--add-label L] [--remove-label L]
    localgh.py issue close N [-R REPO] [--reason R] [--comment TEXT]
    localgh.py issue reopen N [-R REPO]
    localgh.py issue list [-R REPO] [--label L] [--state open|closed|all] [--json f,g] [-q .field]
    localgh.py label create NAME ...                                       (no effect: labels need no setup)
    localgh.py mode [DIR]                                                  github or local, for the repository of DIR

Without -R, the repository is VIEWKIT_NOTES_REPO, or the repository of the current folder (improvekit.notes_repo).
The issues of REPO are in ISSUES_DIR/OWNER__NAME/N.json (VIEWKIT_ISSUES_DIR, default ~/.local/share/viewkit/issues).
"""
import argparse
import datetime
import fcntl
import getpass
import json
import os
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
ISSUES_DIR = Path(os.environ.get("VIEWKIT_ISSUES_DIR") or Path.home() / ".local" / "share" / "viewkit" / "issues")
FIELDS = ("number", "title", "body", "state", "stateReason", "labels", "comments", "author", "createdAt", "updatedAt",
          "closedAt", "url")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_repo():
    if os.environ.get("VIEWKIT_NOTES_REPO"):
        return os.environ["VIEWKIT_NOTES_REPO"]
    sys.path.insert(0, str(KIT))
    import improvekit
    return improvekit.notes_repo(os.getcwd())


def folder(repo):
    d = ISSUES_DIR / repo.replace("/", "__")
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def path_of(repo, n):
    return folder(repo) / f"{int(n)}.json"


def load(repo, n):
    p = path_of(repo, n)
    if not p.is_file():
        sys.exit(f"localgh: there is no issue #{n} in {repo}")
    return json.loads(p.read_text(encoding="utf-8"))


def save(repo, issue):
    issue["updatedAt"] = now()
    p = path_of(repo, issue["number"])
    tmp = p.with_suffix(f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(issue, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def body_of(a):
    if getattr(a, "body_file", None):
        return sys.stdin.read() if a.body_file == "-" else Path(a.body_file).read_text(encoding="utf-8")
    return getattr(a, "body", None) or ""


def jq(value, expr):
    """The small part of jq that the loop uses: `.field`, `.a.b`, and `.[] | .field` on a list."""
    expr = (expr or "").strip()
    if expr.startswith(".[]"):
        rest = expr[3:].lstrip(" |").strip() or "."
        return [jq(v, rest) for v in value]
    for key in [k for k in expr.split(".") if k]:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def show(value, a):
    if a.jq:
        value = jq(value, a.jq)
        for v in (value if isinstance(value, list) and a.jq.startswith(".[]") else [value]):
            print(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=1))


def pick(issue, fields):
    return {f: issue.get(f) for f in fields.split(",") if f in FIELDS}


def text_of(issue):
    labels = ", ".join(x["name"] for x in issue.get("labels", []))
    out = [f"title:\t{issue['title']}", f"state:\t{issue['state']}", f"labels:\t{labels}", f"number:\t{issue['number']}",
           "--", issue.get("body") or ""]
    for c in issue.get("comments", []):
        out += ["", f"--- {c['author']['login']} ({c['createdAt']})", c["body"]]
    return "\n".join(out)


def cmd_issue(argv):
    ap = argparse.ArgumentParser(prog="localgh issue")
    ap.add_argument("action", choices=["create", "view", "comment", "edit", "close", "reopen", "list"])
    ap.add_argument("n", nargs="?", type=int)
    ap.add_argument("-R", "--repo")
    ap.add_argument("--title", "-t")
    ap.add_argument("--body", "-b")
    ap.add_argument("--body-file", "-F")
    ap.add_argument("--label", "-l", action="append", default=[])
    ap.add_argument("--add-label", action="append", default=[])
    ap.add_argument("--remove-label", action="append", default=[])
    ap.add_argument("--reason", "-r")
    ap.add_argument("--comment", "-c")
    ap.add_argument("--state", "-s", default="open")
    ap.add_argument("--limit", "-L", type=int, default=100)
    ap.add_argument("--json")
    ap.add_argument("-q", "--jq")
    ap.add_argument("--comments", action="store_true")
    a = ap.parse_args(argv)
    repo = a.repo or default_repo()
    user = getpass.getuser()

    if a.action == "create":
        if not a.title:
            sys.exit("localgh: --title is necessary")
        d = folder(repo)
        with open(d / ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            n = max([int(p.stem) for p in d.glob("*.json") if p.stem.isdigit()] + [0]) + 1
            labels = [x for ls in a.label for x in ls.split(",") if x]
            issue = {"number": n, "title": a.title, "body": body_of(a), "state": "OPEN", "stateReason": None,
                     "labels": [{"name": x} for x in labels], "comments": [], "author": {"login": user},
                     "createdAt": now(), "closedAt": None, "url": f"local://{repo}/issues/{n}"}
            save(repo, issue)
        print(issue["url"])
        return
    if a.action == "list":
        want = {"open": ["OPEN"], "closed": ["CLOSED"], "all": ["OPEN", "CLOSED"]}.get(a.state.lower(), ["OPEN"])
        items = []
        for p in sorted(folder(repo).glob("*.json"), key=lambda p: -int(p.stem) if p.stem.isdigit() else 0):
            if not p.stem.isdigit():
                continue
            issue = json.loads(p.read_text(encoding="utf-8"))
            names = {x["name"] for x in issue.get("labels", [])}
            if issue["state"] in want and all(x in names for x in a.label):
                items.append(issue)
        items = items[:a.limit]
        if a.json:
            show([pick(i, a.json) for i in items], a)
        else:
            for i in items:
                print(f"{i['number']}\t{i['state']}\t{i['title']}\t{','.join(x['name'] for x in i['labels'])}")
        return
    if a.n is None:
        sys.exit(f"localgh: issue {a.action} needs the number of the issue")
    issue = load(repo, a.n)
    if a.action == "view":
        if a.json:
            show(pick(issue, a.json), a)
        else:
            print(text_of(issue))
        return
    if a.action == "comment":
        text = body_of(a)
        if not text.strip():
            sys.exit("localgh: the comment is empty")
        issue["comments"].append({"author": {"login": user}, "body": text, "createdAt": now()})
    elif a.action == "edit":
        names = [x["name"] for x in issue["labels"]]
        for x in [y for ls in a.add_label for y in ls.split(",") if y]:
            if x not in names:
                names.append(x)
        for x in [y for ls in a.remove_label for y in ls.split(",") if y]:
            names = [y for y in names if y != x]
        issue["labels"] = [{"name": x} for x in names]
        if a.title:
            issue["title"] = a.title
        if a.body is not None or a.body_file:
            issue["body"] = body_of(a)
    elif a.action == "close":
        if a.comment:
            issue["comments"].append({"author": {"login": user}, "body": a.comment, "createdAt": now()})
        reason = (a.reason or "completed").upper().replace(" ", "_")
        issue.update(state="CLOSED", stateReason=reason, closedAt=now())
    elif a.action == "reopen":
        issue.update(state="OPEN", stateReason="REOPENED", closedAt=None)
    save(repo, issue)
    print(issue["url"])


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    if argv[0] == "issue":
        return cmd_issue(argv[1:])
    if argv[0] == "label":
        return  # labels need no setup in the local tracker
    if argv[0] == "mode":
        sys.path.insert(0, str(KIT))
        import improvekit
        print(improvekit.tracker(improvekit.notes_repo(argv[1] if len(argv) > 1 else os.getcwd())))
        return
    if argv[0] == "repo" and argv[1:2] == ["view"]:
        print(default_repo())
        return
    sys.exit(f"localgh: the local tracker has no command '{' '.join(argv[:2])}'. It knows: issue, label, mode.")


if __name__ == "__main__":
    main(sys.argv[1:])
