---
name: answer-page
description: Answer the open comments on a page or a data file that the user reads in pageview, with a reply in each thread. The comments are in a JSON file next to the page, and you read and answer them with the `pageview` subcommands. Use when the user runs /answer-page, when the pageview Oracle button starts you, or when the user asks Claude to answer the comments on a pageview page.
---

# Answer the comments on a page in pageview

The user reads rich pages in **pageview**, a local viewer started with the `pageview` command (installed at `~/tools/pageview`). A page is a Markdown file with extra blocks, and its tables and charts read CSV or JSON files next to it. pageview can also open a data file alone. The comments are in the file `.pageview/NAME.json` next to the document. Your job is to answer each open comment with a reply in its thread. The viewer follows the comments file and shows your replies at once.

## The comments

- A **thread** has an id, an optional **anchor**, and messages. A message has an author, `you` (the user) or `claude`.
- A **text anchor** is the selected words (`quote`), some words before and after them (`prefix`, `suffix`), and the line of the words in the file (`line`).
- A **cell anchor** is a table cell: `table` (the data file of the table, or `table-N` for the N-th Markdown table of the page), `row` (the value in the key column of the row, by default the first column) and `col` (the column name). In a grid, `row` and `col` are the values of the `rows` and `columns` columns of the file.
- A thread without an anchor is a **general comment** about the whole page.
- A thread is **open** when its last message is not from `claude`.

## The commands

```
pageview list FILE [--json]                       the threads: id, anchor, status, messages
pageview reply FILE ID - <<'EOF'                  a reply to thread ID; the text is on the lines up to EOF
pageview add FILE - --quote 'WORDS' <<'EOF'       a new comment on the exact WORDS of the page
pageview add FILE - --cell TABLE ROW COL <<'EOF'  a new comment on a table cell
```

- **Always give the text on stdin**, with the quoted heredoc `<<'EOF'`. The quotes around EOF stop the shell from a change to `$`, backquotes and quotes, so maths such as `$x^2$` stays correct.
- Do not edit the comments file, and do not read it directly. Use the commands.
- Read the page and the data files with the Read tool. `~/tools/pageview/SYNTAX.md` explains the blocks.

## Steps

1. **Find the document.** Use the path given. If there is none, look in the working directory for files that have a `.pageview/NAME.json` file. If more than one has open threads, ask which.
2. **Check the rules for this file.** Read the `CLAUDE.md` files that apply to it. Follow them.
3. **List the threads.** Run `pageview list FILE --json`.
4. **Read the context of each open thread.**
   - Text anchor: read the page near `line`, and find the quote.
   - Cell anchor: find the block with `src="TABLE"` in the page, read its options, and read the row and the column in the data file.
   - General comment: read the whole page, and the data files that the question needs.
5. **Answer each open thread**, in the order of the page:
   - **Question:** answer it from the page and its data first. Give numbers with their units and the row or column that they come from.
   - **Claim from outside the page:** find a source with WebSearch or WebFetch. Name the source in the reply (author, year, and the theorem or page number). If you cannot verify a detail, say so.
   - **Unclear question, or a choice that the user must make:** ask in the reply.
   - **Request for a change of the page or the data:** you cannot edit files here. Reply with the exact change (the Markdown or the CSV lines), so the user or the chat can make it.
   - **Request for a new comment:** add it with `pageview add`. Then reply in the thread with where it is.
6. **Write each reply** with `pageview reply FILE ID - <<'EOF'`. Use one to four sentences. Write plain text. The viewer shows `code`, **bold**, *italic*, and maths in `$…$` and `$$…$$`.
7. **Verify.** Run `pageview list FILE`. Each thread that you answered must show `[answered]`.
8. **Report** in chat. Write one short line for each thread: what was asked, and what you answered.

## Rules

- Never change the page or its data files. Never edit or delete the comments file.
- Never resolve a thread, and never edit or delete a message. The user does that in the viewer.
- Skip an answered thread. If the user replied after your reply, the thread is open again: answer the new message.
- Add a new comment only when the user asks for it.
- If the user asks you to open the viewer, run `pageview FILE`. It returns at once and opens the browser.
