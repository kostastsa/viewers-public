"""The life cycle of the background server that the viewers share, and its network address.

One `Daemon` per app. It keeps the access key and the grants file in the state folder, finds out whether the server runs,
starts it in the background, stops it, and runs it in the foreground: the server restarts itself when the app or the
kit changes. It holds no policy about who may read what. Each app keeps its own handler, grants and trust rules.
"""
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent


KIT_ASSETS = {"kit.js": "text/javascript; charset=utf-8", "kit.css": "text/css; charset=utf-8",
              "components.js": "text/javascript; charset=utf-8", "components.css": "text/css; charset=utf-8"}  # the files of web/ that a page may load
# web/vendor/: libraries that the apps share (MathJax). A page may load these types from it.
VENDOR_TYPES = {".js": "text/javascript; charset=utf-8", ".woff": "font/woff", ".woff2": "font/woff2"}


def kit_asset(name):
    """(bytes, content type) of a shared page file, for the route /kit/NAME. None if the name is not on the list,
    or for vendor/PATH, if the file is not in web/vendor or its type is not on the list."""
    try:
        if name.startswith("vendor/"):
            vendor = (KIT_DIR / "web" / "vendor").resolve()
            p = (vendor / name[len("vendor/"):]).resolve()
            if vendor not in p.parents or p.suffix not in VENDOR_TYPES or not p.is_file():
                return None
            return p.read_bytes(), VENDOR_TYPES[p.suffix]
        if name.startswith("icons/"):  # the tab icons of the apps, for pages without scripts (folder pages)
            icons = (KIT_DIR / "web" / "icons").resolve()
            p = (icons / name[len("icons/"):]).resolve()
            if icons not in p.parents or p.suffix != ".png" or not p.is_file():
                return None
            return p.read_bytes(), "image/png"
        if name not in KIT_ASSETS:
            return None
        return (KIT_DIR / "web" / name).read_bytes(), KIT_ASSETS[name]
    except OSError:
        return None


def app_host(name):
    """The host name in the links of an app on this computer: NAME.localhost. macOS and the browsers send it to
    127.0.0.1. Each app has its own name, so Safari, which keeps one tab icon for each host name, shows the icon of the app."""
    return f"{name}.localhost"


def local_hosts(name):
    """The Host header values that a local app accepts. This is the check against DNS rebinding, so it is a short list."""
    return {"127.0.0.1", "localhost", app_host(name)}


# ----- the network address: 127.0.0.1, or the Tailscale address for your other computers -----
def tailscale_ip():
    """The Tailscale address of this computer (in 100.64.0.0/10), or None."""
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for m in re.finditer(r"inet (100\.(\d+)\.\d+\.\d+)", out):
        if 64 <= int(m.group(2)) <= 127:
            return m.group(1)
    return None


class Net:
    """Where the server of an app listens, the host in its links, and the Host headers that it accepts.
    PREFIX_BIND: 127.0.0.1 (the default), "tailscale" (the Tailscale address of this computer), or one address.
    0.0.0.0 is refused. PREFIX_NAMES: the names of this computer on the tailnet, for example mac-mini. The first one
    goes in the links. `why` is a reason for the refusal of 0.0.0.0, for example "a cell runs code"."""

    def __init__(self, name, prefix, why=""):
        self.name, self.prefix, self.why, self._bind = name, prefix, why, None

    def bind(self):
        if self._bind is None:
            v = (os.environ.get(self.prefix + "_BIND") or "127.0.0.1").strip()
            if v == "tailscale":
                v = tailscale_ip() or sys.exit(f"{self.name}: {self.prefix}_BIND=tailscale, but this computer has no "
                                                f"Tailscale address. Is Tailscale running?")
            if v in ("0.0.0.0", "::"):
                why = f"{self.why}, and " if self.why else ""
                sys.exit(f"{self.name}: {self.prefix}_BIND must not be 0.0.0.0: {why}every computer on the network could "
                         f"reach it.\nUse {self.prefix}_BIND=tailscale, or one address.")
            self._bind = v
        return self._bind

    def local(self):
        return self.bind() in ("127.0.0.1", "localhost")

    def names(self):
        return [n.strip() for n in (os.environ.get(self.prefix + "_NAMES") or "").split(",") if n.strip()]

    def link_host(self):
        """The host in the links that the command prints: the first name, else NAME.localhost on 127.0.0.1 (its own
        host name, so Safari shows the tab icon of the app), else the address."""
        names = self.names()
        if names:
            return names[0]
        return app_host(self.name) if self.local() else self.bind()

    def hosts(self):
        """The Host header values that are accepted. This is the check against DNS rebinding, so it is a short list."""
        hosts = local_hosts(self.name) | {self.bind()} | set(self.names())
        if not self.local():
            hosts.add(socket.gethostname().split(".")[0])
        return hosts


# ----- the label of a document: a short name for the tab and the header -----
def _project(folder):
    """The folder of the Git repo that holds `folder`, or None. It looks up to the home folder."""
    home = Path.home()
    for d in [folder, *folder.parents]:
        if (d / ".git").exists():
            return d
        if d == home or d == d.parent:
            return None
    return None


def _context(p):
    """The base context of file p: the project and the folder of p in it (thesis/ch2, thesis/…/ch2), or the parent
    folder if p is not in a Git repo."""
    d = p.parent
    proj = _project(d)
    if proj:
        rel = d.relative_to(proj).parts
        return [proj.name] + (["…"] if len(rel) > 1 else []) + ([rel[-1]] if rel else [])
    return ["~"] if d == Path.home() else [d.name or "/"]


