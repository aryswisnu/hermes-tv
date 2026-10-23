import logging
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV_FILE = Path.home() / ".config" / "hermes-tv" / "bridge.env"
USER_AGENT = "HermesTV/1.0"
DEFAULT_YOUTUBE_QUERIES = ("unsolved mysteries", "true crime documentary")

log = logging.getLogger(__name__)
_warned: set[str] = set()  # settings already logged as unusable; load() runs on every request


@dataclass(frozen=True)
class Settings:
    hermes_url: str
    hermes_key: str
    tv_key: str
    tmdb_api_key: str = ""
    region: str = "US"
    local_language: str = "en"
    youtube_queries: tuple[str, ...] = DEFAULT_YOUTUBE_QUERIES
    youtube_exclude: tuple[str, ...] = ()
    # Exact Hermes job names the TV may show; no other job ever appears, whatever jobs.json says.
    schedule_jobs: frozenset[str] = frozenset()
    hermes_container: str = "hermes"
    # Folders under /opt/data/ in the Hermes container; empty turns that export off.
    hermes_tv_dir: str = ""
    hermes_vault_tv_dir: str = ""
    time_zone: str = "UTC"
    # An optional extra source for the From Hermes row; both empty turns it off.
    extra_source_search_url: str = ""
    extra_source_scheme: str = ""
    # Both set, or no weather.
    latitude: float | None = None
    longitude: float | None = None


def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _coordinate(name: str, value: str | None, limit: int) -> float | None:
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        number = None
    if number is not None and -limit <= number <= limit:  # false for nan and inf too
        return number
    if name not in _warned:
        _warned.add(name)
        log.warning("%s is not a number from %d to %d: no weather", name, -limit, limit)
    return None


def load() -> Settings:
    path = Path(os.environ.get("HERMESTV_ENV_FILE", DEFAULT_ENV_FILE))
    env = _read_env_file(path)
    return Settings(
        # Empty runs the bridge without Hermes.
        hermes_url=env.get("HERMES_URL", ""),
        hermes_key=env.get("HERMES_KEY", ""),
        tv_key=env.get("TV_KEY", ""),
        tmdb_api_key=env.get("TMDB_API_KEY", ""),
        region=env.get("REGION") or "US",
        local_language=env.get("LOCAL_LANGUAGE") or "en",
        # Present but empty turns the YouTube search off.
        youtube_queries=_list(env["YOUTUBE_QUERIES"]) if "YOUTUBE_QUERIES" in env else DEFAULT_YOUTUBE_QUERIES,
        youtube_exclude=_list(env.get("YOUTUBE_EXCLUDE", "")),
        schedule_jobs=frozenset(_list(env.get("SCHEDULE_JOBS", ""))),
        hermes_container=env.get("HERMES_CONTAINER") or "hermes",
        hermes_tv_dir=env.get("HERMES_TV_DIR", ""),
        hermes_vault_tv_dir=env.get("HERMES_VAULT_TV_DIR", ""),
        time_zone=env.get("TIME_ZONE") or "UTC",
        extra_source_search_url=env.get("EXTRA_SOURCE_SEARCH_URL", ""),
        extra_source_scheme=env.get("EXTRA_SOURCE_SCHEME", ""),
        latitude=_coordinate("LATITUDE", env.get("LATITUDE"), 90),
        longitude=_coordinate("LONGITUDE", env.get("LONGITUDE"), 180),
    )
