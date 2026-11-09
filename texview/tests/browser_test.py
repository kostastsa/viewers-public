"""Browser test of texview: the compile, the PDF, the editor, SyncTeX in both directions, Save, compile errors, comments,
suggested changes (Accept, Reject, Undo), the chat and the Oracle. It uses its own server on port 8820 and a stand-in
`claude` (viewkit/tests/fake_claude.py). Needs Playwright and its Chromium, latexmk and synctex. Run:
    uv run --with playwright python tests/browser_test.py
It deletes its temporary folder in tests/tmp at the end. Set TEXVIEW_KEEP_TMP=1 to keep it for debugging."""
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
TEXVIEW = str(HERE.parent / "texview.py")
FAKE = HERE.parent.parent / "viewkit" / "tests" / "fake_claude.py"
PORT = "8820"
TMP_ROOT = HERE / "tmp"  # git ignores it. It is inside the home folder, which is the root the server may open.
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="browser-", dir=TMP_ROOT))
bindir = tmp / "bin"
bindir.mkdir()
(bindir / "claude").write_text('#!/bin/sh\nexec python3 "%s" "$@"\n' % FAKE)
(bindir / "claude").chmod((bindir / "claude").stat().st_mode | stat.S_IEXEC)
proj = tmp / "proj"
shutil.copytree(HERE.parent / "samples", proj)
main = proj / "paper.tex"
# a second document in the same folder: the file list opens it with its PDF
(proj / "notes.tex").write_text("\\documentclass{article}\n\\begin{document}\nSecond document here.\n\\end{document}\n")
# a subdirectory with a document and a source file: the file list shows it as a group of its own
(proj / "drafts").mkdir()
(proj / "drafts" / "old.tex").write_text("\\documentclass{article}\n\\begin{document}\nOld draft.\n\\end{document}\n")
(proj / "drafts" / "macros.sty").write_text("% macros\n")
side = proj / ".texview" / "paper.tex.json"
env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", TEXVIEW_PORT=PORT, TEXVIEW_CLAUDE=str(bindir / "claude"),
           TEXVIEW_BUILD=str(tmp / "build"), VIEWKIT_GH=str(HERE.parent.parent / "viewkit" / "tests" / "fake_gh.py"),
           FAKE_GH_LOG=str(tmp / "gh.jsonl"), VIEWKIT_NOTES_QUEUE=str(tmp / "notes-queue.jsonl"),
           VIEWKIT_AUTO_IMPROVE="0", VIEWKIT_IMPROVE_DIR=str(tmp / "improve"))
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def cli(*args, stdin=None):
    return subprocess.run([sys.executable, TEXVIEW, *args], env=env, capture_output=True, text=True, input=stdin)


def threads():
    return json.loads(side.read_text())["threads"] if side.exists() else []


