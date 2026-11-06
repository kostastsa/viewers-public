"""Browser test of the chat panel, with a stand-in `claude` (viewkit/tests/fake_claude.py) and its own server on port 8809.
Needs Playwright and its Chromium, and pandoc. Run:
    uv run --with playwright python tests/chat_browser_test.py
It makes its temporary folder in tests/tmp and deletes it at the end. Set MDVIEW_KEEP_TMP=1 to keep it for debugging."""
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
MDVIEW = str(HERE.parent / "mdview.py")
FAKE = HERE.parent.parent / "viewkit" / "tests" / "fake_claude.py"
PORT = "8809"
# Under tests/tmp (git ignores it): inside the home folder, which is the root the server may open.
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="chatb-", dir=TMP_ROOT))
bindir = tmp / "bin"
bindir.mkdir()
(bindir / "claude").write_text('#!/bin/sh\nexec python3 "%s" "$@"\n' % FAKE)
(bindir / "claude").chmod((bindir / "claude").stat().st_mode | stat.S_IEXEC)
md = tmp / "notes.md"
ORIGINAL = "# Notes\n\nThe first paragraph says something about lemmas.\n\nThe second paragraph is short.\n"
md.write_text(ORIGINAL)
env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", MDVIEW_PORT=PORT, MDVIEW_CLAUDE=str(bindir / "claude"))
url = subprocess.run(["python3", MDVIEW, str(md), "--no-open"], env=env, capture_output=True, text=True, check=True).stdout.strip()
chat_state = Path.home() / ".cache" / "mdview" / "chat" / (hashlib.sha1(str(md.resolve()).encode("utf-8")).hexdigest()[:16] + ".json")
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def send(pg, text):
    pg.locator("#chatText").fill(text)
    pg.locator("#chatText").press("Enter")


def focused(pg):
    try:
        pg.wait_for_function("() => document.activeElement === document.getElementById('chatText')", timeout=2000)
        return True
    except Exception:
        return False


