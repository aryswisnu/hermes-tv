import asyncio
import functools
import json
import logging
import re
import time
import unicodedata
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from hermestv import config, hermes
from hermestv.exchange import CONTAINER, _exec, _Failed
from hermestv.slots import zone

JOBS_FILE = "/opt/data/cron/jobs.json"
READ_TIMEOUT_S = 10.0
MAX_BYTES = 2 * 1024 * 1024
CACHE_S = 30 * 60
# The TV gets the last list after this long; the refresh goes on and fills the cache.
WAIT_S = 20.0
TITLE_MAX = 60
ONCE = re.compile(r"once at (\d{4}-\d{2}-\d{2}) ([01]\d|2[0-3]):([0-5]\d)")

log = logging.getLogger(__name__)


class ScheduleItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    time: str | None
    title: str
    note: str
    job: str
    date: str  # the TIME_ZONE day it is for, "YYYY-MM-DD": today or tomorrow


class ScheduleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ScheduleItem]


_now = time.monotonic
_cache: tuple[float, date, list[ScheduleItem]] | None = None  # (made at, day, items)
_running: tuple[date, asyncio.Task] | None = None  # (day it is for, refresh)
_background: set[asyncio.Task] = set()


def _local_now() -> datetime:
    return datetime.now(zone())


def _reset() -> None:
    global _cache, _running
    _cache = _running = None
    _background.clear()
    _container_ok.cache_clear()


# The TV shows "Today" or "Tomorrow" itself; a job's own "today" would contradict it.
DAY_WORDS = re.compile(r"\b(?:today|tomorrow)\b", re.IGNORECASE)


def _undated(text: str) -> str:
    out = DAY_WORDS.sub("", text)
    if out == text:
        return text
    out = re.sub(r"\s+([:,.;!?])", r"\1", " ".join(out.split())).strip(" :,.;-")
    return out[:1].upper() + out[1:] or text  # a name that is only a day word keeps its name


@functools.cache
def _container_ok(name: str) -> bool:
    if CONTAINER.fullmatch(name):
        return True
    log.warning("schedule: HERMES_CONTAINER is not a container name, no schedule")
    return False


def _clip(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in " ".join(value.split()) if unicodedata.category(ch) not in ("Cc", "Cf"))
    return text[:limit].strip()


async def _read_jobs(container: str, names: frozenset[str]) -> list[dict]:
    """The enabled allowlisted jobs of Hermes's job list, read only."""
    data = await _exec(("docker", "exec", container, "cat", JOBS_FILE), READ_TIMEOUT_S, MAX_BYTES)
    try:
        jobs = json.loads(data)["jobs"]
    except (ValueError, KeyError, TypeError):
        raise _Failed("jobs.json is not a job list") from None
    if not isinstance(jobs, list):
        raise _Failed("jobs.json is not a job list")
    return [j for j in jobs if isinstance(j, dict) and j.get("enabled") is True
            and isinstance(j.get("name"), str) and j["name"] in names]


def _display(job: dict) -> str:
    schedule = job.get("schedule")
    shown = job.get("schedule_display") or (schedule.get("display") if isinstance(schedule, dict) else None)
    return shown if isinstance(shown, str) else ""


def _is_once(job: dict) -> bool:
    schedule = job.get("schedule")
    return (isinstance(schedule, dict) and schedule.get("kind") == "once") or _display(job).startswith("once")


def _once_at(job: dict) -> datetime | None:
    """When a one-time job runs, in TIME_ZONE: its next_run_at, else the time in its schedule text."""
    try:
        at = datetime.fromisoformat(job["next_run_at"])
        if at.tzinfo is not None:
            return at.astimezone(zone())
    except (KeyError, TypeError, ValueError):
        pass
    if m := ONCE.fullmatch(_display(job)):
        try:
            return datetime.fromisoformat(f"{m[1]}T{m[2]}:{m[3]}").replace(tzinfo=zone())
        except ValueError:
            return None
    return None


