import pytest

from hermestv import hermes, history, picks, requests, scenery, schedule, watchlog


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    # No test may read or write the real history, hidden list or caches of the running bridge.
    state = tmp_path / "state"
    monkeypatch.setattr(history, "STATE_DIR", state)
    monkeypatch.setattr(picks, "STATE_DIR", state)
    monkeypatch.setattr(scenery, "DIR", state / "scenery")
    # Nor its settings: a test that needs settings writes its own env file.
    env = tmp_path / "default.env"
    env.write_text("")
    monkeypatch.setenv("HERMESTV_ENV_FILE", str(env))
    hermes._health = None
    schedule._reset()
    watchlog._reset()
    requests._reset()
    yield
    hermes._health = None
    schedule._reset()
    watchlog._reset()
    requests._reset()
