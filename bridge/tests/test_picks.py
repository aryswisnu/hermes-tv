import asyncio
import json
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from hermestv import app as app_mod
from hermestv import catalog, hermes, history, picks, slots, youtube
from hermestv.catalog import Film
from hermestv.hermes import Done, NeedsApproval, Offline
from hermestv.youtube import Video

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures"
TV_KEY = "tv-secret"
AUTH = {"Authorization": f"Bearer {TV_KEY}"}
LUNCH, NIGHT, AFTERNOON, DINNER = (slots.SLOTS[name] for name in ("lunch", "night", "afternoon", "dinner"))
FILM_KEYS = {"title", "year", "runtime_min", "rating", "service", "url", "poster", "backdrop",
             "genres", "why", "seasons", "kind", "channel", "duration_min", "video_id"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _film(title: str, rating: float = 7.2, genres=("horror",), seasons=None) -> Film:
    return Film(title=title, year=2022, runtime_min=95, rating=rating, service="netflix",
                url="https://www.netflix.com/", poster="https://image.tmdb.org/t/p/w500/x.jpg",
                backdrop="https://image.tmdb.org/t/p/w1280/x.jpg", genres=list(genres), seasons=seasons,
                tmdb_id=abs(hash(title)) % 10**8)


class Fake:
    def __init__(self, monkeypatch):
        self.hermes: list[str] = []  # titles Hermes suggests
        self.result = None  # a RunResult that replaces the Done reply
        self.gate: asyncio.Event | None = None
        self.runs: list[tuple[str, str]] = []
        self.catalog: dict[str, Film] = {}  # the films TMDB has, in popularity order
        self.series: dict[str, Film] = {}  # the series TMDB has
        self.fetches: list[str] = []  # the row of each TMDB call
        self.details: list[tuple[str, list[str]]] = []  # the row and titles of each details call
        monkeypatch.setattr(hermes, "start_run", self._start_run)
        monkeypatch.setattr(catalog, "_fetch", self._fetch)
        monkeypatch.setattr(catalog, "details", self._details)

    async def _start_run(self, text, session_key):
        self.runs.append((text, session_key))
        if self.gate:
            await self.gate.wait()
        if self.result is not None:
            return self.result
        block = {"films": [{"title": t, "year": 2022, "why": f"why {t}"} for t in self.hermes]}
        return Done(f"```json\n{json.dumps(block)}\n```")

    async def _fetch(self, row, rule):
        self.fetches.append(row)
        return list((self.series if row == "series" else self.catalog).values())

    async def _details(self, films, row):
        self.details.append((row, [f.title for f in films]))
        return films


@pytest.fixture
def fake(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text(f"HERMES_URL=http://hermes.test\nHERMES_KEY=k\nTV_KEY={TV_KEY}\nTMDB_API_KEY=tmdb-key\n"
                   "TIME_ZONE=Asia/Tokyo\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    monkeypatch.setattr(slots, "current", lambda now=None: NIGHT)
    picks._running.clear()
    picks._last_attempt.clear()
    catalog._available.clear()
    yield Fake(monkeypatch)
    picks._running.clear()
    picks._last_attempt.clear()
    catalog._available.clear()


@pytest.fixture
async def client(fake):
    transport = httpx.ASGITransport(app=app_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as c:
        yield c


def _write_cache(slot: slots.Slot, titles: list[str | Film], age: timedelta, row: str = "films") -> Path:
    path = picks.STATE_DIR / f"picks-{slot.name}-{row}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    films = [{**picks.PickFilmOut.model_validate(t if isinstance(t, Film) else _film(t),
                                                 from_attributes=True).model_dump()}
             for t in titles]
    updated = (datetime.now(slots.zone()) - age).isoformat(timespec="seconds")
    path.write_text(json.dumps({"slot": slot.name, "label": slot.label, "row": row, "films": films,
                                "updated_at": updated}))
    return path


async def _settle():
    await asyncio.gather(*picks._running.values())


async def test_picks_empty_cache_starts_refresh(fake, client):
    fake.hermes = ["Talk to Me"]
    fake.catalog = {"Talk to Me": _film("Talk to Me")}

    r = await client.get("/v1/picks", headers=AUTH)

    assert r.status_code == 200
    assert r.json() == {"slot": "night", "label": "Tonight Movie Time", "row": "films", "films": [],
                        "updated_at": None, "refreshing": True}
    await _settle()
    body = (await client.get("/v1/picks", headers=AUTH)).json()
    assert [f["title"] for f in body["films"]] == ["Talk to Me"]
    assert set(body["films"][0]) == FILM_KEYS
    assert body["refreshing"] is False
    assert datetime.fromisoformat(body["updated_at"]).utcoffset() == timedelta(hours=9)  # TIME_ZONE
    assert len(fake.runs) == 1


@pytest.mark.parametrize("minutes, refreshing", [(9, False), (11, True)])
async def test_picks_fresh_cache_no_refresh(fake, client, minutes, refreshing):
    _write_cache(NIGHT, ["Talk to Me", "Smile"], timedelta(minutes=minutes))

    body = (await client.get("/v1/picks", headers=AUTH)).json()

    assert [f["title"] for f in body["films"]] == ["Talk to Me", "Smile"]
    assert body["refreshing"] is refreshing
    await _settle()
    assert len(fake.fetches) == (1 if refreshing else 0)


async def test_picks_corrupt_cache_counts_as_missing(fake, client):
    path = picks.STATE_DIR / "picks-night-films.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    body = (await client.get("/v1/picks", headers=AUTH)).json()

    assert (body["films"], body["updated_at"], body["refreshing"]) == ([], None, True)
    await _settle()


async def test_refresh_single_flight(fake, client):
    fake.gate = asyncio.Event()
    fake.catalog = {t: _film(t, genres=g) for t, g in (("Night Visitor", ("horror",)), ("Easy Sunday", ("comedy",)))}

    first = picks.refresh(NIGHT)
    assert picks.refresh(NIGHT) is first
    await client.get("/v1/picks", headers=AUTH)
    await client.get("/v1/picks", headers=AUTH)
    lunch = picks.refresh(LUNCH)
    assert lunch is not first
    await asyncio.sleep(0)
    fake.gate.set()
    await asyncio.gather(first, lunch)

    assert len(fake.runs) == 2  # one per slot
    picks.refresh(NIGHT)
    await _settle()
    assert len(fake.runs) == 3  # a finished refresh does not block the next one


@pytest.mark.parametrize("slot, kept", [
    (LUNCH, ["Comedy 6.5", "Comedy 7.9"]),
    (NIGHT, ["Horror 7.0", "Horror 7.5", "Drama 7.5"]),
    (AFTERNOON, ["Horror 7.5", "Drama 7.5"]),
])
async def test_refresh_filters_by_slot(fake, slot, kept):
    films = [
        _film("Comedy 6.5", 6.5, ("comedy",)),
        _film("Comedy 6.4", 6.4, ("comedy",)),
        _film("Comedy 7.9", 7.9, ("comedy",)) if slot is not AFTERNOON else _film("Comedy 7.4", 7.4, ("comedy",)),
        _film("Horror 7.0", 7.0, ("horror", "thriller")),
        _film("Horror 6.9", 6.9, ("horror",)),
        _film("Horror 7.5", 7.5, ("horror",)) if slot is not LUNCH else _film("Horror 6.4", 6.4, ("horror",)),
        _film("Drama 7.5", 7.5, ("drama",)),
    ]
    fake.hermes = [f.title for f in films]
    fake.catalog = {f.title: f for f in films}

    await picks.refresh(slot)

    out = picks.get(slot)
    assert [f.title for f in out.films] == kept
    assert fake.fetches == ["films"]
    await _settle()


@pytest.mark.parametrize("slot", [LUNCH, NIGHT, AFTERNOON])
async def test_refresh_drops_documentaries(fake, slot):
    # Each film passes the slot's rating and genre test; only the genre "documentary" differs.
    films = [_film("Seaside Radio Story", 8.5, ("documentary", "horror")),
             _film("Who Built the Pier?", 8.0, ("horror", "documentary")),
             _film("Kept", 8.0, ("comedy",) if slot is LUNCH else ("horror",))]
    fake.hermes = [f.title for f in films]
    fake.catalog = {f.title: f for f in films}

    await picks.refresh(slot)

    assert [f.title for f in picks.get(slot).films] == ["Kept"]
    await _settle()


async def test_refresh_keeps_hermes_first_and_caps_at_10(fake):
    fake.hermes = [f"Hermes {i}" for i in range(9)]
    fake.catalog = {t: _film(t) for t in fake.hermes}
    fake.catalog |= {t: _film(t, rating) for t, rating in (("Pool A", 9.0), ("Pool B", 8.0))}

    await picks.refresh(NIGHT)

    # The top-up follows Hermes, best rated first, until the row has ten.
    assert [f.title for f in picks.get(NIGHT).films] == fake.hermes + ["Pool A"]
    await _settle()


@pytest.mark.parametrize("slot, genres", [(NIGHT, ("horror",)), (LUNCH, ("comedy",))])
async def test_refresh_keeps_three_old_when_new_are_few(fake, caplog, slot, genres):
    old = [replace(_film(f"Old {i}", genres=genres), why=f"why Old {i}") for i in range(9)]
    _write_cache(slot, old, timedelta(minutes=30))
    fake.hermes = ["New A", "New B", "Nowhere Film"]
    fake.catalog = {t: _film(t, genres=genres) for t in ("New A", "New B")}
    caplog.set_level("INFO", logger=picks.__name__)

    await picks.refresh(slot)

    films = picks.get(slot).films
    assert [f.title for f in films] == ["New A", "New B"] + [f"Old {i}" for i in range(3)]
    assert [f.why for f in films[2:4]] == ["why Old 0", "why Old 1"]
    assert [r.getMessage() for r in caplog.records if r.levelname == "INFO"] == [
        f"picks {slot.name} films: 2 candidates, 2 chosen, 2 new, 3 kept, 0 topped up"]
    await _settle()


async def test_refresh_drops_old_unfit_films(fake):
    _write_cache(NIGHT, [_film("Old Doc", 8.0, ("horror", "documentary")), _film("Old Low", 6.9),
                         _film("Old Drama", 8.0, ("drama",)), "Old Kept"], timedelta(minutes=30))
    fake.hermes = ["New A"]
    fake.catalog = {"New A": _film("New A")}

    await picks.refresh(NIGHT)

    assert [f.title for f in picks.get(NIGHT).films] == ["New A", "Old Drama", "Old Kept"]
    await _settle()


async def test_refresh_no_duplicates_across_old_and_new(fake):
    _write_cache(NIGHT, ["Talk to Me", "Smile", "Below"], timedelta(minutes=30))
    fake.hermes = ["talk to me!", "SMILE"]
    fake.catalog = {"talk to me!": _film("Talk to Me"), "SMILE": _film("Smile")}

    await picks.refresh(NIGHT)

    films = picks.get(NIGHT).films
    assert [(f.title, f.why) for f in films] == [
        ("Talk to Me", "why talk to me!"), ("Smile", "why SMILE"), ("Below", "")]
    await _settle()


async def test_refresh_asks_hermes_for_the_slot_excluding_the_last_list(fake, tmp_path):
    with (tmp_path / "bridge.env").open("a") as env:
        env.write("REGION=CA\n")
    _write_cache(NIGHT, ["Old One", "Old Two"], timedelta(minutes=30))
    fake.hermes = ["Talk to Me"]
    fake.catalog = {"Talk to Me": _film("Talk to Me")}

    await picks.refresh(NIGHT)

    text, key = fake.runs[0]
    assert key == f"hermes-tv-picks-films-{datetime.now(slots.zone()):%Y%m%d}"
    assert "up to 10 films" in text and "Old One, Old Two" in text
    # The night row is a mix, not horror only.
    assert "a mix of horror and drama, roughly half each" in text
    assert "- Talk to Me (2022)" in text
    assert "Every title on it is on Netflix or HBO Max in CA now" in text
    assert "Do not run code" in text  # a tool call waits for an approval nobody gives
    await _settle()


async def test_refresh_fills_details_of_the_final_list(fake, monkeypatch):
    async def details(films, row):
        fake.details.append((row, [f.title for f in films]))
        # Film 11's lookup fails; it keeps the runtime it has.
        return [replace(f, runtime_min=None if f.title == "Film 11" else 100 + int(f.title.split()[-1]))
                for f in films]
    monkeypatch.setattr(catalog, "details", details)
    _write_cache(NIGHT, [replace(_film("Old Kept"), runtime_min=88)], timedelta(minutes=30))
    fake.catalog = {f.title: f for f in _films(12)}
    fake.hermes = ["Film 0"]

    await picks.refresh(NIGHT)

    films = picks.get(NIGHT).films
    assert [f.title for f in films] == ["Film 0", "Old Kept"] + [f"Film {i}" for i in range(11, 3, -1)]
    # Only the ten shown are looked up, not every candidate; the kept film is no candidate.
    assert fake.details == [("films", ["Film 0"] + [f"Film {i}" for i in range(11, 3, -1)])]
    assert [f.runtime_min for f in films[:4]] == [100, 88, 95, 110]
    await _settle()


async def test_refresh_after_approval_request_tops_up_from_the_pool(fake):
    fake.result = NeedsApproval("run_1", "req_1")
    fake.catalog = {"Below": _film("Below")}

    await picks.refresh(NIGHT)
    await _settle()

    assert [f.title for f in picks.get(NIGHT).films] == ["Below"]


async def test_picks_zero_survivors_keeps_old_list(fake, client):
    path = _write_cache(NIGHT, ["Talk to Me", "Smile"], timedelta(minutes=30))
    fake.hermes = ["Nowhere Film"]
    fake.catalog = {"Thriller Night": _film("Thriller Night", 8.0, ("thriller",))}

    await picks.refresh(NIGHT)

    assert [f["title"] for f in json.loads(path.read_text())["films"]] == ["Talk to Me", "Smile"]
    body = (await client.get("/v1/picks", headers=AUTH)).json()
    assert [f["title"] for f in body["films"]] == ["Talk to Me", "Smile"]
    await _settle()


async def test_refresh_cooldown_after_zero_survivors(fake, client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(picks, "_now", lambda: clock[0])
    fake.hermes = ["Nowhere Film"]  # TMDB has nothing, so the cache stays missing

    first = (await client.get("/v1/picks", headers=AUTH)).json()
    await _settle()
    clock[0] += picks.COOLDOWN_S - 1
    second = (await client.get("/v1/picks", headers=AUTH)).json()

    assert first["refreshing"] is True
    assert second["refreshing"] is False  # no refresh runs during the cooldown
    assert second["films"] == [] and second["updated_at"] is None
    assert len(fake.fetches) == 1

    clock[0] += 2
    third = (await client.get("/v1/picks", headers=AUTH)).json()
    await _settle()
    assert third["refreshing"] is True
    assert len(fake.fetches) == 2


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TV_KEY}])
async def test_picks_requires_tv_key(fake, client, headers):
    r = await client.get("/v1/picks", headers=headers)
    assert r.status_code == 401
    assert fake.runs == [] and not picks._running


