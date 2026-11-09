"""Tests of texview without a server: the source paths, the anchors, the comments, the suggested changes, Accept, the
log of a compile, and the subcommands. The compile test needs latexmk and synctex. Run: python3 tests/test_ops.py
It deletes its temporary folder in tests/tmp at the end. Set TEXVIEW_KEEP_TMP=1 to keep it for debugging."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP_ROOT = HERE / "tmp"  # git ignores it
TMP_ROOT.mkdir(exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="ops-", dir=TMP_ROOT))
os.environ["TEXVIEW_BUILD"] = str(tmp / "build")
sys.path.insert(0, str(HERE.parent))
import texview as tv  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def raises(fn):
    try:
        fn()
    except tv.OpError as e:
        return str(e)
    return None


def cli(*args, stdin=None):
    return subprocess.run([sys.executable, str(HERE.parent / "texview.py"), *args], input=stdin, capture_output=True,
                          text=True, env=dict(os.environ))


try:
    proj = tmp / "proj"
    shutil.copytree(HERE.parent / "samples", proj)
    (proj / "sections").mkdir()
    (proj / "sections" / "extra.tex").write_text("Extra text.\nThe bound is tight.\n")
    (proj / ".hidden.tex").write_text("secret")
    main = proj / "paper.tex"

    # ----- source paths: only text sources in the folder of the main file
    check("a source in a subfolder is allowed", tv.source_path(main, "sections/extra.tex").name == "extra.tex")
    check("a hidden file is refused", raises(lambda: tv.source_path(main, ".hidden.tex")) is not None)
    check("a path outside the folder is refused", raises(lambda: tv.source_path(main, "../x.tex")) is not None)
    check("an absolute path is refused", raises(lambda: tv.source_path(main, str(main))) is not None)
    check("a PDF is not a source", raises(lambda: tv.source_path(main, "paper.pdf")) is not None)
    srcs = tv.list_sources(main)
    check("list_sources: the main file first, hidden files left out",
          srcs[0]["path"] == "paper.tex" and not any(".hidden" in s["path"] for s in srcs)
          and any(s["path"] == "sections/extra.tex" for s in srcs), srcs)
    (proj / "notes.tex").write_text("% notes\n\\documentclass{article}\n\\begin{document}x\\end{document}\n")
    (proj / "old.tex").write_text("% \\documentclass{article}\nOnly a comment.\n")
    docs = {s["path"] for s in tv.list_sources(main) if s.get("doc")}
    check("list_sources marks the main file of another document, not inputs or commented lines",
          docs == {"notes.tex"}, docs)

    # ----- locate: the best fit of the words before and after, then the nearest line
    text = "a tight b\nc tight d\ne tight f\n"
    check("locate picks the place with the same words around it",
          tv.locate(text, {"quote": "tight", "prefix": "c ", "suffix": " d"}) == (12, 17))
    check("locate picks the nearest line when the words around do not help",
          tv.locate(text, {"quote": "tight", "line": 3}) == (22, 27))
    check("locate gives None for missing words", tv.locate(text, {"quote": "loose"}) is None)

    # ----- parse_changes: the blocks of `texview suggest`
    ch = tv.parse_changes("<<<<<<< old\nA \\alpha $x$\n=======\nB\n>>>>>>> new\nWhy: shorter.\n"
                          "<<<<<<< old\nC\n=======\n>>>>>>> new\n")
    check("parse_changes reads two blocks, backslashes and an empty new text",
          ch == [("A \\alpha $x$", "B", "Why: shorter."), ("C", "", "")], ch)

    # ----- comments and suggested changes
    tid = tv.change(main, {"op": "add", "anchor": {"file": "paper.tex", "line": 23}, "text": "Why this citation?"})[0]
    t = tv.read_comments(main)[0]["threads"][0]
    check("a comment on a line takes the text of the line as its quote",
          t["anchor"]["quote"].startswith("densities \\cite{alspach1972}") and t["anchor"]["line"] == 23, t)
    check("a comment needs words that are in the file",
          raises(lambda: tv.change(main, {"op": "add", "anchor": {"file": "paper.tex", "quote": "no such words"},
                                          "text": "x"})) is not None)
    err = raises(lambda: tv.change(main, {"op": "suggest", "anchor": {"file": "paper.tex"}, "old": "not there", "new": "y"}))
    check("a suggestion with old text that is not in the file is refused", err and "not in paper.tex" in err, err)
    (proj / "twice.tex").write_text("one tight\ntwo tight\n")
    err = raises(lambda: tv.change(main, {"op": "suggest", "anchor": {"file": "twice.tex"}, "old": "tight", "new": "x"}))
    check("old text that occurs twice needs --line", err and "2 times" in err, err)
    sid = tv.change(main, {"op": "suggest", "anchor": {"file": "twice.tex", "line": 2}, "old": "tight",
                           "new": "sharp", "text": "Better."}, "claude")[0]
    done, skipped, before, files, _ = tv.accept(main, [sid])
    check("Accept changes the place of --line only", (proj / "twice.tex").read_text() == "one tight\ntwo sharp\n"
          and done == [sid] and str(proj / "twice.tex") in files, (proj / "twice.tex").read_text())
    check("Accept keeps the text before, for Undo", files[str(proj / "twice.tex")] == "one tight\ntwo tight\n")
    check("Accept removes the thread", all(t["id"] != sid for t in tv.read_comments(main)[0]["threads"]))
    sid2 = tv.change(main, {"op": "suggest", "anchor": {"file": "sections/extra.tex"}, "old": "Extra text.",
                            "new": "More text."}, "claude")[0]
    (proj / "sections" / "extra.tex").write_text("Changed by hand.\nThe bound is tight.\n")
    done, skipped, *_ = tv.accept(main, [sid2])
    check("a change whose old text is gone is skipped, and stays", done == [] and skipped == [sid2]
          and any(t["id"] == sid2 for t in tv.read_comments(main)[0]["threads"]))
    th = tv.threads_json(main, tv.read_comments(main)[0])
    check("threads_json says when the text of a change is not found",
          next(t for t in th if t["id"] == sid2)["anchor"]["found"] is False)
    tv.change(main, {"op": "reject", "thread": sid2})
    check("Reject removes the change", all(t["id"] != sid2 for t in tv.read_comments(main)[0]["threads"]))

    # line ends: a CRLF file stays CRLF after Accept
    (proj / "crlf.tex").write_bytes(b"first line\r\nsecond line\r\n")
    cid = tv.change(main, {"op": "suggest", "anchor": {"file": "crlf.tex"}, "old": "second", "new": "2nd"})[0]
    tv.accept(main, [cid])
    check("a CRLF file keeps its line ends", (proj / "crlf.tex").read_bytes() == b"first line\r\n2nd line\r\n",
          (proj / "crlf.tex").read_bytes())

    # ----- the subcommands
    r = cli("suggest", str(main), "--file", "paper.tex", stdin="<<<<<<< old\nThe bound is tight for all $n$\n=======\n"
            "The bound is sharp for every $n$\n>>>>>>> new\nThe usual word.\n")
    check("texview suggest adds a change", r.returncode == 0 and "suggested change #" in r.stdout, r.stderr)
    r = cli("list", str(main))
    check("texview list shows the change with old and new", "old: The bound is tight for all $n$" in r.stdout
          and "new: The bound is sharp for every $n$" in r.stdout and "[change]" in r.stdout, r.stdout)
    r = cli("reply", str(main), str(tid), "-", stdin="Because of $\\alpha$.")
    check("texview reply with text from stdin", r.returncode == 0 and
          tv.read_comments(main)[0]["threads"][0]["msgs"][-1]["text"] == "Because of $\\alpha$.", r.stderr)
    r = cli("add", str(main), "On the theorem.", "--file", "paper.tex", "--quote", "goes to zero")
    check("texview add with --quote", r.returncode == 0 and "added comment" in r.stdout, r.stderr)
    r = cli("suggest", str(main), "--file", "paper.tex", stdin="nothing here")
    check("texview suggest without blocks explains the format", r.returncode != 0 and "<<<<<<< old" in r.stderr, r.stderr)

    # ----- the data of the suggestions in the source editor
    aux = ("\\relax\n\\newlabel{sec 3.D}{{3.4}{7}{Title}{section.3}{}}\n\\newlabel{sec 3.D@cref}{{[section][4][3]3.4}{[1][7][]7}}\n"
           "\\newlabel{eq:a-b}{{\\relax 2{a}}{3}}\n\\newlabel{tocindent0}{0pt}\n")
    check("aux_labels: a space and the number, no @cref entry, no entry of another form",
          tv.aux_labels(aux) == {"sec 3.D": "3.4", "eq:a-b": "2a"}, tv.aux_labels(aux))
    bib = "@article{a:1,\n x={@book{no,}}\n}\n% @book{commented,\n@String{s = 1}\n  @BOOK( b-2 ,\n}\n@comment{c,\n}\n"
    check("bib_keys: the entries only", tv.bib_keys(bib) == ["a:1", "b-2"], tv.bib_keys(bib))
    tex = ("\\newcommand{\\aa}{x}\n\\renewcommand*\\bb{x}\n\\providecommand{\\cc}{x}\n\\def\\dd#1{x}\n% \\newcommand{\\no}{x}\n"
           "100\\% \\def\\ee{x}\n\\def\\in@ternal{x}\n\\newenvironment{myenv}{a}{b}\n")
    check("tex_defs: the commands and the environments, not in a comment, not internal",
          tv.tex_defs(tex) == {"commands": ["aa", "bb", "cc", "dd", "ee"], "envs": ["myenv"]}, tv.tex_defs(tex))
    (proj / "sections" / "defs.tex").write_text("\\newcommand{\\fromdefs}{x}\n")
    (proj / ".hidden.bib").write_text("@book{hiddenkey,\n}\n")
    (tmp / "outside.bib").write_text("@book{outsidekey,\n}\n")
    (proj / "link.bib").symlink_to(tmp / "outside.bib")
    pr = tv.Project(main)
    pr.build.mkdir(parents=True, exist_ok=True)
    (pr.build / "paper.aux").write_text(aux)
    (pr.build / "sub").mkdir(exist_ok=True)
    (pr.build / "sub" / "c.aux").write_text("\\newlabel{inc}{{9}{1}}\n")
    pr.inputs = lambda: [main, proj / "sections" / "defs.tex"]
    c = pr.completions()
    check("completions: the labels of all the .aux files of the build folder",
          c["labels"] == {"sec 3.D": "3.4", "eq:a-b": "2a", "inc": "9"}, c["labels"])
    check("completions: the keys of the .bib files, no hidden file, no link to a file outside",
          c["cites"] == ["alspach1972"], c["cites"])
    check("completions: the definitions of the files that the compile read",
          c["sources"].get("sections/defs.tex") == {"commands": ["fromdefs"], "envs": []} and "twice.tex" not in c["sources"], c["sources"])
    real_read, reads = tv.read_source, []
    tv.read_source = lambda p: (reads.append(p), real_read(p))[1]
    pr.completions()
    check("completions: a file that did not change is not read again", reads == [], reads)
    (pr.build / "paper.aux").write_text("\\newlabel{new}{{1}{1}}\n")
    os.utime(pr.build / "paper.aux", ns=(1, 1))
    reads.clear()
    c = pr.completions()
    tv.read_source = real_read
    check("completions: a file with a new modification time is read again",
          reads == [pr.build / "paper.aux"] and "new" in c["labels"], reads)
    shutil.rmtree(pr.build)  # the real compile below starts clean

    # ----- the log of a compile
    log = ("(./paper.tex (./sections/a.tex\n"
           "./sections/a.tex:7: Undefined control sequence.\nl.7 \\foo\n         bar\n"
           "LaTeX Warning: Reference `x' on page 1 undefined on input line 9.\n)\n"
           "! Emergency stop.\n"
           "Package hyperref Warning: Token not allowed in a PDF string on input line 3.\n)\n")
    (proj / "sections" / "a.tex").write_text("x\n")
    errs, warns = tv.parse_log(main, log)
    check("parse_log: an error with its file, line and context",
          errs[0] == {"file": "sections/a.tex", "line": 7, "msg": "Undefined control sequence.  (l.7 \\foo)"}, errs)
    check("parse_log: an error without a place", any(e["msg"] == "Emergency stop." and e["file"] is None for e in errs), errs)
    check("parse_log: a warning in an \\input file gets that file",
          warns[0]["file"] == "sections/a.tex" and warns[0]["line"] == 9, warns)
    check("parse_log: a package warning after the file closed gets the main file",
          warns[1]["file"] == "paper.tex" and warns[1]["msg"].startswith("hyperref:"), warns)
    check("engine_flag reads the magic comment", tv.engine_flag(main) == ["-pdf"])
    (proj / "lua.tex").write_text("% !TEX program = lualatex\n\\documentclass{article}")
    check("engine_flag: lualatex", tv.engine_flag(proj / "lua.tex") == ["-lualatex"])

    # ----- a real compile and SyncTeX (latexmk, synctex)
    if tv.tool("latexmk") and tv.tool("synctex"):
        pr = tv.Project(main)
        res = pr.compile(tv.tool("latexmk"))
        check("the sample compiles with no errors", res["status"] == "ok" and not res["errors"], res.get("errors"))
        check("the build goes to the build folder, not next to the source",
              pr.pdf().is_file() and not (proj / "paper.pdf").exists() and not (proj / "paper.aux").exists())
        check("inputs() finds the .bib file", any(p.name == "paper.bib" for p in pr.inputs()), pr.inputs())
        v = pr.view_at("paper.tex", 41)
        check("synctex view: a line has a page and boxes", v and v["page"] == 1 and v["boxes"], v)
        e = v and pr.edit_at(1, (v["boxes"][0][0] + v["boxes"][0][2]) / 2, (v["boxes"][0][1] + v["boxes"][0][3]) / 2)
        check("synctex edit: the middle of that box is line 41", e and e["file"] == "paper.tex" and e["line"] == 41, e)
        (proj / "bad.tex").write_text("\\documentclass{article}\n\\begin{document}\nok\n\\undefinedmacro\n\\end{document}\n")
        prb = tv.Project(proj / "bad.tex")
        res = prb.compile(tv.tool("latexmk"))
        check("a compile with an error: status error, the error at bad.tex:4",
              res["status"] == "error" and res["errors"] and res["errors"][0]["file"] == "bad.tex"
              and res["errors"][0]["line"] == 4, res.get("errors"))
        r = cli("log", str(proj / "bad.tex"))
        check("texview log prints the error", "error: bad.tex:4: Undefined control sequence." in r.stdout, r.stdout)
    else:
        print("skip the compile tests: latexmk or synctex is missing")
finally:
    if os.environ.get("TEXVIEW_KEEP_TMP"):
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("all passed" if not failures else f"{len(failures)} FAILED: " + ", ".join(failures)))
sys.exit(1 if failures else 0)
