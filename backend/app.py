"""pywebview shell. Thin: all logic lives in the unit-tested modules."""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import threading
from pathlib import Path

import webview

from . import appapi, firstrun, hftoken, models, pipeline, procs
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
    # python -m backend.devsetup installs the pinned ffmpeg here; else PATH.
    _pinned = firstrun.ffmpeg_dir(ROOT) / "ffmpeg.exe"
    FFMPEG = _pinned if _pinned.exists() else None
LOGS = ROOT / "logs"
HF_HOME = ROOT / "hf"                   # speaker-model downloads, in the home
SETTINGS = ROOT / "settings.json"
OUT_DIR = Path.home() / "Transcripts"   # default output (no folder dialog)
WEBVIEW2_URL = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"
# The only pages the UI may open.
LINKS = {"hf_terms": f"https://huggingface.co/{models.DIARIZATION_MODEL}",
         "hf_tokens": "https://huggingface.co/settings/tokens"}
# The picker lists recordings; "All files" stays for anything else.
PICKER_TYPES = ("Recordings ({})".format(
    ";".join(f"*{ext}" for ext in sorted(pipeline.MEDIA_EXTS))),
    "All files (*.*)")


def _ffmpeg():
    return FFMPEG if FFMPEG is not None else shutil.which("ffmpeg")


