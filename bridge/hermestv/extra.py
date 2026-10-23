"""The extra source: one more TV app with a search API, tried for a From Hermes title that is not on Netflix or
HBO Max. Its answer is checked strictly, so the TV only ever gets `<scheme>://title/<movie|series>/<slug>`."""
import asyncio
import json
import logging
import re
import unicodedata
from typing import Literal

import httpx

from hermestv import config
from hermestv.catalog import IMAGE_BASE, Film
from hermestv.titles import _norm

TIMEOUT_S = 5.0
MAX_BYTES = 512 * 1024
MAX_GENRES = 5
MAX_GENRE = 30
MAX_TITLE = 120
YEARS = range(1880, 2101)  # what /v1/opened and /v1/hide accept
SCHEME = re.compile(r"[a-z][a-z0-9]{1,19}")
DENIED = {"http", "https", "intent", "file", "content", "javascript", "market"}
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,119}")
IMAGE = re.compile(r"/[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp)")
KINDS = {"movie": "film", "tv_series": "series"}
PATHS = {"film": "movie", "series": "series"}

log = logging.getLogger(__name__)

_bad: set[str] = set()  # scheme values already logged as invalid


def _reset() -> None:
    _bad.clear()


def scheme() -> str | None:
    """The link scheme, or None when the feature is off or the setting is not a safe scheme."""
    value = config.load().extra_source_scheme
    if not value:
        return None
    if SCHEME.fullmatch(value) and value not in DENIED:
        return value
    if value not in _bad:
        _bad.add(value)
        log.warning("extra: EXTRA_SOURCE_SCHEME is not a safe scheme: the extra source is off")
    return None


def _fold(s: str) -> str:
    return _norm("".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)))


def _image(path, size: str) -> str | None:
    return IMAGE_BASE + size + path if isinstance(path, str) and IMAGE.fullmatch(path) else None


def _year(date) -> int | None:
    year = int(date[:4]) if isinstance(date, str) and re.match(r"\d{4}(-|$)", date) else None
    return year if year in YEARS else None


def _title(hit: dict, asked: str) -> str:
    """The source's title when it is one plain line of at most 120 characters, else the title Hermes asked for."""
    t = hit.get("title")
    ok = isinstance(t, str) and 0 < len(t.strip()) and len(t) <= MAX_TITLE
    return t.strip() if ok and not any(unicodedata.category(c).startswith("C") for c in t) else asked


def _genres(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [g.casefold() for g in raw if isinstance(g, str) and 0 < len(g) <= MAX_GENRE][:MAX_GENRES]


async def _results(url: str, title: str) -> list | None:
    """The `results` list, or None when the answer is too big or not the documented shape; a request failure or a
    whole request over TIMEOUT_S raises."""
    # httpx's timeout is per read, so a server that trickles bytes needs this bound; identity: no gzip bomb.
    async with asyncio.timeout(TIMEOUT_S), httpx.AsyncClient(
            timeout=TIMEOUT_S, headers={"Accept-Encoding": "identity", "User-Agent": config.USER_AGENT}) as client:
        async with client.stream("GET", url, params={"q": title}) as r:
            r.raise_for_status()
            if r.headers.get("content-encoding", "identity") != "identity":
                return None
            body = b""
            async for chunk in r.aiter_raw():
                body += chunk
                if len(body) > MAX_BYTES:
                    return None
    try:
        doc = json.loads(body)
    except (ValueError, RecursionError):
        return None
    results = doc.get("results") if isinstance(doc, dict) else None
    return results if isinstance(results, list) else None


async def find(title: str, year: int | None, kind: Literal["film", "series"]) -> Film | None:
    """The title on the extra source: the same accent-folded title or original title, the kind, then the year (one
    off is allowed, the exact year first); None when it is not there or the feature is off."""
    url, link = config.load().extra_source_search_url, scheme()
    if not url or not link:
        return None
    results = await _results(url, title)
    if results is None:
        log.warning("extra: search answer not usable")
        return None
    want = _fold(title)
    hits = []
    for hit in results:
        if not isinstance(hit, dict) or KINDS.get(hit.get("contentType")) != kind:
            continue
        names = [n for n in (hit.get("title"), hit.get("originalTitle")) if isinstance(n, str)]
        slug, hit_year = hit.get("slug"), _year(hit.get("releaseDate"))
        if not names or want not in map(_fold, names) or not isinstance(slug, str) or not SLUG.fullmatch(slug):
            continue
        if hit_year is None or (year is not None and abs(hit_year - year) > 1):
            continue
        hits.append((hit, hit_year, slug))
    if year is not None:
        hits.sort(key=lambda h: h[1] != year)
    if not hits:
        return None
    hit, hit_year, slug = hits[0]
    return Film(title=_title(hit, title), year=hit_year, runtime_min=None,
                rating=None, service="extra", url=f"{link}://title/{PATHS[kind]}/{slug}",
                poster=_image(hit.get("posterPath"), "w342"), backdrop=_image(hit.get("backdropPath"), "w1280"),
                genres=_genres(hit.get("genres")))
