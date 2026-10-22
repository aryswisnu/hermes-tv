import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import respx

from hermestv import catalog, config, slots

FIXTURES = Path(__file__).parent / "fixtures"
DISCOVER_MOVIE = f"{catalog.API}/discover/movie"
DISCOVER_TV = f"{catalog.API}/discover/tv"
EMPTY = {"page": 1, "results": [], "total_pages": 0, "total_results": 0}
V4_TOKEN = "eyJhbGciOiJIUzI1NiJ9.e30.signature"
NIGHT, LUNCH, DINNER, MORNING = (slots.SLOTS[name] for name in ("night", "lunch", "dinner", "morning"))

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"tmdb_{name}.json").read_text())


def _env(tmp_path, monkeypatch, text: str) -> None:
    env = tmp_path / "bridge.env"
    env.write_text(text)
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))


@pytest.fixture
def tmdb(tmp_path, monkeypatch):
    """A bridge.env with a v3 key, a clock the test moves, and empty caches."""
    _env(tmp_path, monkeypatch, "TV_KEY=t\nTMDB_API_KEY=v3key\n")
    clock = [1000.0]
    monkeypatch.setattr(catalog, "_now", lambda: clock[0])
    monkeypatch.setattr(catalog, "_no_key_logged", False)
    catalog._available.clear()
    catalog._details.clear()
    yield clock
    catalog._available.clear()
    catalog._details.clear()


def _by_provider(pages: dict[str, dict]):
    """Answers each discover call with the page of the provider it asks for."""
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params["with_watch_providers"]])
    return answer


def _page(results: list[dict], total_pages: int = 1) -> dict:
    return {"page": 1, "results": results, "total_pages": total_pages, "total_results": len(results)}


def _film(**kw) -> catalog.Film:
    return replace(catalog.Film("X", 2020, 100, 7.4, "netflix", "u", "p", genres=["drama"]), **kw)


@respx.mock
async def test_discover_query_params(tmdb, tmp_path, monkeypatch):
    movies = respx.get(DISCOVER_MOVIE).mock(return_value=httpx.Response(200, json=EMPTY))
    shows = respx.get(DISCOVER_TV).mock(return_value=httpx.Response(200, json=EMPTY))

    await catalog.available(NIGHT, "films")

    sent = [dict(c.request.url.params) for c in movies.calls]
    # One call per provider, Netflix first.
    assert [p.pop("with_watch_providers") for p in sent] == ["8", "1899"]
    assert sent[0] == sent[1] == {
        "api_key": "v3key", "watch_region": "US", "with_watch_monetization_types": "flatrate",
        "vote_average.gte": "7.0", "vote_count.gte": "50", "without_genres": "99", "with_genres": "27|18",
        "sort_by": "popularity.desc", "page": "1"}
    assert "authorization" not in movies.calls[0].request.headers

    # A v4 read access token has dots and goes in the header; the region and language come from bridge.env.
    _env(tmp_path, monkeypatch, f"TMDB_API_KEY={V4_TOKEN}\nREGION=GB\nLOCAL_LANGUAGE=ko\n")
    await catalog.available(LUNCH, "films")

    request = movies.calls.last.request
    p = dict(request.url.params)
    assert request.headers["authorization"] == f"Bearer {V4_TOKEN}"
    assert "api_key" not in p
    assert (p["watch_region"], p["vote_average.gte"], p["without_genres"], p["with_genres"],
            p["with_original_language"]) == ("GB", "6.5", "99,27", "35|10751|10749", "ko")

    # TV has no horror or romance genre; both ask for mystery instead.
    await catalog.available(NIGHT, "series")
    await catalog.available(LUNCH, "series")
    night_tv, lunch_tv = (dict(shows.calls[i].request.url.params) for i in (0, 2))
    assert (night_tv["with_genres"], night_tv["vote_average.gte"], "with_original_language" in night_tv) == (
        "9648|18", "7.5", False)
    assert (lunch_tv["with_genres"], lunch_tv["with_original_language"]) == ("35|10751|9648", "ko")


