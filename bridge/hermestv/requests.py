"""The From Hermes row: titles Hermes asks the TV to show (requests.json), checked strictly and resolved against
the catalog, so the TV only ever gets catalog URLs."""
import asyncio
import hashlib
import json
import logging
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from hermestv import catalog, config, exchange, history, slots
from hermestv.errors import brief
from hermestv.picks import PickFilmOut, PicksOut
from hermestv.slots import zone
from hermestv.titles import _norm

NAME = "requests.json"
LABEL = "From Hermes"
MAX_BYTES = 64 * 1024
MAX_ITEMS = 10
MAX_TITLE = 120
MAX_WHY = 140
YEARS = range(1900, 2101)
KINDS = ("film", "series")
URL_MARKS = ("http", "://", "www.")
CLOCK_SKEW = timedelta(minutes=5)
CUT_HOUR = 4  # local time in TIME_ZONE
CACHE_S = 60.0
READ_CACHE_S = 5.0  # only bounds bursts of docker exec
OVER = "the request is over: past 04:00 or 24 h old"

log = logging.getLogger(__name__)

_now = time.monotonic
_cache: tuple[str, float, list[PickFilmOut]] | None = None  # file hash, when resolved, films
_read: tuple[float, bytes | None] | None = None  # when read, the file (None: missing or unreadable)
_rejected: str | None = None  # hash of the last file whose rejection was logged


def _reset() -> None:
    global _cache, _read, _rejected
    _cache = None
    _read = None
    _rejected = None


@dataclass(frozen=True)
class Item:
    title: str
    year: int | None
    kind: Literal["film", "series"]
    why: str


def _control(c: str) -> bool:
    return unicodedata.category(c).startswith("C")


def _plain(text: str) -> str:
    """One line of plain text: each control or format character becomes a space, runs of spaces become one."""
    return " ".join("".join(" " if _control(c) else c for c in text).split())


def _not_now(created_at, now: datetime) -> str | None:
    """None from created_at (a clock a little ahead is allowed) until the next 04:00 in TIME_ZONE, which is always
    within 24 h of created_at; else why not."""
    if not isinstance(created_at, str):
        return "created_at is missing"
    try:
        at = datetime.fromisoformat(created_at)
        if at.tzinfo is None:
            return "created_at has no time zone offset"
        local = at.astimezone(zone())
        cut = local.replace(hour=CUT_HOUR, minute=0, second=0, microsecond=0)
        if cut <= local:
            cut += timedelta(days=1)
        if now < at - CLOCK_SKEW:
            return "created_at is in the future"
        return OVER if now >= cut else None
    except (ValueError, OverflowError):  # also a date at the edge of the calendar
        return "created_at is not a date"


def _item(raw) -> Item | None:
    if not isinstance(raw, dict):
        return None
    title, year, kind, why = raw.get("title"), raw.get("year"), raw.get("kind"), raw.get("why")
    if not isinstance(title, str) or not isinstance(why, str) or kind not in KINDS:
        return None
    if len(title) > MAX_TITLE or len(why) > MAX_WHY or any(map(_control, title)) or not _norm(title):
        return None
    if year is not None and (type(year) is not int or year not in YEARS):
        return None
    # Every field, known or not, also in full-width letters: a URL anywhere drops the item.
    text = unicodedata.normalize("NFKC", json.dumps(raw, ensure_ascii=False)).casefold()
    if any(mark in text for mark in URL_MARKS):
        return None
    return Item(title.strip(), year, kind, _plain(why))


def _check(raw: bytes, now: datetime) -> tuple[list[Item], str | None]:
    """The items to show now, and a fixed reason when the file gives none although it should."""
    if len(raw) > MAX_BYTES:
        return [], "the file is over 64 KB"
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError):
        return [], "the file is not JSON"
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        return [], "the file has no items list"
    if reason := _not_now(doc.get("created_at"), now):
        return [], reason
    items = [item for item in map(_item, doc["items"][:MAX_ITEMS]) if item]
    return items, None if items or not doc["items"] else "every item was dropped"


def parse(raw: bytes, now: datetime) -> list[Item]:
    """The items to show now, at most 10; [] when the file is too big, not the format, or not for now."""
    return _check(raw, now)[0]


def _log_rejected(raw: bytes, reason: str) -> None:
    global _rejected
    digest = hashlib.sha256(raw).hexdigest()
    if digest == _rejected:
        return
    _rejected = digest
    # A fixed reason only: the file text comes from outside the bridge.
    log.log(logging.INFO if reason == OVER else logging.WARNING, "requests: requests.json not shown: %s", reason)


async def _resolve(raw: bytes, items: list[Item]) -> list[PickFilmOut]:
    """Each item found on Netflix or HBO Max, in Hermes's order; a result without errors is kept 60 s for the
    same file."""
    global _cache
    digest = hashlib.sha256(raw).hexdigest()
    if _cache and _cache[0] == digest and _now() - _cache[1] < CACHE_S:
        return _cache[2]
    found = await asyncio.gather(*(catalog.find(i.title, i.year, i.kind) for i in items), return_exceptions=True)
    films: list[PickFilmOut] = []
    seen = set()
    failed = False
    for item, film in zip(items, found):
        if isinstance(film, BaseException):
            log.warning("requests: find failed: %s", brief(film))
            failed = True
            continue
        if film is None or (key := (item.kind, _norm(film.title), film.year)) in seen:
            continue
        seen.add(key)
        films.append(PickFilmOut.model_validate(film, from_attributes=True)
                     .model_copy(update={"kind": item.kind, "why": item.why}))
    if not failed:
        _cache = (digest, _now(), films)
    return films


async def _file(folder: str) -> bytes | None:
    """requests.json, read at most once in READ_CACHE_S."""
    global _read
    if _read is None or _now() - _read[0] >= READ_CACHE_S:
        _read = (_now(), await exchange.read(folder, NAME, MAX_BYTES))
    return _read[1]


async def picks(now: datetime | None = None) -> PicksOut:
    """The From Hermes row; empty when the feature is off, the file is missing or bad, or the request is over."""
    now = now or datetime.now(zone())
    out = PicksOut(slot=slots.current(now).name, label=LABEL, row="hermes", films=[], updated_at=None)
    folder = config.load().hermes_tv_dir
    if not folder:
        return out
    raw = await _file(folder)
    if raw is None:  # the exchange has logged why
        return out
    items, reason = _check(raw, now)
    if reason:
        _log_rejected(raw, reason)
    if not items:
        return out
    # Hidden titles only: a request is explicit, so a title opened before may come back.
    hidden = history.hidden()
    films = [f for f in await _resolve(raw, items) if not hidden(f.kind, f.title, f.year)]
    return out.model_copy(update={"films": films})
