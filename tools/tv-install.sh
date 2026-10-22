#!/bin/sh
# Usage: TV_IP=<tv address> tools/tv-install.sh <apk>
# Installs the APK on the TV over adb, then turns its remote-button accessibility service on again.
set -eu
[ $# -eq 1 ] || { echo "usage: TV_IP=<tv address> $0 <apk>" >&2; exit 2; }
: "${TV_IP:?set TV_IP to the TV address}"
adb connect "$TV_IP:5555"
adb -s "$TV_IP:5555" install -r "$1"
adb -s "$TV_IP:5555" shell settings put secure enabled_accessibility_services hermestv.launcher/.NetflixKeyService
adb -s "$TV_IP:5555" shell settings put secure accessibility_enabled 1
