import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from hermestv import weather

FIXTURES = Path(__file__).parent / "fixtures"
URL = ("https://api.open-meteo.com/v1/forecast?latitude=35.6762&longitude=139.6503"
       "&current=temperature_2m,weather_code,is_day&timezone=Asia%2FTokyo")
# The recorded reply: 28.6 °C, WMO code 1 (mainly clear), daytime, observed 08:45 Tokyo time.
EXPECTED = weather.Weather(temp_c=29, code=1, is_day=True,
                           updated_at=datetime(2026, 10, 20, 8, 45, tzinfo=ZoneInfo("Asia/Tokyo")))

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _fresh_cache():
    weather._cache = weather._running = None
    weather._next_try = float("-inf")
    yield
    weather._cache = weather._running = None
    weather._next_try = float("-inf")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    path = tmp_path / "bridge.env"
    path.write_text("TV_KEY=t\nTIME_ZONE=Asia/Tokyo\nLATITUDE=35.6762\nLONGITUDE=139.6503\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(path))
    return path


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(weather, "_now", lambda: now[0])
    return now


def _mock(status: int = 200) -> respx.Route:
    body = (FIXTURES / "openmeteo.json").read_bytes() if status == 200 else b"{}"
    return respx.get(URL).mock(return_value=httpx.Response(status, content=body))


@respx.mock
async def test_weather_parses_fixture():
    route = _mock()
    assert await weather.current() == EXPECTED
    assert route.calls.last.request.url == URL


@respx.mock
async def test_weather_url_uses_settings(env):
    env.write_text("TV_KEY=t\nTIME_ZONE=America/New_York\nLATITUDE=40.7128\nLONGITUDE=-74.006\n")
    url = ("https://api.open-meteo.com/v1/forecast?latitude=40.7128&longitude=-74.006"
           "&current=temperature_2m,weather_code,is_day&timezone=America%2FNew_York")
    route = respx.get(url).mock(return_value=httpx.Response(200, content=(FIXTURES / "openmeteo.json").read_bytes()))
    got = await weather.current()
    assert route.call_count == 1
    # Open-Meteo answers in the asked zone, so its local time is read in TIME_ZONE.
    assert got.updated_at == datetime(2026, 10, 20, 8, 45, tzinfo=ZoneInfo("America/New_York"))


@pytest.mark.parametrize("location", ["", "LATITUDE=40.7128\n", "LONGITUDE=-74.006\n"])
async def test_weather_without_location_makes_no_request(env, caplog, location):
    env.write_text(f"TV_KEY=t\n{location}")
    with respx.mock(assert_all_called=False) as mock, caplog.at_level(logging.WARNING, logger="hermestv.weather"):
        any_request = mock.route()
        assert not weather.enabled()
        assert await weather.current() is None
        assert await weather.current() is None
    assert any_request.call_count == 0
    assert caplog.records == []  # no weather by choice is not a failure


@respx.mock
async def test_weather_cache_30min(clock):
    route = _mock()
    first, second = await asyncio.gather(weather.current(), weather.current())
    assert first == second == EXPECTED
    assert route.call_count == 1  # single-flight: two callers, one request
    clock[0] += 30 * 60 - 1
    assert await weather.current() == EXPECTED
    assert route.call_count == 1
    clock[0] += 2
    assert await weather.current() == EXPECTED
    assert route.call_count == 2


@respx.mock
async def test_weather_error_returns_last(clock, caplog):
    _mock(500)
    with caplog.at_level(logging.WARNING, logger="hermestv.weather"):
        assert await weather.current() is None
    assert len(caplog.records) == 1

    respx.reset()
    _mock()
    clock[0] += weather.RETRY_S
    assert await weather.current() == EXPECTED

    clock[0] += 31 * 60
    respx.reset()
    caplog.clear()
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    with caplog.at_level(logging.WARNING, logger="hermestv.weather"):
        assert await weather.current() == EXPECTED  # stale, but better than nothing on the TV
    assert len(caplog.records) == 1


@respx.mock
async def test_weather_failure_log_has_no_location(caplog):
    _mock(500)
    with caplog.at_level(logging.WARNING, logger="hermestv.weather"):
        assert await weather.current() is None
    assert "500" in caplog.text
    assert "35.6762" not in caplog.text and "139.6503" not in caplog.text


@respx.mock
async def test_weather_error_backs_off_60s(clock, caplog):
    route = respx.get(URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    with caplog.at_level(logging.WARNING, logger="hermestv.weather"):
        assert await weather.current() is None
        clock[0] += weather.RETRY_S - 1
        assert await weather.current() is None  # answered at once, no second 5 s wait
    assert route.call_count == 1
    assert len(caplog.records) == 1


@respx.mock
async def test_weather_error_retries_after_60s(clock):
    _mock(500)
    assert await weather.current() is None
    clock[0] += weather.RETRY_S
    respx.reset()
    route = _mock()
    assert await weather.current() == EXPECTED
    assert route.call_count == 1
