# texview

Read, edit and comment on a LaTeX document in the browser, next to its PDF, and ask Claude Code about it.

**Install:** use `install.sh` (see the section Install in the README of `viewkit`). It installs all the viewers and their Claude Code skills, and builds the macOS apps.

texview compiles the document with `latexmk` and SyncTeX. The build files go to `~/.cache/texview/build/`, so the folder of the document stays clean. A double-click on the PDF opens or closes the source. A ⌘/Ctrl+click on the PDF opens the source at that line. A ⌘/Ctrl+click on a source line shows its place in the PDF.

Claude never edits the source files. When you ask for a change, Claude suggests it. The change shows in the editor as a tracked change, and you accept or reject it. The comments and the suggested changes are in a JSON file next to the main file, in `.texview/MAIN.tex.json`.

## Files

| File | Purpose |
|---|---|
| `texview.py` | The `texview` command and the local server. It compiles, watches the source files, finds places with SyncTeX, saves the sources, keeps the comments file, and runs the chat and the Oracle. Standard library only. |
| `viewer.html`, `viewer.js` | The page: the source editor, the PDF, the comments and the chat. |
| `vendor/codemirror/` | A local copy of CodeMirror 5.65.21 (`codemirror` from npm), with the LaTeX mode and the addons that texview uses. Nothing loads from the internet. |
| `tests/` | `test_ops.py` (no server) and `browser_test.py` (Playwright). |
| `samples/` | `paper.tex` and `paper.bib`: a short document with a theorem, an equation, a citation and references. The previews of the automatic loop open a copy of it. |
| `improve.json` | The manifest of the automatic loop: the tests, the port variable, the comments file, and the samples. |
| `../viewkit/` | The code that the viewers share: the server life cycle, the Claude chat run, the chat panel, the PDF pages (`web/pdfpages.js`, PDF.js in `web/vendor/pdfjs/`), Markdown and maths for comments, and MathJax. It must be next to `texview`. |
| `~/.local/bin/texview` | Link to `texview.py`, so `texview` works from any folder. The chat and the Oracle need it, because Claude calls `texview`. |
| `../viewkit/skills/answer-tex/` | `/answer-tex`: the skill of the Oracle. Claude answers the open comments and suggests changes. `install.sh` of viewkit links it into `~/.claude/skills/`. |

Requires Python 3.9 or newer, the folder `viewkit` next to `texview`, and a TeX distribution with `latexmk` and `synctex` (MacTeX or TeX Live: `brew install --cask mactex-no-gui`). texview finds them in `/Library/TeX/texbin` also when Finder starts it. `pdftotext` (`brew install poppler`) is necessary for `texview text`.

## Open `.tex` files by double-clicking in Finder

```
app/build.sh     # builds ~/Applications/texview.app (run again to rebuild)
```

To make texview the default app of `.tex` files, run `app/build.sh --default` (it needs `duti`: `brew install duti`). Or select a `.tex` file in Finder, choose Get Info > Open with > texview > Change All…

- Open the **main** file of the document (the file with `\documentclass`). texview compiles it and shows the other files of the document in the file list.
- The app has no window and no Dock icon. It gives the files to `~/.local/bin/texview`, so that link must exist.

## Use

```
texview main.tex                       open a document in your browser
texview                                browse the current folder (it lists the files with \documentclass)
texview list main.tex [--json]         print the comments and the suggested changes
texview reply main.tex ID 'text'       add a reply to thread ID (the author is claude)
texview add main.tex 'text' --file F --line N [--quote 'words']   add a comment on a source line (without --file: a general comment)
texview suggest main.tex --file F [--line N] <<'EOF'    suggest changes (see below)
texview log main.tex                   print the errors and warnings of the last compile
texview text main.tex [--page N | --pages A-B]   print the text of the compiled PDF
texview build main.tex                 compile now and print the errors
texview note 'text'                    a note to texview itself: a problem or a wish
texview try N [main.tex]               try the proposal of note N in a sandbox (port 8900+N)
texview --status | --stop | --restart
```

