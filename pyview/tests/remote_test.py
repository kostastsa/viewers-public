"""Test of `pyview --remote HOST FILE` with a stand-in ssh that runs the "remote" command on this computer and
records the tunnel requests. Standard library only. Run: python3 tests/remote_test.py"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYVIEW = str(HERE.parent / "pyview.py")
# Under tests/tmp (git ignores it): inside the home folder, which is the root the server may open.
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="remote-", dir=TMP_ROOT))
bindir, log = tmp / "bin", tmp / "ssh.log"
bindir.mkdir()
(bindir / "ssh").write_text('#!/bin/sh\necho "$@" >> "%s"\ncase "$*" in *"-f -N"*) exit 0;; esac\nshift\nexec sh -c "$*"\n' % log)
(bindir / "ssh").chmod((bindir / "ssh").stat().st_mode | stat.S_IEXEC)
nb = tmp / "nb.py"
nb.write_text("# %% a\nprint(1)\n")
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def remote(port, **extra_env):
    env = dict(os.environ, PATH=f"{bindir}:{Path.home()}/.local/bin:{os.environ['PATH']}", **extra_env)
    log.write_text("")
    r = subprocess.run(["python3", PYVIEW, "--remote", "minihost", str(nb), "--port", str(port), "--no-open"], env=env, capture_output=True, text=True)
    stop = subprocess.run(["python3", PYVIEW, "--stop"], env=dict(env, PYVIEW_PORT=str(port)), capture_output=True)
    return r, log.read_text()


try:
    r, calls = remote(8805)
    check("loopback server: the link is printed", r.returncode == 0 and r.stdout.strip().startswith("http://pyview.localhost:8805/view?f="), r.stdout + r.stderr)
    check("loopback server: an SSH tunnel for that port is opened", "-L 8805:127.0.0.1:8805 minihost" in calls, calls)
    check("and the user is told why", "tunnel" in r.stderr, r.stderr)

    r, calls = remote(8806, PYVIEW_BIND="tailscale", PYVIEW_NAMES="mac-mini")
    check("Tailscale server: the link uses the name", r.returncode == 0 and r.stdout.strip().startswith("http://mac-mini:8806/view?f="), r.stdout + r.stderr)
    check("Tailscale server: no tunnel", "-L" not in calls, calls)

    bad = subprocess.run(["python3", PYVIEW, "--remote", "minihost", str(tmp / "missing.py"), "--no-open"],
                         env=dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}"), capture_output=True, text=True)
    check("a missing file gives an error with advice", bad.returncode != 0 and "could not start the viewer" in bad.stderr, bad.stderr)
finally:
    if os.environ.get("PYVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
