# pyview

Run Python cells and read the results in the browser. Built to work with Claude Code.

**Install:** use `install.sh` (see the section Install in the README of `viewkit`). It installs all the viewers and their Claude Code skills, and builds the macOS apps.

The `.py` file is the master copy. Cells are marked with `# %%`. Comments are lines that start
with `#>`, so Claude can read and answer them as plain text. The outputs (text, tables,
matplotlib figures) are not in the file. They are saved next to it, in `.pyview/`.

## Files

| File | Purpose |
|---|---|
| `samples/` | A sample file, the baseline for the previews of the self-improving loop: `cells.py` (markdown with maths, text output, long output, an error, an open and an answered comment, with the standard library only). `improve.json` lists it. |
| `pyview.py` | The `pyview` command and the local server. It parses cells and comments, edits the file, starts the runners, and saves outputs. Standard library only. |
| `runner.py` | The process that runs the cells of one file in one shared namespace. Standard library only. |
| `viewer.html`, `viewer.js` | The page. |
| `vendor/codemirror.js` | The editor (CodeMirror 6). Built from `vendor/codemirror-src/` with `build.sh` (needs Node, only for the build). |
| `vendor/mathjax/` | A local copy of MathJax, copied from mdview. It loads only when a markdown cell has maths. |
| `.venv/` | The shared interpreter that all sessions use (see Interpreter). Not in git. |
| `tests/` | `test_ops.py` (no server), `browser_test.py` (Playwright), `demo.py`. |
| `../viewkit/` | The code that pyview and mdview share (server life cycle, the Claude chat run, the chat panel with its pulse and hotkey, the stand-in `claude` for the tests). It must be in the folder next to `pyview`, as `~/tools/viewkit`. On a computer that runs `pyview --remote`, clone it there too. |
| `~/.local/bin/pyview` | Link to `pyview.py`, so `pyview` works from any folder. |
| `../viewkit/skills/answer-cells/` | `/answer-cells`: Claude answers the comments in a file. `install.sh` of viewkit links it into `~/.claude/skills/`. |

Requires Python 3.9+ for the command. The shared interpreter needs `uv` (or `python3 -m venv`).

## Set up

Use `install.sh` (see the section Install in the README of `viewkit`). To install by hand, put `pyview` and `viewkit` next to each other in `~/tools`. `pyview` stops with a message if `viewkit` is not there.

```
ln -s ~/tools/pyview/pyview.py ~/.local/bin/pyview
pyview --setup      # builds ~/tools/pyview/.venv with numpy, pandas, matplotlib, scipy, seaborn, sympy, scikit-learn
```

To add a package to the shared interpreter:
`uv pip install --python ~/tools/pyview/.venv/bin/python PACKAGE`.

## Open `.py` files by double-clicking in Finder

```
app/build.sh     # builds ~/Applications/pyview.app (run again to rebuild)
```

Then select a `.py` file in Finder, choose Get Info > Open with > pyview > Change All…

- The app has no window and no Dock icon. Its Finder icon is the tab icon of pyview.
- It gives the files to `~/.local/bin/pyview`, so that link must exist. If you open the app with no file, it opens your home folder.
- The builder is shared with the other viewers: `../viewkit/app/build.sh`.
- A double-click also lets the file run code, the same as `pyview FILE`.
- On a new Mac: clone `pyview` and `viewkit` into `~/tools`, link `pyview.py` to `~/.local/bin/pyview`, run `pyview --setup`, and run `app/build.sh`.

## Use

```
pyview notes.py                 open a file in your browser (this also allows it to run code)
pyview                          browse the current folder
pyview run notes.py --cell 3    run cell 3, wait, print its output (--all, --from N, --to N)
pyview out notes.py [--cell N]  print the saved output without running
pyview notes.py --list [--json] print the #> comment threads
pyview restart notes.py         stop the runner and clear all variables
pyview interrupt notes.py       stop the cell that runs now
pyview note 'text'              a note to pyview itself: a problem or a wish (a GitHub issue)
pyview try N [notes.py]         try the proposal of note N in a sandbox (copies of the files, port 8900+N)
pyview --status | --stop | --restart
```

