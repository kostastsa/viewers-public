#!/usr/bin/env bash
# Install the viewers (mdview, pyview, pdfview, pageview, plateview, texview) and their Claude Code skills.
#
#   git clone https://github.com/kostastsa/viewers-public ~/tools/viewers     # all the viewers in one repository
#   ~/tools/viewers/install.sh
#
# The script works in two layouts:
#   - the repository viewers: the viewers and viewkit are folders of one clone, in any place. If the clone is not
#     ~/tools/viewers, the script links ~/tools/viewers to it, because the skills use the paths ~/tools/viewers/APP.
#   - separate repositories: viewkit is a clone in ~/tools/viewers/viewkit. The script clones the viewers that are
#     missing into ~/tools/viewers, from the same GitHub account as viewkit.
#
# The script:
#   1. finds or clones the viewers, and makes the link ~/tools/viewers if necessary
#   2. links each command into ~/.local/bin (mdview, pyview, pdfview, pageview, plateview, texview)
#   3. links the skills of viewkit/skills into the skills folder of Claude Code
#   4. builds the small macOS apps that open files from Finder (macOS only)
#   5. builds the shared Python interpreter of pyview (numpy, pandas, matplotlib, ...)
#   6. tells you which external tools it cannot find (pandoc, poppler, latexmk, claude, gh)
# You can run it again. It changes only what is not correct.
#
# Options:
#   --update            also pull the new commits (only a clean checkout on its branch). In the repository viewers,
#                       your own commits (accepted proposals) stay: the update merges them with the new commits.
#   --no-apps           do not build the macOS apps
#   --no-venv           do not build the interpreter of pyview (you can run `pyview --setup` later)
#   --config-dir DIR    link the skills into DIR/skills (default: $CLAUDE_CONFIG_DIR or ~/.claude). Use it again
#                       for a second Claude Code configuration folder (then give the default folder too).
#   --maintainer        no effect (the `improve` skill is now linked for all users). Kept for old commands.
set -euo pipefail

APPS="mdview pyview pdfview pageview plateview texview"
USER_SKILLS="answer-comments answer-cells answer-pdf answer-page answer-tex answer-plate page plate improve"

KIT="$(cd "$(dirname "$0")" && pwd -P)"
ROOT="$(dirname "$KIT")"
TOOLS="$HOME/tools/viewers"
BIN="$HOME/.local/bin"
update=0; apps=1; venv=1; maintainer=0; configs=""

while [ $# -gt 0 ]; do
  case "$1" in
    --update) update=1 ;;
    --no-apps) apps=0 ;;
    --no-venv) venv=0 ;;
    --maintainer) maintainer=1 ;;
    --config-dir) [ $# -ge 2 ] || { echo "install.sh: --config-dir needs a folder" >&2; exit 2; }
                  configs="$configs ${2/#\~/$HOME}"; shift ;;
    -h|--help) sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install.sh: unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$configs" ] || configs="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
skills="$USER_SKILLS"

say()  { printf '%-8s %s\n' "$1" "$2"; }
warn() { printf 'WARNING  %s\n' "$1" >&2; }

# ------------------------------------------------------------------------------------------------ 0. checks
command -v git >/dev/null || { echo "install.sh: git is missing." >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null \
  || { echo "install.sh: Python 3.9 or newer is necessary (python3)." >&2; exit 1; }

# The layout: viewkit is a folder of the repository viewers (mono), or a repository of its own (separate).
top="$(git -C "$KIT" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$top" ] && [ "$(cd "$top" && pwd -P)" = "$ROOT" ]; then layout=mono; else layout=separate; fi
if [ $layout = separate ] && [ "$ROOT" != "$(mkdir -p "$TOOLS" && cd "$TOOLS" && pwd -P)" ]; then
  echo "install.sh: viewkit must be in ~/tools/viewers/viewkit (it is in $KIT)." >&2
  echo "Move it: mkdir -p ~/tools/viewers && mv \"$KIT\" ~/tools/viewers/viewkit" >&2
  exit 1
fi

pull() {  # pull <folder>: fast-forward a clean checkout
  local d="$1" b
  b="$(git -C "$d" branch --show-current)"
  if [ -n "$(git -C "$d" status --porcelain --untracked-files=no)" ] || [ -z "$b" ]; then
    warn "$(basename "$d"): not pulled (it has changes, or no branch)"; return
  fi
  if git -C "$d" pull -q --ff-only 2>/dev/null; then say pulled "$(basename "$d") ($b)"; return; fi
  if [ "${layout:-}" = mono ]; then
    # Your accepted proposals are commits on your main: merge the new commits with them.
    if git -C "$d" pull -q --no-rebase --no-edit; then say merged "$(basename "$d") ($b): your commits and the new commits"
    else git -C "$d" merge --abort 2>/dev/null; warn "$(basename "$d"): the new commits conflict with your commits. Merge by hand: git -C \"$d\" pull"; fi
    return
  fi
  warn "$(basename "$d"): pull failed"
}

