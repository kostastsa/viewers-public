# plateview

Show **plates** in the browser. A plate is an HTML page that Claude writes, like an Artifact of claude.ai. It shows tables, grids, charts and figures of your data files. The plate does not hold a copy of the data. It reads the files live, so a plate changes when an experiment writes its files again.

**Install:** use `install.sh` (see the section Install in the README of `viewkit`). It installs all the viewers and their Claude Code skills, and builds the macOS apps.

Plates are temporary. A plate stays while the Claude session that made it runs, or while a tab shows it. Then plateview deletes it. **Keep** makes a plate permanent, and **Export** saves one HTML file with the data inside.

plateview has no comments and no chat.

## Files

| File | Purpose |
|---|---|
| `plateview.py` | The `plateview` command and the local server. Standard library only. |
| `shell.html`, `shell.js` | The frame around a plate: the bar (title, state, theme, Keep, Export, Delete) and the sandboxed frame. At `/`, the list of plates. |
| `runtime.js` | The `pv` library inside a plate. |
| `plate.css` | The default stylesheet of a plate. |
| `examples/demo/` | An example plate (`demo.html`) and its files. The numbers are invented. |
| `tests/` | `test_ops.py` (no server) and `browser_test.py` (Playwright). |
| `app/build.sh` | Builds `~/Applications/plateview.app`, so that Finder can open `.html` files with plateview (Open With). |
| `../viewkit/` | The code that the viewers share: the server life cycle and the network address (`serverkit.py`), the data reader (`datakit.py`), the components (`web/components.js`, `web/components.css`), and the fonts (`web/vendor/fonts/`, IBM Plex). It must be next to `plateview`, as `~/tools/viewkit`. |
| `~/.local/bin/plateview` | A link to `plateview.py`, so that `plateview` works from any folder. |
| `../viewkit/skills/plate/` | `/plate`: the skill that tells Claude how to write a plate. `install.sh` of viewkit links it into `~/.claude/skills/`. |

Requires Python 3.9 or newer, and the folder `viewkit` next to `plateview`. A PDF figure needs `pdftoppm` (from poppler, `brew install poppler`). Without it, plateview uses `sips` of macOS.

## Installation

Use `install.sh` (see the section Install in the README of `viewkit`). To install by hand, put `plateview` and `viewkit` next to each other in `~/tools`, and link the command:

```
ln -s ~/tools/plateview/plateview.py ~/.local/bin/plateview
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
plateview                                             the list of plates in the browser
plateview --status | --stop | --restart
```

- **Allowed folders:** give `--allow` one time for each folder (or file) that the plate may read. Without `--allow`, the plate may read the folder of its HTML file (or the current folder, for stdin).
- **Paths in a plate:** a full path, a path with `~`, or a path relative to the folder of the HTML file (`--base` sets another folder).
- **Never:** a plate never reads a hidden file or folder, or a file outside its allowed folders.

One shared server runs in the background at `http://127.0.0.1:8769` (`PLATEVIEW_PORT`). It restarts itself when `plateview.py` or a `.py` file of `viewkit` changes. Its log is `~/.cache/plateview/server.log`. The plates are in `~/.cache/plateview/plates/`.

### Your other devices (Tailscale)

```
PLATEVIEW_BIND=tailscale PLATEVIEW_NAMES=my-mac plateview --restart
```

- The server then listens on the Tailscale address of this Mac. Put the two variables in `~/.zprofile` to keep them.
- The links that `plateview` prints use the first name in `PLATEVIEW_NAMES`. Open such a link on the other device. It holds the access key.
- plateview refuses `PLATEVIEW_BIND=0.0.0.0`, because then every computer on the network could reach it.

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
- **Every request needs the access key** in `~/.cache/plateview/token-PORT` (readable only by you). The server also checks the host name of each request.
- **An exported file** has a CSP with no network too. It holds the data and the images that the plate loaded.

## Tests

```
python3 tests/test_ops.py                                   # plates, files, lifetime, export, command line
uv run --with playwright python tests/browser_test.py       # the plate in a real browser (port 8815)
```

Each test uses its own state folder in `tests/tmp/` and deletes it at the end. To keep it, set `PLATEVIEW_KEEP_TMP=1`.

## Limits

- **Export** holds only the files that the plate loaded. A plate that loads a file only after a click must call `pv.need([...])` at the start, so that Export has it.
- **A table** shows at most 50 000 rows, and **a data file** of more than 20 MB does not show.
- **Links** in a plate do not open (no pop-ups). Write a path as text.

## License

MIT. See `LICENSE`.