@respx.mock
async def test_film_fields(tmdb):
    respx.get(DISCOVER_MOVIE).mock(side_effect=_by_provider({"8": _load("discover_movie"), "1899": EMPTY}))
    respx.get(DISCOVER_TV).mock(side_effect=_by_provider({"8": EMPTY, "1899": _load("discover_tv")}))

    films = await catalog.available(NIGHT, "films")

    assert films[0] == catalog.Film(
        title="The Lantern Keeper", year=2024, runtime_min=None, rating=7.6, service="netflix",
        url="https://www.netflix.com/", poster="https://image.tmdb.org/t/p/w500/lanternKeeperPoster01.jpg",
        backdrop="https://image.tmdb.org/t/p/w1280/lanternKeeperBackdrop01.jpg", genres=["horror", "thriller"],
        tmdb_id=900001)
    # A documentary and a film under the night minimum are dropped; the order stays TMDB's.
    assert [f.title for f in films] == ["The Lantern Keeper", "Harbour of Small Hours", "Glass Orchard",
                                        "Midnight Ferry"]
    assert films[2].genres == ["drama", "romance"]  # an unknown genre id is skipped

    shows = await catalog.available(NIGHT, "series")

    assert [s.title for s in shows] == ["The Hollow Line"]
    hollow = shows[0]
    assert (hollow.year, hollow.rating, hollow.genres, hollow.service, hollow.url, hollow.seasons,
            hollow.tmdb_id) == (2024, 8.2, ["mystery", "drama"], "hbomax", "https://www.max.com/", None, 800001)


@respx.mock
async def test_service_url_is_app_home(tmdb):
    lantern, harbour, glass = _load("discover_movie")["results"][:3]
    route = respx.get(DISCOVER_MOVIE).mock(side_effect=_by_provider({
        "8": _page([lantern, harbour]), "1899": _page([harbour, glass])}))

    films = await catalog.available(NIGHT, "films")

    assert [c.request.url.params["with_watch_providers"] for c in route.calls] == ["8", "1899"]
    # A title on both services counts once, as Netflix.
    assert [(f.title, f.service, f.url) for f in films] == [
        ("The Lantern Keeper", "netflix", "https://www.netflix.com/"),
        ("Harbour of Small Hours", "netflix", "https://www.netflix.com/"),
        ("Glass Orchard", "hbomax", "https://www.max.com/"),
    ]


@respx.mock
async def test_missing_poster_or_date_dropped(tmdb):
    data = _load("discover_movie")
    data["results"].append({"id": 900099, "title": "Bare Minimum", "genre_ids": [27]})
    respx.get(DISCOVER_MOVIE).mock(side_effect=_by_provider({"8": data, "1899": EMPTY}))
    respx.get(DISCOVER_TV).mock(side_effect=_by_provider({"8": _load("discover_tv"), "1899": EMPTY}))

    films = await catalog.available(NIGHT, "films")
    shows = {s.title for s in await catalog.available(NIGHT, "series")}

    titles = {f.title for f in films}
    assert "Untitled Night Project" not in titles  # no poster
    assert "The Quiet Signal" not in titles  # empty release date
    assert "Bare Minimum" not in titles  # no poster, date or rating at all
    assert "Lowtide" not in shows  # null first air date
    # No backdrop is not a reason to drop a title.
    glass = next(f for f in films if f.title == "Glass Orchard")
    assert glass.backdrop is None and glass.poster.endswith("/glassOrchardPoster03.jpg")


@respx.mock
async def test_no_key_returns_empty_without_request(tmdb, tmp_path, monkeypatch, caplog):
    _env(tmp_path, monkeypatch, "TV_KEY=t\n")
    movies = respx.get(DISCOVER_MOVIE).mock(return_value=httpx.Response(200, json=_load("discover_movie")))
    detail = respx.get(f"{catalog.API}/movie/900001").mock(
        return_value=httpx.Response(200, json=_load("movie_detail")))
    film = _film(tmdb_id=900001, runtime_min=None)

    assert await catalog.available(NIGHT) == []
    assert await catalog.available(NIGHT, "series") == []
    assert await catalog.details([film], "films") == [film]

    assert movies.call_count == 0 and detail.call_count == 0
    assert [r.levelname for r in caplog.records] == ["WARNING"]  # logged once, not on every refresh


