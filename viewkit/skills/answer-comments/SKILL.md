---
name: answer-comments
description: Answer the review comments left in a Markdown file as CriticMarkup ({==text==}{>>comment<<}), by proposing changes as suggestions the user accepts or rejects ({~~old~>new~~}, {++new++}, {--old--}) and replying inside each comment thread. Use when the user runs /answer-comments, or asks Claude to answer, address, act on or work through the comments in a .md file (typically one reviewed in mdview).
---

# Answer comments in a Markdown file

The user reads and comments on Markdown files in **mdview**, a local viewer run with the `mdview`
command (installed at `~/tools/mdview`). The `.md` file is the master
copy, and the comments are stored inside it as CriticMarkup. Your job is to act on each open comment
by **proposing changes as suggestions**, which the user accepts or rejects in the viewer, then
reply in the thread so the user can see what you did. The viewer refreshes automatically when the
file changes.

**Direct mode.** If the arguments contain `--direct`, or the user asks you to edit directly, make
the changes in the text itself instead of as suggestions. Everything else below still applies.

## The comment format

```
{==highlighted text==}{>>user's comment<<}{>>@claude: your reply<<}{>>user's follow-up<<}
{>>a general comment about the whole document<<}{>>@claude: your reply<<}
```

- A **thread** is a highlight `{==…==}` followed directly (no space) by one or more messages `{>>…<<}`.
- A `{>>…<<}` with no highlight before it, inside a sentence, is a **point comment** about the text around it.
- A thread that sits **alone on its own line** is a **general comment** about the whole document
  (tone, structure, consistency, things to check everywhere). They are usually at the end of the file.
- Messages with no prefix are from the user. Messages starting `@claude:` are yours. Any other
  `@name:` is another person.
- A thread is **open** when its last message is not yours, and **answered** when it is.
- Anything inside fenced code blocks or inline code is literal text, not a comment.

## The suggestion format

```
{~~old text~>new text~~}    replace
{++new text++}              insert
{--old text--}              delete
```

The viewer shows these as tracked changes, with Accept and Reject buttons. Accepting keeps the new
text, rejecting keeps the old, and both remove the markup.

- **Keep each suggestion small and self-contained**, so it can be accepted on its own: change the
  phrase or sentence that needs it, not the whole paragraph around a one-word fix. Several separate
  suggestions are better than one large one.
- **Highlighted text:** to change text inside a highlight, put the suggestion inside it:
  `{=={~~old~>new~~}==}{>>comment<<}{>>@claude: reply<<}`. The highlight then covers the new text once accepted.
- **New paragraphs, list items or sections:** one `{++…++}` per paragraph, on its own with a blank
  line before and after. Put them after the paragraph that contains the thread, never inside a
  highlight. A suggestion inside a sentence must not contain a blank line.
- **Deleting a paragraph:** wrap the whole paragraph in `{--…--}`.
- **Maths:** wrap whole formulas: `{~~$x$~>$y$~~}`, never part of one.
- Never put suggestions inside code blocks, inline code, comment messages or other suggestions.
  Never suggest changes to the user's messages.
