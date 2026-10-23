import asyncio
import logging

import pytest

from hermestv import exchange

pytestmark = pytest.mark.anyio

SCRIPT = 'mkdir -p "$(dirname "$1")" && cat > "$1.tmp" && mv "$1.tmp" "$1"'
OWNER_ARGV = ("docker", "exec", "hermes-test", "stat", "-c", "%u:%g", "/opt/data/")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    env = tmp_path / "bridge.env"
    env.write_text("HERMES_CONTAINER=hermes-test\n")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    monkeypatch.setattr(exchange, "_logged", {})


class _Stdout:
    def __init__(self, data: bytes):
        self.data = data

    async def read(self, n: int) -> bytes:
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk


class _Stdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class Docker:
    """Stands in for asyncio.create_subprocess_exec: no test may run the real docker.

    Each call prints the next of `outs`; the last one repeats."""

    def __init__(self, monkeypatch, *outs: bytes, code: int = 0):
        self.outs, self.code = list(outs) or [b""], code
        self.calls: list[tuple] = []
        self.stdin: _Stdin | None = None
        monkeypatch.setattr(asyncio, "create_subprocess_exec", self)

    async def __call__(self, *argv, stdin=None, stdout=None, stderr=None):
        self.calls.append(argv)
        out = self.outs.pop(0) if len(self.outs) > 1 else self.outs[0]
        self.stdin = _Stdin() if stdin == asyncio.subprocess.PIPE else None
        proc = type("Proc", (), {})()
        proc.stdin, proc.stdout, proc.returncode = self.stdin, _Stdout(out), None

        async def wait():
            proc.returncode = self.code
            return self.code

        proc.wait, proc.kill = wait, lambda: None
        return proc


async def test_safe_path_rejects_dotdot_and_outside(monkeypatch):
    assert exchange.safe_path("/opt/data/hermes-tv", "watch.json") == "/opt/data/hermes-tv/watch.json"
    assert exchange.safe_path("/opt/data/notes/TV/", "Watch Log.md") == "/opt/data/notes/TV/Watch Log.md"
    for d, name in [("/opt/data/../etc", "x.json"), ("/opt/data/hermes-tv/..", "x.json"), ("/etc/tv", "x.json"),
                    ("/opt/datax/tv", "x.json"), ("opt/data/tv", "x.json"), ("", "x.json"),
                    ("/opt/data/hermes-tv", ".."), ("/opt/data/hermes-tv", "../x.json"),
                    ("/opt/data/hermes-tv", "a/b.json"), ("/opt/data/hermes-tv", "x;rm.json"),
                    ("/opt/data/hermes-tv", "x.json\n"), ("/opt/data/hermes-tv", "")]:
        with pytest.raises(ValueError):
            exchange.safe_path(d, name)
    # A bad path never reaches docker.
    docker = Docker(monkeypatch, b"secret")
    assert await exchange.read("/etc", "passwd", 1024) is None
    assert await exchange.write("/opt/data/../etc", "x.json", b"{}") is False
    assert docker.calls == []


async def test_read_uses_fixed_argv(monkeypatch):
    docker = Docker(monkeypatch, b'{"items": []}')
    assert await exchange.read("/opt/data/hermes-tv", "requests.json", 64 * 1024) == b'{"items": []}'
    assert docker.calls == [("docker", "exec", "hermes-test", "cat", "/opt/data/hermes-tv/requests.json")]


async def test_read_over_limit_returns_none(monkeypatch):
    Docker(monkeypatch, b"x" * 100)
    assert await exchange.read("/opt/data/hermes-tv", "requests.json", 99) is None


async def test_read_missing_returns_none(monkeypatch):
    Docker(monkeypatch, b"", code=1)  # cat: No such file or directory
    assert await exchange.read("/opt/data/hermes-tv", "requests.json", 1024) is None


async def test_write_sends_stdin_and_fixed_argv(monkeypatch):
    docker = Docker(monkeypatch, b"1000:1001\n", b"")
    assert await exchange.write("/opt/data/hermes-tv", "watch.json", b'{"opens": []}') is True
    # The write runs as the owner of /opt/data/, so Hermes, not root, owns the new folder and file.
    assert docker.calls == [OWNER_ARGV, ("docker", "exec", "-i", "-u", "1000:1001", "hermes-test", "sh", "-c",
                                         SCRIPT, "_", "/opt/data/hermes-tv/watch.json")]
    assert docker.stdin.data == b'{"opens": []}'
    assert docker.stdin.closed  # cat ends only at end of input


@pytest.mark.parametrize("owner", [b"", b"root:root\n", b"1000\n", b"1000:\n", b"-1:0\n", b"0:0 -e X=1\n",
                                   b"0:0\n--privileged\n", b"1000:1000\n\n", "١:١".encode()])
async def test_write_needs_a_numeric_owner(monkeypatch, caplog, owner):
    docker = Docker(monkeypatch, owner, b"")
    assert await exchange.write("/opt/data/hermes-tv", "watch.json", b"{}") is False
    # Only digits:digits reach "-u": anything else could be read as another docker option or a user name.
    assert docker.calls == [OWNER_ARGV]
    assert "the owner of /opt/data/ is not uid:gid" in caplog.text


async def test_write_failure_returns_false(monkeypatch):
    docker = Docker(monkeypatch, b"0:0\n", b"", code=1)
    assert await exchange.write("/opt/data/hermes-tv", "watch.json", b"{}") is False
    assert docker.calls == [OWNER_ARGV]  # the owner read failed: no write as root instead


async def test_docker_missing_returns_none_and_false(monkeypatch, caplog):
    async def missing(*argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)
    clock = [1000.0]
    monkeypatch.setattr(exchange, "_now", lambda: clock[0])
    caplog.set_level(logging.WARNING, logger="hermestv.exchange")

    assert await exchange.read("/opt/data/hermes-tv", "requests.json", 1024) is None
    assert await exchange.read("/opt/data/hermes-tv", "requests.json", 1024) is None
    assert await exchange.write("/opt/data/hermes-tv", "watch.json", b"{}") is False
    assert await exchange.write("/opt/data/hermes-tv", "watch.json", b"{}") is False
    # A stopped container or a missing docker is logged once per 10 min, not on every TV request.
    assert len(caplog.records) == 2
    assert "No such file" not in caplog.text
    clock[0] += 601
    assert await exchange.read("/opt/data/hermes-tv", "requests.json", 1024) is None
    assert len(caplog.records) == 3


async def test_bad_container_name_makes_no_docker_call(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="hermestv.exchange")
    for name in ("-e", "--privileged", "a b", "../hermes", "hermes;rm", "_hermes", ".hermes"):
        monkeypatch.setattr(exchange, "_logged", {})
        caplog.clear()
        (tmp_path / "bridge.env").write_text(f"HERMES_CONTAINER={name}\n")
        docker = Docker(monkeypatch, b"0:0\n")
        # A name that docker could read as an option or a second word never reaches the argv.
        assert await exchange.read("/opt/data/hermes-tv", "requests.json", 1024) is None
        assert await exchange.read("/opt/data/hermes-tv", "requests.json", 1024) is None
        assert await exchange.write("/opt/data/hermes-tv", "watch.json", b"{}") is False
        assert docker.calls == [], name
        assert [r.getMessage() for r in caplog.records].count(
            "exchange: read requests.json failed: _Failed (HERMES_CONTAINER is not a container name)") == 1
    (tmp_path / "bridge.env").write_text("HERMES_CONTAINER=hermes_2.test-a\n")
    assert exchange.container() == "hermes_2.test-a"
