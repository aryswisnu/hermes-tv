import asyncio
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from hermestv import app as app_mod
from hermestv import hermes, schedule

pytestmark = pytest.mark.anyio

TV_KEY = "tv-secret"
AUTH = {"Authorization": f"Bearer {TV_KEY}"}
ZONE = "Australia/Perth"  # UTC+8 all year, like the +08:00 times of the jobs below
TODAY = datetime(2026, 10, 20, 12, 30, tzinfo=ZoneInfo(ZONE))  # a Tuesday
TOMORROW = "2026-10-21"
SECRET = "private-detail-xyz"
REAL_EXEC = schedule._exec
REAL_START_RUN, REAL_PROBE = hermes.start_run, hermes.probe
LISTED = ("Water the plants", "Pay rent", "Book club", "Library books due", "Car service", "Dentist visit",
          "Walk the dog", "Team lunch")
JOBS_ARGV = ("docker", "exec", "hermes", "cat", "/opt/data/cron/jobs.json")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _job(name: str, display: str, script: str | None = None, enabled: bool = True, prompt: str = "") -> dict:
    kind = "once" if display.startswith("once") else "cron"
    next_run = f"{display.removeprefix('once at ').replace(' ', 'T')}:00+08:00" if kind == "once" \
        else "2026-10-21T07:00:00+08:00"
    when = {"kind": kind, "display": display} | ({"expr": display} if kind == "cron" else {})
    return {"id": name[:8], "name": name, "enabled": enabled, "prompt": prompt, "schedule_display": display,
            "schedule": when, "next_run_at": next_run, "no_agent": script is not None, "script": script}


JOBS = [
    _job("Morning News Briefing", "0 6 * * *", prompt="Brief the news."),
    _job("Water the plants", "0 7 * * *", "plants.sh"),
    _job("Pay rent", "0 8 10 * *", prompt=f"Pay the rent {SECRET}."),
    _job("Book club", "0 8 1 * *", enabled=False),
    _job("Library books due", "0 8 1 * *"),
    _job("pay rent", "0 8 * * *", prompt="A near-match name."),
    _job("Car service", "once at 2026-10-20 09:00"),
    _job("Dentist visit", "once at 2027-03-29 09:00"),
]


class Fake:
    def __init__(self, monkeypatch, tmp_path):
        self.calls: list[tuple] = []
        self.gates: list[asyncio.Event] = []  # one per jobs read, in order
        self.gate: asyncio.Event | None = None
        self.clock = [1000.0]
        self.now = TODAY
        self.jobs_file = tmp_path / "jobs.json"
        self.read = ("cat", str(self.jobs_file))  # what a jobs read runs in place of docker
        self.env = tmp_path / "bridge.env"
        self.write_jobs(JOBS)
        self.write_env()
        monkeypatch.setenv("HERMESTV_ENV_FILE", str(self.env))
        monkeypatch.setattr(schedule, "_now", lambda: self.clock[0])
        monkeypatch.setattr(schedule, "_local_now", lambda: self.now)
        monkeypatch.setattr(schedule, "_exec", self._exec)

        async def no_hermes(*args, **kwargs):
            raise AssertionError("the schedule must not ask Hermes")
        monkeypatch.setattr(hermes, "start_run", no_hermes)
        monkeypatch.setattr(hermes, "probe", no_hermes)

    def write_jobs(self, jobs: list[dict]) -> None:
        self.jobs_file.write_text(json.dumps({"jobs": jobs, "updated_at": "2026-10-20T08:00:00+08:00"}))

    def write_env(self, extra: str = f"SCHEDULE_JOBS={','.join(LISTED)}\n") -> None:
        self.env.write_text(f"HERMES_URL=http://hermes.test\nHERMES_KEY=k\nTV_KEY={TV_KEY}\nTIME_ZONE={ZONE}\n{extra}")

    async def _exec(self, cmd, timeout, limit):
        self.calls.append(tuple(cmd))
        await asyncio.sleep(0.01)
        if gate := self.gates.pop(0) if self.gates else self.gate:
            await gate.wait()
        return await REAL_EXEC(self.read, timeout, limit)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    return Fake(monkeypatch, tmp_path)


