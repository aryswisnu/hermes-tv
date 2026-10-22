#!/bin/sh
# Usage: tools/fetch-scenery.sh
# Downloads the idle-screen photos listed in bridge/scenery/CREDITS.md from Wikimedia Commons,
# 1920 px wide, into bridge/state/scenery/<period>/. A photo that is already there is kept.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
dest="$root/bridge/state/scenery"
list=$(sed -n 's|^- \([a-z]*/[A-Za-z0-9._-]*\.jpg\) [|] https://commons\.wikimedia\.org/wiki/File:\([^ ]*\) [|] .*|\1 \2|p' \
    "$root/bridge/scenery/CREDITS.md")
[ -n "$list" ] || { echo "no photos found in bridge/scenery/CREDITS.md" >&2; exit 1; }
echo "$list" | while read -r file name; do
    [ -s "$dest/$file" ] && continue
    mkdir -p "$dest/${file%/*}"
    curl -fsSLg -A "hermes-tv-fetch-scenery/1.0" -o "$dest/$file.part" \
        "https://commons.wikimedia.org/wiki/Special:FilePath/$name?width=1920"
    mv "$dest/$file.part" "$dest/$file"
    echo "$file"
done
