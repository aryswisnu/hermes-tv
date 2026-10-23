from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

import httpx

from hermestv import config
from hermestv.errors import brief
from hermestv.slots import Row, Rule, Slot
from hermestv.titles import _norm

if TYPE_CHECKING:
    from hermestv.picks import PickFilmOut

API = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/"
TIMEOUT_S = 8.0
PAGES = 3
MAX_CANDIDATES = 60
MIN_VOTES = 50
AVAILABLE_TTL_S = 6 * 3600
DETAILS_TTL_S = 7 * 24 * 3600
FIND_FIRST = 3  # search results whose providers are asked for
# TMDB watch provider ids, in order of preference: a title on both counts as Netflix.
PROVIDERS: dict[Literal["netflix", "hbomax"], int] = {"netflix": 8, "hbomax": 1899}
# TMDB has no deep link to a title, so OK opens the service app.
HOMES = {"netflix": "https://www.netflix.com/", "hbomax": "https://www.max.com/"}
# TMDB genre ids to the names the picker uses.
MOVIE_GENRES = {
    28: "action", 16: "animation", 35: "comedy", 80: "crime", 99: "documentary", 18: "drama", 10751: "family",
    14: "fantasy", 36: "history", 27: "horror", 10402: "music", 10749: "romance", 878: "scifi", 53: "thriller",
    10752: "war", 37: "western",
}
TV_GENRES = {
    10759: "action", 16: "animation", 35: "comedy", 80: "crime", 99: "documentary", 18: "drama", 10751: "family",
    10765: "scifi", 9648: "mystery", 10768: "war", 37: "western", 10764: "reality",
}
MYSTERY = 9648  # a TV query asks for this when the rule's genre has a movie id but no TV id
DOCUMENTARY, HORROR = 99, 27
KINDS: dict[str, str] = {"films": "movie", "series": "tv"}
_MOVIE_IDS = {name: i for i, name in MOVIE_GENRES.items()}
_TV_IDS = {name: i for i, name in TV_GENRES.items()}

log = logging.getLogger(__name__)


@dataclass
class Film:
    title: str
    year: int
    runtime_min: int | None
    rating: float | None
    service: Literal["netflix", "hbomax"]
    url: str
    poster: str
    why: str = ""
    backdrop: str | None = None
    genres: list[str] = field(default_factory=list)
    seasons: int | None = None
    tmdb_id: int = 0


# (row, rule) -> (when fetched, films); lunch and dinner share one entry.
_available: dict[tuple[str, Rule], tuple[float, list[Film]]] = {}
# (movie or tv, TMDB id) -> (when fetched, runtime or seasons)
_details: dict[tuple[str, int], tuple[float, int | None]] = {}
_no_key_logged = False
_now = time.monotonic


def _client(key: str) -> httpx.AsyncClient:
    # A v4 read access token is a JWT, so it has dots; a v3 API key has none.
    auth = {"headers": {"Authorization": f"Bearer {key}"}} if "." in key else {"params": {"api_key": key}}
    headers = {"User-Agent": config.USER_AGENT, **auth.get("headers", {})}
    return httpx.AsyncClient(base_url=API, timeout=TIMEOUT_S, headers=headers, params=auth.get("params"))


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    r = await client.get(path, params=params)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError(f"not a JSON object: {path}")
    return data


def _api_key() -> str:
    global _no_key_logged
    key = config.load().tmdb_api_key
    if not key and not _no_key_logged:
        log.warning("TMDB_API_KEY is not set: the films and series rows stay empty")
        _no_key_logged = True
    return key


def _image(path: str | None, size: str) -> str | None:
    return IMAGE_BASE + size + path if path else None


def _genre_id(row: Row, name: str) -> int | None:
    if row == "films":
        return _MOVIE_IDS.get(name)
    return _TV_IDS.get(name) or (MYSTERY if name in _MOVIE_IDS else None)


def _film(row: Row, item: dict, service: Literal["netflix", "hbomax"], need_rating: bool = True) -> Film | None:
    """The TMDB item as a Film; need_rating False also takes a title with no votes yet."""
    title = item.get("title" if row == "films" else "name")
    date = item.get("release_date" if row == "films" else "first_air_date") or ""
    rating = item.get("vote_average")
    if not isinstance(rating, int | float) or isinstance(rating, bool) or item.get("vote_count") == 0:
        rating = None
    if not (title and item.get("poster_path") and date[:4].isdigit() and isinstance(item.get("id"), int)
            and (rating is not None or not need_rating)):
        return None
    names = MOVIE_GENRES if row == "films" else TV_GENRES
    return Film(
        title=title,
        year=int(date[:4]),
        runtime_min=None,
        rating=None if rating is None else float(rating),
        service=service,
        url=HOMES[service],
        poster=_image(item["poster_path"], "w500"),
        backdrop=_image(item.get("backdrop_path"), "w1280"),
        genres=[names[g] for g in item.get("genre_ids") or [] if g in names],
        tmdb_id=item["id"],
    )