`pyview run` exits with code 1 if a cell raises an error. Long output is cut to 20000
characters for each output. Use `--max-chars 0` to print all of it. A figure prints as the path of a PNG file.

One shared server runs in the background at `http://127.0.0.1:8766` (set `PYVIEW_PORT` to change it).
It serves files under your home folder (`PYVIEW_ROOT`). It restarts itself when `pyview.py`
or a `.py` file of `viewkit` changes. Its log is `~/.cache/pyview/server.log`.

### In the viewer

- **Run:** the ▶ button of a cell, or Shift+Enter. Header buttons: Run all, Interrupt, Restart.
  Cells share variables. Restart clears them.
- **Edit:** double-click a cell, or select it and press Enter. Shift+Enter saves and runs.
  ⌘/Ctrl+Enter runs and stays. **Esc saves and leaves the editor without running** (the cell then shows
  *edited since run*). The **Discard** button throws the changes away. Ctrl+Shift+- splits the cell at the cursor.
- **Cells:** the `+ Code` and `+ Markdown` buttons between cells add a cell. The 🗑 button deletes a cell. The ⋯ menu of a cell
  moves it or changes its type. Every file change can be undone with **Undo**.
- **Keys** (when no editor is open): ↑ ↓ select, Enter edit, Shift+Enter run and go on, `a` add above,
  `b` add below, `m` markdown, `y` code.
- **Markdown cells** support headings, lists, tables, code, and maths (`$x^2$`, `$$ … $$`).
- **Comment:** the **+** next to a code line, the 💬 button of a cell, or **+ comment** for the whole file.
  Reply, Edit, Delete and Resolve are in the sidebar. Resolve removes the lines and keeps the code.
  **Resolve all (N)** at the top of the sidebar removes every comment after you confirm; one Undo brings them back.
- **Comments** (header button, or ⌘/Ctrl+\\) hides and shows the sidebar. It shows again when you open a comment box.
- **Tab title and header:** the title is "NAME · CONTEXT", for example "analysis.py · thesis/code". The context is the Git repo of the file and the folder in it, or the parent folder outside a repo. When two open files have the same name and context, the context gets more folders. The header shows the same path. A click on the name copies the full path.
- **Host name:** the links use `pyview.localhost`, not `127.0.0.1`. Each viewer has its own host name, so Safari can show its tab icon. Old links with `127.0.0.1` still work.
- **Tab icon:** the browser tab shows the icon of the viewer: a yellow-green Py. An orange badge shows the number of open comments. A cyan dot shows that Claude works (chat or Oracle). A yellow dot shows that an answer waits for you.
- **Improve pyview** (⌘/Ctrl+Shift+.): the dashboard of the notes to the app. A note is a problem or a wish about pyview itself, not about the file.
  - **New note** (right): write the note and press Enter (or **Add**). The note waits in **Notes to send** (left), with the file that is open. A click on a waiting note edits it, and **×** deletes it. **For: All viewers / Only pyview** is the scope of the note.
  - **Send all** sends each waiting note. Each note becomes a GitHub issue with the label `note`. Claude implements the notes one after the other, in the background.
  - When the tests pass, a preview tab opens: the new version of pyview with copies of the file of the note and of the samples (`samples/`). The card at the top has **Accept** (merge, commit and push), **Change…** (tell Claude what to change) and **Reject** (close the note).
  - **Status** (below) shows each run. A question of Claude, or a failure, shows its text and a box: **Answer** (or **Try again**) starts the work again.
  - The protocol is in `viewkit/docs/self-improving-apps.md`. `VIEWKIT_AUTO_IMPROVE=0` turns the automatic loop off.
