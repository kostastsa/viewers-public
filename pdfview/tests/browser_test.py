"""Browser test of pdfview: pages, text anchors, comments, dark pages, zoom, the contents and the chat.
It uses its own server on port 8810 and a stand-in `claude` (viewkit/tests/fake_claude.py). Needs Playwright and its
Chromium, and pdftotext. Run:
    uv run --with playwright python tests/browser_test.py
It deletes its temporary folder in tests/tmp at the end. Set PDFVIEW_KEEP_TMP=1 to keep it for debugging."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PDFVIEW = str(HERE.parent / "pdfview.py")
FAKE = HERE.parent.parent / "viewkit" / "tests" / "fake_claude.py"
PORT = "8810"
TMP_ROOT = HERE / "tmp"  # git ignores it. It is inside the home folder, which is the root the server may open.
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="browser-", dir=TMP_ROOT))
bindir = tmp / "bin"
bindir.mkdir()
(bindir / "claude").write_text('#!/bin/sh\nexec python3 "%s" "$@"\n' % FAKE)
(bindir / "claude").chmod((bindir / "claude").stat().st_mode | stat.S_IEXEC)
pdf = tmp / "paper.pdf"
shutil.copy(HERE / "fixture.pdf", pdf)
side = tmp / ".pdfview" / "paper.pdf.json"
gh_log, queue = tmp / "gh.jsonl", tmp / "notes-queue.jsonl"  # the notes go to a stand-in gh, not to GitHub
env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", PDFVIEW_PORT=PORT, PDFVIEW_CLAUDE=str(bindir / "claude"),
           VIEWKIT_GH=str(HERE.parent.parent / "viewkit" / "tests" / "fake_gh.py"), FAKE_GH_LOG=str(gh_log),
           VIEWKIT_NOTES_QUEUE=str(queue), VIEWKIT_AUTO_IMPROVE="0",
           VIEWKIT_IMPROVE_DIR=str(tmp / "improve"))  # the automatic loop has its own test
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def cli(*args):
    return subprocess.run([sys.executable, PDFVIEW, *args], env=env, capture_output=True, text=True)


def comments():
    return json.loads(side.read_text())["threads"] if side.exists() else []


# Select the words `what` in the text-layer span that contains `line`, on page n.
SELECT = """([n, line, what]) => {
  const span = [...document.querySelectorAll(`.page[data-n="${n}"] .textLayer span`)].find(s => s.textContent.includes(line));
  if (!span) return false;
  const t = span.firstChild, i = t.data.indexOf(what);
  const r = document.createRange(); r.setStart(t, i); r.setEnd(t, i + what.length);
  const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  return true;
}"""
# The top of the first highlight of thread `id`, relative to its page.
MARK_TOP = """id => { const m = document.querySelector(`.marks div[data-t="${id}"]`); if (!m) return null;
  return m.getBoundingClientRect().top - m.closest('.page').getBoundingClientRect().top; }"""
LINE_TOP = """([n, line]) => { const s = [...document.querySelectorAll(`.page[data-n="${n}"] .textLayer span`)]
  .find(s => s.textContent.includes(line)); return s.getBoundingClientRect().top - s.closest('.page').getBoundingClientRect().top; }"""
# True if the comment box is just under the line with the words `line` on page `n`.
POP_BELOW = """([n, line]) => { const s = [...document.querySelectorAll(`.page[data-n="${n}"] .textLayer span`)]
  .find(s => s.textContent.includes(line)), p = document.getElementById('pop'); if (!s || !p) return false;
  const d = p.getBoundingClientRect().top - s.getBoundingClientRect().bottom; return d >= 0 && d < 30; }"""

try:
    url = cli(str(pdf), "--no-open").stdout.strip()
    check("pdfview prints a link at pdfview.localhost, with the access key",
          url.startswith(f"http://pdfview.localhost:{PORT}/view?f=") and "&t=" in url, url)
    BASE = f"http://pdfview.localhost:{PORT}"  # the cookie belongs to this host name
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(url)
        pg.wait_for_selector('.page[data-n="1"] .textLayer span', timeout=20000)

        # --- the pages
        check("the header shows the folder and the name, and the page count",
              pg.locator("#name .crumbName").inner_text() == "paper.pdf" and pg.locator("#name .crumb").count() >= 1
              and pg.locator("#pageCount").inner_text() == "/ 2", pg.locator("#name").inner_text())
        check("the tab title is the name, then the context", pg.title().startswith("paper.pdf · "), pg.title())
        check("the pages fit the width of the column", pg.evaluate("() => document.querySelector('.page').offsetWidth") > 700,
              pg.evaluate("() => document.querySelector('.page').offsetWidth"))
        check("the tab icon is the pdfview icon, drawn by the kit",
              pg.evaluate("() => document.querySelector('link[rel~=\"icon\"]').href").startswith("data:image/png"))
        check("two page boxes, page 1 drawn", pg.locator(".page").count() == 2 and pg.locator('.page[data-n="1"] canvas.base').count() == 1)
        check("the text layer has the words of the page", "the bound is tight" in pg.locator('.page[data-n="1"] .textLayer').inner_text())
        pg.locator("#navBtn").click()
        check("the contents list the outline", pg.locator("#outline a").all_inner_texts() == ["Introduction", "Section 2. Results"])
        pg.locator("#outline a", has_text="Section 2").click()
        pg.wait_for_timeout(300)
        check("a contents entry goes to its page", pg.locator("#pageNum").input_value() == "2", pg.locator("#pageNum").input_value())
        pg.locator("#navBtn").click()
        pg.locator("#pageNum").fill("1")
        pg.locator("#pageNum").press("Enter")
        pg.wait_for_timeout(300)

        # --- links in the PDF: a link inside the PDF goes to its place, and ⌘/Ctrl+[ comes back; a web link does nothing
        def at_point(n, x, y):  # scroll the point (x, y) of page n (PDF points from the bottom left) into view
            return pg.evaluate("""([n, x, y]) => { const p = document.querySelector(`.page[data-n="${n}"]`);
              const s = document.getElementById('pages'); s.scrollTop = p.offsetTop + (792 - y) / 792 * p.offsetHeight - 300;
              const r = p.getBoundingClientRect();
              return [r.left + x / 612 * r.width, r.top + (792 - y) / 792 * r.height, s.scrollTop]; }""", [n, x, y])
        lx, ly, top0 = at_point(1, 100, 374)
        pg.wait_for_timeout(300)
        pg.mouse.move(lx, ly)
        check("the pointer is a hand on a link", pg.evaluate("([x, y]) => getComputedStyle(document.elementFromPoint(x, y)).cursor",
                                                              [lx, ly]) == "pointer")
        # The link color is in the page canvas: the darkest pixel in the box of each link (PDF units). No layer with a
        # blend mode over the page, because a browser can show it as a solid box while you scroll (pdfview#3).
        link_px = """() => {
          const c = document.querySelector('.page[data-n="1"] canvas.base'), ctx = c.getContext('2d');
          const sx = c.width / 612, sy = c.height / 792;
          const darkest = ([x0, y0, x1, y1]) => {
            const d = ctx.getImageData(Math.round(x0 * sx), Math.round((792 - y1) * sy),
                                       Math.round((x1 - x0) * sx), Math.round((y1 - y0) * sy)).data;
            let best = [255, 255, 255, 765];
            for (let i = 0; i < d.length; i += 4) { const s = d[i] + d[i + 1] + d[i + 2]; if (s < best[3]) best = [d[i], d[i + 1], d[i + 2], s]; }
            return best; };
          const blend = [...document.querySelectorAll('.page *')].filter(e => getComputedStyle(e).mixBlendMode !== 'normal').length;
          return {inner: darkest([72, 366, 147, 382]), web: darkest([130, 336, 200, 352]), blend}; }"""
        lk = pg.evaluate(link_px)
        check("the link inside the PDF has the link color, the web link does not, and no blend layer covers the page",
              lk["blend"] == 0 and lk["inner"][2] - lk["inner"][0] > 80 and abs(lk["web"][2] - lk["web"][0]) < 20, lk)
        pg.mouse.click(lx, ly)
        pg.wait_for_timeout(400)
        check("a click on a link inside the PDF goes to its page", pg.locator("#pageNum").input_value() == "2",
              pg.locator("#pageNum").input_value())
        check("a click on a link shows no notice at the bottom (pdfview#4)", pg.locator(".toast").count() == 0,
              pg.inner_text("#toast"))
        pg.keyboard.press("Control+BracketLeft")
        pg.wait_for_timeout(400)
        top1 = pg.evaluate("() => document.getElementById('pages').scrollTop")
        check("⌘/Ctrl+[ goes back to the place before the link", abs(top1 - top0) < 3 and pg.locator("#pageNum").input_value() == "1",
              (top0, top1))
        url0 = pg.url
        wx, wy, top0 = at_point(1, 165, 344)
        pg.wait_for_timeout(200)
        pg.mouse.click(wx, wy)
        pg.wait_for_timeout(400)
        check("a web link in the PDF does not open", pg.url == url0 and len(b.contexts[0].pages) == 1 and
              pg.evaluate("() => document.getElementById('pages').scrollTop") == top0)
        lx, ly, _ = at_point(1, 74, 374)
        pg.mouse.move(lx, ly)
        pg.mouse.down()
        pg.mouse.move(lx + 120, ly, steps=6)
        pg.mouse.up()
        pg.wait_for_timeout(300)
        check("a drag on a link selects its words and does not follow it", "Section" in pg.evaluate("() => getSelection().toString()")
              and pg.locator("#pageNum").input_value() == "1", pg.evaluate("() => getSelection().toString()"))
        pg.evaluate("() => getSelection().removeAllRanges()")
        pg.locator("#pageNum").fill("1")
        pg.locator("#pageNum").press("Enter")
        pg.wait_for_timeout(300)

        # --- a comment on the second "the bound is tight" (line 2): the anchor must find this one, not the first
        check("the text can be selected", pg.evaluate(SELECT, [1, "A second claim", "the bound is tight"]))
        pg.wait_for_selector("#cbtn", state="visible", timeout=3000)
        check("the chat chip shows the selection with its page", "p. 1" in pg.locator("#chatCtx").inner_text()
              and "the bound is tight" in pg.locator("#chatCtx").inner_text())
        pg.locator("#cbtn").click()
        check("the comment box opens on the page, under the words", pg.locator("#pages #pop").count() == 1
              and "page 1" in pg.locator("#pop").inner_text()
              and pg.evaluate(POP_BELOW, [1, "A second claim"]))
        check("the comment box has the focus", pg.evaluate("() => document.activeElement === document.querySelector('#pop textarea')"))
        check("the sidebar has no comment box", pg.locator("#threads .composer").count() == 0)
        check("a draft highlight shows on the page while writing", pg.locator(".marks div.draft").count() >= 1)
        pg.locator("#pop textarea").fill("Only for small $n$?")
        pg.locator("#pop textarea").press("Control+Enter")
        pg.wait_for_selector('.thread[data-t="1"]', timeout=5000)
        ts = comments()
        check("the comment is saved in .pdfview/paper.pdf.json", len(ts) == 1 and ts[0]["anchor"]["quote"] == "the bound is tight"
              and ts[0]["anchor"]["prefix"].endswith("A second claim: "), ts)
        check("the sidebar shows the thread as open", "Open" in pg.locator('.thread[data-t="1"]').inner_text())
        pg.wait_for_timeout(1500)
        check("the maths in the comment is typeset", pg.locator('.thread[data-t="1"] mjx-container').count() == 1)
        top = pg.evaluate(MARK_TOP, 1)
        line2 = pg.evaluate(LINE_TOP, [1, "A second claim"])
        check("the highlight is on line 2, not on the first match", top is not None and abs(top - line2) < 4, (top, line2))

        pg.reload()
        pg.wait_for_selector('.marks div[data-t="1"]', timeout=20000)
        top2 = pg.evaluate(MARK_TOP, 1)
        check("after a reload the anchor finds the same words", top2 is not None and abs(top2 - line2) < 4, (top2, line2))

        # --- a comment from the command line (as Claude adds one), with words from pdftotext
        r = cli("add", str(pdf), "Is this the Feit-Thompson theorem?", "--page", "2", "--quote", "main theorem holds")
        check("pdfview add works while the server runs", r.returncode == 0, r.stderr)
        pg.wait_for_selector('.thread[data-t="2"]', timeout=6000)
        check("the page shows a comment from outside within a few seconds", "Feit-Thompson" in pg.locator("#threads").inner_text())
        pg.locator('.thread[data-t="2"] q').click()
        pg.wait_for_selector('.page[data-n="2"] .marks div[data-t="2"]', timeout=8000)
        pg.wait_for_timeout(300)
        check("a click on the thread goes to page 2 and shows its highlight", pg.locator("#pageNum").input_value() == "2")
        check("the thread is active", "active" in (pg.locator('.thread[data-t="2"]').get_attribute("class") or ""))

        # --- reply, resolve, undo
        pg.locator('.thread[data-t="2"] button', has_text="Reply").click()
        pg.locator('.thread[data-t="2"] .composer textarea').fill("Yes, it is.")
        pg.locator('.thread[data-t="2"] .composer button', has_text="Reply").click()
        pg.wait_for_function("() => document.querySelector('.thread[data-t=\"2\"]').innerText.includes('Yes, it is.')", timeout=5000)
        check("a reply is saved", [m["text"] for m in comments()[1]["msgs"]] == ["Is this the Feit-Thompson theorem?", "Yes, it is."])
        pg.locator('.thread[data-t="2"] button', has_text="Resolve").click()
        pg.wait_for_selector('.thread[data-t="2"]', state="detached", timeout=5000)
        check("resolve removes the thread and its highlight", len(comments()) == 1 and pg.locator('.marks div[data-t="2"]').count() == 0)
        pg.locator(".toast button", has_text="Undo").first.click()
        pg.wait_for_selector('.thread[data-t="2"]', timeout=5000)
        check("Undo brings it back", len(comments()) == 2)

        # --- a general comment
        pg.locator("#generalBtn").click()
        pg.locator(".composer textarea").fill("A general note.")
        pg.locator(".composer button", has_text="Comment").click()
        pg.wait_for_selector('.thread[data-t="3"]', timeout=5000)
        check("a general comment has no anchor and is listed first", "anchor" not in comments()[2]
              and pg.locator(".thread").first.get_attribute("data-t") == "3")
        check("the toolbar shows Resolve all with the number of comments, on one line with the other buttons",
              pg.locator("#resolveAllBtn").inner_text() == "Resolve all (3)"
              and len({round(b.bounding_box()["y"]) for b in pg.locator(".stools button").all()}) == 1)

        # --- dark pages, images in their colors
        pg.locator("#pageNum").fill("1")
        pg.locator("#pageNum").press("Enter")
        pg.wait_for_timeout(400)
        dark0 = pg.evaluate("() => document.body.classList.contains('darkpages')")
        if dark0:
            pg.locator("#darkBtn").click()
        check("normal pages: no filter on the page", pg.evaluate("() => getComputedStyle(document.querySelector('.page canvas.base')).filter") == "none")
        pg.locator("#darkBtn").click()
        check("dark pages: the page is inverted", "invert" in pg.evaluate("() => getComputedStyle(document.querySelector('.page canvas.base')).filter"))
        px = pg.evaluate("""() => {
          const c = document.querySelector('.page[data-n="1"] canvas.imgs'), ctx = c.getContext('2d');
          const sx = c.width / 612, sy = c.height / 792;  // the image is at x 72..272, y 460..610 in PDF units
          const at = (x, y) => [...ctx.getImageData(Math.round(x * sx), Math.round((792 - y) * sy), 1, 1).data];
          return {right: at(262, 600), left: at(82, 600), text: at(300, 680), visible: getComputedStyle(c).display};
        }""")
        check("the image layer holds the image in its colors", px["right"][3] == 255 and px["right"][0] > 200 and px["left"][0] < 60, px)
        check("the image layer is empty where the text is", px["text"][3] == 0, px)
        check("the image layer shows only in dark mode", px["visible"] == "block", px)
        lk = pg.evaluate(link_px)
        check("dark pages: the link keeps its color in the canvas, with no blend layer", lk["blend"] == 0
              and lk["inner"][2] - lk["inner"][0] > 80
              and "invert" in pg.evaluate("() => getComputedStyle(document.querySelector('.page[data-n=\"1\"] canvas.base')).filter"), lk)

        # --- zoom
        z0 = pg.locator("#zoomLabel").inner_text()
        pg.locator("#zoomIn").click()
        pg.wait_for_selector('.page[data-n="1"] .marks div[data-t="1"]', timeout=8000)
        check("zoom in changes the scale and keeps the highlights", pg.locator("#zoomLabel").inner_text() != z0)
        pg.locator("#zoomFit").click()
        pg.wait_for_timeout(300)
        check("Fit goes back to the page width", pg.locator("#zoomLabel").inner_text() == z0)

        # --- pinch to zoom on a trackpad: Chrome and Firefox send ctrl + wheel, Safari sends gesture events
        pg.locator("#zoomIn").click()
        check("a zoom keeps the old drawing until the new one is ready (no blank page)",
              pg.locator('.page[data-n="1"] canvas.base').count() >= 1 and pg.locator('.page[data-n="1"] .loading').count() == 0)
        pg.locator("#zoomFit").click()
        pg.wait_for_selector('.page[data-n="1"] .marks div[data-t="1"]', timeout=8000)
        WORD_AT = """([x, y]) => { const e = document.elementFromPoint(x, y); return e && e.closest('.textLayer') ? e.textContent : null; }"""
        span = pg.locator('.page[data-n="1"] .textLayer span', has_text="A second claim").first.bounding_box()
        x, y = span["x"] + 20, span["y"] + span["height"] / 2
        before = pg.evaluate(WORD_AT, [x, y])
        s0 = float(pg.locator("#zoomLabel").inner_text().rstrip("%"))
        pg.mouse.move(x, y)
        pg.keyboard.down("Control")
        for _ in range(5):
            pg.mouse.wheel(0, -20)  # fingers apart: zoom in
        pg.keyboard.up("Control")
        pg.wait_for_timeout(700)
        s1 = float(pg.locator("#zoomLabel").inner_text().rstrip("%"))
        check("a pinch out (ctrl + wheel) zooms in", s1 > s0 * 1.5, (s0, s1))
        check("the browser does not zoom the whole page", pg.evaluate("() => window.visualViewport.scale") == 1)
        pg.wait_for_selector('.page[data-n="1"] .marks div[data-t="1"]', timeout=8000)
        check("the words under the fingers stay under them", pg.evaluate(WORD_AT, [x, y]) == before, (before, pg.evaluate(WORD_AT, [x, y])))
        pg.evaluate("""([x, y]) => {
          const box = document.getElementById('pages');
          const ev = (type, scale) => { const e = new Event(type, {bubbles: true, cancelable: true});
            Object.assign(e, {scale, clientX: x, clientY: y}); box.dispatchEvent(e); };
          ev('gesturestart', 1); ev('gesturechange', 0.8); ev('gesturechange', 0.5); ev('gestureend', 0.5);
        }""", [x, y])
        pg.wait_for_timeout(500)
        s2 = float(pg.locator("#zoomLabel").inner_text().rstrip("%"))
        check("a Safari pinch in (gesture events) zooms out", abs(s2 - s1 * 0.5) < 2, (s1, s2))
        pg.locator("#zoomFit").click()
        pg.wait_for_selector('.page[data-n="1"] .marks div[data-t="1"]', timeout=8000)

        # --- the chat
        pg.locator("#chatOpen").click()
        check("the chat opens with a hint", "Ask about this PDF" in pg.locator("#chatMsgs").inner_text())
        pg.locator("#chatText").fill("hello")
        pg.locator("#chatText").press("Enter")
        pg.wait_for_function("() => document.querySelector('#chatMsgs').innerText.includes('Hello from fake Claude')", timeout=10000)
        check("Claude answers in the chat", True)
        pg.locator("#chatText").fill("please EDIT the comments")
        pg.locator("#chatText").press("Enter")
        pg.wait_for_selector('.thread[data-t="4"]', timeout=10000)
        check("Claude can add a comment, and the sidebar shows it", "Added by fake Claude" in pg.locator("#threads").inner_text())
        check("a notice says that Claude changed the comments",
              "Claude changed the comments" in " ".join(pg.locator(".toast").all_inner_texts()))
        pg.locator("#chatAsk").check()
        pg.locator("#chatText").fill("EDIT again")
        pg.locator("#chatText").press("Enter")
        pg.wait_for_function("() => document.querySelector('#chatSend').textContent === 'Send'", timeout=10000)
        pg.wait_for_timeout(500)
        check("Ask only does not change the comments", len(comments()) == 4)
        pg.locator("#chatAsk").uncheck()

        # --- the text size of the chat: Cmd/Ctrl+= and - with the focus in the chat, or Ctrl+wheel on the chat
        CZOOM = "() => getComputedStyle(document.querySelector('#chatMsgs')).zoom"
        FS = "() => document.querySelector('#chatMsgs .cm').getBoundingClientRect().height"
        h0, zoom0 = pg.evaluate(FS), pg.evaluate("() => S.scale")
        pg.locator("#chatText").focus()
        pg.keyboard.press("Control+Equal")
        pg.keyboard.press("Control+Equal")
        check("Ctrl+= in the chat makes the chat text larger", float(pg.evaluate(CZOOM)) > 1.15 and pg.evaluate(FS) > h0 * 1.1)
        check("the chat panel keeps its size", abs(pg.locator("#chatBox").bounding_box()["width"] - 300) < 2)
        pg.keyboard.press("Control+Minus")
        check("Ctrl+- in the chat makes the chat text smaller", 1.05 < float(pg.evaluate(CZOOM)) < 1.15)
        pg.wait_for_timeout(400)
        check("the chat text size stays after a reload (localStorage)",
              pg.evaluate("() => localStorage.getItem('pdfview.chatZoom')") == "1.1")
        pg.keyboard.press("Control+Digit0")
        check("Ctrl+0 in the chat gives the default text size", float(pg.evaluate(CZOOM)) == 1)
        mb = pg.locator("#chatMsgs").bounding_box()
        pg.mouse.move(mb["x"] + mb["width"] / 2, mb["y"] + mb["height"] / 2)
        pg.keyboard.down("Control")
        pg.mouse.wheel(0, -40)
        pg.keyboard.up("Control")
        pg.wait_for_timeout(200)
        check("a pinch (Ctrl+wheel) on the chat makes the chat text larger, and does not zoom the PDF",
              float(pg.evaluate(CZOOM)) > 1.2 and pg.evaluate("() => S.scale") == zoom0)
        pg.locator("#chatText").focus()
        pg.keyboard.press("Control+Digit0")
        pg.wait_for_timeout(400)
        check("the default text size removes the saved size", pg.evaluate("() => localStorage.getItem('pdfview.chatZoom')") is None)

        # --- the Oracle: a background run of /answer-pdf (the stand-in replies to each open thread)
        n_open = sum(1 for t in comments() if t["msgs"][-1]["author"] != "claude")
        check("the Oracle button is ready when there are open comments", n_open > 0 and pg.locator("#oracleBtn").is_enabled())
        pg.locator("#oracleBtn").click()
        pg.wait_for_function("() => document.querySelector('#oracleBtn').textContent.startsWith('Working')", timeout=5000)
        check("while it runs, the button says Working", "Working" in pg.locator("#oracleBtn").inner_text())
        pg.wait_for_function("() => document.querySelector('#oracleBtn').textContent === 'Oracle'", timeout=15000)
        pg.wait_for_timeout(500)
        check("the Oracle answered every open comment", all(t["msgs"][-1]["author"] == "claude" for t in comments())
              and "Answer from the fake Oracle" in pg.locator("#threads").inner_text())
        check("a notice says that Claude answered, with a report",
              "Claude has answered the comments" in " ".join(pg.locator(".toast").all_inner_texts()))
        pg.locator(".toast button", has_text="Report").first.click()
        check("the report shows the end of the log", "fake Oracle answered" in pg.locator("#reportText").inner_text())
        pg.locator("#reportDlg button", has_text="Close").click()
        check("the Oracle button is off with no open comments", pg.locator("#oracleBtn").is_disabled())
        pg.wait_for_function("() => !document.querySelector('#undoBtn').disabled", timeout=5000)
        pg.locator("#undoBtn").click()
        pg.wait_for_timeout(800)
        check("one Undo takes back all the replies of the Oracle",
              sum(1 for t in comments() if t["msgs"][-1]["author"] != "claude") == n_open)

        # --- a comment with the sidebar hidden: the box opens on the page, and the sidebar stays hidden
        NOSIDE = "() => document.body.classList.contains('noside')"
        pg.locator("#sideBtn").click()
        pg.wait_for_timeout(300)
        check("the Comments button hides the sidebar", pg.evaluate(NOSIDE))
        n0 = len(comments())
        pg.evaluate(SELECT, [1, "A second claim", "A second claim"])
        pg.wait_for_selector("#cbtn", state="visible", timeout=3000)
        pg.locator("#cbtn").click()
        check("with the sidebar hidden, the comment box opens on the page and the sidebar stays hidden",
              pg.locator("#pop").is_visible() and pg.evaluate(NOSIDE))
        pg.locator("#pop textarea").press("Escape")
        check("Esc closes the comment box and its draft highlight",
              pg.locator("#pop").count() == 0 and pg.locator(".marks div.draft").count() == 0)
        pg.evaluate(SELECT, [1, "A second claim", "A second claim"])
        pg.keyboard.press("Control+Shift+M")
        pg.wait_for_selector("#pop", timeout=3000)
        box1 = pg.locator('.page[data-n="1"]').bounding_box()
        line1 = pg.locator('.page[data-n="1"] .textLayer span', has_text="A second claim").first.bounding_box()
        out = (box1["x"] + 8, line1["y"] - 12)  # the left margin of the page, above the line: outside the box
        pg.mouse.click(*out)
        check("a click on the page outside an empty box closes it", pg.locator("#pop").count() == 0)
        pg.evaluate(SELECT, [1, "A second claim", "A second claim"])
        pg.keyboard.press("Control+Shift+M")
        pg.locator("#pop textarea").fill("Without the sidebar.")
        pg.mouse.click(*out)
        check("a box with text stays open after a click outside it", pg.locator("#pop").count() == 1)
        pg.locator("#pop button", has_text="Comment").click()
        pg.wait_for_selector("#pop", state="detached", timeout=5000)
        pg.wait_for_timeout(300)
        ts = comments()
        check("the comment is saved, and the sidebar stays hidden", len(ts) == n0 + 1 and pg.evaluate(NOSIDE)
              and ts[-1]["msgs"][0]["text"] == "Without the sidebar." and ts[-1]["anchor"]["quote"] == "A second claim", ts[-1])
        pg.locator("#sideBtn").click()

        # --- a word that breaks with a hyphen at a line end ("argu-" / "ment" on page 2)
        pg.locator("#pageNum").fill("2")
        pg.locator("#pageNum").press("Enter")
        pg.wait_for_selector('.page[data-n="2"] .textLayer span', timeout=8000)
        pg.wait_for_timeout(500)
        ok = pg.evaluate("""() => {
          const spans = [...document.querySelectorAll('.page[data-n="2"] .textLayer span')];
          const a = spans.find(s => s.textContent.includes('careful argu')), b = spans.find(s => s.textContent.includes('ment that'));
          if (!a || !b) return false;
          const r = document.createRange();
          r.setStart(a.firstChild, a.textContent.indexOf('careful'));
          r.setEnd(b.firstChild, b.textContent.indexOf('ment') + 4);
          const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
          return true;
        }""")
        check("words over a hyphenated line end can be selected", ok)
        pg.wait_for_selector("#cbtn", state="visible", timeout=3000)
        pg.locator("#cbtn").click()
        pg.locator("#pop textarea").fill("Which argument?")
        pg.locator("#pop textarea").press("Control+Enter")
        pg.wait_for_selector("#pop", state="detached", timeout=5000)
        hy = comments()[-1]
        pg.wait_for_timeout(500)
        check("a comment over a hyphenated line end is saved, and its words are found on the page",
              pg.locator(f'.page[data-n="2"] .marks div[data-t="{hy["id"]}"]').count() >= 1
              and pg.locator(f'.thread[data-t="{hy["id"]}"] .pill.lost').count() == 0, hy["anchor"])
        n0 = len(comments())
        cli("add", str(pdf), "Without the hyphen", "--page", "2", "--quote", "careful argument that")
        cli("add", str(pdf), "As saved before the fix", "--page", "2", "--quote", "careful argu- ment that")
        pg.wait_for_function(f"() => document.querySelectorAll('.thread').length >= {n0 + 2}", timeout=8000)
        pg.wait_for_timeout(800)
        ids = [t["id"] for t in comments()[-2:]]
        check("a quote without the hyphen (from pdftotext) is found",
              pg.locator(f'.page[data-n="2"] .marks div[data-t="{ids[0]}"]').count() >= 1)
        check("a quote saved as 'argu- ment' (older comments) is found",
              pg.locator(f'.page[data-n="2"] .marks div[data-t="{ids[1]}"]').count() >= 1)

        # --- a comment on an area: ⌥ Option + drag a box over the image of page 1 (x 72..272, y 182..332 from the top)
        pg.locator("#pageNum").fill("1")
        pg.locator("#pageNum").press("Enter")
        pg.wait_for_timeout(600)
        PT = """([n, x, y]) => { const e = document.querySelector(`.page[data-n="${n}"]`), r = e.getBoundingClientRect();
          return [r.left + x * r.width / 612, r.top + y * r.height / 792]; }"""
        a0, a1 = pg.evaluate(PT, [1, 68, 178]), pg.evaluate(PT, [1, 276, 336])
        pg.keyboard.down("Alt")
        pg.mouse.move(*a0)
        pg.mouse.down()
        pg.mouse.move((a0[0] + a1[0]) / 2, (a0[1] + a1[1]) / 2)
        pg.mouse.move(*a1)
        pg.mouse.up()
        pg.keyboard.up("Alt")
        pg.wait_for_selector("#pop", timeout=3000)
        check("⌥ + drag draws a box and opens the comment box for an area", "area of page 1" in pg.locator("#pop").inner_text())
        pg.locator("#pop textarea").fill("What does this figure show?")
        pg.locator("#pop textarea").press("Control+Enter")
        pg.wait_for_selector("#pop", state="detached", timeout=5000)
        bx = comments()[-1]
        saved = bx["anchor"].get("box") or [0, 0, 0, 0]
        check("the box is saved in PDF points from the top-left corner", bx["anchor"]["page"] == 1
              and all(abs(u - v) < 3 for u, v in zip(saved, [68, 178, 276, 336])), bx["anchor"])
        pg.wait_for_timeout(400)
        BOXW = f"""() => {{ const m = document.querySelector('.page[data-n="1"] .marks div.box[data-t="{bx["id"]}"]');
          return m ? m.getBoundingClientRect().width / m.closest('.page').getBoundingClientRect().width : null; }}"""
        w1 = pg.evaluate(BOXW)
        check("the page shows a frame at the area", w1 is not None and abs(w1 - 208 / 612) < 0.01, w1)
        check("the sidebar card says it is an area", "p. 1 ▭" in pg.locator(f'.thread[data-t="{bx["id"]}"]').inner_text())
        pg.locator("#zoomIn").click()
        pg.wait_for_selector(f'.page[data-n="1"] .marks div.box[data-t="{bx["id"]}"]', timeout=8000)
        pg.wait_for_timeout(600)
        w2 = pg.evaluate(BOXW)
        check("after a zoom the frame is still at the area", w2 is not None and abs(w2 - 208 / 612) < 0.01, w2)
        pg.locator("#zoomFit").click()
        pg.wait_for_timeout(800)
        pg.locator(f'.page[data-n="1"] .marks div.box[data-t="{bx["id"]}"] .btag').click()
        check("a click on the tab of a frame selects its comment", "active" in (pg.locator(f'.thread[data-t="{bx["id"]}"]').get_attribute("class") or ""))
        # ⌥ Option + drag over text: the text in the box goes with the comment
        check("there is no Box button in the header", pg.locator("#boxBtn").count() == 0)
        pg.locator("#pages").focus()
        pg.keyboard.press("b")
        check("the key b does nothing now", not pg.evaluate("() => document.body.classList.contains('boxmode')")
              and pg.locator(".toast", has_text="Draw a box").count() == 0)
        pg.keyboard.down("Alt")
        check("⌥ Option down makes the cursor a cross", pg.evaluate("() => getComputedStyle(document.querySelector('.page')).cursor") == "crosshair")
        t0, t1 = pg.evaluate(PT, [1, 66, 98]), pg.evaluate(PT, [1, 560, 136])
        pg.mouse.move(*t0); pg.mouse.down(); pg.mouse.move(*t1); pg.mouse.up()
        pg.keyboard.up("Alt")
        pg.wait_for_selector("#pop", timeout=3000)
        pg.locator("#pop textarea").fill("These two lines")
        pg.locator("#pop textarea").press("Control+Enter")
        pg.wait_for_selector("#pop", state="detached", timeout=5000)
        q = comments()[-1]["anchor"].get("quote", "")
        check("a box over text keeps the text in the box", "We show that" in q and "A second claim" in q, q)
        check("with ⌥ Option up, the cursor is normal again", pg.evaluate("() => getComputedStyle(document.querySelector('.page')).cursor") != "crosshair")
        sp = pg.locator('.page[data-n="1"] .textLayer span', has_text="We show that").first.bounding_box()
        pg.mouse.move(sp["x"] + 2, sp["y"] + sp["height"] / 2)
        pg.mouse.down()
        pg.mouse.move(sp["x"] + 120, sp["y"] + sp["height"] / 2, steps=5)
        pg.mouse.up()
        picked = pg.evaluate("() => getSelection().toString()")
        check("a mouse drag still selects the text inside a box frame", len(picked.strip()) > 5, repr(picked))
        pg.evaluate("() => getSelection().removeAllRanges()")
        pg.wait_for_timeout(200)
        # the × on the tab of a frame deletes that box comment, without the sidebar
        nb = len(comments())
        pg.locator(f'.page[data-n="1"] .marks div.box[data-t="{bx["id"]}"] .btag .bx').click()
        pg.wait_for_selector(f'.page[data-n="1"] .marks div.box[data-t="{bx["id"]}"]', state="detached", timeout=5000)
        check("× on a frame deletes its box comment", len(comments()) == nb - 1 and all(t["id"] != bx["id"] for t in comments()))
        pg.locator(".toast button", has_text="Undo").first.click()
        pg.wait_for_selector(f'.page[data-n="1"] .marks div.box[data-t="{bx["id"]}"]', timeout=5000)
        check("Undo brings the box comment back", len(comments()) == nb)
        # the command line: add --box, crop, text --box
        r = cli("add", str(pdf), "On the image", "--page", "1", "--box", "72,182,272,332")
        check("pdfview add --box", r.returncode == 0, r.stderr)
        r = cli("list", str(pdf))
        check("pdfview list shows a box", "box 72,182,272,332" in r.stdout, r.stdout[-300:])
        r = cli("crop", str(pdf), "--page", "1", "--box", "72,182,272,332")
        png = Path(r.stdout.strip())
        check("pdfview crop makes a PNG of the area", r.returncode == 0 and png.is_file() and png.read_bytes()[:4] == b"\x89PNG", r.stderr)
        r = cli("text", str(pdf), "--page", "1", "--box", "60,60,560,140")
        check("pdfview text --box prints only the text in the area", "We show that" in r.stdout and "dark mode" not in r.stdout, r.stdout)
        # the page must see the change from the command line first, else the next change gets "The comments changed"
        pg.wait_for_selector(f'.page[data-n="1"] .marks div.box[data-t="{max(t["id"] for t in comments())}"]', timeout=5000)

        # --- a highlight: selected words with no comment
        def hdata():
            return json.loads(side.read_text()).get("highlights", []) if side.exists() else []
        nt = len(comments())
        check("words can be selected for a highlight", pg.evaluate(SELECT, [1, "The constant c", "dimension d"]))
        pg.wait_for_selector("#hbtn", state="visible", timeout=3000)
        counts0 = pg.locator("#counts").inner_text()
        check("the selection bar has Comment and Highlight side by side", pg.locator("#selbar #cbtn").is_visible()
              and pg.locator("#selbar #hbtn").is_visible())
        pg.locator("#hbtn").click()
        pg.wait_for_function("() => document.querySelectorAll('.page[data-n=\"1\"] .marks div.hl').length > 0", timeout=5000)
        hs = hdata()
        check("Highlight saves a highlight with its words, and no comment", len(hs) == 1 and hs[0]["anchor"]["quote"] == "dimension d"
              and len(comments()) == nt, hs)
        check("a highlight is not a comment: the counts do not change", pg.locator("#counts").inner_text() == counts0,
              (counts0, pg.locator("#counts").inner_text()))
        pg.locator('.page[data-n="1"] .marks div.hl').first.click()
        pg.wait_for_selector("#hmenu", state="visible", timeout=3000)
        pg.locator("#hremove").click()
        pg.wait_for_function("() => !document.querySelector('.page[data-n=\"1\"] .marks div.hl')", timeout=5000)
        check("Remove takes the highlight away", hdata() == [])
        pg.locator(".toast button", has_text="Undo").first.click()
        pg.wait_for_function("() => document.querySelectorAll('.page[data-n=\"1\"] .marks div.hl').length > 0", timeout=5000)
        check("Undo brings the highlight back", len(hdata()) == 1)
        pg.locator('.page[data-n="1"] .marks div.hl').first.click()
        pg.wait_for_selector("#hmenu", state="visible", timeout=3000)
        pg.locator("#hcomment").click()
        pg.wait_for_selector("#pop", timeout=3000)
        pg.locator("#pop textarea").fill("Which dimension?")
        pg.locator("#pop textarea").press("Control+Enter")
        pg.wait_for_selector("#pop", state="detached", timeout=5000)
        pg.wait_for_timeout(400)
        last = comments()[-1]
        check("Comment on a highlight makes it a comment, in one step", hdata() == [] and len(comments()) == nt + 1
              and last["anchor"]["quote"] == "dimension d" and last["msgs"][0]["text"] == "Which dimension?", (hdata(), last))
        pg.evaluate(SELECT, [1, "The constant c", "The constant c"])
        pg.wait_for_selector("#hbtn", state="visible", timeout=3000)
        pg.keyboard.press("Control+Shift+H")
        pg.wait_for_timeout(800)
        check("⌘/Ctrl+Shift+H highlights the selection", len(hdata()) == 1 and hdata()[0]["anchor"]["quote"] == "The constant c", hdata())
        r = cli("list", str(pdf))
        check("pdfview list shows the highlights after the comments", "Highlights (no comment): 1" in r.stdout and '"The constant c"' in r.stdout, r.stdout[-200:])

        # --- notes to the app (self-improving apps): the dashboard and the note subcommand
        def issues():
            calls = [json.loads(l) for l in gh_log.read_text().splitlines()] if gh_log.exists() else []
            return [c for c in calls if c["argv"][:2] == ["issue", "create"]]
        def notes():
            return pg.locator(".kitDashNote .kitDashText").all_inner_texts()
        pg.locator("#pages").click()
        pg.keyboard.press("Control+Shift+Period")
        check("⌘/Ctrl+Shift+. opens the dashboard, with the new note box focused",
              pg.locator(".kitDash").is_visible() and pg.evaluate("() => document.activeElement.closest('.kitDashNew') !== null"))
        check("the dashboard has the notes to send, a new note, Send all and the status",
              all(t in pg.inner_text(".kitDash").lower() for t in ("notes to send", "new note", "send all", "status")))
        pg.keyboard.press("Escape")
        check("Esc closes the dashboard", pg.locator(".kitDash").count() == 0)
        pg.keyboard.press("Control+Shift+Period")
        new = pg.locator(".kitDashNew textarea")
        new.fill("The zoom goes back to Fit after a reload.")
        new.press("Enter")
        new.fill("A second note, to delete.")
        pg.locator(".kitDash button", has_text="Add").click()
        new.fill("A third note.")
        new.press("Enter")
        pg.wait_for_timeout(300)
        check("Add puts the notes in the list and sends nothing", len(notes()) == 3 and not issues(), (notes(), issues()))
        pg.locator(".kitDashNote", has_text="to delete").locator(".kitDashDel").click()
        pg.wait_for_timeout(300)
        pg.locator(".kitDashText", has_text="A third note").click()
        pg.locator(".kitDashNote textarea").fill("A third note, changed.")
        pg.locator(".kitDashNote textarea").press("Enter")
        pg.wait_for_timeout(400)
        check("× deletes a note, and a click on a note edits it",
              notes() == ["The zoom goes back to Fit after a reload.", "A third note, changed."], notes())
        pg.keyboard.press("Control+Shift+Period")
        pg.reload()
        pg.wait_for_selector('.page[data-n="1"] .textLayer span', timeout=20000)
        pg.locator("#pages").click()
        pg.keyboard.press("Control+Shift+Period")
        pg.wait_for_timeout(400)
        check("the notes wait on the server: they are still in the list after a reload", len(notes()) == 2, notes())
        check("Send all shows the number of notes", pg.locator(".kitDashSend").inner_text() == "Send all (2)",
              pg.locator(".kitDashSend").inner_text())
        pg.locator(".kitDashSend").click()
        pg.wait_for_timeout(1500)
        iss = issues()
        body = iss[0]["stdin"] if iss else ""
        check("Send all makes one issue with the label note for each note", len(iss) == 2 and "note" in iss[0]["argv"]
              and "The zoom goes back to Fit" in body and "A third note, changed." in iss[1]["stdin"], iss)
        check("the note has the context of the viewer from the time of Add", '"page":' in body and '"doc_type": ".pdf"' in body
              and '"commit":' in body and '"clicks"' in body and "Source: user" in body, body)
        check("the note has no path and no name of the PDF", str(tmp) not in body and "paper.pdf" not in body, body)
        check("Send all empties the list and tells the user", notes() == [] and "2 notes sent to GitHub" in pg.inner_text("#toast"),
              (notes(), pg.inner_text("#toast")))
        pg.keyboard.press("Escape")
        r = subprocess.run([sys.executable, PDFVIEW, "note", "The list subcommand needs a --open option."],
                           env={**env, "FAKE_GH_FAIL": "1"}, capture_output=True, text=True)
        check("without the network, pdfview note keeps the note in the queue", "kept" in r.stdout and queue.exists()
              and len(queue.read_text().splitlines()) == 1, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, PDFVIEW, "note", "-"], env=env, input="Second note.",
                           capture_output=True, text=True)
        texts = [c["stdin"] for c in issues()]
        check("the next note sends the queue first", "note sent" in r.stdout and not queue.exists() and len(texts) == 4
              and "--open option" in texts[2] and "Second note." in texts[3] and "Source: agent" in texts[3],
              r.stdout + r.stderr)

        # --- the sandbox of a proposal: pdfview try N FILE opens copies of the PDF and its comments in the server of
        # the worktree of note N, on port 8900+N. Here the "worktree" is a link to this pdfview.
        wt = tmp / "wt"
        (wt / "note-pdfview-97").mkdir(parents=True)
        (wt / "note-pdfview-97" / "pdfview").symlink_to(HERE.parent)
        before = side.read_text()
        tenv = {**env, "VIEWKIT_WT": str(wt)}
        r = subprocess.run([sys.executable, PDFVIEW, "try", "97", str(pdf), "--no-open"], env=tenv, capture_output=True, text=True)
        box = wt / "note-pdfview-97" / "sandbox-note-97"
        check("pdfview try copies the PDF and its comments into the sandbox", (box / "paper.pdf").is_file()
              and (box / ".pdfview" / "paper.pdf.json").read_text() == before, r.stdout + r.stderr)
        check("pdfview try starts the sandbox server on port 8900+N with a link to the copy",
              "8997" in r.stdout and "sandbox-note-97/paper.pdf" in r.stdout, r.stdout + r.stderr)
        link = next((w for w in r.stdout.split() if w.startswith("http")), "")
        sb = b.new_page()
        sb.goto(link)
        sb.wait_for_selector('.page[data-n="1"] .textLayer span', timeout=20000)
        sb.locator("#side .thread button", has_text="Resolve").first.click(timeout=5000)
        sb.wait_for_timeout(600)
        check("a change in the sandbox changes the copy, not the real comments", side.read_text() == before
              and (box / ".pdfview" / "paper.pdf.json").read_text() != before)
        sb.close()
        r = subprocess.run([sys.executable, PDFVIEW, "try", "--stop", "97"], env=tenv, capture_output=True, text=True)
        check("pdfview try --stop stops the sandbox", "stopped" in r.stdout, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, PDFVIEW, "try", "96"], env=tenv, capture_output=True, text=True)
        check("pdfview try says when there is no proposal", "no proposal for note 96" in r.stderr, r.stderr)

        check("no errors in the console", not errs, errs)

        # --- the folder page (last: its 403 and 404 answers show as console errors)
        pg2 = pg  # the same page and cookie
        r = pg2.goto(f"{BASE}/?d=" + str(tmp.relative_to(Path.home())))
        check("a folder that was not passed to pdfview does not open", r.status == 403, r.status)
        cli(str(tmp), "--no-open")
        pg2.goto(f"{BASE}/?d=" + str(tmp.relative_to(Path.home())))
        check("the folder page lists the PDF with its open comments", "paper.pdf" in pg2.inner_text("body")
              and "open" in pg2.inner_text("body"), pg2.inner_text("body"))
        check("the folder page does not list the hidden comments folder", ".pdfview" not in pg2.inner_text("body"))
        check("the folder page has the pdfview icon", 'href="/kit/icons/pdf.png"' in pg2.content()
              and pg2.request.get(f"{BASE}/kit/icons/pdf.png").headers["content-type"] == "image/png")
        r = pg2.goto(f"{BASE}/pdf?f=" + str(side.relative_to(Path.home())))
        check("the server does not serve the hidden comments file", r.status == 404, r.status)
        b.close()
finally:
    subprocess.run([sys.executable, PDFVIEW, "--stop"], env=env, capture_output=True)
    if os.environ.get("PDFVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