def fits(rule: Rule, film: Film | PickFilmOut) -> bool:
    if film.rating < rule.min_rating or "documentary" in film.genres:
        return False
    if rule.no_horror and "horror" in film.genres:
        return False
    # A series found through the TV query's stand-in genre (mystery) fits too.
    wanted = {*rule.genres, *(TV_GENRES[i] for g in rule.genres if (i := _genre_id("series", g)))}
    return not rule.genres or not wanted.isdisjoint(film.genres)


def _query(row: Row, rule: Rule, settings: config.Settings) -> dict[str, str | int | float]:
    without = [DOCUMENTARY, HORROR] if rule.no_horror else [DOCUMENTARY]
    q: dict[str, str | int | float] = {
        "watch_region": settings.region, "with_watch_monetization_types": "flatrate",
        "vote_average.gte": rule.min_rating, "vote_count.gte": MIN_VOTES,
        "without_genres": ",".join(map(str, without)), "sort_by": "popularity.desc"}
    if ids := [i for i in dict.fromkeys(_genre_id(row, g) for g in rule.genres) if i]:
        q["with_genres"] = "|".join(map(str, ids))
    if rule.local:
        q["with_original_language"] = settings.local_language
    return q


async def _fetch(row: Row, rule: Rule) -> list[Film]:
    settings = config.load()
    query = _query(row, rule, settings)
    films: dict[int, Film] = {}
    done: set[str] = set()  # services with no further page
    async with _client(settings.tmdb_api_key) as client:
        for page in range(1, PAGES + 1):
            for service, provider in PROVIDERS.items():
                if service in done:
                    continue
                data = await _get(client, f"/discover/{KINDS[row]}",
                                  {**query, "with_watch_providers": provider, "page": page})
                for item in data.get("results") or []:
                    if (f := _film(row, item, service)) and f.tmdb_id not in films:
                        films[f.tmdb_id] = f
                if page >= (data.get("total_pages") or 0):
                    done.add(service)
            if len(done) == len(PROVIDERS) or len(films) >= MAX_CANDIDATES:
                break
    return list(films.values())[:MAX_CANDIDATES]


async def available(slot: Slot, row: Row = "films") -> list[Film]:
    """Films or series of the slot on Netflix or HBO Max in the region now, in TMDB popularity order."""
    if not _api_key():
        return []
    rule = slot.rule(row)
    key = (row, rule)
    hit = _available.get(key)
    if hit and _now() - hit[0] < AVAILABLE_TTL_S:
        return hit[1]
    try:
        films = await _fetch(row, rule)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("available %s %s: tmdb failed: %s", slot.name, row, brief(e))
        return hit[1] if hit else []
    films = [f for f in films if fits(rule, f)]
    # An empty answer is not cached, so the next refresh asks TMDB again.
    if films:
        _available[key] = (_now(), films)
    return films


async def _detail(client: httpx.AsyncClient, kind: str, tmdb_id: int) -> int | None:
    hit = _details.get((kind, tmdb_id))
    if hit and _now() - hit[0] < DETAILS_TTL_S:
        return hit[1]
    try:
        data = await _get(client, f"/{kind}/{tmdb_id}")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("details %s %d: tmdb failed: %s", kind, tmdb_id, brief(e))
        return None
    value = data.get("runtime" if kind == "movie" else "number_of_seasons")
    value = value if isinstance(value, int) and value > 0 else None
    _details[(kind, tmdb_id)] = (_now(), value)
    return value


async def details(films: list[Film], row: Row) -> list[Film]:
    """Fills the runtime of films or the seasons of series; a failed call leaves None."""
    key = _api_key()
    if not key or not films:
        return films
    kind = KINDS[row]
    async with _client(key) as client:
        values = await asyncio.gather(*(_detail(client, kind, f.tmdb_id) for f in films))
    name = "runtime_min" if row == "films" else "seasons"
    return [replace(f, **{name: v}) if v is not None else f for f, v in zip(films, values)]


async def _service(client: httpx.AsyncClient, kind: str, tmdb_id: int,
                   region: str) -> Literal["netflix", "hbomax"] | None:
    data = await _get(client, f"/{kind}/{tmdb_id}/watch/providers")
    offers = (data.get("results") or {}).get(region) or {}
    ids = {p.get("provider_id") for p in offers.get("flatrate") or []}
    return next((service for service, provider in PROVIDERS.items() if provider in ids), None)


async def find(title: str, year: int | None, kind: Literal["film", "series"]) -> Film | None:
    """The title on Netflix or HBO Max in the region now: the same normalised title, then the year (one off is
    allowed, as release years differ between sources); None when it is not there. A TMDB failure raises, so the
    caller can tell it from "not there" and ask again."""
    key = _api_key()
    if not key:
        return None
    row: Row = "films" if kind == "film" else "series"
    settings = config.load()
    async with _client(key) as client:
        data = await _get(client, f"/search/{KINDS[row]}", {"query": title})
        films = [f for item in data.get("results") or []
                 if (f := _film(row, item, "netflix", need_rating=False)) and _norm(f.title) == _norm(title)]
        if year is not None:
            films = sorted((f for f in films if abs(f.year - year) <= 1), key=lambda f: f.year != year)
        for f in films[:FIND_FIRST]:
            if service := await _service(client, KINDS[row], f.tmdb_id, settings.region):
                film = replace(f, service=service, url=HOMES[service])
                break
        else:
            return None
    return (await details([film], row))[0]
