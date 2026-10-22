import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hermestv import app as app_mod
from hermestv import history, slots

TV_KEY = "tv-secret"
AUTH = {"Authorization": f"Bearer {TV_KEY}"}
FILM = {"kind": "film", "title": "Talk to Me", "year": 2022, "url": "https://www.netflix.com/title/81234567"}
VIDEO = {"kind": "video", "title": "Case file 1", "video_id": "vid00000001",
         "url": "https://www.youtube.com/watch?v=vid00000001"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text(f"HERMES_URL=http://hermes.test\nHERMES_KEY=k\nTV_KEY={TV_KEY}\nTIME_ZONE=Asia/Tokyo\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    monkeypatch.setattr(slots, "current", lambda now=None: slots.SLOTS["night"])
    with TestClient(app_mod.app) as c:
        yield c


def _lines() -> list[dict]:
    return [json.loads(line) for line in (history.STATE_DIR / "history.jsonl").read_text().splitlines()]


def test_opened_appends_and_trims(client):
    assert client.post("/v1/opened", json=FILM, headers=AUTH).json() == {"ok": True}
    assert client.post("/v1/opened", json=VIDEO, headers=AUTH).status_code == 200

    first, second = _lines()
    assert {k: first[k] for k in ("kind", "title", "year", "video_id", "url", "slot")} == {
        **FILM, "video_id": None, "slot": "night"}
    assert datetime.fromisoformat(first["time"]).utcoffset() == timedelta(hours=9)  # TIME_ZONE
    assert (second["kind"], second["video_id"]) == ("video", "vid00000001")

    for n in range(history.HISTORY_MAX + 5):
        history.opened("film", f"Film {n}", 2000, None, "https://www.netflix.com/title/1", "night")
    lines = _lines()
    # The file stays small: the oldest lines go, the newest stay in order.
    assert len(lines) == history.HISTORY_MAX
    assert lines[-1]["title"] == f"Film {history.HISTORY_MAX + 4}"
    assert lines[0]["title"] == "Film 5"
    assert not list(history.STATE_DIR.glob("*.tmp"))


def test_recent_titles_are_the_last_20_distinct(client):
    for n in range(25):
        history.opened("film", f"Film {n}", 2000, None, "u", "night")
    history.opened("film", "Film 24", 2000, None, "u", "night")
    hints = history.recent_titles()
    assert len(hints) == 20
    assert hints[:2] == ["Film 24", "Film 23"]


def test_opened_window_is_90_days():
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    history.opened("film", "Old Film", 2001, None, "u", "night", now=now - timedelta(days=91))
    history.opened("film", "Recent Film", 2002, None, "u", "night", now=now - timedelta(days=89))
    blocked = history.blocked(now)
    assert blocked("film", "Recent Film", 2002)
    assert blocked("film", "recent film!", 2002)  # normalised title
    assert not blocked("film", "Recent Film", 2003)  # another year is another film
    assert not blocked("series", "Recent Film", 2002)  # a series of that name is another item
    assert not blocked("film", "Old Film", 2001)


def test_hide_writes_hidden_json(client):
    assert client.post("/v1/hide", json={"kind": "series", "title": "Squid Game", "year": 2021},
                       headers=AUTH).json() == {"ok": True}
    client.post("/v1/hide", json={"kind": "video", "title": "Case file", "video_id": "vid00000001"}, headers=AUTH)
    client.post("/v1/hide", json={"kind": "video", "title": "Case file", "video_id": "vid00000002",
                                  "channel": "Night Files"}, headers=AUTH)
    client.post("/v1/hide", json={"kind": "series", "title": "Squid Game", "year": 2021}, headers=AUTH)

    data = json.loads((history.STATE_DIR / "hidden.json").read_text())
    assert sorted(data["items"]) == ["t:series:squidgame:2021", "v:vid00000001"]  # a second hide is no new entry
    assert list(data["channels"]) == ["nightfiles"]
    blocked = history.blocked()
    assert blocked("series", "Squid Game", 2021) and blocked("video", "x", None, "vid00000001")
    assert not blocked("film", "Squid Game", 2021)
    assert blocked("video", "Other", None, "vid00000009", "Night  files")
    assert not blocked("video", "x", None, "vid00000002", "Other Channel")  # the channel hide does not hide by id


def test_hide_without_year_matches_any_year(client):
    client.post("/v1/hide", json={"kind": "film", "title": "Smile"}, headers=AUTH)
    assert history.blocked()("film", "Smile", 2022)


@pytest.mark.parametrize("body", [
    {"kind": "video", "title": "x"},  # a video needs its id or a channel
    {"kind": "video", "title": "x", "video_id": "not-an-id"},
    {"kind": "film", "title": ""},
    {"kind": "song", "title": "x"},
    {"kind": "film", "title": "!!! ???"},  # no letters or digits: no key
    {"kind": "film", "title": "Smile", "video_id": "vid00000001"},  # an id only on a video
    {"kind": "series", "title": "Smile", "channel": "Night Files"},  # a channel only on a video
    {"kind": "video", "title": "x", "channel": " -- "},
])
def test_hide_rejects_bad_bodies(client, body):
    assert client.post("/v1/hide", json=body, headers=AUTH).status_code == 422
    assert not (history.STATE_DIR / "hidden.json").exists()


def test_opened_rejects_bad_bodies(client):
    assert client.post("/v1/opened", json={**FILM, "url": ""}, headers=AUTH).status_code == 422
    assert client.post("/v1/opened", json={**VIDEO, "video_id": None}, headers=AUTH).status_code == 422
    assert client.post("/v1/opened", json={**FILM, "video_id": "vid00000001"}, headers=AUTH).status_code == 422
    assert client.post("/v1/opened", json={**FILM, "title": "..."}, headers=AUTH).status_code == 422
    assert not (history.STATE_DIR / "history.jsonl").exists()


@pytest.mark.parametrize("path, body", [("/v1/opened", FILM), ("/v1/hide", FILM)])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TV_KEY}])
def test_history_routes_require_tv_key(client, path, body, headers):
    assert client.post(path, json=body, headers=headers).status_code == 401
    assert not history.STATE_DIR.exists() or not any(history.STATE_DIR.iterdir())


