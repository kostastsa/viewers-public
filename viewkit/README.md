# viewkit

The code that mdview, pyview, pdfview, pageview and plateview share. It is not an app. It has no command of its own.

## Install

Install all the viewers and their Claude Code skills with two commands. The public repository `viewers` has all the viewers and `viewkit` in one repository:

```
git clone https://github.com/kostastsa/viewers ~/tools/viewers
~/tools/viewers/install.sh
```


`install.sh` does these steps. You can run it again, and it changes only what is not correct:

1. In the repository `viewers`, it links `~/tools/APP` to each folder, because the skills use these paths. A folder `~/tools/APP` that is not a link stays, and the script uses it. With separate repositories, it clones `mdview`, `pyview`, `pdfview`, `pageview` and `plateview` into `~/tools`, next to `viewkit`.
2. It links each command into `~/.local/bin`.
3. It links the skills of `skills/` into `~/.claude/skills/` (or `$CLAUDE_CONFIG_DIR/skills/`).
4. It builds the macOS apps that open files from Finder.
5. It builds the shared Python interpreter of pyview (`pyview --setup`).
6. It tells you which external tools it cannot find: pandoc (mdview, pageview), poppler (pdfview), `claude` (the chat and the Oracle button) and `gh` (optional, for notes).

Options: `--update` (also pull the new commits), `--no-apps`, `--no-venv`, `--config-dir DIR` (a different Claude Code configuration folder, more than one is possible), and `--maintainer` (also the `improve` skill). Run `install.sh --help` for the details.

With separate repositories, the folders must be in `~/tools`, because the skills use these paths. The clone of `viewers` can be in a different place. Each app adds `../viewkit` to its import path and stops with a clear message if the folder is missing. Python 3.9 or newer. Standard library only.

## Files