Give the text as `-` and on stdin when it contains `$`, backslashes, quotes or new lines. With a quoted heredoc (`<<'EOF'`), the shell does not change the text. The Oracle and the chat use this form.

A suggested change has this form on stdin. One call can hold more than one block. The lines after a block are the reason, and they are optional:

```
<<<<<<< old
the exact old text, copied from the file
=======
the new text (empty: delete the old text)
>>>>>>> new
A short reason.
```

The old text must be in the file exactly. If it occurs more than one time, give `--line` (the line where it starts).

One shared server runs in the background at `http://127.0.0.1:8770` (set `TEXVIEW_PORT` to change it). It opens only the folders of the documents that you passed to `texview`. It serves files under your home folder (`TEXVIEW_ROOT`). It restarts itself when `texview.py` or a `.py` file of `viewkit` changes. Its log is `~/.cache/texview/server.log`.

### The compile

- texview runs `latexmk -pdf -synctex=1 -interaction=nonstopmode -file-line-error -f` in the folder of the main file. The build files go to `~/.cache/texview/build/` (`TEXVIEW_BUILD` changes it).
- **The engine:** the magic comment `% !TEX program = lualatex` (or `xelatex`, `pdflatex`) in the first lines of the main file sets it. Without it, texview uses pdflatex. If the folder has a `latexmkrc` file and the main file has no magic comment, `latexmkrc` decides.
- **When:** when you open the document, when you save, when you accept a change, and when a file of the document changes on disk (for example in another editor). The files of the document are the files that the last compile read, the `.bib` files too.
- **The header** shows the state: Compiling…, Compiled (with the time), or the number of errors. A click compiles now. During a compile, a click stops it. ⌘/Ctrl+Shift+B compiles too.
- **Problems:** the errors (and the warnings, with **Show warnings**) show under the source, with the file and the line. A click opens that line. The editor marks the error lines in red and the warning lines with a yellow dot. **Log** shows the output of `latexmk`.
- With errors, texview still shows the PDF if TeX made one (`-f`).

### In the viewer

