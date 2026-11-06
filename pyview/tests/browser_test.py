"""Browser test of the viewer, with Playwright. Needs a Python with `playwright` installed and its Chromium:

    uv venv /tmp/pw && uv pip install --python /tmp/pw/bin/python playwright && /tmp/pw/bin/python -m playwright install chromium
    /tmp/pw/bin/python tests/browser_test.py

It copies tests/fixture.py to a temporary folder in tests/tmp, so it never touches the original.
It deletes this folder at the end. Set PYVIEW_KEEP_TMP=1 to keep it (and final.png) for debugging.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
# Under tests/tmp (git ignores it): inside the home folder, which is the root the server may open.
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="browser-", dir=TMP_ROOT))
nb = tmp / "nb.py"
shutil.copy(HERE / "fixture.py", nb)  # its own copy, so tests/demo.py can be edited freely
url = subprocess.run(["pyview", str(nb), "--no-open"], capture_output=True, text=True, check=True).stdout.strip()
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def text():
    return nb.read_text()


def write_in_editor(pg, content):
    """Replace the text of the open cell editor (CodeMirror) the way a paste does."""
    loc = pg.locator(".cm-content")
    loc.click()
    pg.keyboard.press("ControlOrMeta+A")
    pg.keyboard.insert_text(content)
    return loc


try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 1000})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(url)
        pg.wait_for_selector(".cell")
        pg.wait_for_timeout(500)

        check("the header says which computer serves the page", pg.locator("#hostb").inner_text() == "on " + socket.gethostname().split(".")[0],
              pg.locator("#hostb").inner_text())
        stray = [w for w in ("null", "undefined", "NaN") if w in pg.locator("#cells").inner_text()]
        check("no stray null/undefined text in the cells", not stray, str(stray))

        # --- contents on the left
        toc = pg.locator("#toc a")
        check("contents lists the heading and the titled code cells", toc.count() >= 6 and pg.locator("#toc").is_visible(), str(toc.all_inner_texts()))
        check("contents indents code cells under the heading", pg.locator("#toc a.top").count() == 1 and pg.locator("#toc a.code").count() >= 5)
        pg.locator("#toc a", has_text="An error").click()
        pg.wait_for_timeout(500)
        check("a click on a contents entry scrolls to the cell and highlights it",
              pg.locator("#toc a.active", has_text="An error").count() == 1
              and pg.eval_on_selector('.cell[data-idx="6"]', "e => e.getBoundingClientRect().top") < 200)
        pg.locator("#tocHide").click()
        check("« hides the contents and ☰ shows them again", not pg.locator("#toc").is_visible() and pg.locator("#tocShow").is_visible())
        pg.locator("#tocShow").click()
        check("contents are back", pg.locator("#toc").is_visible())
        pg.evaluate("window.scrollTo(0, 0)")
        check("the header button says Oracle", pg.locator("#claude").inner_text() == "Oracle")

        # --- the bin button deletes a cell
        n0 = pg.locator(".cell").count()
        pg.locator('.cell[data-idx="7"]').hover()
        pg.locator('.cell[data-idx="7"] .chead button', has_text="🗑").click()
        pg.wait_for_timeout(500)
        check("the bin button deletes the cell", pg.locator(".cell").count() == n0 - 1 and "After the error" not in text())
        pg.locator(".toast button", has_text="Undo").click()
        pg.wait_for_timeout(500)
        check("and Undo brings it back", pg.locator(".cell").count() == n0 and "After the error" in text())

        # --- run a cell from the viewer
        pg.locator('.cell[data-idx="2"]').hover()
        pg.locator('.cell[data-idx="2"] .chead button', has_text="▶").click()
        pg.wait_for_selector('.cell[data-idx="2"] .outs pre', timeout=30000)
        check("run cell 2 shows its output", "x has 200 values" in pg.locator('.cell[data-idx="2"] .outs').inner_text())

        # --- edit cell 5 in the viewer (double-click), run with Shift+Enter
        pg.locator('.cell[data-idx="5"] .src').dblclick()
        check("double-click opens CodeMirror", pg.locator(".cm-editor").count() == 1)
        write_in_editor(pg, "print('edited in the viewer')\nv = 6 * 7\nv").press("Shift+Enter")
        pg.wait_for_selector('.cell[data-idx="5"] .outs pre.res', timeout=30000)
        check("Shift+Enter runs the edited cell", "Out: 42" in pg.locator('.cell[data-idx="5"] .outs').inner_text()
              or "42" in pg.locator('.cell[data-idx="5"] .outs .res').inner_text())
        check("the edit is in the file", "print('edited in the viewer')" in text())

        # --- outside edit: a cell changed on disk is marked "edited since run"
        pg.wait_for_function("() => { const c = document.querySelector('.cell[data-idx=\"5\"]'); return c && !c.classList.contains('running') && /\\[\\d+\\]/.test(c.querySelector('.count').textContent); }", timeout=30000)
        nb.write_text(text().replace("v = 6 * 7", "v = 6 * 8"))
        pg.wait_for_timeout(2500)
        check("edit on disk marks the cell stale", pg.locator('.cell[data-idx="5"] .badge.edited').count() == 1)

        # --- comment on a code line, reply, resolve
        pg.locator('.cell[data-idx="2"] .ln').nth(5).hover()
        pg.locator('.cell[data-idx="2"] .ln').nth(5).locator("button.add").click()
        pg.locator(".lcomp textarea").fill("why 200 values?")
        pg.locator(".lcomp textarea").press("Control+Enter")
        pg.wait_for_selector(".thread")
        check("line comment is written above the line", "#> why 200 values?\nx = rng.normal" in text(), text()[:300])
        check("a thread card appears", pg.locator(".thread").count() == 1)
        pg.locator(".thread .tacts button", has_text="Reply").click()
        pg.locator(".thread textarea").fill("thanks")
        pg.locator(".thread textarea").press("Control+Enter")
        pg.wait_for_selector(".thread .msg:nth-of-type(3)")
        check("reply is written as @you", "#> @you: thanks" in text())
        nb.write_text(text().replace("#> @you: thanks", "#> @you: thanks\n#> @claude: it is a demo"))
        pg.wait_for_timeout(2500)
        check("claude's reply shows as answered", pg.locator(".thread.answered").count() == 1)
        pg.locator(".thread .tacts button", has_text="Resolve").click()
        pg.wait_for_timeout(500)
        check("resolve removes the comment lines", "#>" not in text())
        pg.locator(".toast button", has_text="Undo").click()
        pg.wait_for_timeout(500)
        check("undo brings the comment back", "#> why 200 values?" in text())

        # --- general comment
        pg.locator("#gcomment").click()
        pg.locator("#threads textarea").fill("general remark")
        pg.locator("#threads textarea").press("Control+Enter")
        pg.wait_for_timeout(500)
        check("general comment is at the top", text().startswith("#> general remark\n\n"), text()[:80])

        # --- the comments panel: hide and show, resolve all
        check("the Comments button shows the count", pg.locator("#sideBtn").inner_text().startswith("Comments ("), pg.locator("#sideBtn").inner_text())
        pg.locator("#sideBtn").click()
        check("Comments hides the panel", not pg.locator("#threads").is_visible())
        pg.locator("#sideBtn").click()
        check("and shows it again", pg.locator("#threads").is_visible())
        pg.locator("#sideBtn").click()
        pg.locator("#gcomment").click()
        check("opening a comment box shows the hidden panel", pg.locator("#threads").is_visible() and pg.locator("#threads textarea").count() == 1)
        pg.locator("#threads textarea").press("Escape")
        n_threads = pg.locator(".thread").count()
        pg.once("dialog", lambda d: d.dismiss())
        pg.locator("#threads h4 button", has_text="Resolve all").click()
        pg.wait_for_timeout(300)
        check("Resolve all asks first, and Cancel keeps everything", "#>" in text() and pg.locator(".thread").count() == n_threads)
        pg.once("dialog", lambda d: d.accept())
        pg.locator("#threads h4 button", has_text="Resolve all").click()
        pg.wait_for_timeout(600)
        check("Resolve all removes every comment and keeps the code", "#>" not in text() and "x = rng.normal" in text() and pg.locator(".thread").count() == 0)
        pg.locator(".toast button", has_text="Undo").click()
        pg.wait_for_timeout(600)
        check("one Undo brings them all back", pg.locator(".thread").count() == n_threads and "#> why 200 values?" in text())

        # --- add a markdown cell at the end, write maths
        pg.locator(".gap.end button", has_text="+ Markdown").click()
        pg.wait_for_selector(".cm-editor")
        write_in_editor(pg, "## New section\n\nSome $a^2+b^2=c^2$ text.").press("Shift+Enter")
        pg.wait_for_selector("mjx-container", timeout=10000)
        check("markdown cell written with the # prefix", "# %% [markdown]\n# ## New section\n#\n# Some $a^2+b^2=c^2$ text." in text(), text()[-200:])

        # --- typing in the editor: brackets close, a colon indents the next line
        pg.locator('.cell[data-idx="4"] .src').dblclick()
        pg.locator(".cm-content").click()
        pg.keyboard.press("ControlOrMeta+A")
        pg.keyboard.type("for i in range(2):\nprint(i)")
        check("editor closes brackets and indents after a colon",
              pg.locator(".cm-content").inner_text().replace("\u00a0", " ") == "for i in range(2):\n    print(i)",
              repr(pg.locator(".cm-content").inner_text()))
        check("editor highlights Python", pg.locator(".cm-content span[class]").count() > 3)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(600)
        check("Esc saves the edit and leaves the editor", pg.locator(".cm-editor").count() == 0 and "for i in range(2):\n    print(i)" in text())
        # a cell that has run: Esc keeps the edit and does not run it again
        pg.locator('.cell[data-idx="2"] .src').dblclick()
        pg.locator(".cm-content").click()
        pg.keyboard.press("ControlOrMeta+End")
        pg.keyboard.type("\n# a note")
        count_before = pg.locator('.cell[data-idx="2"] .count').inner_text()
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(700)
        check("Esc does not run the cell: it shows 'edited since run' and keeps its old output and counter",
              pg.locator('.cell[data-idx="2"] .badge.edited').count() == 1
              and "x has 200 values" in pg.locator('.cell[data-idx="2"] .outs').inner_text()
              and pg.locator('.cell[data-idx="2"] .count').inner_text() == count_before, count_before)
        # opening another cell keeps the edit of the first one
        pg.locator('.cell[data-idx="5"] .src').dblclick()
        pg.locator(".cm-content").click()
        pg.keyboard.press("ControlOrMeta+End")
        pg.keyboard.type("\n# kept when I moved on")
        pg.locator('.cell[data-idx="3"] .src').dblclick()
        pg.wait_for_timeout(700)
        check("moving to another cell saves the first edit", "# kept when I moved on" in text() and pg.locator(".cm-editor").count() == 1)
        pg.locator(".cm-content").click()
        pg.keyboard.type("zzz")
        pg.locator(".editor .bar button", has_text="Discard").click()
        pg.wait_for_timeout(400)
        check("Discard throws the edit away", pg.locator(".cm-editor").count() == 0 and "zzz" not in text())
        pg.locator('.cell[data-idx="3"] .src').dblclick()
        pg.locator(".editor .bar").get_by_role("button", name="Save", exact=True).click()
        pg.wait_for_timeout(400)
        check("Save leaves an unchanged cell alone", pg.locator(".cm-editor").count() == 0)

        # --- rename a cell from the menu
        pg.locator('.cell[data-idx="1"] .chead button', has_text="⋯").click(force=True)
        pg.locator(".menu button", has_text="Rename").click()
        pg.wait_for_timeout(200)
        check("Rename opens a field with the title", pg.locator(".chead input.rename").input_value() == "Setup")
        pg.keyboard.type("Prepare data")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(500)
        check("Enter in the field writes the new title to the file", "# %% Prepare data\n" in text() and pg.locator(".chead input.rename").count() == 0
              and pg.locator('.cell[data-idx="1"] .chead .title').inner_text() == "Prepare data")
        pg.locator("#undo").click()
        pg.wait_for_timeout(500)
        check("Undo restores the old title", "# %% Setup\n" in text())

        # --- delete a cell and undo
        pg.wait_for_timeout(1000)  # Shift+Enter on the last cell adds an empty code cell, as in Jupyter
        n_before = pg.locator(".cell").count()
        pg.locator('.cell[data-idx="6"] .chead button', has_text="⋯").click(force=True)
        pg.locator(".menu button", has_text="Delete cell").click()
        pg.wait_for_timeout(500)
        check("delete removes the cell", pg.locator(".cell").count() == n_before - 1,
              f"{pg.locator('.cell').count()} vs {n_before}; toasts={pg.locator('.toast').all_inner_texts()}; errs={errs}")
        pg.locator("#undo").click()
        pg.wait_for_timeout(500)
        check("header Undo restores it", pg.locator(".cell").count() == n_before)

        # --- run all stops at the error
        pg.locator("#runall").click()
        pg.wait_for_selector(".cell .badge.err", timeout=60000)
        pg.wait_for_timeout(800)
        check("run all stops at the error cell", pg.locator(".cell .badge.err").count() == 1)

        # --- a second notebook: live output, Interrupt, keys, split
        live = tmp / "live.py"
        live.write_text("# %% slow\nimport time\nfor i in range(3):\n    print('tick', i, flush=True)\n    time.sleep(1)\n\n"
                        "# %% two\na = 1\nb = 2\n\n# %% last\nprint('x')\n")
        url2 = subprocess.run(["pyview", str(live), "--no-open"], capture_output=True, text=True, check=True).stdout.strip()
        pg2 = b.new_page(viewport={"width": 1400, "height": 1000})
        pg2.goto(url2)
        pg2.wait_for_selector(".cell")
        pg2.locator('.cell[data-idx="1"] .chead button', has_text="▶").click(force=True)
        pg2.wait_for_selector('.cell[data-idx="1"] .outs pre')
        pg2.wait_for_timeout(500)
        shown = pg2.locator('.cell[data-idx="1"] .outs').inner_text()
        check("output streams while the cell runs", "tick 0" in shown and "tick 2" not in shown, shown)
        check("the cell shows as running and Interrupt is on",
              pg2.locator('.cell[data-idx="1"].running').count() == 1 and pg2.locator("#interrupt").is_enabled(),
              f"class={pg2.locator('.cell[data-idx=\"1\"]').get_attribute('class')} ktext={pg2.locator('#ktext').inner_text()} "
              f"interrupt_enabled={pg2.locator('#interrupt').is_enabled()}")
        pg2.locator("#interrupt").click()
        pg2.wait_for_selector('.cell[data-idx="1"] .outs pre.err', timeout=10000)
        check("Interrupt stops it with KeyboardInterrupt", "KeyboardInterrupt" in pg2.locator('.cell[data-idx="1"] .outs').inner_text())
        pg2.wait_for_timeout(500)
        check("the runner is idle again", "idle" in pg2.locator("#ktext").inner_text())

        pg2.locator('.cell[data-idx="2"] .chead').click()
        pg2.keyboard.press("b")
        pg2.wait_for_selector(".cm-editor")
        check("key b adds a cell below and opens it", pg2.locator(".cell").count() == 4)
        pg2.keyboard.press("Escape")
        pg2.wait_for_timeout(300)
        pg2.locator('.cell[data-idx="2"] .chead').click()
        pg2.keyboard.press("Shift+Enter")
        pg2.wait_for_timeout(1500)
        check("Shift+Enter on a selected cell runs it and selects the next",
              pg2.locator('.cell[data-idx="2"] .count').inner_text().startswith("[") and pg2.locator('.cell.sel[data-idx="3"]').count() == 1)
        pg2.locator('.cell[data-idx="2"] .src').dblclick()
        pg2.wait_for_selector(".cm-editor")
        pg2.locator(".cm-content").click()
        pg2.keyboard.press("ControlOrMeta+End")  # the cursor is on line 1 of "a = 1 / b = 2"
        pg2.keyboard.press("Control+Shift+Minus")
        pg2.wait_for_timeout(800)
        check("Ctrl+Shift+- splits the cell at the cursor line", live.read_text().count("# %%") == 5, live.read_text())
        pg2.close()

        # --- a browsed (not trusted) file opens read-only
        other = tmp / "other.py"
        other.write_text("# %% a\nprint(1)\n")
        pg3 = b.new_page(viewport={"width": 1400, "height": 800})
        pg3.goto(url2)
        resp = pg3.context.request.post("http://127.0.0.1:8766/api/grant", headers={"X-PyView": "1", "Content-Type": "application/json"},
                                        data='{"path": "%s"}' % tmp)
        rel = other.relative_to(Path.home())
        pg3.goto("http://127.0.0.1:8766/view?f=" + str(rel))
        pg3.wait_for_selector(".cell")
        check("an untrusted file shows the trust banner and cannot run",
              pg3.locator("#banner.on").count() == 1 and pg3.locator("#runall").is_disabled())
        pg3.locator("#trust").click()
        pg3.wait_for_timeout(500)
        check("Trust enables running", pg3.locator("#banner.on").count() == 0 and pg3.locator("#runall").is_enabled())
        pg3.close()

        pg.screenshot(path=str(tmp / "final.png"), full_page=True)
        check("no console errors", not errs, str(errs))
        b.close()
finally:
    subprocess.run(["pyview", "restart", str(nb)], capture_output=True)
    if os.environ.get("PYVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
