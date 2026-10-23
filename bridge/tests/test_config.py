import ast
from pathlib import Path

import pytest

from hermestv import config


def test_load_reads_env_file(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text(
        "# comment\n"
        "HERMES_URL=http://hermes:9000\n"
        "HERMES_KEY=hk=with=equals\n"
        "\n"
        "TV_KEY=tv123\n"
    )
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    assert s == config.Settings(
        hermes_url="http://hermes:9000",
        hermes_key="hk=with=equals",
        tv_key="tv123",
    )


def test_load_defaults_when_keys_absent(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("HERMES_KEY=\nTV_KEY=t\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    assert s.hermes_url == ""  # no Hermes unless HERMES_URL names one
    assert s.hermes_key == ""


def test_load_reads_tmdb_settings(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=t\nTMDB_API_KEY=abc123\nREGION=GB\nLOCAL_LANGUAGE=ko\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    assert (s.tmdb_api_key, s.region, s.local_language) == ("abc123", "GB", "ko")


def test_tmdb_settings_default_when_absent(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=t\nREGION=\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    assert (s.tmdb_api_key, s.region, s.local_language) == ("", "US", "en")


def test_load_reads_schedule_settings(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=t\nSCHEDULE_JOBS=Pay rent, Book club ,,\nHERMES_CONTAINER=hermes-agent\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    assert (s.schedule_jobs, s.hermes_container) == (frozenset({"Pay rent", "Book club"}), "hermes-agent")


def test_schedule_settings_default_when_absent(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=t\nHERMES_CONTAINER=\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    assert (s.schedule_jobs, s.hermes_container) == (frozenset(), "hermes")


def test_load_reads_hermes_exchange_settings(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=t\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    # Empty folders mean the feature is off.
    assert (s.hermes_tv_dir, s.hermes_vault_tv_dir) == ("", "")

    env.write_text("TV_KEY=t\nHERMES_TV_DIR=/opt/data/hermes-tv\nHERMES_VAULT_TV_DIR=/opt/data/notes/TV\n")
    s = config.load()
    assert (s.hermes_tv_dir, s.hermes_vault_tv_dir) == ("/opt/data/hermes-tv", "/opt/data/notes/TV")


def test_load_reads_time_zone_and_location(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=t\nTIME_ZONE=Asia/Tokyo\nLATITUDE=35.6762\nLONGITUDE=139.6503\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    assert (s.time_zone, s.latitude, s.longitude) == ("Asia/Tokyo", 35.6762, 139.6503)


@pytest.mark.parametrize("text", ["", "TIME_ZONE=\nLATITUDE=\nLONGITUDE=\n", "LATITUDE=north\nLONGITUDE=1,5\n"])
def test_time_zone_and_location_default_when_absent(tmp_path, monkeypatch, text):
    env = tmp_path / "bridge.env"
    env.write_text(f"TV_KEY=t\n{text}")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    assert (s.time_zone, s.latitude, s.longitude) == ("UTC", None, None)


def _env_keys_read(source: str) -> set[str]:
    """The string keys of `env.get(...)`, `env[...]` and `... in env` in the source."""
    keys = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "env" and node.args):
            key = node.args[0]
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "env":
            key = node.slice
        elif (isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.In)
              and isinstance(node.comparators[0], ast.Name) and node.comparators[0].id == "env"):
            key = node.left
        else:
            continue
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def test_env_example_lists_every_key_load_reads():
    # Keys come from config.py itself, so a new setting without an example line fails here.
    read = _env_keys_read(Path(config.__file__).read_text())
    example = config._read_env_file(Path(__file__).parent.parent / "bridge.env.example")
    assert read and set(example) == read


@pytest.mark.parametrize("lat, lon, expected", [
    ("nan", "10", (None, 10.0)), ("inf", "10", (None, 10.0)), ("10", "-inf", (10.0, None)),
    ("90.5", "10", (None, 10.0)), ("-91", "10", (None, 10.0)), ("10", "180.1", (10.0, None)), ("10", "-181", (10.0, None)),
])
def test_location_out_of_range_is_unset(tmp_path, monkeypatch, caplog, lat, lon, expected):
    monkeypatch.setattr(config, "_warned", set(), raising=False)
    env = tmp_path / "bridge.env"
    env.write_text(f"TV_KEY=t\nLATITUDE={lat}\nLONGITUDE={lon}\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    for _ in range(3):
        s = config.load()
    assert (s.latitude, s.longitude) == expected
    assert len(caplog.records) == 1  # once, not on every load
    assert lat not in caplog.text and lon not in caplog.text  # the location itself is never logged


def test_location_range_ends_are_valid(tmp_path, monkeypatch, caplog):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=t\nLATITUDE=-90\nLONGITUDE=180\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    assert (config.load().latitude, config.load().longitude) == (-90.0, 180.0)
    assert caplog.records == []


def test_extra_source_settings_default_empty_and_read(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("TV_KEY=t\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    s = config.load()
    assert (s.extra_source_search_url, s.extra_source_scheme) == ("", "")  # off

    env.write_text("TV_KEY=t\nEXTRA_SOURCE_SEARCH_URL=https://search.example/api/search\nEXTRA_SOURCE_SCHEME=demo\n")
    s = config.load()
    assert (s.extra_source_search_url, s.extra_source_scheme) == ("https://search.example/api/search", "demo")
