"""Tests of the cell parser and the edit operations (no server, no runner). Run: python3 tests/test_ops.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pyview as pv  # noqa: E402

SRC = """\
# pyview: python=/usr/bin/python3
#> general note

import os

# %% [markdown]
# # Title
# some $x^2$ text

# %% Load
#> why slow?
#> @claude: because
x = 1
#> check this line
y = 2

# %% Last
print(x + y)
"""


def cells_of(text):
    lines = pv.split_lines(text)
    return lines, pv.parse_cells(lines)


def test_parse():
    lines, cells = cells_of(SRC)
    assert [c.kind for c in cells] == ["code", "markdown", "code", "code"]
    assert cells[2].title == "Load" and cells[1].title == ""
    assert pv.python_header(lines) == "/usr/bin/python3"
    assert pv.is_visible(lines, cells[0])
    assert pv.md_text(lines, cells[1]) == "# Title\nsome $x^2$ text"
    # comments are not part of a cell's identity
    h = pv.cell_hash(lines, cells[2])
    lines2 = lines[:]
    lines2.insert(cells[2].bs, "#> another comment")
    assert pv.cell_hash(lines2, pv.parse_cells(lines2)[2]) == h
    assert pv.exec_source(lines, cells[2]).split("\n")[:3] == ["", "", "x = 1"]


def test_threads():
    lines, cells = cells_of(SRC)
    th = pv.parse_threads(lines, cells)
    assert [t["scope"] for t in th] == ["general", "cell", "line"], th
    assert th[1]["status"] == "answered" and len(th[1]["msgs"]) == 2
    assert th[2]["target"] == "y = 2"


def test_hidden_cell0():
    lines, cells = cells_of("# pyview: python=x\n#> note\n\n# %% A\nx = 1\n")
    assert not pv.is_visible(lines, cells[0])


def test_edit_cell():
    out = pv.apply_op(SRC, {"type": "edit-cell", "cell": 3, "text": "print('hi')\nprint('there')"})
    assert out.endswith("# %% Last\nprint('hi')\nprint('there')\n"), out
    out = pv.apply_op(SRC, {"type": "edit-cell", "cell": 1, "text": "# New\n\nbody"})
    assert "# %% [markdown]\n# # New\n#\n# body\n\n# %% Load" in out, out


def test_add_delete_move():
    out = pv.apply_op(SRC, {"type": "add-cell", "after": 3, "kind": "markdown", "text": "end"})
    assert out.endswith("print(x + y)\n\n# %% [markdown]\n# end\n"), out
    out = pv.apply_op(SRC, {"type": "add-cell", "after": 1, "kind": "code", "text": "z = 3"})
    assert "some $x^2$ text\n\n# %%\nz = 3\n\n# %% Load" in out, out
    out = pv.apply_op(SRC, {"type": "delete-cell", "cell": 2})
    assert "# %% Load" not in out and "# %% Last" in out
    out = pv.apply_op(SRC, {"type": "move-cell", "cell": 3, "dir": -1})
    _, cells = cells_of(out)
    assert [c.title for c in cells] == ["", "", "Last", "Load"], out
    assert out.endswith("y = 2\n"), out


def test_split_and_type():
    out = pv.apply_op(SRC, {"type": "split-cell", "cell": 2, "line": 3})
    _, cells = cells_of(out)
    assert len(cells) == 5
    out = pv.apply_op(SRC, {"type": "set-type", "cell": 3, "kind": "markdown"})
    assert "# %% [markdown] Last\n# print(x + y)" in out or "# %% [markdown] Last" in out
    back = pv.apply_op(out, {"type": "set-type", "cell": 3, "kind": "code"})
    assert back == SRC, back


def test_set_title():
    out = pv.apply_op(SRC, {"type": "set-title", "cell": 3, "title": "  New\n name "})
    _, cells = cells_of(out)
    assert cells[3].title == "New name", cells[3].title
    assert pv.apply_op(out, {"type": "set-title", "cell": 3, "title": "Last"}) == SRC
    assert "# %%\n" in pv.apply_op(SRC, {"type": "set-title", "cell": 3, "title": ""})
    try:
        pv.apply_op(SRC, {"type": "set-title", "cell": 0, "title": "x"})
        assert False
    except ValueError:
        pass


def test_comments():
    out = pv.apply_op(SRC, {"type": "comment-add", "line": 17, "text": "is this right?"})
    assert "#> is this right?\nprint(x + y)" in out, out
    out = pv.apply_op(SRC, {"type": "comment-add", "line": 14, "text": "me too"})  # a thread is there: reply
    assert "#> check this line\n#> @you: me too\ny = 2" in out, out
    out = pv.apply_op(SRC, {"type": "comment-add", "scope": "cell", "cell": 3, "line": 0, "text": "cell note"})
    assert "# %% Last\n#> cell note\nprint" in out, out
    out = pv.apply_op(SRC, {"type": "comment-add", "scope": "general", "line": 0, "text": "top"})
    assert out.startswith("#> top\n\n# pyview"), out
    lines, cells = cells_of(SRC)
    th = pv.parse_threads(lines, cells)
    out = pv.apply_op(SRC, {"type": "comment-reply", "thread": th[2]["id"], "text": "ok", "author": "claude"})
    assert "#> check this line\n#> @claude: ok\ny = 2" in out, out
    out = pv.apply_op(SRC, {"type": "comment-resolve", "thread": th[1]["id"]})
    assert "why slow" not in out and "x = 1" in out
    out = pv.apply_op(SRC, {"type": "comment-edit", "line": th[1]["msgs"][1]["line"], "text": "changed"})
    assert "#> @claude: changed" in out
    out = pv.apply_op(SRC, {"type": "comment-delete", "line": th[2]["msgs"][0]["line"]})
    assert "check this line" not in out


def test_resolve_all():
    out = pv.apply_op(SRC, {"type": "comment-resolve-all"})
    assert "#>" not in out
    assert "x = 1\ny = 2" in out and "# %% Load" in out and out.startswith("# pyview: python=")
    assert pv.apply_op(out, {"type": "comment-resolve-all"}) == out  # nothing left: no change


def test_align():
    old = [{"hash": "a"}, {"hash": "b"}, {"hash": "c"}]
    got = pv.align(old, ["a", "b2", "c"])
    assert [(e and e["hash"], s) for e, s in got] == [("a", "same"), ("b", "edited"), ("c", "same")]
    got = pv.align(old, ["new", "a", "b", "c"])
    assert [(e and e["hash"], s) for e, s in got] == [(None, None), ("a", "same"), ("b", "same"), ("c", "same")]
    got = pv.align(old, ["a", "c"])
    assert [(e and e["hash"]) for e, s in got] == ["a", "c"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
