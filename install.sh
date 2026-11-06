#!/usr/bin/env bash
# Install all the viewers and their Claude Code skills. The installer is viewkit/install.sh (see --help).
exec "$(cd "$(dirname "$0")" && pwd)/viewkit/install.sh" "$@"
