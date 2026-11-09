# Onboarding for a Claude Code agent

Read this file if the user asks you to install the viewers. Do the steps in sequence. A person can also do these steps by hand.

The viewers are `mdview`, `pyview`, `pdfview`, `pageview`, `plateview` and `texview`. Each viewer shows a type of file in the browser. The user writes comments on the file, and Claude answers them.

At the end of the procedure, these conditions apply:

- The folder `~/tools/viewers` is a clone of this repository, or a link to a clone.
- The six commands are in `~/.local/bin`, and this folder is in `PATH`.
- The skills of the viewers are in the skills folder of Claude Code.
- The external tools that the user needs are available.
- Each viewer opens a sample file with no error.

## Rules for the agent

Tell the user the plan before you start. Get the approval of the user before each of these actions:

- Install a package or a program. This includes Homebrew, pandoc, poppler, MacTeX and `gh`.
- Change a shell profile, for example `~/.zprofile`.
- Replace or move a file or a folder that the setup did not make.

Do not do these actions, unless the user asks for them:

- Set a variable `*_BIND`. By default, each server accepts connections only from the computer of the user. A different address makes files available on the network, and `pyview` runs code.
- Edit a `CLAUDE.md` file. The setup does not need this change.
- Open a file that the user did not select.
- Write a viewer URL in a shared place. The URL contains the access key of the server.

Stop and tell the user if a step fails and the output does not show a clear solution. Do not continue, and do not say that the setup is complete.

## Step 1: Check the computer

Run this command:

```bash
uname -s; git --version; python3 --version; command -v brew pandoc pdftotext latexmk claude gh uv
echo "CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR"
ls -ld ~/tools/viewers 2>&1; git -C ~/tools/viewers remote -v 2>&1
```

Use the output to find the next action:

| Condition | Action |
|---|---|
| The system is not macOS (`Darwin`). | Tell the user that the viewers are tested only on macOS. Ask if you must continue. |
| `git` is not available, or Python is older than 3.9. | Stop. The user must install these tools. |
| `~/tools/viewers` does not exist. | Go to step 2. |
| `~/tools/viewers` is a clone of `viewers-public`. | Skip step 2. In step 3, add the option `--update`. |
| `~/tools/viewers` is a different item. | Stop and ask the user. Do not move it and do not remove it. |

Ask the user which viewers they need. Use the answer to find the external tools for step 4. Install all the viewers, because the installer has no option to select them.

The skills go to `$CLAUDE_CONFIG_DIR/skills`, or to `~/.claude/skills` if this variable is empty. Ask the user if they have more than one Claude Code configuration folder.

## Step 2: Clone the repository

```bash
mkdir -p ~/tools
git clone https://github.com/kostastsa/viewers-public ~/tools/viewers
```

## Step 3: Run the installer

```bash
~/tools/viewers/install.sh
```

Add these options if they apply:

| Option | Use |
|---|---|
| `--update` | The clone exists. The installer also gets the new commits. |
| `--config-dir DIR` | Link the skills into `DIR/skills`. Give one option for each configuration folder. Also give the default folder if the user wants it. |
| `--no-apps` | Do not make the macOS apps that open files from Finder. |
| `--no-venv` | Do not make the Python interpreter of `pyview`. The user can run `pyview --setup` later. |

The installer can take many minutes, because it makes the Python interpreter of `pyview`. You can run the installer again. It changes only the items that are not correct.

Read all the output. Each line that starts with `WARNING` needs an action in step 4. A line that starts with `backup` shows a skill folder that the installer moved. The new location is `backups/pre-viewkit` in the configuration folder. Tell the user about each of these lines.

## Step 4: Correct the warnings

Do only the actions that the user approved.

