#!/bin/sh
# Rebuild ../codemirror.js. Needs Node (only for this build; the viewer itself needs none).
set -e
cd "$(dirname "$0")"
npm install --no-audit --no-fund
./node_modules/.bin/esbuild entry.js --bundle --minify --format=iife --legal-comments=none --outfile=../codemirror.js
rm -rf node_modules package-lock.json
