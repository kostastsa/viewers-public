#!/bin/bash
# Build texview.app in ~/Applications, which opens .tex files with texview when you double-click them in Finder.
# The builder is shared: ../../viewkit/app/build.sh. Run again to rebuild.
exec "$(dirname "$0")/../../viewkit/app/build.sh" texview tex 'LaTeX document' org.tug.tex tex "$@"
