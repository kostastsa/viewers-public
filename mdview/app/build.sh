#!/bin/bash
# Build mdview.app in ~/Applications, which opens files with mdview when you double-click them in Finder.
# The builder is shared: ../../viewkit/app/build.sh. Run again to rebuild.
exec "$(dirname "$0")/../../viewkit/app/build.sh" mdview md Markdown net.daringfireball.markdown md markdown