try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(url)
        pg.wait_for_selector("#doc p")
        pg.wait_for_timeout(500)

        check("the chat starts closed, with a Chat button", pg.locator("#chatOpen").is_visible() and not pg.locator("#chatBox").is_visible())
        pg.locator("#chatOpen").click()
        check("the button opens the panel", pg.locator("#chatBox").is_visible() and not pg.locator("#chatOpen").is_visible())
        check("an empty chat shows a hint", "Ask about this document" in pg.locator("#chatMsgs").inner_text())

        # the selected passage goes with the message
        # select again at each poll: the page may draw the document again after it loads, and that drops a selection
        pg.wait_for_function("""() => {
            if (document.getElementById('chatCtx').textContent.includes('lemmas')) return true;
            const r = document.createRange(); r.selectNodeContents(document.querySelector('#doc p'));
            const s = getSelection(); s.removeAllRanges(); s.addRange(r); return false; }""", timeout=10000)
        check("the chip shows the selected passage", "lemmas" in pg.locator("#chatCtx").inner_text())

        send(pg, "hello there")
        pg.wait_for_selector(".cm.assistant .md", timeout=15000)
        pg.wait_for_function("() => document.querySelector('.cm.assistant .cfoot')", timeout=15000)
        check("the question shows on the right", "hello there" in pg.locator(".cm.user").first.inner_text())
        check("the passage shows with the question", "lemmas" in pg.locator(".cm.user .cq").first.inner_text())
        check("the answer streams into the panel", "Hello from fake Claude." in pg.locator(".cm.assistant").first.inner_text())
        check("the cost shows and the button is Send again", "$0.010" in pg.locator(".cm.assistant .cfoot").first.inner_text() and pg.locator("#chatSend").inner_text() == "Send")
        check("the text box is empty after sending", pg.locator("#chatText").input_value() == "")

        # Stop
        send(pg, "SLOW please")
        pg.wait_for_function("() => document.querySelector('#chatSend').textContent === 'Stop'", timeout=10000)
        check("while Claude answers the button is Stop and New is off", pg.locator("#chatNew").is_disabled())
        pg.wait_for_selector(".cm.assistant:last-child .md", timeout=10000)
        pg.locator("#chatSend").click()
        pg.wait_for_function("() => document.querySelector('#chatSend').textContent === 'Send'", timeout=10000)
        check("Stop ends the answer and marks it", "stopped" in pg.locator(".cm.assistant").last.inner_text())

        # an edit, with the Undo notice
        send(pg, "please EDIT the file")
        pg.wait_for_selector(".ctool", timeout=15000)
        pg.wait_for_function("() => document.getElementById('toastBtn') && !document.getElementById('toastBtn').hidden", timeout=15000)
        check("a tool step shows as a line", "Edit notes.md" in pg.locator(".ctool").first.inner_text(), pg.locator(".ctool").first.inner_text())
        check("a notice says that Claude changed the document", "Claude changed the document" in pg.locator("#toastText").inner_text())
        check("the file changed", "edited by fake claude" in md.read_text())
        pg.locator("#toastBtn").click()
        pg.wait_for_timeout(700)
        check("Undo in the notice takes the whole turn back", md.read_text() == ORIGINAL, md.read_text())

        # Ask only
        pg.locator("#chatAsk").check()
        send(pg, "please EDIT the file")
        pg.wait_for_function("() => document.querySelectorAll('.cm.assistant .cfoot').length >= 3", timeout=15000)
        check("Ask only: the file stays as it was", md.read_text() == ORIGINAL)
        pg.locator("#chatAsk").uncheck()

        # a code block in an answer has a Copy button
        pg.context.grant_permissions(["clipboard-read", "clipboard-write"])
        pg.wait_for_function("() => document.querySelectorAll('.cm.assistant').length >= 4 && document.querySelector('#chatSend').textContent === 'Send'", timeout=15000)
        send(pg, "show CODE")
        pg.wait_for_selector(".cm.assistant .kitCopy", timeout=15000)
        check("a code block in an answer has a Copy button", pg.locator(".cm.assistant .kitCode .kitCopy").count() == 1, pg.locator(".cm.assistant").last.inner_html())
        pg.locator(".kitCopy").last.click()
        pg.wait_for_timeout(200)
        check("Copy says Copied", pg.locator(".kitCopy").last.inner_text() == "Copied", pg.locator(".kitCopy").last.inner_text())
        check("Copy puts the code on the clipboard", pg.evaluate("navigator.clipboard.readText()").strip() == "print(1)")

        # resize: drag the top-left corner
        b0 = pg.locator("#chatBox").bounding_box()
        g = pg.locator("#chatBox .kitGrip.nw").bounding_box()
        pg.mouse.move(g["x"] + 5, g["y"] + 5)
        pg.mouse.down()
        pg.mouse.move(g["x"] - 95, g["y"] - 55, steps=6)
        pg.mouse.up()
        b1 = pg.locator("#chatBox").bounding_box()
        check("a drag on the corner makes the chat larger", abs(b1["width"] - b0["width"] - 100) < 3 and abs(b1["height"] - b0["height"] - 60) < 3, (b0, b1))
        check("the right and the bottom edges stay in place", abs(b1["x"] + b1["width"] - b0["x"] - b0["width"]) < 2 and abs(b1["y"] + b1["height"] - b0["y"] - b0["height"]) < 2)

        # the conversation is kept over a reload
        n = pg.locator(".cm").count()
        pg.reload()
        pg.wait_for_selector("#doc p")
        pg.wait_for_timeout(1200)
        check("after a reload the panel is open and the conversation is back", pg.locator("#chatBox").is_visible() and pg.locator(".cm").count() == n, f"{pg.locator('.cm').count()} vs {n}")
        b2 = pg.locator("#chatBox").bounding_box()
        check("after a reload the chat keeps its size", abs(b2["width"] - b1["width"]) < 2 and abs(b2["height"] - b1["height"]) < 2, (b1, b2))
        pg.locator("#chatBox .kitGrip.w").dblclick()
        b3 = pg.locator("#chatBox").bounding_box()
        check("a double-click on an edge gives the default size back", abs(b3["width"] - b0["width"]) < 2 and abs(b3["height"] - b0["height"]) < 2, (b0, b3))

        # New chat
        pg.once("dialog", lambda d: d.accept())
        pg.locator("#chatNew").click()
        pg.wait_for_timeout(800)
        check("New empties the conversation", pg.locator(".cm").count() == 0 and "Ask about this document" in pg.locator("#chatMsgs").inner_text())

        pg.locator("#chatMin").click()
        check("the minus button closes the panel", pg.locator("#chatOpen").is_visible() and not pg.locator("#chatBox").is_visible())

        # the hotkey
        pg.keyboard.press("Control+s")
        check("Ctrl+S opens the chat and focuses the text box", pg.locator("#chatBox").is_visible() and focused(pg))
        pg.keyboard.press("Control+s")
        check("Ctrl+S in the text box hides the chat", not pg.locator("#chatBox").is_visible())
        pg.keyboard.press("Control+s")
        focused(pg)
        pg.locator("#doc p").first.click()
        pg.keyboard.press("Control+s")
        check("Ctrl+S with the cursor elsewhere moves it to the text box", pg.locator("#chatBox").is_visible() and focused(pg))

        # the pulse, with the panel closed
        send(pg, "SLOW please")
        pg.wait_for_function("() => document.querySelector('#chatSend').textContent === 'Stop'", timeout=10000)
        pg.locator("#chatMin").click()
        check("no pulse while the answer is written", not pg.locator("#chatOpen.ready").count())
        pg.wait_for_selector("#chatOpen.ready", timeout=20000)
        check("the Chat button pulses when the answer ends", pg.locator("#chatOpen").evaluate("e => getComputedStyle(e).animationName") == "chatReady")
        check("the open panel does not pulse while the window has focus", not pg.locator("#chatBox.ready").count())
        pg.locator("#chatOpen").click()
        check("opening the chat stops the pulse", not pg.locator("#chatOpen.ready").count())

        # the pulse, with the panel open and the window without focus
        pg.evaluate("() => { document.hasFocus = () => false; chat.attn.notify(); }")
        check("an open panel in an unfocused window pulses", pg.locator("#chatBox.ready").count() == 1 and pg.locator("#chatBox").evaluate("e => getComputedStyle(e).animationName") == "chatBoxReady")
        check("the tab title gets a dot", pg.title().startswith("● "), pg.title())
        pg.evaluate("() => { document.hasFocus = () => true; dispatchEvent(new Event('focus')); }")
        check("coming back clears the pulse and the dot", not pg.locator("#chatBox.ready").count() and not pg.title().startswith("● "), pg.title())

        check("no errors in the console", not errs, str(errs))
        b.close()
finally:
    subprocess.run(["python3", MDVIEW, "--stop"], env=env, capture_output=True)
    for f in (Path.home() / ".cache" / "mdview" / n for n in (f"token-{PORT}", f"grants-{PORT}.json")):
        f.unlink(missing_ok=True)
    chat_state.unlink(missing_ok=True)
    if os.environ.get("MDVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
