---
name: improve
description: Run the loop of a self-improving app (the viewers in ~/tools: pdfview, mdview and pyview). Read the notes to the app (GitHub issues with the label `note`), make one tested proposal on a branch for each note, ask a question, or close the note. Then accept or reject a proposal when the user decides, and update the principles. Use when the user runs /improve, asks to "work on the notes" or "improve" an app, or accepts or rejects a proposal (improve/note-N).
---

# Improve an app from its notes

If `~/tools/viewers/viewkit/docs/self-improving-apps.md` exists, read it the first time in a session: it is the full protocol. The short form:

- A **note** is an issue with the label `note`: a GitHub issue of the repository of the app, or an issue of the local tracker (section "The tracker and the layout"). The user writes it in the dashboard of the app (⌘/Ctrl+Shift+.). Claude writes it with `APP note "..."`.
- A **proposal** is one change for one note, on the branch `improve/note-N` (N is the issue number), with passed tests.
- The **principles** are the design rules of the user: `~/tools/viewers/viewkit/PRINCIPLES.md` and `~/tools/viewers/APP/PRINCIPLES.md`.
- Only the user puts a change into the app.
- **The automatic loop** (version 2, with viewkit changes since version 3): the user writes notes in the dashboard of the app (⌘/Ctrl+Shift+.) and clicks Send all. Each note starts a background run (`~/tools/viewers/viewkit/improverun.py`). The run calls this skill in the modes `implement`, `change` and `learn` (section "Background modes"). Then it runs the tests and opens a preview tab with Accept, Change… and Reject. The run, not Claude, pushes, merges and closes the issue. The interactive commands below stay for the notes from Claude and for the notes that the automatic loop did not finish (label `question`).

## The tracker and the layout

- **The tracker.** The notes are GitHub issues when the `gh` user may push to the repository. Else they are issues of the local tracker, `~/tools/viewers/viewkit/localgh.py`, with JSON files in `~/.local/share/viewkit/issues/`. The local tracker knows the `gh` commands of this skill: `issue view`, `issue comment`, `issue edit`, `issue close` and `issue list`.
  - In the background modes, `gh` is already the correct tracker.
  - In the interactive commands, run `python3 ~/tools/viewers/viewkit/localgh.py mode ~/tools/viewers/APP`. If it prints `local`, use `~/tools/viewers/viewkit/bin/gh` in place of `gh`, and do not push.
- **The layout.** Each app is a repository of its own next to viewkit, or all the apps and viewkit are folders of one repository (`viewers`, the public repository).
  - In `viewers`, a proposal has one worktree of the whole repository, `~/tools/wt/note-APP-N/viewers`, on one branch `improve/note-N`. The app is the folder `APP` of this worktree, and the kit is its folder `viewkit`.
  - The interactive steps of "Make a proposal", "Accept" and "Reject" are for separate repositories. In `viewers`, use the dashboard of the app: the automatic loop does these steps.

## Commands

| Command | What to do |
|---|---|
| `/improve APP` | Triage the open notes of APP and make the proposals (section "Triage"). |
| `/improve status APP` | List the notes and the proposals with their labels. Change nothing. |
| `/improve try APP N [FILE]` | Start the sandbox of the proposal of note N: `APP try N FILE`. Give the link to the user. |
| `/improve accept APP N` | Ship the proposal of note N (section "Accept"). |
| `/improve reject APP N "REASON"` | Reject the proposal of note N (section "Reject"). |
| `/improve implement APP N` | Background only (the run starts it, in the worktree of note N). Section "Background modes". |
| `/improve change APP N` | Background only: change the proposal from the feedback of the preview. |
| `/improve learn APP N REPO DECISION` | Background only: update the principles after a decision in the preview. |

APP is a folder in `~/tools/viewers` (for example `pdfview`). The repository is the `origin` of `~/tools/viewers/APP`. Find it with `gh repo view --json nameWithOwner -q .nameWithOwner` in that folder.

## Background modes

The run of the automatic loop starts these modes with `claude -p`. Nobody reads the chat. Do not ask the user anything in the chat. Your last message is the only output: the run puts it on the issue and in the preview tab.

### implement APP N

The working directory is the worktree of the app for note N (`~/tools/viewers/wt/note-APP-N/APP`, branch `improve/note-N`). Next to it, `../viewkit` is a worktree of the kit (branch `improve/APP-note-N`). In `viewers`, `../viewkit` is the folder viewkit of the same worktree. The app imports this kit. You may edit files only in these two folders.

