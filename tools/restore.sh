#!/bin/sh
# Usage: TV_IP=<tv address> tools/restore.sh <package list>
# Enables each package in the list again (the undo of tools/debloat.sh).
set -eu
[ $# -eq 1 ] || { echo "usage: TV_IP=<tv address> $0 <package list>" >&2; exit 2; }
: "${TV_IP:?set TV_IP to the TV address}"
[ -r "$1" ] || { echo "cannot read $1" >&2; exit 2; }

# Prints the package names of a list. Blank and # lines are skipped; any other line that is not a package name is reported and skipped.
packages() {
    tr -d '\r' < "$1" | while read -r line; do
        case "$line" in '' | '#'*) continue ;; esac
        if printf '%s\n' "$line" | grep -Eq '^[A-Za-z0-9._]+$'; then
            printf '%s\n' "$line"
        else
            printf 'rejected (not a package name): %s\n' "$line" >&2
        fi
    done
}

adb connect "$TV_IP:5555"
packages "$1" | while read -r pkg; do
    adb -s "$TV_IP:5555" shell pm enable "$pkg" < /dev/null
done
