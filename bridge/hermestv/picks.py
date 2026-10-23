import asyncio
import logging
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from hermestv import catalog, hermes, history, prompt, youtube
from hermestv.catalog import Film
from hermestv.history import Blocked
from hermestv.hermes import Done, NeedsApproval
from hermestv.reply import Candidate, parse_candidates
from hermestv.slots import Row, Slot, SlotName, zone
from hermestv.titles import _norm
from hermestv.youtube import Video

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
FRESH_FOR = timedelta(minutes=10)
MAX_FILMS = 10
KEEP_OLD = 3
MAX_CANDIDATES = 60
# A failed or empty refresh leaves the cache stale; this stops every GET starting a new Hermes run.
COOLDOWN_S = 120.0

log = logging.getLogger(__name__)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PickFilmOut(_Strict):
    title: str
    year: int | None
    runtime_min: int | None
    rating: float | None
    service: Literal["netflix", "hbomax", "youtube", "extra"]
    url: str
    poster: str | None
    backdrop: str | None
    genres: list[str]
    why: str
    seasons: int | None = None
    kind: Literal["film", "series", "video"] = "film"
    channel: str | None = None
    duration_min: int | None = None
    video_id: str | None = None


class PicksOut(_Strict):
    slot: SlotName
    label: str
    row: Row = "films"
    films: list[PickFilmOut]
    updated_at: datetime | None
    refreshing: bool = False


# (slot key, row) -> its refresh; a finished task stays here until the next refresh replaces it.
_running: dict[tuple[str, Row], asyncio.Task] = {}
_last_attempt: dict[tuple[str, Row], float] = {}  # (slot key, row) -> when its last refresh ended
_now = time.monotonic


def _path(slot: str, row: Row) -> Path:
    return STATE_DIR / f"picks-{slot}-{row}.json"


def _read(slot: Slot, row: Row) -> PicksOut | None:
    try:
        # The label comes from the slot, so a renamed slot shows at once, not after the next refresh.
        return PicksOut.model_validate_json(_path(slot.key, row).read_bytes()).model_copy(update={"label": slot.label})
    except FileNotFoundError:
        return None
    except (OSError, ValidationError) as e:
        log.warning("picks %s %s: unreadable cache: %r", slot.name, row, e)
        return None


def _write(slot: Slot, out: PicksOut) -> None:
    path = _path(slot.key, out.row)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(out.model_dump_json(exclude={"refreshing"}))
    os.replace(tmp, path)


def get(slot: Slot, row: Row = "films") -> PicksOut:
    """Return the cached list of the slot's row; start a refresh when it is missing or stale."""
    empty = PicksOut(slot=slot.name, label=slot.label, row=row, films=[], updated_at=None)
    if row not in slot.rows:
        return empty
    cached = _read(slot, row)
    stale = cached is None or cached.updated_at is None or datetime.now(zone()) - cached.updated_at > FRESH_FOR
    cooling = _now() - _last_attempt.get((slot.key, row), float("-inf")) < COOLDOWN_S
    if stale and not cooling:
        refresh(slot, row)
    task = _running.get((slot.key, row))
    out = cached or empty
    # A hide shows at once, not only after the next refresh.
    return out.model_copy(update={"films": _allowed(history.blocked(), row, out.films),
                                  "refreshing": task is not None and not task.done()})


def refresh(slot: Slot, row: Row = "films") -> asyncio.Task:
    """Start a background refresh of the slot's row, or return the one already running."""
    key = (slot.key, row)
    task = _running.get(key)
    if task is None or task.done():
        task = _running[key] = asyncio.create_task(_refresh(slot, row))
    return task


async def _choose(slot: Slot, row: Row, note: str | None) -> list[Candidate]:
    if note is None or not hermes.enabled():  # nothing to choose from, or nobody to choose
        return []
    key = f"hermes-tv-picks-{row}-{datetime.now(zone()):%Y%m%d}"
    result = await hermes.start_run(note, key)
    if isinstance(result, NeedsApproval):
        log.warning("picks %s %s: hermes asked for a tool; the run was stopped", slot.name, row)
        return []
    if not isinstance(result, Done):
        log.warning("picks %s %s: hermes offline: %s", slot.name, row, result.reason)
        return []
    return parse_candidates(result.text)


def _unique(films: list[Film]) -> list[Film]:
    out: dict[str, Film] = {}
    for f in films:
        out.setdefault(_norm(f.title), f)
    return list(out.values())


def _matched(candidates: list[Film], chosen: list[Candidate]) -> list[Film]:
    by_title: dict[str, Film] = {}
    by_year: dict[tuple[str, int], Film] = {}
    for f in candidates:
        by_title.setdefault(_norm(f.title), f)
        by_year.setdefault((_norm(f.title), f.year), f)
    return [replace(f, why=c.why) for c in chosen
            if (f := by_year.get((_norm(c.title), c.year)) or by_title.get(_norm(c.title)))]


def _key(f: PickFilmOut) -> str:
    return f.video_id or _norm(f.title)


def _kind(row: Row) -> Literal["film", "series", "video"]:
    return {"films": "film", "series": "series", "youtube": "video"}[row]


def _allowed(blocked: Blocked, row: Row, films: list[PickFilmOut]) -> list[PickFilmOut]:
    return [f for f in films if not blocked(_kind(row), f.title, f.year, f.video_id, f.channel)]


