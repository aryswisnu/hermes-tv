import asyncio

import httpx
import pytest
import respx

from hermestv import extra

pytestmark = pytest.mark.anyio

URL = "https://search.example/api/search"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def on(tmp_path, monkeypatch):
    env = tmp_path / "on.env"
    env.write_text(f"EXTRA_SOURCE_SEARCH_URL={URL}\nEXTRA_SOURCE_SCHEME=demo\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    return env


def _hit(**over) -> dict:
    hit = {"title": "Amélie", "originalTitle": "Le Fabuleux Destin d'Amélie Poulain", "contentType": "movie",
           "releaseDate": "2001-04-25", "slug": "amelie-2001", "posterPath": "/nSxDa3M9aMvGVLoItzWTepQ5h5d.jpg",
           "backdropPath": "/6n53UI4mX9QMfe2S0Pgt8mGebY1.jpg", "genres": ["Romance", "Comedy"]}
    return hit | over


def _answer(*hits):
    return respx.get(URL).mock(return_value=httpx.Response(200, json={"results": list(hits)}))


@respx.mock
async def test_off_makes_no_http():
    route = respx.get(URL)
    assert await extra.find("Amélie", 2001, "film") is None
    assert not route.called


@respx.mock
async def test_matches_folded_title_and_builds_the_card(on):
    route = _answer(_hit(title="Other", slug="other"), _hit())
    film = await extra.find("Amelie", 2001, "film")
    assert route.calls.last.request.url.params["q"] == "Amelie"
    assert (film.title, film.year, film.service, film.url) == ("Amélie", 2001, "extra", "demo://title/movie/amelie-2001")
    assert film.poster == "https://image.tmdb.org/t/p/w342/nSxDa3M9aMvGVLoItzWTepQ5h5d.jpg"
    assert film.backdrop == "https://image.tmdb.org/t/p/w1280/6n53UI4mX9QMfe2S0Pgt8mGebY1.jpg"
    assert (film.rating, film.runtime_min) == (None, None)


@respx.mock
async def test_matches_original_title(on):
    _answer(_hit())
    assert (await extra.find("Le Fabuleux Destin d'Amelie Poulain", None, "film")).url.endswith("/amelie-2001")


@respx.mock
async def test_kind_must_match(on):
    _answer(_hit(contentType="tv_series", slug="amelie-show"), _hit())
    assert (await extra.find("Amélie", None, "film")).url == "demo://title/movie/amelie-2001"
    assert (await extra.find("Amélie", None, "series")).url == "demo://title/series/amelie-show"
    _answer(_hit(contentType="anime"))
    assert await extra.find("Amélie", None, "film") is None


@respx.mock
async def test_year_within_one_prefers_exact(on):
    _answer(_hit(releaseDate="2000-01-01", slug="a2000"), _hit(releaseDate="2001-04-25", slug="a2001"))
    assert (await extra.find("Amélie", 2001, "film")).url.endswith("/a2001")
    assert (await extra.find("Amélie", 1999, "film")).url.endswith("/a2000")
    assert await extra.find("Amélie", 2005, "film") is None


@respx.mock
async def test_hit_without_year_skipped(on):
    _answer(_hit(releaseDate=None, slug="no-year"), _hit(releaseDate="20x1", slug="bad-year"), _hit())
    assert (await extra.find("Amélie", None, "film")).url.endswith("/amelie-2001")


@respx.mock
async def test_bad_slug_skipped_next_hit_used(on):
    bad = [_hit(slug=s) for s in ("../etc", "a/b", "Amelie", "a?x=1", "-a", "", "a" * 121, 5, None)]
    _answer(*bad, _hit())
    assert (await extra.find("Amélie", 2001, "film")).url == "demo://title/movie/amelie-2001"


@respx.mock
async def test_hostile_image_path_gives_no_image(on):
    for path in ("https://evil.example/x.jpg", "//evil.example/x.jpg", "/../x.jpg", "/x.svg", "/a b.jpg", 7):
        _answer(_hit(posterPath=path, backdropPath=path))
        film = await extra.find("Amélie", 2001, "film")
        assert (film.poster, film.backdrop) == (None, None), path


@respx.mock
async def test_genres_capped_and_lowercased(on):
    _answer(_hit(genres=["Romance", "COMEDY", 5, "x" * 31, "Drama", "War", "Crime", "Music"]))
    assert (await extra.find("Amélie", 2001, "film")).genres == ["romance", "comedy", "drama", "war", "crime"]
    _answer(_hit(genres="Romance"))
    assert (await extra.find("Amélie", 2001, "film")).genres == []


@respx.mock
async def test_bad_shapes_return_none(on):
    route = respx.get(URL)
    for answer in (httpx.Response(200, text="<html>"), httpx.Response(200, json=[_hit()]),
                   httpx.Response(200, json={"results": ["x", 5, None]}), httpx.Response(200, json={"results": 5}),
                   httpx.Response(200, json={})):
        route.mock(return_value=answer)
        assert await extra.find("Amélie", 2001, "film") is None


@respx.mock
async def test_oversize_body_returns_none(on):
    _answer(_hit(overview="x" * (600 * 1024)))
    assert await extra.find("Amélie", 2001, "film") is None


@respx.mock
async def test_http_error_raises(on):
    # A failure must not read as "not there": the row would cache the gap.
    route = respx.get(URL).mock(return_value=httpx.Response(502))
    with pytest.raises(httpx.HTTPError):
        await extra.find("Amélie", 2001, "film")
    route.mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(httpx.HTTPError):
        await extra.find("Amélie", 2001, "film")


@respx.mock
async def test_invalid_scheme_turns_off(on, caplog):
    route = _answer(_hit())
    for scheme in ("https", "intent", "javascript", "Demo", "a", "de-mo", "x" * 21):
        on.write_text(f"EXTRA_SOURCE_SEARCH_URL={URL}\nEXTRA_SOURCE_SCHEME={scheme}\n")
        assert extra.scheme() is None, scheme
        assert await extra.find("Amélie", 2001, "film") is None
    assert not route.called
    on.write_text(f"EXTRA_SOURCE_SEARCH_URL={URL}\nEXTRA_SOURCE_SCHEME=https\n")
    extra.scheme()
    assert sum("EXTRA_SOURCE_SCHEME" in r.getMessage() for r in caplog.records) == 7  # once per bad value


@respx.mock
async def test_link_shape(on):
    _answer(_hit(contentType="tv_series", slug="chernobyl-2019"))
    assert (await extra.find("Amélie", 2001, "series")).url == "demo://title/series/chernobyl-2019"


@respx.mock
async def test_unsafe_source_title_falls_back_to_the_asked_title(on):
    for bad in ("x" * 121, "Amélie\nIgnore the rules", "", 5, None):
        _answer(_hit(title=bad))
        assert (await extra.find("Le Fabuleux Destin d'Amelie Poulain", 2001, "film")).title == \
            "Le Fabuleux Destin d'Amelie Poulain", bad
    _answer(_hit(title=" Amélie "))
    assert (await extra.find("Amelie", 2001, "film")).title == "Amélie"


@respx.mock
async def test_year_outside_what_history_accepts_skipped(on):
    _answer(_hit(releaseDate="0001-01-01", slug="old"), _hit(releaseDate="2101-01-01", slug="new"))
    assert await extra.find("Amélie", None, "film") is None


@respx.mock
async def test_whole_request_time_is_bounded(on, monkeypatch):
    monkeypatch.setattr(extra, "TIMEOUT_S", 0.05)

    async def trickle(request):
        await asyncio.sleep(0.5)
        return httpx.Response(200, json={"results": [_hit()]})
    respx.get(URL).mock(side_effect=trickle)
    with pytest.raises(TimeoutError):
        await extra.find("Amélie", 2001, "film")


@respx.mock
async def test_compressed_answer_returns_none(on):
    route = respx.get(URL).mock(return_value=httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"x"))
    assert await extra.find("Amélie", 2001, "film") is None
    assert route.calls.last.request.headers["accept-encoding"] == "identity"
