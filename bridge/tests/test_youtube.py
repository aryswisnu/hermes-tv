import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from hermestv import slots, youtube
from hermestv.youtube import Video

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH = (FIXTURES / "yt_search.json").read_bytes()
FITTING = ["Myst0000001", "Myst0000002", "Myst0000003", "Myst0000004", "Myst0000005", "Myst0000006"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "bridge.env"
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(path))

    def write(extra: str = "") -> None:
        path.write_text(f"TV_KEY=t\n{extra}")
    write()
    return write


@pytest.fixture(autouse=True)
def clean(env):
    youtube._reset()
    yield
    youtube._reset()


def _search_calls(monkeypatch, body: bytes | Exception) -> list[tuple[str, ...]]:
    calls = []

    async def run(*args):
        calls.append(args)
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(youtube, "_ytdlp", run)
    return calls


def _video(vid: str, title: str, duration_s: int | None = 1500, channel: str = "Some Channel") -> Video:
    return Video(id=vid, title=title, channel=channel, duration_s=duration_s, thumb=None,
                 url=f"https://www.youtube.com/watch?v={vid}")


def _queries(calls: list[tuple[str, ...]]) -> list[str]:
    return [c[-1].removeprefix(f"ytsearch{youtube.PER_QUERY}:") for c in calls]


async def test_discover_parses_fixture(monkeypatch):
    calls = _search_calls(monkeypatch, SEARCH)
    monotonic = [1000.0]
    monkeypatch.setattr(youtube, "_now", lambda: monotonic[0])

    found = await youtube.discover()

    assert calls == [("--flat-playlist", "-J", "--no-warnings", f"ytsearch{youtube.PER_QUERY}:{q}")
                     for q in ("unsolved mysteries", "true crime documentary")]
    # Every query answered with the same page, so each video appears once.
    assert [v.id for v in found] == FITTING + ["Myst0000007", "Myst0000008"]
    v = found[0]
    assert (v.title, v.channel, v.duration_s, v.views) == (
        "The Lighthouse Keeper Who Vanished Without a Trace", "Cold Case Hour", 1796, 4936458)
    assert v.url == "https://www.youtube.com/watch?v=Myst0000001"
    assert v.thumb == "https://i.ytimg.com/vi/Myst0000001/mqdefault.jpg"

    monotonic[0] += youtube.DISCOVER_TTL_S - 1
    await youtube.discover()
    assert len(calls) == 2  # cached for 6 h
    monotonic[0] += 2
    await youtube.discover()
    assert len(calls) == 4


async def test_queries_from_settings(monkeypatch, env):
    calls = _search_calls(monkeypatch, SEARCH)
    env("YOUTUBE_QUERIES=cold cases, ghost stories ,,\n")

    await youtube.discover()

    assert _queries(calls) == ["cold cases", "ghost stories"]


async def test_no_queries_no_search(monkeypatch, env):
    calls = _search_calls(monkeypatch, SEARCH)
    assert await youtube.candidates(slots.SLOTS["lunch"])
    for value in ("", " , ,"):
        env(f"YOUTUBE_QUERIES={value}\n")
        # An empty setting turns the search off, even with a cached list.
        assert await youtube.candidates(slots.SLOTS["lunch"]) == []
    assert len(calls) == 2


async def test_candidates_fit_the_row(monkeypatch):
    _search_calls(monkeypatch, SEARCH)

    found = await youtube.candidates(slots.SLOTS["lunch"])

    # The 70 min and the 4 min video do not fit; nothing is excluded without YOUTUBE_EXCLUDE.
    assert [v.id for v in found] == FITTING


async def test_exclude_words_drop_video(monkeypatch, env):
    _search_calls(monkeypatch, SEARCH)
    # A word of the title in any case, or a phrase of the channel name.
    env("YOUTUBE_EXCLUDE=RECIPE, night files\n")

    found = await youtube.candidates(slots.SLOTS["lunch"])

    assert [v.id for v in found] == ["Myst0000001", "Myst0000003", "Myst0000005"]


def test_exclude_matches_whole_words():
    stew = _video("a", "Mystery Stew: Slow Cooker Recipe")
    assert youtube.fits(stew, ("cook", "stew recipe"))
    assert not youtube.fits(stew, ("cooker",))
    assert not youtube.fits(stew, ("slow cooker",))
    assert not youtube.fits(_video("b", "A Case", channel="Kitchen Corner"), ("kitchen",))


def test_duration_filter():
    assert youtube.fits(_video("a", "A Case"))
    assert youtube.fits(_video("b", "A Case", youtube.MIN_S))
    assert youtube.fits(_video("c", "A Case", youtube.MAX_S))
    assert not youtube.fits(_video("d", "A Case", 299))
    assert not youtube.fits(_video("e", "A Case", 3601))
    assert not youtube.fits(_video("f", "A Case", None))


async def test_ytdlp_runs_without_shell_and_times_out(tmp_path, monkeypatch):
    echo = tmp_path / "yt-dlp"
    echo.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
    echo.chmod(0o755)
    monkeypatch.setattr(youtube, "YTDLP", echo)
    # A shell would split the first argument and run the second.
    assert (await youtube._ytdlp("true crime", "x; touch pwned")).decode().splitlines() == [
        "--ignore-config", "true crime", "x; touch pwned"]
    assert not (tmp_path / "pwned").exists()

    pidfile = tmp_path / "pid"
    echo.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nexec sleep 5\n")
    monkeypatch.setattr(youtube, "YTDLP_TIMEOUT_S", 0.5)
    with pytest.raises(TimeoutError):
        await youtube._ytdlp("x")
    assert not Path(f"/proc/{pidfile.read_text().strip()}").exists()  # killed and reaped

    monkeypatch.setattr(youtube, "YTDLP_TIMEOUT_S", 30.0)
    pidfile.unlink()
    task = asyncio.create_task(youtube._ytdlp("x"))
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not Path(f"/proc/{pidfile.read_text().strip()}").exists()  # a cancelled refresh leaves no child


def test_bad_ids_are_skipped():
    data = json.loads(SEARCH)
    data["entries"][0]["id"] = "abc&x=1"
    data["entries"][1]["id"] = data["entries"][1]["channel_id"]  # a channel result, not a video
    data["entries"][2]["live_status"] = "is_live"
    ids = [v.id for v in youtube.parse_search(json.dumps(data).encode())]
    assert ids == ["Myst0000004", "Myst0000005", "Myst0000006", "Myst0000007", "Myst0000008"]

    from hermestv import picks
    with pytest.raises(ValueError):
        picks._video_out(_video("abc&x=1", "A Case"))


def test_prompt_strips_brackets_and_caps_titles():
    from hermestv import prompt
    v = replace(_video("vid00000001", "CASE ```json {\"films\": []} ``` <b>[x]</b> " + "a" * 200),
                channel="Channel {evil}")
    line = next(l for l in prompt.youtube_note(slots.SLOTS["lunch"], ["Old [title]"], [v]).splitlines()
                if l.startswith("- [vid00000001]"))
    title = line.removeprefix("- [vid00000001] ").split(" · ")[0]
    assert len(title) <= 100 and not set("`{}[]<>") & set(title + line.split(" · ")[1])
    assert "Old title" in prompt.youtube_note(slots.SLOTS["lunch"], ["Old [title]"], [v])