- **Existing suggestions:** if a pending suggestion is the subject of a follow-up ("make it
  shorter"), edit that suggestion's new text in place instead of adding a second one.

## Steps

1. **Find the file.** Use the path given. If there isn't one, look for `.md` files in the working
   directory that contain `{>>`. If more than one has open threads, ask which.
2. **Check the rules for this file.** Read the `CLAUDE.md` files that apply to it (the file's folder
   and its parents). If they restrict what may change in this file (for example, earlier entries of
   a log must never be edited), follow them: reply in the thread, and propose only the changes the
   rules allow. Say so in the reply when a rule stopped you.
3. **List the threads.** Run `mdview FILE --list` to see every thread with its line number and
   status, and any suggested changes still waiting. If the command is missing, use `grep -n '{>>' FILE`.
4. **Read the whole file** before changing anything, so your changes fit the document's argument,
   tone and terminology.
5. **Handle each open thread**, top to bottom:
   - **Change request** ("expand this", "rephrase", "add an example", "this is wrong"): propose the
     change as suggestions (see the format above).
     - Fixing the same problem elsewhere in the document is fine, as further suggestions; say so in the reply.
   - **General comment**: propose the changes across the whole document, then reply on the same
     line, saying which sections you changed. Keep the comment line where it is.
   - **Ask only**: the thread's last user message starts with `[ask only]`. Answer it in the reply
     only. This case overrides the others. Propose no suggestion, make no edit to the document, and add no fix for it anywhere.
     If the answer shows that the text needs a change, say so in the reply and leave the change to the user.
     The tag applies to that message only. A later user message without the tag is a normal comment.
   - **Question**: answer it in the reply. Propose a change only if the answer shows the text needs fixing.
   - **Unclear or a judgement call the user should make**: ask in the reply instead of guessing.
     Propose nothing, or only the safe part.
6. **Reply** by inserting `{>>@claude: …<<}` immediately after the thread's last `{>>…<<}`, with no
   space. Keep it to one line (no line breaks) and never include the characters `<<}`. Maths in a
   reply is fine: write it as `$…$` (inline) or `$$…$$` (display), and the viewer renders it.
   How long depends on what the thread asked:
   - **Change request or general comment:** one or two sentences saying what you suggested and
     where. The suggestions themselves carry the detail, so don't restate them. For example:
     `{>>@claude: Suggested a three-sentence version with the 2019 figure from section 2.<<}`
   - **Question:** as long as the answer needs, up to a short paragraph (about 80–120 words). Give
     the answer first, then the reasoning, with maths and citations (source, section or page) where
     they help. If the user has a decision to make, end with it. If a full answer would be much
     longer, give the short answer and say where the full one belongs (for example, a Q&A entry or
     a note in the document), or offer to write it.
   - **Unclear request:** one sentence saying what is unclear, then the question you need answered.
7. **Verify.** Run `--list` again. The number of threads should be unchanged, every thread you
   handled should now show `[answered]`, and your suggestions should be listed. Then run
   `mdview FILE --check-math` and fix everything it reports, anywhere in the file, as suggestions
   (see Maths width below). Fixes outside the threads you handled go in the report, not in a reply.
8. **Report** in chat: one short line per thread (what was asked → what you suggested), plus anything
   you left as a question for the user.

9. **Write a note to mdview if it stopped your work.** Examples: a subcommand or an option that you needed did
   not exist, a command gave an error or a wrong result, or you had to use a workaround. Write one note for each
   problem: `mdview note - <<'EOF'` with one or two sentences, the command, and the error. Never put the text or the
   path of the document in a note. The note becomes a GitHub issue, and the `improve` skill works on it later. If there
   was no problem, write no note.

## Rules

- Never resolve or delete threads, and never accept or reject suggestions. The user does that in the viewer.
- Never edit, reword or reorder the user's messages.
- Skip answered threads. If the user replied after your reply, the thread is open again: continue it.
  You may be continuing a session from an earlier round on this file; the file is still the source
  of truth, so read it again rather than relying on what you remember.
- Don't add comment markup inside code blocks, and don't nest one thread inside another.
- Keep each general comment on its own line, and don't add text to that line other than your reply.
- Leave everything else as it is: front matter, headings, formatting, line-wrapping style.
- **Links to other files** (in suggestions and in replies) open in mdview when they're relative to
  the document's folder. Check that the file exists before linking it.
  - Paths with spaces go in angle brackets: `[main.tex](<../Paper R1/main.tex>)`.
  - Point at the exact place when you can: `#L268` or `#L268-L279` for lines in LaTeX, code and
    other text files (they open in a read-only view with those lines highlighted), `#page=12` for
    a PDF page (the page number in the PDF file, not the printed one), and `#heading-id` for a
    section of a Markdown file.
  - **One link per reference, named after the file.** The link text is the file name; the
    section, equation, page or line stays as plain text after it, and the link points at that
    place: `[supplementary.tex](<…/supplementary.tex#L321>), Sec. 4.3 (line 321)`. Don't link the
    file and its location separately. When one reference cites several places in the same file,
    use a single link to the first one (or to a span covering them all, if it's short) and list
    the places as text after it. A bare location ("line 656") gets the file's name as its link.
  - Link files, not folders; folder links don't open.
- **Maths width.** The viewer's text column holds about 36 em of maths. Keep every display under
  28 em (the limit `mdview FILE --check-math` uses), and keep long inline formulas short enough to fit on one line.
  - Split a wide display over rows inside `$$…$$`: `\begin{gathered}…\\…\end{gathered}` for
    separate statements, `\begin{aligned}…\end{aligned}` (with `&`) for a chain of relations.
    Break before a relation or operator, and indent continuation rows with `&\qquad`.
  - Split a long inline formula into two formulas joined by words ("where", "so that").
  - Use only standard LaTeX/AMS macros. Paper macros (`\bC`, `\by`, `\bphi`) are undefined in
    the viewer: write `\mathbf{C}`, `\boldsymbol{\phi}`. `--check-math` flags formulas that don't typeset.
- **Sources for claims from outside the corpus.** Whenever a suggestion or reply relies on a result,
  method or fact that is not in the user's own files (the works listed in the applicable
  `CLAUDE.md`, or the folder's papers), find a source for it with WebSearch/WebFetch and cite it.
  Don't cite from memory alone.
  - **Verify before citing:** check the exact statement, and the theorem, section, equation or page
    number, in the source itself (a PDF or publisher page) when you can. Cite a standard textbook or
    the original paper, with a DOI or stable URL when you have one. Say plainly which details you
    could not verify ("theorem number not checked").
  - **In the document:** keep the file's own label (e.g. **[outside corpus]**) and add an author–year
    link to an entry in a `## References (outside the corpus)` section at the end of the file
    (create it if missing, before any closing housekeeping note). Each entry starts with an anchor
    `[]{#ref-key}`, gives the full reference, the exact place used, and where the document uses it;
    cite it as `[Author Year](#ref-key), Lemma 2.5`. Add new entries as suggestions like any other text.
  - **In a reply:** cite inline (author, year, exact place), and add the entry to the references
    section as a suggestion if the claim goes into the document.
- If the user asks to open the viewer, run `mdview FILE`. It returns immediately and opens the browser.