| File | Purpose |
|---|---|
| `serverkit.py` | The background server of an app: access key, grants file, start, stop, ping, and the restart when the app or the kit changes. It also serves the page files of `web/` at `/kit/NAME`, and the files of `web/vendor/` at `/kit/vendor/PATH` (`kit_asset`). It has no rules about who may read what. `app_host(name)` and `local_hosts(name)` give the host name of an app (`NAME.localhost`) and the accepted host names. `doc_label(p, others)` gives the short label of a document for the tab and the header. `Net(name, prefix)` gives the network address of a server: 127.0.0.1, or the Tailscale address with `PREFIX_BIND=tailscale` (and the names in `PREFIX_NAMES`), the host in the links, and the accepted host names. pyview and plateview use it. |
| `datakit.py` | Data files for the viewers that show data (pageview, plateview): `read_table` reads CSV, TSV, JSON and JSON Lines as columns and rows, and `pdf_figure` makes a PNG of the first page of a PDF figure. |
| `claudekit.py` | The chat run: the environment, the command line, the process, and the stream-json lines of `claude -p`. The app keeps its own system prompt, tool lists, saved conversation, and the way it tells the page about news. `Oracle` is the background run that answers the comments of a document with a skill: a lock per document, one session per document, and a report at the end. mdview and pdfview use it. pyview still has its own simple version. |
| `improvekit.py` | The notes of the self-improving apps (`docs/self-improving-apps.md`). `add(app, app_dir, text, source, context)` makes a GitHub issue with the label `note` with `gh`, and adds the commit of the app. Without the network, the note waits in `~/.cache/viewkit/notes-queue.jsonl` and goes with the next note. A note goes to GitHub only when the `gh` user may push to the repository (the owner, or a fork). In a clone of the public repository of another person, the note stays in the queue. Thus the app does not make a public issue. `clean_context` makes the context from a page safe. `dash_api` is the route of the dashboard: the waiting notes (`~/.cache/viewkit/improve/APP-drafts.json`), Send all, and the list of the runs. `note_main` is the `APP note` subcommand. `try_main` is the `APP try N [FILE]` subcommand: the sandbox of a proposal, with the server of its worktree (`~/tools/wt/note-APP-N/APP`) on port 8900+N and copies of the files (`VIEWKIT_WT` changes the folder for the tests). pdfview uses it. |
| `improverun.py` | The background runs of the automatic loop: implement a note (a worktree, `claude -p "/improve implement"`, the tests, the push, the preview tab), change it from the feedback of the preview, accept it (merge, push, close the issue) or reject it. `improvekit.spawn` starts it. Its status and log are in `~/.cache/viewkit/improve/`. An app joins with `improve.json` (the tests, the port variable, the companion files, and the samples: representative documents in the repository that every preview opens, next to the document of the note). |
| `improve.json` | The test commands of all the viewers. The automatic loop runs them when a proposal changes the kit, in worktrees of the viewers next to the worktree of the kit. |
| `web/kit.js` | One global, `kit`. `kit.chatPanel(opts)` is the chat panel: the messages, Send and Stop, New, Ask only, the context chip, polling or the event stream, the resize grips (the size goes into `--kit-chat-w` and `--kit-chat-h` on `<html>`, and the app uses them in its CSS), the text size of the chat, and a **Copy** button on each code block of an answer. For the text size, put the focus in the panel (or click in it) and push ⌘/Ctrl+= (larger), ⌘/Ctrl+− (smaller) or ⌘/Ctrl+0 (the default). A pinch or Ctrl+wheel on the panel also changes the size, and does not zoom the document. The size goes into `--kit-chat-zoom` on `<html>`, and `kit.css` uses it as the CSS `zoom` of `chatMsgs`, `chatCtx` and `chatText`. The panel keeps its size. The browser keeps the text size in `localStorage` (`KEY.chatZoom`). The app gives the requests, the context, the answer renderer, and what to do when Claude changes the file (see the comment above `chatPanel`). `kit.chatAttention(...)` makes the Chat button pulse when an answer ends, puts a dot in the tab title, and sets the hotkey ⌘/Ctrl+S. `chatPanel` uses it. `kit.title(base)` keeps the dot when the app sets the title. `kit.tab({app, open, oracle})` draws the tab icon of the app (a blue M↓, a yellow-green Py, a red page, a teal square with bars). A badge shows the open comments, and a dot shows that Claude works or that an answer waits. The chat panel sets its own parts. `kit.docTitle(app, label)` sets the title (the name and the context), and `kit.crumbs(el, label)` shows the path in the header. `kit.copyText(text)` copies text to the clipboard. `kit.improveDash({app, get, post, context, onDone})` is the dashboard of the self-improving apps: ⌘/Ctrl+Shift+. opens it. New notes wait in a list (with the context of the app and the last buttons that the user clicked) until **Send all**, and a status list follows the runs (`improvekit.dash_api`). `kit.previewBand({get, post})` is the card of a preview tab, with Accept, Change… and Reject. `kit.md(text)` makes a little safe Markdown (plain text only) for answers and comments, and `kit.typeset(roots)` typesets its maths with the MathJax copy of the kit. pdfview uses both. |
| `web/components.js`, `web/components.css` | The components of the viewers that show data: tables, grids (pivot), charts, figures, tabs, toggles, columns, callouts, stat cards, code with Copy, and widgets. `PV.enhance` makes them from the blocks of a pageview page, and `PV.make` makes one from an options object (plateview). The app defines the colour and font tokens. |
| `web/kit.css` | The pulse styles, the resize grips, the text size and the **Copy** buttons of the chat panel, the dashboard and the preview card. An app can change `--kit-shadow-btn` and `--kit-shadow-box`. |
| `app/build.sh` | Builds `~/Applications/NAME.app` for a viewer: a small macOS app that opens the files that you double-click in Finder with the command NAME. Each viewer calls it from its own `app/build.sh` with its file types. |
| `web/icons/` | The icons as PNG files. `APP-1024.png` is the flat drawing of `kit.js`, and the app builder makes the Finder icon from it (`tests/make_icons.py` makes it again). `APP-app.png` (256 px) and `APP.png` (64 px) are the app icons as macOS shows them, with its rounded shape and glass effect. The exception is pdfview: its `pdf-app.png` and `pdf.png` are the flat red page (`FLAT` in `tests/app_icons.py`). The tab icon and the folder pages use them, so the tab and the app have the same icon. After a change of an icon, run `tests/make_icons.py`, then `app/build.sh` of the viewer, then `python3 tests/app_icons.py`. |
| `web/vendor/fonts/` | IBM Plex Sans, Sans Condensed and Mono (WOFF2, SIL Open Font License, `LICENSE.txt`). plateview puts them in each plate. |
| `web/vendor/mathjax/` | A local copy of MathJax 3.2.2, the same as in mdview. pdfview loads it from the kit. mdview and pyview still use their own copies. |
| `tests/improve_test.py` | The test of the automatic loop for one app (`pdfview`, `mdview` or `pyview`): the dashboard, a question and its answer, a failed test and Try again, a change in the kit, the preview with the samples, Change, Accept of two repositories, and Reject. It uses copies of the app and of viewkit, a stand-in `gh` and a stand-in `claude`. It never changes the real repositories. |
| `tests/fake_gh.py` | A stand-in for `gh` in the tests of the notes. It writes each call to `FAKE_GH_LOG`. With `FAKE_GH_FAIL=1` it fails like `gh` without a network. With `FAKE_GH_NOPUSH=1` the user may not push to the repository. Set `VIEWKIT_GH` to its path, and `VIEWKIT_NOTES_QUEUE` to a test file. |
| `tests/notes_test.py` | The test of the push check of the notes, with the stand-in `gh`. No network and no server. |
| `install.sh` | The installer of all the viewers and their skills (see Install). |
| `skills/` | The Claude Code skills of the viewers. `install.sh` links them into the skills folder of Claude Code. `answer-comments` (mdview), `answer-cells` (pyview), `answer-pdf` (pdfview) and `answer-page` (pageview) answer the comments of a document. These are the skills of the Oracle button. `page` and `plate` tell Claude how to write a pageview page and a plate. `improve` is the loop of the self-improving apps, for the owner of the repositories (`install.sh --maintainer`). |
| `tests/fake_claude.py` | A stand-in for `claude` that prints the same stream-json. The apps use it in their chat tests. For a PDF, EDIT adds a comment with `pdfview add`. For `/improve implement` and `/improve change`, it adds a line to `README.md` and commits it. |

