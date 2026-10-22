import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from hermestv import slots

UTC_OFFSET_TOKYO = timedelta(hours=9)


@pytest.fixture(autouse=True)
def tokyo(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=t\nTIME_ZONE=Asia/Tokyo\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    return env


@pytest.fixture
def server_in_kolkata(monkeypatch):
    # The server clock is IST (UTC+5:30); slots must still follow TIME_ZONE.
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _local(hh: int, mm: int) -> datetime:
    """The UTC instant of hh:mm Tokyo time on 2026-10-20, so a wrong zone shifts the result."""
    return datetime(2026, 10, 20, hh, mm, tzinfo=timezone.utc) - UTC_OFFSET_TOKYO


@pytest.mark.parametrize("hh, mm, name", [
    (5, 59, "late"), (6, 0, "morning"), (10, 59, "morning"), (11, 0, "lunch"), (13, 59, "lunch"),
    (14, 0, "afternoon"), (16, 59, "afternoon"), (17, 0, "dinner"), (18, 59, "dinner"),
    (19, 0, "night"), (23, 59, "night"), (0, 0, "night"), (3, 59, "night"), (4, 0, "late"),
])
def test_slot_boundaries_six(server_in_kolkata, hh, mm, name):
    assert time.strftime("%Z") == "IST"
    assert slots.current(_local(hh, mm)).name == name


def test_slot_labels_and_rows():
    got = {s.name: (s.label, s.rows) for s in (slots.current(_local(h, 0)) for h in (8, 12, 15, 18, 20, 5))}
    assert got == {
        "morning": ("Good morning", ("films", "series")),
        "lunch": ("Lunch together", ("films", "series", "youtube")),
        "afternoon": ("Top rated for you", ("films", "series")),
        "dinner": ("Dinner together", ("films", "series", "youtube")),
        "night": ("Tonight Movie Time", ("films", "series")),
        "late": ("Top rated for you", ("films", "series")),
    }
    # Dinner has the same rows and rules as lunch.
    lunch, dinner = slots.SLOTS["lunch"], slots.SLOTS["dinner"]
    assert (lunch.films, lunch.series) == (dinner.films, dinner.series)
    assert lunch.films == slots.Rule(6.5, ("comedy", "family", "romance"), no_horror=True, local=True)


def test_weekend_afternoon_family():
    # 2026-10-20 is a Tuesday; the 24th and 25th are Saturday and Sunday.
    weekday = slots.current(_local(15, 0))
    sat, sun = (slots.current(_local(15, 0) + timedelta(days=d)) for d in (4, 5))
    assert (weekday.label, weekday.films.min_rating, weekday.films.genres) == ("Top rated for you", 7.5, ())
    for s in (sat, sun):
        assert (s.name, s.label) == ("afternoon", "Weekend family films")
        assert (s.films.min_rating, s.films.genres, s.films.no_horror) == (7.0, ("family", "animation", "comedy"), True)
    # The weekend rule is for the afternoon only; Saturday night is still night.
    assert slots.current(_local(20, 0) + timedelta(days=4)).label == "Tonight Movie Time"
    assert slots.current(_local(12, 0) + timedelta(days=4)).label == "Lunch together"


def test_slot_follows_time_zone(tokyo):
    instant = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
    assert slots.current(instant).name == "morning"  # 09:00 in Tokyo
    tokyo.write_text("TV_KEY=t\nTIME_ZONE=America/New_York\n")
    assert slots.current(instant).name == "night"  # 20:00 the day before in New York
    tokyo.write_text("TV_KEY=t\n")
    assert slots.zone() == ZoneInfo("UTC")
    assert slots.current(instant).name == "night"  # 00:00 UTC


@pytest.mark.parametrize("name", ["Mars/Olympus_Mons", "../etc/passwd", "Asia"])
def test_bad_time_zone_is_utc_with_one_log_line(tokyo, caplog, name):
    slots._zone.cache_clear()
    tokyo.write_text(f"TV_KEY=t\nTIME_ZONE={name}\n")
    with caplog.at_level(logging.WARNING, logger="hermestv.slots"):
        assert slots.zone() == ZoneInfo("UTC")
        assert slots.zone() == ZoneInfo("UTC")
    assert len(caplog.records) == 1
    assert "TIME_ZONE" in caplog.text
