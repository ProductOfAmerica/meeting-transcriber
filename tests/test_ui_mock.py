"""tests\\ui\\mock.js stands in for backend.app.Api when the UI flows run
(tests\\ui\\rig.py). CI doesn't run those flows, so this checks the mock
against Api here: a change to Api's methods that the mock misses fails."""
import inspect
import re
from pathlib import Path

from backend import app

MOCK = (Path(__file__).parent / "ui" / "mock.js").read_text(encoding="utf-8")


def _arity() -> dict:
    block = re.search(r"const ARITY = \{(.*?)\};", MOCK, re.S)
    assert block, "mock.js has no ARITY table"
    return {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", block.group(1))}


def test_mock_takes_the_arguments_api_takes():
    # set_window is for Python; the page never calls it.
    api = {name: len(inspect.signature(fn).parameters) - 1
           for name, fn in inspect.getmembers(app.Api, inspect.isfunction)
           if not name.startswith("_") and name != "set_window"}
    assert _arity() == api


def test_mock_defines_every_method_it_lists():
    assert set(re.findall(r"^    async (\w+)\(", MOCK, re.M)) == set(_arity())
