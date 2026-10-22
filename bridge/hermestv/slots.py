import functools
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hermestv import config

SlotName = Literal["morning", "lunch", "afternoon", "dinner", "night", "late"]
Row = Literal["films", "series", "youtube"]

log = logging.getLogger(__name__)


def zone() -> ZoneInfo:
    """The TIME_ZONE setting: slots, scenery, schedule days and stored times follow it."""
    return _zone(config.load().time_zone)


@functools.cache  # one log line per bad name, not one per call
def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("TIME_ZONE %r is not a time zone name; using UTC", name)
        return ZoneInfo("UTC")


@dataclass(frozen=True)
class Rule:
    """What a films or series row of a slot may show; genres use the picker's names."""
    min_rating: float  # TMDB vote average
    genres: tuple[str, ...] = ()  # at least one of these; empty means any genre
    no_horror: bool = False
    local: bool = False  # original language is the LOCAL_LANGUAGE setting


@dataclass(frozen=True)
class Slot:
    name: SlotName
    label: str
    ask: str  # the slot's taste, as Hermes reads it
    films: Rule
    series: Rule
    rows: tuple[Row, ...] = ("films", "series")
    variant: str = ""  # a slot with other rules at the same time keeps its own cache

    @property
    def key(self) -> str:
        return f"{self.name}-{self.variant}" if self.variant else self.name

    def rule(self, row: Row) -> Rule:
        return self.series if row == "series" else self.films


_LIGHT_LOCAL = Rule(6.5, ("comedy", "family", "romance"), no_horror=True, local=True)
_ANY = Rule(7.5)
_FAMILY = Rule(7.0, ("family", "animation", "comedy"), no_horror=True)
_MEAL_ROWS: tuple[Row, ...] = ("films", "series", "youtube")
_LIGHT_ASK = "light, easy-to-follow comedy, family or romance in the local language"
_ANY_ASK = "highly rated titles of any genre"

SLOTS: dict[str, Slot] = {
    "morning": Slot("morning", "Good morning", "feel-good comedy, family or animation for the morning",
                    Rule(7.0, ("comedy", "family", "animation"), no_horror=True),
                    Rule(7.0, ("comedy", "family", "animation"), no_horror=True)),
    "lunch": Slot("lunch", "Lunch together", f"{_LIGHT_ASK} at lunch", _LIGHT_LOCAL, _LIGHT_LOCAL, _MEAL_ROWS),
    "afternoon": Slot("afternoon", "Top rated for you", _ANY_ASK, _ANY, _ANY),
    "dinner": Slot("dinner", "Dinner together", f"{_LIGHT_ASK} at dinner", _LIGHT_LOCAL, _LIGHT_LOCAL, _MEAL_ROWS),
    "night": Slot("night", "Tonight Movie Time",
                  "a mix of horror and drama, roughly half each when the list allows it, for tonight",
                  Rule(7.0, ("horror", "drama")), Rule(7.5, ("horror", "drama"))),
    "late": Slot("late", "Top rated for you", _ANY_ASK, _ANY, _ANY),
}
WEEKEND_AFTERNOON = Slot("afternoon", "Weekend family films",
                         "family, animation or comedy for a weekend afternoon",
                         _FAMILY, _FAMILY, variant="weekend")


def current(now: datetime | None = None) -> Slot:
    local = (now or datetime.now(timezone.utc)).astimezone(zone())
    hour = local.hour
    if 6 <= hour < 11:
        return SLOTS["morning"]
    if 11 <= hour < 14:
        return SLOTS["lunch"]
    if 14 <= hour < 17:
        return WEEKEND_AFTERNOON if local.weekday() >= 5 else SLOTS["afternoon"]
    if 17 <= hour < 19:
        return SLOTS["dinner"]
    if 4 <= hour < 6:
        return SLOTS["late"]
    return SLOTS["night"]
