import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from hermestv import app as app_mod
from hermestv import exchange, history, watchlog

pytestmark = pytest.mark.anyio

ZONE = "Australia/Perth"  # UTC+8 all year
NOW = datetime(2026, 10, 22, 21, 0, tzinfo=ZoneInfo(ZONE))
NETFLIX = "https://www.netflix.com/title/81234567"
HBO = "https://play.hbomax.com/show/abc123"
YOUTUBE = "https://www.youtube.com/watch?v=vid00000001"
VAULT = "/opt/data/notes/TV"
OWNER = b"1000:1000\n"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "bridge.env"
    path.write_text(f"TV_KEY=t\nTIME_ZONE={ZONE}\nHERMES_CONTAINER=hermes-test\nHERMES_TV_DIR=/opt/data/hermes-tv\n"
                    f"HERMES_VAULT_TV_DIR={VAULT}\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(path))
    monkeypatch.setattr(exchange, "_logged", {})
    return path


class Exec:
    """Stands in for exchange._exec: answers the owner read, records each write, never runs docker."""

    def __init__(self, monkeypatch):
        self.writes: list[tuple[str, bytes]] = []
        self.fail = False
        monkeypatch.setattr(exchange, "_exec", self)

    async def __call__(self, cmd, timeout, limit, stdin=None):
        if cmd[3] == "stat":
            return OWNER
        assert cmd[:6] == ("docker", "exec", "-i", "-u", "1000:1000", "hermes-test")
        self.writes.append((cmd[-1], stdin))
        if self.fail:
            raise exchange._Failed("exit code is not 0")
        return b""


@pytest.fixture
def docker(env, monkeypatch):
    return Exec(monkeypatch)


def _open_three() -> None:
    history.opened("film", "Talk to Me", 2022, None, NETFLIX, "night", now=NOW - timedelta(hours=3))
    history.opened("series", "The Last of Us", 2023, None, HBO, "lunch", now=NOW - timedelta(hours=2))
    history.opened("video", "Unsolved case 1", None, "vid00000001", YOUTUBE, "night", now=NOW - timedelta(hours=1))


async def _settle() -> None:
    if watchlog._pending:
        await watchlog._pending


def test_build_shape_and_order(env):
    _open_three()
    history.hide("film", "The Hunt", 2020, None, None)

    data = watchlog.build(NOW.astimezone(timezone.utc))

    # Times are in the TIME_ZONE setting, whatever zone "now" comes in.
    assert data["updated_at"] == "2026-10-22T21:00:00+08:00"
    assert data["opened"] == [
        {"title": "Unsolved case 1", "year": None, "kind": "video", "service": "youtube",
         "opened_at": "2026-10-22T20:00:00+08:00"},
        {"title": "The Last of Us", "year": 2023, "kind": "series", "service": "hbomax",
         "opened_at": "2026-10-22T19:00:00+08:00"},
        {"title": "Talk to Me", "year": 2022, "kind": "film", "service": "netflix",
         "opened_at": "2026-10-22T18:00:00+08:00"},
    ]
    assert data["hidden"] == [{"title": "The Hunt", "year": 2020, "kind": "film"}]
    # History records carry no genres today: the taste has none rather than made-up ones.
    assert data["taste"] == {"kinds": {"film": 1, "series": 1, "video": 1},
                             "services": {"netflix": 1, "hbomax": 1, "youtube": 1},
                             "slots": {"night": 2, "lunch": 1}}

    entries = history._history()
    entries[0]["genres"] = ["horror", "thriller"]
    entries[1]["genres"] = ["drama", "horror"]
    history._write_history(entries)
    assert watchlog.build(NOW)["taste"]["genres"] == {"horror": 2, "thriller": 1, "drama": 1}

    history._write_history([{"time": (NOW - timedelta(minutes=n)).isoformat(), "kind": "film", "title": f"Film {n}",
                             "year": 2000, "video_id": None, "url": NETFLIX, "slot": "night"}
                            for n in reversed(range(history.HISTORY_MAX + 5))])
    opened = watchlog.build(NOW)["opened"]
    assert len(opened) == watchlog.MAX_ITEMS
    assert [o["title"] for o in opened[:2]] == ["Film 0", "Film 1"]


def test_build_has_no_urls_or_ids_beyond_title_year(env):
    _open_three()
    history.hide("video", "Unsolved case 2", None, "vid00000002", None)
    history.hide("video", "Any", None, None, "Some Channel")

    text = json.dumps(watchlog.build(NOW))

    for secret in ("http", "netflix.com", "vid0000000", "url", "video_id", "Some Channel", "time\""):
        assert secret not in text
    data = json.loads(text)
    assert {k for o in data["opened"] for k in o} == {"title", "year", "kind", "service", "opened_at"}
    assert data["hidden"] == [{"title": "Unsolved case 2", "year": None, "kind": "video"}]


async def test_schedule_debounces_to_one_write(docker, monkeypatch):
    monkeypatch.setattr(watchlog, "DEBOUNCE_S", 0.05)
    _open_three()
    history.hide("film", "The Hunt", 2020, None, None)
    # The open request does not wait for docker: the write happens later, in the background.
    assert docker.writes == []

    await _settle()

    assert len(docker.writes) == 1
    path, body = docker.writes[0]
    assert path == "/opt/data/hermes-tv/watch.json"
    data = json.loads(body)
    assert [o["title"] for o in data["opened"]] == ["Unsolved case 1", "The Last of Us", "Talk to Me"]
    assert data["hidden"] == [{"title": "The Hunt", "year": 2020, "kind": "film"}]

    history.opened("film", "The Hunt 2", 2025, None, NETFLIX, "night", now=NOW)
    await _settle()
    assert len(docker.writes) == 2
    history.hide("series", "Lost", 2004, None, None)
    await _settle()
    assert len(docker.writes) == 3
    assert {"title": "Lost", "year": 2004, "kind": "series"} in json.loads(docker.writes[2][1])["hidden"]


async def test_write_failure_keeps_local_history(docker, monkeypatch):
    monkeypatch.setattr(watchlog, "DEBOUNCE_S", 0.01)
    docker.fail = True
    history.opened("film", "Talk to Me", 2022, None, NETFLIX, "night", now=NOW)
    await _settle()
    assert len(docker.writes) == 1
    assert [e["title"] for e in history._history()] == ["Talk to Me"]

    # The next open writes the whole list again, the failed open included.
    docker.fail = False
    history.opened("film", "The Hunt", 2020, None, NETFLIX, "night", now=NOW + timedelta(minutes=1))
    await _settle()
    assert len(docker.writes) == 2
    assert [o["title"] for o in json.loads(docker.writes[1][1])["opened"]] == ["The Hunt", "Talk to Me"]


async def test_feature_off_makes_no_docker_call(tmp_path, monkeypatch):
    path = tmp_path / "bridge.env"
    path.write_text("TV_KEY=t\nHERMES_CONTAINER=hermes-test\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(path))
    calls = []

    async def record(*args, **kwargs):
        calls.append(args)
        return True

    monkeypatch.setattr(exchange, "write", record)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", record)
    monkeypatch.setattr(watchlog, "DEBOUNCE_S", 0.01)

    _open_three()
    history.hide("film", "The Hunt", 2020, None, None)
    assert watchlog._pending is None
    assert await watchlog.vault_once() is False
    await asyncio.sleep(0.05)
    assert calls == []
    assert len(history._history()) == 3


def test_lifespan_starts_vault_only_when_set(env, monkeypatch):
    started = []

    def daily():
        started.append(True)
        return asyncio.sleep(3600)

    monkeypatch.setattr(watchlog, "vault_daily", daily)
    with TestClient(app_mod.app):
        pass
    assert started == [True]

    env.write_text("TV_KEY=t\nHERMES_TV_DIR=/opt/data/hermes-tv\n")
    with TestClient(app_mod.app):
        pass
    assert started == [True]


def test_next_vault_is_2350_in_the_time_zone_setting(env):
    zone = ZoneInfo(ZONE)
    assert watchlog.next_vault(datetime(2026, 10, 22, 23, 49, tzinfo=zone)) == datetime(2026, 10, 22, 23, 50,
                                                                                       tzinfo=zone)
    assert watchlog.next_vault(datetime(2026, 10, 22, 23, 50, tzinfo=zone)) == datetime(2026, 10, 23, 23, 50,
                                                                                       tzinfo=zone)
    # 16:00 UTC is already 00:00 of the next day in UTC+8.
    utc_16 = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)
    assert watchlog.next_vault(utc_16) == datetime(2026, 10, 23, 23, 50, tzinfo=zone)

    env.write_text("TV_KEY=t\nTIME_ZONE=UTC\n")
    assert watchlog.next_vault(utc_16) == datetime(2026, 10, 22, 23, 50, tzinfo=timezone.utc)


def test_markdown_tables(env):
    history._write_history([{"time": (NOW - timedelta(minutes=n)).isoformat(), "kind": "film",
                             "title": f"Film {n}", "year": 2000, "video_id": None, "url": NETFLIX, "slot": "night"}
                            for n in reversed(range(1, 105))])
    history.opened("series", "A | B\nC", None, None, HBO, "lunch", now=NOW)

    log, taste = watchlog.markdown(watchlog.build(NOW))

    lines = log.splitlines()
    assert lines[0].startswith("# ")
    assert "_Written by the TV bridge._" in lines
    assert "Updated 2026-10-22 21:00 Australia/Perth." in lines
    header = lines.index("| Date | Title | Year | Kind | Service |")
    assert lines[header + 1] == "|---|---|---|---|---|"
    rows = [line for line in lines[header + 2:] if line.startswith("| ")]
    assert len(rows) == watchlog.NOTE_ROWS
    # A title cannot break the table: its pipe is escaped and its newline is a space.
    assert rows[0] == r"| 2026-10-22 | A \| B C |  | series | hbomax |"
    assert rows[1] == "| 2026-10-22 | Film 1 | 2000 | film | netflix |"
    assert "http" not in log

    lines = taste.splitlines()
    assert lines[0].startswith("# ")
    assert "_Written by the TV bridge._" in lines
    assert "| film | 104 |" in lines and "| series | 1 |" in lines and "| video | 0 |" in lines
    assert "| netflix | 104 |" in lines and "| hbomax | 1 |" in lines
    assert "| night | 104 |" in lines and "| lunch | 1 |" in lines
    assert "Genre" not in taste


async def test_vault_once_writes_both_notes(docker, monkeypatch):
    monkeypatch.setattr(watchlog, "DEBOUNCE_S", 0.01)
    _open_three()
    await _settle()
    docker.writes.clear()
    assert await watchlog.vault_once() is True
    assert [p for p, _ in docker.writes] == [f"{VAULT}/Watch Log.md", f"{VAULT}/Taste.md"]
    assert b"| Talk to Me | 2022 | film | netflix |" in docker.writes[0][1]
    docker.fail = True
    assert await watchlog.vault_once() is False


def test_markdown_cells_cannot_link_or_inject(env):
    history.opened("film", "[[Private Note]] <script>x</script> `code` a|b c\\", 2022, None, NETFLIX, "night", now=NOW)

    log, _ = watchlog.markdown(watchlog.build(NOW))

    row = next(line for line in log.splitlines() if "Private" in line)
    # No note link, no HTML tag, no code span, and the pipe stays inside its cell.
    assert row == (r"| 2026-10-22 | \[\[Private Note\]\] \<script\>x\</script\> \`code\` a\|b c\\ | 2022 | film "
                   r"| netflix |")


def test_failures_never_reach_the_open(env, monkeypatch, caplog):
    def broken():
        raise ValueError("bad env file")

    monkeypatch.setattr(watchlog, "config", SimpleNamespace(load=broken))
    history.opened("film", "Talk to Me", 2022, None, NETFLIX, "night", now=NOW)
    history.hide("film", "The Hunt", 2020, None, None)
    assert [e["title"] for e in history._history()] == ["Talk to Me"]
    assert "settings unreadable: ValueError" in caplog.text
    assert "bad env file" not in caplog.text


async def test_build_failure_is_logged_by_type(docker, monkeypatch, caplog):
    monkeypatch.setattr(watchlog, "DEBOUNCE_S", 0.01)

    def broken(now):
        raise ValueError("Talk to Me")

    monkeypatch.setattr(watchlog, "build", broken)
    history.opened("film", "Talk to Me", 2022, None, NETFLIX, "night", now=NOW)
    task = watchlog._pending
    await task
    assert task.exception() is None
    assert docker.writes == []
    assert "watch.json failed: ValueError" in caplog.text
    assert "Talk to Me" not in caplog.text


async def test_shutdown_cancels_a_waiting_write(env):
    async with app_mod._lifespan(app_mod.app):
        history.opened("film", "Talk to Me", 2022, None, NETFLIX, "night", now=NOW)
        task = watchlog._pending
        assert task is not None
    await asyncio.sleep(0)
    assert task.cancelled()
