"""pywebview shell. Thin: all logic lives in the unit-tested modules."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import webview

from . import appapi, firstrun, pipeline, updates
from .writeprobe import probe_writable, ProbeError

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    # Thin launcher. The real install lives in a fixed per-user home,
    # built on first run (backend.firstrun). The exe can sit anywhere
    # (Downloads is fine) -- nothing is written next to it. ui/ +
    # backend/ + requirements.txt are bundled under _MEIPASS.
    ROOT = firstrun.app_home()
    BUNDLE = Path(sys._MEIPASS)
    UI = BUNDLE / "ui"
else:
    ROOT = Path(__file__).resolve().parent.parent
    BUNDLE = ROOT
    UI = ROOT / "ui"
VENV = ROOT / "venv"
SETTINGS = ROOT / "settings.json"
OUT_DIR = Path.home() / "Transcripts"   # default output (no folder dialog)


class Api:
    def __init__(self):
        self._settings = appapi.load_settings(SETTINGS)
        self._cancel = threading.Event()
        self._proc = None
        self._window = None
        self._maxed = False

    def set_window(self, window):
        self._window = window

    def _emit(self, channel, payload):
        if self._window is not None:
            # JSON is a valid JS literal for str/int/bool/None/list/dict
            # (Python %r is not: it emits None/True/False). Sinks in app.js
            # are textContent / numeric innerHTML, so this stays XSS-safe.
            self._window.evaluate_js(
                "window.__on(%s, %s)"
                % (json.dumps(channel), json.dumps(payload)))

    def pick_input(self):
        # pywebview's FOLDER dialog is broken on this Windows build, so we
        # use only the (working) OPEN file dialog. detect_input resolves a
        # picked file to per-track vs mono from its folder structure.
        res = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=False)
        if not res:
            return None
        path = Path(res[0])
        info = pipeline.summarize_input(path)
        return {"path": str(path), "name": path.name,
                "mode": info["mode"], "reason": info["reason"],
                "title": info["title"], "speakers": info["speakers"],
                "needs_token": info["needs_token"]}

    def _effective_out_dir(self) -> Path:
        # Default ~/Transcripts; a hand-edited settings.json still wins
        # (escape hatch, not a per-run control).
        saved = (self._settings.get("last_output_dir") or "").strip()
        return Path(saved) if saved else OUT_DIR

    def update_banner(self):
        latest = updates.check_whisperx_update(
            enabled=bool(self._settings.get("update_check_enabled", True)))
        return latest  # version string or None

    def env_status(self):
        # Dev/source runs use the in-tree venv; never show the installer
        # (the _run_one guard still backstops a missing dev venv).
        if not FROZEN:
            return {"ready": True, "home": str(ROOT)}
        try:
            want = firstrun.requirements_hash(
                (BUNDLE / "requirements.txt").read_bytes())
        except OSError:
            return {"ready": False, "home": str(ROOT),
                    "reason": "bundled requirements.txt missing"}
        return {"ready": firstrun.is_ready(ROOT, want),
                "home": str(ROOT)}

    def bootstrap_env(self):
        self._cancel.clear()

        def worker():
            def emit(phase, pct, msg):
                self._emit("progress", {"mode": "setup", "stage": phase,
                                        "pct": pct, "msg": msg})
            try:
                want = firstrun.requirements_hash(
                    (BUNDLE / "requirements.txt").read_bytes())
                firstrun.bootstrap(
                    ROOT, BUNDLE, want, emit=emit,
                    cancel_event=self._cancel,
                    register_proc=self._register,
                    no_window=_NO_WINDOW,
                    env_builder=pipeline.build_env)
                self._emit("progress", {"mode": "setup", "stage": "ready"})
            except Exception as exc:
                self._emit("progress", {
                    "mode": "setup", "stage": "failed",
                    "msg": f"{exc.__class__.__name__}: {exc}"})

        threading.Thread(target=worker, daemon=True).start()

    def _register(self, proc):
        self._proc = proc

    def cancel(self):
        self._cancel.set()
        proc = self._proc
        if proc and proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def start(self, opts):
        self._cancel.clear()
        path = Path(opts["path"])
        out_dir = self._effective_out_dir()
        det = pipeline.detect_input(path)

        def worker():
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                probe_writable(out_dir)
                if det["mode"] == "ask":
                    raise RuntimeError(
                        "Could not tell if this is a single mixed file or a "
                        "per-participant recording. Pick the mixed audio "
                        "file, or a track inside the Audio Record folder.")
                common = dict(
                    out_dir=out_dir, venv_dir=VENV,
                    progress_cb=lambda stage, line, meta=None: self._emit(
                        "progress", {
                            "stage": stage,
                            "mode": det["mode"],
                            "track": (meta or {}).get("track"),
                            "tracks": (meta or {}).get("tracks"),
                            "name": (meta or {}).get("name"),
                            "pct": (meta or {}).get("pct")}),
                    cancel_event=self._cancel,
                    register_proc=self._register)
                if det["mode"] == "pertrack":
                    stats = pipeline.run_pertrack(
                        audio=det["audio"], **common)
                else:
                    stats = pipeline.run_mono(
                        audio=det["audio"][0], **common)
                self._emit("done", {
                    "duration": stats["duration"],
                    "speakers": stats["speakers"],
                    "turns": stats["turns"],
                    "approx_tokens": stats["approx_tokens"],
                    "output_path": stats["output_path"]})
            except Exception as exc:
                self._emit("error", f"{exc.__class__.__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def copy_transcript(self, output_path):
        try:
            text = Path(output_path).read_text(encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        # webview clipboard via JS; return text for the page to copy
        return {"ok": True, "text": text}

    def open_folder(self, output_path):
        folder = str(Path(output_path).parent)
        subprocess.run(["explorer", folder])
        return {"ok": True}

    # --- frameless window controls (custom title bar) ---
    def win_minimize(self):
        if self._window is not None:
            self._window.minimize()

    def win_toggle_max(self):
        if self._window is None:
            return False
        if self._maxed:
            self._window.restore()
        else:
            self._window.maximize()
        self._maxed = not self._maxed
        return self._maxed

    def win_close(self):
        if self._window is not None:
            self._window.destroy()

    def win_size(self):
        w = self._window
        if w is None:
            return {"w": 560, "h": 720}
        return {"w": int(w.width), "h": int(w.height)}

    def win_resize(self, w, h):
        if self._window is not None:
            self._window.resize(int(w), int(h))


def main():
    api = Api()
    window = webview.create_window(
        "Transcribe", str(UI / "index.html"), js_api=api,
        width=580, height=380, background_color="#0c0d10",
        frameless=True, easy_drag=False, resizable=True)
    api.set_window(window)
    webview.start()


if __name__ == "__main__":
    main()
