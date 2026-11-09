#!/bin/bash
# Build pageview.app in ~/Applications. It puts pageview in Finder's Open With menu (right click) for pages and data
# files: .md, .markdown, .csv, .tsv and .json. It never becomes the default app of a type: .md files stay with mdview
# unless you choose Change All… The builder is shared: ../../viewkit/app/build.sh. Run again to rebuild.
exec "${PAGEVIEW_KIT:-$(dirname "$0")/../../viewkit}/app/build.sh" pageview page 'Page or data file' \
  net.daringfireball.markdown,public.comma-separated-values-text,public.tab-separated-values-text,public.json \
  md markdown csv tsv json "$@"
