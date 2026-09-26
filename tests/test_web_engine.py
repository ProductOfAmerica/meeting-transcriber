import sys

import pytest

from backend import app


@pytest.mark.skipif(sys.platform != "win32", reason="pywebview's WinForms backend")
def test_pywebview_reports_its_web_engine():
    # main() refuses to start unless this says "edgechromium".
    assert app._web_engine() in {"edgechromium", "mshtml", "cef"}


def test_main_stops_before_any_window_without_webview2(monkeypatch):
    asked = []
    monkeypatch.setattr(app, "_web_engine", lambda: "mshtml")
    monkeypatch.setattr(app, "_ask_for_webview2", lambda: asked.append(True))
    monkeypatch.setattr(app.webview, "create_window",
                        lambda *a, **k: pytest.fail("a window was created"))
    app.main()
    assert asked == [True]
