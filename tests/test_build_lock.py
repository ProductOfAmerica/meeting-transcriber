import re
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / "requirements-build.lock"


def test_build_lock_setuptools_builds_wheels_itself():
    # build.cmd installs this pin first, then builds the sdist-only proxy-tools
    # without isolation; setuptools only builds wheels on its own from 70.1 on.
    m = re.search(r"^setuptools==(\d+)\.(\d+)", LOCK.read_text(encoding="utf-8"),
                  re.M)
    assert m and (int(m.group(1)), int(m.group(2))) >= (70, 1)
