"""pywebview shell. Thin: all logic lives in the unit-tested modules."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import webview

from . import appapi, firstrun, pipeline, procs
from .writeprobe import probe_writable, ProbeError

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    # Thin launcher. The real install lives in a fixed per-user home,
    # built on first run (backend.firstrun). The exe can sit anywhere
    # (Downloads is fine): nothing is written next to it. ui/, backend/
    # and requirements.lock are bundled under _MEIPASS.
    ROOT = firstrun.app_home()
    BUNDLE = Path(sys._MEIPASS)
    UI = BUNDLE / "ui"
    try:
        LAYOUT = firstrun.Layout(ROOT, BUNDLE)
    except OSError:                 # broken build: env_status reports it
        LAYOUT = None
    VENV = LAYOUT.env if LAYOUT else ROOT / "envs" / "missing"
    CODE = LAYOUT.code if LAYOUT else ROOT / "code" / "missing"
    MODELS = ROOT / "models"
    FFMPEG = LAYOUT.ffmpeg_exe if LAYOUT else None
else:
    ROOT = Path(__file__).resolve().parent.parent
    BUNDLE = ROOT
    UI = ROOT / "ui"
    LAYOUT = None
    VENV = ROOT / "venv"
    CODE = ROOT
    MODELS = ROOT / "models"
    FFMPEG = None                   # from source: ffmpeg on PATH
LOGS = ROOT / "logs"
SETTINGS = ROOT / "settings.json"
OUT_DIR = Path.home() / "Transcripts"   # default output (no folder dialog)


def _ffmpeg():
    return FFMPEG if FFMPEG is not None else shutil.which("ffmpeg")


class Api:
    def __init__(self):
        self._settings = appapi.load_settings(SETTINGS)
        self._jobs = procs.Supervisor()     # setup and transcription
        self._window = None
        self._maxed = False
        self._fit_h = None      # window height win_fit last set
        self._rest_h = None     # height win_fit shrinks back to

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

    def env_status(self):
        # Dev/source runs use the in-tree venv; never show the installer
        # (run_job's preflight still backstops a missing dev venv).
        if not FROZEN:
            return {"ready": True, "home": str(ROOT)}
        if LAYOUT is None:
            return {"ready": False, "home": str(ROOT),
                    "reason": "bundled requirements.lock missing"}
        return {"ready": LAYOUT.ready(), "home": str(ROOT)}

    def bootstrap_env(self):
        # Begin the job before anything the user could cancel.
        self._jobs.begin()

        def worker():
            sink = procs.LineSink(procs.new_log_path(LOGS, "setup"))

            def emit(phase, pct, msg):
                if msg:
                    sink.note(f"[{phase}] {msg}")
                self._emit("progress", {"mode": "setup", "stage": phase,
                                        "pct": pct, "msg": msg})

            def failed(msg):
                self._emit("progress", {
                    "mode": "setup", "stage": "failed",
                    "msg": f"{msg}\n\nSetup log: {sink.path}"})
            try:
                if LAYOUT is None:
                    raise firstrun.SetupError(
                        "This build of Transcribe is broken (the bundled "
                        "requirements.lock is missing).")
                firstrun.bootstrap(LAYOUT, emit=emit, supervisor=self._jobs,
                                   sink=sink)
                self._emit("progress", {"mode": "setup", "stage": "ready"})
            except procs.Cancelled:
                failed("Cancelled.")
            except firstrun.SetupError as exc:
                sink.note(f"setup failed: {exc}")
                failed(str(exc))
            except Exception as exc:
                sink.note(f"setup failed: {exc!r}")
                failed(f"{exc.__class__.__name__}: {exc}\n\nLast output:\n"
                       + (sink.tail() or "(none)"))
            finally:
                sink.close()
                self._jobs.end()

        threading.Thread(target=worker, daemon=True).start()

    def cancel(self):
        self._jobs.cancel()

    def start(self, opts):
        # Begin the job before anything the user could cancel.
        self._jobs.begin()
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
                stats = pipeline.run_job(
                    mode=det["mode"], audio=det["audio"], out_dir=out_dir,
                    venv_dir=VENV, code_dir=CODE, models_root=MODELS,
                    ffmpeg=_ffmpeg(),
                    supervisor=self._jobs,
                    log_path=procs.new_log_path(LOGS, "run"),
                    progress_cb=lambda stage, line, meta=None: self._emit(
                        "progress", {
                            "stage": stage,
                            "mode": det["mode"],
                            "track": (meta or {}).get("track"),
                            "tracks": (meta or {}).get("tracks"),
                            "name": (meta or {}).get("name"),
                            "pct": (meta or {}).get("pct")}))
                self._emit("done", {
                    "duration": stats["duration"],
                    "speakers": stats["speakers"],
                    "turns": stats["turns"],
                    "approx_tokens": stats["approx_tokens"],
                    "output_path": stats["output_path"]})
            except procs.Cancelled:
                self._emit("error", "Cancelled.")
            except (pipeline.PreflightError, pipeline.RunnerError) as exc:
                self._emit("error", str(exc))
            except Exception as exc:
                self._emit("error", f"{exc.__class__.__name__}: {exc}")
            finally:
                self._jobs.end()

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

    def win_fit(self, need):
        """Fit the window height to the page's height `need` (physical px)."""
        form = self._window.native if self._window is not None else None
        if form is None:
            return
        from System import Action
        from System.Windows.Forms import FormWindowState, Screen

        def fit():
            if form.WindowState != FormWindowState.Normal:
                return
            b = form.Bounds
            if b.Height != self._fit_h:     # launch size, or resized by hand
                self._rest_h = b.Height
            area = Screen.FromControl(form).WorkingArea
            frame = b.Height - form.ClientSize.Height
            h = min(max(int(need) + frame, self._rest_h), area.Height)
            y = max(area.Top, min(b.Y, area.Bottom - h))
            form.SetBounds(b.X, y, b.Width, h)
            self._fit_h = form.Height
        form.Invoke(Action(fit))


def _launch_housekeeping():
    """Frozen only, once setup is complete: make sure this exe's runner code
    is installed (a new exe may carry new code with the same lock), then
    clear out folders from older builds in the background."""
    if not (FROZEN and LAYOUT is not None and LAYOUT.ready()):
        return
    firstrun.ensure_code(LAYOUT)
    threading.Thread(target=firstrun.collect_garbage, args=(LAYOUT,),
                     daemon=True).start()


def main():
    _launch_housekeeping()
    api = Api()
    window = webview.create_window(
        "Transcribe", str(UI / "index.html"), js_api=api,
        width=580, height=380, min_size=(480, 360),
        background_color="#0c0d10",
        frameless=True, easy_drag=False, resizable=True)
    api.set_window(window)
    webview.start()


if __name__ == "__main__":
    main()
