"""What the setup screen asks the backend: whether setup is needed, and
before it offers Install, whether setup's own first checks (the GPU, its
driver, free space) would stop it, and why."""
from backend import app, firstrun


def _frozen(monkeypatch, gpu=None, space=None):
    monkeypatch.setattr(app, "FROZEN", True)
    monkeypatch.setattr(app, "LAYOUT", object())

    def check(exc):
        def run(*_a, **_k):
            if exc:
                raise exc
        return run
    monkeypatch.setattr(firstrun, "check_gpu", check(gpu))
    monkeypatch.setattr(firstrun, "check_space", check(space))


def test_source_runs_never_see_the_installer():
    api = app.Api()
    assert api.env_status() == {"ready": True}
    assert api.setup_check() == {"reason": None}


def test_a_supported_pc_can_start(monkeypatch):
    _frozen(monkeypatch)
    assert app.Api().setup_check() == {"reason": None}


def test_a_failed_check_answers_with_its_reason(monkeypatch):
    _frozen(monkeypatch, gpu=firstrun.SetupError("No NVIDIA GPU was found."))
    assert app.Api().setup_check() == {"reason": "No NVIDIA GPU was found."}
    _frozen(monkeypatch, space=firstrun.SetupError("The drive is full."))
    assert app.Api().setup_check() == {"reason": "The drive is full."}


def test_an_unexpected_failure_leaves_setup_to_report_it(monkeypatch):
    _frozen(monkeypatch, gpu=PermissionError("denied"))
    assert app.Api().setup_check() == {"reason": None}
