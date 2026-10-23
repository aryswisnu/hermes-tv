import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from hermestv import watchlog
from hermestv.slots import zone
from hermestv.titles import _norm

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
HISTORY_MAX = 500
OPENED_FOR = timedelta(days=90)
HINTS = 20

Kind = Literal["film", "series", "video"]

log = logging.getLogger(__name__)


def _key(kind: str | None, title: str, year: int | None = None, video_id: str | None = None) -> str:
    return f"v:{video_id}" if video_id else f"t:{kind}:{_norm(title)}:{year or ''}"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _set_aside(path: Path) -> None:
    """Keep a bad state file as <name>.bad, so the next read starts clean and warns no more."""
    log.warning("%s is corrupt; a copy is in %s.bad", path.name, path.name)
    try:
        shutil.copyfile(path, path.with_name(path.name + ".bad"))
    except OSError:
        log.warning("%s: no .bad copy could be made", path.name)


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("%s: the corrupt file could not be removed", path.name)


def _history() -> list[dict]:
    path = STATE_DIR / "history.jsonl"
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        _set_aside(path)
        _remove(path)
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            entry = None
        if isinstance(entry, dict):
            out.append(entry)
    if len(out) != len(lines):
        _set_aside(path)
        try:
            _write_history(out)
        except OSError:
            log.warning("history.jsonl: the good lines could not be written back")
    return out


def _write_history(entries: list[dict]) -> None:
    _write(STATE_DIR / "history.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


def _hidden() -> dict:
    path = STATE_DIR / "hidden.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {"items": {}, "channels": {}}
    except (OSError, ValueError):
        data = None
    if (isinstance(data, dict) and isinstance(data.get("items", {}), dict)
            and isinstance(data.get("channels", {}), dict)):
        return {"items": dict(data.get("items") or {}), "channels": dict(data.get("channels") or {})}
    _set_aside(path)
    _remove(path)
    return {"items": {}, "channels": {}}


def opened(kind: Kind, title: str, year: int | None, video_id: str | None, url: str, slot: str,
           now: datetime | None = None) -> None:
    now = (now or datetime.now(zone())).astimezone(zone()).replace(microsecond=0)
    entry = {"time": now.isoformat(), "kind": kind, "title": title, "year": year, "video_id": video_id,
             "url": url, "slot": slot}
    _write_history((_history() + [entry])[-HISTORY_MAX:])
    watchlog.schedule()


def hide(kind: Kind, title: str, year: int | None, video_id: str | None, channel: str | None) -> None:
    """Hide one item for good, or with `channel` the whole YouTube channel."""
    data = _hidden()
    at = datetime.now(zone()).replace(microsecond=0).isoformat()
    if channel:
        data["channels"].setdefault(_norm(channel), {"channel": channel, "time": at})
    else:
        data["items"].setdefault(_key(kind, title, year, video_id),
                                 {"kind": kind, "title": title, "year": year, "time": at})
    _write(STATE_DIR / "hidden.json", json.dumps(data, ensure_ascii=False, indent=1))
    watchlog.schedule()


def recent_titles(n: int = HINTS) -> list[str]:
    """The last opened titles, newest first, each once."""
    out: list[str] = []
    for e in reversed(_history()):
        title = e.get("title")
        if isinstance(title, str) and title and title not in out:
            out.append(title)
    return out[:n]


@dataclass(frozen=True)
class Blocked:
    keys: frozenset[str]
    channels: frozenset[str]

    def __call__(self, kind: str, title: str, year: int | None = None, video_id: str | None = None,
                 channel: str | None = None) -> bool:
        if video_id:
            return _key(kind, title, None, video_id) in self.keys or bool(channel and _norm(channel) in self.channels)
        # A hide or open sent without a year matches the title in any year.
        return _key(kind, title, year) in self.keys or _key(kind, title) in self.keys


def hidden() -> Blocked:
    """Only what was hidden, items and channels; not what was opened."""
    data = _hidden()
    return Blocked(frozenset(data["items"]), frozenset(data["channels"]))


def blocked(now: datetime | None = None) -> Blocked:
    """What no row may show: hidden items and channels, and items opened in the last 90 days."""
    hidden = _hidden()
    since = (now or datetime.now(zone())) - OPENED_FOR
    keys = set(hidden["items"])
    for e in _history():
        try:
            if datetime.fromisoformat(e["time"]) >= since:
                keys.add(_key(e["kind"], e["title"], e.get("year"), e.get("video_id")))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return Blocked(frozenset(keys), frozenset(hidden["channels"]))
