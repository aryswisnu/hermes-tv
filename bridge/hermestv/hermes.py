import asyncio
import json
import logging
import time
from dataclasses import dataclass

import httpx

from hermestv import config
from hermestv.errors import brief
from hermestv.prompt import BACKGROUND_NOTE

RUN_TIMEOUT_S = 90.0
STOP_TIMEOUT_S = 10.0
TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}
FAILED_EVENTS = TERMINAL_EVENTS - {"run.completed"}
HEALTH_TIMEOUT_S = 3.0
HEALTH_CACHE_S = 60.0
# Hermes still booting: it or its proxy answers with these, or it ends a run at once with no event.
NOT_READY_STATUS = {502, 503, 504}
AT_ONCE_S = 10.0

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Done:
    text: str


@dataclass(frozen=True)
class NeedsApproval:
    run_id: str
    request_id: str


@dataclass(frozen=True)
class Offline:
    reason: str
    connect: bool = False  # Hermes was not reachable or not ready (booting), so no model work was done


RunResult = Done | NeedsApproval | Offline


@dataclass
class _Run:
    started: float
    run_id: str | None = None  # set once Hermes accepted the run
    ended: bool = False  # Hermes sent a terminal run.* event
    events: int = 0  # events before the terminal one

    def at_once(self) -> bool:
        return self.events == 0 and _now() - self.started < AT_ONCE_S


def enabled() -> bool:
    """False when HERMES_URL is empty: the bridge then runs without Hermes and never calls it."""
    return config.load().hermes_url != ""


def _client(timeout: float | None = None) -> httpx.AsyncClient:
    s = config.load()
    if not s.hermes_url:
        raise ValueError("HERMES_URL is not set")
    if not s.hermes_key:
        raise ValueError("HERMES_KEY is not set")
    return httpx.AsyncClient(
        base_url=s.hermes_url,
        headers={"Authorization": f"Bearer {s.hermes_key}"},
        timeout=timeout or RUN_TIMEOUT_S,
    )


async def _guarded(work, run: _Run) -> RunResult:
    try:
        async with asyncio.timeout(RUN_TIMEOUT_S):
            return await work
    except (TimeoutError, httpx.TimeoutException):
        return Offline("timeout")
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        return Offline(f"http {code}", connect=code in NOT_READY_STATUS)
    except httpx.ConnectError as e:
        return Offline(str(e) or type(e).__name__, connect=True)
    except (httpx.NetworkError, httpx.RemoteProtocolError) as e:
        return Offline(str(e) or type(e).__name__, connect=run.at_once())
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as e:
        return Offline(str(e) or type(e).__name__)


async def _start(text: str, key: str, run: _Run) -> RunResult:
    async with _client() as client:
        r = await client.post(
            "/v1/runs",
            json={"input": text, "instructions": BACKGROUND_NOTE},
            headers={"X-Hermes-Session-Key": key},
        )
        r.raise_for_status()
        run.run_id = r.json()["run_id"]
        async with client.stream("GET", f"/v1/runs/{run.run_id}/events") as events:
            events.raise_for_status()
            async for line in events.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line.removeprefix("data:"))
                name = event.get("event")
                run.ended = name in TERMINAL_EVENTS
                if not run.ended:
                    run.events += 1
                if name == "approval.request":
                    return NeedsApproval(run.run_id, event["request_id"])
                if name == "run.completed":
                    return Done(event.get("output") or "")
                if name in FAILED_EVENTS:
                    return Offline(event.get("error") or name, connect=run.at_once())
    return Offline("stream ended", connect=run.at_once())


async def _stop(run_id: str) -> None:
    try:
        async with _client(STOP_TIMEOUT_S) as client:
            r = await client.post(f"/v1/runs/{run_id}/stop")
            r.raise_for_status()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("hermes: run %s was not stopped: %s", run_id, brief(e))


_now = time.monotonic
_health: tuple[float, bool] | None = None  # (checked at, up)


async def probe() -> bool:
    """Hermes's own /health, which makes no model call."""
    try:
        async with _client() as client:
            r = await client.get("/health", timeout=HEALTH_TIMEOUT_S)
        return r.status_code == 200
    except (httpx.HTTPError, ValueError):
        return False


async def healthy() -> bool:
    global _health
    if _health is None or _now() - _health[0] >= HEALTH_CACHE_S:
        _health = (_now(), await probe())
    return _health[1]


async def start_run(text: str, session_key: str) -> RunResult:
    """One background run. A run that asks for a tool or outlives its timeout is stopped on Hermes:
    nobody answers its approval and nobody reads its late answer."""
    run = _Run(started=_now())
    result = await _guarded(_start(text, session_key, run), run)
    if run.run_id is not None and not run.ended:
        await _stop(run.run_id)
    return result
