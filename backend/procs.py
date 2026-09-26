"""Child processes: kernel-enforced lifetime, race-free cancel, full drain.

Import-safe: no side effects at import. Every child the app starts for a job
goes through Supervisor.spawn, which puts it in a Windows job object created
with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. The GUI process holds the only handle
to that job, so when the GUI exits for any reason (window closed, crash, Task
Manager) the kernel kills the whole tree: the venv launcher, the interpreter
it starts, and anything that interpreter starts (ffmpeg). The job never sets
BREAKAWAY_OK, so a nested job that allows silent breakaway (the venv
launcher's) cannot move grandchildren out of ours.

Cancel is one transition under one lock: set the flag, terminate the job.
Spawn checks the flag and assigns the new process to the job under the same
lock, and the process starts suspended until it is in the job, so no process
can escape a cancel.
"""
from __future__ import annotations

import collections
import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
_CREATE_SUSPENDED = 0x00000004
_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMIT_INFORMATION = 9


class Cancelled(Exception):
    """The user cancelled the job."""


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def _resume(process_handle) -> None:
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(int(process_handle))
    if status != 0:
        raise OSError(f"NtResumeProcess failed (NTSTATUS {status:#x})")


class Job:
    """A job object whose processes die when it is closed or terminated."""

    def __init__(self):
        self._k32 = _kernel32()
        handle = self._k32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        if not self._k32.SetInformationJobObject(
                handle, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            self._k32.CloseHandle(handle)
            raise ctypes.WinError(err)
        self._handle = handle

    def assign(self, process_handle) -> None:
        if not self._k32.AssignProcessToJobObject(self._handle,
                                                  int(process_handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self) -> None:
        if self._handle:
            self._k32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        """Close the handle; KILL_ON_JOB_CLOSE ends any process still in it."""
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None


class LineSink:
    """Collects non-protocol output: appended to a log file (if given) and
    kept as a short in-memory tail for error messages. Thread-safe."""

    def __init__(self, log_path=None, tail_lines=40):
        self._lock = threading.Lock()
        self._tail = collections.deque(maxlen=tail_lines)
        self._fh = None
        self.path = None if log_path is None else Path(log_path)
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(log_path, "a", encoding="utf-8", errors="replace")

    def note(self, line: str) -> None:
        line = line.replace("\x00", "")
        if not line.strip():
            return
        with self._lock:
            self._tail.append(line)
            if self._fh:
                self._fh.write(line + "\n")
                self._fh.flush()

    def tail(self) -> str:
        with self._lock:
            return "\n".join(self._tail)

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None


def new_log_path(logs_dir, prefix: str, keep: int = 20) -> Path:
    """Path for a new timestamped log. Deletes older logs of the same prefix
    (matched by exact name pattern) so at most `keep` remain."""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(prefix)}-\d{{8}}-\d{{6}}(-\d+)?\.log$")
    old = sorted(e.name for e in os.scandir(logs_dir)
                 if e.is_file() and pattern.match(e.name))
    for name in old[:max(0, len(old) - (keep - 1))]:
        try:
            (logs_dir / name).unlink()
        except OSError:
            pass
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path, n = logs_dir / f"{prefix}-{stamp}.log", 1
    while path.exists():
        path, n = logs_dir / f"{prefix}-{stamp}-{n}.log", n + 1
    return path


def drain(stream, handle_line, sink: LineSink) -> None:
    """Read a text stream to EOF. Never stops early: a pipe nobody reads fills
    up and blocks the child. A failing handler is logged, not fatal."""
    try:
        for raw in stream:
            line = raw.rstrip("\r\n")
            try:
                handle_line(line)
            except Exception as exc:  # keep draining no matter what
                sink.note(f"[line handler failed: {exc!r}] {line}")
    finally:
        stream.close()


class Supervisor:
    """Runs one job at a time: begin(), spawn()/run() any number of children,
    end(). cancel() may be called from any thread at any time."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._job = None

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def begin(self) -> None:
        """Start a new job. Call before any work the user could cancel."""
        with self._lock:
            if self._job is not None:
                self._job.close()
            self._cancelled = False
            self._job = Job()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._job is not None:
                self._job.terminate()

    def end(self) -> None:
        """Finish the job; kills anything still running in it."""
        with self._lock:
            job, self._job = self._job, None
        if job is not None:
            job.close()

    def spawn(self, cmd, *, cwd=None, env=None) -> subprocess.Popen:
        with self._lock:
            if self._cancelled:
                raise Cancelled()
            if self._job is None:
                raise RuntimeError("Supervisor.spawn called outside a job")
            proc = subprocess.Popen(
                [str(c) for c in cmd], cwd=None if cwd is None else str(cwd),
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=CREATE_NO_WINDOW | _CREATE_SUSPENDED)
            try:
                self._job.assign(proc._handle)
                _resume(proc._handle)
            except BaseException:
                proc.kill()
                proc.wait()
                raise
            return proc

    def run(self, cmd, *, cwd=None, env=None, on_stdout=None,
            sink: LineSink) -> int:
        """Spawn, drain both pipes to EOF, wait. Returns the exit code.
        Raises Cancelled if the job was cancelled at any point."""
        proc = self.spawn(cmd, cwd=cwd, env=env)
        readers = [
            threading.Thread(target=drain, args=(
                proc.stdout, on_stdout or sink.note, sink), daemon=True),
            threading.Thread(target=drain, args=(
                proc.stderr, sink.note, sink), daemon=True)]
        for t in readers:
            t.start()
        rc = proc.wait()
        for t in readers:
            t.join(timeout=10)
        if self.cancelled:
            raise Cancelled()
        return rc


if sys.platform != "win32":  # pragma: no cover - the app is Windows-only
    def _unsupported(*_a, **_k):
        raise OSError("backend.procs requires Windows job objects")
    Job.__init__ = _unsupported
