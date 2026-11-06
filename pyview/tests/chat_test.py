"""Test of the chat on the server side, with a stand-in `claude` (viewkit/tests/fake_claude.py). Standard library only.
Run: python3 tests/chat_test.py"""
import http.client
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYVIEW = str(HERE.parent / "pyview.py")
PORT = "8807"
# Under tests/tmp (git ignores it): inside the home folder, which is the root the server may open.
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="chat-", dir=TMP_ROOT))
bindir, log = tmp / "bin", tmp / "claude.log"
bindir.mkdir()
(bindir / "claude").write_text('#!/bin/sh\nexec python3 "%s" "$@"\n' % (HERE.parent.parent / "viewkit" / "tests" / "fake_claude.py"))
(bindir / "claude").chmod((bindir / "claude").stat().st_mode | stat.S_IEXEC)
nb, other = tmp / "nb.py", tmp / "other.py"
ORIGINAL = "# %% a\nx = 1\n\n# %% b\nprint(x)\n"
nb.write_text(ORIGINAL)
other.write_text(ORIGINAL)
env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", PYVIEW_PORT=PORT, FAKE_CLAUDE_LOG=str(log))
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def cli(*args):
    return subprocess.run(["python3", PYVIEW, *args], env=env, capture_output=True, text=True)


def start(path):
    link = cli(str(path), "--no-open").stdout.strip()
    return link.split("&t=")[-1]


