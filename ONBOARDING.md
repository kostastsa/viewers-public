# Onboarding for a Claude Code agent

Read this file if the user asks you to install the viewers. Do the steps in sequence. A person can also do these steps by hand.

The viewers are `mdview`, `pyview`, `pdfview`, `pageview`, `plateview` and `texview`. Each viewer shows a type of file in the browser. The user writes comments on the file, and Claude answers them.

The viewers are a work in progress, with version tags from `v0.1`. They change frequently, and a function can change or stop. Tell the user this when you tell the plan.

At the end of the procedure, these conditions apply:

- The folder `~/tools/viewers` is a clone of this repository, or a link to a clone.
- The six commands are in `~/.local/bin`, and this folder is in `PATH`.
- The skills of the viewers are in the skills folder of Claude Code.
- The external tools that the user needs are available.
- Each viewer opens a sample file with no error.

## Platforms

The viewers are made and tested on macOS. They use only the Python standard library and POSIX functions. Thus, they move easily to Linux.

**Disclaimer:** nobody tested the viewers on Linux or on Windows. The steps in this section are a guide, not a tested procedure.

On Linux:

- On Linux, use the same installer. It does not make the macOS apps that open files from Finder.
- Install the external tools with the package manager of Linux, for example `sudo apt install pandoc poppler-utils latexmk`.

Native Windows needs changes in the code:

- Replace the file locks of `fcntl` with `msvcrt.locking`, or with a lock file.
- Replace the process groups (`start_new_session` and `os.killpg`) with `CREATE_NEW_PROCESS_GROUP` and `taskkill /T`.
- Write a PowerShell installer to replace `install.sh`. In a Windows virtual environment, the interpreter is `.venv\Scripts\python.exe`, not `.venv/bin/python`.

On Windows, the easy solution is WSL (Windows Subsystem for Linux). Install the viewers in WSL as on Linux. WSL 2 forwards `localhost` to Windows, so the links open in the browser of Windows. If the browser does not open, copy the link that the command prints.

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
| The system is Linux, or Linux in WSL. | Tell the user that the viewers are tested only on macOS, but that they move easily to Linux. Ask if you must continue. In step 4, use the package manager of Linux (see [Platforms](#platforms)). |
| The system is Windows without WSL (`uname` shows `MINGW`, `MSYS` or `CYGWIN`, or it is not available). | Stop. Tell the user to use WSL, or to change the code first (see [Platforms](#platforms)). |
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

## Claude Code accounts in the chat

The chat and the Oracle run Claude Code with an account. An account is a configuration folder of Claude Code, with its own login and its own conversations:

- `~/.claude` is the account `claude`.
- Each folder `~/.claude-NAME` is the account `claude-NAME`, for example `~/.claude-team`. The viewers find the folder if it contains `projects`, `.claude.json` or `settings.json`. To make an account, run `CLAUDE_CONFIG_DIR=~/.claude-team claude` and log in.
- Each model of a local Ollama server is the account `ollama:MODEL`. The `README.md` of `viewkit` tells how to add the models of an SSH host.

**One account:** the chat shows no selector. All the runs use `~/.claude`. If the shell that started the server had the variable `CLAUDE_CONFIG_DIR`, the runs use that folder.

**Two accounts or more:** a selector shows before the **New** button of the chat. The selection applies to the chat and to the Oracle of that document. The viewers find the account of a document in this sequence:

1. The account that you selected for this document.
2. The last account that you selected, in all the documents. Thus, a selection is also the default of the documents that have no selection.
3. The folder in `CLAUDE_CONFIG_DIR` of the shell that started the server.
4. `~/.claude`.

The selections are in `~/.config/viewkit/accounts.json`, and all the viewers use this file. A new selection applies from the next run (a chat message or an answer of the Oracle). You can change the account during a conversation. The viewers then copy the conversation to the new account, and the conversation continues.

## Art in the margins

`mdview` and `pageview` can show pale figures in the free margins at the left and the right of the page. The figures are your own images. The repository has no art. Thus, after the installation, the margins are empty.

To add art:

1. Make the folder `~/tools/viewers/viewkit/art/figures`. Git ignores the folder `art`. Thus, your images stay out of the repository. To keep the art in a different folder, set the variable `VIEWKIT_ART` to the folder that contains `figures`. Then restart the servers: `mdview --restart` and `pageview --restart`.
2. Put each figure in `figures` as a PNG file with a transparent background. The viewer uses only the shape of the figure. It paints the shape in the accent color of the page. A line drawing or a silhouette gives the best result.
3. Give each file a name like `oNNN-NN-mask.png`, for example `o001-01-mask.png`. The scores (see below) work only with names like this.
4. Write the file `figures/manifest.json`. It is a list with one item for each figure: `id`, `kind` (`"organism"`), `w` and `h` (the size in pixels), and `mask` (the file name). This script writes the manifest for all the files `*-mask.png` in the folder:

   ```bash
   cd ~/tools/viewers/viewkit/art/figures && python3 - <<'EOF'
   import json, struct, pathlib
   man = []
   for p in sorted(pathlib.Path(".").glob("*-mask.png")):
       w, h = struct.unpack(">II", p.read_bytes()[16:24])  # the size in the PNG header
       man.append({"id": p.name[:-len("-mask.png")], "kind": "organism", "w": w, "h": h, "mask": p.name})
   pathlib.Path("manifest.json").write_text(json.dumps(man, indent=1))
   EOF
   ```

5. Reload the page. The viewers read the folder at each page load.

On the page, a click on a figure adds 1 to its score, and a double click subtracts 1. A figure with a higher score shows more frequently. The scores are in `figures/scores.json`, and all the viewers share them. You can also ask Claude Code to make the folder from your images.

## Updates

```bash
~/tools/viewers/install.sh --update
```

The update keeps the commits of the user and merges them with the new commits. If the commits conflict, the script tells the user to merge them with `git -C ~/tools/viewers pull`.
