---
name: answer-pdf
description: Answer the open comments on a PDF that the user reads in pdfview, with a reply in each thread. The comments are in a JSON file next to the PDF, and you read and answer them with the `pdfview` subcommands. Use when the user runs /answer-pdf, when the pdfview Oracle button starts you, or when the user asks Claude to answer the comments on a PDF.
---

# Answer the comments on a PDF in pdfview

The user reads PDF files in **pdfview**, a local viewer started with the `pdfview` command (installed at
`~/tools/pdfview`). pdfview never changes the PDF. The comments are in the file `.pdfview/NAME.pdf.json` next to
the PDF. Your job is to answer each open comment with a reply in its thread. The viewer follows the comments file
and shows your replies at once.

## The comments

- A **thread** has an id, an optional **anchor**, and messages. A message has an author, `you` (the user) or `claude`.
- The **anchor** is the place on the page that the comment is about: the page number (from 1) and the selected words
  (`quote`), with some words before and after them (`prefix`, `suffix`).
- A **box anchor** is an area of a page: the page and `box` = `[x0, y0, x1, y1]`. The numbers are PDF points from
  the top-left corner of the page. The anchor also holds the text in the area (`quote`) if it has some. The user
  draws a box for a figure, an equation, a table or a scanned page.
- A thread without an anchor is a **general comment** about the whole PDF.
- **Highlights** (`pdfview list` shows them after the comments) are passages with no comment. Do not answer them.
- A thread is **open** when its last message is not from `claude`.

## The commands

```
pdfview list FILE [--json]                     the threads: id, page, quote, status, messages
pdfview text FILE --page N                     the text of page N (also --pages A-B; no option: all pages)
pdfview reply FILE ID - <<'EOF'                a reply to thread ID; the text is on the lines up to EOF
pdfview add FILE - --page N --quote 'WORDS' <<'EOF'   a new comment on page N, at the exact WORDS of the page
pdfview add FILE - --page N --box x0,y0,x1,y1 <<'EOF'   a new comment on an area of page N
pdfview crop FILE --page N --box x0,y0,x1,y1   a PNG of the area (it prints the path); read it with the Read tool
pdfview text FILE --page N --box x0,y0,x1,y1   the text in the area only
```

- **Always give the text on stdin**, with the quoted heredoc `<<'EOF'`. The quotes around EOF stop the shell from a
  change to `$`, backquotes and quotes, so maths such as `$x^2$` stays correct.
- Do not edit the comments file, and do not read it directly. Use the commands.
- To look at a figure, a table or an equation, read the page as an image with the Read tool and its `pages`
  parameter. `pdfview text` gives only the text.

## Steps

1. **Find the PDF.** Use the path given. If there is none, look in the working directory for PDF files that have a
   `.pdfview/NAME.pdf.json` file. If more than one has open threads, ask which.
2. **Check the rules for this file.** Read the `CLAUDE.md` files that apply to it. Follow them.
3. **List the threads.** Run `pdfview list FILE --json`.
4. **Read the context of each open thread.**
   - Anchored thread: read its page with `pdfview text FILE --page N`. Find the quote on the page. Read the pages
     before and after it if the passage continues there.
   - Box anchor: look at the area with `pdfview crop`, and read the PNG that it prints with the Read tool. Read its
     text with `pdfview text FILE --page N --box …`, and the page around it if the question needs more.
   - General comment: read as much of the PDF as the question needs. Start with the first pages and the contents.
   - If the question is about a figure, a table or an equation, read the page as an image (see above).
5. **Answer each open thread**, in the order of the pages:
   - **Question:** answer it. Use the text of the PDF first. Give page numbers, for example "(p. 7)", when you
     refer to other parts of the PDF.
   - **Claim from outside the PDF:** the answer can need a result that is not in the PDF. Then find a source with
     WebSearch or WebFetch. Name the source in the reply (author, year, and the theorem or page number).
     If you cannot verify a detail, say so.
   - **Unclear question, or a choice that the user must make:** ask in the reply.
   - **Request for a new comment** (for example "mark where the paper defines X"): add it with `pdfview add`, with
     the exact words from `pdfview text` as `--quote`. Then reply in the thread with the page number.
6. **Write each reply** with `pdfview reply FILE ID - <<'EOF'`.
   - Use one to four sentences. Write plain text. The viewer shows `code`, **bold**, *italic*, and maths in `$…$`
     and `$$…$$`.
   - Quote only a few words of the PDF, in quotation marks, with the page number.
7. **Verify.** Run `pdfview list FILE`. Each thread that you answered must show `[answered]`.
8. **Report** in chat. Write one short line for each thread: what was asked, and what you answered.
9. **Write a note to pdfview if it stopped your work.** Examples: a subcommand that you needed did not exist, a
   subcommand gave an error or a wrong result, or you had to use a workaround. Write one note for each problem:
   `pdfview note - <<'EOF'` with one or two sentences, the command, and the error. Never put the text or the path of
   the PDF in a note. The note becomes a GitHub issue, and the `improve` skill works on it later. If there was no
   problem, write no note.

## Rules

- Never change the PDF. Never edit or delete the comments file.
- Never resolve a thread, and never edit or delete a message. The user does that in the viewer.
- Skip an answered thread. If the user replied after your reply, the thread is open again: answer the new message.
- Add a new comment only when the user asks for it.
- If `pdfview text` says that pdftotext is missing, read the pages with the Read tool, and tell the user that
  `brew install poppler` installs pdftotext.
- If the user asks you to open the viewer, run `pdfview FILE`. It returns at once and opens the browser.
