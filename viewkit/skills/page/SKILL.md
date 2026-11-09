---
name: page
description: Make or change a pageview page, the local replacement for a claude.ai Artifact. A page is a Markdown file with extra blocks (tables and charts from CSV or JSON files, figures, tabs, toggles, columns, callouts, stat cards, maths, widgets) that the user opens with the `pageview` command. Use when the user runs /page, asks for a page, a results page, a report to look at, a table or chart view of data, or "make it in pageview", and when the user asks to change a page that they read in pageview.
---

# Make a pageview page

**pageview** (`~/tools/viewers/pageview`) shows a Markdown file with extra blocks as a rich page in the browser. It reads the data of tables and charts from CSV, TSV or JSON files next to the page. It updates the page when a file changes. The user comments on the page in the viewer.

## Before you write

1. **Read the syntax.** Read `~/tools/viewers/pageview/SYNTAX.md` in full the first time in a session. Use only the blocks and the options in it.
2. **Find the data.** Use the real files of the user (results, logs, CSV files). Do not invent numbers. If you must show an example, say in the page that the numbers are examples.
3. **Read the rules of the folder.** Read the `CLAUDE.md` files that apply. Follow them.

## Where the files go

- Put the page and its data in one folder, for example `results/optimizers/`: `optimizers.md`, `runs.csv`, `curves.csv`, `fig/`.
- The data files and the images must be in the folder of the page, in a folder below it, or in the same Git project.
- Use the existing data files of the user when they have the necessary columns. Otherwise write new CSV files from the data, with a short script if the data is large. Do not change the existing files of the user.

## How to write the page

- **Front matter:** always give `title` and `view: page`. Add `subtitle`, `date` and `tags` when they help.
- **Summary first.** Start with the result: a `.callout .result` with one to three sentences, and a `stats` block with the key numbers.
- **Data in files, not in the text.** Put a table with more than about six rows in a CSV file and a `.table src=…` block. Do the same for numbers that a script makes. Use a Markdown table only for a few rows of text.
- **Long form for grids.** For a grid (for example models × datasets), write one row per run (`model,dataset,acc_mean,acc_sd`) and use `rows`, `columns` and `value`. The viewer makes the grid and can swap rows and columns.
- **Mean and spread.** Keep the mean and the standard deviation in two columns, and join them with `sd="mean:sd"`. Mark the best values with `best="acc:max loss:min"` in a table, or `best="col:max"` in a grid.
- **Charts from the same data.** Use `.chart` with `src`, `x`, `y` and `color`. Give `x-label` and `y-label` with units.
- **Captions.** Start the text of a table or chart block with `**Table N.**` or `**Figure N.**`. Then write one sentence that tells what a cell or a line is.
- **Details out of the way.** Put settings, hardware and long notes in `.toggle` or `tabs` blocks.
- **Wide tables.** Add `.wide` to a table with many columns.
- **Widgets only when necessary.** Use a `widget` block only for an interactive view that the blocks cannot give. It has no network and no access to files. Keep it small.
- **No raw HTML.** pageview shows raw HTML as text.
- **Maths:** `$…$` and `$$…$$`.

## Open and check

1. Open the page: `pageview PATH/page.md`. It returns at once and opens the browser. If the page is already open, it updates by itself.
2. Check that the page renders: `pageview PATH/page.md --no-open` must print a link and no error. A block with a wrong column name shows a dashed box with the error in the page. Read the CSV header and fix the names.
3. Tell the user the path of the page and the command to open it.

## Change a page

- Read the page and `SYNTAX.md` first. Change only what the user asks.
- The comments of the user are in `.pageview/NAME.json`. Do not edit that file. Use `pageview list FILE`, and `/answer-page` to answer them.