1. Read the note: `gh issue view N --json title,body,comments`. The body has a **scope** line:
   - **Scope: all the viewers.** Put the change into `../viewkit` when the feature is shared (the chat panel, the dashboard, the tab, the kit styles), so that all the viewers get it. Change the app only for its own wiring.
   - **Scope: only APP.** Change only the app. If the app needs a new part of the kit, make it an option. The other viewers do not use the option, so they do not change.
   - No scope line (an old note): use "all the viewers" for a shared feature, else "only APP".
2. Read `../viewkit/PRINCIPLES.md`, `PRINCIPLES.md` if it exists, `README.md`, `improve.json` (the tests), and the `CLAUDE.md` files that apply.
3. **Decide.** Do not edit in these cases:
   - The note is not clear, or it has more than one good solution.
   - The note touches the security rules.
   - "All the viewers" needs a change in another app repository (mdview, pyview, pageview, plateview). These are not in your worktrees. 
   In these cases, write the question on the issue (`gh issue comment N --body-file -`, with two to four options). Then end with a last message that starts with `QUESTION:` and repeats the question.
   Read the comments first: an "Answer from the dashboard" answers your earlier question. Then do what it says.
   If `git log main..HEAD` here or in `../viewkit` shows commits, an earlier run started the work: continue from it.
4. Make the change. One change only. Keep the diff small. Follow the principles and the style of the code. Update the README in STE if the behavior changes.
5. Add a test for the change in the test file of the app. For a bug, see the test fail before the fix.
6. Run the tests of `improve.json`. The run tests again after you. If the kit changed, the run also tests all the other viewers against the new kit (`../viewkit/improve.json`, "apps"). A failed test stops the proposal. For a kit change, update `../viewkit/README.md` in STE.
7. Commit in the app: `git add -A` and `git commit -m "CHANGE" -m "Note: #N"`. Do not commit in `../viewkit`: the run commits it (you cannot run git there). In `viewers`, `git add -A` in the app also adds the changes in `../viewkit`, and that is correct. Do not push. Do not change labels. Do not close the issue.
8. **Last message:** the summary for the preview, in STE, three short lines or fewer: what changed, where to see it in the app, and "all the viewers" if the kit changed. Plain text, no heading. The first line is also the commit message, so make it a short title.

### change APP N

The same folder and rules as `implement`. The last comment on the issue, "Feedback from the preview", tells what to change.

1. Read the issue with its comments, and `git log --oneline main..HEAD` and `git diff main...HEAD` (the proposal so far).
2. Make the change that the feedback asks for. Update the test. Run the tests. Commit (`Note: #N`).
3. **Last message:** the summary of the whole proposal now (not only of this change), three short lines or fewer. If the feedback is not clear, do not edit, and end with `QUESTION:` and the question.

### learn APP N REPO DECISION

The working directory is `~/tools/viewers/viewkit`. DECISION is `accepted after feedback` or `rejected`.

1. Read the issue with its comments: `gh issue view N -R REPO --json title,body,comments`. The feedback and the reason of the user are there.
2. If they show a general preference of the user, add one rule to the principles, or change one, with the source `APP#N`: `~/tools/viewers/viewkit/PRINCIPLES.md` for all the viewers, or `~/tools/viewers/APP/PRINCIPLES.md` for one app (make the file if it does not exist, with a heading and the same format). Use STE. Keep the numbering.
3. If there is nothing general to learn, change nothing.
4. Do not commit: the run commits and pushes. **Last message:** the new rule, or "Nothing to learn."

## Triage

1. **Read the rules.** Read `~/tools/viewers/viewkit/PRINCIPLES.md`, `~/tools/viewers/APP/PRINCIPLES.md` if it exists, the `README.md` of the app (its tests), `~/tools/viewers/viewkit/README.md`, and the `CLAUDE.md` files that apply.
2. **Read the inbox.** Run `gh issue list -R REPO --label note --state open --json number,title,body,labels,comments`. Skip a note with the label `proposed` (it waits for the user) unless it has a new comment from the user. Read the comments of each note: a note with the label `question` or `rejected` can have an answer or a reason from the user.
3. **Join the duplicates.** If two notes are about the same problem, comment on the newer one "Same as #M", close it, and work on #M.
4. **Decide for each note.** Use the principles. Do one of these:
   - **Proposal:** the change is clear, and it obeys the principles. Go to "Make a proposal".
   - **Question:** there is more than one good solution, or the change is against a principle, or it touches the security rules (access key, grants, the files that the server may read, Content-Security-Policy). Comment on the issue with the question and two to four options. Add the label `question`.
   - **No change:** the note is not a problem of the app, or a principle says no. Comment with the reason, and close the issue with `--reason "not planned"`.
5. **Report** in the chat: one line for each note, with the decision. For each proposal, give the summary and the commands `/improve accept APP N` and `/improve reject APP N "reason"`.

