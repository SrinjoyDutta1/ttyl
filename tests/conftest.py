import pytest


@pytest.fixture(autouse=True)
def _private_dirs(tmp_path_factory, monkeypatch):
    """Never read or write the real ~/.config/ttyl or ~/.local/state/ttyl from tests."""
    base = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.delenv("TTYL_SUMMARIES", raising=False)
