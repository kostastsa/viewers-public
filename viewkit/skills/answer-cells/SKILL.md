---
name: answer-cells
description: Answer the #> comments in a Python cell file (a .py file with `# %%` cells) that the user reads in pyview, reply in each thread, and write or change the code in the cells, then run the cells to check the result. Use when the user runs /answer-cells, or asks Claude to answer, address or act on the comments in a pyview .py file.
---

# Answer comments in a pyview cell file

The user runs and reads Python cells in **pyview**, a local viewer started with the `pyview`
command (installed at `~/tools/viewers/pyview`). The `.py` file is the master copy. The comments are
lines in the file. Your job is to act on each open comment. Edit the code **directly**, run the
cells to prove that the change works, and reply in the thread. The viewer follows the file and
shows your changes at once.

## The file format

```python
# %% [markdown]
# # A title
# Text with maths: $x^2$.

# %% Load data
#> why is this slow?
#> @claude: read_csv parses the dates here.
df = pd.read_csv("big.csv", parse_dates=["t"])
```

- A cell starts at a line `# %%`. A markdown cell starts at `# %% [markdown]`, and each of its
  text lines starts with `# `. Text before the first `# %%` is cell 0.
- A **comment** is one or more lines that start with `#>`. A comment above a code line is about that line.
  A comment directly under a `# %%` line is about the whole cell (use it for a question about an output).
  A comment before the first cell, followed by a blank line, is general.
- A line `#> @name: text` starts a message. A line `#> text` with no `@name:` is from the user
  when it starts a thread, and it continues the message above when it does not.
  Messages `@claude:` are yours. A thread is **open** when its last message is not yours.
- Cells share one namespace in one runner process. Outputs are not in the file.
  Pyview saves them in the `.pyview/` folder next to the file.

## The commands

```
pyview FILE --list [--json]     the comment threads, with cell and line
pyview out FILE [--cell N]      the saved output of the cells (it marks a cell "EDITED since run")
pyview run FILE --cell N        run one cell, wait, print its output (exit code 1 if it fails)
pyview run FILE --all           run every code cell; it stops at the first error
pyview run FILE --from N --to M run the cells N to M
pyview restart FILE             stop the runner and clear all variables
```

`run` prints the text form of each output. For a figure it prints the path of a PNG file. Open
that file with the Read tool to look at the plot. Long text is cut: add `--max-chars 0` to see all of it.

## Steps

1. **Find the file.** Use the path given. If there is none, look in the working directory for
   `.py` files that contain `#>`. If more than one has open threads, ask which.
2. **Check the rules for this file.** Read the `CLAUDE.md` files that apply to it. Follow them.
3. **List the threads.** Run `pyview FILE --list`.
4. **Read the whole file**, then run `pyview out FILE`. This shows which cells ran, what they
   printed, and which cells changed since they ran.
5. **Handle each open thread**, top to bottom:
   - **Question** about the code or an output: answer it in the reply. Run a cell if the answer
     needs a fact from the data. Change code only if the answer shows that the code is wrong.
   - **Change request or new code:** edit the cell with the Edit tool. Keep every `#>` line
     where it is. To add code, add a new cell with a `# %% Title` line after the cell it follows.
     Do not use `pyview` to edit: edit the file.
   - **Unclear request, or a choice the user must make:** ask in the reply. Change nothing, or only the safe part.
6. **Run what you changed.** Run each changed cell with `pyview run FILE --cell N`. Then run the
   cells below it that use what changed. Look at the output. If a cell fails, fix it and run it again.
   Do not leave a cell that fails, unless the user asked for that.
7. **Reply** with `#> @claude: …` lines placed directly after the last `#>` line of the thread.
   - Use one to three sentences. Say what you changed and what the output now shows.
   - Put a second line `#> more text` under the first to continue the same message.
   - Do not write the code in the reply: the code is in the cell.
8. **Verify.** Run `pyview FILE --list`. Each thread you handled must show `[answered]`. Run
   `pyview out FILE` and check that no cell you changed shows ERROR or EDITED.
9. **Report** in chat. Write one short line for each thread: what was asked, and what you did.

10. **Write a note to pyview if it stopped your work.** Examples: a subcommand or an option that you needed did
   not exist, a command gave an error or a wrong result, or you had to use a workaround. Write one note for each
   problem: `pyview note - <<'EOF'` with one or two sentences, the command, and the error. Never put the text or the
   path of the file in a note. The note becomes a GitHub issue, and the `improve` skill works on it later. If there
   was no problem, write no note.

## Rules

- Never resolve or delete a thread. Never edit or move a message of the user. The user does that in the viewer.
- Skip an answered thread. If the user replied after your reply, the thread is open again.
- Change only the code that the comments concern. Keep imports, names, and style as they are.
- Do not change or remove the `# %%` line of a cell that exists. Pyview uses the cell text to match outputs.
- Do not write to the `.pyview/` folder and do not read its JSON files. Use `pyview out`.
- A cell must not wait for input. `input()` raises an error in pyview.
- If `pyview run` says that there is no interpreter, tell the user to run `pyview --setup`.
  If a package is missing, tell the user. Do not install a package unless the user asks.
- If the user asks you to open the viewer, run `pyview FILE`. It returns at once and opens the browser.