async def _settle():
    if schedule._running:
        await schedule._running[1]
    await asyncio.gather(*schedule._background)


@pytest.fixture
async def client(fake):
    transport = httpx.ASGITransport(app=app_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as c:
        yield c


@pytest.mark.parametrize("extra", ["", "SCHEDULE_JOBS=\n", "SCHEDULE_JOBS= , ,\n"])
async def test_empty_allowlist_reads_nothing(fake, client, extra):
    fake.write_env(extra)
    r = await client.get("/v1/schedule", headers=AUTH)
    assert (r.status_code, r.json()) == (200, {"items": []})
    assert await schedule.upcoming() == []
    assert fake.calls == []


async def test_schedule_off_without_hermes(fake, client):
    fake.env.write_text(f"TV_KEY={TV_KEY}\nTIME_ZONE={ZONE}\nSCHEDULE_JOBS={','.join(LISTED)}\n")
    assert await schedule.upcoming() == []
    r = await client.get("/v1/schedule", headers=AUTH)
    assert (r.status_code, r.json()) == (200, {"items": []})
    assert fake.calls == []  # no docker call


async def test_reads_jobs_once_with_fixed_argv(fake):
    assert [(i.date, i.time, i.job, i.title, i.note) for i in await schedule.upcoming()] == [
        ("2026-10-20", "09:00", "Car service", "Car service", "")]
    assert fake.calls == [JOBS_ARGV]


async def test_hermes_container_names_the_container(fake):
    fake.write_env("SCHEDULE_JOBS=Car service\nHERMES_CONTAINER=hermes-agent_2.x\n")
    assert [i.job for i in await schedule.upcoming()] == ["Car service"]
    assert fake.calls == [("docker", "exec", "hermes-agent_2.x", "cat", "/opt/data/cron/jobs.json")]


@pytest.mark.parametrize("container", ["-it", ".hermes", "hermes;rm -rf /", "her mes", "hermes\\n", "hérmes"])
async def test_bad_hermes_container_reads_nothing(fake, caplog, container):
    fake.write_env(f"SCHEDULE_JOBS=Car service\nHERMES_CONTAINER={container}\n")
    caplog.set_level("INFO")
    assert await schedule.upcoming() == []
    fake.clock[0] += schedule.CACHE_S + 1
    assert await schedule.upcoming() == []
    assert fake.calls == []
    assert caplog.text.count("HERMES_CONTAINER is not a container name") == 1
    assert container not in caplog.text


async def test_cron_job_today_and_tomorrow(fake):
    fake.write_jobs([_job("Walk the dog", "30 18 * * *"), _job("Team lunch", "15 12 * * 3")])
    assert [(i.date, i.time, i.job) for i in await schedule.upcoming()] == [
        ("2026-10-20", "18:30", "Walk the dog"), (TOMORROW, "12:15", "Team lunch"), (TOMORROW, "18:30", "Walk the dog")]


@pytest.mark.parametrize("now, expected", [
    (TODAY.replace(day=9), [("2026-10-10", "08:00", "Pay rent")]),
    (TODAY.replace(day=10), [("2026-10-10", "08:00", "Pay rent")]),
    (TODAY.replace(day=31), [("2026-11-01", "08:00", "Library books due")]),  # Book club is disabled
    (TODAY.replace(month=12, day=31), [("2027-01-01", "08:00", "Library books due")]),
    (TODAY.replace(day=1), [("2026-10-01", "08:00", "Library books due")]),
    (TODAY.replace(day=2), []),
])
async def test_cron_jobs_on_their_day(fake, now, expected):
    fake.now = now
    assert [(i.date, i.time, i.job) for i in await schedule.upcoming()] == expected


async def test_once_job(fake):
    car = {**_job("Car service", "once at 2026-10-21 09:00"), "next_run_at": "2026-10-20T02:30:00+00:00"}
    dentist = {**_job("Dentist visit", "once at 2026-10-21 11:15"), "next_run_at": None}
    lunch = {**_job("Team lunch", "once at 2026-10-20 09:00"), "next_run_at": "2027-04-19T09:00:00+08:00"}
    fake.write_jobs([car, dentist, lunch])
    # next_run_at wins (10:30 in TIME_ZONE); the schedule text is only a fallback.
    assert [(i.date, i.time, i.job) for i in await schedule.upcoming()] == [
        ("2026-10-20", "10:30", "Car service"), (TOMORROW, "11:15", "Dentist visit")]


async def test_once_bad_date_is_skipped(fake):
    bad = {**_job("Car service", "once at 2026-02-31 10:00"), "next_run_at": None}
    fake.write_jobs([bad, _job("Dentist visit", "once at 2026-10-20 11:15")])
    assert [i.job for i in await schedule.upcoming()] == ["Dentist visit"]


async def test_disabled_job_hidden(fake):
    fake.write_jobs([_job("Walk the dog", "0 7 * * *", enabled=False),
                     {**_job("Pay rent", "0 8 * * *"), "enabled": "true"},
                     _job("Car service", "once at 2026-10-20 09:00", enabled=False), _job("Team lunch", "0 12 * * *")])
    assert {i.job for i in await schedule.upcoming()} == {"Team lunch"}


async def test_unlisted_job_hidden(fake):
    fake.write_jobs([_job("Morning News Briefing", "0 6 * * *"), _job("pay rent", "0 8 * * *"),
                     _job("Pay rent ", "0 8 * * *"), _job("Team", "0 8 * * *"),
                     {**_job("x", "0 7 * * *"), "name": ["Pay rent"]}, {**_job("x", "0 7 * * *"), "name": None},
                     _job("Team lunch", "0 12 * * *")])
    assert {i.job for i in await schedule.upcoming()} == {"Team lunch"}


@pytest.mark.parametrize("script", ["plants.sh", None, "", ["plants.sh"]])
async def test_script_job_skipped(fake, caplog, script):
    # A no_agent job runs a script, not its schedule: the bridge cannot tell what it says or when.
    fake.write_jobs([{**_job("Water the plants", "0 7 * * *", "plants.sh"), "script": script},
                     _job("Team lunch", "0 12 * * *")])
    caplog.set_level("INFO")
    assert {i.job for i in await schedule.upcoming()} == {"Team lunch"}
    assert "Water the plants is a script job, skipped" in caplog.text
    assert caplog.records[0].levelname == "WARNING"


async def test_once_script_job_skipped(fake, caplog):
    fake.write_jobs([_job("Car service", "once at 2026-10-20 09:00", "car.sh"), _job("Team lunch", "0 12 * * *")])
    assert {i.job for i in await schedule.upcoming()} == {"Team lunch"}
    assert "Car service is a script job, skipped" in caplog.text


async def test_agent_flag_must_be_false(fake):
    no_flag = {k: v for k, v in _job("Water the plants", "0 7 * * *").items() if k != "no_agent"}
    fake.write_jobs([no_flag, _job("Team lunch", "0 12 * * *")])
    assert {i.job for i in await schedule.upcoming()} == {"Team lunch"}


def test_cron_reads_numbers_stars_and_lists_only():
    def cron(expr):
        return schedule._cron({"schedule": {"kind": "cron", "expr": expr, "display": expr}})

    assert cron("0 8 10 * *") == ("08:00", [{10}, None, None])
    assert cron("30 7 1,15 2,8 *") == ("07:30", [{1, 15}, {2, 8}, None])
    for bad in ("*/5 * * * *", "0 8 1-5 * *", "0 8 32 * *", "0 * * * *", "0 7,19 * * *", "0 8 10 *", "0 8 ١ * *",
                "every 2h", ""):
        assert cron(bad) is None, bad
    on = schedule._cron_on
    assert on([None, None, {1}], date(2026, 10, 19)) and not on([None, None, {1}], date(2026, 10, 20))  # Monday
    assert on([None, None, {0}], date(2026, 10, 25)) and on([None, None, {7}], date(2026, 10, 25))  # Sunday
    assert on([{10}, {10}, None], date(2026, 10, 10)) and not on([{10}, {10}, None], date(2026, 11, 10))
    assert on([{10}, None, {1}], date(2026, 10, 19))  # day 19 is a Monday: either day field matches


async def test_unreadable_cron_is_skipped_and_logged(fake, caplog):
    fake.now = TODAY.replace(day=9)
    fake.write_jobs([_job("Pay rent", "*/30 8 10 * *"), _job("Library books due", "0 8 10 * *")])
    caplog.set_level("INFO")
    assert [i.job for i in await schedule.upcoming()] == ["Library books due"]
    assert "Pay rent has a schedule the bridge cannot read" in caplog.text


async def test_schedule_cached_per_day(fake):
    first = await schedule.upcoming()
    assert len(fake.calls) == 1
    fake.write_jobs([_job("Team lunch", "0 12 * * *")])  # not read again within 30 min
    fake.clock[0] += schedule.CACHE_S - 1
    assert await schedule.upcoming() == first and len(fake.calls) == 1
    fake.clock[0] += 2
    assert [(i.date, i.job) for i in await schedule.upcoming()] == [
        ("2026-10-20", "Team lunch"), (TOMORROW, "Team lunch")]
    assert len(fake.calls) == 2
    fake.now = TODAY.replace(day=21)  # a new day is a new question, before the 30 min
    assert [(i.date, i.job) for i in await schedule.upcoming()] == [
        (TOMORROW, "Team lunch"), ("2026-10-22", "Team lunch")]
    assert len(fake.calls) == 3


async def test_failure_keeps_last(fake, caplog):
    last = await schedule.upcoming()
    assert last
    fake.clock[0] += schedule.CACHE_S + 1
    fake.read = ("false",)
    assert await schedule.upcoming() == last
    assert "refresh failed" in caplog.text


async def test_schedule_nothing_is_empty(fake):
    fake.read = ("false",)
    assert await schedule.upcoming() == []


async def test_schedule_last_list_is_for_today_only(fake):
    assert await schedule.upcoming()  # the Car service job on 20 Oct
    fake.read = ("false",)
    fake.now = TODAY.replace(day=21)
    assert await schedule.upcoming() == []


async def test_schedule_read_is_bounded(fake, monkeypatch):
    fake.read = ("sleep", "5")
    monkeypatch.setattr(schedule, "READ_TIMEOUT_S", 0.2)
    assert await schedule.upcoming() == []
    schedule._reset()
    fake.write_jobs(JOBS + [_job("Pay rent", "0 8 10 * *", prompt="x" * 2000)])
    fake.read = ("cat", str(fake.jobs_file))
    monkeypatch.setattr(schedule, "MAX_BYTES", 1000)
    assert await schedule.upcoming() == []


async def test_schedule_logs_counts_only(fake, caplog):
    fake.now = TODAY.replace(day=9)
    caplog.set_level("INFO")
    assert [i.job for i in await schedule.upcoming()] == ["Pay rent"]
    assert "schedule:" in caplog.text and SECRET not in caplog.text


async def test_schedule_route_shape(fake, client):
    fake.write_jobs(JOBS + [_job("Team lunch", "once at 2026-10-21 12:00")])
    r = await client.get("/v1/schedule", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"items": [
        {"time": "09:00", "title": "Car service", "note": "", "job": "Car service", "date": "2026-10-20"},
        {"time": "12:00", "title": "Team lunch", "note": "", "job": "Team lunch", "date": TOMORROW}]}


async def test_schedule_makes_no_hermes_call(fake, client, monkeypatch):
    monkeypatch.setattr(hermes, "start_run", REAL_START_RUN)
    monkeypatch.setattr(hermes, "probe", REAL_PROBE)
    with respx.mock(assert_all_called=False) as router:
        to_hermes = router.route(host="hermes.test").mock(return_value=httpx.Response(200, json={"status": "ok"}))
        r = await client.get("/v1/schedule", headers=AUTH)
    assert [i["title"] for i in r.json()["items"]] == ["Car service"]
    assert to_hermes.call_count == 0


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TV_KEY}])
async def test_schedule_requires_tv_key(fake, client, headers):
    assert (await client.get("/v1/schedule", headers=headers)).status_code == 401
    assert fake.calls == [] and schedule._running is None


