"""Tests of pageview without a server: the comments file, the subcommands, the front matter, the data files, the pandoc
render and the access rules. Needs pandoc. Run: python3 tests/test_ops.py
It makes its temporary folder in tests/tmp and deletes it at the end. Set PAGEVIEW_KEEP_TMP=1 to keep it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pageview as pv  # noqa: E402

PAGEVIEW = str(HERE.parent / "pageview.py")
TMP_ROOT = HERE / "tmp"  # git ignores it
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="ops-", dir=TMP_ROOT))
shutil.copytree(HERE.parent / "examples", tmp / "examples")
page = tmp / "examples" / "optimizers" / "optimizers.md"
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


def cli(*args, stdin=None):
    return subprocess.run([sys.executable, PAGEVIEW, *args], capture_output=True, text=True, input=stdin)


def raises(fn):
    try:
        fn()
    except pv.OpError as e:
        return str(e)
    return None


try:
    # ---- the comments file
    data = {"threads": []}
    t1 = pv.apply_op(data, {"op": "add", "anchor": {"quote": " the best test accuracy ", "prefix": "x" * 60, "line": 14},
                            "text": "Why?"})
    t2 = pv.apply_op(data, {"op": "add", "anchor": {"table": "runs.csv", "row": "AdamW", "col": "acc"}, "text": "Seeds?"})
    t3 = pv.apply_op(data, {"op": "add", "text": "General."})
    a1, a2 = data["threads"][0]["anchor"], data["threads"][1]["anchor"]
    check("a text anchor keeps the words, a short prefix and the line", a1 == {"quote": "the best test accuracy",
          "prefix": "x" * 40, "line": 14}, a1)
    check("a cell anchor keeps the table, the row and the column", a2 == {"table": "runs.csv", "row": "AdamW", "col": "acc"}, a2)
    check("a general comment has no anchor", "anchor" not in data["threads"][2] and (t1, t2, t3) == (1, 2, 3))
    check("a cell anchor needs a column", "column" in (raises(lambda: pv.apply_op(data, {"op": "add", "anchor": {"table": "x"}, "text": "a"})) or ""))
    check("an empty comment is refused", raises(lambda: pv.apply_op(data, {"op": "add", "text": "  "})) == "Write a comment first.")
    pv.apply_op(data, {"op": "reply", "thread": 2, "text": "Five."}, author="claude")
    check("a reply from claude makes the thread answered", pv.status_of(data["threads"][1]) == "answered")
    pv.apply_op(data, {"op": "resolve", "thread": 1})
    check("resolve removes the thread", [t["id"] for t in data["threads"]] == [2, 3])

    # ---- the subcommands
    r = cli("add", str(page), "-", "--quote", "AdamW gives the best test accuracy", stdin="Is it $p < 0.05$?")
    check("pageview add with --quote and the text on stdin", r.returncode == 0 and "added comment #1" in r.stdout, r.stderr)
    r = cli("add", str(page), "Check this cell", "--cell", "runs.csv", "Lion", "acc", "--author", "you")
    check("pageview add with --cell", r.returncode == 0 and "#2" in r.stdout, r.stderr)
    r = cli("reply", str(page), "1", "-", stdin="Yes: $p = 0.01$.")
    check("pageview reply", r.returncode == 0, r.stderr)
    r = cli("list", str(page))
    check("pageview list shows the quote, the cell and the status", '"AdamW gives the best test accuracy"  [answered]' in r.stdout
          and "cell runs.csv [Lion, acc]  [open]" in r.stdout, r.stdout)
    side = page.parent / ".pageview" / "optimizers.md.json"
    ts = json.loads(side.read_text())["threads"]
    check("the comments are in .pageview/NAME.json, and the maths is kept", ts[0]["msgs"][0]["text"] == "Is it $p < 0.05$?"
          and ts[0]["msgs"][1]["author"] == "claude", ts)
    r = cli("list", str(page), "--json")
    check("pageview list --json", r.returncode == 0 and len(json.loads(r.stdout)) == 2)
    r = cli("add", str(tmp / "examples" / "vision_grid.csv"), "On the grid", "--cell", "vision_grid.csv", "Swin-T", "Flowers-102")
    check("a data file opened alone takes comments too", r.returncode == 0, r.stderr)
    r = cli("list", str(tmp / "nothing.txt"))
    check("pageview refuses a file that is not a page or a data file", r.returncode != 0 and "not a page" in r.stderr)

    # ---- pageview deps: the files that the preview of a note copies (improve.json, viewkit/improvekit.py)
    r = cli("deps", str(page))
    check("pageview deps lists the data files and figures of the page, not the links to other files",
          r.stdout.split() == ["curves.csv", "fig/confusion_adamw.png", "fig/gradnorm_e10.pdf", "fig/lr_scatter.png",
                               "runs.csv"], r.stdout + r.stderr)
    r = cli("deps", str(tmp / "examples" / "vision_grid.csv"))
    check("pageview deps of a data file is its NAME.view.json", r.stdout.split() == ["vision_grid.view.json"], r.stdout)
    sys.path.insert(0, str(HERE.parent.parent / "viewkit"))
    import improvekit  # noqa: E402
    keep = [c.relative_to(page.parent).as_posix()
            for c in improvekit.companions_of(improvekit.manifest(HERE.parent), page, HERE.parent)]
    check("the preview copies the comments and the deps of the page", keep == [".pageview/optimizers.md.json",
          "curves.csv", "fig/confusion_adamw.png", "fig/gradnorm_e10.pdf", "fig/lr_scatter.png", "runs.csv"], keep)

    # ---- the front matter
    fm = pv.front_matter("---\ntitle: 'A: B'\ntags: [a, b]\nauthors:\n  - X\n  - Y\nview: page\n---\n\n# H\n")
    check("front matter: a quoted value, a list in brackets, and a list of lines",
          fm == {"title": "A: B", "tags": ["a", "b"], "authors": ["X", "Y"], "view": "page"}, fm)
    check("no front matter gives nothing", pv.front_matter("# Title\n---\n") == {})

    # ---- data files
    d = pv.read_table(page.parent / "runs.csv")
    check("CSV: the columns, and numbers as numbers", d["columns"][:3] == ["optimizer", "lr", "acc"] and d["rows"][0][2] == 91.2
          and d["rows"][0][1] == 0.1 and d["rows"][0][7] == "✅", d["rows"][0])
    (tmp / "t.tsv").write_text("a\tb\n1\tx\n2\t\n")
    check("TSV", pv.read_table(tmp / "t.tsv") == {"columns": ["a", "b"], "rows": [[1, "x"], [2, ""]], "truncated": False})
    (tmp / "r.json").write_text('[{"a": 1, "b": NaN}, {"a": 2, "c": [1, 2]}]')
    d = pv.read_table(tmp / "r.json")
    check("JSON records: all the keys, NaN as null, a list as text", d["columns"] == ["a", "b", "c"] and d["rows"][0] == [1, None, None]
          and d["rows"][1] == [2, None, "[1, 2]"], d)
    (tmp / "c.json").write_text('{"x": [1, 2], "y": [3]}')
    check("JSON columns of lists", pv.read_table(tmp / "c.json")["rows"] == [[1, 3], [2, None]])
    (tmp / "l.jsonl").write_text('{"a": 1}\n\n{"a": 2}\n')
    check("JSON Lines", pv.read_table(tmp / "l.jsonl")["rows"] == [[1], [2]])
    (tmp / "bad.json").write_text('{"a": 1}')
    check("JSON that is not a table gives a clear error", "not a table" in (raises(lambda: pv.read_table(tmp / "bad.json")) or ""))

    # ---- the render
    src = page.read_text()
    html, widgets, deps = pv.render_page(src, "x/optimizers.md")
    check("the render keeps the block classes and options", '<div class="table"' in html and 'data-best="acc:max loss:min time:min"' in html
          and '<div class="chart"' in html and '<div class="tabs"' in html)
    check("the render finds the widget and the files that the page uses", len(widgets) == 1 and "<script>" in widgets[0]
          and deps == ["curves.csv", "fig/confusion_adamw.png", "fig/gradnorm_e10.pdf", "fig/lr_scatter.png", "runs.csv"], deps)
    check("images go through /file, a PDF figure as PNG", '/file?f=x/optimizers.md&amp;src=fig/gradnorm_e10.pdf&amp;as=png' in html)
    check("links to files go through /go", '/go?f=x/optimizers.md&amp;src=../vision_grid.csv' in html)
    check("blocks keep their source lines", 'data-pos="' in html and "data-wrapper" not in html)
    evil = ('<script>alert(1)</script>\n\nText <img src=x onerror=alert(2)> and <b>bold</b>.\n\n'
            '[x](javascript:alert(3)) ![y](//evil.example/y.png) ::: {.callout onclick="alert(4)" style="color:red"}\nA\n:::\n\n'
            '::: {.callout onclick="alert(5)"}\nB\n:::\n')
    out = pv.render_page(evil, "e.md")[0]
    check("raw HTML shows as text, not as tags", "<script>" not in out and "&lt;script&gt;" in out and "<img src=x" not in out
          and "<b>" not in out, out)
    check("a javascript: link and an image of another host are dropped", "javascript:" not in out and "evil.example" not in out, out)
    check("event handlers and style attributes are dropped", "onclick" not in out and 'style="color' not in out, out)

    # ---- the access rules
    app = pv.App(Path.home())
    app.grant(page)
    check("a granted page is allowed, its folder is not", app.allowed(page) and not app.allowed(page.parent))
    check("the page may use a file in its folder", app.dep(page, "runs.csv") == (page.parent / "runs.csv").resolve())
    check("the page may use a file of its Git project", app.dep(page, "../vision_grid.csv") is not None)
    (page.parent / ".secret").write_text("x")
    check("the page may not use a hidden file", app.dep(page, ".secret") is None)
    check("the page may not use a file outside the root, or an absolute path",
          app.dep(page, "../" * 30 + "etc/hosts") is None and app.dep(page, "/etc/hosts") is None)
    outside = Path(tempfile.mkdtemp(prefix="pv-out-", dir=Path.home()))
    try:
        (outside / "x.csv").write_text("a\n1\n")
        rel = os.path.relpath(outside / "x.csv", page.parent)
        check("the page may not use a file of another project", app.dep(page, rel) is None)
    finally:
        shutil.rmtree(outside, ignore_errors=True)

    # ---- the watermark art (viewkit/artkit.py)
    import artkit  # noqa: E402
    old = os.environ.get("VIEWKIT_ART")
    os.environ["VIEWKIT_ART"] = str(tmp / "art")
    try:
        check("without the art folder, the manifest is empty and no file is served",
              artkit.file("figures/manifest.json") == (b"[]", "application/json") and artkit.file("x.png") is None)
        (tmp / "art" / "figures").mkdir(parents=True)
        (tmp / "art" / "figures" / "o001-01-mask.png").write_bytes(b"\x89PNG")
        (tmp / "secret.png").write_bytes(b"\x89PNG")
        check("the art route serves an image of the folder, and nothing outside it",
              artkit.file("figures/o001-01-mask.png") == (b"\x89PNG", "image/png")
              and artkit.file("../secret.png") is None and artkit.file("figures/../../secret.png") is None)
        check("a vote changes the score of a figure, and a bad id is refused",
              artkit.vote_api({"id": "o001-01", "vote": 1}) == (200, {"score": 1})
              and artkit.vote_api({"id": "o001-01", "vote": -1}) == (200, {"score": 0})
              and artkit.vote_api({"id": "../x", "vote": 1})[0] == 404 and artkit.scores() == {"o001-01": 0})
    finally:
        if old is None:
            os.environ.pop("VIEWKIT_ART", None)
        else:
            os.environ["VIEWKIT_ART"] = old

    # ---- PDF figures
    png = pv.pdf_figure(page.parent / "fig" / "gradnorm_e10.pdf")
    check("a PDF figure becomes a PNG (pdftoppm or sips)", png is not None and png.read_bytes()[:4] == b"\x89PNG")
finally:
    if os.environ.get("PAGEVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
