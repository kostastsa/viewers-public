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
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path

# Variables of the Claude Code session that may have started the server. A run is a fresh session.
SESSION_VARS = {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
                "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_EXECPATH",
                "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SSE_PORT",
                # macOS: a server that a background job started can have XPC_FLAGS=0x2. With it, `claude` cannot read
                # the login in the keychain and says "Not logged in".
                "XPC_FLAGS"}


def clean_env(drop_prefix=None, doc=None, local=True):
    """The environment of a run: ours, without the variables of the session that started the server.
    With `drop_prefix`, also without every variable that starts with it. CLAUDE_CONFIG_DIR is the account of the
    document `doc` (see `account`). If the account is an Ollama model, the run uses that model (`ollama_env`).
    With `local=False` it does not: the run uses Claude (the self-improving loop)."""
    env = {k: v for k, v in os.environ.items()
           if k not in SESSION_VARS and not (drop_prefix and k.startswith(drop_prefix))}
    name, d = account(doc)
    if d == DEFAULT_DIR:
        env.pop("CLAUDE_CONFIG_DIR", None)  # an explicit ~/.claude can use a different keychain entry: leave it out
    else:
        env["CLAUDE_CONFIG_DIR"] = str(d)
    if local and is_ollama(name):
        host, model = ollama_target(name)
        url = ollama_url() if host is None else ssh_tunnel(host, wait=True)  # the tunnel can have stopped
        env.update(ollama_env(model, url))
    return env


# Accounts: each Claude Code configuration folder (~/.claude, ~/.claude-NAME) has its own login and transcripts.
# Each document has its own account, in ACCOUNT_FILE: {"default": NAME, "docs": {PATH: NAME}}. The last choice is also
# the default of the documents without a choice. Each run (a chat turn, an Oracle round) reads the file, so a change has
# effect at the next run. Without any choice: the CLAUDE_CONFIG_DIR of the server, else ~/.claude.
DEFAULT_DIR = Path.home() / ".claude"
ACCOUNT_FILE = Path(os.environ.get("VIEWKIT_ACCOUNT_FILE") or Path.home() / ".config" / "viewkit" / "accounts.json")
_START_DIR = os.environ.get("CLAUDE_CONFIG_DIR")


def _start_dir():
    return Path(_START_DIR).expanduser() if _START_DIR else DEFAULT_DIR


# Ollama: a local model of Ollama is also an account, "ollama:MODEL". Its run is the same `claude -p`, with the same
# tools and skills, but Claude Code sends its requests to the Anthropic-compatible route of the Ollama server
# (OLLAMA_HOST, else 127.0.0.1:11434) in place of Anthropic. Its configuration folder is that of the server.
# A model of the Ollama server of an SSH host is the account "ollama@HOST:MODEL". The hosts are in OLLAMA_FILE
# ({"ssh": ["HOST", "HOST=PORT"]}) or in VIEWKIT_OLLAMA_SSH ("HOST,HOST=PORT"). For each host, the kit keeps one tunnel
# `ssh -N -L 127.0.0.1:PORT:127.0.0.1:11434 HOST`, and the runs send their requests to http://127.0.0.1:PORT. The port is
# fixed for each host, so the servers of all the apps share one tunnel, and a tunnel that the user opens is used too.
OLLAMA = "ollama:"
OLLAMA_SSH = "ollama@"
OLLAMA_FILE = Path(os.environ.get("VIEWKIT_OLLAMA_FILE") or Path.home() / ".config" / "viewkit" / "ollama.json")
SSH = os.environ.get("VIEWKIT_SSH") or "ssh"
_ollama = {}  # {url: {"at": time, "models": [names]}}
_tunnels = {}  # {host: {"proc": Popen, "at": time of the last start}}
_tunnel_lock = threading.Lock()


def is_ollama(name):
    return bool(name) and (name.startswith(OLLAMA) or name.startswith(OLLAMA_SSH))


def ollama_target(name):
    """(host, model) of an Ollama account: host is None for the local server."""
    if name.startswith(OLLAMA_SSH):
        host, _, model = name[len(OLLAMA_SSH):].partition(":")
        return host, model
    return None, name[len(OLLAMA):]


def ssh_hosts():
    """The SSH hosts with an Ollama server, as {host: local port}."""
    spec = os.environ.get("VIEWKIT_OLLAMA_SSH")
    if spec is None:
        try:
            items = json.loads(OLLAMA_FILE.read_text()).get("ssh") or []
        except (OSError, ValueError, AttributeError):
            items = []
    else:
        items = spec.split(",")
    out = {}
    for item in items:
        host, _, port = str(item).strip().partition("=")
        if host and not host.startswith("-") and ":" not in host:
            out[host] = int(port) if port.isdigit() else 11500 + int(hashlib.sha1(host.encode()).hexdigest(), 16) % 400
    return out