- **Chat** (bottom right, or ⌘/Ctrl+S): talk to Claude about the notebook. Claude runs on the computer that serves the page (the
  panel says which) and keeps one saved conversation per notebook, in `.pyview/NAME.py.chat.json`.
  - Select a cell, and the chip under the messages tells Claude which cell you mean (the × removes it). The ⋯ menu of a
    cell has **Ask Claude about this cell**.
  - Claude may read the notebook, edit it, and run cells with `pyview run`, so its changes show in the page at once.
    **Ask only** (a box in the panel) lets it read and answer only: it gets no edit tool and no permission to run cells.
  - After a turn that changed the file, a notice says so and has an **Undo** button. One Undo takes back the whole turn.
  - ⌘/Ctrl+S opens the chat and puts the cursor in the text box. Press it again in the text box to hide the chat. It works in
    a cell editor too.
  - When an answer ends and the chat is hidden, the **Chat** button pulses slowly until you open the chat. If the chat is
    open but the window has no focus, the panel pulses and the tab title gets a "●" until you return.
  - **Size:** drag the left edge, the top edge or the top-left corner of the panel to resize it. A double-click on one of them gives the default size back. The viewer keeps the size.
  - **Copy:** each code block in an answer has a **Copy** button at the top right. A click puts the code on the clipboard.
  - **Stop** ends an answer that is being written. **New** starts a new conversation (the old one is removed).
  - Only a trusted file can chat, because Claude can change it and run code. The Oracle and the chat do not run at the same time.
  - Each message starts a new `claude` process. The first word comes after about 2 to 3 seconds, and a turn costs about $0.07.
- **Oracle** starts `/answer-cells` for the file in the background. The button reads *working…* while it runs; click it to stop.
- **Contents** (left) lists the markdown headings and the titled code cells. A click goes there, the part you read is
  highlighted, and a number shows the open comments. Hide it with « or ⌘/Ctrl+Shift+\\.
- A cell shows **edited since run** when its code changed after its last run, and **previous session**
  (dimmed) when its output comes from a runner that no longer exists.

## The file format

```python
# %% [markdown]
# # A title
# Text with maths: $x^2$.

# %% Load data
#> why is this slow?
#> @claude: read_csv parses the dates here.
df = pd.read_csv("big.csv", parse_dates=["t"])
```

- A cell starts at `# %%` or `# %% Title`. A markdown cell starts at `# %% [markdown]`. Text
  before the first marker is cell 0 (it shows only if it holds code).
- A **comment** is a block of `#>` lines. Above a code line, it is about that line. Directly under
  a `# %%` line, it is about the cell. Before the first cell and followed by a blank line, it is general.
- A line `#> @name: text` starts a message. A line `#> text` starts the first message (by you),
  or continues the message above. A thread is *answered* when its last message is by `claude`.
- Comment lines are not part of the code that runs, and they do not change the cell.
- A line `# pyview: python=/path/to/python` in cell 0 picks the interpreter for this file.

## Outputs

Saved in `.pyview/NAME.py.json`, with figures as PNG files in `.pyview/NAME.py/`. The `.pyview/`
folder has its own `.gitignore`, so no repository file changes. No cell has an ID in the file.
After a file change, pyview matches saved outputs to cells like this:

1. A cell with the same code keeps its output.
2. An edited cell takes the output at its place in a diff of the old and new cells. It shows *edited since run*.
3. A new cell has no output.

## Interpreter

The runner uses the first of these that exists:

1. `PYVIEW_PYTHON`, an environment variable.
2. `--python PATH` on `pyview run`, or a `# pyview: python=PATH` line in the file.
3. The nearest `.venv/` in the folder of the file or a parent folder.
4. The shared `~/tools/pyview/.venv` (made by `pyview --setup`).

If none exists, `pyview` stops with a message. It never falls back to `python3` without telling you.
A runner starts on the first run. It stops on Restart, when the server stops, or after 4 hours
without a run (`PYVIEW_IDLE`, in seconds). Saved outputs stay.

## Use it from another computer (Tailscale)

Run the server on one computer (for example the mac mini) and open the page on another (the MacBook), with
no SSH tunnel. Both computers need Tailscale. On the computer that runs the server, add to `~/.zprofile`:

```
export PYVIEW_BIND=tailscale     # listen on the Tailscale address only (or give an address, e.g. 100.x.y.z)
export PYVIEW_NAMES=mac-mini     # the name that the other computer uses; it is also the name in the printed link
```