- **Source → PDF:** ⌘/Ctrl+click a line in the source, or click **→ PDF**. The place in the PDF shows with an orange box for a moment.
- **PDF → source:** ⌘/Ctrl+click a place on a page. The source opens at that line if it is closed, and the line flashes. You can also select text on a page and click **✎ Source**.
- **Double-click on a page:** this opens or closes the source. The cursor in the source does not move.
- **Edit and save:** edit in the source pane. In the source, ⌘/Ctrl+S saves all the changed files (a dot shows a file with unsaved changes), and the server compiles. The editor keeps a text, a cursor and an Undo for each file while the page is open. If a file changes on disk and you have no unsaved changes, the editor takes the new text. With unsaved changes, a notice asks what to do. When you close the tab with unsaved changes, the browser asks first.
- **The editor:** it has LaTeX colors, line numbers and bracket pairs. ⌘/Ctrl+/ comments out the lines (`%`). ⌘/Ctrl+F opens the find bar over the editor, with a Find field and a Replace field (⌘/Ctrl+Alt+F puts the cursor in the Replace field). The bar shows the number of matches. Enter goes to the next match, and Shift+Enter goes to the previous match. In the Replace field, Enter replaces this match and goes to the next match. **Replace all** (or ⌘/Ctrl+Enter) replaces all the matches in the file, and one ⌘/Ctrl+Z undoes it. A query in lower case also finds capital letters. Esc closes the bar. The file list (top left) shows the files of the document first, then the other documents of the folder (the `.tex` files with `\documentclass`), then the other source files of the folder. Each subdirectory has a group of its own (📁 `name/`), with its documents first (📄).
- **Suggestions:** a popup shows suggestions while you type in the source. After `\` and a letter, it shows commands. After `\ref{` and the other reference commands, it shows labels, with the number when the label is compiled. After `\cite{` and the other citation commands, it shows the keys of the `.bib` files. In `\cite{a,b`, only the last key changes. After `\begin{` or `\end{`, it shows environments. The lists include the `\newcommand`, `\renewcommand`, `\providecommand`, `\def` and `\newenvironment` lines of the sources, and the labels of the open files that are not compiled yet. Enter or Tab accepts the selected suggestion, and Esc closes the popup. The popup does not show in a `%` comment, and it uses no AI model. Only the data of the document in texview is used.
- **Other documents:** choose a file in the group **Other documents** of the file list. texview opens that document, with its source and its PDF, in the same tab. The browser Back button goes back to the first document. In a document of a subdirectory, the group **↑ ../** of the file list shows the documents of the folder above. Choose one to go back up. This group shows only when you passed the folder above to texview.
- **Comment:** select text in the source or on a page, and click **💬 Comment** next to the selection (or press ⌘/Ctrl+Shift+M). For a selection on a page, the comment goes on the source line of that place. The comment box opens next to the selection, and the sidebar stays closed. Write the comment and press ⌘/Ctrl+Enter. **Send** saves the comment and starts the Oracle at once. If the Oracle is already working, the next Oracle run answers the comment. Without a selection, **+ Comment** adds a general comment in the sidebar.
- **Comments in the sidebar:** a click on a comment opens its line in the source and shows its place in the PDF. **Reply** and **Resolve** are on each comment. **Edit** and **Delete** show when the pointer is on a message. The source shows each comment with a yellow mark. A click on the mark selects the comment.
- **Suggested changes:** a change of Claude shows in the source as a tracked change. A red line goes through the words that go, and the words that come follow in green. Click **✓** to accept or **✗** to reject, in the source or in the sidebar. **Accept** puts the new text into the file and compiles. If the file has unsaved changes, texview saves them first. If the old text is no longer in the file, the change does not fit, and texview tells you. **Accept all** and **Reject all** show when there are two or more changes.
- **Undo** (header) takes back the last change to the comments, also an Accept (the file gets its old text back) and a change by Claude. texview refuses to undo an Accept when the file changed after it: then use ⌘/Ctrl+Z in the editor.
- **Chat** (bottom right, or ⌘/Ctrl+S outside the source, as in the other viewers): ask Claude about the document. Claude gets the text that you selected (in the source, with its file and lines, or on a page, with its source line), the line of the cursor, and the page that you look at. When you ask for a change, Claude suggests it with `texview suggest`. It can also add and answer comments when you ask. **Ask only** stops all of that. texview keeps the conversation in `~/.cache/texview/chat/`, one for each document. Set `TEXVIEW_CLAUDE` if `claude` is not in your `PATH`.
- **Oracle:** click **Oracle** at the top of the sidebar. It starts Claude Code in the background (`claude -p "/answer-tex FILE"`, from the folder of the main file). Claude answers each open comment, and suggests a change when a comment asks for one. One session per document: later runs continue it, and **New session** forgets it. One run per document at a time. One **Undo** takes back all the replies of a run.
- **Zoom keys:** Option with `+`, `-` or `0` zooms the pane of the focus or of the last click. In the source, it changes the size of the text, and `0` gives the default size. On the pages, it changes the zoom, and `0` fits the page width. In a text box (a comment, the chat, the find bar), Option types its character.
- **Pages:** zoom with `−`, `+` and **Fit** (keys `-`, `+`, `0`, also with Option) or a pinch. **◐ Dark** (key `d`) makes the pages dark. A click on a link inside the PDF goes to its place. ⌘/Ctrl+[ goes back to the place before a jump, and ⌘/Ctrl+] goes forward again. A jump is a link, a section in the contents, or a page number. In the source, these keys change the indent. Type a page number in the header to go there.
- **Source text:** the size of the source text is independent of the zoom of the pages. Click in the source, then use Option or ⌘/Ctrl with `+`, `-` or `0` (the default size). A pinch on the source also changes it.
- **Panels:** **Source** (Control+S on a Mac, Option+Tab, or the key `e`) shows or hides the source. On a Mac, ⌘+S stays the key for Save and the chat. On other systems, Ctrl+S is that key, so use Option+Tab or `e` for the source. Drag the bar between the source and the pages to resize them, and double-click it for the default size. **Comments** (⌘/Ctrl+D or ⌘/Ctrl+\\) shows or hides the sidebar. **☰** (⌘/Ctrl+E or the key `o`) shows or hides the contents of the PDF, left of the pages. Click a section to go to it. The contents come from the outline of the PDF, so the document must load `hyperref`.
- **Tab icon:** a purple square with the TeX mark. An orange badge shows the number of open comments and changes. A cyan dot shows that Claude works or that a compile runs.
- **Improve texview** (⌘/Ctrl+Shift+.): the dashboard of the notes to the app, as in the other viewers (see the README of pdfview). A note gets the state of the viewer, never the text, the name or the path of the document.

## The comments file

```json
{"threads": [
  {"id": 1,
   "anchor": {"file": "sections/intro.tex", "line": 12, "quote": "the bound is tight", "prefix": "We show that ", "suffix": " for all n."},
   "msgs": [{"author": "you", "text": "Why is it tight?", "ts": 1730000000}]},
  {"id": 2,
   "anchor": {"file": "paper.tex", "line": 41, "quote": "tight", "prefix": "The bound is ", "suffix": " for all"},
   "change": {"old": "tight", "new": "sharp"},
   "msgs": [{"author": "claude", "text": "The usual word.", "ts": 1730000100}]}
]}
```

- **The anchor** is a place in a source file: the file (relative to the folder of the main file), the line at the time of the comment, the words (`quote`), and up to 40 characters before and after them (`prefix`, `suffix`). The viewer finds the words again after edits. When the words occur more than one time, the words before and after decide, then the nearest line.
- **A change** has the old text and the new text. Its anchor holds the old text.
- **A general comment** has no anchor.
- **The status** is Change for a suggested change, Answered when the last message is from Claude, and Open in all other cases.
- When the last thread goes, the file goes too, and the folder `.texview` goes if it is empty.

## Security

- Every request needs the access key in `~/.cache/texview/token-PORT` (readable only by you). The exceptions are `/api/ping` and the tab icons. `texview` puts the key in the link that it opens, and the browser keeps it as a cookie.
- The server opens only the folders of the documents that you passed to `texview`. It reads and writes only text sources (`.tex`, `.bib`, `.sty`, `.cls`, `.bst`, …) in the folder of the main file. It never reads, lists or serves hidden files and folders. For the suggestions in the editor, it also reads the `.aux` files of the build folder of the document, and it sends only the names and numbers of the labels to the page.
- The Content-Security-Policy of the page allows only the scripts of the server: `viewer.js`, the kit, PDF.js and its worker, CodeMirror, and MathJax. PDF.js runs with no scripting and no `eval`.
- The chat and the Oracle can never edit files: `Edit`, `Write` and `texview build` are not allowed. A source file changes only when you save or accept a change. With **Ask only**, `texview suggest`, `texview add` and `texview reply` are not allowed.
- texview runs `latexmk` on your document, as you, when you open it and when its files change. `latexmk` reads a `latexmkrc` file in the folder of the document, and that file can run any program. A document can also run programs if your TeX configuration allows shell escape (texview does not turn it on). Open only documents that you trust.

## Tests

```
python3 tests/test_ops.py                                   # the sources, anchors, changes, Accept, the log, a real compile
uv run --with playwright python tests/browser_test.py       # the viewer, in a real browser (port 8820)
```

Each test makes its temporary folder in `tests/tmp/`, and Git ignores this folder. The test deletes its folder at the end, also when it fails. To keep the folder for debugging, set `TEXVIEW_KEEP_TMP=1`.

## Limits

- One main file per document. Open the file with `\documentclass`. A comment or a change in an `\input` file works, but `texview` always compiles the main file.
- The sandbox of a proposal (`texview try`) copies only the main file, its comments file and `paper.bib`. A document with `\input` files does not compile there in full.
- The PDF shows the comments only when you click them (a box at their place for a moment). It has no marks of its own for the comments.

## License

MIT. See `LICENSE`. CodeMirror in `vendor/codemirror/` is MIT too (`vendor/codemirror/LICENSE`). PDF.js is in `../viewkit/web/vendor/pdfjs/` (Apache License 2.0).
