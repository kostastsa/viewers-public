# pageview

Show rich pages in the browser, comment on them, and ask Claude Code about them. A page is a Markdown file with a few extra blocks. The blocks are tables and charts from CSV or JSON files, figures, tabs, toggles, columns, callouts, stat cards, maths, code, and small widgets. pageview also opens a CSV, TSV or JSON file directly, as one table that fills the window.

**Install:** use `install.sh` (see the section Install in the README of `viewkit`). It installs all the viewers and their Claude Code skills, and builds the macOS apps.

pageview is a local replacement for the Artifacts of claude.ai. Claude writes the page and its data files. pageview shows them, and updates the page when a file changes.

pageview never changes the page or its data. The comments are in a JSON file next to the page, in `.pageview/NAME.json`. Claude reads and writes them with the `pageview` subcommands.

## Files

| File | Purpose |
|---|---|
| `pageview.py` | The `pageview` command and the local server. It renders the page with pandoc, and serves the data files, the images and the widgets. It keeps the comments file, and runs the chat and the Oracle. Standard library only. |
| `page.lua` | The pandoc filter that makes a page inert (see Security). |
| `viewer.html`, `viewer.js` | The page in the browser: the layout, the comments, the chat, and the live update. |
| `SYNTAX.md` | The syntax of the blocks. It is also a pageview page. |
| `examples/` | An example page (`optimizers/optimizers.md`) and a data file with settings (`vision_grid.csv`, `vision_grid.view.json`). The numbers are invented. `make_data.py` makes them again. |
| `tests/` | `test_ops.py` (no server) and `browser_test.py` (Playwright). |
| `app/build.sh` | Builds `~/Applications/pageview.app`, to open files from Finder (Open With). |
| `../viewkit/` | The code that the viewers share: the blocks (`web/components.js`, `web/components.css`: tables, grids, charts, figures, tabs, toggles, columns, callouts, stat cards, code, widgets), the data reader (`datakit.py`), the server life cycle and the chat. It must be next to `pageview`, as `~/tools/viewers/viewkit`. |
| `~/.local/bin/pageview` | A link to `pageview.py`, so `pageview` works from any folder. The chat and the Oracle need it. |
| `../viewkit/skills/page/` | `/page`: the skill that tells Claude how to write a page. `install.sh` of viewkit links it into `~/.claude/skills/`. |
| `../viewkit/skills/answer-page/` | `/answer-page`: the skill of the Oracle. Claude answers the open comments of a page. `install.sh` of viewkit links it into `~/.claude/skills/`. |

Requires Python 3.9 or newer, pandoc 3 (`brew install pandoc`), and the folder `viewkit` next to `pageview`. A PDF figure needs `pdftoppm` (from poppler, `brew install poppler`). Without it, pageview uses `sips` of macOS.

## Installation

Use `install.sh` (see the section Install in the README of `viewkit`). To install by hand, put `pageview` and `viewkit` next to each other in `~/tools`, and link the command:

```
ln -s ~/tools/viewers/pageview/pageview.py ~/.local/bin/pageview
```

## Open files from Finder

```
app/build.sh     # builds ~/Applications/pageview.app (run again to rebuild)
```

Then right click a `.md`, `.markdown`, `.csv`, `.tsv` or `.json` file in Finder, and choose **Open With > pageview**.

- pageview does not become the default app of these files. `.md` files still open in mdview by a double click.
- To make pageview the default for its types, run `app/build.sh --default` (it needs `duti`). Or select a file and choose Get Info > Open with > pageview > Change All…
- The app has no window and no Dock icon. It gives the files to `~/.local/bin/pageview`, so that link must exist.
- The builder is shared: `../viewkit/app/build.sh`. Set `PAGEVIEW_KIT` to use another copy of the kit.

## Use

```
pageview page.md                     open a page in your browser
pageview runs.csv                    open a data file as a table
pageview a.md b.csv                  open several, each in its own tab
pageview                             browse the current folder
pageview list page.md [--json]       print the comment threads
pageview reply page.md ID 'text'     add a reply to thread ID (the author is claude)
pageview add page.md 'text' --quote 'words of the page'          add a comment on words of the page
pageview add page.md 'text' --cell runs.csv AdamW acc            add a comment on a table cell
pageview add page.md 'text'          add a general comment
pageview reply page.md ID - <<'EOF'  the text from stdin, up to a line EOF (also for add)
pageview --status | --stop | --restart
```

Give the text as `-` and on stdin when it contains `$`, quotes or new lines. With a quoted heredoc (`<<'EOF'`), the shell does not change the text.

One shared server runs in the background at `http://127.0.0.1:8768` (set `PAGEVIEW_PORT` to change it). It opens only the files and folders that you passed to `pageview` since it started. It serves files under your home folder (`PAGEVIEW_ROOT`). It restarts itself when `pageview.py` or a `.py` file of `viewkit` changes. Its log is `~/.cache/pageview/server.log`.

### In the viewer

