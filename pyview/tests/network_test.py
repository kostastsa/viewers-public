"""Test of the server when it listens on the Tailscale address (PYVIEW_BIND=tailscale). It needs Tailscale
running on this computer, and uses only the standard library. Run: python3 tests/network_test.py"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pyview as pv  # noqa: E402

ip = pv.tailscale_ip()
if not ip:
    print("skipped: this computer has no Tailscale address")
    sys.exit(0)
PORT = "8803"
env = dict(os.environ, PYVIEW_BIND="tailscale", PYVIEW_PORT=PORT, PYVIEW_NAMES="Mac-Mini")
# Under tests/tmp (git ignores it): inside the home folder, which is the root the server may open.
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="net-", dir=TMP_ROOT))
nb = tmp / "nb.py"
nb.write_text("# %% a\nprint('over the tailnet')\n")
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def get(host_header, path, cookie=None, addr=ip):
    c = http.client.HTTPConnection(addr, int(PORT), timeout=5)
    headers = {"Host": host_header}
    if cookie:
        headers["Cookie"] = cookie
    c.request("GET", path, headers=headers)
    r = c.getresponse()
    return r.status, r.read()


try:
    out = subprocess.run(["python3", str(HERE.parent / "pyview.py"), str(nb), "--no-open"], env=env, capture_output=True, text=True)
    link = out.stdout.strip()
    check("pyview prints a link with the first name in PYVIEW_NAMES", link.startswith(f"http://Mac-Mini:{PORT}/view?f="), link or out.stderr)
    token = link.split("&t=")[-1]
    cookie = f"pyview{PORT}={token}"
    rel = str(nb.relative_to(Path.home()))

    check("the server listens on the Tailscale address", get(f"{ip}:{PORT}", "/api/ping")[0] == 200)
    try:
        http.client.HTTPConnection("127.0.0.1", int(PORT), timeout=3).request("GET", "/api/ping")
        refused = False
    except OSError:
        refused = True
    check("and not on 127.0.0.1", refused)
    check("a name from PYVIEW_NAMES is accepted in the Host header (any case)", get(f"mac-mini:{PORT}", "/api/ping")[0] == 200)
    check("another name is refused (DNS rebinding)", get(f"evil.example:{PORT}", "/api/ping")[0] == 403)
    check("without the access key a document is refused", get(f"{ip}:{PORT}", f"/api/doc?f={rel}")[0] == 403)
    check("a wrong key is refused", get(f"{ip}:{PORT}", f"/api/doc?f={rel}", cookie=f"pyview{PORT}=wrong")[0] == 403)
    status, body = get(f"{ip}:{PORT}", f"/api/doc?f={rel}", cookie=cookie)
    check("with the key the document loads", status == 200 and json.loads(body)["cells"][-1]["runnable"], str(status))

    run = subprocess.run(["python3", str(HERE.parent / "pyview.py"), "run", str(nb), "--cell", "1"], env=env, capture_output=True, text=True)
    check("pyview run works over the Tailscale address", "over the tailnet" in run.stdout and run.returncode == 0, run.stdout + run.stderr)

    bad = subprocess.run(["python3", str(HERE.parent / "pyview.py"), "--status"], env=dict(env, PYVIEW_BIND="0.0.0.0"), capture_output=True, text=True)
    check("PYVIEW_BIND=0.0.0.0 is refused", bad.returncode != 0 and "must not be 0.0.0.0" in bad.stderr, bad.stderr)
finally:
    subprocess.run(["python3", str(HERE.parent / "pyview.py"), "--stop"], env=env, capture_output=True)
    if os.environ.get("PYVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
