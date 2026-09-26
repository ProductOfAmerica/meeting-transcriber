"""backend.procs: job-object lifetime, race-free cancel, full drain.

Drives tests/fake_runner.py through the real Supervisor (Windows only).
"""
import ctypes
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from backend import procs

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="job objects are Windows-only")

FAKE = str(Path(__file__).with_name("fake_runner.py"))


def _alive(pid: int) -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE|QUERY
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(ctypes.c_void_p(h), 0) == 0x102
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def _wait_for(path: Path, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def _gone(pid, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_run_ok_collects_protocol_lines(tmp_path):
    sup, lines = procs.Supervisor(), []
    sup.begin()
    try:
        rc = sup.run([sys.executable, FAKE, "ok", tmp_path, 2],
                     on_stdout=lines.append, sink=procs.LineSink())
    finally:
        sup.end()
    assert rc == 0
    evs = [json.loads(x)["ev"] for x in lines]
    assert evs.count("result") == 2 and evs[-1] == "done"


def test_cancel_before_spawn_starts_nothing(tmp_path):
    sup = procs.Supervisor()
    sup.begin()
    sup.cancel()
    with pytest.raises(procs.Cancelled):
        sup.spawn([sys.executable, FAKE, "hang", tmp_path])
    sup.end()
    assert not (tmp_path / "pid.txt").exists()


def test_spawn_outside_job_is_refused(tmp_path):
    with pytest.raises(RuntimeError):
        procs.Supervisor().spawn([sys.executable, FAKE, "ok", tmp_path])


def test_cancel_mid_run_kills_and_raises(tmp_path):
    sup, outcome = procs.Supervisor(), {}
    sup.begin()

    def work():
        try:
            sup.run([sys.executable, FAKE, "hang", tmp_path],
                    sink=procs.LineSink())
        except procs.Cancelled:
            outcome["cancelled"] = True

    t = threading.Thread(target=work)
    t.start()
    pid = _wait_for(tmp_path / "pid.txt")
    t0 = time.time()
    sup.cancel()
    t.join(15)
    sup.end()
    assert outcome.get("cancelled")
    assert time.time() - t0 < 10
    assert _gone(pid)


def test_end_kills_leftover_grandchild(tmp_path):
    sup = procs.Supervisor()
    sup.begin()
    rc = sup.run([sys.executable, FAKE, "orphan", tmp_path],
                 sink=procs.LineSink())
    child = _wait_for(tmp_path / "child.pid")
    assert rc == 0 and _alive(child)
    sup.end()                       # KILL_ON_JOB_CLOSE
    assert _gone(child)


def test_flood_and_bad_bytes_do_not_deadlock(tmp_path):
    sup, lines = procs.Supervisor(), []
    sink = procs.LineSink(tmp_path / "run.log")
    sup.begin()
    t0 = time.time()
    try:
        rc = sup.run([sys.executable, FAKE, "flood", tmp_path],
                     on_stdout=lines.append, sink=sink)
    finally:
        sup.end()
        sink.close()
    assert rc == 0 and time.time() - t0 < 60
    assert any('"done"' in x for x in lines)
    assert (tmp_path / "run.log").stat().st_size > 1_000_000


def test_crash_leaves_tail_for_the_error(tmp_path):
    sup, sink = procs.Supervisor(), procs.LineSink()
    sup.begin()
    try:
        rc = sup.run([sys.executable, FAKE, "crash", tmp_path], sink=sink)
    finally:
        sup.end()
    assert rc == 3
    assert "boom" in sink.tail()


def test_linesink_drops_nul_and_blank_lines(tmp_path):
    sink = procs.LineSink(tmp_path / "x.log")
    sink.note("a\x00b")
    sink.note("   ")
    sink.close()
    assert sink.tail() == "ab"
    assert (tmp_path / "x.log").read_text(encoding="utf-8") == "ab\n"


def test_new_log_path_keeps_newest_and_ignores_other_files(tmp_path):
    for k in range(25):
        (tmp_path / f"run-20260101-0000{k:02d}.log").write_text("x")
    (tmp_path / "setup-20260101-000000.log").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    p = procs.new_log_path(tmp_path, "run", keep=20)
    runs = sorted(x.name for x in tmp_path.glob("run-*.log"))
    assert len(runs) == 19 and runs[-1] == "run-20260101-000024.log"
    assert (tmp_path / "setup-20260101-000000.log").exists()
    assert (tmp_path / "notes.txt").exists()
    assert p.name.startswith("run-") and not p.exists()