async def test_midnight_rollover(fake, monkeypatch):
    monkeypatch.setattr(schedule, "WAIT_S", 0.05)
    yesterday, today = asyncio.Event(), asyncio.Event()
    fake.gates = [yesterday, today]
    fake.write_jobs([_job("Car service", "once at 2026-10-21 09:00"),
                     _job("Dentist visit", "once at 2026-10-22 10:00")])
    assert await schedule.upcoming() == []  # the 20 Oct run waits for its read
    late = schedule._running[1]
    fake.now = TODAY.replace(day=21, hour=0, minute=1)

    assert await schedule.upcoming() == []  # not yesterday's run: a new one
    today.set()
    await schedule._running[1]
    expected = [(TOMORROW, "Car service"), ("2026-10-22", "Dentist visit")]
    assert [(i.date, i.job) for i in await schedule.upcoming()] == expected
    yesterday.set()
    await late
    # Yesterday's run ended last but did not replace today's list.
    assert [(i.date, i.job) for i in await schedule.upcoming()] == expected


async def test_after_midnight_yesterdays_tomorrow_items_show(fake):
    fake.write_jobs(JOBS + [_job("Team lunch", "once at 2026-10-21 12:00")])
    assert [(i.date, i.job) for i in await schedule.upcoming()] == [
        ("2026-10-20", "Car service"), (TOMORROW, "Team lunch")]
    fake.now = TODAY.replace(day=21, hour=0, minute=1)
    fake.read = ("false",)
    assert [(i.date, i.job) for i in await schedule.upcoming()] == [(TOMORROW, "Team lunch")]


