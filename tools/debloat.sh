#!/bin/sh
# Usage: TV_IP=<tv address> tools/debloat.sh <package list> [never-disable list]
# Disables each package in the list for user 0. Undo with tools/restore.sh and the same list.
# It never uninstalls a package: pm disable-user is reversible, pm uninstall on a system app is not.
# It skips the core packages below and every package in the never-disable list: without them the TV may not start.
set -eu
[ $# -eq 1 ] || [ $# -eq 2 ] || { echo "usage: TV_IP=<tv address> $0 <package list> [never-disable list]" >&2; exit 2; }
: "${TV_IP:?set TV_IP to the TV address}"
for f in "$@"; do [ -r "$f" ] || { echo "cannot read $f" >&2; exit 2; }; done

CORE="android com.android.systemui hermestv.launcher com.google.android.gms com.google.android.gsf
com.android.vending com.google.android.packageinstaller com.android.packageinstaller
com.android.permissioncontroller com.android.providers.settings com.android.tv.settings com.android.settings"

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

protected=$(printf '%s\n' $CORE)
if [ $# -eq 2 ]; then
    protected=$(printf '%s\n%s\n' "$protected" "$(packages "$2")")
fi

adb connect "$TV_IP:5555"
packages "$1" | while read -r pkg; do
    if printf '%s\n' "$protected" | grep -qxF -- "$pkg"; then
        printf 'skipped (protected): %s\n' "$pkg" >&2
    else
        adb -s "$TV_IP:5555" shell pm disable-user --user 0 "$pkg" < /dev/null
    fi
done