def _folders(p):
    """All the folders of p, from the home folder (or from / outside it)."""
    try:
        return ["~", *p.parent.relative_to(Path.home()).parts]
    except ValueError:
        return list(p.parent.parts)


def tilde(p):
    """The path with ~ for the home folder."""
    try:
        return "~/" + Path(p).relative_to(Path.home()).as_posix()
    except ValueError:
        return str(p)


def doc_label(p, others=()):
    """The label of document p, for the tab title and the page header:
    {"name": "report.md", "context": "thesis/ch2", "crumbs": ["thesis", "ch2"], "path": "~/…", "abs": "/Users/…"}.
    `others` are the other documents that the server opened. If one of them has the same name and the same context, the
    context gets more folders, until the two labels are different."""
    p = Path(p)
    parts = _context(p)
    same = [q for q in map(Path, others) if q != p and q.name == p.name and _context(q) == parts]
    if same:
        full = _folders(p)
        for n in range(1, len(full) + 1):
            if all(_folders(q)[-n:] != full[-n:] for q in same):
                if n > len([x for x in parts if x != "…"]):
                    parts = full[-n:]
                break
        else:
            parts = full
    return {"name": p.name, "context": "/".join(parts), "crumbs": parts, "path": tilde(p), "abs": str(p)}


class Daemon:
    def __init__(self, name, state_dir, script, port_env, bind=lambda: "127.0.0.1", stop_wait=3.0):
        self.name = name
        self.state_dir = Path(state_dir)
        self.log = self.state_dir / "server.log"
        self.script = Path(script).resolve()  # the app file: it is started again with --serve
        self.port_env = port_env
        self.bind = bind  # the address of the server, as a function: it can depend on the environment
        self.stop_wait = stop_wait  # seconds to wait for the server to stop

    # ----- the access key and the files next to it -----
    def token_file(self, port):
        return self.state_dir / f"token-{port}"

    def grants_file(self, port):
        return self.state_dir / f"grants-{port}.json"

    def write_private(self, path, text):
        """Write a file in the state folder that only you can read."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, prefix="." + path.name + ".")  # created 0600
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)

    def new_token(self, port):
        """Make a fresh access key, readable only by you."""
        token = secrets.token_urlsafe(32)
        self.write_private(self.token_file(port), token)
        return token

    def read_token(self, port):
        try:
            return self.token_file(port).read_text().strip() or None
        except OSError:
            return None

    # ----- is it running, start it, stop it -----
    def ping(self, port):
        try:
            with urllib.request.urlopen(f"http://{self.bind()}:{port}/api/ping", timeout=1) as r:
                info = json.load(r)
            return info if info.get("app") == self.name else None
        except Exception:
            return None

    def port_in_use(self, port):
        with socket.socket() as s:
            return s.connect_ex((self.bind(), port)) == 0

    def ensure_server(self, port):
        """Return the running server's info, starting it in the background if needed."""
        info = self.ping(port)
        if info:
            return info
        if self.port_in_use(port):
            sys.exit(f"{self.name}: port {port} is used by another program. Set {self.port_env} to use a different one.")
        self.new_token(port)  # a fresh key and no grants for each fresh start
        self.grants_file(port).unlink(missing_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log, "ab") as log:
            subprocess.Popen([sys.executable, str(self.script), "--serve", "--port", str(port)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True, cwd=str(Path.home()))
        for _ in range(50):
            time.sleep(0.1)
            info = self.ping(port)
            if info:
                return info
        sys.exit(f"{self.name}: the server didn't start. See {self.log}")

    def stop_server(self, port):
        info = self.ping(port)
        if not info:
            print(f"{self.name}: not running.")
            return
        os.kill(info["pid"], signal.SIGTERM)
        for _ in range(int(self.stop_wait * 10)):
            time.sleep(0.1)
            if not self.ping(port):
                break
        print(f"{self.name}: stopped (pid {info['pid']}).")

    # ----- run in the foreground -----
    def serve(self, server, port, banner, on_stop=None):
        """Serve until stopped. When the app file or a file of the kit changes (and still compiles), stop the server
        and start the app again with the same port. `on_stop` runs after the server has closed, also before a restart."""
        watched = [self.script] + sorted(KIT_DIR.glob("*.py"))
        restart = threading.Event()

        def mtimes():
            out = {}
            for f in watched:
                try:
                    out[f] = f.stat().st_mtime
                except OSError:
                    pass
            return out

        def watch():
            last = mtimes()
            while True:
                time.sleep(1)
                now = mtimes()
                changed = [f for f in now if now[f] != last.get(f)]
                if not changed:
                    continue
                last = now
                time.sleep(0.5)  # let the editor finish writing
                try:
                    for f in changed:
                        compile(f.read_text(encoding="utf-8"), str(f), "exec")
                except (SyntaxError, OSError, UnicodeDecodeError) as e:
                    print(f"{self.name}: {f.name} changed but has an error, not restarting: {e}", flush=True)
                    continue
                restart.set()
                server.shutdown()
                return

        threading.Thread(target=watch, daemon=True).start()
        print(banner, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            if on_stop:
                on_stop()
        if restart.is_set():
            print(f"{self.name}: code changed, restarting", flush=True)
            os.execv(sys.executable, [sys.executable, str(self.script), "--serve", "--port", str(port)])
