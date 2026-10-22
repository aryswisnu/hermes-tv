import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from hermestv.slots import zone

DIR = Path(__file__).resolve().parent.parent / "state" / "scenery"
PERIODS = ("morning", "day", "afternoon", "evening")
NAME = re.compile(r"[A-Za-z0-9._-]+\.jpg")

Period = Literal["morning", "day", "afternoon", "evening"]


def period(now: datetime | None = None) -> Period:
    """The idle screen's photo set at `now` (TIME_ZONE): morning 05-11, day 11-15, afternoon 15-18, evening 18-05."""
    hour = (now or datetime.now(timezone.utc)).astimezone(zone()).hour
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 15:
        return "day"
    if 15 <= hour < 18:
        return "afternoon"
    return "evening"


def photo(period: str, name: str) -> Path | None:
    """The photo file, or None when the period or name is not allowed or the file is not in the period's folder."""
    if period not in PERIODS or not NAME.fullmatch(name):
        return None
    folder = (DIR / period).resolve()
    path = (folder / name).resolve()
    return path if path.parent == folder and path.is_file() else None


def items(period: Period) -> list[dict]:
    folder = DIR / period
    names = sorted(f.name for f in folder.iterdir() if photo(period, f.name)) if folder.is_dir() else []
    return [{"id": n.removesuffix(".jpg"), "url": f"/v1/scenery/{period}/{n}"} for n in names]