async def test_wait_is_bounded_and_refresh_fills_cache(fake, monkeypatch):
    monkeypatch.setattr(schedule, "WAIT_S", 0.05)
    fake.gate = asyncio.Event()
    assert await schedule.upcoming() == []  # the caller does not wait for the slow read
    fake.gate.set()
    await _settle()
    assert [i.job for i in await schedule.upcoming()] == ["Car service"]
    assert len(fake.calls) == 1


async def test_two_callers_share_one_run(fake):
    fake.gate = asyncio.Event()
    first = asyncio.create_task(schedule.upcoming())
    second = asyncio.create_task(schedule.upcoming())
    await asyncio.sleep(0.05)
    fake.gate.set()
    assert await first == await second
    assert len(fake.calls) == 1


def test_clip_removes_control_and_format_characters():
    assert schedule._clip("Pay​ re\u0007nt‮\n  today\t", 60) == "Pay rent today"


def test_titles_do_not_name_the_day():
    # The TV puts "Today" or "Tomorrow" in front of each item.
    assert schedule._undated("Today: water the plants") == "Water the plants"
    assert schedule._undated("Pay rent tomorrow") == "Pay rent"
    assert schedule._undated("Tomorrow") == "Tomorrow"
    assert schedule._undated("Todays news, tomorrowland") == "Todays news, tomorrowland"


async def test_title_is_clipped_and_undated(fake):
    long = f"Tomorrow sports day {'x' * 80}"
    fake.write_env(f"SCHEDULE_JOBS=Pay rent today,Tomorrow,{long}\n")
    fake.write_jobs([_job("Pay rent today", "0 8 * * *"), _job("Tomorrow", "once at 2026-10-20 09:00"),
                     _job(long, "once at 2026-10-20 10:00")])
    # The day word goes after the clip.
    assert [(i.date, i.title, i.job) for i in await schedule.upcoming()][:3] == [
        ("2026-10-20", "Pay rent", "Pay rent today"), ("2026-10-20", "Tomorrow", "Tomorrow"),
        ("2026-10-20", f"Sports day {'x' * 40}", long)]