def wait(fn, timeout=20.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    return None


# The client point (x, y) of the middle of the text-layer span that contains `words`, on page n.
SPAN_AT = """([n, words]) => { const s = [...document.querySelectorAll(`.page[data-n="${n}"] .textLayer span`)]
  .find(s => s.textContent.includes(words)); if (!s) return null; const r = s.getBoundingClientRect();
  return [r.left + Math.min(r.width / 2, 40), r.top + r.height / 2]; }"""
SELECT = """([n, words]) => {
  const span = [...document.querySelectorAll(`.page[data-n="${n}"] .textLayer span`)].find(s => s.textContent.includes(words));
  if (!span) return false;
  const t = span.firstChild, i = t.data.indexOf(words);
  const r = document.createRange(); r.setStart(t, i); r.setEnd(t, i + words.length);
  const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  return true;
}"""
MOD = "Meta" if sys.platform == "darwin" else "Control"

try:
    url = cli(str(main), "--no-open").stdout.strip()
    check("texview prints a link at texview.localhost, with the access key",
          url.startswith(f"http://texview.localhost:{PORT}/view?f=") and "&t=" in url, url)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(url)
        pg.wait_for_selector(".page canvas.base", timeout=60000)
        check("the PDF of the compile shows", pg.locator(".page").count() == 1)
        check("the build button says Compiled", wait(lambda: "Compiled" in pg.inner_text("#buildBtn")), pg.inner_text("#buildBtn"))
        check("the file list has the main file and the .bib file",
              pg.eval_on_selector_all("#fileSel option", "os => os.filter(o => !o.dataset.doc).map(o => o.value)")[:2]
              == ["paper.tex", "paper.bib"])
        check("the editor shows the main file", pg.evaluate("S.cur") == "paper.tex" and "\\documentclass" in pg.evaluate("cm.getValue()"))
        check("no build files next to the source", not (proj / "paper.aux").exists() and not (proj / "paper.pdf").exists())

        # ----- a double-click on the PDF opens or closes the source; SyncTeX: ⌘/Ctrl+click on the PDF goes to the source
        # line, ⌘/Ctrl+click on a line marks the PDF (texview#27)
        NOSRC = "document.body.classList.contains('nosrc')"
        ON_CONCLUSION = "cm.getLine(cm.getCursor().line).startsWith('\\\\section{Conclusion}')"
        def mod_click(x, y):
            pg.keyboard.down(MOD)
            pg.mouse.click(x, y)
            pg.keyboard.up(MOD)
        pg.wait_for_selector(".page .textLayer span")
        check("the source is open at the start", not pg.evaluate(NOSRC))
        x, y = pg.evaluate(SPAN_AT, [1, "Conclusion"])
        line0 = pg.evaluate("cm.getCursor().line")
        pg.mouse.dblclick(x, y)
        check("a double-click on the PDF closes the open source", wait(lambda: pg.evaluate(NOSRC), 3))
        check("the double-click that closes the source does not move the cursor", pg.evaluate("cm.getCursor().line") == line0)
        pg.wait_for_timeout(400)  # the pages get their new layout
        check("the double-click leaves no selection and no selection bar",
              pg.evaluate("getSelection().isCollapsed && getComputedStyle($('selbar')).display === 'none'"))
        x, y = pg.evaluate(SPAN_AT, [1, "Conclusion"])
        pg.mouse.dblclick(x, y)
        check("a double-click on the PDF opens the closed source", wait(lambda: not pg.evaluate(NOSRC), 3))
        check("the double-click that opens the source does not move the cursor", pg.evaluate("cm.getCursor().line") == line0)
        pg.wait_for_timeout(400)
        x, y = pg.evaluate(SPAN_AT, [1, "Conclusion"])
        mod_click(x, y)
        check("a ⌘/Ctrl+click on 'Conclusion' in the PDF goes to its source line", wait(lambda: pg.evaluate(ON_CONCLUSION), 5),
              pg.evaluate("cm.getCursor().line + 1"))
        check("a ⌘/Ctrl+click on the PDF keeps an open source open", not pg.evaluate(NOSRC))
        pg.evaluate("cm.setCursor({line: 0, ch: 0}); setSrc(false)")
        pg.wait_for_timeout(400)
        x, y = pg.evaluate(SPAN_AT, [1, "Conclusion"])
        mod_click(x, y)
        check("a ⌘/Ctrl+click on the PDF opens the closed source", wait(lambda: not pg.evaluate(NOSRC), 5))
        check("the ⌘/Ctrl+click that opens the source goes to the source line", wait(lambda: pg.evaluate(ON_CONCLUSION), 5),
              pg.evaluate("cm.getCursor().line + 1"))
        pg.wait_for_timeout(400)
        x, y = pg.evaluate(SPAN_AT, [1, "Conclusion"])
        pg.keyboard.down(MOD)
        pg.mouse.dblclick(x, y)
        pg.keyboard.up(MOD)
        pg.wait_for_timeout(400)
        check("a ⌘/Ctrl+double-click on the PDF does not close the source", not pg.evaluate(NOSRC))
        coords = pg.evaluate("(() => { const c = cm.charCoords({line: 22, ch: 3}, 'window'); return [c.left, (c.top + c.bottom) / 2]; })()")
        pg.keyboard.down(MOD)
        pg.mouse.click(*coords)
        pg.keyboard.up(MOD)
        check("⌘/Ctrl+click on a source line marks its place in the PDF", wait(lambda: pg.locator(".marks .sync").count() > 0, 5))
        check("⌘/Ctrl+click does not add a second cursor", pg.evaluate("cm.listSelections().length") == 1)

        # ----- find and replace in the editor (⌘/Ctrl+F): the find bar, Replace, Replace all, one Undo
        before = pg.evaluate("cm.getValue()")
        n = before.lower().count("section")
        pg.evaluate("cm.setCursor({line: 0, ch: 0}); cm.focus()")
        pg.keyboard.press(f"{MOD}+f")
        check("⌘/Ctrl+F in the editor opens the find bar with the focus in the Find field",
              wait(lambda: pg.evaluate("!$('findbar').hidden && document.activeElement === $('findQ')"), 3))
        pg.keyboard.type("section")
        check("the bar selects the first match and shows the count", wait(lambda: pg.inner_text("#findN") == f"1 of {n}", 3),
              pg.inner_text("#findN"))
        check("a query in lower case also finds the capital letters", n > before.count("section"))
        check("the editor shows the matches", pg.locator(".cm-searching").count() > 0)
        pg.keyboard.press("Enter")
        check("Enter goes to the next match", pg.inner_text("#findN") == f"2 of {n}", pg.inner_text("#findN"))
        pg.keyboard.press("Shift+Enter")
        check("Shift+Enter goes to the previous match", pg.inner_text("#findN") == f"1 of {n}", pg.inner_text("#findN"))
        pg.locator("#replQ").click()
        pg.keyboard.type("Zzq")
        pg.keyboard.press("Enter")
        after = pg.evaluate("cm.getValue()")
        check("Enter in the Replace field replaces the selected match only",
              after.lower().count("section") == n - 1 and after.count("Zzq") == 1, pg.inner_text("#findN"))
        check("then the next match is selected", pg.inner_text("#findN") == f"1 of {n - 1}", pg.inner_text("#findN"))
        pg.locator("#replAll").click()
        after = pg.evaluate("cm.getValue()")
        check("Replace all replaces all the matches", after.lower().count("section") == 0 and after.count("Zzq") == n)
        check("the bar shows that there is no match now", pg.inner_text("#findN") == "none", pg.inner_text("#findN"))
        pg.evaluate("cm.focus()")
        pg.keyboard.press(f"{MOD}+z")
        check("one Undo brings back all the matches of Replace all", pg.evaluate("cm.getValue()").lower().count("section") == n - 1)
        pg.keyboard.press(f"{MOD}+z")
        check("a second Undo brings back the text", pg.evaluate("cm.getValue()") == before)
        pg.locator("#findQ").click()
        pg.keyboard.press("Escape")
        check("Esc closes the find bar and removes the marks of the matches",
              pg.evaluate("$('findbar').hidden") and pg.locator(".cm-searching").count() == 0)
        check("Esc puts the focus back in the editor", pg.evaluate("cm.hasFocus()"))
        check("the file has no unsaved changes after the Undo", not pg.evaluate("$('dirty').classList.contains('on')"))

        # ----- the suggestions in the editor (texview#30)
        HINTS = ".CodeMirror-hints li"
        def hints():
            return pg.eval_on_selector_all(HINTS, "ls => ls.map(l => l.firstChild.textContent)")
        def hint_nums():
            return pg.eval_on_selector_all(HINTS, "ls => Object.fromEntries(ls.map(l => [l.firstChild.textContent, l.children[1] ? l.children[1].textContent : '']))")
        def fresh_line():  # a new empty line at the end, with the focus
            pg.evaluate("cm.replaceRange('\\n', {line: cm.lastLine()}); cm.setCursor({line: cm.lastLine(), ch: 0}); cm.focus()")
        def this_line():
            return pg.evaluate("cm.getLine(cm.getCursor().line)")
        def popup_closed():
            return wait(lambda: pg.locator(HINTS).count() == 0, 2, 0.05)
        before = pg.evaluate("cm.getValue()")
        check("the data of the compile comes from the server", wait(lambda: pg.evaluate("hintServer.labels['sec:intro']"), 10))
        fresh_line()
        pg.keyboard.type("\\sec")
        check("a backslash and letters show the commands", wait(lambda: hints()[:1] == ["\\section"], 3), hints() if pg.locator(HINTS).count() else "no popup")
        pg.keyboard.press("Tab")
        check("Tab accepts the selected suggestion", this_line() == "\\section" and popup_closed(), this_line())
        fresh_line()
        pg.keyboard.type("\\sec")
        pg.keyboard.press("Tab")  # no wait: the popup can be in its update
        check("a fast Tab accepts too, and types no tab character", this_line() == "\\section" and popup_closed(), this_line())
        fresh_line()
        pg.keyboard.press("Tab")
        check("Tab keeps its function when the popup is closed", this_line() == "  " or this_line() == "\t", repr(this_line()))
        fresh_line()
        pg.keyboard.type("\\zzqnone")
        check("no popup when nothing fits", popup_closed())
        fresh_line()
        pg.keyboard.type("\\ref{")
        check("\\ref{ shows the labels with their numbers", wait(lambda: hint_nums().get("sec:intro") == "1", 3), hint_nums())
        check("no @cref entry in the labels", not [h for h in hints() if "@cref" in h], hints())
        pg.keyboard.press("Escape")
        check("Esc closes the popup", popup_closed())
        check("Esc leaves the text", this_line() == "\\ref{", this_line())
        pg.keyboard.press("Enter")
        check("Enter makes a new line when the popup is closed", this_line() == "" and popup_closed(), this_line())
        for cmd in ("eqref", "pageref", "autoref", "cref", "nameref"):
            fresh_line()
            pg.keyboard.type("\\%s{" % cmd)
            check(f"\\{cmd}{{ shows the labels", wait(lambda: "eq:sum" in hints(), 3))
        fresh_line()
        pg.keyboard.type("\\label{sec 3.D}")
        popup_closed()
        pg.wait_for_timeout(450)  # the pause before the scan
        fresh_line()
        pg.keyboard.type("\\ref{sec 3")
        check("a label that is not compiled shows, and a space does not close the popup", wait(lambda: hints() == ["sec 3.D"], 3), hints())
        pg.keyboard.press("Enter")
        check("Enter accepts a label with a space", wait(lambda: this_line() == "\\ref{sec 3.D", 2, 0.05) and popup_closed(), this_line())
        pg.keyboard.type("}")
        fresh_line()
        pg.keyboard.type("\\cite{zz,alsp")
        check("\\cite{ shows the keys of the .bib file", wait(lambda: hints() == ["alspach1972"], 3), hints())
        pg.keyboard.press("Enter")
        check("after a comma, only the last key changes", wait(lambda: this_line() == "\\cite{zz,alspach1972", 2, 0.05), this_line())
        for cmd in ("citep", "citet", "citeauthor"):
            fresh_line()
            pg.keyboard.type("\\%s{als" % cmd)
            check(f"\\{cmd}{{ shows the keys", wait(lambda: hints() == ["alspach1972"], 3), hints())
        fresh_line()
        pg.keyboard.type("\\newenvironment{tvbox}{}{} \\newcommand{\\tvmacro}{x}")
        popup_closed()
        pg.wait_for_timeout(450)
        fresh_line()
        pg.keyboard.type("\\begin{tvb")
        check("\\begin{ shows the environments of the buffer", wait(lambda: hints() == ["tvbox"], 3), hints())
        pg.keyboard.press("Escape")
        fresh_line()
        pg.keyboard.type("\\end{equ")
        check("\\end{ shows the fixed environments", wait(lambda: "equation" in hints(), 3), hints())
        pg.keyboard.press("Escape")
        fresh_line()
        pg.keyboard.type("\\tvmac")
        check("the commands of the buffer show", wait(lambda: hints() == ["\\tvmacro"], 3), hints())
        check("one match is not inserted automatically", this_line() == "\\tvmac", this_line())
        pg.keyboard.press("Escape")
        fresh_line()
        pg.keyboard.type("% \\ref{")
        pg.wait_for_timeout(120)
        check("no popup in a comment", pg.locator(HINTS).count() == 0)
        fresh_line()
        pg.keyboard.type("100\\% \\ref{")
        check("an escaped percent is not a comment", wait(lambda: "eq:sum" in hints(), 3))
        pg.keyboard.press("Escape")
        fresh_line()
        pg.keyboard.type("\\\\ref")
        pg.wait_for_timeout(120)
        check("no popup after the line break \\\\", pg.locator(HINTS).count() == 0)
        fresh_line()
        pg.keyboard.type("\\sec")
        wait(lambda: pg.locator(HINTS).count() > 0, 3)
        pg.keyboard.press(f"{MOD}+f")
        check("⌘/Ctrl+F works while the popup is open", wait(lambda: pg.evaluate("!$('findbar').hidden"), 3))
        check("the popup closes when the editor loses the focus", popup_closed())
        pg.keyboard.press("Escape")
        pg.evaluate("cm.focus(); cm.setCursor({line: cm.lastLine()})")
        pg.keyboard.press(f"{MOD}+/")
        check("⌘/Ctrl+/ still comments out the line", this_line().startswith("%"), this_line())
        pg.evaluate("cm.setCursor({line: 0, ch: 0}); for (let i = 0; i < 500 && cm.getValue() !== %s; i++) cm.undo();" % json.dumps(before))
        check("Undo brings back the text after the suggestions", pg.evaluate("cm.getValue()") == before)
        # The time from a key press to the popup, in a text of 80 kB with a long line (TEXVIEW_SPEED_DOC: time another document).
        SPEED = """() => { window.hintTimes = []; let t0 = 0;
          addEventListener('keydown', () => { t0 = performance.now(); }, true);
          new MutationObserver(ms => { if (t0 && ms.some(m => [...m.addedNodes].some(n => n.classList && n.classList.contains('CodeMirror-hints')))) {
            hintTimes.push(performance.now() - t0); t0 = 0; } }).observe(document.body, {childList: true}); }"""
        pg.evaluate(SPEED)
        speed_doc = os.environ.get("TEXVIEW_SPEED_DOC")
        long_text = Path(speed_doc).read_text(encoding="utf-8", errors="replace") if speed_doc else ("word \\ref{sec:intro} " * 300 + "\n" + "A line of text.\n" * 5000)[:80000]
        pg.evaluate("t => { cm.setValue(t); cm.focus(); }", long_text)
        pg.wait_for_timeout(450)
        longest = pg.evaluate("(() => { let b = 0; for (let i = 0; i < cm.lineCount(); i++) if (cm.getLine(i).length > cm.getLine(b).length) b = i; return b; })()")
        for _ in range(6):
            pg.evaluate("n => { cm.setCursor({line: n}); cm.focus(); }", longest)
            for key in " \\sectio":
                pg.keyboard.type(key)
                pg.wait_for_timeout(70)
            pg.keyboard.press("Escape")
        times = sorted(pg.evaluate("hintTimes"))
        print(f"info the popup after a key press, {len(long_text)} characters, longest line {pg.evaluate('n => cm.getLine(n).length', longest)}: "
              f"median {times[len(times) // 2]:.1f} ms, largest {times[-1]:.1f} ms, {len(times)} samples" if times else "info no times")
        check("the popup shows less than 50 ms after a key press (median), also on a long line",
              len(times) >= 20 and times[len(times) // 2] < 50, times)
        pg.evaluate("t => { for (let i = 0; i < 2000 && cm.getValue() !== t; i++) cm.undo(); }", before)
        check("the text is back after the time test", pg.evaluate("cm.getValue()") == before)
        check("the file has no unsaved changes after the tests of the suggestions", wait(lambda: not pg.evaluate("$('dirty').classList.contains('on')"), 2))

        # ----- edit, Save, compile again: the PDF changes
        seq0 = pg.evaluate("S.build.seq")
        pg.evaluate("cm.replaceRange(' Extra words here.', {line: 40, ch: cm.getLine(40).length})")
        check("an edit shows the unsaved mark", pg.evaluate("$('dirty').classList.contains('on')"))
        pg.locator(".CodeMirror").click()
        pg.keyboard.press(f"{MOD}+s")
        check("⌘/Ctrl+S writes the file", wait(lambda: "Extra words here." in main.read_text(), 5))
        check("⌘/Ctrl+S does not open the chat", pg.evaluate("$('chatBox').hidden"))
        check("the save starts a compile, and the new PDF shows the words",
              wait(lambda: pg.evaluate("S.build.seq") > seq0 and pg.evaluate("S.build.status") == "ok" and
                   pg.locator(".textLayer span", has_text="Extra words here").count() > 0, 40))
        check("the unsaved mark goes after Save", not pg.evaluate("$('dirty').classList.contains('on')"))

        # ----- a compile error: the problems list and the error line
        pg.evaluate("cm.replaceRange('\\\\undefinedmacro\\n', {line: 40, ch: 0})")
        pg.keyboard.press(f"{MOD}+s")
        check("a compile with an error says so in the header",
              wait(lambda: pg.evaluate("S.build.status") == "error" and "error" in pg.inner_text("#buildBtn"), 40),
              pg.inner_text("#buildBtn"))
        check("the problems list shows the error with its line",
              wait(lambda: "paper.tex:41" in pg.inner_text("#problems")), pg.inner_text("#problems"))
        check("the error line is marked in the editor", pg.locator(".tv-errline").count() == 1)
        pg.evaluate("cm.replaceRange('', {line: 40, ch: 0}, {line: 41, ch: 0})")
        pg.keyboard.press(f"{MOD}+s")
        check("after the fix, the compile is ok again", wait(lambda: pg.evaluate("S.build.status") == "ok", 40))
        check("the error mark goes", wait(lambda: pg.locator(".tv-errline").count() == 0, 5))

        # ----- a file changed on disk: the editor takes it (no unsaved changes)
        main.write_text(main.read_text().replace("A. Author", "B. Author"))
        check("a change on disk shows in the editor", wait(lambda: "B. Author" in pg.evaluate("cm.getValue()"), 10))

        # ----- a comment on text selected in the source: the comment box is next to the selection, the sidebar stays closed
        pg.click("#sideBtn")
        check("the sidebar hides", wait(lambda: pg.evaluate("document.body.classList.contains('noside')"), 3))
        pg.evaluate("""(() => { const l = cm.getLine(22), i = l.indexOf('weights'); cm.focus();
                       cm.setSelection({line: 22, ch: i}, {line: 22, ch: i + 'weights are positive'.length}); })()""")
        check("a selection in the source shows the 💬 Comment bubble",
              wait(lambda: pg.evaluate("getComputedStyle($('edsel')).display") == "flex", 3))
        check("the bubble is next to the selection", pg.evaluate("""(() => {
            const b = $('edsel').getBoundingClientRect(), c = cm.cursorCoords(cm.getCursor('to'), 'window');
            return Math.abs(b.top - c.bottom) < 40 && Math.abs(b.left - c.left) < 120; })()"""))
        pg.click("#edcbtn")
        pg.wait_for_selector("#pop textarea")
        check("the bubble hides when the comment box opens", pg.evaluate("getComputedStyle($('edsel')).display") == "none")
        check("the comment box has the focus", pg.evaluate("document.activeElement === document.querySelector('#pop textarea')"))
        check("the comment box is next to the selection, and the sidebar stays closed", pg.evaluate("""(() => {
            const b = $('pop').getBoundingClientRect(), c = cm.cursorCoords(cm.getCursor('to'), 'window');
            return Math.abs(b.top - c.bottom) < 40 && document.body.classList.contains('noside')
                   && $('threads').querySelector('.composer') === null; })()"""))
        check("the comment box says where", "paper.tex:23" in pg.inner_text("#pop .cwhere"))
        pg.fill("#pop textarea", "Are they? $w_i$")
        pg.keyboard.press(f"{MOD}+Enter")
        check("the comment is saved with its words", wait(lambda: threads() and threads()[0]["anchor"]["quote"] == "weights are positive"),
              threads())
        check("the comment is marked in the editor", wait(lambda: pg.locator(".tv-comment").count() == 1, 5))
        check("the comment box closes after the save", pg.locator("#pop").count() == 0)
        check("the sidebar stays closed after the save", pg.evaluate("document.body.classList.contains('noside')"))
        pg.click("#sideBtn")
        check("the comment card shows", pg.locator(".thread").count() == 1 and "Are they?" in pg.inner_text("#threads"))
        pg.evaluate("cm.focus(); cm.setSelection({line: 22, ch: 0}, {line: 22, ch: 5})")
        pg.keyboard.press(f"{MOD}+Shift+m")
        check("⌘/Ctrl+Shift+M also opens the comment box", wait(lambda: pg.locator("#pop textarea").count() == 1, 3))
        pg.click("#pop button:has-text('Cancel')")
        check("Cancel closes the comment box", wait(lambda: pg.locator("#pop").count() == 0, 3))

        # ----- a comment on text selected in the PDF: it goes on the source line
        pg.evaluate(SELECT, [1, "Main result"])
        check("a selection on a page shows the selection bar", wait(lambda: pg.evaluate("getComputedStyle($('selbar')).display") == "flex", 3))
        pg.click("#cbtn")
        pg.wait_for_selector("#pop textarea")
        check("the comment box of a PDF selection names the source line",
              "paper.tex:25" in pg.inner_text("#pop .cwhere"), pg.inner_text("#pop .cwhere"))
        check("the comment box of a PDF selection is on the page", pg.evaluate("""(() => {
            const b = $('pop').getBoundingClientRect(), r = pv.pages[0].el.getBoundingClientRect();
            return b.left >= r.left - 4 && b.top >= r.top && b.top <= r.bottom; })()"""))
        pg.fill("#pop textarea", "A better title?")
        pg.click("#pop button.on")
        check("the PDF comment is on line 25", wait(lambda: any(t["anchor"]["line"] == 25 and
                                                                t["anchor"]["quote"] == "\\section{Main result}" for t in threads())), threads())

        # ----- the chat: Claude suggests a change; it shows in the source; Accept puts it in; Undo takes it back
        pg.click("#chatOpen")
        pg.fill("#chatText", "EDIT please make it sharper")
        pg.keyboard.press("Enter")
        check("the chat answer comes", wait(lambda: "fake Claude" in pg.inner_text("#chatMsgs"), 30), pg.inner_text("#chatMsgs")[-600:])
        pos = pg.evaluate("""(() => {
            const b = $('chatMsgs'), pad = document.createElement('div');
            pad.style.cssText = 'height: 3000px; flex: none';
            b.prepend(pad);
            b.scrollTop = 500;
            $('chatMin').click(); $('chatOpen').click();
            const top = b.scrollTop;
            b.scrollTop = b.scrollHeight;
            $('chatMin').click(); $('chatOpen').click();
            const end = b.scrollHeight - b.scrollTop - b.clientHeight;
            pad.remove();
            return [top, end]; })()""")
        check("Minimize and Open keep the scroll position of the chat", pos[0] == 500, pos)
        check("Minimize and Open keep the chat at the end if it was at the end", pos[1] < 2, pos)
        check("the suggested change shows in the sidebar", wait(lambda: pg.locator(".thread .diff").count() == 1, 10))
        check("only the word that changes is crossed out in the editor, and the new word follows it",
              wait(lambda: pg.locator(".tv-old").inner_text() == "tight" and pg.locator(".tv-new").inner_text() == "sharp", 5))
        check("the card shows the change as tracked changes",
              pg.inner_text(".thread .diff del") == "tight" and pg.inner_text(".thread .diff ins") == "sharp")
        check("the source file did not change yet", "the bound is tight" in main.read_text())
        pg.locator(".tv-acts .yes").click()
        check("✓ in the editor accepts: the file has the new text", wait(lambda: "the bound is sharp" in main.read_text(), 5))
        check("the editor shows the new text", wait(lambda: "the bound is sharp" in pg.evaluate("cm.getValue()"), 5))
        check("the change is gone from the sidebar", wait(lambda: pg.locator(".thread .diff").count() == 0, 5))
        check("Accept compiles again", wait(lambda: pg.evaluate("S.build.status") == "ok" and
                                             pg.locator(".textLayer span", has_text="bound is sharp").count() > 0, 40))
        pg.click("#undoBtn")
        check("Undo puts the old text back in the file", wait(lambda: "the bound is tight" in main.read_text(), 5))
        check("Undo brings the change back", wait(lambda: pg.locator(".thread .diff").count() == 1, 5))
        check("the editor shows the old text after Undo", wait(lambda: "the bound is tight" in pg.evaluate("cm.getValue()"), 5))
        pg.locator(".thread .tacts .no").click()
        check("Reject removes the change and keeps the file", wait(lambda: pg.locator(".thread .diff").count() == 0, 5)
              and "the bound is tight" in main.read_text())

        # a change from the command line, with unsaved edits in the same file: Accept saves them first
        cli("suggest", str(main), "--file", "paper.tex", stdin="<<<<<<< old\nsimple to use\n=======\neasy to use\n>>>>>>> new\n")
        wait(lambda: pg.locator(".thread .diff").count() == 1, 5)
        pg.evaluate("cm.replaceRange('% a note\\n', {line: 0, ch: 0})")
        pg.locator(".thread .tacts .yes").click()
        check("Accept with unsaved edits saves them and puts the change in",
              wait(lambda: main.read_text().startswith("% a note\n") and "easy to use" in main.read_text(), 8))

        # ----- the Oracle answers the open comments
        pg.click("#oracleBtn")
        check("the Oracle answers the open comments",
              wait(lambda: threads() and all(t["msgs"][-1]["author"] == "claude" for t in threads()), 20), threads())
        check("the answer shows in the sidebar", wait(lambda: "fake Oracle" in pg.inner_text("#threads"), 10))

        # ----- Send in the comment box saves the comment and starts the Oracle
        n0 = len(threads())
        check("the Oracle is free before Send", wait(lambda: not pg.evaluate("oracle.running"), 10))
        pg.evaluate("cm.focus(); cm.setSelection({line: 22, ch: 0}, {line: 22, ch: 5})")
        pg.keyboard.press(f"{MOD}+Shift+m")
        pg.wait_for_selector("#pop textarea")
        check("the comment box has a Send button", pg.locator("#pop button:has-text('Send')").count() == 1)
        pg.fill("#pop textarea", "Send this one")
        pg.click("#pop button:has-text('Send')")
        check("Send saves the comment and the Oracle answers it",
              wait(lambda: len(threads()) == n0 + 1 and all(t["msgs"][-1]["author"] == "claude" for t in threads()), 20),
              threads())

        # ----- the source pane and the keys
        pg.locator("#pages").click(position={"x": 5, "y": 5})
        pg.keyboard.press("e")
        check("the key e hides the source", pg.evaluate("document.body.classList.contains('nosrc')"))
        pg.keyboard.press("e")
        check("the key e shows it again", not pg.evaluate("document.body.classList.contains('nosrc')"))

        # ----- the zoom of the source is separate from the zoom of the pages
        font = "parseFloat(getComputedStyle(document.querySelector('#editor .CodeMirror')).fontSize)"
        f0, z0 = pg.evaluate(font), pg.evaluate("pv.scale")
        pg.locator(".CodeMirror").click()
        pg.keyboard.press(f"{MOD}+Equal")
        check("⌘/Ctrl+= in the source makes the source text larger",
              pg.evaluate(font) > f0 + 0.5, (f0, pg.evaluate(font)))
        check("the zoom of the pages does not change", pg.evaluate("pv.scale") == z0)
        check("the size of the source text is kept", float(pg.evaluate("stored('srcZoom')") or 0) > 1)
        pg.keyboard.press(f"{MOD}+Minus")
        pg.keyboard.press(f"{MOD}+Minus")
        check("⌘/Ctrl+- makes the source text smaller", pg.evaluate(font) < f0)
        pg.keyboard.press(f"{MOD}+0")
        check("⌘/Ctrl+0 gives the default size of the source text", pg.evaluate(font) == f0)
        pg.locator("#pages").click(position={"x": 5, "y": 5})
        pg.keyboard.press("+")
        check("the key + on the pages zooms only the pages",
              pg.evaluate("pv.scale") > z0 and pg.evaluate(font) == f0)
        pg.evaluate("setZoom('fit')")

        # ----- Option with +, - and 0 zooms the pane of the last click, and Option+Tab shows or hides the source
        z0 = pg.evaluate("pv.scale")
        pg.locator(".CodeMirror").click()
        pg.keyboard.press("Alt+Equal")
        check("Option+= in the source makes the source text larger",
              pg.evaluate(font) > f0 + 0.5 and pg.evaluate("pv.scale") == z0, (f0, pg.evaluate(font)))
        pg.keyboard.press("Alt+0")
        check("Option+0 in the source gives the default size", pg.evaluate(font) == f0)
        check("Option does not type in the source", "≠" not in pg.evaluate("cm.getValue()"))
        pg.locator("#pages").click(position={"x": 5, "y": 5})
        pg.keyboard.press("Alt+Equal")
        check("Option+= on the pages zooms only the pages",
              pg.evaluate("pv.scale") > z0 and pg.evaluate(font) == f0, pg.evaluate("pv.scale"))
        z1 = pg.evaluate("pv.scale")
        pg.keyboard.press("Alt+Minus")
        check("Option+- on the pages zooms out", pg.evaluate("pv.scale") < z1)
        pg.keyboard.press("Alt+0")
        check("Option+0 on the pages fits the page width", pg.evaluate("pv.zoom") == "fit")
        pg.locator("#pageNum").click()
        pg.keyboard.press("Alt+Minus")
        check("Option in a text box does not zoom", pg.evaluate("pv.zoom") == "fit")
        pg.locator(".CodeMirror").click()
        pg.keyboard.press("Alt+Tab")
        check("Option+Tab in the editor hides the source", pg.evaluate("document.body.classList.contains('nosrc')"))
        pg.keyboard.press("Alt+Tab")
        check("Option+Tab shows it again", not pg.evaluate("document.body.classList.contains('nosrc')"))
        check("Option+Tab does not type a tab in the source", "\t" not in pg.evaluate("cm.getValue()"))
        if sys.platform == "darwin":  # Control+S on a Mac shows or hides the source; ⌘+S does not change (texview#29)
            nosrc = "document.body.classList.contains('nosrc')"
            pg.evaluate("window.nsave = 0; { const f = saveAll; saveAll = (...a) => { nsave++; return f(...a); }; window.fsave = f; }")
            pg.locator(".CodeMirror").click()
            pg.keyboard.press("Control+s")
            check("Control+S in the editor hides the source", pg.evaluate(nosrc))
            pg.keyboard.press("Control+s")
            check("Control+S shows the source again", not pg.evaluate(nosrc))
            check("Control+S does not save", pg.evaluate("nsave") == 0)
            pg.evaluate("chat.open(false)")
            check("the chat is hidden before Control+S", pg.evaluate("$('chatBox').hidden"))
            pg.locator("#pages").click(position={"x": 5, "y": 5})
            pg.keyboard.press("Control+s")
            check("Control+S on the pages hides the source", pg.evaluate(nosrc))
            check("Control+S does not open the chat", pg.evaluate("$('chatBox').hidden"))
            pg.keyboard.press("Control+s")
            check("Control+S on the pages shows the source again", not pg.evaluate(nosrc))
            pg.locator(".CodeMirror").click()
            pg.keyboard.press("Meta+s")
            check("⌘+S in the editor saves and does not hide the source", pg.evaluate("nsave") == 1 and not pg.evaluate(nosrc))
            pg.evaluate("saveAll = fsave")
        pg.evaluate("setZoom('fit')")

        # ----- the contents of the PDF: hidden at the start, the button ☰ or the key o shows them
        check("the contents are hidden at the start", not pg.locator("#nav").is_visible())
        pg.click("#navBtn")
        check("the button ☰ shows the contents", pg.locator("#nav").is_visible())
        check("the contents list the sections of the PDF",
              wait(lambda: pg.locator("#outline a", has_text="Conclusion").count() == 1, 10), pg.inner_text("#outline"))
        pg.evaluate("setZoom(3)")  # the sample is short: zoom in, so that the view can scroll to the section
        pg.wait_for_timeout(300)
        pg.evaluate("$('pages').scrollTop = 0")
        pg.locator("#outline a", has_text="Conclusion").click()
        check("a click on a section goes to its place in the PDF",
              wait(lambda: pg.evaluate("$('pages').scrollTop") > 0, 5), pg.evaluate("$('pages').scrollTop"))
        # ⌘/Ctrl+[ and ⌘/Ctrl+] go back and forward over the jumps, as in a browser (texview#22)
        top_sec, url0 = pg.evaluate("$('pages').scrollTop"), pg.url
        pg.keyboard.press(f"{MOD}+BracketLeft")
        check("⌘/Ctrl+[ goes back to the place before the jump", wait(lambda: pg.evaluate("$('pages').scrollTop") < 3, 3),
              pg.evaluate("$('pages').scrollTop"))
        check("⌘/Ctrl+[ stays in the document", pg.url == url0, pg.url)
        pg.keyboard.press(f"{MOD}+BracketRight")
        check("⌘/Ctrl+] goes forward to the section again",
              wait(lambda: abs(pg.evaluate("$('pages').scrollTop") - top_sec) < 3, 3), (top_sec, pg.evaluate("$('pages').scrollTop")))
        pg.keyboard.press(f"{MOD}+BracketRight")
        check("⌘/Ctrl+] at the last place does nothing", abs(pg.evaluate("$('pages').scrollTop") - top_sec) < 3)
        pg.locator(".CodeMirror").click()
        pg.wait_for_timeout(300)
        top_src = pg.evaluate("$('pages').scrollTop")
        pg.keyboard.press(f"{MOD}+BracketLeft")
        pg.wait_for_timeout(200)
        check("⌘/Ctrl+[ in the source does not move the pages", pg.evaluate("$('pages').scrollTop") == top_src)
        pg.evaluate("setZoom('fit')")
        pg.locator("#pages").click(position={"x": 5, "y": 5})
        pg.keyboard.press("o")
        check("the key o hides the contents", not pg.locator("#nav").is_visible())
        pg.keyboard.press(f"{MOD}+e")
        check("⌘/Ctrl+E shows the contents", pg.locator("#nav").is_visible())
        pg.keyboard.press(f"{MOD}+e")
        check("⌘/Ctrl+E hides the contents", not pg.locator("#nav").is_visible())
        noside = "document.body.classList.contains('noside')"
        side0 = pg.evaluate(noside)
        pg.keyboard.press(f"{MOD}+d")
        check("⌘/Ctrl+D changes the sidebar", pg.evaluate(noside) != side0)
        pg.keyboard.press(f"{MOD}+d")
        check("⌘/Ctrl+D changes the sidebar back", pg.evaluate(noside) == side0)
        pg.locator(".CodeMirror").click()
        text0 = pg.evaluate("cm.getValue()")
        pg.keyboard.press(f"{MOD}+d")
        check("⌘/Ctrl+D in the source changes the sidebar and keeps the text",
              pg.evaluate(noside) != side0 and pg.evaluate("cm.getValue()") == text0)
        pg.keyboard.press(f"{MOD}+d")
        if sys.platform == "darwin":
            pg.evaluate("cm.focus(); cm.setCursor({line: 22, ch: 0})")
            pg.keyboard.press("Control+e")
            check("Ctrl+E in the source on a Mac goes to the end of the line, and the contents stay hidden",
                  pg.evaluate("cm.getCursor().ch === cm.getLine(22).length") and not pg.locator("#nav").is_visible())
        pg.evaluate("chat.open(false)")
        pg.locator("#pages").click(position={"x": 5, "y": 5})
        pg.keyboard.press(f"{MOD}+s")
        check("⌘/Ctrl+S outside the source opens the chat", wait(lambda: not pg.evaluate("$('chatBox').hidden"), 3))
        check("the chat text box gets the focus", wait(lambda: pg.evaluate("document.activeElement === $('chatText')"), 3))
        pg.keyboard.press(f"{MOD}+s")
        check("⌘/Ctrl+S in the chat text box hides the chat", wait(lambda: pg.evaluate("$('chatBox').hidden"), 3))
        pg.locator(".CodeMirror").click()
        pg.keyboard.press(f"{MOD}+s")
        check("⌘/Ctrl+S in the source does not open the chat", pg.evaluate("$('chatBox').hidden"))
        pg.keyboard.press(f"{MOD}+k")
        check("⌘/Ctrl+K does not open the chat", pg.evaluate("$('chatBox').hidden"))
        check("no errors in the page", not errs, errs)

        # ----- the dashboard (kit.improveDash) is a floating window: drag its header to move it
        pg.locator("#name").click()
        pg.keyboard.press(f"{MOD}+Shift+Period")
        check("⌘/Ctrl+Shift+. opens the dashboard", wait(lambda: pg.locator(".kitDash").is_visible(), 5))
        box0 = pg.locator(".kitDash").bounding_box()
        hb = pg.locator(".kitDashHead b").bounding_box()
        hx, hy = hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2
        pg.mouse.move(hx, hy)
        pg.mouse.down()
        pg.mouse.move(hx - 150, hy + 120, steps=6)
        pg.mouse.up()
        box1 = pg.locator(".kitDash").bounding_box()
        check("a drag of the header moves the dashboard",
              abs(box1["x"] - (box0["x"] - 150)) < 3 and abs(box1["y"] - (box0["y"] + 120)) < 3, (box0, box1))
        pg.keyboard.press("Escape")
        pg.keyboard.press(f"{MOD}+Shift+Period")
        box2 = pg.locator(".kitDash").bounding_box()
        check("the dashboard opens again at the new place", abs(box2["x"] - box1["x"]) < 3 and abs(box2["y"] - box1["y"]) < 3,
              (box1, box2))
        pg.mouse.move(hx - 150, hy + 120)
        pg.mouse.down()
        pg.mouse.move(-400, -400, steps=6)
        pg.mouse.up()
        box3 = pg.locator(".kitDash").bounding_box()
        check("the header stays in the window", box3["x"] >= 0 and box3["y"] >= 0, box3)
        pg.locator(".kitDashHead b").dblclick()
        box4 = pg.locator(".kitDash").bounding_box()
        check("a double-click on the header gives the default place back",
              abs(box4["x"] - box0["x"]) < 3 and abs(box4["y"] - box0["y"]) < 3, (box0, box4))
        pg.keyboard.press("Escape")
        check("Esc closes the dashboard", pg.locator(".kitDash").count() == 0)
        check("no errors in the page after the dashboard", not errs, errs)

        # ----- the preview card (kit.previewBand) is a floating window too: drag its header to move it.
        # A fake status makes the card here; after the test, the fake status says "not a preview", so the card stops.
        pg.evaluate("""() => { window._pv = {preview: true, n: 99, title: 'A test note', state: 'preview', rev: 1, summary: 'Test.'};
                       kit.previewBand({get: async () => window._pv, post: async () => window._pv}); }""")
        check("the preview card shows", wait(lambda: pg.locator(".kitPreview").is_visible(), 5))
        box0 = pg.locator(".kitPreview").bounding_box()
        hb = pg.locator(".kitPreviewHead b").bounding_box()
        hx, hy = hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2
        pg.mouse.move(hx, hy)
        pg.mouse.down()
        pg.mouse.move(hx + 100, hy + 200, steps=6)
        pg.mouse.up()
        box1 = pg.locator(".kitPreview").bounding_box()
        check("a drag of the header moves the preview card",
              abs(box1["x"] - (box0["x"] + 100)) < 3 and abs(box1["y"] - (box0["y"] + 200)) < 3, (box0, box1))
        check("the browser keeps the place of the card", pg.evaluate("JSON.parse(localStorage.getItem('kit.previewPos'))")
              == {"x": round(box1["x"]), "y": round(box1["y"])})
        pg.locator(".kitPreviewMin").click()
        box2 = pg.locator(".kitPreview").bounding_box()
        check("the button in the header does not move the card", abs(box2["x"] - box1["x"]) < 3 and abs(box2["y"] - box1["y"]) < 3,
              (box1, box2))
        pg.locator(".kitPreviewMin").click()
        pg.locator(".kitPreviewHead b").dblclick()
        box3 = pg.locator(".kitPreview").bounding_box()
        check("a double-click on the header of the card gives the default place back",
              abs(box3["x"] - box0["x"]) < 3 and abs(box3["y"] - box0["y"]) < 3, (box0, box3))
        pg.evaluate("() => { window._pv = {preview: false}; localStorage.removeItem('kit.previewPos'); document.querySelector('.kitPreview').remove(); }")
        check("no errors in the page after the preview card", not errs, errs)

        # ----- another document in the folder: the file list opens it with its PDF, in this tab
        check("the file list shows the other document in its own group",
              pg.evaluate("""[...document.querySelectorAll('#fileSel optgroup')].some(g => g.label.startsWith('Other documents')
                             && [...g.children].some(o => o.value === 'notes.tex' && o.dataset.doc))"""))
        sub = pg.evaluate("""[...document.querySelectorAll('#fileSel optgroup')].filter(g => g.label === '📁 drafts/')
                             .map(g => [...g.children].map(o => [o.value, o.textContent, o.dataset.doc || '']))""")
        check("the subdirectory shows as a group, with its document first, marked, and short names",
              sub == [[["drafts/old.tex", "📄 old.tex", "1"], ["drafts/macros.sty", "macros.sty", ""]]], sub)
        pg.select_option("#fileSel", "notes.tex")
        check("choosing the other document opens it in this tab",
              wait(lambda: pg.evaluate("location.search").endswith("%2Fnotes.tex"), 30), pg.url)
        check("the other document compiles, and its PDF shows",
              wait(lambda: pg.locator(".textLayer span", has_text="Second document here").count() > 0, 60))
        check("the editor shows the main file of the other document", wait(lambda: pg.evaluate("S.cur") == "notes.tex", 10))
        check("paper.tex is now in the group of the other documents",
              pg.evaluate("""$('fileSel').querySelector('option[value="paper.tex"]').dataset.doc""") == "1")
        check("no errors in the page of the other document", not errs, errs)
        check("no group of the folder above when that folder was not passed to texview",
              not pg.evaluate("[...document.querySelectorAll('#fileSel optgroup')].some(g => g.label.startsWith('↑'))"))

        # ----- a document in a subdirectory: the group "↑ ../" goes back to the documents of the folder above
        pg.select_option("#fileSel", "drafts/old.tex")
        check("choosing the document of the subdirectory opens it in this tab",
              wait(lambda: pg.evaluate("location.search").endswith("%2Fdrafts%2Fold.tex"), 30), pg.url)
        check("the document of the subdirectory compiles", wait(lambda: pg.locator(".textLayer span", has_text="Old draft").count() > 0, 60))
        up = pg.evaluate("""[...document.querySelectorAll('#fileSel optgroup')].filter(g => g.label.startsWith('↑ ../'))
                            .map(g => [...g.children].map(o => [o.value, o.dataset.doc || '']))""")
        check("the file list has the documents of the folder above, in the group ↑ ../",
              up == [[["../notes.tex", "1"], ["../paper.tex", "1"]]], up)
        pg.select_option("#fileSel", "../paper.tex")
        check("choosing ../paper.tex goes back up to the first document",
              wait(lambda: pg.evaluate("location.search").endswith("%2Fpaper.tex")
                   and "drafts" not in pg.evaluate("location.search"), 30), pg.url)
        check("the editor shows paper.tex again", wait(lambda: pg.evaluate("S.cur") == "paper.tex", 30))
        check("no errors in the page after the way back up", not errs, errs)
        b.close()
finally:
    cli("--stop")
    if os.environ.get("TEXVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("all passed" if not failures else f"{len(failures)} FAILED: " + ", ".join(failures)))
sys.exit(1 if failures else 0)
