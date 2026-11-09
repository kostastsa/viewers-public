---
name: answer-plate
description: Answer the open comments on a plate that the user reads in plateview, with a reply in each thread. A plate is a temporary HTML page that shows live data files. The comments are in the folder of the plate, and you read and answer them with the `plateview` subcommands. Use when the user runs /answer-plate, when the plateview Oracle button starts you, or when the user asks Claude to answer the comments on a plate.
---

# Answer the comments on a plate in plateview

The user reads **plates** in **plateview**, a local viewer started with the `plateview` command (installed at `~/tools/viewers/plateview`). A plate is an HTML page that shows tables, grids, charts and figures of data files. It reads the data files live with the `pv` library. Each plate has an id (for example `3f9a0c1b2d4e`). Your job is to answer each open comment on the plate with a reply in its thread. The viewer follows the comments and shows your replies at once.

## The comments

- A **thread** has an id, an optional **anchor**, and messages. A message has an author, `you` (the user) or `claude`.
- A **text anchor** is the selected words of the plate (`quote`), and some words before and after them (`prefix`, `suffix`).
- A **cell anchor** is a cell of a table or a grid: `table` (the data file of the table, as the plate gives it to `pv.table` or `pv.grid`, or the `key` option), `row` (the value in the key column of the row, by default the first column) and `col` (the column name). In a grid, `row` and `col` are the values of the `rows` and `columns` columns of the file.
- A thread without an anchor is a **general comment** about the whole plate.
- A thread is **open** when its last message is not from `claude`.

## The commands

```
plateview show ID                                  the HTML file of the plate, its base folder, its folders, the files it reads
plateview comments ID [--json]                     the threads: id, anchor, status, messages
plateview reply ID N - <<'EOF'                     a reply to thread N; the text is on the lines up to EOF
plateview add ID - --quote 'WORDS' <<'EOF'         a new comment on the exact WORDS of the plate
plateview add ID - --cell TABLE ROW COL <<'EOF'    a new comment on a table cell
```

- `ID` can also be the path of the folder of the plate, or of its file `plate.html`.
- **Always give the text on stdin**, with the quoted heredoc `<<'EOF'`. The quotes around EOF stop the shell from a change to `$`, backquotes and quotes, so maths such as `$x^2$` stays correct.
- Do not edit the comments file, and do not read it directly. Use the commands.
- Read the HTML of the plate and its data files with the Read tool. A relative path in the plate starts at the base folder. The section "Write a plate" of `~/tools/viewers/plateview/README.md` explains the `pv` functions.

## Steps

1. **Find the plate.** Use the id given. If there is none, run `plateview list` and ask which plate.
2. **Read the plate.** Run `plateview show ID`. Read the HTML file. Note the data files that it reads.
3. **Check the rules for the data.** Read the `CLAUDE.md` files of the base folder of the plate. Follow them.
4. **List the threads.** Run `plateview comments ID --json`.
5. **Read the context of each open thread.**
   - Text anchor: find the quote in the HTML, or in the data that the plate shows (a title, a label, a value).
   - Cell anchor: find the call of `pv.table` or `pv.grid` with that data file in the HTML, and read its options. Then read the row and the column in the data file.
   - General comment: read the whole plate, and the data files that the question needs.
6. **Answer each open thread**, in the order of the plate:
   - **Question:** answer it from the plate and its data first. Give numbers with their units and the row or column that they come from.
   - **Claim from outside the plate:** find a source with WebSearch or WebFetch. Name the source in the reply (author, year, and the theorem or page number). If you cannot verify a detail, say so.
   - **Unclear question, or a choice that the user must make:** ask in the reply.
   - **Request for a change of the plate or the data:** you cannot edit files here. Reply with the exact change (the HTML or the script lines), so the user or the chat can make it.
   - **Request for a new comment:** add it with `plateview add`. Then reply in the thread with where it is.
7. **Write each reply** with `plateview reply ID N - <<'EOF'`. Use one to four sentences. Write plain text. The viewer shows `code`, **bold**, *italic*, and maths in `$…$` and `$$…$$`.
8. **Verify.** Run `plateview comments ID`. Each thread that you answered must show `[answered]`.
9. **Report** in chat. Write one short line for each thread: what was asked, and what you answered.

## Rules

- Never change the plate or its data files. Never run `plateview update` or `plateview delete`. Never edit or delete the comments file.
- Never resolve a thread, and never edit or delete a message. The user does that in the viewer.
- Skip an answered thread. If the user replied after your reply, the thread is open again: answer the new message.
- Add a new comment only when the user asks for it.
- If the user asks you to open the plate, run `plateview open ID`. It returns at once and opens the browser.