Work on at most five notes in one triage. Start with the bugs, then the notes that have the most duplicates.

## Make a proposal

1. **Make the worktree.** The live checkout serves the running app, and each change of a file restarts the server. Never edit it.
   ```
   W=~/tools/viewers/wt/note-APP-N
   mkdir -p "$W"
   git -C ~/tools/viewers/APP fetch origin
   git -C ~/tools/viewers/APP worktree add -b improve/note-N "$W/APP" origin/main
   ln -s ~/tools/viewers/viewkit "$W/viewkit"
   ```
   If the change is in `viewkit`, make a viewkit worktree in place of the symlink:
   `git -C ~/tools/viewers/viewkit worktree add -b improve/APP-note-N "$W/viewkit" origin/main`.
2. **Write a failing test first** for a bug, in the test file of the app (or of each app for a viewkit change). Run it, and see it fail.
3. **Make the change.** One change only. Keep the diff small. Follow the principles and the style of the code. Update the README in STE if the behavior changes.
4. **Run the tests** of the app, from the worktree. They are in the README of the app. For a viewkit change, run the tests of all the apps (`viewkit/README.md`), with the app worktrees or symlinks next to the viewkit worktree. If a test fails and you cannot fix it, do not make a proposal. Comment on the issue with what you found, and add the label `question`.
5. **Commit** in the worktree. The message starts with the change, and ends with `Note: #N` and the attribution lines of the session.
6. **Push the branch:** `git -C "$W/APP" push -u origin improve/note-N` (and the viewkit branch, if there is one).
7. **Comment on the issue** with the summary: what changed, why, how to try it (three lines or fewer), the tests that passed, and the branch. Add the label `proposed`.
8. **Give the sandbox command** in the report: `APP try N PAPER` (for pdfview: `pdfview try N PAPER.pdf`). It starts the version of the proposal on port 8900+N, with copies of the files and their comments in `$W/sandbox-note-N/`. Without a file, it opens the test file of the app. The user tries the proposal there before the decision.

## Feedback on a proposal

The user tries the proposal and asks for more changes in the chat, before the decision. Make them on the same branch, in the same worktree, as a new commit (`Note: #N`). Do not make a new note. Run the tests again, push, and comment on the issue "Proposal changed" with the new commit and what changed. Then tell the user to try it again: stop the sandbox with `APP try --stop N` and start it again.

## Accept

The user accepts with `/improve accept APP N`.

1. Check that the live checkout `~/tools/viewers/APP` is on `main` and has no changes that are not committed. If it has some, stop and tell the user. Stop the sandbox: `APP try --stop N`.
2. In the worktree, rebase on `origin/main` if `main` moved. Then run the tests again.
3. Merge with a fast-forward merge: `git -C ~/tools/viewers/APP merge --ff-only improve/note-N`. The running server restarts only when a `.py` file changes. The page files (`viewer.js`, `viewer.html`) come from the disk at each request, so tell the user to reload the open tabs. For a viewkit change, merge viewkit first, then the app.
4. Push `main`. Comment on the issue "Shipped in COMMIT", and close it.
5. Remove the worktree and the branch: `git -C ~/tools/viewers/APP worktree remove "$W/APP"`, `git -C ~/tools/viewers/APP branch -d improve/note-N`, `git -C ~/tools/viewers/APP push origin --delete improve/note-N`, then delete the `$W` folder with the sandbox copies (remove the symlink, not the kit).
6. **Learn.** If the decision shows a preference, add a rule to the principles or change one, with the source `APP#N`. A change in only one app goes into `APP/PRINCIPLES.md`. Commit the principles in their repository and push.
7. Tell the user to run `git -C ~/tools/viewers/APP pull` on the other computer.

## Reject

The user rejects with `/improve reject APP N "REASON"`.

1. Comment on the issue "Rejected: REASON". Remove the label `proposed`, and add `rejected`.
2. Stop the sandbox (`APP try --stop N`). Remove the worktree, the folder `$W` with the sandbox, and the branch (local and on GitHub).
3. **Learn.** Add or change a rule in the principles from the reason, with the source `APP#N`. Commit and push.
4. Ask the user one question: close the note, or keep it open for a different proposal. If the user does not answer, keep it open. The next triage reads the reason.

## Rules

- Never edit the live checkout of an app or of viewkit, except for the merge in "Accept" and the principles.
- One proposal contains one change and one note (or one group of duplicates).
- A proposal with a failed test does not go to the user.
- Never copy the text or the path of a document into an issue or a commit.
- Changes to the security rules are always a question, never a proposal.
- Write the comments on the issues, the README text and the principles in STE.
