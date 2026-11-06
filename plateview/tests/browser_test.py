"""Browser test of plateview: a plate in its sandbox, the data and images by messages, the allowed folders, live data,
update, errors, Export, Keep, Delete, and the lifetime (a short GRACE). It uses its own server on port 8815 and its own
state folder. Needs Playwright and its Chromium. Run: uv run --with playwright python tests/browser_test.py
It deletes its temporary folder in tests/tmp at the end. Set PLATEVIEW_KEEP_TMP=1 to keep it for debugging."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PLATEVIEW = str(HERE.parent / "plateview.py")
PORT = "8815"
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="browser-", dir=TMP_ROOT))
data = tmp / "data"
shutil.copytree(HERE.parent / "examples" / "demo", data)
(tmp / "secret.csv").write_text("a,b\n1,2\n")
env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}  # plates with no Claude session
env.update(PLATEVIEW_PORT=PORT, PLATEVIEW_STATE=str(tmp / "state"), PLATEVIEW_GRACE="4", PLATEVIEW_REAP="1")
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def cli(*args, extra_env=None, stdin=None):
    return subprocess.run([sys.executable, PLATEVIEW, *args, "--no-open"] if args and args[0] in ("new",) else
                          [sys.executable, PLATEVIEW, *args], env={**env, **(extra_env or {})}, capture_output=True,
                          text=True, input=stdin)


def new(path, *more, extra_env=None, stdin=None):
    r = cli("new", path, *more, extra_env=extra_env, stdin=stdin)
    if r.returncode != 0:
        raise SystemExit(r.stderr)
    lines = r.stdout.strip().splitlines()
    return lines[0].split()[2], lines[-1]


def plates():
    return {p["id"]: p for p in json.loads(cli("list", "--json").stdout or "[]")}


try:
    pid, url = new(str(data / "demo.html"))
    check("plateview new prints a link at plateview.localhost with the access key",
          url.startswith(f"http://plateview.localhost:{PORT}/p/{pid}?t="), url)
    BASE = f"http://plateview.localhost:{PORT}"
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1300, "height": 950}, accept_downloads=True)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(url)
        fr = pg.frame_locator("#frame")
        fr.locator(".pv-table tbody tr").first.wait_for(timeout=20000)
        fr.locator(".pv-grid tbody tr").first.wait_for(timeout=20000)
        pg.wait_for_timeout(1500)
        frame = pg.frames[1]

        # --- the plate
        check("the header shows the title and the state", "Optimizers on CIFAR-10" in pg.locator("#title").inner_text()
              and pg.locator("#state").inner_text() == "kept while a tab shows it")
        check("the plate is in a sandboxed frame", pg.locator("#frame").get_attribute("sandbox") == "allow-scripts")
        check("the plate has an opaque origin", frame.evaluate("() => self.origin") == "null")
        check("the plate has the default fonts", frame.evaluate("() => document.fonts.check('600 20px \"IBM Plex Sans Condensed\"')"))
        check("the table reads runs.csv (6 rows, mean ± sd, ★)", fr.locator(".pv-table tbody tr").count() == 6
              and "94.1 ± 0.2" in fr.locator(".pv-table tbody tr").first.inner_text() and fr.locator(".pv-table td.best").count() == 3)
        check("the grid reads vision_grid.csv", fr.locator(".pv-grid tbody tr").count() == 10)
        check("the chart has three lines", fr.locator(".pv-chart path.ln").count() == 3)
        imgs = frame.evaluate("() => [...document.querySelectorAll('.pv-figure img')].map(i => i.naturalWidth > 0 && i.src.startsWith('blob:'))")
        check("three figures as blob images, also the PDF one", imgs == [True, True, True], imgs)
        check("the stat cards come from the data", "94.1" in fr.locator(".kpis").inner_text())
        fr.locator(".pv-seg button", has_text="Without SGD").click()
        pg.wait_for_timeout(300)
        check("a control changes what the chart shows", fr.locator(".pv-chart path.ln").count() == 2)

        # --- the sandbox: no network, no server
        res = frame.evaluate("""async () => { try { await fetch('/api/plates'); return 'fetched'; } catch (e) { return 'blocked'; } }""")
        check("the plate cannot call the server", res == "blocked", res)
        res = frame.evaluate("""async () => { try { await fetch('https://example.com/'); return 'fetched'; } catch (e) { return 'blocked'; } }""")
        check("the plate cannot reach the network", res == "blocked", res)
        res = frame.evaluate("() => { try { return String(parent.document.title); } catch (e) { return 'blocked'; } }")
        check("the plate cannot read the frame around it", res == "blocked", res)
        (data / "nested.json").write_text('{"a": {"b": [1, 2, 3]}}')
        res = frame.evaluate("""() => pv.json('nested.json').then(j => j.a.b.length, e => e.message)""")
        check("pv.json reads a nested JSON file as it is", res == 3, res)
        res = frame.evaluate("""() => pv.data('../secret.csv').then(() => 'read', e => e.message)""")
        check("the plate cannot read a file outside its folders", "may not read" in res, res)

        # --- live data: a data file changes on disk
        runs = data / "runs.csv"
        runs.write_text(runs.read_text().replace("AdamW,1e-3,94.1", "AdamW,1e-3,95.5"))
        fr.locator(".pv-table tbody", has_text="95.5").wait_for(timeout=8000)
        check("the table and the stat cards follow the data file", "95.5" in fr.locator(".kpis").inner_text())

        # --- update: the HTML changes
        (tmp / "v2.html").write_text("<title>Version two</title><div class='wrap'><h1 id='h'>Two</h1><div id='t'></div></div>"
                                     "<script>pv.table('#t', 'runs.csv', {cols: 'optimizer,acc'})</script>")
        cli("update", pid, str(tmp / "v2.html"))
        fr.locator("#h", has_text="Two").wait_for(timeout=8000)
        pg.wait_for_timeout(500)
        check("plateview update shows the new HTML and title", pg.locator("#title").inner_text() == "Version two"
              and fr.locator(".pv-table tbody tr").count() == 6)

        # --- an error in a plate shows in the frame
        (tmp / "bad.html").write_text("<p>x</p><script>pv.table('#none', 'runs.csv'); undefinedFunction();</script>")
        cli("update", pid, str(tmp / "bad.html"))
        pg.wait_for_function("() => document.getElementById('err').classList.contains('on')", timeout=8000)
        check("an error of the plate shows above it", "undefinedFunction" in pg.locator("#err").inner_text()
              or "no element" in pg.locator("#err").inner_text(), pg.locator("#err").inner_text())
        cli("update", pid, str(data / "demo.html"))
        fr.locator(".pv-table tbody tr").first.wait_for(timeout=10000)
        fr.locator(".pv-figure img").first.wait_for(timeout=10000)
        pg.wait_for_timeout(1500)

        # --- Export: one file, which opens from the disk with its data
        with pg.expect_download() as dl:
            pg.locator("#exportBtn").click()
        out = tmp / "export.html"
        dl.value.save_as(out)
        check("Export downloads one HTML file named after the plate", dl.value.suggested_filename == "Optimizers on CIFAR-10.html", dl.value.suggested_filename)
        pe = ctx.new_page()
        reqs = []
        pe.on("request", lambda r: reqs.append(r.url))
        pe.goto(out.as_uri())
        pe.wait_for_selector(".pv-table tbody tr", timeout=10000)
        pe.wait_for_timeout(800)
        check("the exported file shows the data with no server", pe.locator(".pv-table tbody tr").count() == 6
              and pe.locator(".pv-grid tbody tr").count() == 10 and "95.5" in pe.locator(".kpis").inner_text())
        check("the exported file shows the figures", pe.evaluate("() => [...document.images].every(i => i.naturalWidth > 0)"))
        check("the exported file loads nothing from anywhere", all(u.startswith(("file:", "blob:", "data:")) for u in reqs),
              [u for u in reqs if not u.startswith(("file:", "blob:", "data:"))][:3])
        pe.close()

        # --- Keep
        pg.locator("#keepBtn").click()
        pg.wait_for_timeout(300)
        check("Keep makes the plate permanent", plates()[pid]["kept"] is True and pg.locator("#keepBtn").inner_text() == "Kept ✓")
        pg.locator("#keepBtn").click()
        pg.wait_for_timeout(300)
        check("a second click takes Keep back", plates()[pid]["kept"] is False)

        # --- the lifetime: a plate stays while a tab shows it, and goes GRACE seconds after the tab closes
        pid2, url2 = new(str(data / "demo.html"))
        p2 = ctx.new_page()
        p2.goto(url2)
        p2.frame_locator("#frame").locator(".pv-table tbody tr").first.wait_for(timeout=15000)
        time.sleep(7)  # longer than GRACE: the tab keeps it
        check("a plate stays while a tab shows it", pid2 in plates())
        p2.close()
        time.sleep(7)
        check("the plate goes after the tab closes and GRACE ends", pid2 not in plates())
        # a plate stays while its Claude session runs, also with no tab
        session = subprocess.Popen(["sleep", "60"])
        pid3, _ = new(str(data / "demo.html"), extra_env={"CLAUDE_PID": str(session.pid), "CLAUDE_CODE_SESSION_ID": "test"})
        time.sleep(7)
        check("a plate stays while its Claude session runs", pid3 in plates() and plates()[pid3]["state"] == "session")
        session.kill(); session.wait()
        time.sleep(7)
        check("the plate goes after the session ends and GRACE ends", pid3 not in plates())

        # --- the list of plates, and Delete
        pg.goto(BASE + "/")
        pg.wait_for_selector("td.t a", timeout=8000)
        check("the list page shows the plates", pid in pg.locator("table").inner_text())
        pg.goto(BASE + f"/p/{pid}")
        pg.wait_for_selector("#frame", timeout=8000)
        pg.once("dialog", lambda d: d.accept())
        pg.locator("#deleteBtn").click()
        pg.wait_for_url(BASE + "/", timeout=8000)
        check("Delete removes the plate", pid not in plates())

        # --- the rules of the server
        r = b.new_context().request.get(f"{BASE}/api/plates")
        check("a request without the access key is refused", r.status == 403, r.status)
        r = pg.request.get(f"http://127.0.0.1:{PORT}/api/plates", headers={"Host": "evil.example"})
        check("a request with another host name is refused", r.status == 403, r.status)
        pid4, _ = new(str(data / "demo.html"))
        r = pg.request.get(f"{BASE}/p/{pid4}/plate")
        csp = r.headers.get("content-security-policy", "")
        check("the plate comes with a sandbox CSP and no network", "sandbox allow-scripts" in csp and "connect-src" not in csp
              and "default-src 'none'" in csp, csp)
        errs = [e for e in errs if "undefinedFunction" not in e]  # the error that the test puts in a plate on purpose
        check("no errors in the frame", not errs, errs)
        b.close()
finally:
    subprocess.run([sys.executable, PLATEVIEW, "--stop"], env=env, capture_output=True)
    if os.environ.get("PLATEVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
