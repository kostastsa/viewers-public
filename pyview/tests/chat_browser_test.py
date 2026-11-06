"""Browser test of the chat panel, with a stand-in `claude` (viewkit/tests/fake_claude.py) and its own server on port 8808.
Needs Playwright (see tests/browser_test.py). Run: <python with playwright> tests/chat_browser_test.py"""
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PYVIEW = str(HERE.parent / "pyview.py")
PORT = "8808"
# Under tests/tmp (git ignores it): inside the home folder, which is the root the server may open.
TMP_ROOT = HERE / "tmp"
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="chatb-", dir=TMP_ROOT))
bindir = tmp / "bin"
bindir.mkdir()
(bindir / "claude").write_text('#!/bin/sh\nexec python3 "%s" "$@"\n' % (HERE.parent.parent / "viewkit" / "tests" / "fake_claude.py"))
(bindir / "claude").chmod((bindir / "claude").stat().st_mode | stat.S_IEXEC)
nb = tmp / "nb.py"
shutil.copy(HERE / "fixture.py", nb)
ORIGINAL = nb.read_text()
env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", PYVIEW_PORT=PORT)
url = subprocess.run(["python3", PYVIEW, str(nb), "--no-open"], env=env, capture_output=True, text=True, check=True).stdout.strip()
failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def focused(pg):
    try:
        pg.wait_for_function("() => document.activeElement === document.getElementById('chatText')", timeout=2000)
        return True
    except Exception:
        return False


def send(pg, text):
    pg.locator("#chatText").fill(text)
    pg.locator("#chatText").press("Enter")


try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(url)
        pg.wait_for_selector(".cell")
        pg.wait_for_timeout(500)

        check("the chat starts closed, with a Chat button", pg.locator("#chatOpen").is_visible() and not pg.locator("#chatBox").is_visible())
        pg.locator("#chatOpen").click()
        check("the button opens the panel", pg.locator("#chatBox").is_visible() and not pg.locator("#chatOpen").is_visible())
        check("the panel says which computer it runs on", pg.locator("#chatHost").inner_text() == "on " + socket.gethostname().split(".")[0])
        check("an empty chat shows a hint", "Ask about this notebook" in pg.locator("#chatMsgs").inner_text())

        pg.locator('.cell[data-idx="2"] .chead').click()
        check("the chip shows the selected cell", "Cell 2 · Setup" in pg.locator("#chatCtx").inner_text(), pg.locator("#chatCtx").inner_text())

        send(pg, "hello there")
        pg.wait_for_selector(".cm.assistant .md", timeout=15000)
        pg.wait_for_function("() => document.querySelector('.cm.assistant .cfoot')", timeout=15000)
        check("the question shows on the right", pg.locator(".cm.user").first.inner_text() == "hello there")
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
        pg.wait_for_selector(".toast button", timeout=15000)
        check("a tool step shows as a line", "Edit nb.py" in pg.locator(".ctool").first.inner_text(), pg.locator(".ctool").first.inner_text())
        check("a notice says that Claude changed the notebook", "Claude changed the notebook" in " ".join(pg.locator(".toast").all_inner_texts()))  # the version poll can show "changed on disk" first
        check("the file changed", "# edited by fake claude" in nb.read_text())
        pg.locator(".toast button", has_text="Undo").click()
        pg.wait_for_timeout(700)
        check("Undo in the notice takes the whole turn back", nb.read_text() == ORIGINAL)

        # Ask only
        pg.locator("#chatAsk").check()
        send(pg, "please EDIT the file")
        pg.wait_for_function("() => document.querySelectorAll('.cm.assistant .cfoot').length >= 3", timeout=15000)
        check("Ask only: the file stays as it was", nb.read_text() == ORIGINAL)
        pg.locator("#chatAsk").uncheck()

        # "Ask about this cell" in the menu
        pg.locator('.cell[data-idx="4"]').hover()
        pg.locator('.cell[data-idx="4"] .chead button', has_text="⋯").click(force=True)
        pg.locator(".menu button", has_text="Ask Claude about this cell").click()
        check("the cell menu points the chat at that cell", "Cell 4" in pg.locator("#chatCtx").inner_text() and pg.locator("#chatText").evaluate("e => e === document.activeElement"))
        pg.locator("#chatCtx .chip button").click()
        check("the chip can be removed", "Cell 4" not in pg.locator("#chatCtx").inner_text())

        # the conversation is kept over a reload
        n = pg.locator(".cm").count()
        pg.reload()
        pg.wait_for_selector(".cell")
        pg.wait_for_timeout(800)
        check("after a reload the panel is open and the conversation is back", pg.locator("#chatBox").is_visible() and pg.locator(".cm").count() == n, f"{pg.locator('.cm').count()} vs {n}")
        pg.screenshot(path=str(tmp / "chat.png"))

        # New chat
        pg.once("dialog", lambda d: d.accept())
        pg.locator("#chatNew").click()
        pg.wait_for_timeout(500)
        check("New empties the conversation", pg.locator(".cm").count() == 0 and "Ask about this notebook" in pg.locator("#chatMsgs").inner_text())

        pg.locator("#chatMin").click()
        check("the minus button closes the panel", pg.locator("#chatOpen").is_visible() and not pg.locator("#chatBox").is_visible())
        # the hotkey
        pg.keyboard.press("Control+s")
        check("Ctrl+S opens the chat and focuses the text box", pg.locator("#chatBox").is_visible() and focused(pg))
        pg.keyboard.press("Control+s")
        check("Ctrl+S in the text box hides the chat", not pg.locator("#chatBox").is_visible())
        pg.keyboard.press("Control+s")
        focused(pg)
        pg.locator('.cell[data-idx="2"] .chead').click()
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

        # the key works in a cell editor too
        pg.locator("#chatMin").click()
        pg.locator('.cell[data-idx="2"] .chead').click()
        pg.keyboard.press("Enter")
        pg.wait_for_selector(".cm-editor", timeout=5000)
        pg.locator(".cm-editor .cm-content").click()
        pg.keyboard.press("Control+s")
        check("Ctrl+S in a cell editor opens the chat", pg.locator("#chatBox").is_visible() and focused(pg))
        pg.keyboard.press("Escape")
        check("no errors in the console", not errs, str(errs))
        b.close()
finally:
    subprocess.run(["python3", PYVIEW, "--stop"], env=env, capture_output=True)
    if os.environ.get("PYVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