Then run `pyview ~/path/notebook.py --no-open` there. It prints a link such as
`http://mac-mini:8766/view?f=path/notebook.py&t=KEY`. Open that link in the browser of the other computer.
The code runs on the computer that runs the server, and so does Claude (the Oracle button).

- `PYVIEW_BIND` is the one address that the server listens on. `0.0.0.0` is refused, because every computer
  on the Wi-Fi could then reach a server that runs code, over plain HTTP. The Tailscale network encrypts the traffic.
- The server accepts only the `Host` names that it knows: the address, `127.0.0.1`, `localhost`, the name of the
  computer, and `PYVIEW_NAMES`. The access key is still needed for every request.
- After a fresh start of the server (not after a code change) the key changes. Run `pyview FILE --no-open` again to get a new link.
- The header shows `on <computer>`: the computer that serves the page, where the cells run and where Claude (Oracle) runs.
  If it names the computer that you sit at, the page is not the remote one.
- **One command from the other computer:** `pyview --remote mac-mini ~/path/notebook.py` starts the viewer on `mac-mini` over SSH
  (the file path is a path there) and opens the link here. If the server there listens on `127.0.0.1` only, it opens the SSH tunnel by itself.
  `pyview` must be on the `PATH` of a login shell there (put it in `~/.zprofile`). Use `--port 8767` if you also run pyview on this computer.
- Without Tailscale, use an SSH tunnel: `ssh -L 8767:127.0.0.1:8767 you@mini`, then
  `PYVIEW_PORT=8767 pyview FILE --no-open` on the mini, and open the printed link on the other computer.

## Security

A cell runs arbitrary code, so pyview is stricter than a viewer for documents:

- Only a file that you pass to `pyview` on the command line can run code. A file that you reach
  by browsing a folder opens read-only, with a **Trust this file** button.
- The server listens on `127.0.0.1` only (or on the one address in `PYVIEW_BIND`) and checks the `Host` header. Every request needs the access key in
  `~/.cache/pyview/token-8766` (readable only by you). The link that `pyview` opens carries it,
  and the browser keeps it as a cookie. Changes need a custom header, so other websites cannot post.
- Hidden files and folders are never read, listed, or saved.
- Output in a table (HTML) shows in a sandboxed frame that cannot run scripts.
- Claude's use of `pyview run` goes through Claude Code's own permission prompts.

## Tests

```
python3 tests/test_ops.py                 # the parser and the edit operations
python3 tests/network_test.py             # the server on the Tailscale address (needs Tailscale)
python3 tests/remote_test.py              # pyview --remote, with a stand-in ssh
python3 tests/chat_test.py                # the chat on the server, with a stand-in claude (viewkit/tests/fake_claude.py)
<python with playwright> tests/chat_browser_test.py   # the chat panel, in a real browser
uv venv /tmp/pw && uv pip install --python /tmp/pw/bin/python playwright
/tmp/pw/bin/python -m playwright install chromium
/tmp/pw/bin/python tests/browser_test.py  # the viewer, in a real browser
```

Each test makes its temporary folder in `tests/tmp/`, and Git ignores this folder. The test deletes its folder at the end, also when it fails. To keep the folder (and its screenshot) for debugging, set `PYVIEW_KEEP_TMP=1`.

## Known limits

- The chat starts a new `claude` process for each message. The system prompt of a conversation is the one of its first message (a resumed session keeps it), so the mode (Ask only or not) goes into each message.

- Python only. No widgets, no plotly, no `.ipynb` import or export.
- `input()` raises an error. Output from child processes is shown, but it can come a little out of order with `print` output.
- A comment on the first code line of a cell is a comment on the cell.
- A multi-line string that contains a line that starts with `#>` is read as a comment.

## License

MIT. See `LICENSE`. The files in `vendor/` have their own licenses: MathJax (Apache License 2.0, `vendor/mathjax/LICENSE`) and CodeMirror 6 with Lezer (MIT, `vendor/LICENSE-codemirror`).
