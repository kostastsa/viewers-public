"""Smoke test of the sample PDFs (samples/): each one opens in the viewer and draws, with no error on the page.
It is the baseline of the automatic loop (improve.json "tests"): a proposal that breaks one kind of PDF stops here.
It uses copies of the samples, its own server on port 8813 and a stand-in claude. Needs Playwright and pdfinfo. Run:
    uv run --with playwright python tests/samples_test.py
It deletes its folder in tests/tmp at the end."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PDFVIEW = str(REPO / "pdfview.py")
PORT = "8813"
SAMPLES = json.loads((REPO / "improve.json").read_text())["samples"]
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="samples-", dir=TMP_ROOT))
env = dict(os.environ, PDFVIEW_PORT=PORT, PDFVIEW_CLAUDE="/bin/false", VIEWKIT_AUTO_IMPROVE="0",
           VIEWKIT_IMPROVE_DIR=str(tmp / "improve"))
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def pages(p):
    out = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True).stdout
    return next((int(x.split()[-1]) for x in out.splitlines() if x.startswith("Pages:")), 0)


try:
    for rel in SAMPLES:  # copies, with their comments
        src = REPO / rel
        shutil.copy2(src, tmp / src.name)
        side = src.parent / ".pdfview" / (src.name + ".json")
        if side.is_file():
            (tmp / ".pdfview").mkdir(exist_ok=True)
            shutil.copy2(side, tmp / ".pdfview" / side.name)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for rel in SAMPLES:
            name = Path(rel).name
            pg = b.new_page(viewport={"width": 1300, "height": 900})  # one page each, so the errors are of one sample
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            url = subprocess.run([sys.executable, PDFVIEW, str(tmp / name), "--no-open"], env=env, capture_output=True,
                                 text=True).stdout.strip().split()[-1]
            pg.goto(url)
            pg.wait_for_selector('.page[data-n="1"] canvas.base', timeout=30000)
            pg.wait_for_timeout(500)
            n = pages(tmp / name)
            check(f"{name}: one page box for each page ({n})", pg.locator(".page").count() == n, pg.locator(".page").count())
            text = pg.locator('.page[data-n="1"] .textLayer').inner_text()
            if name == "scan.pdf":
                check(f"{name}: a scanned page has no text layer", not text.strip(), text[:80])
            else:
                check(f"{name}: the text layer has the words of page 1", len(text.strip()) > 40, text[:80])
            if name == "paper.pdf":
                check(f"{name}: the sidebar shows the three comment threads", pg.locator("#threads .thread").count() == 3,
                      pg.locator("#threads .thread").count())
                k = pg.evaluate("() => (pv.pages[0].links || []).length")
                check(f"{name}: the viewer finds the links inside the PDF on page 1", k > 3, k)
                pg.locator("#navBtn").click()
                check(f"{name}: the contents list the sections", "Introduction" in pg.inner_text("#outline"),
                      pg.inner_text("#outline")[:200])
                pg.locator("#navBtn").click()
            check(f"{name}: no errors on the page", not errs, errs)
            pg.close()
        b.close()
finally:
    subprocess.run([sys.executable, PDFVIEW, "--stop"], env=env, capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