class Api:
    def __init__(self):
        self._settings = appapi.load_settings(SETTINGS)
        self._jobs = procs.Supervisor()     # setup and transcription
        self._window = None
        self._fit_h = None      # window height win_fit last set
        self._rest_h = None     # height win_fit shrinks back to
        # Paths stay here: the page names no file for the backend to use.
        self._chosen = None         # the recording pick_input returned
        self._last_output = None    # the transcript the last job wrote

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
            webview.FileDialog.OPEN, allow_multiple=False,
            file_types=PICKER_TYPES)
        if not res:
            return None
        path = Path(res[0])
        info = pipeline.summarize_input(path)
        self._chosen = path
        return {"name": path.name,
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
                sink.note("setup cancelled")
                self._emit("cancelled", "setup")
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

    def start(self):
        path = self._chosen
        if path is None:
            return
        # Begin the job before anything the user could cancel.
        self._jobs.begin()
        out_dir = self._effective_out_dir()
        det = pipeline.detect_input(path)

        def worker():
            log_path = None

            def failed(msg):
                # The run writes its log only once the speech engine starts.
                self._emit("error", f"{msg}\n\nLog: {log_path}"
                           if log_path and log_path.exists() else msg)
            try:
                log_path = procs.new_log_path(LOGS, "run")
                out_dir.mkdir(parents=True, exist_ok=True)
                probe_writable(out_dir)
                if det["mode"] == "ask":
                    raise pipeline.PreflightError(det["reason"])
                token, _source = hftoken.resolve()
                stats = pipeline.run_job(
                    mode=det["mode"], audio=det["audio"], out_dir=out_dir,
                    venv_dir=VENV, code_dir=CODE, models_root=MODELS,
                    ffmpeg=_ffmpeg(), hf_token=token, hf_home=HF_HOME,
                    supervisor=self._jobs, log_path=log_path,
                    progress_cb=lambda stage, line, meta=None: self._emit(
                        "progress", {
                            "stage": stage,
                            "mode": det["mode"],
                            "track": (meta or {}).get("track"),
                            "tracks": (meta or {}).get("tracks"),
                            "name": (meta or {}).get("name"),
                            "pct": (meta or {}).get("pct")}))
                self._last_output = Path(stats["output_path"])
                self._emit("done", {
                    "duration": stats["duration"],
                    "speakers": stats["speakers"],
                    "turns": stats["turns"],
                    "approx_tokens": stats["approx_tokens"],
                    "output_path": stats["output_path"]})
            except procs.Cancelled:
                self._emit("cancelled", "transcribe")
            except ProbeError as exc:
                # There is no folder picker; settings.json is the way out.
                failed(f"{exc}\n\nTo save transcripts somewhere else, put "
                       f'{{"last_output_dir": "D:\\\\Transcripts"}} in '
                       f"{SETTINGS}, then start Transcribe again.")
            except (pipeline.PreflightError, pipeline.RunnerError) as exc:
                failed(str(exc))
            except Exception as exc:
                failed(f"{exc.__class__.__name__}: {exc}")
            finally:
                self._jobs.end()

        threading.Thread(target=worker, daemon=True).start()

    # --- Hugging Face token (mixed recordings). The JS bridge can reach any
    # attribute of this object, so the token is never stored on it; errors
    # come back as fixed status words (see backend.hftoken).
    def hf_token_status(self):
        try:
            _token, source = hftoken.resolve()
        except Exception:
            source = None
        return {"source": source}

    def hf_token_save(self, raw):
        try:
            token = hftoken.normalize(raw)
            if token is None:
                return {"ok": False, "status": "format"}
            status = hftoken.check(token, models.DIARIZATION_MODEL)
            if status == "invalid":
                return {"ok": False, "status": "invalid"}
            if not hftoken.save(token):
                return {"ok": False, "status": "store"}
            return {"ok": True, "status": status}
        except Exception:
            return {"ok": False, "status": "error"}

    def hf_token_clear(self):
        try:
            hftoken.clear()
        except Exception:
            pass
        return self.hf_token_status()

    def open_link(self, name):
        url = LINKS.get(name)
        if url:
            os.startfile(url)

    def copy_transcript(self):
        if self._last_output is None:
            return {"ok": False, "error": "There is no transcript yet."}
        try:
            text = self._last_output.read_text(encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        # webview clipboard via JS; return text for the page to copy
        return {"ok": True, "text": text}

    def open_folder(self):
        if self._last_output is not None:
            os.startfile(self._last_output.parent)

    # --- frameless window controls (custom title bar) ---
    def win_minimize(self):
        if self._window is not None:
            self._window.minimize()

    def win_toggle_max(self):
        """Maximize or restore; returns whether the window is now maximized.
        Decided from the window itself rather than remembered here, so it
        stays right when something else maximizes or restores the window."""
        form = self._window.native if self._window is not None else None
        if form is None:
            return False
        from System.Windows.Forms import FormWindowState
        if form.WindowState == FormWindowState.Maximized:
            self._window.restore()
            return False
        self._window.maximize()
        return True

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


def _web_engine():
    """The engine pywebview will render with; initialize() decides it once."""
    from webview.guilib import initialize
    return getattr(initialize(), "renderer", None)


def _ask_for_webview2():
    # Without WebView2, pywebview silently falls back to the IE11 engine, which
    # cannot run this UI's JavaScript.
    mb_yesno, mb_iconwarning, idyes = 0x4, 0x30, 6
    answer = ctypes.windll.user32.MessageBoxW(
        None,
        "Transcribe needs the Microsoft Edge WebView2 Runtime, which is not "
        "installed on this PC.\n\nOpen Microsoft's download page now? After "
        "installing it, start Transcribe again.",
        "Transcribe", mb_yesno | mb_iconwarning)
    if answer == idyes:
        os.startfile(WEBVIEW2_URL)


def _pin_web_view(window):
    """Restoring the minimized window could leave it blank. While Windows is
    still restoring it, the window gets moved again to its saved bounds (by
    all signs WinForms putting them back); Windows then finishes its own move
    with stale numbers and shifts the web view a second time, by the distance
    from the minimized spot (-32000, -32000). Move the web view back to the
    window's corner after each restore."""
    swp_nosize, swp_nozorder, swp_noactivate = 0x1, 0x4, 0x10

    def put_back():
        hwnd = window.native.browser.webview.Handle.ToInt64()
        ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
            swp_nosize | swp_nozorder | swp_noactivate)

    def on_restored():
        from System import Action
        # Queued on the UI thread, so it runs after the restore has finished.
        window.native.BeginInvoke(Action(put_back))

    window.events.restored += on_restored


def _place_window(window, width, height):
    """pywebview sizes the form before making it borderless, which shrinks it
    (580x380 became 564x360), and asks for CenterScreen only after creating
    the window; it opened cascaded down from the top left instead. Before it
    shows, size it and center it on the monitor under the pointer. Also keep
    a maximized window inside the work area: a borderless form otherwise
    maximizes over the taskbar."""
    from System.Drawing import Rectangle
    from System.Windows.Forms import Cursor, Screen

    def work_area(form):
        screen = Screen.FromControl(form)
        work, monitor = screen.WorkingArea, screen.Bounds
        # MaximizedBounds counts from the monitor's corner, not the desktop's.
        return Rectangle(work.X - monitor.X, work.Y - monitor.Y,
                         work.Width, work.Height)

    def before_show():
        form = window.native
        scale = ctypes.windll.user32.GetDpiForWindow(
            ctypes.c_void_p(form.Handle.ToInt64())) / 96
        w, h = int(width * scale), int(height * scale)
        area = Screen.FromPoint(Cursor.Position).WorkingArea
        form.SetBounds(area.X + (area.Width - w) // 2,
                       area.Y + (area.Height - h) // 2, w, h)
        form.MaximizedBounds = work_area(form)
        # Moved to another monitor: maximize to that one's work area.
        form.Move += lambda _sender, _args: setattr(
            form, "MaximizedBounds", work_area(form))

    window.events.before_show += before_show


def _clear_dll_directory():
    """PyInstaller's bootloader points the DLL search path at the unpacked
    bundle, and child processes (pip, the speech runtime, ffmpeg) inherit it
    (PyInstaller docs, Common Issues and Pitfalls). Clear it once the window
    is up, after the modules that load DLLs from the bundle are imported."""
    ctypes.windll.kernel32.SetDllDirectoryW(None)


def main():
    if _web_engine() != "edgechromium":
        _ask_for_webview2()
        return
    _launch_housekeeping()
    api = Api()
    width, height = 580, 380
    window = webview.create_window(
        "Transcribe", str(UI / "index.html"), js_api=api,
        width=width, height=height, min_size=(480, 360),
        background_color="#0b0b10",
        frameless=True, easy_drag=False, resizable=True)
    api.set_window(window)
    _place_window(window, width, height)
    _pin_web_view(window)
    # The title bar's maximize button shows the window's real state, however
    # it changed.
    window.events.maximized += lambda: api._emit("maxed", True)
    window.events.restored += lambda: api._emit("maxed", False)
    # Closing the window ends the job: only the flag and the job object's
    # terminate, since this runs on the UI thread.
    window.events.closing += api.cancel
    if FROZEN:
        import hashlib, ssl, tarfile, zipfile  # noqa: F401,E401 load their DLLs now
        window.events.shown += _clear_dll_directory
    webview.start()


if __name__ == "__main__":
    main()
