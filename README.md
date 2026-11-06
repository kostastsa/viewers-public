# viewers

Local viewers for documents, code and data, made to work with [Claude Code](https://claude.com/claude-code). Each viewer opens a file in the browser. You read the file and comment on it, and Claude answers the comments or the chat.

| Folder | What it opens | The comments |
|---|---|---|
| [`mdview`](mdview) | Markdown files (`.md`) | In the file, as CriticMarkup. Claude proposes changes as suggestions that you accept or reject. |
| [`pyview`](pyview) | Python cell files (`.py` with `# %%` cells). It runs the cells and shows the outputs. | Lines `#>` in the file. Claude edits the cells and runs them. |
| [`pdfview`](pdfview) | PDF files. The PDF does not change. | A JSON file next to the PDF. Claude replies in each thread. |
| [`pageview`](pageview) | Rich pages: Markdown with tables, charts and figures from CSV or JSON files | A JSON file next to the page. |
| [`plateview`](plateview) | Plates: HTML pages that Claude writes, which show live data files | No comments. |
| [`viewkit`](viewkit) | The code that the viewers share, the Claude Code skills, and the installer | |

Each viewer runs a small local server and opens a page in your browser. The viewers use the Python standard library only.

## Install

The viewers are made and tested on macOS. You need git and Python 3.9 or newer.

```
git clone https://github.com/kostastsa/viewers ~/tools/viewers
~/tools/viewers/install.sh
```

The script does these steps:

1. It links `~/tools/APP` to each folder, because the skills use these paths.
2. It links the commands `mdview`, `pyview`, `pdfview`, `pageview` and `plateview` into `~/.local/bin`.
3. It links the Claude Code skills into `~/.claude/skills/`.
4. It builds the macOS apps that open files from Finder.
5. It builds the shared Python interpreter of pyview (numpy, pandas, matplotlib and other packages).
6. It tells you which external tools it cannot find: pandoc (mdview, pageview), poppler (pdfview), Claude Code (the chat and the Oracle button), and `gh` (optional).

You can run it again. It changes only what is not correct. Run `~/tools/viewers/install.sh --help` for the options.

## Use

```
mdview notes.md
pyview analysis.py
pdfview paper.pdf
pageview results/page.md
```

The command opens the browser. In the page, select text to comment on it. To get answers from Claude, click the Oracle button or open the chat. You can also use the skills in Claude Code:

| Skill | What it does |
|---|---|
| `/answer-comments FILE.md` | Answer the comments in a Markdown file (mdview) |
| `/answer-cells FILE.py` | Answer the comments in a cell file, and change and run the cells (pyview) |
| `/answer-pdf FILE.pdf` | Answer the comments on a PDF (pdfview) |
| `/answer-page FILE.md` | Answer the comments on a page (pageview) |
| `/page` | Make a pageview page from your data |
| `/plate` | Make a plate from your data files |

The README of each viewer gives the details.

## Update

```
~/tools/viewers/install.sh --update
```

## Security

The viewers read your files, and pyview runs code. By default, each server listens only on your computer (`127.0.0.1`) and accepts only requests with its access key. Read the section Security in the README of each viewer before you open files from other people.

## Problems and ideas

Open an issue in this repository. The note box in the apps sends a note only to a repository that you can push to. In a clone of this repository, the notes stay on your computer.

## License

MIT. See `LICENSE`. The folders `vendor/` of the viewers have their own licenses (MathJax, PDF.js, CodeMirror and IBM Plex). The README of each viewer names them.
