import asyncio
import json

import httpx
import pytest
import respx

from hermestv import hermes
from hermestv.hermes import Done, NeedsApproval, Offline
from hermestv.prompt import BACKGROUND_NOTE

pytestmark = pytest.mark.anyio

BASE = "http://hermes.test"
RUN_ID = "run_0123456789abcdef0123456789abcdef"
REQUEST_ID = "fedcba9876543210fedcba9876543210"
SESSION = "hermes-tv-20260101"

APPROVAL_REQUEST = {
    "event": "approval.request", "run_id": RUN_ID, "timestamp": 1792000000.1234567,
    "command": "<terminal> (plugin approval rule)", "pattern_key": "plugin_rule:terminal",
    "pattern_keys": ["plugin_rule:terminal"], "description": "Plugin requires approval for terminal",
    "allow_permanent": True, "allow_session": True, "request_id": REQUEST_ID,
    "choices": ["once", "session", "always", "deny"],
}
DONE_EVENTS = [
    {"event": "tool.started", "run_id": RUN_ID, "timestamp": 1792000020.10, "tool": "read_file", "preview": "hostname"},
    {"event": "tool.completed", "run_id": RUN_ID, "timestamp": 1792000020.20, "tool": "read_file",
     "duration": 0.118, "error": False, "preview": "{\"content\": \"1|0a1b2c3d4e5f\"}"},
    {"event": "message.delta", "run_id": RUN_ID, "timestamp": 1792000030.10, "delta": "4"},
    {"event": "reasoning.available", "run_id": RUN_ID, "timestamp": 1792000030.20, "text": "4."},
    {"event": "run.completed", "run_id": RUN_ID, "timestamp": 1792000030.30, "output": "4.",
     "usage": {"input_tokens": 1200, "output_tokens": 6, "total_tokens": 1206},
     "completed": True, "partial": False, "interrupted": False},
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def env_file(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text(f"HERMES_URL={BASE}\nHERMES_KEY=test-key\nTV_KEY=tv\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))


def _sse(events: list[dict]) -> httpx.Response:
    body = ": keepalive\n\n" + "".join(f"data: {json.dumps(e)}\n\n" for e in events) + ": stream closed\n\n"
    return httpx.Response(200, content=body.encode(), headers={"Content-Type": "text/event-stream"})


def _mock_start(events: list[dict]):
    post = respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(202, json={"run_id": RUN_ID, "status": "started", "replayed": False}))
    respx.get(f"{BASE}/v1/runs/{RUN_ID}/events").mock(return_value=_sse(events))
    return post


def _mock_stop():
    return respx.post(f"{BASE}/v1/runs/{RUN_ID}/stop").mock(
        return_value=httpx.Response(200, json={"run_id": RUN_ID, "status": "stopping"}))


@respx.mock
async def test_run_done():
    post = _mock_start(DONE_EVENTS)
    stop = _mock_stop()
    assert await hermes.start_run("what is 2+2", SESSION) == Done("4.")
    assert not stop.called  # a finished run is not stopped
    req = post.calls.last.request
    assert req.headers["Authorization"] == "Bearer test-key"
    assert req.headers["X-Hermes-Session-Key"] == SESSION
    assert json.loads(req.content) == {"input": "what is 2+2", "instructions": BACKGROUND_NOTE}


@respx.mock
async def test_run_needs_approval_is_stopped():
    # Nobody answers an approval on a background run, so the run must not wait on Hermes.
    _mock_start([APPROVAL_REQUEST, DONE_EVENTS[-1]])
    stop = _mock_stop()
    approval = respx.post(f"{BASE}/v1/runs/{RUN_ID}/approval")
    assert await hermes.start_run("buat file catatan", SESSION) == NeedsApproval(RUN_ID, REQUEST_ID)
    assert stop.call_count == 1
    assert stop.calls.last.request.headers["Authorization"] == "Bearer test-key"
    assert not approval.called


RUN_FAILED = {"event": "run.failed", "run_id": RUN_ID, "timestamp": 1.0, "error": "provider down"}


@respx.mock
async def test_run_failed_offline():
    _mock_start([DONE_EVENTS[2], RUN_FAILED])
    stop = _mock_stop()
    result = await hermes.start_run("hi", SESSION)
    assert result == Offline("provider down") and result.connect is False  # the model was at work
    assert not stop.called  # the run has ended on Hermes


@respx.mock
async def test_run_failed_at_once_is_not_ready():
    # A booting Hermes takes the run and fails it before any event.
    _mock_start([RUN_FAILED])
    result = await hermes.start_run("hi", SESSION)
    assert result == Offline("provider down", connect=True)


@respx.mock
async def test_run_failed_late_is_not_connect(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(hermes, "_now", lambda: clock[0])

    async def late_events(request):
        clock[0] += hermes.AT_ONCE_S
        return _sse([RUN_FAILED])

    respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(202, json={"run_id": RUN_ID, "status": "started", "replayed": False}))
    respx.get(f"{BASE}/v1/runs/{RUN_ID}/events").mock(side_effect=late_events)
    result = await hermes.start_run("hi", SESSION)
    assert result == Offline("provider down", connect=False)


@respx.mock
async def test_run_stream_ends_at_once_is_not_ready():
    _mock_start([])
    _mock_stop()
    assert await hermes.start_run("hi", SESSION) == Offline("stream ended", connect=True)


@pytest.mark.parametrize("status, connect", [(502, True), (503, True), (504, True), (500, False), (401, False)])
@respx.mock
async def test_run_http_status_not_ready(status, connect):
    respx.post(f"{BASE}/v1/runs").mock(return_value=httpx.Response(status, text="Bad Gateway"))
    assert await hermes.start_run("hi", SESSION) == Offline(f"http {status}", connect=connect)


@respx.mock
async def test_run_closed_connection_is_not_ready():
    respx.post(f"{BASE}/v1/runs").mock(
        side_effect=httpx.RemoteProtocolError("Server disconnected without sending a response."))
    result = await hermes.start_run("hi", SESSION)
    assert isinstance(result, Offline) and result.connect is True


@respx.mock
async def test_run_stream_ends_without_result_offline():
    _mock_start(DONE_EVENTS[:2])
    stop = _mock_stop()
    result = await hermes.start_run("hi", SESSION)
    assert result == Offline("stream ended") and result.connect is False
    assert stop.call_count == 1  # the run may still be going on in Hermes


@respx.mock
async def test_run_unauthorized_offline():
    respx.post(f"{BASE}/v1/runs").mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))
    assert await hermes.start_run("hi", SESSION) == Offline("http 401")