def _keepable(slot: Slot, row: Row, films: list[PickFilmOut], blocked: Blocked) -> list[PickFilmOut]:
    """The items of the current list that may stay on the row."""
    films = _allowed(blocked, row, films)
    if row == "youtube":
        return [f for f in films if f.kind == "video"]
    rule = slot.rule(row)
    # A list saved before `kind` existed reads as films.
    kind = "series" if row == "series" else "film"
    return [f.model_copy(update={"kind": kind}) for f in films if catalog.fits(rule, f)]


# candidates, chosen, new, fill-ups by preference, the films the final list takes details from
Found = tuple[int, int, list[PickFilmOut], list[PickFilmOut], list[Film]]


async def _titles(slot: Slot, row: Row, exclude: list[str], blocked: Blocked) -> Found:
    candidates = [f for f in await catalog.available(slot, row) if not blocked(_kind(row), f.title, f.year)]
    note = (prompt.picks_note(slot, row, exclude, candidates[:MAX_CANDIDATES], history.recent_titles())
            if candidates else None)
    hermes_films = _matched(candidates, await _choose(slot, row, note))
    kind = "series" if row == "series" else "film"

    def out(f: Film) -> PickFilmOut:
        return PickFilmOut.model_validate(f, from_attributes=True).model_copy(update={"kind": kind})

    return (len(candidates), len(hermes_films), [out(f) for f in _unique(hermes_films)],
            [out(f) for f in sorted(candidates, key=lambda f: -f.rating)], candidates)


def _video_out(v: Video) -> PickFilmOut:
    if not youtube.VIDEO_ID.fullmatch(v.id):
        raise ValueError(f"not a YouTube video id: {v.id!r}")
    return PickFilmOut(title=v.title, year=None, runtime_min=None, rating=None, service="youtube", url=v.url,
                       poster=v.thumb, backdrop=v.thumb, genres=[], why=v.why, kind="video",
                       channel=v.channel, duration_min=round(v.duration_s / 60) if v.duration_s else None,
                       video_id=v.id)


async def _videos(slot: Slot, exclude: list[str], blocked: Blocked) -> Found:
    candidates = [v for v in await youtube.candidates(slot) if not blocked("video", v.title, None, v.id, v.channel)]
    note = (prompt.youtube_note(slot, exclude, candidates[:MAX_CANDIDATES], history.recent_titles())
            if candidates else None)
    chosen = await _choose(slot, "youtube", note)
    by_id = {v.id: v for v in candidates}
    by_title: dict[str, Video] = {}
    for v in candidates:
        by_title.setdefault(_norm(v.title), v)
    picked = {}
    for c in chosen:
        if v := by_id.get(c.id or "") or by_title.get(_norm(c.title)):
            picked.setdefault(v.id, replace(v, why=c.why))
    return (len(candidates), len(picked), [_video_out(v) for v in picked.values()],
            [_video_out(v) for v in sorted(candidates, key=lambda v: -(v.views or 0))], [])


async def _with_details(row: Row, films: list[PickFilmOut], candidates: list[Film]) -> list[PickFilmOut]:
    """Fills the runtime of films or the seasons of series in the final list; a failed lookup changes nothing."""
    def key(f: Film | PickFilmOut) -> tuple[str, int | None]:
        return _norm(f.title), f.year

    by_key = {key(f): f for f in candidates}
    field = "runtime_min" if row == "films" else "seasons"
    filled = {key(f): getattr(f, field)
              for f in await catalog.details([by_key[key(f)] for f in films if key(f) in by_key], row)}
    return [f.model_copy(update={field: v}) if (v := filled.get(key(f))) is not None else f for f in films]


async def _refresh(slot: Slot, row: Row) -> None:
    try:
        old = _read(slot, row)
        # Without Hermes there is no taste to keep: the row is the top of the catalog.
        old_films = old.films if old and hermes.enabled() else []
        exclude = [f.title for f in old_films]
        blocked = history.blocked()
        n_candidates, n_chosen, new, pool, candidates = await (
            _videos(slot, exclude, blocked) if row == "youtube" else _titles(slot, row, exclude, blocked))
        seen = {_key(f) for f in new}
        kept = [f for f in _keepable(slot, row, old_films, blocked) if _key(f) not in seen]
        # With nothing new (Hermes offline), the whole old list stays rather than the top of the pool.
        if new:
            kept = kept[:KEEP_OLD]
            new = new[:MAX_FILMS - len(kept)]
        seen |= {_key(f) for f in kept}
        old_keys = {_key(f) for f in old_films}
        top = sorted((f for f in pool if _key(f) not in seen), key=lambda f: _key(f) in old_keys)
        merged = (new + kept + top)[:MAX_FILMS]
        n_new = min(len(new), MAX_FILMS)
        n_kept = min(len(kept), MAX_FILMS - n_new)
        log.info("picks %s %s: %d candidates, %d chosen, %d new, %d kept, %d topped up", slot.name, row,
                 n_candidates, n_chosen, n_new, n_kept, len(merged) - n_new - n_kept)
        if candidates:
            merged = await _with_details(row, merged, candidates)
        if not merged:
            log.warning("picks %s %s: nothing survived and none kept", slot.name, row)
            return
        if old and merged == old.films:
            return
        _write(slot, PicksOut(slot=slot.name, label=slot.label, row=row,
                        updated_at=datetime.now(zone()).replace(microsecond=0), films=merged))
    except Exception:  # A background task has nobody to raise to.
        log.exception("picks %s %s: refresh failed", slot.name, row)
    finally:
        _last_attempt[(slot.key, row)] = _now()
