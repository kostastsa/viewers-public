"""Tests of the comments file and the subcommands, without a server. Run: python3 tests/test_ops.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pdfview as pv  # noqa: E402

PDFVIEW = str(HERE.parent / "pdfview.py")
TMP_ROOT = HERE / "tmp"  # git ignores it
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="ops-", dir=TMP_ROOT))
pdf = tmp / "paper.pdf"
shutil.copy(HERE / "fixture.pdf", pdf)
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


def cli(*args):
    return subprocess.run([sys.executable, PDFVIEW, *args], capture_output=True, text=True)


def raises(fn):
    try:
        fn()
    except pv.OpError as e:
        return str(e)
    return None


try:
    data, v = pv.read_comments(pdf)
    check("no file: no threads and an empty version", data == {"threads": []} and v == "")

    an = {"page": 1, "quote": "the bound is tight", "prefix": "We show that ", "suffix": " for all n."}
    tid, before, v1 = pv.change(pdf, {"op": "add", "anchor": an, "text": "Why is it tight?"})
    check("add makes the file in .pdfview/", pv.sidecar(pdf).is_file() and pv.sidecar(pdf).parent.name == ".pdfview")
    check("the first thread has id 1, and nothing was there before", tid == 1 and before is None and v1)
    t = pv.read_comments(pdf)[0]["threads"][0]
    check("the anchor and the message are saved", t["anchor"]["quote"] == "the bound is tight" and t["msgs"][0]["author"] == "you")

    tid2 = pv.change(pdf, {"op": "add", "text": "A general note"})[0]
    t2 = pv.read_comments(pdf)[0]["threads"][1]
    check("a general comment has no anchor", tid2 == 2 and "anchor" not in t2)

    check("a stale version is refused", "changed" in (raises(lambda: pv.change(pdf, {"op": "reply", "thread": 1, "text": "x"},
                                                                                 version=v1)) or ""))
    check("an empty comment is refused", raises(lambda: pv.change(pdf, {"op": "add", "text": "  "})) is not None)
    check("an anchor without words is refused",
          raises(lambda: pv.change(pdf, {"op": "add", "anchor": {"page": 1, "quote": ""}, "text": "x"})) is not None)
    check("page 0 is refused",
          raises(lambda: pv.change(pdf, {"op": "add", "anchor": {"page": 0, "quote": "a"}, "text": "x"})) is not None)
    long_prefix = pv.clean_anchor({"page": 2, "quote": "q", "prefix": "x" * 100, "suffix": "y" * 100})
    check("prefix and suffix are cut to 40 characters", len(long_prefix["prefix"]) == 40 and len(long_prefix["suffix"]) == 40)
    check("an unknown thread is refused", "no comment #9" in (raises(lambda: pv.change(pdf, {"op": "reply", "thread": 9,
                                                                                               "text": "x"})) or ""))

    # the subcommands, as Claude uses them
    r = cli("reply", str(pdf), "1", "Because the example in section 3 reaches it.")
    check("pdfview reply adds a message as claude", r.returncode == 0 and "replied to #1" in r.stdout, r.stderr)
    r = cli("list", str(pdf))
    check("pdfview list shows the page, the quote and the status",
          '#1  page 1  "the bound is tight"  [answered]' in r.stdout and "#2  general  [open]" in r.stdout, r.stdout)
    j = json.loads(cli("list", str(pdf), "--json").stdout)
    check("pdfview list --json gives the threads", [t["status"] for t in j] == ["answered", "open"]
          and j[0]["msgs"][1]["author"] == "claude")
    r = cli("add", str(pdf), "Note this", "--page", "2", "--quote", "main theorem")
    check("pdfview add with a page and a quote", r.returncode == 0 and "added comment #3" in r.stdout, r.stderr)
    r = cli("add", str(pdf), "x", "--page", "2")
    check("pdfview add with a page and no quote is refused", r.returncode != 0 and "together" in r.stderr)
    r = cli("reply", str(pdf), "7", "x")
    check("pdfview reply to a missing thread fails", r.returncode != 0 and "no comment #7" in r.stderr)
    r = subprocess.run([sys.executable, PDFVIEW, "reply", str(pdf), "3", "-"], input="Maths: $x^2$ and 'quotes'.\nLine 2.",
                       capture_output=True, text=True)
    last = pv.read_comments(pdf)[0]["threads"][-1]["msgs"][-1]["text"]
    check("pdfview reply - reads the text from stdin, with $ and quotes", r.returncode == 0
          and last == "Maths: $x^2$ and 'quotes'.\nLine 2.", (r.stderr, last))
    r = cli("text", str(pdf), "--page", "1")
    check("pdfview text prints one page", r.returncode == 0 and "=== page 1 ===" in r.stdout
          and "the bound is tight" in r.stdout and "page 2" not in r.stdout, r.stderr)
    r = cli("text", str(pdf))
    check("pdfview text prints all pages", "=== page 2 ===" in r.stdout and "odd order" in r.stdout)
    r = cli("list", str(tmp / "missing.pdf"))
    check("a missing PDF gives an error", r.returncode != 0 and "not a PDF file" in r.stderr)

    # edit, delete, resolve
    pv.change(pdf, {"op": "edit", "thread": 1, "msg": 0, "text": "Why is it tight? (edited)"})
    m = pv.read_comments(pdf)[0]["threads"][0]["msgs"][0]
    check("edit changes the text and marks it", m["text"].endswith("(edited)") and m.get("edited"))
    pv.change(pdf, {"op": "delete", "thread": 2, "msg": 0})
    check("deleting the only message removes the thread", [t["id"] for t in pv.read_comments(pdf)[0]["threads"]] == [1, 3])
    tid4 = pv.change(pdf, {"op": "add", "text": "new"})[0]
    check("ids are not used again while higher ones exist", tid4 == 4)
    pv.change(pdf, {"op": "resolve", "thread": 1})
    check("resolve removes the thread", [t["id"] for t in pv.read_comments(pdf)[0]["threads"]] == [3, 4])
    pv.change(pdf, {"op": "resolve-all"})
    check("with no threads the file and the folder are removed", not pv.sidecar(pdf).exists()
          and not (tmp / ".pdfview").exists())

    # ---- box anchors: an area of a page
    a = pv.clean_anchor({"page": 1, "box": "272, 332, 72, 182", "quote": " the text "})
    check("a box anchor: the corners in order, rounded, with the text in it", a == {"page": 1, "box": [72.0, 182.0, 272.0, 332.0], "quote": "the text"}, a)
    check("a box anchor needs no text", pv.clean_anchor({"page": 2, "box": [1, 2, 30, 40]}) == {"page": 2, "box": [1.0, 2.0, 30.0, 40.0]})
    check("a box needs four numbers", "four numbers" in (raises(lambda: pv.clean_anchor({"page": 1, "box": [1, 2, 3]})) or ""))
    check("a box cannot be empty", "too small" in (raises(lambda: pv.clean_anchor({"page": 1, "box": [5, 5, 5.5, 40]})) or ""))
    r = cli("add", str(pdf), "x", "--box", "1,2,30,40")
    check("add --box needs --page", r.returncode != 0 and "--box needs --page" in r.stderr, r.stderr)

    # ---- highlights: passages with no comment
    d = {"threads": []}
    h1 = pv.apply_op(d, {"op": "highlight", "anchor": {"page": 1, "quote": "the bound"}})
    h2 = pv.apply_op(d, {"op": "highlight", "anchor": {"page": 2, "box": [10, 10, 50, 50]}})
    check("a highlight has an id and an anchor (text or box)", [h["id"] for h in d["highlights"]] == [1, 2] and (h1, h2) == (1, 2))
    check("a highlight needs an anchor", "Select some text" in (raises(lambda: pv.apply_op(d, {"op": "highlight"})) or ""))
    t = pv.apply_op(d, {"op": "add", "anchor": {"page": 1, "quote": "the bound"}, "text": "Why?", "from_highlight": 1})
    check("a comment from a highlight takes its place", [h["id"] for h in d["highlights"]] == [2] and d["threads"][0]["id"] == t)
    pv.apply_op(d, {"op": "resolve-all"})
    check("Resolve all keeps the highlights", d["threads"] == [] and len(d["highlights"]) == 1)
    pv.apply_op(d, {"op": "unhighlight", "highlight": 2})
    check("unhighlight removes it", d["highlights"] == [])
    check("an unknown highlight gives an error", "no such highlight" in (raises(lambda: pv.apply_op(d, {"op": "unhighlight", "highlight": 9})) or ""))
    pv.change(pdf, {"op": "highlight", "anchor": {"page": 1, "quote": "the bound"}})
    check("a file with only highlights stays", pv.sidecar(pdf).exists() and pv.read_comments(pdf)[0]["highlights"][0]["id"] == 1)
    r = cli("list", str(pdf))
    check("pdfview list shows the highlights", "Highlights (no comment): 1" in r.stdout and 'h1  page 1  "the bound"' in r.stdout, r.stdout)
    pv.change(pdf, {"op": "unhighlight", "highlight": 1})
    check("with no threads and no highlights the file goes", not pv.sidecar(pdf).exists())

    pv.sidecar(pdf).parent.mkdir()
    pv.sidecar(pdf).write_text("not json")
    check("a broken comments file gives a clear error", "not a valid comments file" in (raises(lambda: pv.read_comments(pdf)) or ""))
finally:
    if os.environ.get("PDFVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
