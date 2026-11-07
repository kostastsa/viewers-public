# Principles of the viewers

The design rules that you prefer, for all the viewers. The `improve` skill reads this file before each proposal. After you accept a proposal with feedback, or reject one with a reason, Claude can add a rule here or change one. You can also edit this file yourself. Rules for one app only go into `APP/PRINCIPLES.md`.

Each rule has its source: a commit, or a note (`APP#N`). A proposal against a rule must give the reason in its summary.

## Interface

No rules yet. Claude adds them from your decisions.

## Code

1. **A feature that more than one app needs goes into `viewkit`. Do not write it two times.** The document model, the comment format and the security rules stay in the app. Source: `viewkit/README.md`.
2. **Standard library only for Python.** No install step. Source: `viewkit/README.md`.
3. **Each feature has a test in the browser test of the app.** Source: the tests of the viewers.

## Text

No rules yet.
