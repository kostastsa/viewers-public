"""Make web/icons/APP-1024.png: the flat drawing of the icon of each viewer, from kit.iconURL in web/kit.js. The app
builder (app/build.sh) makes the Finder icon of the app from it. The tab icons (APP.png, APP-app.png) are the app icons
as macOS shows them: tests/app_icons.py makes them after the apps are built.
Needs Playwright and its Chromium. Run: uv run --with playwright python tests/make_icons.py"""
import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parent.parent / "web"
OUT = WEB / "icons"
OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_content("<!doctype html><title>icons</title>")
    pg.add_script_tag(content=(WEB / "kit.js").read_text())
    for app in ("md", "py", "pdf", "page", "plate"):
        for size, name in ((1024, f"{app}-1024.png"),):  # the source of the Finder icon
            url = pg.evaluate("([app, size]) => kit.iconURL(app, size)", [app, size])
            (OUT / name).write_bytes(base64.b64decode(url.split(",", 1)[1]))
            print(OUT / name)
    b.close()
