---
name: answer-tex
description: Answer the open comments on a LaTeX document that the user reads in texview, with a reply in each thread, and suggest changes to the source when a comment asks for one (the user accepts or rejects them). The comments are in a JSON file next to the main .tex file, and you read and answer them with the `texview` subcommands. Use when the user runs /answer-tex, when the texview Oracle button starts you, or when the user asks Claude to answer the comments on a LaTeX document in texview.
---

# Answer the comments on a LaTeX document in texview

The user reads and edits LaTeX documents in **texview**, a local viewer started with the `texview` command (installed
at `~/tools/viewers/texview`). texview shows the source next to the compiled PDF. The comments and the suggested
changes are in the file `.texview/MAIN.tex.json` next to the main file. Your job is to answer each open comment with a
reply in its thread. When a comment asks for a change to the text, suggest the change: the user sees it in the editor
and accepts or rejects it. The viewer follows the comments file and shows your work at once.

## The comments

- A **thread** has an id, an optional **anchor**, and messages. A message has an author, `you` (the user) or `claude`.
- The **anchor** is the place in a source file: `file` (relative to the folder of the main file), `line`, and the
  words (`quote`), with some words before and after them (`prefix`, `suffix`). `texview list` gives the line as it is
  now, and says "text not found" when the words are no longer in the file.
- A thread without an anchor is a **general comment** about the whole document.
- A thread with **change** is a suggested change (old text and new text) that waits for the user. Do not answer it.
- A thread is **open** when its last message is not from `claude` and it is not a change.

## The commands

```
texview list MAIN [--json]                      the threads: id, file:line, quote, status, messages
texview reply MAIN ID - <<'EOF'                 a reply to thread ID; the text is on the lines up to EOF
texview add MAIN - --file F --line N <<'EOF'    a new comment on line N of source file F (--quote 'WORDS': on words)
texview suggest MAIN --file F [--line N] <<'EOF'   suggested changes to source file F (the format is below)
texview log MAIN                                the errors and warnings of the last compile
texview text MAIN --page N                      the text of page N of the compiled PDF (also --pages A-B)
```

The format of `texview suggest` (one or more blocks; the lines after a block are the reason, optional):

```
texview suggest MAIN --file sections/intro.tex --line 12 <<'EOF'
<<<<<<< old
the exact old text, copied from the file
=======
the new text
>>>>>>> new
A short reason.
EOF
```

- **Always give the text on stdin**, with the quoted heredoc `<<'EOF'`. The quotes around EOF stop the shell from a
  change to `$`, backslashes and quotes, so LaTeX and maths stay correct.
- The old text must be in the file **exactly**: the same spaces, line ends and commands. Copy it from the Read tool
  output (without the line numbers). If it occurs more than one time, give `--line` (the line where it starts).
- Keep each block small: a phrase, a sentence or a paragraph. Never suggest a whole section in one block.
- Do not edit the source files, and do not edit or read the comments file directly. Use the commands. The user
  accepts or rejects each change in the viewer.

## Steps

1. **Find the document.** Use the path given (the main .tex file). If there is none, look in the working directory for
   a `.texview/MAIN.tex.json` file. If more than one has open threads, ask which.
2. **Check the rules for this document.** Read the `CLAUDE.md` files that apply to it, and a project tracker if it
   has one. Follow them.
3. **List the threads.** Run `texview list MAIN --json`.
4. **Read the context of each open thread.**
   - Anchored thread: read the source file around its line with the Read tool (an offset some lines before it). Read
     more if the paragraph or the environment continues.
   - General comment: read as much of the document as the question needs. Start with the main file.
   - If the question is about how the PDF looks (a figure, a table, the layout), read the page of the compiled PDF:
     `texview text MAIN --page N`, or the PDF in the build folder with the Read tool and its `pages` parameter.
   - If the question is about a compile error or a warning, run `texview log MAIN`.
5. **Answer each open thread**, in the order of the files and lines:
   - **Question:** answer it. Give `file:line` when you refer to other parts of the source.
   - **Request for a change** ("shorten this", "fix the grammar", "use \cref here"): suggest the change with
     `texview suggest`, then reply in the thread in one sentence: what you suggest, and why.
   - **Claim from outside the document:** the answer can need a result that is not in the document. Then find a source
     with WebSearch or WebFetch. Name the source in the reply (author, year, and the theorem or page number). If you
     cannot verify a detail, say so.
   - **Unclear question, or a choice that the user must make:** ask in the reply.
   - **Request for a new comment** (for example "mark where we define X"): add it with `texview add`, then reply in
     the thread with the place.
6. **Write each reply** with `texview reply MAIN ID - <<'EOF'`.
   - Use one to four sentences. Write plain text. The viewer shows `code`, **bold**, *italic*, and maths in `$…$`
     and `$$…$$`.
7. **Verify.** Run `texview list MAIN`. Each thread that you answered must show `[answered]`, and each change that you
   suggested must show `[change]`. If `texview suggest` refused a block, copy the old text again from the file.
8. **Report** in chat. Write one short line for each thread: what was asked, and what you answered or suggested.
9. **Write a note to texview if it stopped your work.** Examples: a subcommand that you needed did not exist, a
   subcommand gave an error or a wrong result, or you had to use a workaround. Write one note for each problem:
   `texview note - <<'EOF'` with one or two sentences, the command, and the error. Never put the text or the path of
   the document in a note. If there was no problem, write no note.

## Rules

- Never edit a source file and never run `texview build`. The user saves, and accepts or rejects your changes.
- Never edit or delete the comments file. Never resolve a thread, and never edit or delete a message.
- Skip an answered thread and a change. If the user replied after your reply, the thread is open again: answer it.
- Suggest a change only when a comment asks for it, or when the answer is a correction of the text that the user
  will want (then say so in the reply).
- If the user asks you to open the viewer, run `texview MAIN`. It returns at once and opens the browser.
