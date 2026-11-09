#!/bin/bash
# Build pyview.app in ~/Applications, which opens files with pyview when you double-click them in Finder.
# The builder is shared: ../../viewkit/app/build.sh. Run again to rebuild.
exec "$(dirname "$0")/../../viewkit/app/build.sh" pyview py 'Python script' public.python-script py "$@"