| Warning | Used by | Action |
|---|---|---|
| `~/.local/bin` is not in `PATH` | All the viewers | Add `export PATH="$HOME/.local/bin:$PATH"` to the shell profile (`~/.zprofile` for zsh, `~/.bash_profile` for bash). |
| `pandoc` is missing | `mdview`, `pageview` | `brew install pandoc`. `pageview` needs pandoc 3 or newer. |
| `pdftotext` is missing | `pdfview text`, `pdfview crop`, PDF figures in `pageview` and `plateview` | `brew install poppler` |
| `latexmk` is missing | `texview` | `brew install --cask mactex-no-gui`. Tell the user that the download is many gigabytes. |
| `claude` is missing | The chat and the Oracle button | Install Claude Code: https://claude.com/claude-code |
| `gh` is missing | Optional: notes as GitHub issues | Do nothing, unless the user wants it. Without `gh`, the notes stay on the computer. |
| `pyview --setup failed` | `pyview` | Read the error. The setup needs `uv` or `python3 -m venv`. Then run `pyview --setup` again. |
| A command `is a file, not a link` | That command | Show the file to the user. Remove it only with the approval of the user, then run the installer again. |
| `could not build APP.app` | Finder only | Run the `build.sh` that the warning shows, and read the error. |

If `brew` is not available, tell the user. Do not install Homebrew without approval. On a different system, use the package manager of that system.

After a change, run the installer again and read the output again.

## Step 5: Verify the setup

Run this script. If you changed the skills folder with `--config-dir`, set `SKILLS` to that folder.

```bash
export PATH="$HOME/.local/bin:$PATH"
SKILLS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
for a in mdview pyview pdfview pageview plateview texview; do
  command -v "$a" >/dev/null && echo "ok      $a" || echo "MISSING $a"
done
for s in answer-comments answer-cells answer-pdf answer-page answer-tex answer-plate page plate improve; do
  [ -f "$SKILLS/$s/SKILL.md" ] && echo "ok      skill $s" || echo "MISSING skill $s"
done
[ -x ~/tools/viewers/pyview/.venv/bin/python ] && echo "ok      pyview interpreter" || echo "MISSING pyview interpreter"
```

Then make sure that each viewer can start. Do this test only if `APP --status` shows that no viewer server operates now. These commands do not open the browser and do not change the samples.

```bash
V=~/tools/viewers
mdview   --no-open "$V/mdview/samples/review.md"
pyview   --no-open "$V/pyview/samples/cells.py"
pdfview  --no-open "$V/pdfview/samples/paper.pdf"
pageview --no-open "$V/mdview/samples/structure.md"
texview  --no-open "$V/texview/samples/paper.tex"
plateview --status
```

The first five commands must each print a URL. Do not show the URLs in your report. Skip `texview` if `latexmk` is not available, and tell the user about this. Stop the servers at the end of the test:

```bash
for a in mdview pyview pdfview pageview plateview texview; do "$a" --stop; done
```

If a viewer does not start, read its log in `~/.cache/APP/server.log`.

## Step 6: Report to the user

Tell the user these items:

1. The viewers that work, and the viewers that do not work. Tell why each of these viewers does not work.
2. The external tools that are not available, and the features that need them.
3. The changes that you made outside `~/tools/viewers`, for example a shell profile or a moved skill folder.
4. These actions that only the user can do:
   - Start a new Claude Code session, so that the skills load.
   - Open a new terminal if the shell profile changed.
   - To open files from Finder, select a file, then use **Get Info > Open with > the app > Change All**. The apps are in `~/Applications`.

Then show this example:

```bash
mdview ~/tools/viewers/mdview/samples/review.md
```

In the page, the user selects text and writes a comment. Then the user clicks the Oracle button or runs a skill in Claude Code:

| Skill | Viewer |
|---|---|
| `/answer-comments FILE.md` | `mdview` |
| `/answer-cells FILE.py` | `pyview` |
| `/answer-pdf FILE.pdf` | `pdfview` |
| `/answer-page FILE.md` | `pageview` |
| `/answer-tex FILE.tex` | `texview` |
| `/answer-plate ID` | `plateview` |

The skills `/page` and `/plate` make a page or a plate from data files. The skill `/improve APP` works on the notes to a viewer. The `README.md` of each viewer gives the details and the security rules. The file `HOTKEYS.md` lists the keys.

## Updates

```bash
~/tools/viewers/install.sh --update
```

The update keeps the commits of the user and merges them with the new commits. If the commits conflict, the script tells the user to merge them with `git -C ~/tools/viewers pull`.
