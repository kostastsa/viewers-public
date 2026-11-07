#!/usr/bin/env python3
"""A stand-in for the `claude` command that prints the same stream-json events. Used by the tests of mdview, pyview and
pdfview. The prompt can contain: SLOW (the answer takes a few seconds), EDIT (it appends a line to the file, or for a PDF
it adds a comment with `pdfview add`), FAIL (it exits with an error).
The file is found in the system prompt of the app: pyview says "the file PATH (a .py file)", mdview and pdfview say
"the file PATH.".
A run of the self-improving loop has the prompt '/improve MODE APP N': for implement and change it adds a line to
README.md in its folder and commits it (with FAKE_IMPROVE_KIT=1 it also changes ../viewkit/README.md, not committed);
for learn it does nothing. With FAKE_IMPROVE_GH_FILE, it writes the title from `gh issue view N` to that file. With
FAKE_IMPROVE_QUESTION_FILE, the first implement asks a question and makes the file.
An Oracle run has no system prompt and its prompt is '/SKILL "PATH"'. It waits one second, prints a report, and for a
PDF it replies to each open thread with `pdfview reply`. If the path contains FAIL, it exits with an error."""
import json
import os
import re
import subprocess
import sys
import time

args = sys.argv[1:]
if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
        f.write(json.dumps(args) + "\n")
prompt = args[args.index("-p") + 1]
if prompt.startswith("/improve "):  # a run of the self-improving loop (improverun.py), in the worktree of a note
    words = prompt.split()
    mode, n = words[1], (words[3] if len(words) > 3 else "?")
    text = "Nothing to learn."
    flag = os.environ.get("FAKE_IMPROVE_QUESTION_FILE")
    if mode == "implement" and flag and not os.path.exists(flag):  # the first run asks a question and changes nothing
        open(flag, "w").close()
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                          "result": "QUESTION: Which color?\n\n1. **Red**\n2. **Blue**"}), flush=True)
        sys.exit(0)
    if mode in ("implement", "change"):
        time.sleep(float(os.environ.get("FAKE_IMPROVE_SLEEP", "0.5")))
        if os.environ.get("FAKE_IMPROVE_GH_FILE"):  # the `gh` of the run (the local tracker in the repository viewers)
            r = subprocess.run(["gh", "issue", "view", n, "--json", "title", "-q", ".title"], capture_output=True, text=True)
            with open(os.environ["FAKE_IMPROVE_GH_FILE"], "a") as f:
                f.write(r.stdout.strip() + "\n")
        if os.environ.get("FAKE_IMPROVE_KIT") == "1" and os.path.isdir("../viewkit"):  # the kit part, not committed
            with open("../viewkit/README.md", "a") as f:
                f.write(f"\nA kit {mode} by the fake Claude for note #{n}.\n")
        with open("README.md", "a") as f:
            f.write(f"\nA {mode} by the fake Claude for note #{n}.\n")
        subprocess.run(["git", "add", "README.md"], capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", f"Fake {mode} for note #{n}\n\nNote: #{n}"], capture_output=True)
        text = f"The fake Claude made a {mode} in the README for note #{n}."
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                      "total_cost_usd": 0.01, "duration_ms": 10}), flush=True)
    sys.exit(0)
if "--append-system-prompt" not in args:  # an Oracle run: /answer-comments "PATH", /answer-pdf "PATH", ...
    path = re.match(r'/[\w-]+ "?([^"]+)"?', prompt).group(1)
    time.sleep(1)
    if "FAIL" in path:
        print("boom: the Oracle failed")
        sys.exit(2)
    if path.endswith(".pdf"):
        pdfview = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "pdfview", "pdfview.py")
        threads = json.loads(subprocess.run([sys.executable, pdfview, "list", path, "--json"], capture_output=True,
                                            text=True).stdout or "[]")
        for t in threads:
            if t["status"] == "open":
                subprocess.run([sys.executable, pdfview, "reply", path, str(t["id"]), "-"], input="Answer from the fake Oracle, $x^2$.",
                               capture_output=True, text=True)
    print("Report: the fake Oracle answered the open comments.")
    sys.exit(0)
system = args[args.index("--append-system-prompt") + 1]
sid = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
path = re.search(r"the file (\S+?)(?: \(a \.py file\)|\.\s)", system).group(1)


def out(o):
    print(json.dumps(o), flush=True)


def say(words, slow=False):
    out({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}})
    for w in words:
        time.sleep(1.0 if slow else 0.02)
        out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": w}}})
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "".join(words)}]}})
    out({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}})


out({"type": "system", "subtype": "init", "session_id": sid})
if "FAIL" in prompt:
    sys.stderr.write("boom: not logged in\n")
    sys.exit(1)
say(["Hello ", "from ", "fake ", "Claude. "], slow="SLOW" in prompt)
if "CODE" in prompt:  # an answer with a code block, for the Copy button
    say(["Here:\n\n```\n", "print(1)\n", "```\n"])
if "EDIT" in prompt and "Ask only" not in prompt and path.endswith(".pdf"):  # the PDF is never edited: add a comment
    pdfview = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "pdfview", "pdfview.py")
    cmd = ["pdfview", "add", path, "Added by fake Claude"]
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": " ".join(cmd)}}]}})
    subprocess.run([sys.executable, pdfview, *cmd[1:]], capture_output=True)
    say(["Done."])
elif "EDIT" in prompt and "Ask only" not in prompt:
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": path}}]}})
    with open(path, "a") as f:
        f.write("\n# edited by fake claude\n" if path.endswith(".py") else "\nedited by fake claude\n")
    say(["Done."])
out({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01, "duration_ms": 123, "session_id": sid})
