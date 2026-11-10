# mdview

Read and comment on Markdown files in the browser, then have Claude Code answer the comments.

**Install:** use `install.sh` (see the section Install in the README of `viewkit`). It installs all the viewers and their Claude Code skills, and builds the macOS apps.

The `.md` file is the master copy. Comments are stored inside it as
[CriticMarkup](https://github.com/CriticMarkup/CriticMarkup-toolkit), so they survive editing,
git and re-opening, and Claude can read them as plain text.

## Files

| File | Purpose |
|---|---|
| `samples/` | Sample documents, the baseline for the previews of the self-improving loop: `maths.md` (inline, displayed and aligned maths, maths in a comment), `review.md` (comments, a reply, suggested changes, a general comment), `structure.md` (lists, a table, code, a quote, links, footnotes). `improve.json` lists them. |
| `mdview.py` | The `mdview` command and the local server: renders the `.md` with pandoc and writes comments back into it |
| `viewer.html`, `viewer.js` | The page you read and comment on |
| `safe.lua` | pandoc filter that makes documents inert (see Security) |
| `vendor/mathjax/` | A trimmed local copy of MathJax 3.2.2, so maths works offline and nothing loads from the internet |
| `../viewkit/` | The code that mdview and pyview share (server life cycle, the Claude chat run, the chat panel with its pulse and hotkey, the stand-in `claude` for the tests). It must be in the folder next to `mdview`, as `~/tools/viewers/viewkit` |
| `tests/blocks_browser_test.py` | Browser test of the tables (card, sort, grid editor, Undo) and the blocks, with comments and suggested changes in them. Run: `uv run --with playwright python tests/blocks_browser_test.py` (port 8835). |
| `tests/chat_browser_test.py` | Browser test of the chat panel. Needs Playwright and pandoc. Run: `uv run --with playwright python tests/chat_browser_test.py`. It makes its temporary folder in `tests/tmp/` (Git ignores it) and deletes it at the end. Set `MDVIEW_KEEP_TMP=1` to keep it. |
| `~/.local/bin/mdview` | Link to `mdview.py`, so `mdview` works from any folder |
| `../viewkit/skills/answer-comments/` | `/answer-comments`: Claude answers the comments in a file, proposing changes as suggestions (`--direct` to edit directly). `install.sh` of viewkit links it into `~/.claude/skills/`. |

Requires Python 3.9+, pandoc, and the folder `viewkit` next to `mdview`. No other packages.

## Open `.md` files by double-clicking in Finder

```
app/build.sh     # builds ~/Applications/mdview.app (run again to rebuild)
```

To make mdview the default app of `.md` files, run `app/build.sh --default` (it needs `duti`: `brew install duti`). Or select a `.md` file in Finder, choose Get Info > Open with > mdview > Change All…

- The app has no window and no Dock icon. Its Finder icon is the tab icon of mdview.
- It gives the files to `~/.local/bin/mdview`, so that link must exist. If you open the app with no file, it opens your home folder.
- The builder is shared with the other viewers: `../viewkit/app/build.sh`.
- On a new Mac: clone `mdview` and `viewkit` into `~/tools`, link `mdview.py` to `~/.local/bin/mdview`, install pandoc, and run `app/build.sh`.

## Use

```
mdview notes.md          # open a file in your browser
mdview a.md b.md c.md    # open several, each in its own browser tab
mdview                   # browse the current folder
mdview notes.md --list   # print the comment threads (--json for JSON)
mdview notes.md --check-math  # formulas too wide for the page, or that don't typeset
mdview note 'text'       # a note to mdview itself: a problem or a wish (a GitHub issue)
mdview try N [notes.md]  # try the proposal of note N in a sandbox (copies of the files, port 8900+N)
mdview --status          # is the server running?
mdview --stop            # stop it (--restart to restart)
```

One shared server runs in the background at `http://127.0.0.1:8765`. It opens only the files and
folders you have passed to `mdview` since it started: passing a folder lets you browse it and open
everything in it. The first `mdview` starts it; later calls reuse it and return immediately. It keeps
running after you close the terminal or Claude session, and restarts itself when `mdview.py`
or a `.py` file of `viewkit` changes (a change with a syntax error is ignored). Its log is `~/.cache/mdview/server.log`.

To open files outside your home folder, restart it with a wider root: `MDVIEW_ROOT=/ mdview --restart`.
If port 8765 is taken, set `MDVIEW_PORT`.

- **Comment:** select text, then click the 💬 Comment button that appears (or press ⌘/Ctrl+Shift+M).
- **General comment:** click **+ comment** at the top of the sidebar, for notes about the
  whole document. They're listed first in the sidebar, under General.
- **Reply, resolve:** hover over a message in the sidebar and click its ⋯ for Reply, Resolve, Edit and Delete. Resolving removes
  the comment from the file and keeps the text. Resolve and delete can be undone from the
  notification that appears.
- **Live:** the page refreshes on its own when the file changes, for example when Claude edits it.
- **Edit or delete a block:** hover a paragraph, heading, list, quote, table or code block. A ✎ in the right margin (or a double-click on the block) opens its Markdown in a text box (⌘/Ctrl+Enter saves, Esc cancels); a × deletes it. Both can be undone with **Undo** in the header.
- **Tables:** each Markdown table shows in a card. The toolbar of the card gives the size of the table, **Edit**, and copies of the table as **MD**, **CSV** or **LaTeX**. Columns of numbers are aligned to the right, with digits of one width. Move the pointer on a column name and click **↕** to sort the rows: up, down, then the order of the file again. The sort changes only the view, never the file. Comments and suggested changes in a cell work as in the text.
  - **Grid editor:** double-click a cell (or click **Edit**, or ✎) to edit the table in a grid. Each cell holds its Markdown as in the file, also CriticMarkup. **Tab** goes to the next cell, **Enter** to the next row, and both add a row at the end. The buttons above a column change its alignment (– ⇤ ↔ ⇥), move it (◀ ▶), add a column after it (+), or delete it (×). The buttons at the left of a row move it (↑ ↓). They also add a row below it (+), or delete it (×). **Save** (or ⌘/Ctrl+Enter) writes a pipe table with aligned columns in place of the old table. **Undo** takes it back. **Markdown…** shows the text of the table, and **Grid** goes back to the grid.
  - A table that is not a plain pipe table (for example in a quote, or with a row that has more cells than the header) opens as Markdown text.
  - **Best values:** put the table in a block `::: {.table best="Accuracy:max Loss:min"}` … `:::` to mark the best value of these columns.
- **Blocks:** mdview shows the blocks of pageview (`~/tools/viewers/pageview/SYNTAX.md`): callouts, stat cards, columns, tabs, toggles, figures, code with **Copy**, and tables and charts that read a CSV, TSV or JSON file (`::: {.table src="runs.csv"}`, `::: {.chart src="runs.csv" …}`). A data file must be next to the document or below its folder, or in a folder that you passed to mdview. The words of a block stay the words of the file, so comments, suggested changes and block editing work in blocks too. Widgets show as code. The sample `samples/blocks.md` shows all of them.
- **Fonts:** **Aa** in the header chooses the font of the text (Sans, Serif or Mono) and of the headings (Serif, or as the text). It also chooses the font of the interface (Sans or Mono) and the size of the text. The default is the look of pageview: sans text, serif headings and a sans interface. The choice is for all files, and the browser keeps it. Each file keeps its own color.
- **Hide comments:** **Comments** in the header (or ⌘/Ctrl+\\) hides the sidebar for plain reading;
  clicking a highlight brings it back. The choice is remembered.
- **Contents:** the headings are listed on the left (for files with two or more). Click one to
  jump to it, unfolding it if needed; the section you're reading is highlighted, and a yellow
  number marks open comments in a section's own text. Hide it with **«** at its top, **Contents**
  in the header or ⌘/Ctrl+Shift+\\; bring it back with the **☰** tab at the left edge (or the same
  button or keys). The choice is remembered. It's hidden on narrow windows.
- **Fold sections:** click the ▾ in the margin beside a heading to hide its section, subsections
  included (Alt-click folds or unfolds its subsections too). A folded heading shows how many
  comments it hides. **Fold all** in the header leaves only the outline. Clicking a comment in the
  sidebar, or a link to a heading, unfolds what's in the way. Folds are remembered per file;
  the file itself is never changed.
- **Suggested changes:** Claude proposes edits as tracked changes: deleted text struck through in
  red, new text underlined in green. They're listed under **Suggested changes** in the sidebar;
  hover over one to **Accept** or **Reject** it, or use **Accept all** / **Reject all**. Both
  can be undone from the notification. Clicking a change in the text finds it in the sidebar.
  Text with a pending change can't take a new comment until it's accepted or rejected.
- **Links to other files** work when they're relative to the document (put paths with spaces in
  angle brackets: `[main.tex](<../Paper R1/main.tex>)`):
  - Markdown files open in the viewer, at `#heading-id` if given.
  - LaTeX, code and other text files (`.tex`, `.bib`, `.py`, `.json`, ...) open read-only with
    line numbers in a new tab; `#L12` or `#L12-L20` highlights those lines.
  - Images, audio, video and PDFs open from `/files/` in a new tab, keeping `#page=N` for PDFs.
  - Links in comments work the same way. Comments also show `code`, **bold** and *italic*.
