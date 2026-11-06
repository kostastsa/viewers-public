"""Browser test of pageview: the components, sort and Copy, text and cell comments, live reload, a data file alone,
the chat and the Oracle. It uses its own server on port 8811 and a stand-in `claude` (viewkit/tests/fake_claude.py).
Needs Playwright and its Chromium, and pandoc. Run:
    uv run --with playwright python tests/browser_test.py
It deletes its temporary folder in tests/tmp at the end. Set PAGEVIEW_KEEP_TMP=1 to keep it for debugging."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PAGEVIEW = str(HERE.parent / "pageview.py")
FAKE = HERE.parent.parent / "viewkit" / "tests" / "fake_claude.py"
PORT = "8811"
TMP_ROOT = HERE / "tmp"  # git ignores it. It is inside the home folder, which is the root the server may open.
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="browser-", dir=TMP_ROOT))
bindir = tmp / "bin"
bindir.mkdir()
(bindir / "claude").write_text('#!/bin/sh\nexec python3 "%s" "$@"\n' % FAKE)
(bindir / "claude").chmod((bindir / "claude").stat().st_mode | stat.S_IEXEC)
shutil.copytree(HERE.parent / "examples", tmp / "examples")
page = tmp / "examples" / "optimizers" / "optimizers.md"
grid = tmp / "examples" / "vision_grid.csv"
side = page.parent / ".pageview" / "optimizers.md.json"
env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", PAGEVIEW_PORT=PORT, PAGEVIEW_CLAUDE=str(bindir / "claude"))
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def cli(*args):
    return subprocess.run([sys.executable, PAGEVIEW, *args], env=env, capture_output=True, text=True)


def comments():
    return json.loads(side.read_text())["threads"] if side.exists() else []


# Select the words `what` in the first text node under `sel` that holds them.
SELECT = """([sel, what]) => {
  const root = document.querySelector(sel), w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const i = n.data.indexOf(what);
    if (i < 0) continue;
    const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + what.length);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    return true;
  }
  return false;
}"""
FIRST_COL = "sel => [...document.querySelectorAll(sel + ' tbody tr')].map(tr => tr.cells[0].textContent.trim())"
T1 = '.pv-table[data-table="runs.csv"]'

try:
    url = cli(str(page), "--no-open").stdout.strip()
    check("pageview prints a link at pageview.localhost, with the access key",
          url.startswith(f"http://pageview.localhost:{PORT}/view?f=") and "&t=" in url, url)
    BASE = f"http://pageview.localhost:{PORT}"
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1400, "height": 950}, color_scheme="light")
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        pg.goto(url)
        pg.wait_for_selector(T1 + " tbody tr", timeout=20000)
        pg.wait_for_timeout(800)

        # --- the components
        check("the tab title is the name, then the context", pg.title().startswith("optimizers.md · "), pg.title())
        check("the front matter gives the title, the subtitle and the tags",
              pg.locator(".pv-head h1").inner_text() == "Optimizers on CIFAR-10" and pg.locator(".pv-head .chip").count() == 3)
        check("the callout has its kind and title", pg.locator(".pv-callout.k-result .ctitle").inner_text() == "Result")
        check("four stat cards", pg.locator(".pv-stats .stat").count() == 4 and "94.1" in pg.locator(".pv-stats .v").first.inner_text())
        check("the data table has 6 rows, sorted by accuracy", pg.evaluate(FIRST_COL, T1)[:2] == ["AdamW", "Lion"], pg.evaluate(FIRST_COL, T1))
        check("mean ± sd in one cell, and the labels", "94.1 ± 0.2" in pg.locator(T1 + " tbody tr").first.inner_text()
              and "Accuracy (%)" in pg.locator(T1 + " thead").inner_text())
        check("a ★ on the best value of each of the 3 columns", pg.locator(T1 + " td.best").count() == 3)
        check("the chart has one line for each optimizer", pg.locator(".pv-chart path.ln").count() == 3
              and pg.locator(".pv-chart .legend button").count() == 3)
        pg.locator(".pv-chart .legend button", has_text="SGD").click()
        check("a click on a legend name hides its line", pg.locator(".pv-chart path.ln").count() == 2)
        imgs = pg.evaluate("() => [...document.querySelectorAll('.pv-figs img')].map(i => i.complete && i.naturalWidth > 0)")
        check("three figures, all shown, also the PDF one", imgs == [True, True, True], imgs)
        check("tabs: three, the first shown", pg.locator(".pv-tabs .tablist button").count() == 3
              and pg.locator(".pv-tabs > .tab:not([hidden])").count() == 1)
        pg.locator(".pv-tabs .tablist button", has_text="Lion").click()
        check("a click on a tab shows its pane", "1·10⁻⁴" in pg.locator(".pv-tabs > .tab:not([hidden])").inner_text())
        check("the toggle is closed", pg.evaluate("() => !document.querySelector('details.pv-toggle').open"))
        check("columns", pg.locator(".pv-cols > .col").count() == 2)
        check("every plain Markdown table gets the table bar too", pg.locator(".pv-auto .pv-table .tbar").count() >= 1)
        check("the maths is typeset", pg.locator("#doc mjx-container").count() >= 2)
        check("code blocks get a Copy button", pg.locator("#doc pre.pv-code .cp").count() == 1)
        check("the widget runs in a sandboxed frame", pg.locator(".pv-widget iframe[sandbox='allow-scripts']").count() == 1)
        frame = pg.frame_locator(".pv-widget iframe")
        check("the script of the widget runs", frame.locator("svg path").count() == 1)
        toc = pg.locator("#toc a").all_inner_texts()
        check("the contents list the sections", toc[:3] == ["Summary", "Results table", "Training curves"], toc)

        # --- sort, filter, Copy
        pg.locator(T1 + " th", has_text="Test loss").click()
        check("a click on a column sorts it (loss: lowest first)", pg.evaluate(FIRST_COL, T1)[0] == "AdamW")
        pg.locator(T1 + " th", has_text="Optimizer").click()
        check("a text column sorts by name", pg.evaluate(FIRST_COL, T1)[0] == "Adam", pg.evaluate(FIRST_COL, T1))
        pg.locator(T1 + " input[type=search]").fill("sgd")
        check("the filter keeps the matching rows", pg.evaluate(FIRST_COL, T1) == ["SGD", "SGD + momentum"], pg.evaluate(FIRST_COL, T1))
        pg.locator(T1 + " input[type=search]").fill("")
        pg.locator(T1 + " button", has_text="LaTeX").click()
        pg.wait_for_timeout(200)
        tex = pg.evaluate("() => navigator.clipboard.readText()")
        check("Copy LaTeX gives a tabular with the best values in bold", "\\toprule" in tex and "\\textbf{$94.1 \\pm 0.2$}" in tex, tex[:300])
        pg.locator(T1 + " button", has_text="⤢ Wide").click()
        check("Wide makes the table use the full width", pg.evaluate("s => document.querySelector(s).closest('.table').classList.contains('wide')", T1))

        # --- a comment on selected words: the box opens on the page, the sidebar stays closed
        check("the sidebar is closed at the start", pg.evaluate("() => document.body.classList.contains('noside')"))
        check("words can be selected", pg.evaluate(SELECT, [".pv-callout", "best test accuracy"]))
        pg.wait_for_selector("#cbtn", state="visible", timeout=3000)
        pg.locator("#cbtn").click()
        check("the comment box opens on the page, and the sidebar stays closed",
              pg.locator("#pop").is_visible() and pg.evaluate("() => document.body.classList.contains('noside')"))
        pg.locator("#pop textarea").fill("Is the gap to Lion significant?")
        pg.locator("#pop textarea").press("Control+Enter")
        pg.wait_for_selector("#pop", state="detached", timeout=5000)
        ts = comments()
        check("the text comment is saved with its words and line", len(ts) == 1 and ts[0]["anchor"]["quote"] == "best test accuracy"
              and ts[0]["anchor"].get("line") == 14, ts)  # the line of the words
        check("the words are highlighted", pg.evaluate("() => CSS.highlights.get('pv-active').size + CSS.highlights.get('pv-mark').size") == 1)

        # --- a comment on a table cell
        pg.locator(T1 + " td[data-row='Lion'][data-col='acc']").click()
        pg.wait_for_selector("#cbtn", state="visible", timeout=3000)
        check("a click on a cell offers a cell comment, and tells the chat", "cell" in pg.locator("#cbtn").inner_text()
              and "Lion" in pg.locator("#chatCtx").inner_text())
        pg.locator("#cbtn").click()
        check("the box names the cell", "Lion · acc" in pg.locator("#pop").inner_text())
        pg.locator("#pop textarea").fill("Rerun with 10 seeds?")
        pg.locator("#pop button", has_text="Comment").click()
        pg.wait_for_selector("#pop", state="detached", timeout=5000)
        ts = comments()
        check("the cell comment is saved", len(ts) == 2 and ts[1]["anchor"] == {"table": "runs.csv", "row": "Lion", "col": "acc"}, ts)
        check("the cell shows a comment mark", pg.locator(T1 + " td.cmt[data-row='Lion']").count() == 1)

        # --- a comment from the command line, the sidebar, reply, resolve, undo
        r = cli("add", str(page), "Added from outside", "--quote", "SGD is the fastest per epoch")
        check("pageview add works while the server runs", r.returncode == 0, r.stderr)
        pg.wait_for_function("() => document.getElementById('sideBtn').textContent === 'Comments 3'", timeout=8000)
        check("the page shows a comment from outside within a few seconds", True)
        pg.locator("#sideBtn").click()
        check("the sidebar lists the 3 comments, the text comments first", pg.locator(".thread").count() == 3
              and "▦ runs.csv" in pg.locator(".thread").last.inner_text())
        pg.locator('.thread[data-t="1"] button', has_text="Reply").click()
        pg.locator('.thread[data-t="1"] .composer textarea').fill("Yes, p = 0.01.")
        pg.locator('.thread[data-t="1"] .composer button', has_text="Reply").click()
        pg.wait_for_function("() => document.querySelector('.thread[data-t=\"1\"]').innerText.includes('p = 0.01')", timeout=5000)
        check("a reply is saved", [m["text"] for m in comments()[0]["msgs"]] == ["Is the gap to Lion significant?", "Yes, p = 0.01."])
        pg.locator('.thread[data-t="2"] button', has_text="Resolve").click()
        pg.wait_for_selector('.thread[data-t="2"]', state="detached", timeout=5000)
        check("resolve removes the cell comment and its mark", len(comments()) == 2 and pg.locator(T1 + " td.cmt").count() == 0)
        pg.locator(".toast button", has_text="Undo").first.click()
        pg.wait_for_selector('.thread[data-t="2"]', timeout=5000)
        check("Undo brings it back", len(comments()) == 3 and pg.locator(T1 + " td.cmt").count() == 1)
        pg.locator('.thread[data-t="2"] q').click()
        pg.wait_for_timeout(300)
        check("a click on a cell comment marks its cell", pg.locator(T1 + " td.cact").count() == 1)

        # --- live reload: a data file changes on disk
        rows = (page.parent / "runs.csv").read_text().replace("AdamW,1e-3,94.1", "AdamW,1e-3,95.5")
        (page.parent / "runs.csv").write_text(rows)
        pg.wait_for_function(f"() => (document.querySelector('{T1} tbody') || {{}}).innerText?.includes('95.5')", timeout=8000)
        check("the table updates when its data file changes", True)

        # --- the chat
        pg.locator("#chatOpen").click()
        pg.locator("#chatText").fill("hello")
        pg.locator("#chatText").press("Enter")
        pg.wait_for_function("() => document.querySelector('#chatMsgs').innerText.includes('Hello from fake Claude')", timeout=10000)
        check("Claude answers in the chat", True)
        pg.locator("#chatText").fill("please EDIT the page")
        pg.locator("#chatText").press("Enter")
        pg.wait_for_function("() => document.querySelector('#doc').innerText.includes('edited by fake claude')", timeout=10000)
        check("when Claude changes the page, the page shows the change", True)
        pg.locator("#chatAsk").check()
        before = page.read_text()
        pg.locator("#chatText").fill("EDIT again")
        pg.locator("#chatText").press("Enter")
        pg.wait_for_function("() => document.querySelector('#chatSend').textContent === 'Send'", timeout=10000)
        pg.wait_for_timeout(500)
        check("Ask only does not change the page", page.read_text() == before)
        pg.locator("#chatAsk").uncheck()
        pg.locator("#chatMin").click()

        # --- the Oracle (the stand-in only prints a report for a page)
        check("the Oracle button is ready when there are open comments", pg.locator("#oracleBtn").is_enabled())
        pg.locator("#oracleBtn").click()
        pg.wait_for_function("() => document.querySelector('#oracleBtn').textContent.startsWith('Working')", timeout=5000)
        pg.wait_for_function("() => document.querySelector('#oracleBtn').textContent === 'Oracle'", timeout=15000)
        pg.wait_for_function("() => document.getElementById('toast').innerText.includes('Claude has answered the comments')", timeout=5000)
        check("the Oracle runs and ends with a report", True)

        # --- theme
        pg.locator("#themeBtn").click()
        check("the theme button sets light, then dark", pg.evaluate("() => document.documentElement.dataset.theme") == "light")
        pg.locator("#themeBtn").click()
        check("dark", pg.evaluate("() => document.documentElement.dataset.theme") == "dark")
        pg.locator("#themeBtn").click()

        check("no errors in the console (page)", not errs, errs)

        # --- a data file alone: the grid
        gurl = cli(str(grid), "--no-open").stdout.strip()
        pg.goto(gurl)
        pg.wait_for_selector(".pv-grid tbody tr", timeout=15000)
        check("a data file opens as one table that fills the window", pg.evaluate("() => document.body.classList.contains('datadoc')"))
        check("the grid: 10 models, 10 datasets, two column groups", pg.locator(".pv-grid tbody tr").count() == 10
              and pg.locator(".pv-grid thead tr.g th[colspan]").count() == 3, pg.locator(".pv-grid thead tr.g").inner_text())
        check("the title comes from vision_grid.view.json", "Top-1 accuracy" in pg.locator(".pv-grid .ttl").inner_text())
        check("a ★ in each column of the grid", pg.locator(".pv-grid td.best").count() == 10)
        check("a missing run shows —", pg.locator(".pv-grid td.na").count() == 1)
        pg.locator(".pv-grid button", has_text="Transpose").click()
        check("Transpose: the datasets become the rows", pg.locator(".pv-grid tbody tr").count() == 10
              and "MNIST" in pg.locator(".pv-grid tbody").inner_text() and "TRANSFORMER" in pg.locator(".pv-grid thead tr.g").inner_text().upper())
        pg.locator(".pv-grid td[data-r='Flowers-102'][data-c='Swin-T']").click()
        pg.wait_for_selector("#cbtn", state="visible", timeout=3000)
        pg.locator("#cbtn").click()
        pg.locator("#pop textarea").fill("Why so high?")
        pg.locator("#pop textarea").press("Control+Enter")
        pg.wait_for_selector("#pop", state="detached", timeout=5000)
        gs = json.loads((grid.parent / ".pageview" / "vision_grid.csv.json").read_text())["threads"]
        check("a cell comment in a transposed grid keeps the model as the row", gs[0]["anchor"] ==
              {"table": "vision_grid.csv", "row": "Swin-T", "col": "Flowers-102"}, gs)
        pg.locator(".pv-grid button", has_text="Transpose").click()
        check("the comment mark stays on its cell after a transpose", pg.locator(".pv-grid td.cmt[data-r='Swin-T'][data-c='Flowers-102']").count() == 1)

        # --- the rules of the server
        r = pg.request.get(f"{BASE}/widget?f=" + str(page.relative_to(Path.home())) + "&i=0")
        check("the widget route is a sandbox with no network", r.status == 200 and "sandbox allow-scripts" in r.headers.get("content-security-policy", "")
              and "connect-src" not in r.headers.get("content-security-policy", "") and "default-src 'none'" in r.headers.get("content-security-policy", ""))
        (page.parent / ".hidden.png").write_bytes(b"x")
        r = pg.request.get(f"{BASE}/file?f=" + str(page.relative_to(Path.home())) + "&src=.hidden.png")
        check("a hidden file is never served", r.status == 404, r.status)
        other = tmp / "other.md"
        other.write_text("# Other\n")
        r = pg.request.get(f"{BASE}/api/doc?f=" + str(other.relative_to(Path.home())))
        check("a page that was not passed to pageview does not open", r.status == 403, r.status)
        r = pg.request.get(f"{BASE}/api/data?f=" + str(page.relative_to(Path.home())) + "&src=../../../../../../../../etc/hosts")
        check("a data path outside the project is refused", r.status == 404, r.status)
        r = pg.goto(f"{BASE}/?d=" + str((tmp / "examples").relative_to(Path.home())))
        check("a folder that was not passed to pageview does not open", r.status == 403, r.status)
        cli(str(tmp / "examples"), "--no-open")
        pg.goto(f"{BASE}/?d=" + str((tmp / "examples").relative_to(Path.home())))
        body = pg.inner_text("body")
        check("the folder page lists the data files and folders", "vision_grid.csv" in body and "optimizers" in body
              and "vision_grid.view.json" not in body, body)
        b.close()
finally:
    subprocess.run([sys.executable, PAGEVIEW, "--stop"], env=env, capture_output=True)
    if os.environ.get("PAGEVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
