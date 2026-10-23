# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Cursor, Gemini CLI and others) that open this repo.
README.md is the full guide for people. This file tells an agent how to act.

There are two kinds of work. Find out which one the user wants before you start.

- **Set up Hermes TV on the user's TV.** Follow [Setup](#setup).
- **Change the code.** Follow [Development](#development).

## Setup

You are setting up Hermes TV on the user's Android TV. Read README.md first and follow it.
In this section, "me" and "my" mean the user.

### 1. Ask first

Ask the user for these values in one message, and wait for the answers:
1. The TV's IP address (Settings > Network & Internet > the Wi-Fi network on the TV).
2. The bridge machine: this computer or another always-on Linux machine (how do you reach it?).
3. Hermes: the Hermes API server URL and key, or "no Hermes".
4. My TMDB API key or read access token.
5. My location for the weather (latitude, longitude) or "no weather", and my time zone (IANA name, the same zone as the TV).
6. My country for streaming (REGION, ISO 3166-1, for example US) and my local language (ISO 639-1, for example en).
7. My cameras: for each one (up to 4) the brand, IP address, user, password and a name, or "no cameras".

### 2. Rules

- Never print, log or commit TV_KEY, HERMES_KEY, the TMDB key or a camera password. tv/bridge.properties and tv/camera.properties are gitignored: keep it that way.
- Never run `pm uninstall` on the TV. Only `pm disable-user`, through tools/debloat.sh.
- Never forward a port on my router. Never use Tailscale Funnel.
- tools/tv-install.sh replaces the whole enabled_accessibility_services list. Before step 8, save the old value
  (`adb shell settings get secure enabled_accessibility_services`). If it holds other services, add them back after the install,
  separated by `:`, and tell me.
- Before each change on the TV, record the old value with `adb shell settings get ...` or `adb shell cmd package resolve-activity ...`.
  Write restore-my-tv.sh in the repo root (the repo's .gitignore already lists it) that puts every old value back,
  re-enables every package you disabled (tools/restore.sh) and sets the old home screen. Keep it up to date after each step.
- After each TV step, take a screenshot with `adb exec-out screencap -p > step-N.png` and look at it. Camera video is drawn on
  a separate plane, so screencap shows camera areas black: that is normal.
- Stop and ask me when a step fails twice.

### 3. Steps

1. Connect adb (README section "Connect to the TV over adb"): try `adb connect <tv-ip>:5555`. If it fails, tell me how to turn on
   USB debugging or Wireless debugging (pairing code), or ask me for a USB cable once and use `adb tcpip 5555`.
   After Wireless debugging (a random port), run `adb connect <tv-ip>:<port>`, then `adb -s <tv-ip>:<port> tcpip 5555`,
   then `adb connect <tv-ip>:5555`: the repo tools use port 5555. This lasts until the TV restarts.
   Tell me to accept "Allow USB debugging?" on the TV. Check: `adb devices` lists `<tv-ip>:5555` as `device`.
2. Bridge (README "Set it up by hand", part 1): venv, ~/.config/hermes-tv/bridge.env from bridge/bridge.env.example with my values
   (TV_KEY from `openssl rand -hex 32`), tools/fetch-scenery.sh, the systemd user units, `tailscale serve --bg --https=8444 http://127.0.0.1:8650`.
   Check: `curl -s http://127.0.0.1:8650/health` and
   `curl -s -H "Authorization: Bearer $(sed -n 's/^TV_KEY=//p' ~/.config/hermes-tv/bridge.env)" "http://127.0.0.1:8650/v1/picks?row=films"`
   (the first call can return an empty list while the bridge fills its cache: call it again after 1 minute).
3. Hermes, only if I have one (README part 2): the gate plugin and the API server. Ask me before you restart Hermes.
   Set the Hermes container's time zone to the same zone as TIME_ZONE, and check it with `docker exec <container> date`.
   Optional, only if Hermes runs in Docker on the bridge machine and I want it (ask me): README "Ask Hermes in chat".
   Set HERMES_TV_DIR=/opt/data/hermes-tv in bridge.env, restart the bridge, and install hermes-skill/hermes-tv/SKILL.md
   in the container as the owner of /opt/data, with the docker exec command in that section.
4. Cameras: build each RTSP URL from the README camera table. Test each URL with `ffprobe -rtsp_transport tcp -i <url>` before you
   write it. Write tv/camera.properties only with URLs that pass (main and sub). If a URL fails, show me the error and ask.
5. Write tv/bridge.properties (BRIDGE_URL = the bridge's https://<host>.<tailnet>.ts.net:8444, TV_KEY = the same value as in bridge.env).
6. Build: `cd tv && ./gradlew assembleRelease`. Check that tv/app/build/outputs/apk/release/app-release.apk exists.
7. Ask me to install Tailscale on the TV from the Play Store and sign in to my tailnet. Wait for my "done".
8. Install: `TV_IP=<tv-ip> tools/tv-install.sh tv/app/build/outputs/apk/release/app-release.apk` (this also turns on the remote-button service).
9. Set the home screen: `adb shell cmd package set-home-activity --user 0 hermestv.launcher/.HomeActivity`. Ask me for my "yes" first.
10. Debloat: `adb shell pm list packages` and compare with examples/tcl-g09/disabled.txt. Make my own list: keep only packages that exist on
    my TV. Ask me about each package that is not in the example list before you add it. Never add a package from
    examples/tcl-g09/never-disable.txt. Show me the final list and wait for my "yes".
    Then run `TV_IP=<tv-ip> tools/debloat.sh <my list> examples/tcl-g09/never-disable.txt`.
    Then run the set-home-activity command of step 9 again and press HOME (`adb shell input keyevent KEYCODE_HOME`). Check: the screenshot shows Hermes TV.
11. Idle screen: `adb shell settings put secure screensaver_components hermestv.launcher/.IdleDream` and
    `adb shell settings put secure screensaver_enabled 1`. Test with `adb shell service call dreams 1` (never `service call dreams 6`).
12. Ask me to run the 7-test check list in the README at the TV and tell you the result of each test.
13. Show me restore-my-tv.sh and tell me how to run it.

The user can undo every change with `restore-my-tv.sh` and `tools/restore.sh`. Tell them where both are at the end.

## Development

### Layout

| Path | What it is |
|---|---|
| `tv/` | The Android TV app (Kotlin, package `hermestv.launcher`): home screen, rows, dock, cameras, idle screen, remote buttons. |
| `bridge/` | The bridge (Python 3.12, FastAPI, package `hermestv`): picks, TMDB, YouTube, weather, schedule, the Hermes exchange. |
| `hermes-plugin/` | The `hermes-tv-gate` plugin: blocks every Hermes tool on API-server runs. |
| `hermes-skill/` | The `hermes-tv` skill for "Ask Hermes in chat". |
| `tools/` | `debloat.sh`, `restore.sh`, `tv-install.sh`, `fetch-scenery.sh`, and their tests. |
| `examples/` | Example debloat lists for one TV model. |
| `docs/` | README images and the architecture diagram (`architecture.excalidraw`). |

### Tests

Run the tests that cover your change before you say it is done:

```sh
# from the repo root
(cd bridge && .venv/bin/python -m pytest -q)
(cd hermes-plugin && ../bridge/.venv/bin/python -m pytest -q)
(cd tv && ./gradlew testReleaseUnitTest)      # needs tv/bridge.properties (placeholder values are fine)
tools/tests/debloat-test.sh
```

No test may touch a real TV, a real Hermes container or a live API: use fakes (`respx` for HTTP, a fake `adb`, a fake `docker exec`).
A test must fail when the logic it covers is removed. A test must not depend on the wall clock: pass `now` in or freeze it.

### Rules

- Keep secrets out of the repo: no keys, tokens, camera URLs or passwords, IP addresses, host names, tailnet names or personal names.
  `bridge.env`, `tv/bridge.properties` and `tv/camera.properties` stay gitignored.
- Logs never carry an exception text or a key: use `errors.brief()` in the bridge.
- Everything that touches Hermes stays off by default (`HERMES_URL`, `HERMES_TV_DIR` and `HERMES_VAULT_TV_DIR` empty). Without Hermes the app works and runs no `docker`.
- `docker exec` calls use fixed argument lists. Paths come from settings and stay under `/opt/data/`.
- The TV opens only Netflix, HBO Max and YouTube watch URLs (`Actions.kt`). Do not widen that list without a reason in the change.
- Match the style of the file you edit: names, comment density, short functions. English only.
- Update README.md when a setting, a command or a behaviour that it describes changes.
