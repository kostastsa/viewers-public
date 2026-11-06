"""Run `claude -p` for the viewers (mdview, pyview, pdfview): the chat, and the Oracle.

The kit does the parts that do not depend on the app: the environment of the run, the command line, the process, and the
lines of `--output-format stream-json`. The app keeps its own system prompt, tool lists, saved conversation and way to
tell the page about news (mdview counts a revision, pyview sends events). `Oracle` is the background run that answers
the comments of a document with a skill.
"""
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

# Variables of the Claude Code session that may have started the server. A run is a fresh session.
SESSION_VARS = {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
                "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_EXECPATH",
                "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SSE_PORT",
                # macOS: a server that a background job started can have XPC_FLAGS=0x2. With it, `claude` cannot read
                # the login in the keychain and says "Not logged in".
                "XPC_FLAGS"}


def clean_env(drop_prefix=None):
    """The environment of a run: ours, without the variables of the session that started the server.
    With `drop_prefix`, also without every variable that starts with it."""
    return {k: v for k, v in os.environ.items()
            if k not in SESSION_VARS and not (drop_prefix and k.startswith(drop_prefix))}


def tool_summary(block):
    """A short line for a tool_use block, for the chat panel."""
    i, name = block.get("input") or {}, block.get("name", "")
    if name == "Bash":
        return str(i.get("command", ""))[:140]
    if name in ("Read", "Edit", "Write"):
        return os.path.basename(str(i.get("file_path", "")))
    if name in ("Grep", "Glob"):
        return str(i.get("pattern", ""))[:80]
    if name == "WebSearch":
        return str(i.get("query", ""))[:100]
    if name == "WebFetch":
        return str(i.get("url", ""))[:100]
    return ""


def build_command(exe, prompt, system, session, started, allowed, disallowed=None, permission_mode=None):
    """The command line of one chat turn. `started` says that the session has had a turn, so it is resumed."""
    cmd = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--append-system-prompt", system, "--resume" if started else "--session-id", session]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    cmd += ["--allowedTools", ",".join(allowed)]
    if disallowed:
        cmd += ["--disallowedTools", ",".join(disallowed)]
    return cmd


def apply_event(cur, ev):
    """Put one line of the stream into `cur`, the answer that is being written ({"parts": [...], ...}).
    Returns (started, news). `started` is True for the first line: the session now exists. `news` lists what changed, as
    {"k": "delta", "i", "text"} and {"k": "tool", "i", "part"}, for an app that sends events to the page."""
    t, news = ev.get("type"), []
    if t == "system" and ev.get("subtype") == "init":
        return True, news
    if t == "stream_event":
        e = ev.get("event") or {}
        et = e.get("type")
        if et == "content_block_start" and (e.get("content_block") or {}).get("type") == "text":
            cur["parts"].append({"type": "text", "text": ""})
        elif et == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
            if not cur["parts"] or cur["parts"][-1]["type"] != "text":
                cur["parts"].append({"type": "text", "text": ""})
            text = e["delta"].get("text", "")
            cur["parts"][-1]["text"] += text
            news.append({"k": "delta", "i": len(cur["parts"]) - 1, "text": text})
    elif t == "assistant":
        for b in (ev.get("message") or {}).get("content") or []:
            if b.get("type") == "tool_use":
                part = {"type": "tool", "name": b.get("name", ""), "summary": tool_summary(b)}
                cur["parts"].append(part)
                news.append({"k": "tool", "i": len(cur["parts"]) - 1, "part": part})
    elif t == "result":
        cur["cost"], cur["ms"] = ev.get("total_cost_usd"), ev.get("duration_ms")
        if ev.get("is_error"):
            cur["error"] = str(ev.get("result") or "Claude reported an error.")
    return False, news


def run_stream(cmd, cwd, env, on_event, on_proc=None):
    """Run the command, call `on_event(dict)` for each line it prints, and wait for it to end.
    `on_proc(proc)` is called when the process exists, so the app can stop it. Returns (error, stderr, returncode):
    `error` is a message if the process could not start, else None."""
    stderr, proc = [], None
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                start_new_session=True)
        if on_proc:
            on_proc(proc)
        threading.Thread(target=lambda: stderr.append(proc.stderr.read()), daemon=True).start()
        for line in proc.stdout:
            try:
                on_event(json.loads(line))
            except ValueError:
                continue
        proc.wait()
    except OSError as e:
        return f"Could not start claude: {e}", "", None
    return None, "".join(stderr), proc.returncode


