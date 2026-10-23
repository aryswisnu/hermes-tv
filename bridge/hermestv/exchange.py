"""Files shared with the Hermes container, through docker exec with fixed argv; content goes through stdin and
stdout only."""
import asyncio
import logging
import re
import time
from collections.abc import Sequence

from hermestv import config
from hermestv.errors import brief

ROOT = "/opt/data/"
NAME = re.compile(r"[A-Za-z0-9 ._-]+")
# A docker container name; anything else could be read as a docker option.
CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
OWNER = re.compile(rb"([0-9]+:[0-9]+)\n?")
READ_TIMEOUT_S = 10.0
WRITE_TIMEOUT_S = 10.0
# Write to a temp file, then rename: Hermes never reads a half-written file.
WRITE_SCRIPT = 'mkdir -p "$(dirname "$1")" && cat > "$1.tmp" && mv "$1.tmp" "$1"'
LOG_EVERY_S = 10 * 60

log = logging.getLogger(__name__)

_now = time.monotonic
_logged: dict[str, float] = {}  # "read <name>" or "write <name>" -> when last logged


class _Failed(Exception):
    """A failure with a fixed reason, safe to log."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


async def _exec(cmd: Sequence[str], timeout: float, limit: int, stdin: bytes | None = None) -> bytes:
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=None if stdin is None else asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    data = b""
    try:
        async with asyncio.timeout(timeout):
            if stdin is not None:
                proc.stdin.write(stdin)
                await proc.stdin.drain()
                proc.stdin.close()
            while chunk := await proc.stdout.read(65536):
                data += chunk
                if len(data) > limit:
                    raise _Failed("too much output")
            await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        raise _Failed("exit code is not 0")
    return data


def safe_path(dir: str, name: str) -> str:
    """The file's path in the container; ValueError unless it is a plain name in a folder under /opt/data/."""
    if not dir.startswith(ROOT) or ".." in dir or not NAME.fullmatch(name) or ".." in name:
        raise ValueError("not a path under /opt/data/")
    return f"{dir.rstrip('/')}/{name}"


def container() -> str:
    """HERMES_CONTAINER; _Failed unless it is a plain name."""
    name = config.load().hermes_container
    if not CONTAINER.fullmatch(name):
        raise _Failed("HERMES_CONTAINER is not a container name")
    return name


async def _owner(name: str) -> str:
    """The uid:gid that owns /opt/data/ in the container, read on each write: a new container can have a new one."""
    out = await _exec(("docker", "exec", name, "stat", "-c", "%u:%g", ROOT), READ_TIMEOUT_S, 64)
    if not (m := OWNER.fullmatch(out)):
        raise _Failed("the owner of /opt/data/ is not uid:gid")
    return m[1].decode()


def _warn(what: str, e: Exception) -> None:
    if what in _logged and _now() - _logged[what] < LOG_EVERY_S:
        return
    _logged[what] = _now()
    # Only fixed reasons: an exception text could hold file content.
    log.warning("exchange: %s failed: %s (%s)", what, brief(e),
                e.reason if isinstance(e, _Failed) else "unexpected error")


async def read(dir: str, name: str, limit: int) -> bytes | None:
    """The file's bytes from the Hermes container, or None if it is missing or cannot be read."""
    try:
        cmd = ("docker", "exec", container(), "cat", safe_path(dir, name))
        return await _exec(cmd, READ_TIMEOUT_S, limit)
    except Exception as e:  # The exchange is optional: any failure means "no file".
        _warn(f"read {name}", e)
        return None


async def write(dir: str, name: str, data: bytes) -> bool:
    """Replace the file in the Hermes container with data; False on any failure."""
    try:
        path, target = safe_path(dir, name), container()
        # As the owner of /opt/data/, not root: Hermes runs as that user and must be able to change the files.
        cmd = ("docker", "exec", "-i", "-u", await _owner(target), target, "sh", "-c", WRITE_SCRIPT, "_", path)
        await _exec(cmd, WRITE_TIMEOUT_S, 0, stdin=data)
        return True
    except Exception as e:  # The caller keeps its data and tries again later.
        _warn(f"write {name}", e)
        return False
