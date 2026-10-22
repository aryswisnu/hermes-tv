"""No test may touch the live bridge's state: every module-level path of hermestv that points into
bridge/state must be moved to tmp_path by the autouse fixture in conftest.py."""
import importlib
import pkgutil
from pathlib import Path

import hermestv

LIVE = (Path(hermestv.__file__).resolve().parent.parent / "state").resolve()


def _module_paths() -> list[tuple[str, Path]]:
    out = []
    for info in pkgutil.iter_modules(hermestv.__path__):
        module = importlib.import_module(f"hermestv.{info.name}")
        out += [(f"{info.name}.{name}", value) for name, value in vars(module).items() if isinstance(value, Path)]
    return out


def test_no_module_path_points_at_live_state():
    paths = _module_paths()
    assert any(name == "picks.STATE_DIR" for name, _ in paths)  # the walk really sees the modules
    for name, path in paths:
        resolved = path.resolve()
        assert resolved != LIVE and LIVE not in resolved.parents, name
