# pdfview

Read PDF files in the browser, comment on them, and ask Claude Code about them.

**Install:** use `install.sh` (see the section Install in the README of `viewkit`). It installs all the viewers and their Claude Code skills, and builds the macOS apps.

pdfview never changes the PDF. The comments are in a JSON file next to the PDF, in `.pdfview/NAME.pdf.json`. Claude reads and writes them with the `pdfview` subcommands.

## Files

| File | Purpose |
|---|---|
| `pdfview.py` | The `pdfview` command and the local server. It serves the PDF, keeps the comments file, and runs the chat. Standard library only. |
| `viewer.html`, `viewer.js` | The page. |
| `vendor/pdfjs/` | A local copy of PDF.js 4.10.38 (`pdfjs-dist` from npm): `pdf.min.mjs`, `pdf.worker.min.mjs`, `cmaps/`, `standard_fonts/`. Nothing loads from the internet. |
| `tests/` | `test_ops.py` (no server), `browser_test.py` (Playwright), `make_fixture.py` and `fixture.pdf` (the test PDF). |
| `samples/` | Sample PDFs, the baseline of the previews and of `tests/samples_test.py`: `paper.pdf` (an outline, equations, a theorem, a figure, a table, citations, links, three comment threads in `samples/.pdfview/`), `twocolumn.pdf`, `slides.pdf` (beamer) and `scan.pdf` (an image with no text layer). `make_samples.py` makes them again from `samples/src/` (needs pdflatex, pdftoppm and Pillow). |
| `improve.json` | The manifest of the automatic loop: the tests, the port variable, the comments file, and the samples. |
| `../viewkit/` | The code that mdview, pyview and pdfview share: the server life cycle, the Claude chat run, the chat panel, Markdown and maths for comments, and MathJax. It must be next to `pdfview`, as `~/tools/viewkit`. |
| `~/.local/bin/pdfview` | Link to `pdfview.py`, so `pdfview` works from any folder. The chat and the Oracle need it, because Claude calls `pdfview`. |
| `../viewkit/skills/answer-pdf/` | `/answer-pdf`: the skill of the Oracle. Claude answers the open comments on a PDF. `install.sh` of viewkit links it into `~/.claude/skills/`. |

Requires Python 3.9 or newer and the folder `viewkit` next to `pdfview`. `pdftotext`, `pdfinfo` and `pdftoppm` (from poppler, `brew install poppler`) are necessary for `pdfview text` and `pdfview crop`.

## Installation

Use `install.sh` (see the section Install in the README of `viewkit`). To install by hand, put `pdfview` and `viewkit` next to each other in `~/tools`, and link the command:

```
ln -s ~/tools/pdfview/pdfview.py ~/.local/bin/pdfview
```

## Open `.pdf` files by double-clicking in Finder

```
app/build.sh     # builds ~/Applications/pdfview.app (run again to rebuild)
```

Then select a `.pdf` file in Finder, choose Get Info > Open with > pdfview > Change All…

- The app has no window and no Dock icon. Its Finder icon is the tab icon of pdfview.
- It gives the files to `~/.local/bin/pdfview`, so that link must exist. If you open the app with no file, it opens your home folder.
- The builder is shared with the other viewers: `../viewkit/app/build.sh`.
- With Change All, all PDF files open in pdfview, not in Preview. To open one file in Preview, use Open with > Preview.
- On a new Mac: clone `pdfview` and `viewkit` into `~/tools`, link `pdfview.py` to `~/.local/bin/pdfview`, and run `app/build.sh`.

## Use

```
pdfview paper.pdf                      open a file in your browser
pdfview a.pdf b.pdf                    open several, each in its own tab
pdfview                                browse the current folder
pdfview list paper.pdf [--json]        print the comment threads
pdfview reply paper.pdf ID 'text'      add a reply to thread ID (the author is claude)
pdfview add paper.pdf 'text' --page N --quote 'words on the page'   add a comment (without --page: a general comment)
pdfview reply paper.pdf ID - <<'EOF'   the text from stdin, up to a line EOF (also for add)
pdfview text paper.pdf [--page N | --pages A-B]   print the text, page by page
pdfview add paper.pdf 'text' --page N --box x0,y0,x1,y1   add a comment on an area of a page (a figure, an equation)
pdfview text paper.pdf --page N --box x0,y0,x1,y1  print only the text in that area
pdfview crop paper.pdf --page N --box x0,y0,x1,y1  make a PNG of that area, to look at it (needs pdftoppm)
pdfview note 'text'                    a note to pdfview itself: a problem or a wish (a GitHub issue, see below)
pdfview try N [paper.pdf]              try the proposal of note N in a sandbox (copies of the files, port 8900+N)
pdfview try --stop N                   stop that sandbox
pdfview --status | --stop | --restart
```

