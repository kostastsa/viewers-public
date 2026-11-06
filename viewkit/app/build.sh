#!/bin/bash
# Build NAME.app in ~/Applications: a small macOS app that opens files with the command NAME when you double-click
# them in Finder. It has no window and no Dock icon. It gives the files to ~/.local/bin/NAME, so that link must exist.
# Run again to rebuild. Each viewer calls this from its own app/build.sh with its document types:
#
#   build.sh NAME ICON TYPE_NAME UTI EXT [EXT ...]
#     NAME       the command and the app name (mdview, pyview, pdfview)
#     ICON       the icon in ../web/icons: md, py or pdf (ICON-1024.png)
#     TYPE_NAME  the name of the document type (Markdown, Python script, PDF document)
#     UTI        the uniform type identifier of the files (net.daringfireball.markdown, public.python-script, com.adobe.pdf),
#                or more than one, separated by commas (net.daringfireball.markdown,public.json)
#     EXT        the file extensions (md markdown, py, pdf)
set -euo pipefail
if [ $# -lt 5 ]; then sed -n '2,13p' "$0"; exit 2; fi
NAME=$1; ICON=$2; TYPE_NAME=$3; UTI=$4; shift 4
KIT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/$NAME.app"
CMD="$HOME/.local/bin/$NAME"
[ -x "$CMD" ] || { echo "$NAME: $CMD is missing. Link it first, e.g.: ln -s ~/tools/$NAME/$NAME.py $CMD" >&2; exit 1; }
mkdir -p "$HOME/Applications"
rm -rf "$APP"

# Finder starts apps without the PATH of the shell, so the command is called by its full path.
# With no files (the app opened by itself), it opens your home folder.
osacompile -o "$APP" - <<APPLESCRIPT
on open theFiles
	set args to ""
	repeat with f in theFiles
		set args to args & " " & quoted form of POSIX path of f
	end repeat
	do shell script "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin; ${CMD}" & args & " >/dev/null 2>&1"
end open

on run
	do shell script "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin; ${CMD} \"\$HOME\" >/dev/null 2>&1"
end run
APPLESCRIPT

# The document types, no Dock icon, and an identifier of its own.
PL="$APP/Contents/Info.plist"
# osacompile already writes some of these keys (a document type for all files): delete them first.
for k in LSUIElement CFBundleIdentifier CFBundleDocumentTypes; do /usr/libexec/PlistBuddy -c "Delete :$k" "$PL" 2>/dev/null || true; done
args=(-c "Add :LSUIElement bool true"
      -c "Add :CFBundleIdentifier string local.$NAME.app"
      -c "Add :CFBundleDocumentTypes array"
      -c "Add :CFBundleDocumentTypes:0 dict"
      -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string $TYPE_NAME"
      -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer"
      -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate"
      -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array"
      -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array")
i=0
IFS=',' read -ra UTIS <<< "$UTI"
for u in "${UTIS[@]}"; do args+=(-c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:$i string $u"); i=$((i + 1)); done
i=0
for ext in "$@"; do args+=(-c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:$i string $ext"); i=$((i + 1)); done
# PlistBuddy of macOS 26 stops (abort, exit 134) with about 16 commands in one call: give them 8 at a time.
for ((j = 0; j < ${#args[@]}; j += 16)); do /usr/libexec/PlistBuddy "${args[@]:j:16}" "$PL" >/dev/null; done

# The icon: the tab icon of the viewer, as viewer.icns. The icon of osacompile is in Assets.car (CFBundleIconName), and
# macOS uses that before CFBundleIconFile, so it goes.
PNG="$KIT/web/icons/$ICON-1024.png"
if [ -f "$PNG" ]; then
  SET="$(mktemp -d)/$NAME.iconset"
  mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$PNG" --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) "$PNG" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/viewer.icns"
  rm -rf "$(dirname "$SET")"
  /usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" -c "Set :CFBundleIconFile viewer" "$PL"
  rm -f "$APP/Contents/Resources/Assets.car"
fi

codesign --force --sign - "$APP" >/dev/null 2>&1 || true
touch "$APP"  # so Finder shows the new icon
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
echo "Installed $APP"
echo "Finder: right click a .$1 file > Open With > $NAME. To make $NAME the default: Get Info > Open with > $NAME > Change All…"