- **📁** in the header browses the file's folder.
- **Oracle:** click **Oracle** at the top of the sidebar. It starts Claude Code in the
  background (`claude -p "/answer-comments FILE"`, run from the file's folder), which proposes
  changes as suggestions (below) and replies in each open thread. Its replies show up in the
  sidebar, and answered comments turn green. While it works, the button reads **Working… ■**; click it to stop. When it finishes, **Report** in the notification shows Claude's
  summary.
  - **One session per file.** The first click starts a session; later clicks continue it, so
    Claude remembers earlier rounds on that file. **New session** under the sidebar heading
    forgets them. The session also knows the `CLAUDE.md` files that apply to the file.
  - Only **one run per file** at a time: the run holds a lock (`~/.cache/mdview/claude/`) until it
    exits, so further clicks, other tabs or a server restart can't start a second one.
  - The run may only read, edit, search, run `mdview`/`grep` and search or fetch from the web (to
    find sources for claims from outside your files), and doesn't ask before editing.
    It uses your Claude plan like any other session. `MDVIEW_CLAUDE` sets the path to `claude`.
  - **Ask only** (checkbox in the comment box and in the reply box) puts `[ask only]` in front of
    that message. Oracle then answers it in a reply to the comment and suggests or edits nothing.
    The thread shows an **Ask only** label.
  - You can also run `/answer-comments notes.md` yourself in Claude Code.
- **Tab title and header:** the title is "NAME · CONTEXT", for example "report.md · thesis/ch2". The context is the Git repo of the file and the folder in it, or the parent folder outside a repo. When two open files have the same name and context, the context gets more folders. The header shows the same path. A click on the name copies the full path.
- **Host name:** the links use `mdview.localhost`, not `127.0.0.1`. Each viewer has its own host name, so Safari can show its tab icon. Old links with `127.0.0.1` still work.
- **Tab icon:** the browser tab shows the icon of the viewer: a blue M↓. An orange badge shows the number of open comments. A cyan dot shows that Claude works (chat or Oracle). A yellow dot shows that an answer waits for you.
- **Improve mdview** (⌘/Ctrl+Shift+.): the dashboard of the notes to the app. A note is a problem or a wish about mdview itself, not about the document.
  - **New note** (right): write the note and press Enter (or **Add**). The note waits in **Notes to send** (left), with the document that is open. A click on a waiting note edits it, and **×** deletes it. **For: All viewers / Only mdview** is the scope of the note.
  - **Send all** sends each waiting note. Each note becomes a GitHub issue with the label `note`. Claude implements the notes one after the other, in the background.
  - When the tests pass, a preview tab opens: the new version of mdview with copies of the document of the note and of the samples (`samples/`). The card at the top has **Accept** (merge, commit and push), **Change…** (tell Claude what to change) and **Reject** (close the note).
  - **Status** (below) shows each run. A question of Claude, or a failure, shows its text and a box: **Answer** (or **Try again**) starts the work again.
  - The protocol is in `viewkit/docs/self-improving-apps.md`. `VIEWKIT_AUTO_IMPROVE=0` turns the automatic loop off.
- **Chat:** the **Chat** button at the bottom right (or ⌘/Ctrl+S) opens a panel where you ask Claude about the
  document and ask it to change the text. Each message runs `claude -p` in the file's folder, in
  one session per file (kept in `~/.cache/mdview/chat/`), and the answer appears as it is written.
  Select text in the document first and the passage goes with your message.
  - **Ask only** (checkbox) lets Claude read, search and answer, but not edit. Without it, Claude
    edits the file directly when you ask for a change, and a notification offers **Undo**, which
    takes back everything that message changed. Ask for a "suggestion" to get tracked changes.
  - ⌘/Ctrl+S opens the chat and puts the cursor in the text box. Press it again in the text box to hide the chat.
  - When an answer ends and the chat is hidden, the **Chat** button pulses slowly until you open the chat.
    If the chat is open but the window has no focus, the panel pulses and the tab title gets a "●" until you return.
  - **Size:** drag the left edge, the top edge or the top-left corner of the panel to resize it. A double-click on one of them gives the default size back. The viewer keeps the size.
  - **Copy:** each code block in an answer has a **Copy** button at the top right. A click puts the code on the clipboard.
  - **Stop** ends an answer, **New** starts a new conversation. Chat and Oracle never run on the
    same file together.
  - Edit mode uses the same tools as Oracle (read, edit, `mdview`, `grep`, web search and fetch).

## Comment format

```
{==highlighted text==}{>>your comment<<}{>>@claude: Claude's reply<<}
A sentence with a note attached to a point{>>like this<<} in it.

{>>A general comment: any comment alone on its own line.<<}
```

New general comments are added at the end of the file.

Suggested changes use CriticMarkup too:

```
{~~old text~>new text~~}   {++inserted text++}   {--deleted text--}
```

A change inside a highlight (`{=={~~old~>new~~}==}{>>…<<}`) belongs to that comment.

Comments can contain maths, which the sidebar renders with MathJax: `$…$` or `\(…\)` inline,
`$$…$$` or `\[…\]` displayed. As in pandoc, `$` only opens maths when the next character isn't a
space, and only closes it when no digit follows, so "costs $5 and $10" stays text; write `\$` for a
literal dollar sign. Editing a message shows its TeX.

You can type these by hand in any editor too. VS Code and Obsidian have CriticMarkup plugins.

## Other source formats

Convert a file to Markdown once, then work on the Markdown:

```
pandoc report.docx -t commonmark_x --extract-media=media -o report.md
```

## Limits

- A comment covers text within one paragraph, heading, list item or table cell.
- Comments can't go inside code blocks or maths.
- Comments are stored on a single line, so line breaks you type are turned into spaces.
- Raw HTML in a document (`<details>`, `<kbd>`, …) is shown as text, not rendered.
- Links to folders, and to files other than Markdown, text, images, audio, video and PDFs, don't open.
- A Markdown file you open by typing its address (not by following a link from an open document)
  shows a message with the command to open it, unless you've passed it or its folder to `mdview`.
- Files in hidden folders (`~/.claude`, `~/.ssh`, …) can't be opened.
- MathJax's right-click menu is turned off.
- Displayed maths wider than the text column scrolls sideways. `mdview FILE --check-math`
  (needs pdflatex) lists formulas wider than 28 em, the safe width, and ones that don't typeset.
- The server only accepts connections from this computer (127.0.0.1).

## Security

mdview can read your home folder and edit Markdown in it, so it guards against documents and
programs you don't trust:

- **Documents are inert.** `safe.lua` shows raw HTML as text, drops event-handler attributes
  (`{onclick=…}`) and keeps only `http(s)`, `mailto`, relative and `data:image` links. The viewer's
  Content-Security-Policy is a second line of defence: only `viewer.js` and the local MathJax may
  run, and the page may only talk to the mdview server (images and media may come from the web).
- **Access key.** Each fresh server start writes a random key to `~/.cache/mdview/token-PORT`,
  readable only by you. The `mdview` command puts it in the link it opens; the browser keeps it as
  a cookie, and every request without it is refused. Other user accounts and sandboxed programs
  can't read the key, so they can't use the server. A tab left open from before a restart shows
  "access key missing": reopen the file with `mdview`.
- **Only what you pass it.** The server opens only the files and folders you have given to the
  `mdview` command, plus the images, media, Markdown and text files those documents (and their
  comments) link to. The list is kept in
  `~/.cache/mdview/grants-PORT.json`, survives code-change restarts and is cleared on a fresh start
  (`--stop`, `--restart`, logging out).
- **Restricted files.** Hidden files and folders are never read, listed or saved, even through a
  link. `/files/` serves only images, audio, video and PDFs (SVGs in a sandbox, since they can carry
  scripts).
- **Browser headers.** Responses can't be framed or embedded by other websites.

## License

MIT. See `LICENSE`. The files in `vendor/mathjax/` are MathJax (Apache License 2.0, `vendor/mathjax/LICENSE`).