async def test_picks_requires_tv_key_empty_key_rejects(fake, client, tmp_path):
    (tmp_path / "bridge.env").write_text("HERMES_KEY=k\nTV_KEY=\n")
    r = await client.get("/v1/picks", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


async def test_picks_returns_under_500ms_while_refreshing(fake, client):
    fake.gate = asyncio.Event()  # Hermes does not answer until the test lets it
    fake.hermes = ["Talk to Me"]
    fake.catalog = {"Talk to Me": _film("Talk to Me")}

    started = time.monotonic()
    async with asyncio.timeout(2):  # a GET that waits for the refresh fails here, not hangs
        first = await client.get("/v1/picks", headers=AUTH)
        second = await client.get("/v1/picks", headers=AUTH)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert first.json()["refreshing"] and second.json()["refreshing"]
    assert not picks._running[("night", "films")].done()
    fake.gate.set()
    await _settle()
    assert len(fake.runs) == 1


def test_picks_fixture_matches_model():
    data = json.loads((FIXTURES / "picks_night.json").read_text())
    out = picks.PicksOut.model_validate(data)
    assert out.slot == "night" and len(out.films) >= 3
    # A list saved before series existed still reads.
    assert all(set(f) == FILM_KEYS - {"seasons", "kind", "channel", "duration_min", "video_id"} for f in data["films"])
    assert {f.kind for f in out.films} == {"film"}


def _films(n: int) -> list[Film]:
    # Popularity order differs from rating order: "Film 0" is the most popular and the lowest rated.
    return [_film(f"Film {i}", 7.0 + i / 10) for i in range(n)]


async def test_refresh_accepts_only_candidates(fake):
    fake.catalog = {f.title: f for f in _films(3)}
    fake.hermes = ["Film 2", "The Shining", "film 0"]

    await picks.refresh(NIGHT)

    text, _ = fake.runs[0]
    assert "- Film 0 (2022)\n- Film 1 (2022)\n- Film 2 (2022)" in text
    # Hermes' own title is not on Netflix or HBO Max in the region, so it is dropped.
    assert [(f.title, f.why) for f in picks.get(NIGHT).films] == [
        ("Film 2", "why Film 2"), ("Film 0", "why film 0"), ("Film 1", "")]
    await _settle()


async def test_refresh_hermes_offline_uses_top_candidates(fake):
    fake.result = Offline("connect timeout")
    fake.catalog = {f.title: f for f in _films(12)}

    await picks.refresh(NIGHT)

    films = picks.get(NIGHT).films
    assert [f.title for f in films] == [f"Film {i}" for i in range(11, 1, -1)]
    assert {f.why for f in films} == {""}
    await _settle()


async def test_refresh_fills_ten_when_available(fake):
    fake.catalog = {f.title: f for f in _films(15)}
    fake.hermes = ["Film 3", "Film 0", "Film 7"]

    await picks.refresh(NIGHT)

    films = picks.get(NIGHT).films
    assert [f.title for f in films] == ["Film 3", "Film 0", "Film 7"] + [
        f"Film {i}" for i in (14, 13, 12, 11, 10, 9, 8)]
    assert [f.why for f in films[:4]] == ["why Film 3", "why Film 0", "why Film 7", ""]
    await _settle()


async def test_refresh_unchanged_list_keeps_updated_at(fake):
    path = _write_cache(NIGHT, ["Talk to Me"], timedelta(minutes=30))
    before = path.read_text()
    fake.result = Offline("connect timeout")
    fake.catalog = {"Talk to Me": _film("Talk to Me")}

    await picks.refresh(NIGHT)

    # A fresh updated_at would hide the stale list from the next retry for 10 minutes.
    assert path.read_text() == before
    await _settle()


def test_info_log_reaches_handler(tmp_path):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=k\n")
    code = ("import logging, hermestv.app; logging.getLogger('hermestv.picks').info('picks night: 1 chosen'); "
            "logging.getLogger('httpx').info('HTTP Request: noise')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         cwd=Path(__file__).parent.parent, env={"HERMESTV_ENV_FILE": str(env)})
    assert out.returncode == 0, out.stderr
    assert "picks night: 1 chosen" in out.stderr
    assert "noise" not in out.stderr  # other libraries stay at WARNING


async def test_refresh_hermes_offline_keeps_old_list_first(fake, caplog):
    old = [replace(_film(f"Old {i}", 7.0), why=f"why Old {i}") for i in range(4)]
    _write_cache(NIGHT, old, timedelta(minutes=30))
    fake.result = Offline("connect timeout")
    fake.catalog = {f.title: f for f in _films(12) + old}
    caplog.set_level("INFO", logger=picks.__name__)

    await picks.refresh(NIGHT)

    # A short outage must not replace what Hermes chose; the best-rated candidates only fill the row.
    films = picks.get(NIGHT).films
    assert [(f.title, f.why) for f in films] == [(f"Old {i}", f"why Old {i}") for i in range(4)] + [
        (f"Film {i}", "") for i in range(11, 5, -1)]
    assert [r.getMessage() for r in caplog.records if r.levelname == "INFO"] == [
        "picks night films: 16 candidates, 0 chosen, 0 new, 4 kept, 6 topped up"]
    await _settle()


async def test_lunch_drops_horror_keeps_thriller(fake):
    fake.catalog = {f.title: f for f in (
        _film("Horror Comedy", 7.0, ("comedy", "horror")),
        _film("Comedy Thriller", 7.0, ("comedy", "thriller")))}
    fake.hermes = list(fake.catalog)

    await picks.refresh(LUNCH)

    assert [f.title for f in picks.get(LUNCH).films] == ["Comedy Thriller"]
    await _settle()


async def test_prompt_holds_at_most_60_candidates(fake):
    fake.catalog = {f.title: f for f in _films(70)}

    await picks.refresh(NIGHT)

    listed = [line for line in fake.runs[0][0].splitlines() if line.startswith("- Film ")]
    assert len(listed) == 60 and listed[-1] == "- Film 59 (2022)"
    await _settle()


async def test_rotation_keeps_three(fake):
    # The first old film no longer fits the night, so the next three are the ones kept.
    old = [_film("Old Unfit", 6.0)] + [replace(_film(f"Old {i}"), why=f"why Old {i}") for i in range(9)]
    _write_cache(NIGHT, old, timedelta(minutes=30))
    fake.hermes = [f"New {i}" for i in range(10)]
    fake.catalog = {t: _film(t) for t in fake.hermes}

    await picks.refresh(NIGHT)

    first = [f.title for f in picks.get(NIGHT).films]
    assert first == [f"New {i}" for i in range(7)] + ["Old 0", "Old 1", "Old 2"]
    assert "Old Unfit, Old 0, Old 1" in fake.runs[0][0]  # Hermes is told what is on screen now

    # The next refresh keeps the top three of this list, so no film stays for good.
    catalog._available.clear()
    fake.hermes = [f"Next {i}" for i in range(10)]
    fake.catalog = {t: _film(t) for t in fake.hermes}
    await picks.refresh(NIGHT)
    assert [f.title for f in picks.get(NIGHT).films] == (
        [f"Next {i}" for i in range(7)] + ["New 0", "New 1", "New 2"])
    await _settle()


async def test_rotation_new_choice_of_an_old_film_counts_once(fake):
    _write_cache(NIGHT, [f"Old {i}" for i in range(5)], timedelta(minutes=30))
    fake.hermes = ["Old 1", "New A"]
    fake.catalog = {t: _film(t) for t in ("Old 1", "New A")}

    await picks.refresh(NIGHT)

    films = picks.get(NIGHT).films
    assert [(f.title, f.why) for f in films] == [
        ("Old 1", "why Old 1"), ("New A", "why New A"), ("Old 0", ""), ("Old 2", ""), ("Old 3", "")]
    await _settle()


async def test_picks_without_row_is_films(fake, client):
    # The cache of the one-row design is ignored.
    picks.STATE_DIR.mkdir(parents=True)
    (picks.STATE_DIR / "picks-night.json").write_text(json.dumps({"slot": "night", "label": "x", "films": [],
                                                                  "updated_at": None}))
    fake.hermes = ["Talk to Me"]
    fake.catalog = {"Talk to Me": _film("Talk to Me")}
    fake.series = {"Show": _film("Show", seasons=2)}

    first = (await client.get("/v1/picks", headers=AUTH)).json()
    await _settle()
    bare = (await client.get("/v1/picks", headers=AUTH)).json()
    films = (await client.get("/v1/picks?row=films", headers=AUTH)).json()

    assert (first["row"], first["refreshing"]) == ("films", True)
    assert bare == films
    assert bare["row"] == "films" and [f["title"] for f in bare["films"]] == ["Talk to Me"]
    assert bare["films"][0]["seasons"] is None
    assert fake.fetches == ["films"]
    saved = json.loads((picks.STATE_DIR / "picks-night-films.json").read_text())
    assert (saved["slot"], saved["row"]) == ("night", "films")
    assert (await client.get("/v1/picks?row=books", headers=AUTH)).status_code == 422


async def test_picks_row_series(fake, client):
    fake.gate = asyncio.Event()
    fake.hermes = ["Dark Show"]
    fake.catalog = {"Talk to Me": _film("Talk to Me")}
    fake.series = {t: _film(t, rating, genres, seasons=n) for t, rating, genres, n in (
        ("Dark Show", 8.0, ("drama",), 3), ("Low Show", 7.4, ("horror",), 1), ("Funny Show", 8.5, ("comedy",), 2))}

    r = await client.get("/v1/picks?row=series", headers=AUTH)
    films = picks.refresh(NIGHT, "films")
    # Films and series of one slot refresh on their own, each one at a time.
    assert picks.refresh(NIGHT, "series") is picks._running[("night", "series")] is not films
    fake.gate.set()
    await _settle()

    assert (r.json()["row"], r.json()["refreshing"]) == ("series", True)
    body = (await client.get("/v1/picks?row=series", headers=AUTH)).json()
    assert [(f["title"], f["seasons"], f["why"]) for f in body["films"]] == [("Dark Show", 3, "why Dark Show")]
    assert (body["slot"], body["label"], body["row"]) == ("night", "Tonight Movie Time", "series")
    assert sorted(fake.fetches) == ["films", "series"]
    assert ("series", ["Dark Show"]) in fake.details
    series_note, series_key = next(run for run in fake.runs if "- Dark Show (2022)" in run[0])
    assert series_key.startswith("hermes-tv-picks-series-")
    assert "up to 10 series" in series_note and "Talk to Me" not in series_note
    assert json.loads((picks.STATE_DIR / "picks-night-series.json").read_text())["row"] == "series"


async def test_weekend_afternoon_ignores_the_weekday_cache(fake, client, monkeypatch):
    # Friday's any-genre list must not show under "Weekend family films" on Saturday.
    monkeypatch.setattr(slots, "current", lambda now=None: slots.WEEKEND_AFTERNOON)
    _write_cache(AFTERNOON, ["Weekday Horror"], timedelta(minutes=1))
    fake.gate = asyncio.Event()
    fake.hermes = ["Family Film"]
    fake.catalog = {"Family Film": _film("Family Film", 7.5, ("family",))}

    body = (await client.get("/v1/picks", headers=AUTH)).json()

    assert (body["slot"], body["label"], body["films"], body["refreshing"]) == (
        "afternoon", "Weekend family films", [], True)
    assert picks.refresh(slots.WEEKEND_AFTERNOON) is picks._running[("afternoon-weekend", "films")]
    assert ("afternoon", "films") not in picks._running  # the weekday refresh state is apart
    fake.gate.set()
    await _settle()
    assert [f.title for f in picks.get(slots.WEEKEND_AFTERNOON).films] == ["Family Film"]
    assert (picks.STATE_DIR / "picks-afternoon-weekend-films.json").exists()
    assert [f.title for f in picks.get(AFTERNOON).films] == ["Weekday Horror"]
    await _settle()


def _vid(n: int, views: int = 1000, duration_s: int = 1500) -> Video:
    vid = f"vid{n:08d}"
    return Video(id=vid, title=f"Case file {n}", channel=f"Channel {n % 2}", duration_s=duration_s,
                 thumb=f"https://i.ytimg.com/vi/{vid}/hq720.jpg", url=f"https://www.youtube.com/watch?v={vid}",
                 views=views)


def _videos(monkeypatch, videos: list[Video]) -> list[str]:
    calls = []

    async def candidates(slot):
        calls.append(slot.name)
        return list(videos)
    monkeypatch.setattr(youtube, "candidates", candidates)
    return calls


def _pick_json(*items: dict) -> Done:
    return Done(f"```json\n{json.dumps({'films': list(items)})}\n```")


async def test_youtube_only_candidates(fake, monkeypatch):
    videos = [_vid(i, views=100 * i) for i in range(6)]
    _videos(monkeypatch, videos)
    old = picks.PicksOut(slot="lunch", label=LUNCH.label, row="youtube", updated_at=None,
                         films=[picks._video_out(_vid(0))])
    picks._write(LUNCH, old)
    fake.result = _pick_json(
        {"id": "vid00000002", "title": "Case file 2", "why": "A neat solve."},
        {"id": "notThere001", "title": "Another video", "why": "x"},  # not a candidate: dropped
        {"title": "case file 4!", "why": "Gripping."})  # no id: matched by title

    await picks.refresh(LUNCH, "youtube")

    text, key = fake.runs[0]
    assert key.startswith("hermes-tv-picks-youtube-")
    assert "- [vid00000003] Case file 3 · Channel 1 · 25 min" in text
    assert "unsolved mysteries, true crime documentary" in text  # the YOUTUBE_QUERIES taste
    films = picks.get(LUNCH, "youtube").films
    # Hermes first, then the kept video, then the most viewed candidates fill the row.
    assert [(f.video_id, f.why) for f in films] == [
        ("vid00000002", "A neat solve."), ("vid00000004", "Gripping."), ("vid00000000", ""),
        ("vid00000005", ""), ("vid00000003", ""), ("vid00000001", "")]
    first = films[0].model_dump()
    assert first == {"title": "Case file 2", "year": None, "runtime_min": None, "rating": None,
                     "service": "youtube", "url": "https://www.youtube.com/watch?v=vid00000002",
                     "poster": "https://i.ytimg.com/vi/vid00000002/hq720.jpg",
                     "backdrop": "https://i.ytimg.com/vi/vid00000002/hq720.jpg", "genres": [],
                     "why": "A neat solve.", "seasons": None, "kind": "video",
                     "channel": "Channel 0", "duration_min": 25, "video_id": "vid00000002"}
    assert fake.fetches == [] and fake.details == []  # TMDB is not asked for the YouTube row
    await _settle()


async def test_youtube_row_only_at_meals(fake, client, monkeypatch):
    assert [s.name for s in slots.SLOTS.values() if "youtube" in s.rows] == ["lunch", "dinner"]
    assert "youtube" not in slots.WEEKEND_AFTERNOON.rows
    calls = _videos(monkeypatch, [_vid(1)])
    fake.result = _pick_json({"id": "vid00000001", "title": "Case file 1", "why": "Gripping."})

    night = (await client.get("/v1/picks?row=youtube", headers=AUTH)).json()
    await _settle()
    assert (night["row"], night["films"], night["refreshing"]) == ("youtube", [], False)
    assert calls == [] and fake.runs == []

    for slot in (LUNCH, DINNER):
        monkeypatch.setattr(slots, "current", lambda now=None, s=slot: s)
        first = (await client.get("/v1/picks?row=youtube", headers=AUTH)).json()
        await _settle()
        body = (await client.get("/v1/picks?row=youtube", headers=AUTH)).json()
        assert first["refreshing"] is True
        assert [(f["video_id"], f["kind"]) for f in body["films"]] == [("vid00000001", "video")]
        assert (picks.STATE_DIR / f"picks-{slot.name}-youtube.json").exists()
    assert calls == ["lunch", "dinner"]


async def test_youtube_failure_keeps_last(fake, monkeypatch, caplog):
    async def broken(*args):
        raise RuntimeError("yt-dlp exit 1")
    monkeypatch.setattr(youtube, "_ytdlp", broken)
    youtube._reset()
    picks._write(LUNCH, picks.PicksOut(slot="lunch", label=LUNCH.label, row="youtube",
                                       updated_at=datetime.now(slots.zone()).replace(microsecond=0),
                                       films=[picks._video_out(_vid(i)) for i in range(4)]))
    path = picks.STATE_DIR / "picks-lunch-youtube.json"
    before = path.read_text()
    caplog.set_level("INFO")

    await picks.refresh(LUNCH, "youtube")

    assert path.read_text() == before
    assert fake.runs == []  # nothing to choose from, so Hermes is not asked
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= 30]
    assert len(warnings) == 1 and "yt-dlp exit 1" in warnings[0]
    youtube._reset()
    await _settle()


async def test_kept_series_from_an_old_cache_reads_as_series(fake):
    _write_cache(NIGHT, [_film("Old Show", 8.0, ("drama",), seasons=2)], timedelta(minutes=30), row="series")
    fake.hermes = ["New Show"]
    fake.series = {"New Show": _film("New Show", 8.0, ("drama",), seasons=1)}

    await picks.refresh(NIGHT, "series")

    assert [(f.title, f.kind) for f in picks.get(NIGHT, "series").films] == [
        ("New Show", "series"), ("Old Show", "series")]
    await _settle()


@pytest.mark.parametrize("row", ["films", "series"])
async def test_hidden_excluded_everywhere(fake, row):
    hidden = _film("Smile", 8.0, ("horror",), seasons=1 if row == "series" else None)
    other = _film("Talk to Me", 7.5, ("horror",), seasons=1 if row == "series" else None)
    _write_cache(NIGHT, [hidden], timedelta(minutes=30), row=row)  # would be kept
    setattr(fake, "series" if row == "series" else "catalog", {"Smile": hidden, "Talk to Me": other})
    fake.hermes = ["Smile", "Talk to Me"]  # Hermes would choose it
    history.hide("series" if row == "series" else "film", "Smile", 2022, None, None)

    await picks.refresh(NIGHT, row)

    # Not even in the saved list, where it would take a place of the ten.
    assert [f.title for f in picks._read(NIGHT, row).films] == ["Talk to Me"]
    assert [f.title for f in picks.get(NIGHT, row).films] == ["Talk to Me"]
    assert "- Smile (2022)" not in fake.runs[0][0]  # Hermes is not offered it
    await _settle()


async def test_hidden_item_leaves_the_cached_list_at_once(fake):
    _write_cache(NIGHT, ["Smile", "Talk to Me"], timedelta(minutes=1))
    history.hide("film", "Smile", 2022, None, None)
    assert [f.title for f in picks.get(NIGHT).films] == ["Talk to Me"]
    assert fake.runs == []  # a hide does not start a refresh


async def test_hidden_video_excluded(fake, monkeypatch):
    _videos(monkeypatch, [_vid(i) for i in range(4)])
    picks._write(LUNCH, picks.PicksOut(slot="lunch", label=LUNCH.label, row="youtube", updated_at=None,
                                       films=[picks._video_out(_vid(0))]))
    fake.result = _pick_json({"id": "vid00000002", "title": "Case file 2", "why": "Gripping."})
    history.hide("video", "Case file 0", None, "vid00000000", None)
    history.hide("video", "Case file 2", None, "vid00000002", None)

    await picks.refresh(LUNCH, "youtube")

    assert [f.video_id for f in picks.get(LUNCH, "youtube").films] == ["vid00000001", "vid00000003"]
    assert "vid00000002" not in fake.runs[0][0]
    await _settle()


async def test_hide_channel(fake, monkeypatch):
    _videos(monkeypatch, [_vid(i) for i in range(6)])  # "Channel 0" has the even ids, "Channel 1" the odd ones
    picks._write(LUNCH, picks.PicksOut(slot="lunch", label=LUNCH.label, row="youtube",
                                       updated_at=datetime.now(slots.zone()).replace(microsecond=0),
                                       films=[picks._video_out(_vid(1)), picks._video_out(_vid(2))]))
    history.hide("video", "Case file 1", None, "vid00000001", "Channel 1")
    assert [f.video_id for f in picks.get(LUNCH, "youtube").films] == ["vid00000002"]

    fake.result = _pick_json({"id": "vid00000003", "title": "Case file 3", "why": "Gripping."},
                             {"id": "vid00000004", "title": "Case file 4", "why": "Gripping."})
    await picks.refresh(LUNCH, "youtube")

    ids = [f.video_id for f in picks.get(LUNCH, "youtube").films]
    assert ids and all(int(i[-1]) % 2 == 0 for i in ids)
    assert "Channel 1" not in fake.runs[0][0]
    await _settle()


async def test_opened_excluded_90_days(fake):
    now = datetime.now(slots.zone())
    fake.catalog = {t: _film(t) for t in ("Talk to Me", "Smile", "Barbarian")}
    fake.hermes = ["Talk to Me", "Smile", "Barbarian"]
    history.opened("film", "Smile", 2022, None, "u", "night", now=now - timedelta(days=91))
    history.opened("film", "Talk to Me", 2022, None, "u", "night", now=now - timedelta(days=89))

    await picks.refresh(NIGHT)

    assert [f.title for f in picks.get(NIGHT).films] == ["Smile", "Barbarian"]
    text = fake.runs[0][0]
    assert "- Talk to Me (2022)" not in text
    # The last opened titles are taste hints, the newest first.
    assert "Recently opened on this TV (taste hints): Talk to Me, Smile" in text
    await _settle()


async def test_opened_videos_are_youtube_taste_hints(fake, monkeypatch):
    _videos(monkeypatch, [_vid(i) for i in range(3)])
    fake.result = _pick_json()
    history.opened("video", "Case [old]", None, "vid00000001", "u", "lunch")

    await picks.refresh(LUNCH, "youtube")

    text = fake.runs[0][0]
    assert "Recently opened on this TV (taste hints): Case old" in text
    assert "[vid00000001]" not in text
    await _settle()


async def test_corrupt_hidden_json_does_not_break_picks(fake, client):
    _write_cache(NIGHT, ["Smile", "Talk to Me"], timedelta(minutes=1))
    (history.STATE_DIR / "hidden.json").write_text("[not, json")
    r = await client.get("/v1/picks", headers=AUTH)
    assert r.status_code == 200
    assert [f["title"] for f in r.json()["films"]] == ["Smile", "Talk to Me"]


async def test_history_write_back_failure_does_not_break_picks(fake, client, monkeypatch, caplog):
    _write_cache(NIGHT, ["Smile", "Talk to Me"], timedelta(minutes=1))
    good = json.dumps({"time": datetime.now(slots.zone()).isoformat(), "kind": "film", "title": "Smile",
                       "year": 2022, "video_id": None, "url": "u", "slot": "night"})
    (history.STATE_DIR / "history.jsonl").write_text(f"{good}\n{{broken\n")

    def read_only(entries):
        raise PermissionError("read-only disk")
    monkeypatch.setattr(history, "_write_history", read_only)

    r = await client.get("/v1/picks", headers=AUTH)
    assert r.status_code == 200
    assert [f["title"] for f in r.json()["films"]] == ["Talk to Me"]  # the good line still counts
    assert any("could not be written back" in rec.getMessage() for rec in caplog.records)


def test_cached_picks_take_the_current_slot_label(fake):
    picks._write(NIGHT, picks.PicksOut(slot="night", label="Old night label", row="films", films=[], updated_at=None))
    assert picks._read(NIGHT, "films").label == "Tonight Movie Time"


def _without_hermes(tmp_path) -> None:
    (tmp_path / "bridge.env").write_text(f"TV_KEY={TV_KEY}\nTMDB_API_KEY=tmdb-key\n")


async def test_disabled_hermes_makes_no_request(fake, tmp_path):
    _without_hermes(tmp_path)
    fake.hermes = ["Low"]  # what Hermes would choose, were it asked
    fake.catalog = {f.title: f for f in (_film("Low", 7.1), _film("High", 8.6), _film("Mid", 7.9))}
    with respx.mock(assert_all_called=False) as mock:
        any_request = mock.route()
        picks.get(NIGHT)
        await _settle()
    assert [(f.title, f.why) for f in picks.get(NIGHT).films] == [("High", ""), ("Mid", ""), ("Low", "")]
    assert fake.runs == []
    assert any_request.call_count == 0


async def test_disabled_hermes_row_follows_catalog(fake, tmp_path):
    _without_hermes(tmp_path)
    _write_cache(NIGHT, ["Gone", "Mid"], timedelta(minutes=30))
    fake.catalog = {f.title: f for f in (_film("Mid", 7.9), _film("High", 8.6))}
    await picks.refresh(NIGHT)
    # No taste to keep: a title that left the catalog leaves the row, and the order is the rating.
    assert [f.title for f in picks.get(NIGHT).films] == ["High", "Mid"]