def test_state_files_are_private(client):
    client.post("/v1/opened", json=FILM, headers=AUTH)
    client.post("/v1/hide", json=FILM, headers=AUTH)
    assert history.STATE_DIR.stat().st_mode & 0o777 == 0o700
    for name in ("history.jsonl", "hidden.json"):
        assert (history.STATE_DIR / name).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"items": [1], "channels": {}}'])
def test_corrupt_hidden_json_is_set_aside(client, caplog, content):
    history.STATE_DIR.mkdir(parents=True)
    (history.STATE_DIR / "hidden.json").write_text(content)

    assert not history.blocked()("film", "Talk to Me", 2022)
    assert not history.blocked()("film", "Talk to Me", 2022)  # the second read does not warn again
    assert client.post("/v1/hide", json=FILM, headers=AUTH).status_code == 200

    assert (history.STATE_DIR / "hidden.json.bad").read_text() == content
    assert list(json.loads((history.STATE_DIR / "hidden.json").read_text())["items"]) == ["t:film:talktome:2022"]
    assert len([r for r in caplog.records if "corrupt" in r.getMessage()]) == 1


def test_corrupt_history_lines_are_set_aside(client, caplog):
    history.STATE_DIR.mkdir(parents=True)
    good = json.dumps({"time": "2026-10-20T12:00:00+08:00", "kind": "film", "title": "Smile", "year": 2022,
                       "video_id": None, "url": "u", "slot": "night"})
    (history.STATE_DIR / "history.jsonl").write_text(f"{good}\n{{broken\n[1]\n")

    assert history.blocked()("film", "Smile", 2022)
    assert history.blocked()("film", "Smile", 2022)
    assert client.post("/v1/opened", json=FILM, headers=AUTH).status_code == 200

    assert "{broken" in (history.STATE_DIR / "history.jsonl.bad").read_text()
    assert [e["title"] for e in _lines()] == ["Smile", "Talk to Me"]
    assert len([r for r in caplog.records if "corrupt" in r.getMessage()]) == 1
