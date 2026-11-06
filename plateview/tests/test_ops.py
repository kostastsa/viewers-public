"""Tests of plateview without a server: plates, the files that a plate may read, the lifetime, the page of a plate,
Export, and the command line. Run: python3 tests/test_ops.py
It uses its own state folder in tests/tmp and deletes it at the end. Set PLATEVIEW_KEEP_TMP=1 to keep it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP_ROOT = HERE / "tmp"  # git ignores it
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="ops-", dir=TMP_ROOT))
os.environ["PLATEVIEW_STATE"] = str(tmp / "state")
os.environ["PLATEVIEW_GRACE"] = "2"
os.environ.pop("CLAUDE_PID", None)  # the plates of these tests have no Claude session
sys.path.insert(0, str(HERE.parent))
import plateview as pv  # noqa: E402

PLATEVIEW = str(HERE.parent / "plateview.py")
data = tmp / "data"
shutil.copytree(HERE.parent / "examples" / "demo", data)
failures = []
env = dict(os.environ)


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


def raises(fn):
    try:
        fn()
    except pv.PlateError as e:
        return str(e)
    return None


def cli(*args, stdin=None):
    return subprocess.run([sys.executable, PLATEVIEW, *args], capture_output=True, text=True, input=stdin, env=env)


try:
    # ---- a plate
    m = pv.make_plate("<title>My plate</title><p>x</p>", [str(data)], base=data)
    check("a plate has an id, its title from <title>, and its folders", pv.ID_RE.match(m["id"]) and m["title"] == "My plate"
          and m["allow"] == [str(data.resolve())] and m["owner"] is None)
    check("the plate is a folder with plate.html and meta.json", (pv.PLATES / m["id"] / "plate.html").is_file()
          and pv.read_meta(m["id"])["id"] == m["id"])
    check("a bad id is refused", "no such plate" in (raises(lambda: pv.read_meta("../x")) or ""))

    # ---- the files that a plate may read
    check("a file in an allowed folder, by a relative path", pv.plate_file(m, "runs.csv") == (data / "runs.csv").resolve())
    check("the same file by its full path", pv.plate_file(m, str(data / "runs.csv")) == (data / "runs.csv").resolve())
    (tmp / "secret.csv").write_text("a\n1\n")
    check("a file outside the allowed folders is refused", "may not read" in (raises(lambda: pv.plate_file(m, "../secret.csv")) or ""))
    (data / ".hidden.csv").write_text("a\n1\n")
    check("a hidden file is refused, also in an allowed folder", "hidden" in (raises(lambda: pv.plate_file(m, ".hidden.csv")) or ""))
    check("a missing file gives a clear error", "not found" in (raises(lambda: pv.plate_file(m, "nope.csv")) or ""))

    # ---- JSON files, as they are
    (data / "sel.json").write_text('{"a0.5": {"L-GSF": [{"rank": "best", "mse": NaN}]}}')
    j = pv.read_json(pv.plate_file(m, "sel.json"))
    check("a nested JSON file is read as it is, NaN as null", j == {"a0.5": {"L-GSF": [{"rank": "best", "mse": None}]}}, j)
    check("a CSV file is not JSON", "Not a JSON file" in (raises(lambda: pv.read_json(pv.plate_file(m, "runs.csv"))) or ""))

    # ---- the lifetime
    now = time.time()
    check("a new plate stays (it is new)", pv.state_of(m, {}) == "new")
    old = {**m, "created": now - 100}
    check("an old plate with no session and no tab can go", pv.state_of(old, {}) is None)
    check("an old plate stays while a tab asks about it", pv.state_of(old, {m["id"]: now}) == "tab")
    check("a kept plate stays", pv.state_of({**old, "kept": True}, {}) == "kept")
    me = {"pid": os.getpid(), "start": pv.proc_start(os.getpid())}
    check("a plate stays while its Claude session runs", pv.state_of({**old, "owner": me}, {}) == "session")
    check("a process with the same number but another start time is not the session",
          pv.state_of({**old, "owner": {**me, "start": "Mon Jan  1 00:00:00 2001"}}, {}) is None)
    sleeper = subprocess.Popen(["sleep", "30"])
    owner = {"pid": sleeper.pid, "start": pv.proc_start(sleeper.pid)}
    alive = pv.owner_alive(owner)
    sleeper.kill(); sleeper.wait()
    check("when the session ends, it does not count", alive and not pv.owner_alive(owner))
    m2 = pv.make_plate("<p>old</p>", [str(data)], base=data)
    meta2 = pv.read_meta(m2["id"]); meta2["created"] = now - 100; pv.write_meta(m2["id"], meta2)
    gone = pv.reap({})
    check("the reaper deletes the plates that can go, and keeps the others", gone == [m2["id"]]
          and not (pv.PLATES / m2["id"]).exists() and (pv.PLATES / m["id"]).exists(), gone)

    # ---- the page of a plate
    head = pv.plate_head()
    check("the head of a plate holds the fonts, the styles, the components and pv", "@font-face" in head
          and "font/woff2;base64," in head and ".pv-table" in head and "const PV =" in head and "window.pv =" in head)
    check("the head has no </script> inside its scripts", head.count("</script>") == 1)
    out = pv.compose("<!doctype html><html><head><title>T</title></head><body>B</body></html>", "<!--HEAD-->")
    check("the head goes right after <head>", "<head><!--HEAD--><title>T</title>" in out)
    check("a fragment becomes a whole page", pv.compose("<p>B</p>", "<!--H-->").startswith("<!doctype html><html><head><!--H--></head><body><p>B</p>"))

    # ---- Export
    m3 = pv.make_plate((data / "demo.html").read_text(), [str(data)], base=data)
    meta3 = pv.read_meta(m3["id"])
    meta3["used"] = ["runs.csv", "fig/gradnorm_e10.pdf", "../secret.csv", "json:sel.json"]
    pv.write_meta(m3["id"], meta3)
    html, _ = pv.export_plate(m3["id"])
    check("an export holds the data that the plate used", '"runs.csv": {"columns": ["optimizer"' in html, html[:200])
    check("an export holds the images, a PDF figure as a PNG", '"fig/gradnorm_e10.pdf": {"type": "image/png", "b64": "iVBOR' in html)
    check("an export never holds a file that the plate may not read", "secret.csv\": {" not in html)
    check("an export holds the JSON files as they are", '"json": {"sel.json": {"json": {"a0.5"' in html)
    check("an export has a CSP with no network", 'http-equiv="Content-Security-Policy"' in html and "connect-src" not in
          html.split('http-equiv="Content-Security-Policy" content="')[1].split('"')[0])

    # ---- the command line
    r = cli("new", str(data / "demo.html"), "--no-open")
    pid = r.stdout.split()[2] if r.returncode == 0 else ""
    check("plateview new makes a plate and prints its link", r.returncode == 0 and "/p/" + pid in r.stdout, r.stderr)
    meta = pv.read_meta(pid) if pid else {}
    check("by default the plate may read the folder of its file", meta.get("allow") == [str(data.resolve())], meta.get("allow"))
    r = cli("new", "-", "--allow", str(data), "--title", "From stdin", "--no-open", stdin="<p>hi</p>")
    check("plateview new - reads the HTML from stdin", r.returncode == 0 and "From stdin" in r.stdout, r.stderr)
    (tmp / "v2.html").write_text("<title>Second version</title><p>2</p>")
    r = cli("update", pid, str(tmp / "v2.html"))
    check("plateview update changes the HTML and the title", r.returncode == 0 and pv.read_meta(pid)["title"] == "Second version"
          and "2</p>" in (pv.PLATES / pid / "plate.html").read_text(), r.stderr)
    r = cli("list")
    check("plateview list shows the plates", r.returncode == 0 and pid in r.stdout and "Second version" in r.stdout, r.stdout)
    r = cli("keep", pid)
    check("plateview keep", r.returncode == 0 and pv.read_meta(pid)["kept"] is True)
    out = tmp / "out.html"
    r = cli("export", pid, str(out))
    check("plateview export writes one file", r.returncode == 0 and out.is_file() and "window.__PV_BUNDLE" in out.read_text(), r.stderr)
    r = cli("delete", pid)
    check("plateview delete", r.returncode == 0 and not (pv.PLATES / pid).exists())
    r = cli("delete", "abcdefabcdef")
    check("a missing plate gives a clear error", r.returncode != 0 and "There is no plate" in r.stderr, r.stderr)
    r = subprocess.run([sys.executable, PLATEVIEW, str(data / "demo.html"), "--no-open"], capture_output=True, text=True, env=env)
    check("plateview FILE.html is plateview new FILE.html", r.returncode == 0 and "plateview: plate " in r.stdout, r.stderr)
finally:
    subprocess.run([sys.executable, PLATEVIEW, "--stop"], capture_output=True, env=env)
    if os.environ.get("PLATEVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