- **Blocks:** `SYNTAX.md` tells how to write each block. Open it with `pageview ~/tools/viewers/pageview/SYNTAX.md`.
- **Source:** move the pointer on a block. Click **Source** to see its lines in the file. **Source** in the header shows the button on all blocks.
- **Tables:** click a column name to sort. Type in **Filter rows** to show only the rows that match. **Columns** hides columns. **⤢ Wide** uses the full width of the page. **⛶** opens the table in the full window (Esc closes it). **LaTeX**, **MD** and **CSV** copy the table as you see it.
- **Grids** (`rows` and `columns`): **Best per** (column or row), **Color** (heat or rank), **± sd**, **Compact** and **⇄ Transpose**.
- **Charts:** move the pointer on the chart to read the values. Click a name in the legend to hide its series.
- **Figures:** click an image to see it large.
- **Comment on words:** select words on the page, then click **💬 Comment** (or press ⌘/Ctrl+Shift+M). A comment box opens under the words. Write the comment and press ⌘/Ctrl+Enter. The sidebar does not open.
- **Comment on a table cell:** click the cell, then click **💬 Comment on cell**. A cell with comments has an orange corner.
- **Comments sidebar** (header, or ⌘/Ctrl+\\): the threads, **+ Comment** (about the whole page), **Oracle**, and **Resolve all**. **Reply** and **Resolve** are on each comment. **Edit** and **Delete** show when the pointer is on a message. **Undo** in the header takes back the last change, also a change by Claude.
- **Oracle:** starts Claude Code in the background (`claude -p "/answer-page FILE"`). Claude replies in each open thread. One session per page. **New session** forgets the earlier rounds. The run cannot edit files.
- **Chat** (bottom right, or ⌘/Ctrl+S): ask Claude about the page. Claude gets the selected words or cell, and the section that you read. In the normal mode, Claude can change the page and its data files, and answer or add comments, when you ask. **Ask only** stops all changes.
- **Live:** the page updates when the page file, a data file, an image, or the comments change.
- **Theme:** **◐ Auto** follows the system. Click it for light, then dark.
- **Contents:** ☰ (or the key `o`) shows the sections at the left.

## The comments file

```json
{"threads": [
  {"id": 1, "anchor": {"quote": "the best test accuracy", "prefix": "AdamW gives ", "suffix": " (94.1 %)", "line": 14},
   "msgs": [{"author": "you", "text": "Significant?", "ts": 1730000000}]},
  {"id": 2, "anchor": {"table": "runs.csv", "row": "Lion", "col": "acc"},
   "msgs": [{"author": "claude", "text": "Five seeds.", "ts": 1730000100}]}
]}
```

- **A text anchor** holds the selected words (`quote`) and up to 40 characters before and after them (`prefix`, `suffix`). It also holds the line of the words in the file (`line`). The viewer finds the words again. If they occur more than one time, the words before and after and the line select the correct place.
- **A cell anchor** holds the table (its data file, or `table-N` for the N-th Markdown table), the row (the value in its key column, by default the first column) and the column.
- **A general comment** has no anchor.
- If the viewer cannot find the words or the cell, the comment shows **not found**.

## Security

- **Pages are inert.** `page.lua` shows raw HTML as text, drops event handlers and style attributes, and keeps only safe links (`http`, `https`, `mailto`, and the routes of the server).
- **Widgets run in a sandbox.** A widget block runs in a frame with an opaque origin and the Content-Security-Policy `sandbox allow-scripts; default-src 'none'`. It cannot load anything, call the server, or read the page.
- The Content-Security-Policy of the viewer allows only the scripts of the server: `viewer.js`, `components.js`, the kit, and MathJax. Nothing loads from the internet.
- Every request needs the access key in `~/.cache/pageview/token-PORT` (readable only by you). `pageview` puts the key in the link that it opens, and the browser keeps it as a cookie.
- The server opens only the files and folders that you passed to `pageview`. A page can also use data files and images in its own folder, below it, or in its Git project. A link from such a page to another page or data file of the same project opens it.
- Hidden files and folders are never read, listed or served. The comments file is not served either.
- The Oracle cannot edit files. In the chat, **Ask only** stops all edits and all comment changes.

## Tests

```
python3 tests/test_ops.py                                   # comments, subcommands, data files, render, access rules
uv run --with playwright python tests/browser_test.py       # the viewer, in a real browser (port 8811)
```

Each test makes its temporary folder in `tests/tmp/`, and Git ignores this folder. The test deletes its folder at the end, also when it fails. To keep the folder for debugging, set `PAGEVIEW_KEEP_TMP=1`.

## Limits

- Mermaid diagrams show as code. EPS figures do not show.
- No colour highlighting in code blocks.
- A data file of more than 20 MB, or the rows after the first 50 000, are not shown.
- Text comments cannot be on the text in tables. Use a cell comment there.
- If you move or rename a page, move `.pageview/NAME.json` with it, or the comments stay behind.

## License

MIT. See `LICENSE`.
