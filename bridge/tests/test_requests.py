import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hermestv import app as app_mod
from hermestv import catalog, exchange, extra, history, requests, watchlog
from hermestv.picks import PicksOut

pytestmark = pytest.mark.anyio

ZONE = ZoneInfo("Australia/Perth")  # UTC+8 all year
CREATED = datetime(2026, 10, 22, 19, 0, tzinfo=ZONE)
NOW = CREATED + timedelta(hours=1)
CMD = ("docker", "exec", "hermes-test", "cat", "/opt/data/hermes-tv/requests.json")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "bridge.env"
    path.write_text("TV_KEY=t\nTMDB_API_KEY=v3key\nTIME_ZONE=Australia/Perth\nHERMES_CONTAINER=hermes-test\n"
                    "HERMES_TV_DIR=/opt/data/hermes-tv\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(path))
    monkeypatch.setattr(exchange, "_logged", {})
    catalog._details.clear()
    yield path
    catalog._details.clear()


class Exec:
    """Stands in for exchange._exec: serves requests.json from memory, never runs docker."""

    def __init__(self, monkeypatch):
        self.file: bytes | None = None
        self.calls = 0
        monkeypatch.setattr(exchange, "_exec", self)

    async def __call__(self, cmd, timeout, limit, stdin=None):
        assert cmd == CMD and limit == 64 * 1024
        self.calls += 1
        if self.file is None:
            raise exchange._Failed("exit code is not 0")
        if len(self.file) > limit:
            raise exchange._Failed("too much output")
        return self.file


@pytest.fixture
def docker(env, monkeypatch):
    return Exec(monkeypatch)


def _doc(*items, created_at=CREATED.isoformat(), **extra) -> bytes:
    return json.dumps({"created_at": created_at, "items": list(items), **extra}).encode()


def _item(title="The Lantern Keeper", kind="film", why="A slow, cold ghost story.", **extra) -> dict:
    return {"title": title, "kind": kind, "why": why, **extra}


def _titles(raw: bytes, now: datetime = NOW) -> list[str]:
    return [i.title for i in requests.parse(raw, now)]


def _hit(tmdb_id: int, title: str, year: int, kind: str = "movie") -> dict:
    named = ({"title": title, "release_date": f"{year}-05-01"} if kind == "movie"
             else {"name": title, "first_air_date": f"{year}-05-01"})
    return {"id": tmdb_id, **named, "poster_path": f"/poster{tmdb_id}.jpg", "backdrop_path": None,
            "genre_ids": [27], "vote_average": 7.4, "vote_count": 900}


LANTERN = [_hit(900001, "The Lantern Keeper", 2024)]
DUNE = [_hit(900002, "Dune Sea", 2021)]
SERVICES = {900001: 8, 900002: 1899}


def _tmdb(movies: dict[str, list[dict] | httpx.Response], shows: dict[str, list[dict]] | None = None) -> respx.Route:
    """TMDB answers each search query with its hits, or with the given response; ids in SERVICES stream."""
    def search(request: httpx.Request) -> httpx.Response:
        by_query = movies if request.url.path.endswith("/movie") else shows or {}
        found = by_query.get(request.url.params["query"], [])
        if isinstance(found, httpx.Response):
            return found
        return httpx.Response(200, json={"page": 1, "results": found, "total_pages": 1})

    def providers(request: httpx.Request) -> httpx.Response:
        provider = SERVICES.get(int(request.url.path.split("/")[-3]))
        return httpx.Response(200, json={"results": {"US": {"flatrate": [{"provider_id": provider}]}}})

    respx.get(path__regex=r"^/3/(movie|tv)/\d+/watch/providers$").mock(side_effect=providers)
    respx.get(path__regex=r"^/3/(movie|tv)/\d+$").mock(return_value=httpx.Response(200, json={"runtime": 101}))
    return respx.get(path__regex=r"^/3/search/(movie|tv)$").mock(side_effect=search)


def test_parse_rejects_bad_json_and_oversize(env):
    good = _doc(_item())
    assert _titles(good) == ["The Lantern Keeper"]
    for raw in (b"{", b"\xff\xfe", b"[]", b"null", b"[" * 30000 + b"]" * 30000,
                _doc(created_at=None), json.dumps({"created_at": CREATED.isoformat(), "items": {}}).encode(),
                _doc(_item(), created_at="yesterday"), _doc(_item(), created_at="2026-10-22T19:00:00"),
                _doc(_item(), created_at="0001-01-01T00:00:00+00:00"),
                _doc(_item(), created_at="9999-12-31T04:00:00+08:00")):
        assert requests.parse(raw, NOW) == [], raw[:60]
    padded = _doc(_item(), pad="x" * (64 * 1024 - len(_doc(_item(), pad=""))))
    assert len(padded) == 64 * 1024 and _titles(padded) == ["The Lantern Keeper"]
    assert requests.parse(_doc(_item(), pad="x" * (64 * 1024 + 1 - len(_doc(_item(), pad="")))), NOW) == []
    assert requests.parse(b" " * (10 * 1024 * 1024) + good, NOW) == []


def test_parse_caps_ten_items(env):
    raw = _doc(*[_item(title=f"Title {n}") for n in range(50)])
    assert _titles(raw) == [f"Title {n}" for n in range(10)]


def test_parse_drops_item_with_url(env):
    raw = _doc(_item(title="Kept"),
               _item(title="A", why="see https://evil.example"),
               _item(title="B", why="see HTTP stuff"),
               _item(title="C", why="go to www.evil.example"),
               _item(title="evil.example://x"),
               _item(title="D", note="http://evil.example"),
               _item(title="E", why="javascript://x"),
               _item(title="F", why="ｈｔｔｐｓ：／／evil.example"),
               _item(title="G", why="ｗｗｗ．evil.example"))
    assert _titles(raw) == ["Kept"]


def test_parse_drops_newlines_and_long_fields(env):
    raw = _doc(_item(title="T" * 120, why="w" * 140),
               _item(title="T" * 121),
               _item(title="Kept", why="w" * 141),
               _item(title="Two\nlines"),
               _item(title="Tab\there"),
               _item(title="Bell\x07"),
               _item(title="Zero​width"),
               _item(title="Clean why", why="Great\nfilm\x00 \x1b[1m tonight‮."))
    items = requests.parse(raw, NOW)
    assert [i.title for i in items] == ["T" * 120, "Clean why"]
    assert items[1].why == "Great film [1m tonight ."


def test_parse_checks_kind_year_and_types(env):
    raw = _doc(_item(title="Film", year=2019),
               _item(title="Series", kind="series"),
               _item(title="Video", kind="video"),
               _item(title="Old", year=1899),
               _item(title="Far", year=2101),
               _item(title="Text year", year="2019"),
               _item(title="Bool year", year=True),
               _item(title="Float year", year=2019.0),
               _item(title="No why", why=None),
               _item(title="!!!"),
               _item(title=" "),
               "not an item")
    items = requests.parse(raw, NOW)
    assert [(i.title, i.year, i.kind) for i in items] == [("Film", 2019, "film"), ("Series", None, "series")]


def test_stale_or_after_0400_is_empty(env):
    raw = _doc(_item())
    next_cut = datetime(2026, 10, 23, 4, 0, tzinfo=ZONE)
    assert _titles(raw, next_cut - timedelta(seconds=1)) == ["The Lantern Keeper"]
    assert _titles(raw, next_cut) == []
    assert _titles(raw, CREATED + timedelta(hours=25)) == []
    # Made after midnight: shown until 04:00 the same morning.
    early = _doc(_item(), created_at="2026-10-22T18:00:00Z")  # 02:00 local on 23 October
    assert _titles(early, next_cut - timedelta(minutes=1)) == ["The Lantern Keeper"]
    assert _titles(early, next_cut) == []
    # Made at 04:00 sharp: shown until the next 04:00.
    at_cut = _doc(_item(), created_at=next_cut.isoformat())
    assert _titles(at_cut, next_cut + timedelta(hours=23)) == ["The Lantern Keeper"]
    # A clock up to 5 minutes ahead is allowed; a request from further in the future is not.
    assert _titles(raw, CREATED - timedelta(minutes=5)) == ["The Lantern Keeper"]
    assert _titles(raw, CREATED - timedelta(minutes=6)) == []


def test_cut_follows_the_time_zone_setting(env):
    env.write_text("TV_KEY=t\nTIME_ZONE=UTC\n")
    raw = _doc(_item())  # 11:00 UTC on 22 October
    assert _titles(raw, datetime(2026, 10, 23, 3, 59, tzinfo=ZoneInfo("UTC"))) == ["The Lantern Keeper"]
    assert _titles(raw, datetime(2026, 10, 23, 4, 0, tzinfo=ZoneInfo("UTC"))) == []
    # 04:00 in Perth is 20:00 UTC: still before the cut in UTC.
    assert _titles(raw, datetime(2026, 10, 23, 4, 0, tzinfo=ZONE)) == ["The Lantern Keeper"]


@respx.mock
async def test_row_gone_after_0400_while_file_stays(docker):
    _tmdb({"The Lantern Keeper": LANTERN})
    docker.file = _doc(_item())
    before = await requests.picks(datetime(2026, 10, 23, 3, 59, tzinfo=ZONE))
    assert [f.title for f in before.films] == ["The Lantern Keeper"]
    after = await requests.picks(datetime(2026, 10, 23, 4, 0, tzinfo=ZONE))
    assert after.films == [] and docker.calls == 1  # the cut is checked on the cached file


@respx.mock
async def test_unresolved_title_dropped_rest_kept(docker):
    search = _tmdb({"The Lantern Keeper": LANTERN, "Dune Sea": DUNE, "Broken": httpx.Response(500)})
    docker.file = _doc(_item(why="A lighthouse, a ghost, a long night."),
                       _item(title="Not On Any Service"),
                       _item(title="Broken"),
                       _item(title="Dune Sea", year=2021, why="Big desert\nfilm."),
                       _item(title="Dune Sea", year=2021, kind="series"))
    out = await requests.picks(NOW)
    assert [(f.title, f.service, f.kind, f.why, f.runtime_min) for f in out.films] == [
        ("The Lantern Keeper", "netflix", "film", "A lighthouse, a ghost, a long night.", 101),
        ("Dune Sea", "hbomax", "film", "Big desert film.", 101)]
    assert out.films[0].url == catalog.HOMES["netflix"]
    assert (out.label, out.row, out.refreshing, out.slot) == ("From Hermes", "hermes", False, "night")
    sent = [(c.request.url.path, c.request.url.params["query"]) for c in search.calls]
    assert sorted(sent) == [("/3/search/movie", "Broken"), ("/3/search/movie", "Dune Sea"),
                            ("/3/search/movie", "Not On Any Service"), ("/3/search/movie", "The Lantern Keeper"),
                            ("/3/search/tv", "Dune Sea")]


@respx.mock
async def test_tmdb_error_is_not_cached(docker, monkeypatch, caplog):
    movies = {"The Lantern Keeper": httpx.Response(500)}
    search = _tmdb(movies)
    monkeypatch.setattr(requests, "_now", lambda: 1000.0)  # well within the 60 s of a kept result
    docker.file = _doc(_item())
    assert (await requests.picks(NOW)).films == []
    # httpx puts the request URL, api_key included, in its error text.
    assert "find failed: http 500" in caplog.text and "v3key" not in caplog.text
    movies["The Lantern Keeper"] = LANTERN
    # A TMDB blip does not blank the row for a minute: the next call asks again.
    assert [f.title for f in (await requests.picks(NOW)).films] == ["The Lantern Keeper"]
    assert search.call_count == 2
    await requests.picks(NOW)
    assert search.call_count == 2  # a clean answer is kept


async def test_one_failing_find_drops_only_that_item(docker, monkeypatch):
    async def find(title, year, kind):
        if title == "Boom":
            raise RuntimeError("bad answer")
        return catalog._film("films", LANTERN[0], "netflix")
    monkeypatch.setattr(catalog, "find", find)
    docker.file = _doc(_item(title="Boom"), _item())
    assert [f.title for f in (await requests.picks(NOW)).films] == ["The Lantern Keeper"]
    calls = []
    monkeypatch.setattr(catalog, "find", lambda *a: calls.append(a) or find(*a))
    await requests.picks(NOW)
    assert len(calls) == 2  # a result with an error is not cached: the next call asks again


@respx.mock
async def test_resolution_cached_for_60s_by_file(docker, monkeypatch):
    search = _tmdb({"The Lantern Keeper": LANTERN, "Dune Sea": DUNE})
    clock = [1000.0]
    monkeypatch.setattr(requests, "_now", lambda: clock[0])
    docker.file = _doc(_item())
    assert len((await requests.picks(NOW)).films) == 1
    assert search.call_count == 1 and docker.calls == 1
    docker.file = _doc(_item(), _item(title="Dune Sea"))
    clock[0] += 30  # the next read sees the new file, which is resolved at once, within 60 s of the old one
    assert [f.title for f in (await requests.picks(NOW)).films] == ["The Lantern Keeper", "Dune Sea"]
    assert search.call_count == 3 and docker.calls == 2
    clock[0] += 59  # read again, the same file: its resolution is kept
    assert len((await requests.picks(NOW)).films) == 2
    assert search.call_count == 3 and docker.calls == 3
    clock[0] += 2
    await requests.picks(NOW)
    assert search.call_count == 5 and docker.calls == 3


async def test_file_read_at_most_once_in_5s(docker, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(requests, "_now", lambda: clock[0])
    await requests.picks(NOW)  # the file is missing: that is cached too
    clock[0] += 4.9
    await requests.picks(NOW)
    assert docker.calls == 1
    clock[0] += 0.1
    await requests.picks(NOW)
    assert docker.calls == 2


async def test_rejected_file_logs_one_fixed_reason(docker, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="hermestv.requests")
    monkeypatch.setattr(requests, "READ_CACHE_S", 0)  # each call reads the file again
    secret = "private-detail-xyz"
    cases = [
        (f'{{"{secret}"'.encode(), "the file is not JSON"),
        (b"", "the file is not JSON"),
        (json.dumps({"created_at": CREATED.isoformat(), "items": secret}).encode(), "the file has no items list"),
        (_doc(_item(why=secret), created_at=None), "created_at is missing"),
        (_doc(_item(why=secret), created_at="2026-10-22T19:00:00"), "created_at has no time zone offset"),
        (_doc(_item(why=secret), created_at=secret), "created_at is not a date"),
        (_doc(_item(why=secret), created_at="9999-12-31T04:00:00+08:00"), "created_at is not a date"),
        (_doc(_item(why=secret), created_at=(NOW + timedelta(minutes=6)).isoformat()), "created_at is in the future"),
        (_doc(_item(why=secret), created_at=(NOW - timedelta(hours=25)).isoformat()),
         "the request is over: past 04:00 or 24 h old"),
        (_doc(_item(title=secret, why="see https://evil.example")), "every item was dropped"),
    ]
    for raw, reason in cases:
        caplog.clear()
        docker.file = raw
        for _ in range(3):
            assert (await requests.picks(NOW)).films == []
        # One line per file, not one per TV request, and never the file's text.
        assert [r.getMessage() for r in caplog.records] == [f"requests: requests.json not shown: {reason}"], reason
        assert caplog.records[0].levelname == ("INFO" if "over" in reason else "WARNING")
        assert secret not in caplog.text and "evil" not in caplog.text
    assert requests._check(b" " * (64 * 1024 + 1), NOW) == ([], "the file is over 64 KB")
    # An empty list clears the row on purpose: nothing to log.
    caplog.clear()
    docker.file = _doc()
    assert (await requests.picks(NOW)).films == [] and caplog.records == []


@respx.mock
async def test_hidden_title_left_out(docker, monkeypatch):
    _tmdb({"The Lantern Keeper": LANTERN, "Dune Sea": DUNE})
    monkeypatch.setattr(watchlog, "schedule", lambda: None)
    history.hide("film", "The Lantern Keeper", 2024, None, None)
    # An open does not hide a title here: Hermes may ask for a rewatch.
    history.opened("film", "Dune Sea", 2021, None, catalog.HOMES["hbomax"], "night", now=NOW - timedelta(days=1))
    docker.file = _doc(_item(), _item(title="Dune Sea"))
    assert [f.title for f in (await requests.picks(NOW)).films] == ["Dune Sea"]


@respx.mock
async def test_container_down_row_empty(docker):
    search = _tmdb({"The Lantern Keeper": LANTERN})
    out = await requests.picks(NOW)  # docker.file is None: the container is down or the file is missing
    assert (out.films, out.label, out.row, out.refreshing) == ([], "From Hermes", "hermes", False)
    assert docker.calls == 1
    docker.file = b"x" * (64 * 1024 + 1)
    requests._reset()
    assert (await requests.picks(NOW)).films == []
    assert docker.calls == 2 and search.call_count == 0


async def test_feature_off_reads_nothing(env, monkeypatch):
    env.write_text("TV_KEY=t\nHERMES_CONTAINER=hermes-test\nHERMES_TV_DIR=\n")
    reads = []

    async def read(*args):
        reads.append(args)
        return _doc(_item())
    monkeypatch.setattr(exchange, "read", read)
    out = await requests.picks(NOW)
    assert (out.films, out.row) == ([], "hermes") and reads == []  # no read, so no "read failed" warning


def test_route_row_hermes_needs_tv_key(env, monkeypatch):
    served = PicksOut(slot="night", label="From Hermes", row="hermes", films=[], updated_at=None)
    calls = []

    async def picks(now=None):
        calls.append(now)
        return served
    monkeypatch.setattr(requests, "picks", picks)
    with TestClient(app_mod.app) as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "t"}):
            assert client.get("/v1/picks?row=hermes", headers=headers).status_code == 401
        assert calls == []
        r = client.get("/v1/picks?row=hermes", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200 and r.json()["row"] == "hermes" and r.json()["label"] == "From Hermes"
    assert len(calls) == 1


@pytest.mark.parametrize("raw", [b"{", _doc(_item(), created_at="0001-01-01T00:00:00+00:00"),
                                 _doc(_item(), created_at="9999-12-31T04:00:00+08:00"),
                                 _doc(_item(why="see https://evil.example"), _item(title="Kept"))])
def test_route_survives_hostile_file(env, docker, monkeypatch, raw):
    async def find(title, year, kind):
        return catalog._film("films", LANTERN[0], "netflix")
    monkeypatch.setattr(catalog, "find", find)
    picks = requests.picks
    # The clock is pinned: the request in the file is for NOW, not for the day the test runs.
    monkeypatch.setattr(requests, "picks", lambda now=None: picks(NOW))
    docker.file = raw
    with TestClient(app_mod.app) as client:
        r = client.get("/v1/picks?row=hermes", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200 and r.json()["row"] == "hermes"
    assert [f["why"] for f in r.json()["films"]] == (["A slow, cold ghost story."] if b"Kept" in raw else [])
    assert "evil" not in r.text


LANTERN_FILM = catalog.Film(title="The Lantern Keeper", year=2024, runtime_min=None, rating=7.4, service="netflix",
                            url=catalog.HOMES["netflix"], poster="https://image.tmdb.org/t/p/w342/p.jpg")
AMELIE = catalog.Film(title="Amélie", year=2001, runtime_min=None, rating=None, service="extra",
                      url="demo://title/movie/amelie-2001", poster=None)


def _finds(monkeypatch, on_catalog, on_extra):
    """catalog.find and extra.find from two {title: Film | Exception} maps; records every call."""
    calls = []

    def fake(name, found):
        async def find(title, year, kind):
            calls.append((name, title))
            got = found.get(title)
            if isinstance(got, Exception):
                raise got
            return got
        return find
    monkeypatch.setattr(catalog, "find", fake("catalog", on_catalog))
    monkeypatch.setattr(extra, "find", fake("extra", on_extra))
    return calls


async def test_netflix_wins_over_extra(docker, monkeypatch):
    calls = _finds(monkeypatch, {"The Lantern Keeper": LANTERN_FILM}, {"The Lantern Keeper": AMELIE})
    docker.file = _doc(_item())
    assert [f.service for f in (await requests.picks(NOW)).films] == ["netflix"]
    assert calls == [("catalog", "The Lantern Keeper")]


async def test_not_on_catalog_found_on_extra(docker, monkeypatch):
    calls = _finds(monkeypatch, {}, {"Amelie": AMELIE})
    docker.file = _doc(_item(title="Amelie", why="Warm and odd."))
    [film] = (await requests.picks(NOW)).films
    assert (film.service, film.url, film.why, film.kind) == ("extra", "demo://title/movie/amelie-2001",
                                                              "Warm and odd.", "film")
    assert calls == [("catalog", "Amelie"), ("extra", "Amelie")]


async def test_catalog_failure_does_not_fall_through(docker, monkeypatch):
    # TMDB down must not turn a Netflix title into an extra-source card, nor be cached.
    calls = _finds(monkeypatch, {"The Lantern Keeper": httpx.ConnectTimeout("slow")},
                   {"The Lantern Keeper": AMELIE})
    docker.file = _doc(_item())
    assert (await requests.picks(NOW)).films == []
    assert calls == [("catalog", "The Lantern Keeper")]
    _finds(monkeypatch, {"The Lantern Keeper": LANTERN_FILM}, {})
    assert [f.service for f in (await requests.picks(NOW)).films] == ["netflix"]


async def test_extra_failure_drops_item_not_cached(docker, monkeypatch):
    docker.file = _doc(_item(title="Amelie"), _item())
    _finds(monkeypatch, {"The Lantern Keeper": LANTERN_FILM}, {"Amelie": httpx.ReadTimeout("slow")})
    assert [f.title for f in (await requests.picks(NOW)).films] == ["The Lantern Keeper"]
    calls = _finds(monkeypatch, {"The Lantern Keeper": LANTERN_FILM}, {"Amelie": AMELIE})
    assert [f.title for f in (await requests.picks(NOW)).films] == ["Amélie", "The Lantern Keeper"]
    assert ("extra", "Amelie") in calls


@respx.mock
async def test_extra_off_keeps_todays_behaviour(docker, monkeypatch):
    # No extra-source settings: the real extra.find makes no request and does not fail, so the gap is cached.
    calls = []
    monkeypatch.setattr(catalog, "find", lambda *a: calls.append(a) or _none())
    docker.file = _doc(_item(title="Amelie"))
    assert (await requests.picks(NOW)).films == []
    assert (await requests.picks(NOW)).films == []
    assert len(calls) == 1 and not respx.calls


async def _none():
    return None


async def test_no_tmdb_key_never_uses_extra(docker, env, monkeypatch):
    # Without TMDB the bridge cannot tell a Netflix title from a missing one, so it must not guess the extra source.
    env.write_text(env.read_text().replace("TMDB_API_KEY=v3key\n", ""))
    calls = _finds(monkeypatch, {}, {"Amelie": AMELIE})
    docker.file = _doc(_item(title="Amelie"))
    assert (await requests.picks(NOW)).films == []
    assert calls == [("catalog", "Amelie")]