Give the text as `-` and on stdin when it contains `$`, quotes or new lines. With a quoted heredoc (`<<'EOF'`), the shell does not change the text. The Oracle and the chat use this form.

One shared server runs in the background at `http://127.0.0.1:8767` (set `PDFVIEW_PORT` to change it). It opens only the files and folders that you passed to `pdfview` since it started. It serves files under your home folder (`PDFVIEW_ROOT`). It restarts itself when `pdfview.py` or a `.py` file of `viewkit` changes. Its log is `~/.cache/pdfview/server.log`.

### In the viewer

- **Comment on an area:** hold ⌥ Option (the cursor becomes a cross) and drag a box on a page. Esc stops a drag. A comment box opens under the box. Use it for a figure, an equation, a table, or a scanned page. The page shows the area as an orange frame. A click on the tab of the frame selects its comment, and its **×** deletes the comment at once (Undo brings it back). The text in the box goes with the comment, for Claude. The text inside a frame stays selectable.
- **Highlight:** select text on a page, then click **🖍 Highlight** (or press ⌘/Ctrl+Shift+H). It marks the words in green, with no comment. A click on a highlight gives **💬 Comment** (the highlight becomes a comment) and **Remove**. Highlights are not open comments, and **Resolve all** keeps them.
- **Comment:** select text on a page, then click **💬 Comment** (or press ⌘/Ctrl+Shift+M). A comment box opens on the page, under the words. The sidebar does not open. Write the comment and press ⌘/Ctrl+Enter. Esc or **Cancel** closes the box. A click on the page outside an empty box closes it too. A box with text stays open. **+ Comment** at the top of the sidebar adds a comment about the whole file.
- **Comments in the sidebar:** a click on a comment shows its words on the page. A click on a highlight on the page selects its comment. **Reply** and **Resolve** are on each comment. **Resolve all** is at the top of the sidebar. **Edit** and **Delete** show when the pointer is on a message. Resolve removes the comment. **Undo** in the header takes back the last change, also a change by Claude.
- **Maths** in comments: `$x^2$` and `$$ … $$`.
- **Dark pages** (◐ Dark, or the key `d`): the text, the lines and the page are dark, and the images keep their colors. Vector figures become dark too, with colors close to the original. A scanned page is one large image, so it becomes dark as a whole. The default follows the dark mode of your system.
- **Zoom:** pinch on the trackpad, or use `−`, `+` and **Fit** in the header, or the keys `-`, `+` and `0`. **Fit** fits the width of the pages column. A pinch keeps the words under your fingers in place. The pages stretch while you pinch, and they become sharp when you stop. Control and the mouse wheel zoom too.
- **Pages:** type a page number in the header and press Enter.
- **Contents:** ☰ (or the key `o`) shows the contents of the PDF, if it has them.
- **Links:** a click on a link to a place inside the PDF (a reference, a citation, an equation, a section) goes to that place. These links are blue, in light and in dark pages. The pointer becomes a hand on a link. ⌘/Ctrl+[ returns to the place before the link. A click on a link shows no notice. A drag on a link selects its words, as on other text. A web link in the PDF does not open.
- **Comments** (header, or ⌘/Ctrl+\\) hides and shows the sidebar.
- **Oracle:** click **Oracle** at the top of the sidebar. It starts Claude Code in the background (`claude -p "/answer-pdf FILE"`, from the folder of the PDF). Claude reads the pages of the open comments and replies in each thread. The replies show in the sidebar while it works.
  - While it works, the button shows **Working… ■**. Click it to stop. When it ends, **Report** in the notice shows the summary of Claude.
  - **One session per file.** Later runs continue the session, so Claude remembers earlier rounds. **New session** under the sidebar heading forgets them.
  - **One run per file** at a time. The run holds a lock in `~/.cache/pdfview/claude/` until it exits.
  - The run can read files, run `pdfview`, `pdftotext` and `pdfinfo`, and search or fetch from the web. It cannot edit files.
  - One **Undo** takes back all the replies of a run.
  - The Oracle and the chat do not run at the same time on the same file.
  - The skill is `../viewkit/skills/answer-pdf/`. `install.sh` of viewkit links it into `~/.claude/skills/`. You can also run `/answer-pdf paper.pdf` yourself in Claude Code.
- **Tab title and header:** the title is "NAME · CONTEXT", for example "paper.pdf · thesis/reading". The context is the Git repo of the file and the folder in it, or the parent folder outside a repo. When two open files have the same name and context, the context gets more folders. The header shows the same path. A click on the name copies the full path.
- **Host name:** the links use `pdfview.localhost`, not `127.0.0.1`. Each viewer has its own host name, so Safari can show its tab icon. Old links with `127.0.0.1` still work.
- **Tab icon:** the browser tab shows the icon of the viewer: a red page (the flat drawing, with no gray plate). The icon files (`/kit/icons/`, `/favicon.ico`) do not need the access key, because Safari loads the tab icon without the cookie. An orange badge shows the number of open comments. A cyan dot shows that Claude works (chat or Oracle). A yellow dot shows that an answer waits for you.
- **Chat** (bottom right, or ⌘/Ctrl+S): ask Claude about the PDF. Claude gets the selected text with its page, and the page that you look at. Claude can answer and add comments when you ask. Select **Ask only** to stop that. To change the text size of the chat, put the focus in the chat and push ⌘/Ctrl+= or ⌘/Ctrl+−. ⌘/Ctrl+0 gives the default size. A pinch or Ctrl+wheel on the chat also changes the size, and does not zoom the PDF. The size stays after a reload. The conversation is kept in `~/.cache/pdfview/chat/`, one per PDF. Set `PDFVIEW_CLAUDE` if `claude` is not in your `PATH`.
  - **Size:** drag the left edge, the top edge or the top-left corner of the panel to resize it. A double-click on one of them gives the default size back. The viewer keeps the size.
  - **Copy:** each code block in an answer has a **Copy** button at the top right. A click puts the code on the clipboard.
- **Improve pdfview** (⌘/Ctrl+Shift+.): the dashboard of the notes to the app. A note is a problem or a wish about pdfview itself, not about the PDF.
  - **New note** (right): write the note and press Enter (or **Add**). The note does not go yet. It waits in **Notes to send** (left), with the open PDF and the state of the viewer at that time. A click on a waiting note edits it, and **×** deletes it. The notes stay in the list after a reload, in `~/.cache/viewkit/improve/pdfview-drafts.json`.
  - **For: All viewers / Only pdfview** (under the note box): the scope of the new note. With **All viewers** (the default), Claude puts a shared change into viewkit, so all the viewers get it.
  - **Send all** sends each waiting note. Claude works on the notes one after the other.
  - **Status** (below) shows each run: Claude works, tests run, preview ready (**Open**), question or failed (**GitHub**), shipped or rejected. A question of Claude, or a failure, shows its text and a box: **Answer** (or **Try again**) starts the work again on that note. The page tells you when a preview opens, also with the dashboard closed. Esc or the hotkey closes the dashboard.
  - Each sent note becomes a GitHub issue with the label `note` in the pdfview repository. The note gets the commit of pdfview and the state of the viewer: the page number, the zoom, the panels, and the last buttons that you clicked. It never gets the text, the name or the path of the PDF. Without the network, the note waits in `~/.cache/viewkit/notes-queue.jsonl` and goes with the next note.
  - Claude writes notes too, with `pdfview note`, if the Oracle cannot do its work with the subcommands. These notes wait for `/improve pdfview`.
  - The test of the loop is in viewkit: `uv run --with playwright python tests/improve_test.py pdfview`.
  - **The automatic loop:** after Send all, Claude implements each note in the background. When the tests pass, a preview tab opens. The preview shows the new version of pdfview with copies of the PDF of the note and of the sample PDFs (`samples/`), with their comments. The links under the summary of the card ("Try it on") open the other files in the preview. A card at the top of the preview has **Accept** (merge the change into pdfview, commit and push it), **Change…** (tell Claude what to change, and the preview reloads with the new version), and **Reject** (close the note). The live tab tells you when the preview is open, or when Claude asks a question on GitHub. `VIEWKIT_AUTO_IMPROVE=0` turns the automatic loop off.
  - Claude makes a proposal for each note on the branch `improve/note-N`. `pdfview try N paper.pdf` opens copies of the PDF and its comments in the version of the proposal, on port 8900+N. Your real files do not change. Without a file, it opens the test PDF. To work on the notes, run `/improve pdfview` in Claude Code (the `improve` skill, protocol in `viewkit/docs/self-improving-apps.md`).

## The comments file

```json
{"threads": [
  {"id": 1,
   "anchor": {"page": 4, "quote": "the bound is tight", "prefix": "We show that ", "suffix": " for all n."},
   "msgs": [{"author": "you", "text": "Why is it tight?", "ts": 1730000000},
            {"author": "claude", "text": "Because ...", "ts": 1730000100}]}
]}
```

- **The anchor** tells the viewer where the comment belongs. It holds the page (from 1), the selected words (`quote`), and up to 40 characters before and after them (`prefix`, `suffix`). The viewer finds the words on the page again. If the words occur more than one time, the words before and after select the correct place. If the exact words are not on the page, the viewer tries again without spaces and line-end hyphens. `pdftotext` and the page can differ there. If it still cannot find them, the comment shows **not found**.
- **A box anchor** holds the page, the area (`box`: `[x0, y0, x1, y1]` in PDF points from the top-left corner of the page), and the text in it (`quote`, optional). The area stays in place at every zoom.
- **A general comment** has no anchor.
- **Highlights** are in a second list, `"highlights": [{"id": 1, "anchor": {…}, "ts": …}]`, with the same anchors and no messages. `pdfview list` shows them after the comments.
- **The status** is Answered when the last message is from Claude, and Open in all other cases.
- When the last comment goes, the file goes too, and the folder `.pdfview` goes if it is empty.

## Security

- PDF.js runs with no scripting and no `eval` (`isEvalSupported: false`). A link to a place inside the PDF goes to that place. A web link in the PDF does not open.
- The Content-Security-Policy of the page allows only the scripts of the server: `viewer.js`, the kit, PDF.js and its worker, and MathJax.
- Every request needs the access key in `~/.cache/pdfview/token-PORT` (readable only by you). The exceptions are `/api/ping` and the tab icons. `pdfview` puts the key in the link that it opens, and the browser keeps it as a cookie.
- The server opens only the files and folders that you passed to `pdfview`. It never reads, lists or serves hidden files and folders, so the comments file is not served either.
- A note to the app (`/api/note`, `pdfview note`) goes to GitHub with `gh`. The server sends only the text of the note, the commit, and short values of the state of the viewer. It never sends the text, the name or the path of the PDF.
- The chat can never edit files: `Edit` and `Write` are not allowed. With **Ask only**, `pdfview reply` and `pdfview add` are not allowed.

## Tests

```
python3 tests/test_ops.py                                   # the comments file and the subcommands
uv run --with playwright python tests/browser_test.py       # the viewer, in a real browser (port 8810)
uv run --with playwright python tests/samples_test.py       # each sample PDF opens and draws, with no error (port 8813)
python3 tests/make_fixture.py                               # make tests/fixture.pdf again
```

Each test makes its temporary folder in `tests/tmp/`, and Git ignores this folder. The test deletes its folder at the end, also when it fails. To keep the folder for debugging, set `PDFVIEW_KEEP_TMP=1`.

## Limits

- Links in the PDF and form fields do not work. Annotations show as the PDF draws them, but you cannot change them.
- The whole PDF loads at the start. A very large file takes some time.
- If you move or rename the PDF, move `.pdfview/NAME.pdf.json` with it, or the comments stay behind.

## License

MIT. See `LICENSE`. The files in `vendor/pdfjs/` are PDF.js (Apache License 2.0, `vendor/pdfjs/LICENSE`). The fonts and the CMaps in it have their own licenses in their folders.