## Rules

- The kit holds mechanics only. Trust, grants, document formats, and the way the page shows a document stay in the apps.
- A change in a `.py` file of the kit restarts the running servers of all the apps. Check the syntax first: a file that does not compile does not restart them.
- Run the chat tests of all the apps after each change: `tests/chat_browser_test.py` in `mdview` and in `pyview`, `tests/chat_test.py` in `pyview`, `tests/browser_test.py` in `pdfview`, `pageview` and `plateview`, `tests/improve_test.py pdfview|mdview|pyview` in `viewkit`, and `tests/network_test.py` in `pyview` after a change of `Net`.
- A computer that runs `pyview --remote` needs `viewkit` next to `pyview` there too.

## Tests

The tests of the apps cover the kit. The kit has one test of its own, `tests/improve_test.py`. It tests the automatic loop of the notes for one app, on copies of the app and of the kit. Run all of them after a change:

```
cd ~/tools/pyview && python3 tests/chat_test.py                                    # the chat on the server
cd ~/tools/pyview && uv run --with playwright python tests/chat_browser_test.py    # the chat panel, real browser
cd ~/tools/mdview && uv run --with playwright python tests/chat_browser_test.py    # the chat panel, real browser
cd ~/tools/pdfview && uv run --with playwright python tests/browser_test.py        # the viewer and its chat
cd ~/tools/pdfview && uv run --with playwright python tests/samples_test.py        # the sample PDFs
cd ~/tools/viewkit && for a in pdfview mdview pyview; do uv run --with playwright python tests/improve_test.py $a; done   # the loop
cd ~/tools/viewkit && python3 tests/notes_test.py                                  # the push check of the notes
cd ~/tools/pageview && python3 tests/test_ops.py                                   # pageview without a server
cd ~/tools/pageview && uv run --with playwright python tests/browser_test.py       # the viewer and its chat
cd ~/tools/pyview && python3 tests/network_test.py                                 # pyview on the Tailscale address
cd ~/tools/plateview && python3 tests/test_ops.py                                  # plateview without a server
cd ~/tools/plateview && uv run --with playwright python tests/browser_test.py      # plates in a real browser
```

## Not shared yet

- **Chat markup and styles.** Each app keeps the HTML and the CSS of the chat panel, because the apps look different. The panel finds its elements by their ids (`chatOpen`, `chatBox`, `chatNew`, `chatMin`, `chatMsgs`, `chatCtx`, `chatAsk`, `chatText`, `chatSend`).
- **The answer renderer.** mdview gives `chatMd`, and pyview gives `mdRender`. pdfview uses `kit.md`.
- **MathJax.** mdview and pyview still use their own copies, not the copy in `web/vendor/`.
- **The comment sidebar.** The two `renderThreads` functions are too different. The plan is to share only the thread card.

## License

MIT. See `LICENSE`. The files in `web/vendor/` have their own licenses: MathJax (Apache License 2.0, `web/vendor/mathjax/LICENSE`) and IBM Plex (SIL Open Font License 1.1, `web/vendor/fonts/LICENSE.txt`).
