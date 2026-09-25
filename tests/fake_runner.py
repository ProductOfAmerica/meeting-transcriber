"""Stand-in for backend.runner in tests. Speaks the same JSON-line protocol.

usage: python fake_runner.py <mode> <out_dir> [n_inputs]

modes:
  ok      emit a normal job for n inputs and write result files
  mono    one input whose words carry diarized speakers
  slow    like ok, with short pauses between events
  flood   megabytes of stderr noise and non-cp1252 bytes, then ok
  error   emit a classified error event and exit 1
  crash   print a line to stderr and exit 3 with no error event
  hang    write pid.txt and sleep (to be cancelled)
  orphan  start a detached-looking grandchild (child.pid), then exit 0
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WORDS = {1: [{"word": "Um,", "start": 1.0, "end": 1.2},
             {"word": "hello", "start": 1.3, "end": 1.6},
             {"word": "there.", "start": 1.7, "end": 2.0}],
         2: [{"word": "Hi.", "start": 2.5, "end": 2.8}]}


MONO_WORDS = [{"word": "Morning", "start": 0.5, "end": 0.9,
               "speaker": "SPEAKER_00"},
              {"word": "all.", "start": 1.0, "end": 1.2,
               "speaker": "SPEAKER_00"},
              {"word": "Hi.", "start": 2.0, "end": 2.3,
               "speaker": "SPEAKER_01"}]


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def ok(out, n, pause=0.0):
    emit({"ev": "phase", "phase": "load_model"})
    for i in range(1, n + 1):
        emit({"ev": "input", "i": i, "n": n})
        emit({"ev": "phase", "phase": "transcribe"})
        time.sleep(pause)
        emit({"ev": "pct", "phase": "transcribe", "pct": 50.0})
        js = out / f"{i:03d}.json"
        js.write_text(json.dumps({"words": WORDS.get(i, []),
                                  "duration": 3.0}), encoding="utf-8")
        emit({"ev": "result", "i": i, "json": str(js)})
    emit({"ev": "done"})


def main():
    mode, out = sys.argv[1], Path(sys.argv[2])
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    out.mkdir(parents=True, exist_ok=True)
    if mode == "ok":
        ok(out, n)
    elif mode == "mono":
        emit({"ev": "phase", "phase": "load_model"})
        emit({"ev": "input", "i": 1, "n": 1})
        emit({"ev": "phase", "phase": "transcribe"})
        emit({"ev": "phase", "phase": "load_diarize"})
        emit({"ev": "phase", "phase": "diarize"})
        emit({"ev": "pct", "phase": "diarize", "pct": 50.0})
        js = out / "001.json"
        js.write_text(json.dumps({"words": MONO_WORDS, "duration": 3.0}),
                      encoding="utf-8")
        emit({"ev": "result", "i": 1, "json": str(js)})
        emit({"ev": "done"})
    elif mode == "slow":
        ok(out, n, pause=0.2)
    elif mode == "flood":
        noise = "noise line " + "x" * 200 + "\n"
        for _ in range(10000):          # about 2 MB on stderr
            sys.stderr.write(noise)
        sys.stderr.flush()
        sys.stdout.buffer.write(b"\x81\x8d\x90\x9d raw bytes \xd0\x90\n")
        sys.stdout.buffer.write("plain non-protocol line\n".encode())
        sys.stdout.flush()
        ok(out, n)
    elif mode == "error":
        emit({"ev": "error", "code": "oom", "msg": "The GPU ran out of memory."})
        sys.exit(1)
    elif mode == "crash":
        emit({"ev": "phase", "phase": "load_model"})
        sys.stderr.write("boom: something native went wrong\n")
        sys.stderr.flush()
        os._exit(3)
    elif mode == "hang":
        (out / "pid.txt").write_text(str(os.getpid()))
        emit({"ev": "phase", "phase": "load_model"})
        time.sleep(120)
    elif mode == "orphan":
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        (out / "child.pid").write_text(str(child.pid))
        emit({"ev": "done"})
    else:
        sys.exit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
