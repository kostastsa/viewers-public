# plateview

Show **plates** in the browser. A plate is an HTML page that Claude writes, like an Artifact of claude.ai. It shows tables, grids, charts and figures of your data files. The plate does not hold a copy of the data. It reads the files live, so a plate changes when an experiment writes its files again.

**Install:** use `install.sh` (see the section Install in the README of `viewkit`). It installs all the viewers and their Claude Code skills, and builds the macOS apps.

Plates are temporary. A plate stays while the Claude session that made it runs, or while a tab shows it. Then plateview deletes it. **Keep** makes a plate permanent, and **Export** saves one HTML file with the data inside. ⌘/Ctrl+P saves a PDF of the plate.

You can comment on a plate, chat with Claude about it, and let the Oracle answer the comments. See [Comments, chat and the Oracle](#comments-chat-and-the-oracle).

## Files

| File | Purpose |
|---|---|
| `plateview.py` | The `plateview` command and the local server. Standard library only. |
| `shell.html`, `shell.js` | The frame around a plate: the bar (title, state, theme, Keep, Export, Delete, Undo, Comments), the sandboxed frame, the comments sidebar, the chat panel, the Oracle, and the dashboard of the notes. At `/`, the list of plates. |
| `runtime.js` | The `pv` library inside a plate, and the bridge of the comments: it tells the frame what you select, and paints the marks of the comments. |
| `improve.json` | The settings of the self-improving loop (`viewkit/improvekit.py`): the tests, the sample plate, and the files that go with a copy of a plate. |
| `plate.css` | The default stylesheet of a plate. |
| `examples/demo/` | An example plate (`demo.html`) and its files. The numbers are invented. |
| `tests/` | `test_ops.py` (no server) and `browser_test.py` (Playwright). |
| `app/build.sh` | Builds `~/Applications/plateview.app`, so that Finder can open `.html` files with plateview (Open With). |
| `../viewkit/` | The code that the viewers share. The server parts: the server life cycle and the network address (`serverkit.py`), the data reader (`datakit.py`), the chat run and the Oracle (`claudekit.py`), and the notes (`improvekit.py`). The page parts: the chat panel, the dashboard and the tab icon (`web/kit.js`, `web/kit.css`), the components (`web/components.js`, `web/components.css`), and the fonts (`web/vendor/fonts/`, IBM Plex). It must be next to `plateview`, as `~/tools/viewers/viewkit`. |
| `~/.local/bin/plateview` | A link to `plateview.py`, so that `plateview` works from any folder. |
| `../viewkit/skills/plate/` | `/plate`: the skill that tells Claude how to write a plate. `install.sh` of viewkit links it into `~/.claude/skills/`. |
| `../viewkit/skills/answer-plate/` | `/answer-plate`: the skill of the Oracle. Claude answers the open comments of a plate. |

Requires Python 3.9 or newer, and the folder `viewkit` next to `plateview`. The chat and the Oracle need Claude Code (`claude`). A PDF figure needs `pdftoppm` (from poppler, `brew install poppler`). Without it, plateview uses `sips` of macOS.

## Installation

Use `install.sh` (see the section Install in the README of `viewkit`). To install by hand, put `plateview` and `viewkit` next to each other in `~/tools`, and link the command:

```
ln -s ~/tools/viewers/plateview/plateview.py ~/.local/bin/plateview
```

## Use

```
plateview new plate.html --allow ~/results/exp_a      make a plate and open it; it may read that folder
plateview plate.html                                  the same, and the plate may read the folder of plate.html
plateview new - --allow DIR --title T <<'EOF'         the HTML from stdin, up to a line EOF
plateview update ID plate.html                        change the HTML of a plate; the open tab shows it at once
plateview list [--json]                               the plates and their state
plateview open ID                                     open a plate in the browser
plateview keep ID                                     make a plate permanent
plateview delete ID                                   delete a plate now
plateview export ID [out.html]                        save one HTML file with the data inside
plateview show ID                                     the HTML file, the base folder, the allowed folders, the files it reads
plateview comments ID [--json]                        the comments of a plate
plateview reply ID N - <<'EOF'                        a reply to comment N (as Claude); the text up to a line EOF
plateview add ID - --quote 'WORDS' <<'EOF'            a new comment on the words of the plate (or --cell TABLE ROW COL)
plateview note TEXT                                   a note to the app (see "Notes to the app")
plateview try N [FILE]                                try the proposal of note N in a sandbox
plateview                                             the list of plates in the browser
plateview --status | --stop | --restart
```

- **Allowed folders:** give `--allow` one time for each folder (or file) that the plate may read. Without `--allow`, the plate may read the folder of its HTML file (or the current folder, for stdin).
- **Paths in a plate:** a full path, a path with `~`, or a path relative to the folder of the HTML file (`--base` sets another folder).
- **Never:** a plate never reads a hidden file or folder, or a file outside its allowed folders.
- **More files:** `plateview new a.html b.html` makes one plate for each file.
- **ID:** the commands `show`, `comments`, `reply` and `add` also accept the folder of the plate or the path of its `plate.html`.

One shared server runs in the background at `http://127.0.0.1:8769` (`PLATEVIEW_PORT`). It restarts itself when `plateview.py` or a `.py` file of `viewkit` changes. Its log is `~/.cache/plateview/server.log`. The plates are in `~/.cache/plateview/plates/`.

### A PDF of a plate

Push ⌘/Ctrl+P in the tab of a plate (also when the plate has the focus). The tooltip of **Export** tells this key.

1. A new tab opens with the print view: the exported plate in the light theme, with its data and images inside.
2. When the plate shows all its data, fonts and images, the print dialog opens.
3. In the print dialog, select **Save as PDF**.

The print view holds only the files that the plate loaded, as Export does. A panel, a stat card or a figure stays on one page if it can.

### Your other devices (Tailscale)

```
PLATEVIEW_BIND=tailscale PLATEVIEW_NAMES=my-mac plateview --restart
```

- The server then listens on the Tailscale address of this Mac. Put the two variables in `~/.zprofile` to keep them.
- The links that `plateview` prints use the first name in `PLATEVIEW_NAMES`. Open such a link on the other device. It holds the access key.
- plateview refuses `PLATEVIEW_BIND=0.0.0.0`, because then every computer on the network could reach it.

## Comments, chat and the Oracle

The bar has **Comments** (⌘/Ctrl+\\), which shows the sidebar, and **Undo**, which takes back the last change to the comments.

- **Comment on words:** select words in the plate. Click **💬 Comment** (or push ⌘/Ctrl+Shift+M). A comment box opens under the words. ⌘/Ctrl+Enter saves. The plate marks the words in yellow.
- **Comment on a cell:** click a cell of a table or a grid, then **💬 Comment on cell**. The plate marks the cell with a small corner. The comment names the data file of the table, the row and the column.
- **Comment on the whole plate:** **+ Comment** in the sidebar.
- **In the sidebar:** a click on a comment shows its words or its cell in the plate. A click on marked words in the plate selects their comment. **Reply**, **Edit**, **Delete** and **Resolve** work as in the other viewers. **Resolve all** removes all the comments. If the words of a comment are not in the plate now, the comment shows **not found**.
- **Where they are:** in `~/.cache/plateview/plates/ID/comments.json`. They go when the plate goes. **Keep** keeps them too.
- **Chat:** the button at the bottom right (⌘/Ctrl+S, also when the plate has the focus). Claude runs in the base folder of the plate, so it reads the `CLAUDE.md` of your project. It can read the plate and its data files, change the HTML of the plate, and answer or add comments when you ask. The open tab shows a change at once. The selected words or cell go with your question. **Ask only** stops all changes. One Claude session for each plate. **New** starts a new conversation. The conversation is in the folder of the plate.
- **Oracle:** the button at the top of the sidebar. It starts Claude Code in the background (`claude -p "/answer-plate ID"`). Claude replies in each open thread. The run cannot edit files. One session for each plate: later runs continue it, and **New session** forgets it. One **Undo** takes back all the replies of a run.
- **The bridge:** the plate runs in a sandbox, so the frame cannot see into it. `runtime.js` tells the frame what you select, and paints the marks that the frame sends. The plate never changes the comments: only the buttons of the frame do. An exported plate has no comments.

## Notes to the app

⌘/Ctrl+Shift+. opens the dashboard of the notes (`viewkit/improvekit.py`), as in the other viewers. Write what is wrong with plateview, or what you want it to do. **Send all** sends the notes to the self-improving loop. Claude makes a proposal on a branch. Then a preview tab opens with **Accept**, **Change** and **Reject**.

- A note from a plate keeps a copy of the plate, its folders and its comments in `~/.cache/plateview/notes/ID/`. The preview shows this copy with your data, and the sample plate `examples/demo/demo.html`. The data files do not change: a plate only reads them.
- A preview server keeps its plates in `~/.cache/plateview/preview/plateview-N/`, so it never shows or deletes your plates.
- `plateview note TEXT` sends a note from the command line. `plateview try N` opens the proposal of note N in a sandbox on port 8900+N.

## The lifetime of a plate

| A plate stays while… | State in the bar |
|---|---|
| it is kept | kept |
| the Claude session that made it runs (the server checks the process and its start time) | kept while its Claude session runs |
| a tab shows it, and for 10 minutes after the last tab closes | kept while a tab shows it |
| it is new (the first 10 minutes) | new |

When none of these is true, plateview deletes the plate. A reload, a short sleep of the computer, or a break of a few minutes is safe. A plate that you make yourself, not from Claude, follows only the tab rule. `PLATEVIEW_GRACE` changes the 10 minutes (in seconds).

## Write a plate

A plate is HTML with a `<title>`, your layout, and a `<script>` that calls `pv`. plateview adds the fonts, `plate.css`, the components and `pv`. Do not load scripts, styles or fonts from the internet. The sandbox blocks them.

```html
<title>Exp. A results</title>
<div class="wrap">
  <header>
    <div class="eyebrow">my-project · Exp. A</div>
    <h1>Results of Exp. A</h1>
    <p class="lede">One sentence that tells what the plate shows.</p>
  </header>
  <div class="controls"><div class="ctl"><span class="ctl-label">Metric</span><div id="metric"></div></div></div>
  <section class="panel"><h2>MSE <span class="tag">table_runs.csv</span></h2><div id="t"></div></section>
</div>
<script>
pv.table('#t', 'output/table_runs.csv', {best: 'mse:min', sort: 'mse:asc'});
pv.seg('#metric', {options: [['mse', 'MSE'], ['lpe', 'LPE']], value: 'mse', onChange: v => { /* draw again */ }});
</script>
```

### The pv functions

| Function | What it does |
|---|---|
| `pv.table(el, source, opts)` | A table. `source` is a path or data. It draws again when the file changes. |
| `pv.grid(el, source, opts)` | A grid (pivot) of long data: `rows`, `columns`, `value` are necessary. |
| `pv.chart(el, source, opts)` | A chart: `type` (`line`, `scatter`, `bar`, `area`), `x`, `y`, `color`, `err`, labels and limits. |
| `pv.figure(el, path, {caption})` | An image: PNG, JPEG, GIF, WebP, SVG, or the first page of a PDF. A click opens it large. |
| `pv.data(path)` | A Promise of the data, `{columns, rows}`. |
| `pv.watch(path, fn)` | Calls `fn(data)` now, and again each time the file changes. |
| `pv.json(path)`, `pv.watchJSON(path, fn)` | Any JSON file as it is (a JSON Lines file as a list), once or live. |
| `pv.records(d)`, `pv.fromRecords(list)` | Data as a list of objects, and back. Use `pv.fromRecords` to give JSON to a table, a grid or a chart. |
| `pv.seg(el, {options, value, onChange})` | Segmented buttons. `options` is `[[value, label], …]`. |
| `pv.select(el, {options, value, onChange})` | A select. |
| `pv.need([paths])` | Loads files now, so that Export has them. |
| `pv.url(path)` | A Promise of a blob URL of an image. |
| `pv.fmt(x, dec)`, `pv.theme()`, `pv.toast(msg)` | A number as text, `light` or `dark`, a short message. |

- **`el`** is an element or a CSS selector.
- **Options:** the options of tables, grids and charts have the names of `pageview/SYNTAX.md`, for example `cols`, `labels`, `sd`, `best`, `sort`, `dec`, `row-groups`, `color`, `height`. Write a name with a hyphen in quotes: `{'y-label': 'MSE'}`.
- **More options:** `caption` and `title` (text), `wide` (true), and `prepare: d => d`, a function that changes the data first, for example a filter.

### The classes of plate.css

| Class | What it is |
|---|---|
| `.wrap` (`.wrap.narrow`) | The page column, 1240 px (860 px) wide |
| `header`, `.eyebrow`, `h1`, `.lede` | The head: a small label line in capitals, the title, one sentence |
| `.controls`, `.ctl`, `.ctl-label` | A row of controls with labels |
| `.cells` (`--n`: the columns) | A grid of setting buttons with `aria-pressed` |
| `.panel`, `.row2`, `.row3`, `.cols` | A box, two or three columns, a stack |
| `.kpis`, `.kpi` (`.l`, `.v`, `.s`) | Stat cards |
| `h2 .tag` (`.tag.good`, `.tag.bad`) | A small label after a title |
| `.note`, `footer` | Small notes |

## Security

- **The plate runs in a sandbox.** It has an opaque origin, no network (CSP `default-src 'none'`) and no pop-ups. It cannot reach the frame around it or the server. Thus it can show data only on your screen.
- **The plate reads files only through the frame.** The frame asks the server. The server gives only files in the allowed folders of the plate, and never hidden files.
- **The plate cannot change the comments or start Claude.** Its messages can only ask for a file, or report an error. They can also show a selection, a mark or a key in the frame. The frame checks each message.
- **Every request needs the access key** in `~/.cache/plateview/token-PORT` (readable only by you). The server also checks the host name of each request.
- **An exported file** has a CSP with no network too. It holds the data and the images that the plate loaded.
- **The print view** (`/p/ID/print`) is the exported plate in a tab of its own. It is a sandbox with an opaque origin and no network, as the plate is. Its CSP also lets it open the print dialog (`allow-modals`), and nothing more. It cannot reach the tab that opened it.

## Tests

```
python3 tests/test_ops.py                                   # plates, files, lifetime, export, comments, the preview copy, command line (port 8817)
uv run --with playwright python tests/browser_test.py       # the plate, Export, the PDF, the comments, the chat, the Oracle, the dashboard (port 8815)
```

Each test uses its own state folder in `tests/tmp/` and deletes it at the end. To keep it, set `PLATEVIEW_KEEP_TMP=1`.

## Limits

- **Export** and the PDF hold only the files that the plate loaded. A plate that loads a file only after a click must call `pv.need([...])` at the start, so that Export has it.
- **A table** shows at most 50 000 rows, and **a data file** of more than 20 MB does not show.
- **Links** in a plate do not open (no pop-ups). Write a path as text.

## License

MIT. See `LICENSE`.
