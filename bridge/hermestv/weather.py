import asyncio
import logging
import time
from datetime import datetime
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict

from hermestv import config
from hermestv.errors import brief
from hermestv.slots import zone

URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 5.0
CACHE_S = 30 * 60
# After a failed fetch, answer from the cache at once instead of waiting 5 s on every call.
RETRY_S = 60

log = logging.getLogger(__name__)


class Weather(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    temp_c: int
    code: int
    is_day: bool
    updated_at: datetime


_now = time.monotonic
_cache: tuple[float, Weather] | None = None  # (fetched at, value)
_running: asyncio.Task | None = None
_next_try = float("-inf")


def enabled() -> bool:
    """False without LATITUDE and LONGITUDE: then there is no weather and no call to Open-Meteo."""
    s = config.load()
    return s.latitude is not None and s.longitude is not None


async def _fetch() -> Weather:
    s, tz = config.load(), zone()
    # Open-Meteo answers in the asked zone, so its local time is read in the same zone.
    url = (f"{URL}?latitude={s.latitude}&longitude={s.longitude}"
           f"&current=temperature_2m,weather_code,is_day&timezone={quote(tz.key, safe='')}")
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers={"User-Agent": config.USER_AGENT}) as client:
        r = await client.get(url)
        r.raise_for_status()
    now = r.json()["current"]
    return Weather(temp_c=round(now["temperature_2m"]), code=now["weather_code"], is_day=bool(now["is_day"]),
                   updated_at=datetime.fromisoformat(now["time"]).replace(tzinfo=tz))


async def _refresh() -> Weather | None:
    global _cache, _next_try
    try:
        weather = await _fetch()
    except Exception as e:  # Weather is optional: any failure means "keep the last value".
        log.warning("weather: fetch failed: %s", brief(e))
        _next_try = _now() + RETRY_S
        return _cache[1] if _cache else None
    _cache, _next_try = (_now(), weather), float("-inf")
    return weather


async def current() -> Weather | None:
    global _running
    if not enabled():
        return None
    if _cache and _now() - _cache[0] < CACHE_S:
        return _cache[1]
    if _now() < _next_try:
        return _cache[1] if _cache else None
    if _running is None or _running.done():
        _running = asyncio.create_task(_refresh())
    # One caller that disconnects must not cancel the fetch the others wait for.
    return await asyncio.shield(_running)