@respx.mock
async def test_tmdb_failure_log_has_no_key(tmdb, caplog):
    # httpx puts the request URL, api_key included, in its error text.
    respx.get(DISCOVER_MOVIE).mock(return_value=httpx.Response(401))
    respx.get(f"{catalog.API}/movie/900001").mock(return_value=httpx.Response(401))

    assert await catalog.available(NIGHT) == []
    assert await catalog.details([_film(tmdb_id=900001)], "films") == [_film(tmdb_id=900001)]

    assert len(caplog.records) == 2
    assert all("401" in r.getMessage() for r in caplog.records)
    assert "v3key" not in caplog.text


@respx.mock
async def test_available_caches_by_row_and_rule(tmdb):
    route = respx.get(DISCOVER_MOVIE).mock(side_effect=_by_provider({"8": _load("discover_movie"), "1899": EMPTY}))

    films = await catalog.available(NIGHT)
    assert route.call_count == 2

    tmdb[0] += catalog.AVAILABLE_TTL_S - 1
    assert await catalog.available(NIGHT) == films
    assert route.call_count == 2

    assert [f.title for f in await catalog.available(DINNER)] == ["Glass Orchard"]
    # Lunch has the same rule as dinner, so it reads dinner's list.
    assert [f.title for f in await catalog.available(LUNCH)] == ["Glass Orchard"]
    assert route.call_count == 4

    tmdb[0] += 2
    await catalog.available(NIGHT)
    assert route.call_count == 6


@respx.mock
async def test_available_error_returns_empty_and_is_not_cached(tmdb, caplog):
    route = respx.get(DISCOVER_MOVIE).mock(side_effect=httpx.ConnectTimeout("timeout"))

    assert await catalog.available(NIGHT) == []
    assert [r.levelname for r in caplog.records] == ["WARNING"]

    route.mock(return_value=httpx.Response(200, text="not json"), side_effect=None)
    assert await catalog.available(NIGHT) == []
    route.mock(return_value=httpx.Response(200, json=EMPTY), side_effect=None)
    assert await catalog.available(NIGHT) == []
    route.mock(side_effect=_by_provider({"8": _load("discover_movie"), "1899": EMPTY}))
    films = await catalog.available(NIGHT)
    assert len(films) == 4
    assert route.call_count == 1 + 1 + 2 + 2

    # After the TTL a failed call shows the last list, not an empty row.
    tmdb[0] += catalog.AVAILABLE_TTL_S + 1
    route.mock(side_effect=httpx.ConnectTimeout("timeout"))
    caplog.clear()
    assert await catalog.available(NIGHT) == films
    assert [r.levelname for r in caplog.records] == ["WARNING"]


def _items(start: int, n: int = 20) -> list[dict]:
    lantern = _load("discover_movie")["results"][0]
    return [{**lantern, "id": i, "title": f"Title {i}"} for i in range(start, start + n)]


@respx.mock
async def test_pages_stop_at_total_pages_and_cap_at_60(tmdb):
    netflix = [_page(_items(100 * p), total_pages=5) for p in range(1, 6)]
    hbo = [_page(_items(100), total_pages=1)]  # the same titles as Netflix's first page

    def answer(request: httpx.Request) -> httpx.Response:
        pages = netflix if request.url.params["with_watch_providers"] == "8" else hbo
        return httpx.Response(200, json=pages[int(request.url.params["page"]) - 1])
    route = respx.get(DISCOVER_MOVIE).mock(side_effect=answer)

    films = await catalog.available(NIGHT)

    # HBO Max has one page only, so it is not asked again; three pages is the limit.
    assert [(c.request.url.params["with_watch_providers"], c.request.url.params["page"]) for c in route.calls] == [
        ("8", "1"), ("1899", "1"), ("8", "2"), ("8", "3")]
    assert len(films) == 60 and len({f.tmdb_id for f in films}) == 60

    # Two full providers reach 60 after the second page and stop there.
    hbo[:] = [_page(_items(10_000 + 100 * p), total_pages=5) for p in range(1, 6)]
    catalog._available.clear()
    films = await catalog.available(NIGHT)
    assert route.call_count == 4 + 4
    assert len(films) == 60
    assert [f.tmdb_id for f in films[:21]] == [*range(100, 120), 10_100]


