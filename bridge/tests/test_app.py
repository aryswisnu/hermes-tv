import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hermestv import app as app_mod
from hermestv import catalog, hermes, picks, weather

TV_KEY = "tv-secret"
AUTH = {"Authorization": f"Bearer {TV_KEY}"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "bridge.env"
    path.write_text(f"HERMES_URL=http://hermes.test\nHERMES_KEY=k\nTV_KEY={TV_KEY}\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(path))
    return path


@pytest.fixture
def client(env):
    with TestClient(app_mod.app) as c:
        yield c


def test_empty_tv_key_rejects_everything(env):
    env.write_text("HERMES_KEY=k\nTV_KEY=\n")
    with TestClient(app_mod.app) as client:
        assert client.get("/v1/health", headers={"Authorization": "Bearer "}).status_code == 401
        assert client.get("/v1/health", headers={}).status_code == 401


@respx.mock
def test_health_reports_hermes(client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(hermes, "_now", lambda: clock[0])
    route = respx.get("http://hermes.test/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    assert client.get("/health").json() == {"ok": True, "hermes": True}
    route.mock(side_effect=httpx.ConnectError("refused"))
    assert client.get("/health").json() == {"ok": True, "hermes": True}
    assert route.call_count == 1  # a monitor that polls /health does not reach Hermes on each call
    clock[0] += hermes.HEALTH_CACHE_S
    assert client.get("/health").json() == {"ok": True, "hermes": False}


def test_health_without_hermes_key_is_false(tmp_path, client):
    (tmp_path / "bridge.env").write_text(f"HERMES_URL=http://hermes.test\nHERMES_KEY=\nTV_KEY={TV_KEY}\n")
    assert client.get("/health").json() == {"ok": True, "hermes": False}


def _weather(monkeypatch, result) -> list[int]:
    calls = []

    async def current():
        calls.append(1)
        return result
    monkeypatch.setattr(weather, "current", current)
    return calls


def test_weather_returns_tv_shape(client, monkeypatch):
    at = datetime(2026, 10, 20, 8, 45, tzinfo=timezone(timedelta(hours=8)))
    _weather(monkeypatch, weather.Weather(temp_c=29, code=1, is_day=True, updated_at=at))
    r = client.get("/v1/weather", headers=AUTH)
    assert r.status_code == 200
    # The TV WeatherClient reads exactly these four keys.
    assert r.json() == {"temp_c": 29, "code": 1, "is_day": True, "updated_at": "2026-10-20T08:45:00+08:00"}


def test_weather_none_is_503(client, env, monkeypatch):
    env.write_text(f"TV_KEY={TV_KEY}\nLATITUDE=40.7128\nLONGITUDE=-74.006\n")
    _weather(monkeypatch, None)
    r = client.get("/v1/weather", headers=AUTH)
    assert r.status_code == 503
    assert r.json() == {"error": "weather unavailable"}


def test_weather_without_location_is_200(client, monkeypatch):
    # No LATITUDE and LONGITUDE: no weather by choice, so not 503; the TV hides the weather the same way.
    _weather(monkeypatch, None)
    r = client.get("/v1/weather", headers=AUTH)
    assert (r.status_code, r.json()) == (200, {"error": "weather unavailable"})


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TV_KEY}])
def test_weather_requires_tv_key(client, monkeypatch, headers):
    calls = _weather(monkeypatch, None)
    assert client.get("/v1/weather", headers=headers).status_code == 401
    assert calls == []


@respx.mock
def test_health_cached(client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(hermes, "_now", lambda: clock[0])
    route = respx.get("http://hermes.test/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))

    assert client.get("/v1/health", headers=AUTH).json() == {"hermes": True, "enabled": True}
    assert route.calls.last.request.headers["Authorization"] == "Bearer k"
    route.mock(side_effect=httpx.ConnectError("refused"))
    clock[0] += 59
    assert client.get("/v1/health", headers=AUTH).json() == {"hermes": True, "enabled": True}
    assert route.call_count == 1  # no second check within 60 s
    clock[0] += 2
    assert client.get("/v1/health", headers=AUTH).json() == {"hermes": False, "enabled": True}
    route.mock(return_value=httpx.Response(503))
    clock[0] += 61
    assert client.get("/v1/health", headers=AUTH).json() == {"hermes": False, "enabled": True}
    assert route.call_count == 3
    assert not any("/v1/runs" in str(c.request.url) for c in respx.calls)  # no model call


@respx.mock
def test_health_slow_hermes_is_offline(client):
    respx.get("http://hermes.test/health").mock(side_effect=httpx.ReadTimeout("slow"))
    assert client.get("/v1/health", headers=AUTH).json() == {"hermes": False, "enabled": True}
    assert hermes.HEALTH_TIMEOUT_S == 3.0


@pytest.mark.parametrize("hermes_url", ["", "HERMES_URL=\n"])
def test_health_reports_disabled(client, env, hermes_url):
    env.write_text(f"{hermes_url}HERMES_KEY=k\nTV_KEY={TV_KEY}\n")
    with respx.mock(assert_all_called=False) as mock:
        any_request = mock.route()
        assert client.get("/v1/health", headers=AUTH).json() == {"hermes": False, "enabled": False}
        assert client.get("/health").json() == {"ok": True, "hermes": False}
    assert any_request.call_count == 0


def test_minimal_env_boots(env):
    # Only TV_KEY: no TMDB, no Hermes, no location, no schedule. Every TV call still answers 200.
    env.write_text(f"TV_KEY={TV_KEY}\n")
    picks._running.clear()
    picks._last_attempt.clear()
    catalog._available.clear()
    with respx.mock(assert_all_called=False) as mock, TestClient(app_mod.app) as client:
        any_request = mock.route()
        health = client.get("/v1/health", headers=AUTH)
        films = client.get("/v1/picks?row=films", headers=AUTH)
        schedule = client.get("/v1/schedule", headers=AUTH)
        weather_r = client.get("/v1/weather", headers=AUTH)
    assert [r.status_code for r in (health, films, schedule, weather_r)] == [200] * 4
    assert health.json() == {"hermes": False, "enabled": False}
    assert (films.json()["row"], films.json()["films"]) == ("films", [])
    assert schedule.json() == {"items": []}
    assert weather_r.json() == {"error": "weather unavailable"}
    assert any_request.call_count == 0  # nothing leaves the bridge


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TV_KEY}])
def test_v1_health_requires_tv_key(client, headers):
    with respx.mock:
        route = respx.get("http://hermes.test/health").mock(return_value=httpx.Response(200))
        assert client.get("/v1/health", headers=headers).status_code == 401
        assert route.call_count == 0


def _v1_routes() -> list[tuple[str, str]]:
    """(method, concrete path) of every /v1 route, path parameters filled with a dummy value."""
    out = []
    for route in app_mod.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/v1/"):
            concrete = re.sub(r"\{[^}]+\}", "x", path)
            out += [(method, concrete) for method in sorted(route.methods - {"HEAD", "OPTIONS"})]
    return out


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TV_KEY}])
def test_every_v1_route_requires_tv_key(client, headers):
    # A new route without Depends(_auth) must fail here, whatever it does.
    routes = _v1_routes()
    assert ("GET", "/v1/picks") in routes and len(routes) >= 8  # the walk really found the routes
    for method, path in routes:
        r = client.request(method, path, headers=headers, json={})
        assert r.status_code == 401, (method, path, r.status_code)