# ------------------------------------------------------------------------------------------------ 1. repositories
if [ $layout = mono ]; then
  echo "== The repository viewers in $ROOT"
  if [ $update = 1 ]; then pull "$ROOT"; fi
  if [ "$ROOT" = "$(cd "$TOOLS" 2>/dev/null && pwd -P)" ]; then say ok "$ROOT"
  else
    # The skills use the paths ~/tools/viewers/APP, so ~/tools/viewers must lead to the clone.
    mkdir -p "$(dirname "$TOOLS")"
    if [ -L "$TOOLS" ] || [ ! -e "$TOOLS" ]; then ln -sfn "$ROOT" "$TOOLS"; say linked "~/tools/viewers"
    else warn "$TOOLS is a folder of its own, so the commands and the skills use it, not $ROOT."; fi
  fi
else
  # The viewers come from the same GitHub account as viewkit, so a fork installs the forks.
  origin="$(git -C "$KIT" remote get-url origin 2>/dev/null || true)"
  case "$origin" in
    *viewkit.git) base="${origin%/viewkit.git}" ;;
    *viewkit)     base="${origin%/viewkit}" ;;
    *)            base="https://github.com/kostastsa" ;;
  esac
  echo "== Repositories in $TOOLS"
  if [ $update = 1 ]; then pull "$KIT"; fi
  for a in $APPS; do
    if [ -d "$TOOLS/$a/.git" ] || [ -f "$TOOLS/$a/.git" ]; then
      say ok "$a"
      if [ $update = 1 ]; then pull "$TOOLS/$a"; fi
    elif [ -e "$TOOLS/$a" ]; then
      warn "$TOOLS/$a exists and is not a Git checkout. Skipped."
    else
      git clone -q "$base/$a" "$TOOLS/$a" && say cloned "$a" || warn "could not clone $base/$a"
    fi
  done
fi

# ------------------------------------------------------------------------------------------------ 2. commands
echo "== Commands in $BIN"
mkdir -p "$BIN"
for a in $APPS; do
  src="$TOOLS/$a/$a.py" dst="$BIN/$a"
  [ -f "$src" ] || continue
  chmod +x "$src"
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then say ok "$a"
  elif [ -L "$dst" ] || [ ! -e "$dst" ]; then ln -sf "$src" "$dst"; say linked "$a"
  else warn "$dst is a file, not a link. Remove it and run install.sh again."; fi
done
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not in PATH. Add to ~/.zprofile: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;; esac

# ------------------------------------------------------------------------------------------------ 3. skills
for cfg in $configs; do
  echo "== Skills in $cfg/skills"
  mkdir -p "$cfg/skills"
  for s in $skills; do
    src="$TOOLS/viewkit/skills/$s" dst="$cfg/skills/$s"
    [ -d "$src" ] || { warn "the skill $s is not in $TOOLS/viewkit/skills. Skipped."; continue; }
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then say ok "$s"; continue; fi
    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
      # A copy inside skills/ would load as a second skill, so the old folder goes to backups/.
      mkdir -p "$cfg/backups/pre-viewkit"
      mv "$dst" "$cfg/backups/pre-viewkit/$s.$(date +%Y%m%d%H%M%S)"
      say backup "$s (to $cfg/backups/pre-viewkit)"
    fi
    ln -sfn "$src" "$dst"; say linked "$s"
  done
done

# ------------------------------------------------------------------------------------------------ 4. macOS apps
if [ $apps = 1 ] && [ "$(uname)" = Darwin ] && command -v osacompile >/dev/null; then
  echo "== macOS apps in ~/Applications"
  for a in $APPS; do
    [ -x "$TOOLS/$a/app/build.sh" ] || continue
    if "$TOOLS/$a/app/build.sh" >/dev/null 2>&1; then say built "$a.app"; else warn "could not build $a.app (run $TOOLS/$a/app/build.sh)"; fi
  done
  echo "To open files from Finder: select a file, Get Info > Open with > the app > Change All."
fi

# ------------------------------------------------------------------------------------------------ 5. pyview interpreter
if [ $venv = 1 ] && [ -f "$TOOLS/pyview/pyview.py" ]; then
  echo "== The interpreter of pyview"
  if [ -x "$TOOLS/pyview/.venv/bin/python" ]; then say ok "$TOOLS/pyview/.venv"
  elif python3 "$TOOLS/pyview/pyview.py" --setup; then say built "$TOOLS/pyview/.venv"
  else warn "pyview --setup failed. Run it again later."; fi
fi

# ------------------------------------------------------------------------------------------------ 6. external tools
echo "== External tools"
need() {  # need <command> <what uses it> <how to install>
  if command -v "$1" >/dev/null; then say ok "$1"; else warn "$1 is missing: $2. Install: $3"; fi
}
need pandoc    "mdview and pageview need it"            "brew install pandoc"
need pdftotext "pdfview text and pdfview crop need it"  "brew install poppler"
need claude    "the chat and the Oracle button need it" "https://claude.com/claude-code"
command -v latexmk >/dev/null 2>&1 || [ -x /Library/TeX/texbin/latexmk ] || [ ! -d "$TOOLS/texview" ] \
  || warn "latexmk is missing: texview needs it to compile (brew install --cask mactex-no-gui)"
need gh        "optional: notes as GitHub issues (without it, the notes stay in the local tracker)" "brew install gh"

echo "Done. Open a file, for example: mdview ~/tools/viewers/mdview/samples/review.md"
