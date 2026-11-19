---
name: plate
description: Make a plate, a temporary HTML page like a claude.ai Artifact that the user opens with plateview, to show tables, grids, charts and figures of live data files without a copy of the data. Use when the user runs /plate, asks for a plate, or asks to "build a page with the data from" an experiment, results folder or data files, and when the user asks to change a plate. Also use it for a deck of slides (a presentation) from data, text or figures, or to open a claude.ai deck folder (deck.json, slides/) in plateview. Do not use it for Markdown notes (pageview) or for a file to send (use Export of plateview).
---

# Make a plate

**plateview** (`~/tools/viewers/plateview`) shows a plate: an HTML page that you write, in a sandboxed frame. The plate reads data files and images live through the `pv` library. It never holds a copy of the data. The full reference of `pv` and of the classes is in `~/tools/viewers/plateview/README.md`. Read the section "Write a plate" of it the first time in a session.

## Steps

1. **Find the data.** Use the real output files of the experiment: CSV, TSV, JSON or JSON Lines, and figures (PNG, SVG, PDF). Read their headers (the first lines) to get the column names. Do not invent numbers. Do not copy data into the plate.
2. **Read the rules of the project.** Read the `CLAUDE.md` files that apply. Follow them.
3. **Plan the plate.** Start with the result: a header, then the key numbers (`.kpis`), then the tables, grids, charts and figures. Use controls (`pv.seg`, `pv.select`, `.cells`) when one view has settings to choose from. Put notes and sources in the footer.
4. **Write the HTML** to a scratch file, for example in the scratchpad or `/tmp`. Give it a `<title>`. Use the classes of `plate.css` (`.wrap`, `header`, `.eyebrow`, `h1`, `.lede`, `.controls`, `.ctl`, `.ctl-label`, `.panel`, `.row2`, `.kpis`, `.tag`, `.note`, `footer`). Add a small `<style>` only for what the classes do not give.
5. **Make the plate** with the folders that it may read. If the computer has a Tailscale address, use the Tailscale settings, so that the link opens on the other devices of the user. If it has no Tailscale address, the plate stays on this computer:

   ```
   if [ -n "$(python3 -c 'import sys,os; sys.path.insert(0, os.path.expanduser("~/tools/viewers/viewkit")); import serverkit; print(serverkit.tailscale_ip() or "")')" ]; then
     export PLATEVIEW_BIND=tailscale PLATEVIEW_NAMES=${PLATEVIEW_NAMES:-$(scutil --get LocalHostName | tr A-Z a-z)}
   fi
   PV="$(command -v plateview || echo python3 ~/tools/viewers/plateview/plateview.py)"
   $PV --status      # with Tailscale: if it shows 127.0.0.1, run: $PV --restart
   $PV new /path/plate.html --allow ~/path/to/experiment/output --base ~/path/to/experiment
   ```

   - Put the `if … fi` lines in each shell call that runs plateview. The command line uses `PLATEVIEW_NAMES` for the host of the link. The server uses `PLATEVIEW_BIND` when it starts.
   - With Tailscale: if `--status` shows `127.0.0.1`, the server does not listen on the Tailscale address. Run `$PV --restart` one time. The plates stay.

   - Give `--allow` for each folder of data. Give the smallest folders that hold the files.
   - With `--base`, the paths in the plate are relative to that folder. Without it, they are relative to the folder of the HTML file.
   - The command prints the plate id and opens the browser.
6. **Check it.** An error of the plate (a wrong path, a wrong column, a script error) shows in a red line above the plate. Ask the user, or fix it and run `plateview update ID /path/plate.html`. The open tab reloads by itself.
7. **Tell the user** the title, the id, the link (from `$PV open ID` with the settings of step 5), and what the plate shows. With Tailscale, give the Tailscale link (`http://<tailnet name>:8769/p/ID?t=…`), never a link with `localhost` or `127.0.0.1`. Say that the plate stays while this session runs, and that **Keep** and **Export** are in its bar.

## Make a deck

A deck is a plate of slides (1920 × 1080 px), with speaker notes. Use a deck when the user asks for slides, a deck or a presentation. The full rules are in the section "Decks" of `~/tools/viewers/plateview/README.md`. Read it the first time in a session. The example is `~/tools/viewers/plateview/examples/deck/`.

1. **Find the content.** Read the data files, the text and the figures that the user names. Do not invent numbers.
2. **Make a deck folder** in the project (for example `talks/NAME/`), not in the scratchpad, because the user keeps it. Write these files:
   - `deck.json`: `title`, `order` (the slide ids), `sections` (`{"s1": {"description", "start"}}`) and `format.shared` (`["deck.css"]`).
   - `deck.css`: the look of the deck: the colors as variables in `:root`, the fonts, the type sizes in px, and the classes of the slides.
   - `slides/ID.html`: one `<section id="ID">` for each slide, with the speaker notes in `<aside>`.
3. **Write the slides.** Give each slide one message, in the title. Use px sizes for a canvas of 1920 × 1080 px: a title of 60 to 70 px, text of 26 to 32 px, and a padding of about 120 px. Put a diagram in a box with `position: relative`, and draw its arrows with `<x-connector>`. Use `data-transition="fade"` and a staged animation class if the deck needs motion.
4. **Use live data.** Get each number, table, chart or figure of a data file from `pv`, in a `<script>` in the slide. Use `pv.watch`, `pv.chart`, `pv.table`, `pv.figure`, or `<img src>` of an allowed folder. The rule "No data in the HTML" also applies to slides. Text from the user (an argument, a list) goes into the slide as text.
5. **Make the deck plate** with the settings of step 5 above: `$PV deck /path/to/deck-folder --allow /path/to/data`. The deck may read its own folder without `--allow`. A claude.ai deck folder (with `project/deck.json`) opens in the same way.
6. **Check it.** Open each slide (→). Look for an error line, for text that goes out of the slide, and for empty charts. Fix the files of the folder. The open tab follows the folder, so do not run `plateview update`.
7. **Tell the user** the link, the deck folder, and the keys: → and ← (move), N (notes), G (grid), F (full screen). Say that Export saves the deck as one HTML file. Say also that ⌘/Ctrl+P makes a PDF with one slide on each page.

## Rules

- **No data in the HTML.** Every number comes from a file through `pv.table`, `pv.grid`, `pv.chart`, `pv.watch`, `pv.data`, or `pv.json` and `pv.watchJSON` for a JSON file that is not a table. Write a transform (a filter, a mean, a join) in JavaScript, in `prepare` or in `pv.watch`.
- **Nothing from the internet.** No external scripts, styles, fonts or images: the sandbox blocks them. The fonts are IBM Plex, already in the plate.
- **Live.** Use `pv.watch(path, fn)` for parts that you draw yourself (for example the stat cards), so that they follow the file.
- **Export.** If the plate loads a file only after a click, call `pv.need([...])` with those paths at the start, so that Export has them.
- **Change a plate** with `plateview update ID FILE`. Do not make a new plate for a small change. To change a deck, edit the files of its deck folder: the plate follows them.
- **Do not keep or delete** a plate unless the user asks.
- **Comments.** The user can comment on a plate. `plateview comments ID` prints the comments, and `/answer-plate ID` answers them. Keep the words and the table keys of a plate when you change it, so that the comments find their place again. A comment on a cell names the data file of the table (or the `key` option).
