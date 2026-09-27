"""Loads ui\\ in headless Edge, the engine WebView2 runs on, with mock.js
standing in for backend\\app.py's Api, and sizes the page like the real
window: 580 px wide and 380 px tall until app.js asks win_fit() for more.

The flow scripts next to this file drive it. They need Playwright, which
requirements-build.lock pins, so run them with .venv-build\\Scripts\\python.exe.
They are not pytest tests, and CI doesn't run them.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
UI = HERE.parent.parent / "ui"
MOCK = (HERE / "mock.js").read_text(encoding="utf-8")
REST_H = 380
SCREEN_H = 1040


def ui_dir() -> Path:
    """The ui\\ folder to test: the first argument, else this checkout's."""
    return Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else UI


def launch(p):
    """Headless Edge: channel "msedge" runs the Edge installed on this PC."""
    return p.chromium.launch(channel="msedge")


class Rig:
    def __init__(self, browser, ui: Path):
        self.browser, self.ui = browser, ui
        self.page = None

    def open(self, width=580, height=REST_H, **mock):
        """A fresh page with window.__mock set up from the keyword arguments
        (see the defaults in mock.js)."""
        if self.page:
            self.page.context.close()
        ctx = self.browser.new_context(
            viewport={"width": width, "height": height}, color_scheme="dark")
        page = ctx.new_page()
        page.on("pageerror", lambda e: print("  PAGE ERROR:", e, file=sys.stderr))
        page.on("console", lambda m: m.type in ("error", "warning") and print(
            f"  console.{m.type}: {m.text}", file=sys.stderr))
        page.add_init_script(
            script="window.__mockInit = %s;\n%s" % (json.dumps(mock), MOCK))
        self.rest = height
        self.width = width
        page.goto((self.ui / "index.html").as_uri())
        page.wait_for_function("window.__mock && window.__mock.readyFired")
        self.page = page
        self.settle(400)
        return page

    def fit(self):
        """Apply the last win_fit() the way backend\\app.py's win_fit does."""
        need = self.page.evaluate("window.__mock.fitNeed")
        h = REST_H if need is None else min(max(math.ceil(need), self.rest),
                                             SCREEN_H)
        if h != self.page.viewport_size["height"]:
            self.page.set_viewport_size({"width": self.width, "height": h})

    def settle(self, ms=900):
        self.page.wait_for_timeout(ms // 2)
        self.fit()
        self.page.wait_for_timeout(ms - ms // 2)
        self.fit()

    def js(self, code, *args):
        return self.page.evaluate(code, *args)

    def click(self, sel, ms=900):
        self.page.click(sel)
        self.settle(ms)

    def progress(self, **p):
        self.js("p => window.__mock.progress(p)", p)
