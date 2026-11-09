"""Browser test of plateview: a plate in its sandbox, the data and images by messages, the allowed folders, live data,
update, errors, Export, Keep, the comments (words, cells, the whole plate), the chat, the Oracle, the dashboard of the
notes, Delete, and the lifetime (a short GRACE). The chat and the Oracle use viewkit/tests/fake_claude.py. It uses its own server on port 8815 and its own
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
KIT = HERE.parent.parent / "viewkit"
PORT = "8815"
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="browser-", dir=TMP_ROOT))
data = tmp / "data"
shutil.copytree(HERE.parent / "examples" / "demo", data)
(tmp / "secret.csv").write_text("a,b\n1,2\n")
bindir = tmp / "bin"  # a stand-in `claude` for the chat and the Oracle
bindir.mkdir()
(bindir / "claude").write_text('#!/bin/sh\nexec python3 "%s" "$@"\n' % (KIT / "tests" / "fake_claude.py"))
(bindir / "claude").chmod(0o755)
env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}  # plates with no Claude session
env.update(PLATEVIEW_PORT=PORT, PLATEVIEW_STATE=str(tmp / "state"), PLATEVIEW_GRACE="4", PLATEVIEW_REAP="1",
           PLATEVIEW_CLAUDE=str(bindir / "claude"), VIEWKIT_GH=str(KIT / "tests" / "fake_gh.py"),
           FAKE_GH_LOG=str(tmp / "gh.jsonl"), VIEWKIT_NOTES_QUEUE=str(tmp / "notes-queue.jsonl"),
           VIEWKIT_AUTO_IMPROVE="0", VIEWKIT_IMPROVE_DIR=str(tmp / "improve"))
MOD = "Meta" if sys.platform == "darwin" else "Control"
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

        # --- PDF: ⌘/Ctrl+P (also from inside the plate) opens the print view, which opens the print dialog
        ctx.add_init_script("window.print = () => { window.__printed = true; }")
        pg.locator("#title").click()
        with ctx.expect_page() as np:
            pg.keyboard.press("ControlOrMeta+p")
        pp = np.value
        pp.wait_for_function("() => window.__printed === true", timeout=15000)
        check("⌘/Ctrl+P opens the print view of the plate, and it opens the print dialog",
              pp.url.startswith(url.split("?")[0] + "/print") and pp.title() == "Optimizers on CIFAR-10"
              and pp.locator(".pv-table tbody tr").count() == 6 and pp.locator(".pv-grid tbody tr").count() == 10, pp.url)
        check("the print view shows the figures and the light theme",
              pp.evaluate("() => [...document.images].every(i => i.complete && i.naturalWidth > 0)")
              and pp.evaluate("() => document.documentElement.dataset.theme") == "light")
        check("the print view is a sandbox: no opener, no server",
              pp.evaluate("() => self.origin") == "null" and pp.evaluate("() => window.opener") is None
              and pp.evaluate("async () => { try { await fetch('/api/plates'); return 'fetched'; } catch (e) { return 'blocked'; } }") == "blocked")
        pp.close()
        frame.locator("h1").click()
        with ctx.expect_page() as np:
            pg.keyboard.press("ControlOrMeta+p")
        np.value.wait_for_function("() => window.__printed === true", timeout=15000)
        check("⌘/Ctrl+P works also when the plate has the focus", np.value.url.startswith(url.split("?")[0] + "/print"))
        np.value.close()

        # --- Keep
        pg.locator("#keepBtn").click()
        pg.wait_for_timeout(300)
        check("Keep makes the plate permanent", plates()[pid]["kept"] is True and pg.locator("#keepBtn").inner_text() == "Kept ✓")
        pg.locator("#keepBtn").click()
        pg.wait_for_timeout(300)
        check("a second click takes Keep back", plates()[pid]["kept"] is False)

        # --- comments: on selected words of the plate
        def comments():
            return json.loads(cli("comments", pid, "--json").stdout or "[]")
        title = frame.evaluate("() => document.querySelector('h1').textContent.trim()")
        frame.evaluate("""() => { const r = document.createRange(); r.selectNodeContents(document.querySelector('h1'));
                                  getSelection().removeAllRanges(); getSelection().addRange(r); }""")
        pg.locator("#cbtn").wait_for(state="visible", timeout=5000)
        check("a selection in the plate shows the comment button", "Comment" in pg.locator("#cbtn").inner_text())
        pg.locator("#cbtn").click()
        pg.locator("#pop textarea").wait_for(timeout=5000)
        check("the comment box opens on the plate, and the sidebar stays closed",
              pg.evaluate("() => document.body.classList.contains('noside')"))
        pg.locator("#pop textarea").fill("Is this the right title? $x^2$")
        pg.keyboard.press(f"{MOD}+Enter")
        pg.wait_for_timeout(600)
        cs = comments()
        check("the comment goes into the comments of the plate, with the words", len(cs) == 1 and cs[0]["anchor"]["quote"] == title, cs)
        nmarks = frame.evaluate("() => CSS.highlights.get('pv-mark').size + CSS.highlights.get('pv-active').size")
        check("the plate marks the words of the comment", nmarks == 1, nmarks)
        check("the button shows the number of comments", pg.locator("#sideBtn").inner_text() == "Comments 1")

        # --- comments: on a cell of a table
        fr.locator(".pv-table tbody td").nth(1).click()
        pg.locator("#cbtn", has_text="cell").wait_for(state="visible", timeout=5000)
        pg.locator("#cbtn").click()
        pg.locator("#pop textarea").fill("Check this number.")
        pg.locator("#pop button", has_text="Comment").click()
        pg.wait_for_timeout(800)
        cs = comments()
        check("a cell comment names the data file, the row and the column", len(cs) == 2 and cs[1]["anchor"]
              and cs[1]["anchor"].get("table") == "runs.csv" and cs[1]["anchor"].get("col"), cs[-1:])
        check("the plate marks the cell with a comment", fr.locator(".pv-table td.cmt").count() == 1)

        # --- comments: about the whole plate, in the sidebar
        pg.locator("#sideBtn").click()
        pg.locator("#generalBtn").click()
        pg.locator("#threads .composer textarea").fill("A general remark.")
        pg.locator("#threads .composer button", has_text="Comment on the plate").click()
        pg.wait_for_timeout(600)
        check("a general comment shows in the sidebar", pg.locator("#threads .thread").count() == 3
              and "General comment" in pg.locator("#threads").inner_text())
        pg.locator("#threads .thread", has_text="Is this the right title").locator("q").click()
        pg.wait_for_timeout(400)
        check("a click on a comment marks its words as the active comment",
              frame.evaluate("() => CSS.highlights.get('pv-active').size") == 1)
        pg.locator("#threads .thread", has_text="A general remark").locator("button", has_text="Resolve").click()
        pg.wait_for_timeout(500)
        check("Resolve removes a comment", len(comments()) == 2)
        pg.locator("#undoBtn").click()
        pg.wait_for_timeout(500)
        check("Undo brings it back", len(comments()) == 3)
        cli("reply", pid, "1", "Yes, it is.")
        pg.locator("#threads .thread", has_text="Is this the right title").locator(".pill.answered").wait_for(timeout=6000)
        check("a reply from the command line shows in the sidebar", "Yes, it is." in pg.locator("#threads").inner_text())
        r = cli("comments", pid)
        check("plateview comments lists the threads", "#1" in r.stdout and "[answered]" in r.stdout and "#3  general" in r.stdout, r.stdout)

        # --- the keys work when the plate has the focus: the dashboard of the notes (⌘/Ctrl+Shift+.)
        fr.locator("h1").click()
        pg.keyboard.press(f"{MOD}+Shift+Period")
        pg.locator(".kitDash textarea").wait_for(timeout=5000)
        check("⌘/Ctrl+Shift+. in the plate opens the dashboard", pg.locator(".kitDash").count() == 1)
        pg.locator(".kitDash textarea").fill("The plate needs a dark grid.")
        pg.keyboard.press("Enter")
        pg.locator(".kitDashList", has_text="dark grid").wait_for(timeout=5000)
        snap = tmp / "state" / "notes" / pid / "plate.html.plate.json"
        info = json.loads(snap.read_text()) if snap.is_file() else {}
        check("a note waits in the dashboard, with a copy of the plate for its preview",
              (tmp / "state" / "notes" / pid / "plate.html").is_file() and info.get("allow") == [str(data.resolve())]
              and len(info.get("threads", [])) == 3, info)
        pg.keyboard.press("Escape")
        check("Esc closes the dashboard", pg.locator(".kitDash").count() == 0)

        # --- the chat: Claude answers, and can change the plate
        pg.locator("#chatOpen").click()
        pg.locator("#chatText").fill("Make it better EDIT")
        pg.locator("#chatSend").click()
        pg.locator(".cm.assistant", has_text="Hello from fake Claude").wait_for(timeout=15000)
        html_file = tmp / "state" / "plates" / pid / "plate.html"
        pg.wait_for_function("() => !document.querySelector('#chatSend').classList.contains('stop')", timeout=15000)
        check("the chat answers in the panel", "Done." in pg.locator("#chatMsgs").inner_text())
        check("the chat can change the HTML of the plate", "edited by fake claude" in html_file.read_text())
        log = (tmp / "state" / "plates" / pid / "chat.json")
        check("the conversation is kept with the plate", log.is_file() and "Make it better" in log.read_text())
        pg.locator("#chatMin").click()
        cli("update", pid, str(data / "demo.html"))
        fr.locator(".pv-table tbody tr").first.wait_for(timeout=10000)
        pg.wait_for_timeout(1500)

        # --- the Oracle: a background run answers the open comments
        check("the Oracle button is on while comments are open", pg.locator("#oracleBtn").is_enabled())
        pg.locator("#oracleBtn").click()
        pg.locator(".toast", has_text="answered the comments").wait_for(timeout=20000)
        cs = comments()
        check("the Oracle answers every open comment", all(t["status"] == "answered" for t in cs) and
              "fake Oracle" in pg.locator("#threads").inner_text(), [t["status"] for t in cs])
        check("the plate still marks its comments after a reload",
              frame.evaluate("() => CSS.highlights.get('pv-mark').size + CSS.highlights.get('pv-active').size") == 1
              and fr.locator(".pv-table td.cmt").count() == 1)

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
