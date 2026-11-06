#!/usr/bin/env bash
# Install the viewers (mdview, pyview, pdfview, pageview, plateview) and their Claude Code skills.
#
#   git clone https://github.com/kostastsa/viewers ~/tools/viewers     # all the viewers in one repository
#   ~/tools/viewers/install.sh
#
# The script works in two layouts:
#   - the repository viewers: the viewers and viewkit are folders of one clone, in any place. The script links
#     ~/tools/APP to each folder, because the skills use the paths ~/tools/APP.
#   - separate repositories: viewkit is a clone in ~/tools/viewkit. The script clones the viewers that are
#     missing into ~/tools, from the same GitHub account as viewkit.
#
# The script:
#   1. finds or clones the viewers, and makes the links in ~/tools
#   2. links each command into ~/.local/bin (mdview, pyview, pdfview, pageview, plateview)
#   3. links the skills of viewkit/skills into the skills folder of Claude Code
#   4. builds the small macOS apps that open files from Finder (macOS only)
#   5. builds the shared Python interpreter of pyview (numpy, pandas, matplotlib, ...)
#   6. tells you which external tools it cannot find (pandoc, poppler, claude, gh)
# You can run it again. It changes only what is not correct.
#
# Options:
#   --update            also pull the new commits (only a clean checkout on its branch)
#   --no-apps           do not build the macOS apps
#   --no-venv           do not build the interpreter of pyview (you can run `pyview --setup` later)
#   --config-dir DIR    link the skills into DIR/skills (default: $CLAUDE_CONFIG_DIR or ~/.claude). Use it again
#                       for a second Claude Code configuration folder (then give the default folder too).
#   --maintainer        also link the `improve` skill, if it is in viewkit/skills (the loop of the
#                       self-improving apps, for the owner of the repositories)
set -euo pipefail

APPS="mdview pyview pdfview pageview plateview"
USER_SKILLS="answer-comments answer-cells answer-pdf answer-page page plate"

KIT="$(cd "$(dirname "$0")" && pwd -P)"
ROOT="$(dirname "$KIT")"
TOOLS="$HOME/tools"
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
skills="$USER_SKILLS"; [ $maintainer = 1 ] && skills="$skills improve"

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
  echo "install.sh: viewkit must be in ~/tools/viewkit (it is in $KIT)." >&2
  echo "Move it: mkdir -p ~/tools && mv \"$KIT\" ~/tools/viewkit" >&2
  exit 1
fi

pull() {  # pull <folder>: fast-forward a clean checkout
  local d="$1" b
  b="$(git -C "$d" branch --show-current)"
  if [ -n "$(git -C "$d" status --porcelain --untracked-files=no)" ] || [ -z "$b" ]; then
    warn "$(basename "$d"): not pulled (it has changes, or no branch)"; return
  fi
  git -C "$d" pull -q --ff-only && say pulled "$(basename "$d") ($b)" || warn "$(basename "$d"): pull failed"
}

# ------------------------------------------------------------------------------------------------ 1. repositories
if [ $layout = mono ]; then
  echo "== The repository viewers in $ROOT, and the links in $TOOLS"
  if [ $update = 1 ]; then pull "$ROOT"; fi
  mkdir -p "$TOOLS"
  for a in viewkit $APPS; do
    src="$ROOT/$a" dst="$TOOLS/$a"
    [ -d "$src" ] || { warn "$src is missing"; continue; }
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then say ok "$a"
    elif [ -L "$dst" ] || [ ! -e "$dst" ]; then ln -sfn "$src" "$dst"; say linked "~/tools/$a"
    else warn "$dst is a folder of its own, so the commands and the skills use it, not $src."; fi
  done
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
need gh        "only for notes to the apps (optional)"  "brew install gh"

echo "Done. Open a file, for example: mdview ~/tools/mdview/samples/review.md"
