from backend import updates as U


def test_newer_returns_version(monkeypatch):
    monkeypatch.setattr(U, "_installed", lambda: "3.8.5")
    monkeypatch.setattr(U, "_latest", lambda timeout: "3.9.0")
    assert U.check_whisperx_update(enabled=True) == "3.9.0"


def test_equal_returns_none(monkeypatch):
    monkeypatch.setattr(U, "_installed", lambda: "3.8.5")
    monkeypatch.setattr(U, "_latest", lambda timeout: "3.8.5")
    assert U.check_whisperx_update(enabled=True) is None


def test_older_returns_none(monkeypatch):
    monkeypatch.setattr(U, "_installed", lambda: "3.9.0")
    monkeypatch.setattr(U, "_latest", lambda timeout: "3.8.5")
    assert U.check_whisperx_update(enabled=True) is None


def test_disabled_skips(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not be called")
    monkeypatch.setattr(U, "_latest", boom)
    assert U.check_whisperx_update(enabled=False) is None


def test_network_error_is_silent(monkeypatch):
    monkeypatch.setattr(U, "_installed", lambda: "3.8.5")
    def boom(timeout):
        raise OSError("offline")
    monkeypatch.setattr(U, "_latest", boom)
    assert U.check_whisperx_update(enabled=True) is None
