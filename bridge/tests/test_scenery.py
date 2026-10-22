from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from hermestv import app as app_mod
from hermestv import scenery

TV_KEY = "tv-secret"
AUTH = {"Authorization": f"Bearer {TV_KEY}"}
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture
def photos():
    for period in scenery.PERIODS:
        (scenery.DIR / period).mkdir(parents=True)
    (scenery.DIR / "evening" / "02-tokyo.jpg").write_bytes(JPEG)
    (scenery.DIR / "evening" / "01-fuji.jpg").write_bytes(JPEG)
    (scenery.DIR / "evening" / "notes.txt").write_text("not a photo")
    (scenery.DIR / "morning" / "01-dawn.jpg").write_bytes(JPEG)
    (scenery.DIR / "CREDITS.md").write_text("credits")
    (scenery.DIR.parent / "secret.jpg").write_bytes(b"outside the scenery folder")
    return scenery.DIR


@pytest.fixture
def client(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text(f"HERMES_URL=http://hermes.test\nHERMES_KEY=k\nTV_KEY={TV_KEY}\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    with TestClient(app_mod.app) as c:
        yield c


@pytest.fixture
def tokyo(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text(f"TV_KEY={TV_KEY}\nTIME_ZONE=Asia/Tokyo\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))


def _local(h: int, m: int) -> datetime:
    return datetime(2026, 10, 20, h, m, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.mark.parametrize("hhmm, period", [
    ("04:59", "evening"), ("05:00", "morning"), ("10:59", "morning"), ("11:00", "day"),
    ("14:59", "day"), ("15:00", "afternoon"), ("17:59", "afternoon"), ("18:00", "evening"),
    ("23:59", "evening"), ("00:00", "evening"),
])
def test_scenery_period_boundaries(tokyo, hhmm, period):
    h, m = map(int, hhmm.split(":"))
    assert scenery.period(_local(h, m)) == period
    # The same instant in UTC gives the same period: the period follows TIME_ZONE (UTC+9 here).
    assert scenery.period(_local(h, m).astimezone(timezone.utc)) == period


def test_scenery_lists_the_current_period(photos, client, monkeypatch):
    monkeypatch.setattr(scenery, "period", lambda now=None: "evening")
    r = client.get("/v1/scenery", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"period": "evening", "items": [
        {"id": "01-fuji", "url": "/v1/scenery/evening/01-fuji.jpg"},
        {"id": "02-tokyo", "url": "/v1/scenery/evening/02-tokyo.jpg"},
    ]}


def test_scenery_empty_or_missing_folder_has_no_items(client, monkeypatch):
    monkeypatch.setattr(scenery, "period", lambda now=None: "day")
    r = client.get("/v1/scenery", headers=AUTH)
    assert r.json() == {"period": "day", "items": []}


def test_scenery_serves_the_photo(photos, client):
    r = client.get("/v1/scenery/evening/01-fuji.jpg", headers=AUTH)
    assert r.status_code == 200
    assert r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "max-age=86400"


@pytest.mark.parametrize("path", [
    "/v1/scenery/evening/..%2Fsecret.jpg",
    "/v1/scenery/evening/%2e%2e%2f%2e%2e%2fsecret.jpg",
    "/v1/scenery/..%2F..%2Fsecret.jpg",
    "/v1/scenery/night/01-fuji.jpg",
    "/v1/scenery/evening/notes.txt",
    "/v1/scenery/evening/03-missing.jpg",
    "/v1/scenery/morning/01-fuji.jpg",
    "/v1/scenery/evening/01 fuji.jpg",
])
def test_scenery_rejects_traversal(photos, client, path):
    assert client.get(path, headers=AUTH).status_code == 404


def test_scenery_rejects_traversal_and_links_out_of_the_folder(photos):
    assert scenery.photo("evening", "01-fuji.jpg") == (photos / "evening" / "01-fuji.jpg").resolve()
    for period, name in [("evening", "../secret.jpg"), ("evening", "../../secret.jpg"), ("..", "secret.jpg"),
                         ("evening", "/etc/x.jpg"), ("evening", "..jpg"), ("Evening", "01-fuji.jpg")]:
        assert scenery.photo(period, name) is None, (period, name)
    # A link inside the folder that points outside it is refused too.
    (photos / "evening" / "link.jpg").symlink_to(photos.parent / "secret.jpg")
    assert scenery.photo("evening", "link.jpg") is None
    assert "link" not in [i["id"] for i in scenery.items("evening")]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TV_KEY}])
@pytest.mark.parametrize("path", ["/v1/scenery", "/v1/scenery/evening/01-fuji.jpg"])
def test_scenery_auth(photos, client, headers, path):
    r = client.get(path, headers=headers)
    assert r.status_code == 401
    assert JPEG not in r.content
