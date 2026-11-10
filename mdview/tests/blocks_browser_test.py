"""Browser test of the rich blocks and the tables of mdview: the blocks of viewkit/components.js, the Markdown tables in
a card (number columns, best values, sort of the view), comments and changes in tables and blocks, and the grid editor
of a table (cells, rows, Markdown…, Undo). Its own server on port 8835, on a copy of samples/blocks.md.
Needs Playwright and its Chromium, and pandoc. Run:
    uv run --with playwright python tests/blocks_browser_test.py
It makes its temporary folder in tests/tmp and deletes it at the end. Set MDVIEW_KEEP_TMP=1 to keep it for debugging."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
MDVIEW = str(HERE.parent / "mdview.py")
PORT = "8835"
TMP_ROOT = HERE / "tmp"  # git ignores it. It is inside the home folder, which is the root the server may open.
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="blocks-", dir=TMP_ROOT))
for f in ("blocks.md", "runs.csv"):
    shutil.copy(HERE.parent / "samples" / f, tmp / f)
md = tmp / "blocks.md"
# a tracked change in a cell, and a table in a quote (not a plain pipe table: it opens as text)
src = md.read_text().replace("|         93.4 |", "| {~~93.4~>93.5~~} |", 1)
src += "\n> | a | b |\n> |---|---|\n> | 1 | 2 |\n"
md.write_text(src)
env = dict(os.environ, MDVIEW_PORT=PORT, VIEWKIT_ART=str(tmp / "no-art"))
url = subprocess.run([sys.executable, MDVIEW, str(md), "--no-open"], env=env, capture_output=True, text=True,
                     check=True).stdout.strip().split()[-1]
MOD = "Meta" if sys.platform == "darwin" else "Control"
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def wait_file(cond, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if cond(md.read_text()):
            return True
        time.sleep(0.1)
    return False


# Select the words `what` in the first text node under `sel` that holds them (the words are in one span).
SELECT = """([sel, what]) => {
  for (const root of document.querySelectorAll(sel)) {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), nodes = [];
    for (let n = w.nextNode(); n; n = w.nextNode()) nodes.push(n);
    const text = nodes.map(n => n.data).join(''), i = text.indexOf(what);
    if (i < 0) continue;
    const at = k => { let s = 0; for (const n of nodes) { if (k <= s + n.length) return [n, k - s]; s += n.length; } };
    const [a, ao] = at(i), [b, bo] = at(i + what.length);
    a.parentElement.scrollIntoView({block: 'center'});
    const r = document.createRange(); r.setStart(a, ao); r.setEnd(b, bo);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    document.dispatchEvent(new Event('selectionchange'));
    return true;
  }
  return false;
}"""


def comment(pg, sel, words, text):
    check(f"the words “{words}” can be selected", pg.evaluate(SELECT, [sel, words]))
    pg.keyboard.press(f"{MOD}+Shift+M")  # the comment key of mdview
    pg.locator("#dlg[open]").wait_for(timeout=3000)
    pg.locator("#ta").fill(text)
    pg.locator("#ta").press(f"{MOD}+Enter")


try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1500, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        pg.goto(url)
        pg.wait_for_selector(".mdt", timeout=20000)
        T1, T2 = pg.locator(".mdt").nth(0), pg.locator(".mdt").nth(1)

        # --- the tables in a card
        check("each Markdown table is in a card with a toolbar (also the one in a quote)",
              pg.locator(".mdt").count() == 3 and pg.locator(".mdt > .mdt-bar").count() == 3)
        check("the table card is a block of the file: it has the block buttons",
              T1.evaluate("e => e.parentElement.id === 'doc' && !!e.dataset.pos && !!e.querySelector('.eblk') && !!e.querySelector('.xblk')"))
        check("the number columns are marked (right aligned)",
              T2.locator("td.num").count() == 6 and T2.locator("td:not(.num)").count() == 3)
        check("best=\"Accuracy:max Loss:min\" marks the best cells",
              T2.locator("td.best").all_inner_texts() == ["95.0", "0.198"], T2.locator("td.best").all_inner_texts())
        check("a comment in a table cell is highlighted", T1.locator("mark.c").count() >= 1)
        check("a tracked change in a table cell shows as del and ins",
              "".join(T1.locator("del.sg").all_inner_texts()) == "93.4" and "".join(T1.locator("ins.sg").all_inner_texts()) == "93.5")

        # --- the blocks of components.js
        check("callout, stat cards, columns, tabs, toggle and code with Copy",
              pg.locator(".pv-callout").count() == 1 and pg.locator(".pv-stats .stat").count() == 3
              and pg.locator(".pv-cols .col").count() == 2 and pg.locator(".pv-tabs").count() == 1
              and pg.locator("details.pv-toggle").count() == 1 and pg.locator("pre.pv-code .cp").count() == 1)
        stat = pg.locator(".pv-stats .stat").first
        check("a stat card keeps the words of the file (label, value, note)",
              stat.locator(".l").inner_text().strip().lower() == "best accuracy" and stat.locator(".v").inner_text().strip() == "94.1 %"
              and stat.locator(".s").inner_text().strip() == "AdamW" and stat.locator("span[data-pos]").count() > 3,
              stat.inner_text())
        pg.locator(".pv-table tbody tr").first.wait_for(timeout=5000)
        check("a .table block reads its CSV file (sd, best)", pg.locator(".pv-table tbody tr").count() == 9
              and pg.locator(".pv-table td.best").count() == 2)
        pg.locator(".pv-tabs .tablist button", has_text="Chart").click()
        pg.locator(".pv-chart svg").first.wait_for(timeout=5000)
        check("a .chart block draws its CSV file", pg.locator(".pv-chart svg path").count() > 2)
        base = url.split("/view")[0]
        rel = str(md.relative_to(Path.home()))
        r = pg.request.get(f"{base}/api/data?f={rel}&src=../../mdview.py").status  # the cookie of the page, no console error
        check("the data route reads only the data files of the document", r == 404, r)

        # --- comments on words in a table cell and in a callout: CriticMarkup in the file, at the right place
        comment(pg, ".mdt", "seed 3 diverged", "Which seed?")
        check("a comment on words in a table cell goes into the cell of the file",
              wait_file(lambda s: "| {==seed 3 diverged==}{>>" in s and "Which seed?" in s), md.read_text()[-600:])
        pg.locator(".mdt mark.c", has_text="diverged").wait_for(timeout=5000)
        comment(pg, ".pv-callout", "best accuracy", "On which split?")
        check("a comment on words in a callout goes into the block of the file",
              wait_file(lambda s: "AdamW gives the {==best accuracy==}{>>" in s))

        # --- sort: the view only
        before = md.read_text()
        T1 = pg.locator(".mdt").nth(0)
        T1.locator("thead th").nth(2).hover()
        T1.locator("thead th").nth(2).locator(".srt").click()
        T1.locator("thead th").nth(2).locator(".srt").click()
        first = T1.locator("tbody tr td:first-child").all_inner_texts()
        check("↕ sorts the rows by the column (down on the second click)", first[0] == "AdamW" and first[-1] == "Adam", first)
        check("the sort does not change the file", md.read_text() == before)
        check("the toolbar tells that the sort is of the view", "view only" in T1.locator(".mdt-bar").inner_text())

        # --- the grid editor
        T1.locator("td", has_text="rerun planned").dblclick()
        pg.locator(".tedit textarea").first.wait_for(timeout=5000)
        check("a double-click on a cell opens the grid editor on that cell (in the sorted view too)",
              pg.evaluate("document.activeElement.value") == "rerun planned")
        check("each cell of the grid holds its Markdown, also CriticMarkup",
              pg.locator(".tedit textarea").evaluate_all("as => as.some(a => a.value === '{~~93.4~>93.5~~}')"))
        pg.keyboard.press(f"{MOD}+A")
        pg.keyboard.type("rerun done")
        pg.keyboard.press("Tab")
        check("Tab goes to the next cell", pg.evaluate("document.activeElement.dataset.c") == "0"
              and pg.evaluate("document.activeElement.value") == "SGD + momentum")
        pg.locator(".tedit button", has_text="+ Row").click()
        pg.keyboard.type("Muon")
        pg.keyboard.press("Tab")
        pg.keyboard.type("0.02")
        pg.locator(".tedit tr.ctool td").nth(5).locator("button").first.click()  # Notes: no alignment -> left
        pg.keyboard.press(f"{MOD}+Enter")
        ok = wait_file(lambda s: "rerun done" in s and "| Muon " in s)
        s = md.read_text()
        lines = [ln for ln in s.splitlines() if ln.startswith("| ") and ("Optimizer" in ln or "Muon" in ln or ":--" in ln)]
        check("Save writes the cells, the new row and the alignment as a pipe table", ok, s[:900])
        check("the columns of the written table are aligned", len({len(ln) for ln in lines[:3]}) == 1, lines)
        check("the comment and the change in the cells stay in the file",
              "{==0.248==}{>>@you: Is this loss correct?<<}" in s and "{~~93.4~>93.5~~}" in s and "{==seed 3 diverged==}" in s)
        check("the other blocks of the file do not change", s.split("::: {.table best=")[1] == before.split("::: {.table best=")[1])
        pg.locator("#toast button", has_text="Undo").first.click()
        check("Undo takes the table back", wait_file(lambda x: x == before), md.read_text()[:600])

        # ✎ on the card opens the grid editor, Esc closes it, Markdown… edits the text
        pg.locator(".mdt").first.wait_for()
        T1 = pg.locator(".mdt").nth(0)
        T1.hover()
        T1.locator(":scope > .eblk").click()
        pg.locator(".tedit textarea").first.wait_for(timeout=5000)
        pg.keyboard.press("Escape")
        check("✎ opens the grid editor, and Esc closes it without a change",
              pg.locator(".tedit").count() == 0 and md.read_text() == before)
        pg.locator(".mdt").nth(1).locator(".mdt-bar button", has_text="Edit").click()
        pg.locator(".tedit button", has_text="Markdown…").click()
        raw = pg.locator(".tedit textarea.raw")
        check("Markdown… shows the text of the table", raw.input_value().startswith("| Model"), raw.input_value()[:80])
        raw.fill(raw.input_value().replace("ViT-S", "ViT-B"))
        pg.locator(".tedit button", has_text="Save").click()
        check("the text of the table is saved as it is", wait_file(lambda x: "| ViT-B " in x))
        # a table that is not a plain pipe table opens as text
        pg.locator(".mdt").nth(2).locator("td", has_text="1").dblclick()
        pg.locator(".block-edit textarea").wait_for(timeout=5000)
        check("a table in a quote opens as Markdown text", "|---|---|" in pg.locator(".block-edit textarea").input_value())
        pg.keyboard.press("Escape")

        # --- folding still hides the blocks of a section
        pg.locator("#doc h2", has_text="Results").locator(".fold").click()
        check("folding a section hides its tables and blocks",
              pg.locator(".mdt").first.evaluate("e => e.classList.contains('folded-away')")
              and pg.locator(".pv-callout").evaluate("e => e.classList.contains('folded-away')"))
        pg.locator("#doc h2", has_text="Results").locator(".fold").click()

        # --- the fonts (Aa): the look of pageview by default, a choice for all files that stays after a reload
        font = "(sel) => getComputedStyle(document.querySelector(sel)).fontFamily"
        check("by default: sans text, serif headings and a sans interface, as in pageview",
              pg.evaluate(font, "#doc").startswith("system-ui") and pg.evaluate(font, "#doc h2").startswith('"Iowan Old Style"')
              and pg.evaluate(font, "header").startswith("system-ui"))
        accent = pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--accent')")
        pg.locator("#fontBtn").click()
        pg.locator("#fontMenu .seg button", has_text="Serif").first.click()
        pg.locator("#fontMenu .seg button", has_text="Mono").last.click()
        pg.locator("#fontMenu .seg button", has_text="A+").click()
        check("the menu stays open while you choose", pg.locator("#fontMenu").is_visible())
        pg.keyboard.press("Escape")
        check("Esc closes the font menu", pg.locator("#fontMenu").is_hidden())
        check("Serif, Mono and A+ change the text and the interface",
              pg.evaluate(font, "#doc").startswith("Charter") and pg.evaluate(font, "header").startswith("ui-monospace")
              and pg.evaluate("getComputedStyle(document.getElementById('doc')).fontSize") == "19px")
        pg.reload()
        pg.wait_for_selector(".mdt", timeout=20000)
        check("the choice of fonts stays after a reload, and the color of the file stays",
              pg.evaluate(font, "#doc").startswith("Charter")
              and pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--accent')") == accent)
        pg.locator("#fontBtn").click()
        pg.locator("#fontMenu button", has_text="Default (as pageview)").click()
        check("Default goes back to the look of pageview", pg.evaluate(font, "#doc").startswith("system-ui"))
        check("no errors in the page", not errs, errs)
        b.close()
finally:
    subprocess.run([sys.executable, MDVIEW, "--stop"], env=env, capture_output=True)
    if os.environ.get("MDVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