class Oracle:
    """The Oracle: one background `claude -p "/SKILL FILE"` run per document, which answers the open comments.

    The run holds a lock file until it exits, so a second click, another tab or a server restart cannot start a second
    run. It continues one Claude session per document, so Claude remembers earlier rounds. When it ends, the exit code
    and the end of its log are the report. The files are in `state_dir`, named by a hash of the document path.
      exe(): the path of `claude`. It raises RuntimeError with a message for the user if it is not found.
      prompt(p): the prompt of a run, e.g. '/answer-comments "PATH"'.
      tools, disallowed: the --allowedTools and --disallowedTools lists. permission_mode: e.g. "acceptEdits", or None.
    """

    def __init__(self, state_dir, exe, prompt, tools, disallowed=None, permission_mode=None, drop_prefix=None):
        self.dir = state_dir
        self.exe, self.prompt, self.tools, self.disallowed = exe, prompt, tools, disallowed
        self.permission_mode, self.drop_prefix = permission_mode, drop_prefix

    def files(self, p):
        key = hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:16]
        return {k: self.dir / f"{key}.{k}" for k in ("lock", "log", "exit", "json", "session")}

    def session(self, p):
        """The id of the document's Claude session, if it still exists."""
        try:
            sid = str(uuid.UUID(self.files(p)["session"].read_text().strip()))
        except (OSError, ValueError):
            return None
        return sid if any((Path.home() / ".claude" / "projects").glob(f"*/{sid}.jsonl")) else None

    def busy(self, p):
        """True while a run holds the lock."""
        try:
            fd = os.open(self.files(p)["lock"], os.O_RDONLY)
        except FileNotFoundError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(fd)  # also releases the lock if we just took it

    def status(self, p):
        f = self.files(p)
        try:
            meta = json.loads(f["json"].read_text())
        except (OSError, ValueError):
            return {"running": False, "session": bool(self.session(p))}
        out = {"running": self.busy(p), "started": meta.get("started"), "session": bool(self.session(p))}
        if not out["running"]:
            try:
                out["exit"] = int(f["exit"].read_text())
            except (OSError, ValueError):
                out["exit"] = None  # stopped before it finished
            try:
                out["report"] = f["log"].read_text(encoding="utf-8", errors="replace")[-6000:].strip()
            except OSError:
                out["report"] = ""
        return out

    def start(self, p):
        """Start a run for document p, and continue its session if there is one. False if a run is already going."""
        exe = self.exe()
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        f = self.files(p)
        fd = os.open(f["lock"], os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            f["exit"].unlink(missing_ok=True)
            env = clean_env(self.drop_prefix)
            env["ORACLE_EXIT"] = str(f["exit"])
            sid = self.session(p)
            if sid:
                session = ["--resume", sid]
            else:
                sid = str(uuid.uuid4())
                session = ["--session-id", sid]
                _write_private(f["session"], sid)
            cmd = [exe, "-p", self.prompt(p), *session]
            if self.permission_mode:
                cmd += ["--permission-mode", self.permission_mode]
            cmd += ["--allowedTools", *self.tools]
            if self.disallowed:
                cmd += ["--disallowedTools", *self.disallowed]
            # sh starts the run in the background and exits, so the run is never a zombie of the server.
            # The run inherits the locked fd and keeps it until it exits.
            with open(f["log"], "wb") as log:
                sh = subprocess.Popen(["/bin/sh", "-c", '("$@"; echo "$?" > "$ORACLE_EXIT") </dev/null &', "sh", *cmd],
                                      cwd=str(p.parent), env=env, stdin=subprocess.DEVNULL, stdout=log,
                                      stderr=subprocess.STDOUT, pass_fds=(fd,), start_new_session=True)
            sh.wait()
            _write_private(f["json"], json.dumps({"file": str(p), "pgid": sh.pid,
                                                  "started": time.strftime("%Y-%m-%d %H:%M:%S")}))
            return True
        finally:
            os.close(fd)

    def stop(self, p, wait=3.0):
        """Stop the run of document p, and wait up to `wait` seconds for it to exit."""
        if not self.busy(p):
            return
        try:
            os.killpg(json.loads(self.files(p)["json"].read_text())["pgid"], signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            pass
        for _ in range(int(wait * 10)):
            if not self.busy(p):
                break
            time.sleep(0.1)

    def forget(self, p):
        """Forget the session of document p, so the next run starts a new one. Returns an error message, or None."""
        if self.busy(p):
            return "Claude is still working. Stop it first."
        self.files(p)["session"].unlink(missing_ok=True)
        return None


def _write_private(path, text):
    """Write a file that only you can read."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")  # created 0600
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def finish(cur, err, stderr, returncode, stopped):
    """Close the answer: it is no longer streaming, and it says if it was stopped or went wrong."""
    cur["streaming"] = False
    if stopped:
        cur["stopped"] = True
    elif err or (returncode not in (0, None) and not cur.get("error")):
        cur["error"] = err or (stderr.strip()[-400:] or f"claude stopped with code {returncode}.")
