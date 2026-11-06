#!/bin/bash
# Build pdfview.app in ~/Applications, which opens files with pdfview when you double-click them in Finder.
# The builder is shared: ../../viewkit/app/build.sh. Run again to rebuild.
exec "$(dirname "$0")/../../viewkit/app/build.sh" pdfview pdf 'PDF document' com.adobe.pdf pdf
