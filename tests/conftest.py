import pytest


@pytest.fixture(autouse=True)
def _private_dirs(tmp_path_factory, monkeypatch):
    """Never read or write the real ~/.config/ttyl or ~/.local/state/ttyl from tests, and never
    run the real on-device model (tests that need one point TTYL_SUMMARIZE_HELPER at a fake)."""
    base = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.delenv("TTYL_SUMMARIES", raising=False)
    monkeypatch.setenv("TTYL_SUMMARIZE_HELPER", "")
    from ttyl import summarize

    summarize._on_device_cache.clear()
    summarize._claude_paused[:] = [0.0, ""]
