#!/bin/sh
# Usage: tools/tests/debloat-test.sh
# Runs tools/debloat.sh and tools/restore.sh against a fake adb that records each call; never calls a real adb.
set -eu
tools=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/bin"
cat > "$work/bin/adb" <<'ADB'
#!/bin/sh
printf '%s|' "$@" >> "$ADB_LOG"
echo >> "$ADB_LOG"
ADB
chmod +x "$work/bin/adb"
export PATH="$work/bin:$PATH" TV_IP=10.0.0.2 ADB_LOG="$work/adb.log"

printf '# a comment\n\ncom.example.ads\r\nx; rm -rf /\ncom.android.systemui\ncom.example.kept\n' > "$work/list"
printf 'com.example.kept\r\n' > "$work/never"

failed=0
check() {
    if grep -qxF -- "$2" "$ADB_LOG"; then found=yes; else found=no; fi
    if [ "$found" = "$1" ]; then echo "ok: $3"; else echo "FAIL: $3"; failed=1; fi
}

"$tools/debloat.sh" "$work/list" "$work/never" 2> "$work/err"
check yes 'connect|10.0.0.2:5555|' "adb connects to TV_IP"
check yes '-s|10.0.0.2:5555|shell|pm|disable-user|--user|0|com.example.ads|' "a CRLF line reaches pm disable-user"
check no '-s|10.0.0.2:5555|shell|pm|disable-user|--user|0|com.android.systemui|' "a core package is skipped"
check no '-s|10.0.0.2:5555|shell|pm|disable-user|--user|0|com.example.kept|' "a never-disable package is skipped"
if grep -q 'rm -rf' "$ADB_LOG"; then echo "FAIL: a bad line reached adb"; failed=1; else echo "ok: a bad line never reaches adb"; fi
grep -qxF 'rejected (not a package name): x; rm -rf /' "$work/err" && echo "ok: the bad line is reported" || { echo "FAIL: the bad line is not reported"; failed=1; }
grep -qxF 'skipped (protected): com.android.systemui' "$work/err" && echo "ok: the skipped package is reported" || { echo "FAIL: the skipped package is not reported"; failed=1; }
[ "$(grep -c 'disable-user' "$ADB_LOG")" -eq 1 ] && echo "ok: one disable call" || { echo "FAIL: not exactly one disable call"; failed=1; }

: > "$ADB_LOG"
"$tools/restore.sh" "$work/list" 2> /dev/null
check yes '-s|10.0.0.2:5555|shell|pm|enable|com.example.ads|' "restore enables a CRLF line"
if grep -q 'rm -rf' "$ADB_LOG"; then echo "FAIL: restore passed a bad line to adb"; failed=1; else echo "ok: restore rejects a bad line"; fi

exit "$failed"