def test_unknown_rule_genre_is_skipped():
    # "mystery" has no movie id and "nonsense" has no id at all; neither may break the query.
    rule = slots.Rule(7.0, ("mystery", "drama", "nonsense"))
    settings = config.Settings(hermes_url="", hermes_key="", tv_key="")
    assert catalog._query("films", rule, settings)["with_genres"] == "18"
    assert catalog._query("series", rule, settings)["with_genres"] == "9648|18"
    assert "with_genres" not in catalog._query("films", slots.Rule(7.0, ("nonsense",)), settings)
    assert catalog.fits(rule, _film(genres=["drama"])) is True
    assert catalog.fits(rule, _film(genres=["comedy"])) is False


def test_fits():
    film = _film()
    assert catalog.fits(NIGHT.films, film) is True
    assert catalog.fits(NIGHT.series, film) is False  # series need 7.5 at night
    assert catalog.fits(NIGHT.series, replace(film, rating=7.5, genres=["horror"])) is True
    assert catalog.fits(NIGHT.films, replace(film, genres=["thriller"])) is False
    assert catalog.fits(NIGHT.films, replace(film, genres=["drama", "documentary"])) is False
    assert catalog.fits(LUNCH.films, replace(film, genres=["comedy", "horror"])) is False
    assert catalog.fits(LUNCH.films, replace(film, genres=["comedy", "thriller"])) is True
    # A series the query found as mystery (TV has no horror) fits the night; a morning rule asks no mystery.
    assert catalog.fits(NIGHT.series, replace(film, rating=8.0, genres=["mystery"])) is True
    assert catalog.fits(MORNING.series, replace(film, rating=8.0, genres=["mystery"])) is False
    assert catalog.fits(MORNING.series, replace(film, rating=8.0, genres=["animation"])) is True


@respx.mock
async def test_details_fill_runtime_and_seasons(tmdb):
    movie = respx.get(f"{catalog.API}/movie/900001").mock(
        return_value=httpx.Response(200, json=_load("movie_detail")))
    tv = respx.get(f"{catalog.API}/tv/800001").mock(return_value=httpx.Response(200, json=_load("tv_detail")))
    film = _film(title="The Lantern Keeper", runtime_min=None, tmdb_id=900001)
    show = _film(title="The Hollow Line", runtime_min=None, tmdb_id=800001)

    assert await catalog.details([film], "films") == [replace(film, runtime_min=112)]
    assert await catalog.details([show], "series") == [replace(show, seasons=3)]
    assert movie.calls.last.request.url.params["api_key"] == "v3key"
    assert tv.call_count == 1

    # Cached by id for 7 days.
    tmdb[0] += catalog.DETAILS_TTL_S - 1
    await catalog.details([film], "films")
    assert movie.call_count == 1
    tmdb[0] += 2
    await catalog.details([film], "films")
    assert movie.call_count == 2


@respx.mock
async def test_details_failure_keeps_none(tmdb, caplog):
    broken = respx.get(f"{catalog.API}/movie/900001").mock(return_value=httpx.Response(500))
    respx.get(f"{catalog.API}/movie/900002").mock(
        return_value=httpx.Response(200, json={"id": 900002, "runtime": 95}))
    films = [_film(title="A", runtime_min=None, tmdb_id=900001), _film(title="B", runtime_min=None, tmdb_id=900002)]

    out = await catalog.details(films, "films")

    assert [f.runtime_min for f in out] == [None, 95]
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    # A failure is not cached: the next refresh asks again.
    broken.mock(return_value=httpx.Response(200, json=_load("movie_detail")))
    assert [f.runtime_min for f in await catalog.details(films, "films")] == [112, 95]