CRON_FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _cron(job: dict) -> tuple[str, list[set[int] | None]] | None:
    """A cron job's "HH:MM" and its day fields as value sets (None for "*"); None when the bridge cannot read it."""
    schedule = job.get("schedule")
    expr = schedule.get("expr") if isinstance(schedule, dict) else None
    fields = (expr if isinstance(expr, str) else _display(job)).split()
    if len(fields) != 5:
        return None
    out = []
    for field, (low, high) in zip(fields, CRON_FIELDS):
        if field == "*":
            out.append(None)
            continue
        values = field.split(",")
        if not all(v.isascii() and v.isdigit() and low <= int(v) <= high for v in values):
            return None
        out.append({int(v) for v in values})
    minute, hour, *days = out
    if minute is None or hour is None or len(minute) != 1 or len(hour) != 1:  # one time a day only
        return None
    return f"{min(hour):02d}:{min(minute):02d}", days


def _cron_on(days: list[set[int] | None], day: date) -> bool:
    dom, month, dow = days
    if month is not None and day.month not in month:
        return False
    in_dom = dom is None or day.day in dom
    in_dow = dow is None or (day.weekday() + 1) % 7 in {d % 7 for d in dow}
    if dom is not None and dow is not None:  # cron: either day field may match when both are set
        return in_dom or in_dow
    return in_dom and in_dow


async def _build(day: date) -> list[ScheduleItem]:
    settings = config.load()
    if not settings.schedule_jobs or not _container_ok(settings.hermes_container):
        return []
    jobs = await _read_jobs(settings.hermes_container, settings.schedule_jobs)
    days = (day, day + timedelta(days=1))
    items = []
    for j in jobs:
        name, title = j["name"], _undated(_clip(j["name"], TITLE_MAX))
        if j.get("script") or j.get("no_agent") is not False:
            log.warning("schedule: %s is a script job, skipped", name)
        elif _is_once(j):
            if (at := _once_at(j)) and at.date() in days:
                items.append(ScheduleItem(time=f"{at:%H:%M}", title=title, note="", job=name,
                                          date=at.date().isoformat()))
        elif cron := _cron(j):
            at, cron_days = cron
            items += [ScheduleItem(time=at, title=title, note="", job=name, date=d.isoformat())
                      for d in days if _cron_on(cron_days, d)]
        else:
            log.warning("schedule: %s has a schedule the bridge cannot read, skipped", name)
    log.info("schedule: %d jobs, %d items", len(jobs), len(items))
    return sorted(items, key=lambda i: (i.date, i.time is None, i.time or ""))


def _last(day: date) -> list[ScheduleItem]:
    # After midnight, yesterday's list still holds today's items until the new run ends.
    return [i for i in _cache[2] if i.date >= day.isoformat()] if _cache else []


async def _refresh(day: date) -> list[ScheduleItem]:
    global _cache
    try:
        items = await _build(day)
    except Exception as e:  # Schedules are optional: any failure means "keep the last list".
        # Only fixed reasons: an exception text could hold job prompts.
        log.warning("schedule: refresh failed: %s (%s)", type(e).__name__,
                    e.reason if isinstance(e, _Failed) else "unexpected error")
        return _last(day)
    if _cache is None or _cache[1] <= day:  # a run that ends after midnight does not replace the new day
        _cache = (_now(), day, items)
    return items


async def upcoming() -> list[ScheduleItem]:
    """Today's and tomorrow's schedule items; cached 30 min, the last list on failure; empty without Hermes."""
    global _running
    if not hermes.enabled():
        return []
    day = _local_now().date()
    if _cache and _cache[1] == day and _now() - _cache[0] < CACHE_S:
        return _cache[2]
    if _running is None or _running[1].done() or _running[0] != day:
        if _running and not _running[1].done():
            _background.add(_running[1])  # yesterday's run ends on its own
            _running[1].add_done_callback(_background.discard)
        _running = (day, asyncio.create_task(_refresh(day)))
    try:
        # One caller that gives up must not cancel the refresh the others wait for.
        return await asyncio.wait_for(asyncio.shield(_running[1]), WAIT_S)
    except TimeoutError:
        return _last(day)