@respx.mock
async def test_run_connection_refused_offline():
    respx.post(f"{BASE}/v1/runs").mock(side_effect=httpx.ConnectError("Connection refused"))
    stop = _mock_stop()
    result = await hermes.start_run("hi", SESSION)
    assert not stop.called  # no run to stop
    assert isinstance(result, Offline)
    assert "Connection refused" in result.reason
    assert result.connect is True  # only this kind of failure means no run was started


class _BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"event": "message.delta", "delta": "4"}\n\n'
        raise httpx.ReadError("connection reset")


@respx.mock
async def test_run_read_error_mid_stream_is_not_connect():
    respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(202, json={"run_id": RUN_ID, "status": "started", "replayed": False}))
    respx.get(f"{BASE}/v1/runs/{RUN_ID}/events").mock(
        return_value=httpx.Response(200, stream=_BrokenStream(), headers={"Content-Type": "text/event-stream"}))
    stop = _mock_stop()
    result = await hermes.start_run("hi", SESSION)
    assert isinstance(result, Offline) and result.connect is False
    assert stop.call_count == 1


@respx.mock
async def test_run_timeout_offline(monkeypatch):
    monkeypatch.setattr(hermes, "RUN_TIMEOUT_S", 0.2)

    async def slow_events(request):
        await asyncio.sleep(5)
        return _sse(DONE_EVENTS)

    respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(202, json={"run_id": RUN_ID, "status": "started", "replayed": False}))
    respx.get(f"{BASE}/v1/runs/{RUN_ID}/events").mock(side_effect=slow_events)
    stop = _mock_stop()
    assert await hermes.start_run("hi", SESSION) == Offline("timeout")
    assert stop.call_count == 1  # Hermes must not go on with a run nobody waits for


@respx.mock
async def test_stop_failure_keeps_result(caplog):
    _mock_start([APPROVAL_REQUEST])
    stop = respx.post(f"{BASE}/v1/runs/{RUN_ID}/stop").mock(side_effect=httpx.ConnectError("refused"))
    with caplog.at_level("WARNING", logger="hermestv.hermes"):
        assert await hermes.start_run("hi", SESSION) == NeedsApproval(RUN_ID, REQUEST_ID)
    assert stop.call_count == 1
    assert "was not stopped" in caplog.text


@respx.mock
async def test_stop_failure_log_has_no_url(caplog):
    _mock_start([APPROVAL_REQUEST])
    respx.post(f"{BASE}/v1/runs/{RUN_ID}/stop").mock(return_value=httpx.Response(500))
    with caplog.at_level("WARNING", logger="hermestv.hermes"):
        await hermes.start_run("hi", SESSION)
    assert "500" in caplog.text
    assert "hermes.test" not in caplog.text


def test_run_timeout_is_90s():
    assert hermes.RUN_TIMEOUT_S == 90


@respx.mock
async def test_run_without_key_offline(tmp_path, monkeypatch):
    env = tmp_path / "empty.env"
    env.write_text(f"HERMES_URL={BASE}\nHERMES_KEY=\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    post = _mock_start(DONE_EVENTS)
    assert await hermes.start_run("hi", SESSION) == Offline("HERMES_KEY is not set")
    assert not post.called


@pytest.mark.parametrize("text", ["HERMES_KEY=test-key\n", "HERMES_URL=\nHERMES_KEY=test-key\n"])
async def test_run_without_url_makes_no_request(tmp_path, monkeypatch, text):
    env = tmp_path / "bridge.env"
    env.write_text(text)
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    assert not hermes.enabled()
    with respx.mock(assert_all_called=False) as mock:
        any_request = mock.route()
        assert await hermes.start_run("hi", SESSION) == Offline("HERMES_URL is not set")
        assert await hermes.healthy() is False
    assert any_request.call_count == 0


def test_enabled_with_url():
    assert hermes.enabled()
