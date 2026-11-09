"""Make web/icons/APP-app.png (256 px) and web/icons/APP.png (64 px): the icon of each viewer app as macOS shows it in
Finder. macOS 26 cuts an app icon to its own rounded shape and adds a glass effect, so the icon of the app is not the
flat drawing of kit.js. The browser tab and the folder pages use these files, so the tab icon is the same as the app icon.
The apps in FLAT keep the flat drawing (APP-1024.png of make_icons.py) as the tab icon, with no gray plate.
Run it on a Mac after app/build.sh of each viewer: python3 tests/app_icons.py
The apps must be in ~/Applications (mdview.app, pyview.app, pdfview.app, pageview.app, plateview.app, texview.app). A missing app is skipped."""
import subprocess
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
APPS = {"md": "mdview", "py": "pyview", "pdf": "pdfview", "page": "pageview", "plate": "plateview", "tex": "texview"}
FLAT = {"pdf"}  # the tab icon is the flat drawing: the red page, not the red page on the gray plate of macOS
JXA = """ObjC.import('AppKit');
function run(argv) {
  const img = $.NSWorkspace.sharedWorkspace.iconForFile(argv[0]);
  img.size = $.NSMakeSize(1024, 1024);
  const rep = $.NSBitmapImageRep.imageRepWithData(img.TIFFRepresentation);
  rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $()).writeToFileAtomically(argv[1], true);
}"""

if sys.platform != "darwin":
    sys.exit("app_icons.py: this needs macOS (it reads the icons that Finder shows).")
with tempfile.TemporaryDirectory() as tmp:
    for key, name in APPS.items():
        if key in FLAT:
            big = OUT / f"{key}-1024.png"
            for size, out in ((256, OUT / f"{key}-app.png"), (64, OUT / f"{key}.png")):
                subprocess.run(["sips", "-z", str(size), str(size), str(big), "--out", str(out)], check=True, capture_output=True)
                print(out)
            continue
        app = Path.home() / "Applications" / f"{name}.app"
        if not app.exists():
            print(f"skip {name}: {app} is missing (run app/build.sh of {name} first)")
            continue
        big = Path(tmp) / f"{key}.png"
        subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA, str(app), str(big)], check=True, capture_output=True)
        for size, out in ((256, OUT / f"{key}-app.png"), (64, OUT / f"{key}.png")):
            subprocess.run(["sips", "-z", str(size), str(size), str(big), "--out", str(out)], check=True, capture_output=True)
            print(out)
