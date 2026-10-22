import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from hermestv import config
from hermestv.slots import Slot

YTDLP = Path(sys.executable).parent / "yt-dlp"
YTDLP_TIMEOUT_S = 30.0
PER_QUERY = 10
DISCOVER_TTL_S = 6 * 3600
MIN_S, MAX_S = 300, 3600
SEARCH_PARALLEL = 3
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")

log = logging.getLogger(__name__)
_now = time.monotonic


@dataclass(frozen=True)
class Video:
    id: str
    title: str
    channel: str
    duration_s: int | None
    thumb: str | None
    url: str
    views: int | None = None
    why: str = ""


_discovered: tuple[float, list[Video]] | None = None


def _reset() -> None:
    global _discovered
    _discovered = None


def _watch(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _thumb(vid: str) -> str:
    return f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"


def parse_search(data: bytes) -> list[Video]:
    videos = []
    for e in json.loads(data).get("entries") or []:
        if not e or not VIDEO_ID.fullmatch(str(e.get("id") or "")) or e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        videos.append(Video(
            id=e["id"], title=e.get("title") or "", channel=e.get("channel") or e.get("uploader") or "",
            duration_s=int(e["duration"]) if e.get("duration") else None,
            thumb=_thumb(e["id"]), url=_watch(e["id"]), views=e.get("view_count")))
    return videos


def fits(v: Video, exclude: tuple[str, ...] = ()) -> bool:
    """Five to sixty minutes, and no excluded word or phrase in the title or channel name."""
    text = f"{v.title} {v.channel}"
    return (v.duration_s is not None and MIN_S <= v.duration_s <= MAX_S
            and not any(re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE) for w in exclude))


def _unique(videos: list[Video]) -> list[Video]:
    out: dict[str, Video] = {}
    for v in videos:
        out.setdefault(v.id, v)
    return list(out.values())


async def _ytdlp(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(str(YTDLP), "--ignore-config", *args,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), YTDLP_TIMEOUT_S)
    finally:
        if proc.returncode is None:  # timed out or cancelled
            proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp exit {proc.returncode}: {err.decode(errors='replace').strip()[-200:]}")
    return out


async def discover() -> list[Video]:
    """Videos from a yt-dlp search of each YOUTUBE_QUERIES entry; cached 6 h, the last list on failure."""
    global _discovered
    queries = config.load().youtube_queries
    if not queries:
        return []
    if _discovered and _now() - _discovered[0] < DISCOVER_TTL_S:
        return _discovered[1]
    gate = asyncio.Semaphore(SEARCH_PARALLEL)

    async def search(query: str) -> list[Video]:
        async with gate:
            return parse_search(await _ytdlp("--flat-playlist", "-J", "--no-warnings", f"ytsearch{PER_QUERY}:{query}"))

    results = await asyncio.gather(*(search(q) for q in queries), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    if errors:
        log.warning("youtube discover: %d of %d searches failed: %s", len(errors), len(queries), errors[0])
    found = _unique([v for r in results if not isinstance(r, BaseException) for v in r])
    if found:
        _discovered = (_now(), found)
    return found or (_discovered[1] if _discovered else [])


async def candidates(slot: Slot) -> list[Video]:
    """The discovered videos that fit the row."""
    try:
        found = await discover()
    except Exception as e:  # The row stays as it was; picks keeps the last list.
        log.warning("youtube discover %s failed: %r", slot.name, e)
        return []
    exclude = config.load().youtube_exclude
    return [v for v in found if fits(v, exclude)]
