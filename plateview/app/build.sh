#!/bin/bash
# Build plateview.app in ~/Applications. It puts plateview in Finder's Open With menu (right click) for .html files:
# the file becomes a plate that may read its own folder. plateview never becomes the default app of .html files
# unless you choose Change All… The builder is shared: ../../viewkit/app/build.sh. Run again to rebuild.
exec "${PLATEVIEW_KIT:-$(dirname "$0")/../../viewkit}/app/build.sh" plateview plate 'HTML plate' public.html html htm "$@"
