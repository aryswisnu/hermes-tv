"""What the TV opened, for Hermes (watch.json) and for the vault (two notes): titles, years, kinds, services and
times only, never a URL or an id."""
import asyncio
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from hermestv import config, exchange, extra, history
from hermestv.errors import brief
from hermestv.slots import zone

MAX_ITEMS = 500
NOTE_ROWS = 100
DEBOUNCE_S = 60.0
KINDS = ("film", "series", "video")
SERVICES = {"netflix.com": "netflix", "hbomax.com": "hbomax", "max.com": "hbomax", "youtube.com": "youtube",
            "youtu.be": "youtube"}
NOTE_LINE = "_Written by the TV bridge._"

log = logging.getLogger(__name__)

_pending: asyncio.Task | None = None  # the write that has not built its data yet
_running: set[asyncio.Task] = set()  # the loop holds tasks only weakly
_sleep = asyncio.sleep


def _reset() -> None:
    global _pending
    _pending = None
    _running.clear()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _track(task: asyncio.Task) -> None:
    _running.add(task)
    task.add_done_callback(_running.discard)


def _service(url) -> str | None:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except (TypeError, ValueError, AttributeError):
        return None
    if (link := extra.scheme()) and parts.scheme == link:
        return "extra"
    return next((name for domain, name in SERVICES.items() if host == domain or host.endswith("." + domain)), None)


def _year(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _item(e: dict) -> dict | None:
    title, kind = e.get("title"), e.get("kind")
    if not isinstance(title, str) or not title or kind not in KINDS:
        return None
    return {"title": title, "year": _year(e.get("year")), "kind": kind}


def build(now: datetime) -> dict:
    opened, genres, slots = [], Counter(), Counter()
    for e in reversed(history._history()):
        item = _item(e)
        if item is None or not isinstance(e.get("time"), str):
            continue
        opened.append(item | {"service": _service(e.get("url")), "opened_at": e["time"]})
        if isinstance(e.get("genres"), list):
            genres.update(g for g in e["genres"] if isinstance(g, str) and g)
        if isinstance(e.get("slot"), str):
            slots[e["slot"]] += 1
        if len(opened) == MAX_ITEMS:
            break
    hidden = sorted((h for h in history._hidden()["items"].values() if isinstance(h, dict)),
                    key=lambda h: str(h.get("time", "")), reverse=True)
    taste = {"kinds": {k: sum(o["kind"] == k for o in opened) for k in KINDS},
             "services": dict(Counter(o["service"] for o in opened if o["service"]).most_common()),
             "slots": dict(slots.most_common())}
    if genres:  # history records carry genres only when the TV sends them; none are made up
        taste = {"genres": dict(genres.most_common())} | taste
    return {"updated_at": now.astimezone(zone()).replace(microsecond=0).isoformat(), "opened": opened,
            "hidden": [i for h in hidden if (i := _item(h))][:MAX_ITEMS], "taste": taste}


def schedule() -> None:
    """Write watch.json DEBOUNCE_S after a change, in the background; later changes ride along."""
    global _pending
    try:
        tv_dir = config.load().hermes_tv_dir
    except Exception as e:  # An open or hide never fails here; only the type: a text could hold settings.
        log.warning("watchlog: settings unreadable: %s", brief(e))
        return
    if not tv_dir or (_pending and not _pending.done()):
        return
    try:
        _pending = asyncio.get_running_loop().create_task(_write_later(tv_dir))
    except RuntimeError:  # no event loop: a direct call, not a TV request
        return
    _track(_pending)


async def _write_later(tv_dir: str) -> None:
    global _pending
    await asyncio.sleep(DEBOUNCE_S)
    _pending = None  # a change from here on gets its own write
    try:
        data = json.dumps(build(_utc_now()), ensure_ascii=False)
    except Exception as e:  # Only the type: an exception text could hold a title.
        log.warning("watchlog: watch.json failed: %s", brief(e))
        return
    # A failed write is logged by the exchange; the next change writes the whole list again.
    await exchange.write(tv_dir, "watch.json", data.encode())


def _cell(value) -> str:
    """One table cell: a title cannot end the cell, link a note, add HTML, a tag, math or other markup."""
    if value is None:
        return ""
    text = " ".join(str(value).split())
    for ch in "\\|[]<>`#$%=^":  # the backslash first, so an escape is never undone
        text = text.replace(ch, "\\" + ch)
    return text


def _table(head: tuple[str, ...], rows) -> list[str]:
    return [f"| {' | '.join(head)} |", "|" + "---|" * len(head),
            *(f"| {' | '.join(_cell(v) for v in row)} |" for row in rows)]


def markdown(data: dict) -> tuple[str, str]:
    """The Watch Log.md and Taste.md notes."""
    updated = f"Updated {data['updated_at'][:16].replace('T', ' ')} {zone().key}."
    log_note = ["# TV Watch Log", "", NOTE_LINE, "", updated, "",
                *_table(("Date", "Title", "Year", "Kind", "Service"),
                        ((o["opened_at"][:10], o["title"], o["year"], o["kind"], o["service"])
                         for o in data["opened"][:NOTE_ROWS]))]
    taste = data["taste"]
    taste_note = ["# TV Taste", "", NOTE_LINE, "", f"{updated} Counts of the last {len(data['opened'])} opens."]
    for title, key, head in (("Genres", "genres", "Genre"), ("Kinds", "kinds", "Kind"),
                             ("Services", "services", "Service"), ("Time of day", "slots", "Slot")):
        if key in taste:
            taste_note += ["", f"## {title}", "", *_table((head, "Opens"), taste[key].items())]
    return "\n".join(log_note) + "\n", "\n".join(taste_note) + "\n"


async def vault_once() -> bool:
    """Write both notes to the vault folder; False when it is off or a write fails."""
    vault_dir = config.load().hermes_vault_tv_dir
    if not vault_dir:
        return False
    log_note, taste_note = markdown(build(_utc_now()))
    ok = await exchange.write(vault_dir, "Watch Log.md", log_note.encode())
    return await exchange.write(vault_dir, "Taste.md", taste_note.encode()) and ok


def next_vault(now: datetime) -> datetime:
    """The next 23:50 in TIME_ZONE after now."""
    at = now.astimezone(zone()).replace(hour=23, minute=50, second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


async def vault_daily() -> None:
    at = next_vault(_utc_now())
    while True:
        # From the target, not from now: an early wake sleeps again and never writes twice.
        await _sleep((at - _utc_now()).total_seconds())
        if _utc_now() < at:
            continue
        try:
            await vault_once()
        except Exception as e:  # Only the type: an exception text could hold a title.
            log.warning("watchlog: vault notes failed: %s", brief(e))
        at = next_vault(at)


def start_vault() -> None:
    """Write the vault notes at 23:50 each day, until shutdown()."""
    _track(asyncio.get_running_loop().create_task(vault_daily()))


def shutdown() -> None:
    """Cancel the daily vault task and every watch.json write, waiting or already in docker."""
    for task in list(_running):
        task.cancel()