def call(token, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", int(PORT), timeout=10)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Cookie": f"pyview{PORT}={token}", "X-PyView": "1", "Content-Type": "application/json"})
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def wait_idle(token, rel, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = call(token, "GET", f"/api/chat?f={rel}")[1]
        if not d["busy"]:
            return d
        time.sleep(0.1)
    return call(token, "GET", f"/api/chat?f={rel}")[1]


def calls():
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def opt(args, name):
    return args[args.index(name) + 1]


try:
    token = start(nb)
    rel = str(nb.relative_to(Path.home()))
    orel = str(other.relative_to(Path.home()))
    cli(str(tmp), "--no-open")  # the folder: files in it can be opened, but are not trusted to run code
    url = f"/api/chat?f={rel}"

    # events: collect what the page would receive
    events = []

    def listen():
        c = http.client.HTTPConnection("127.0.0.1", int(PORT), timeout=60)
        c.request("GET", f"/api/events?f={rel}", headers={"Cookie": f"pyview{PORT}={token}"})
        r = c.getresponse()
        while True:
            line = r.fp.readline()
            if not line:
                return
            if line.startswith(b"data: "):
                events.append(json.loads(line[6:]))

    threading.Thread(target=listen, daemon=True).start()
    time.sleep(0.5)

    check("a file that is not trusted cannot chat", call(token, "POST", "/api/chat", {"f": orel, "text": "hi"})[0] == 403)
    check("an empty message is refused", call(token, "POST", "/api/chat", {"f": rel, "text": "  "})[0] == 400)

    # a first message
    check("a message starts a turn", call(token, "POST", "/api/chat", {"f": rel, "text": "hi", "cell": 2})[0] == 200)
    d = wait_idle(token, rel)
    m = d["messages"]
    check("the conversation holds the question and the answer", [x["role"] for x in m] == ["user", "assistant"], str(m))
    check("the answer is assembled from the chunks", m[-1]["parts"] and m[-1]["parts"][0]["text"] == "Hello from fake Claude. ")
    check("the answer has its cost and is not streaming", m[-1].get("cost") == 0.01 and not m[-1]["streaming"])
    a = calls()[0]
    check("the first turn starts a session", opt(a, "--session-id") == d["session"] and "--resume" not in a)
    check("the turn tells Claude which cell is selected", 'cell 2' in opt(a, "-p"))
    check("Claude may edit and run, in the folder of the notebook", "Edit" in opt(a, "--allowedTools") and "Bash(pyview:*)" in opt(a, "--allowedTools"))

    # events
    kinds = [e["k"] for e in events if e.get("t") == "chat"]
    cs = [e["cseq"] for e in events if e.get("t") == "chat"]
    check("the page gets start, delta and done events in order", kinds[0] == "start" and "delta" in kinds and kinds[-1] == "done", str(kinds))
    check("and they are numbered in order", cs == sorted(cs) and len(set(cs)) == len(cs))

    # the second message continues the session
    call(token, "POST", "/api/chat", {"f": rel, "text": "again"})
    d2 = wait_idle(token, rel)
    a2 = calls()[1]
    check("the second turn resumes the same session", opt(a2, "--resume") == d["session"] and "--session-id" not in a2)
    check("four messages are kept", len(d2["messages"]) == 4)

    # an edit, and one Undo
    call(token, "POST", "/api/chat", {"f": rel, "text": "please EDIT"})
    d3 = wait_idle(token, rel)
    tools = [p for p in d3["messages"][-1]["parts"] if p["type"] == "tool"]
    check("a tool step shows in the answer", tools and tools[0]["name"] == "Edit" and tools[0]["summary"] == "nb.py", str(d3["messages"][-1]))
    check("Claude changed the file", "# edited by fake claude" in nb.read_text())
    done = [e for e in events if e.get("t") == "chat" and e["k"] == "done"][-1]
    check("the done event says that the file changed", done["changed"] is True)
    doc = call(token, "GET", f"/api/doc?f={rel}")[1]
    check("the turn can be undone", doc["canUndo"] is True)
    call(token, "POST", "/api/op", {"f": rel, "type": "undo"})
    check("one Undo takes back the whole turn", nb.read_text() == ORIGINAL, nb.read_text())

    # Ask only
    call(token, "POST", "/api/chat", {"f": rel, "text": "please EDIT", "ask_only": True})
    wait_idle(token, rel)
    a = calls()[-1]
    check("Ask only: no edit tool, and the message says so", "Edit" not in opt(a, "--allowedTools") and "Mode: Ask only" in opt(a, "-p"))
    call(token, "POST", "/api/chat", {"f": rel, "text": "now go on", "ask_only": False})
    wait_idle(token, rel)
    a = calls()[-1]
    check("switching Ask only off in the middle of a chat tells Claude in the new message",
          "Edit" in opt(a, "--allowedTools") and "Mode: you may edit" in opt(a, "-p") and "Ask only" not in opt(a, "--append-system-prompt"))
    check("Ask only: the file stays as it was", nb.read_text() == ORIGINAL)

    # busy, then stop
    call(token, "POST", "/api/chat", {"f": rel, "text": "SLOW answer"})
    time.sleep(0.5)
    st, body = call(token, "POST", "/api/chat", {"f": rel, "text": "second"})
    check("a second message while Claude answers is refused", st == 409 and "still answering" in body["error"], str(body))
    check("a new chat is refused while Claude answers", call(token, "POST", "/api/chat", {"f": rel, "action": "new"})[0] == 409)
    snap = call(token, "GET", url)[1]
    check("the answer so far is available while it is written", snap["busy"] and snap["messages"][-1]["streaming"] and snap["messages"][-1]["parts"])
    call(token, "POST", "/api/chat", {"f": rel, "action": "stop"})
    d4 = wait_idle(token, rel, timeout=6)
    check("Stop ends the turn and marks the answer", not d4["busy"] and d4["messages"][-1].get("stopped") is True, str(d4["messages"][-1]))

    # an error
    call(token, "POST", "/api/chat", {"f": rel, "text": "FAIL please"})
    d5 = wait_idle(token, rel)
    check("an error of claude shows in the answer", "boom" in d5["messages"][-1].get("error", ""), str(d5["messages"][-1]))

    # saved, and kept over a restart of the server
    check("the conversation is saved next to the notebook", (tmp / ".pyview" / "nb.py.chat.json").exists())
    n_before, sid_before = len(d5["messages"]), d5["session"]
    cli("--stop")
    token = start(nb)
    d6 = call(token, "GET", url)[1]
    check("the conversation is still there after the server restarts", len(d6["messages"]) == n_before and d6["session"] == sid_before)

    # new chat
    check("New chat is accepted", call(token, "POST", "/api/chat", {"f": rel, "action": "new"})[0] == 200)
    d7 = call(token, "GET", url)[1]
    check("it empties the conversation and starts a new session", d7["messages"] == [] and d7["session"] != sid_before)
    call(token, "POST", "/api/chat", {"f": rel, "text": "hi"})
    wait_idle(token, rel)
    a = calls()[-1]
    check("and the next turn starts that session (no resume)", opt(a, "--session-id") == d7["session"] and "--resume" not in a)
finally:
    cli("--stop")
    if os.environ.get("PYVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