def _answers(url, timeout=0.5):
    try:
        with urllib.request.urlopen(url + "/api/version", timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def ssh_tunnel(host, wait=False):
    """The URL of the Ollama server of `host`, through its tunnel. If the tunnel does not answer, start it (at most one
    start in 30 seconds) and wait up to 3 seconds (10 with `wait`) for it."""
    port = ssh_hosts().get(host) or 0
    url = f"http://127.0.0.1:{port}"
    if not port or _answers(url):
        return url
    with _tunnel_lock:
        t = _tunnels.get(host) or {}
        alive = t.get("proc") and t["proc"].poll() is None
        if not alive and time.time() - t.get("at", 0) > 30:
            log = Path.home() / ".cache" / "viewkit" / f"ollama-ssh-{host}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(log, "a") as f:
                    proc = subprocess.Popen(
                        [SSH, "-N", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ExitOnForwardFailure=yes",
                         "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
                         "-L", f"127.0.0.1:{port}:127.0.0.1:11434", host],
                        stdin=subprocess.DEVNULL, stdout=f, stderr=f, start_new_session=True)
                _tunnels[host] = {"proc": proc, "at": time.time()}
            except OSError:
                _tunnels[host] = {"proc": None, "at": time.time()}
    end = time.time() + (10 if wait else 3)
    while time.time() < end:
        if _answers(url, 0.3):
            break
        p = (_tunnels.get(host) or {}).get("proc")
        if p is None or p.poll() is not None:  # ssh stopped: no login, no network, or the port is taken
            break
        time.sleep(0.2)
    return url


def ollama_models(url=None):
    """The models of the Ollama server at `url` (default: the local server) that can use tools, as names. No server:
    an empty list. The answer is kept for 10 seconds."""
    url = url or ollama_url()
    c = _ollama.setdefault(url, {"at": 0.0, "models": []})
    if time.time() - c["at"] > 10:
        models = []
        try:
            with urllib.request.urlopen(url + "/api/tags", timeout=0.5) as r:
                for m in json.loads(r.read()).get("models") or []:
                    caps = m.get("capabilities")  # an old Ollama does not give them
                    if m.get("name") and (caps is None or "tools" in caps):
                        models.append(str(m["name"]))
        except (OSError, ValueError, AttributeError):
            pass
        c.update(at=time.time(), models=sorted(models))
    return list(c["models"])


def ollama_url():
    host = (os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434").strip().rstrip("/")
    if "://" not in host:
        host = "http://" + host
    scheme, rest = host.split("://", 1)
    if ":" not in rest.rsplit("]", 1)[-1]:
        rest += ":11434"
    return f"{scheme}://{rest}".replace("://0.0.0.0:", "://127.0.0.1:")


def remote_models(host, port):
    """The models of the Ollama server of SSH host `host` that can use tools. The tunnel starts only when the list
    is older than 10 seconds."""
    url = f"http://127.0.0.1:{port}"
    if time.time() - _ollama.get(url, {}).get("at", 0) > 10:
        ssh_tunnel(host)
    return ollama_models(url)


def ollama_env(model, url=None):
    """The variables that make `claude` use `model` of the Ollama server at `url` (default: the local server)."""
    return {"ANTHROPIC_BASE_URL": url or ollama_url(), "ANTHROPIC_AUTH_TOKEN": "ollama", "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_MODEL": model, "ANTHROPIC_SMALL_FAST_MODEL": model, "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "CLAUDE_CODE_SUBAGENT_MODEL": model, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}


def accounts():
    """The accounts, as {name: Path}: the configuration folders (~/.claude is "claude", ~/.claude-team is
    "claude-team"), then the local Ollama models ("ollama:MODEL"), then the models of the SSH hosts
    ("ollama@HOST:MODEL"). An Ollama account has the folder of the server."""
    out = _dirs()
    out.update({OLLAMA + m: _start_dir() for m in ollama_models()})
    for host, port in ssh_hosts().items():
        out.update({f"{OLLAMA_SSH}{host}:{m}": _start_dir() for m in remote_models(host, port)})
    return out


def _dirs():
    out = {"claude": DEFAULT_DIR}
    for d in sorted(Path.home().glob(".claude-*")):
        if d.is_dir() and any((d / f).exists() for f in ("projects", ".claude.json", "settings.json")):
            out[d.name[1:]] = d
    if _START_DIR:
        start = Path(_START_DIR).expanduser()
        if start not in out.values():
            name = start.name.lstrip(".") or str(start)
            out[name if name not in out else str(start)] = start
    return out


def _choices():
    try:
        d = json.loads(ACCOUNT_FILE.read_text())
    except (OSError, ValueError):
        return {"default": "", "docs": {}}
    docs = d.get("docs") if isinstance(d, dict) else None
    return {"default": str(d.get("default") or ""), "docs": docs if isinstance(docs, dict) else {}}


def account(doc=None):
    """The account of document `doc` (a path, or None), as (name, Path): its own choice, else the last choice, else
    the CLAUDE_CONFIG_DIR of the server, else ~/.claude."""
    acc, c = accounts(), _choices()
    for name in (c["docs"].get(str(doc)) if doc else None, c["default"]):
        if name in acc:
            return name, acc[name]
        if is_ollama(name):  # the Ollama server does not answer now: the run tells the error
            return name, _start_dir()
    if _START_DIR:
        start = Path(_START_DIR).expanduser()
        for n, d in acc.items():
            if d == start:
                return n, d
    return "claude", DEFAULT_DIR


def account_api(method, body=None, doc=None):
    """The route /api/account of the apps, for document `doc`. GET: {account, accounts}. POST {account: NAME}: select
    it for this document, and make it the default of new documents. Returns (status, json)."""
    if method == "POST":
        name = str((body or {}).get("account", ""))
        if name not in accounts():
            return 400, {"error": f"No such account: {name}"}
        set_account(doc, name, default=True)
    name, names = account(doc)[0], list(accounts())
    return 200, {"account": name, "accounts": names if name in names else names + [name]}


def set_account(doc, name, default=False):
    """Select account `name` for document `doc` (a path, or None). With `default`, also for new documents."""
    ACCOUNT_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(ACCOUNT_FILE.with_name(ACCOUNT_FILE.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)  # the servers of all apps write this file
        c = _choices()
        if default:
            c["default"] = name
        if doc:
            c["docs"][str(doc)] = name
        _write_private(ACCOUNT_FILE, json.dumps(c, indent=1) + "\n")
    finally:
        os.close(lock)


def _projects(env):
    d = env.get("CLAUDE_CONFIG_DIR")
    return (Path(d).expanduser() if d else DEFAULT_DIR) / "projects"


def session_files(sid):
    """The transcripts of session `sid` in all accounts, newest first."""
    found = [f for d in _dirs().values() for f in (d / "projects").glob(f"*/{sid}.jsonl")]
    return sorted(found, key=lambda f: f.stat().st_mtime, reverse=True)


def carry_session(sid, env):
    """Make the newest transcript of session `sid` available in the account of `env`, so `--resume` finds it there.
    A session that started in one account continues in another. True if the account has the session now."""
    found = session_files(sid)
    if not found:
        return False
    src, target = found[0], _projects(env)
    if src.parent.parent == target:
        return True
    dst = target / src.parent.name
    dst.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copy2(src, dst / src.name)
    side = src.with_suffix("")  # the folder of the session (subagents, tool results), if there is one
    if side.is_dir():
        shutil.copytree(side, dst / side.name, dirs_exist_ok=True)
    return True


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
    if "--resume" in cmd:  # the session can have started in a different account
        i = cmd.index("--resume")
        if i + 1 < len(cmd):
            carry_session(cmd[i + 1], env)
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                start_new_session=True)
        if on_proc:
            on_proc(proc)
        reader = threading.Thread(target=lambda: stderr.append(proc.stderr.read()), daemon=True)
        reader.start()
        for line in proc.stdout:
            try:
                on_event(json.loads(line))
            except ValueError:
                continue
        proc.wait()
        # The process can end before the thread has its stderr (a busy machine): wait for the thread. A child of
        # claude can keep stderr open after claude ends, so the wait has a limit.
        reader.join(timeout=5)
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
        """The id of the document's Claude session, if it still exists in one of the accounts."""
        try:
            sid = str(uuid.UUID(self.files(p)["session"].read_text().strip()))
        except (OSError, ValueError):
            return None
        return sid if session_files(sid) else None

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
            env = clean_env(self.drop_prefix, p)
            env["ORACLE_EXIT"] = str(f["exit"])
            sid = self.session(p)
            if sid and carry_session(sid, env):
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
