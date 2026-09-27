"""End-to-end flows against the mock's timed jobs (autoplay), checking the
page's state machine: no page errors, the right view at each step.

    .venv-build\\Scripts\\python.exe tests\\ui\\flows_e2e.py [UI_DIR [SHOTS_DIR]]

With SHOTS_DIR, each flow's last screen is saved there. Exits 1 if any flow
fails.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from rig import Rig, launch, ui_dir

errors = []


def view(r):
    return r.js("document.body.dataset.view")


def wait_view(r, name, timeout=20000):
    r.page.wait_for_function(f"document.body.dataset.view === '{name}'",
                             timeout=timeout)
    r.settle(900)


def run(b, ui, shots, name, fn):
    r = Rig(b, ui)
    try:
        fn(r)
        print(f"{name}: ok")
    except Exception as e:  # noqa: BLE001
        errors.append(f"{name}: {e}")
        print(f"{name}: FAILED {e}")
    finally:
        if r.page:
            if shots:
                r.page.screenshot(path=str(shots / f"flow-{name}.png"))
            r.page.context.close()


def f_pertrack(r):
    p = r.open(pick="pertrack", speed=3)
    p.on("pageerror", lambda e: errors.append(f"pertrack pageerror {e}"))
    r.click("#pick")
    r.click("#go", 300)
    assert view(r) == "running"
    wait_view(r, "done")
    assert r.js("document.querySelectorAll('#pvBody .who').length") >= 3
    r.click("#copy", 300)
    assert r.js("document.getElementById('copy').classList.contains('copied')")
    r.click("#again")
    assert view(r) == "idle"
    assert r.js("!document.getElementById('pick').classList.contains('hidden')")


def f_mono_token(r):
    p = r.open(pick="mono", token=None, speed=3)
    p.on("pageerror", lambda e: errors.append(f"mono pageerror {e}"))
    r.click("#pick")
    assert r.js("document.getElementById('go').disabled")
    r.page.fill("#tokenInput", "hf_abcdefghijklmnopqrstuvwxyz")
    r.click("#tokenSave", 1500)
    assert not r.js("document.getElementById('go').disabled")
    r.click("#go", 300)
    wait_view(r, "done")


def f_cancel(r):
    p = r.open(pick="pertrack", speed=1)
    p.on("pageerror", lambda e: errors.append(f"cancel pageerror {e}"))
    r.click("#pick")
    r.click("#go", 2500)
    r.click("#cancel", 200)
    assert r.js("document.getElementById('cancel').disabled")
    wait_view(r, "errbox")
    assert r.js("document.getElementById('errbox').classList.contains('calm')")
    r.click("#errback")
    assert view(r) == "idle"
    assert r.js("!document.getElementById('ready').classList.contains('hidden')")


def f_fail_retry(r):
    p = r.open(pick="mono", token="saved", speed=3, outcome="oom")
    p.on("pageerror", lambda e: errors.append(f"fail pageerror {e}"))
    r.click("#pick")
    r.click("#go", 300)
    wait_view(r, "errbox")
    assert not r.js("document.getElementById('errbox').classList.contains('calm')")
    # The backend ends the message with the run log, shown as a detail line.
    msg = r.js("document.getElementById('errmsg').textContent")
    log = r.js("document.getElementById('errlog').textContent")
    assert "Log:" not in msg and msg.startswith("The GPU ran out of memory"), msg
    assert log.startswith("Log: C:") and log.endswith(".log"), log
    assert r.js("!document.getElementById('errlog').classList.contains('hidden')")
    r.click("#errback")
    assert view(r) == "idle"
    r.js("window.__mock.outcome = 'ok'")
    r.click("#go", 300)
    wait_view(r, "done")


def f_setup(r):
    p = r.open(envReady=False, speed=4)
    p.on("pageerror", lambda e: errors.append(f"setup pageerror {e}"))
    assert view(r) == "setup"
    r.click("#setupStart", 300)
    wait_view(r, "idle", 30000)


def f_setup_fail_retry(r):
    p = r.open(envReady=False, speed=4, setupOutcome="fail")
    p.on("pageerror", lambda e: errors.append(f"setupfail pageerror {e}"))
    r.click("#setupStart", 300)
    r.page.wait_for_function(
        "!document.getElementById('setupRetry').classList.contains('hidden')",
        timeout=30000)
    r.settle(600)
    r.js("window.__mock.setupOutcome = 'ok'")
    r.click("#setupRetry", 300)
    wait_view(r, "idle", 30000)


def f_setup_cancel(r):
    p = r.open(envReady=False, speed=1)
    p.on("pageerror", lambda e: errors.append(f"setupcancel pageerror {e}"))
    r.click("#setupStart", 1500)
    r.click("#setupCancel", 800)
    assert r.js("document.getElementById('setup').classList.contains('calm')")
    assert r.js("!document.getElementById('setupRetry').classList.contains('hidden')")
    assert r.js("document.getElementById('setupErr').classList.contains('hidden')")
    assert r.js("document.getElementById('setupTitle').textContent") == "Setup cancelled"
    assert not r.js("document.body.classList.contains('working')")


def starts(r):
    return r.js("window.__mock.calls.filter((c) => c === 'start').length")


def f_double_start(r):
    # Two clicks and an Enter during the leave transition start one run.
    p = r.open(pick="pertrack", speed=3)
    p.on("pageerror", lambda e: errors.append(f"double pageerror {e}"))
    r.click("#pick")
    r.js("() => { const g = document.getElementById('go'); g.click(); g.click(); }")
    r.page.keyboard.press("Enter")
    r.page.wait_for_timeout(400)
    assert starts(r) == 1, starts(r)
    wait_view(r, "done")
    r.click("#again")
    r.click("#pick")
    r.click("#go", 300)
    assert starts(r) == 2, starts(r)


def f_start_rejects(r):
    p = r.open(pick="pertrack", speed=3)
    p.on("pageerror", lambda e: errors.append(f"reject pageerror {e}"))
    r.js("""() => {
      const api = window.pywebview.api;
      api._start = api.start;
      api.start = () => Promise.reject(Object.assign(
        new Error("[WinError 53] The network path was not found"),
        { name: "OSError" }));
    }""")
    r.click("#pick")
    r.click("#go", 300)
    wait_view(r, "errbox")
    msg = r.js("document.getElementById('errmsg').textContent")
    assert msg.startswith("OSError: [WinError 53]"), msg
    assert not r.js("document.getElementById('errbox').classList.contains('calm')")
    r.click("#errback")
    r.js("() => { const api = window.pywebview.api; api.start = api._start; }")
    r.click("#go", 300)
    wait_view(r, "done")


def f_setup_rejects(r):
    p = r.open(envReady=False, speed=4)
    p.on("pageerror", lambda e: errors.append(f"setupreject pageerror {e}"))
    r.js("""() => {
      const api = window.pywebview.api;
      api._boot = api.bootstrap_env;
      api.bootstrap_env = () => Promise.reject(Object.assign(
        new Error("[Errno 13] Permission denied"), { name: "PermissionError" }));
    }""")
    r.click("#setupStart", 600)
    assert r.js("document.getElementById('setup').classList.contains('failed')")
    msg = r.js("document.getElementById('setupErrMsg').textContent")
    assert msg.startswith("PermissionError: [Errno 13]"), msg
    r.js("() => { const api = window.pywebview.api; api.bootstrap_env = api._boot; }")
    r.click("#setupRetry", 300)
    wait_view(r, "idle", 30000)


def f_calls_reject(r):
    # A failing dialog, copy or folder call leaves the page as it was.
    p = r.open(pick="pertrack", speed=3)
    p.on("pageerror", lambda e: errors.append(f"calls pageerror {e}"))
    r.js("""() => {
      const api = window.pywebview.api;
      api._pick = api.pick_input;
      api.pick_input = () => Promise.reject(new Error("dialog failed"));
    }""")
    r.click("#pick", 500)
    assert r.js("!document.getElementById('pick').classList.contains('hidden')")
    r.js("() => { const api = window.pywebview.api; api.pick_input = api._pick; }")
    r.click("#pick")
    r.click("#go", 300)
    wait_view(r, "done")
    r.js("""() => {
      const api = window.pywebview.api;
      api.copy_transcript = () => Promise.reject(new Error("gone"));
      api.open_folder = () => Promise.reject(new Error("gone"));
    }""")
    r.click("#copy", 300)
    r.click("#openf", 300)
    assert not r.js("document.getElementById('copy').classList.contains('copied')")
    assert r.js("document.getElementById('copy').classList.contains('nocopy')")
    assert view(r) == "done"


def f_copied_label(r):
    p = r.open(pick="pertrack", speed=3)
    p.on("pageerror", lambda e: errors.append(f"copied pageerror {e}"))
    r.click("#pick")
    r.click("#go", 300)
    wait_view(r, "done")
    def named(name):
        return r.page.get_by_role("button", name=name, exact=True).count()
    assert named("Copy transcript") == 1 and named("Copied") == 0
    r.click("#copy", 300)
    assert named("Copied") == 1 and named("Copy transcript") == 0
    r.page.wait_for_timeout(2000)
    assert named("Copy transcript") == 1 and named("Copied") == 0


def f_token_terms(r):
    p = r.open(pick="mono", token=None, speed=3)
    p.on("pageerror", lambda e: errors.append(f"terms pageerror {e}"))
    r.js("""() => {
      window.pywebview.api.hf_token_save = async () => {
        window.__mock.token = "saved";
        return { ok: true, status: "terms" };
      };
    }""")
    r.click("#pick")
    r.page.fill("#tokenInput", "hf_abcdefghijklmnopqrstuvwxyz")
    r.click("#tokenSave", 600)
    assert r.js("!document.getElementById('tokenForm').classList.contains('hidden')")
    assert r.js("!document.getElementById('hfTerms').closest('.hidden')")
    assert "step 1" in r.js("document.getElementById('tokenMsg').textContent")


def f_back_clears_token_msg(r):
    p = r.open(pick="mono", token=None, speed=3, outcome="hf_gate")
    p.on("pageerror", lambda e: errors.append(f"backmsg pageerror {e}"))
    r.click("#pick")
    r.page.fill("#tokenInput", "hf_abcdefghijklmnopqrstuvwxyz")
    r.click("#tokenSave", 1500)
    assert r.js("document.getElementById('tokenMsg').textContent")
    r.click("#go", 300)
    wait_view(r, "errbox")
    r.click("#errback")
    assert r.js("document.getElementById('tokenMsg').textContent") == ""


def f_refit(r):
    p = r.open(pick="pertrack", speed=3)
    p.on("pageerror", lambda e: errors.append(f"refit pageerror {e}"))
    r.click("#btnMax", 300)
    r.js("window.__mock.fitNeed = null")
    r.click("#btnMax", 600)
    assert r.js("window.__mock.fitNeed") is not None, "no fit after restore"
    r.js("window.__mock.fitNeed = null")
    r.js("""() => {
      Object.defineProperty(document, "visibilityState",
        { value: "visible", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    }""")
    r.page.wait_for_timeout(600)
    assert r.js("window.__mock.fitNeed") is not None, "no fit when visible"


def f_drop_refused(r):
    p = r.open()
    p.on("pageerror", lambda e: errors.append(f"drop pageerror {e}"))
    got = r.js("""() => {
      const dt = new DataTransfer();
      dt.dropEffect = "copy";
      const over = new DragEvent("dragover", { dataTransfer: dt, cancelable: true,
                                               bubbles: true });
      document.getElementById("pick").dispatchEvent(over);
      const drop = new DragEvent("drop", { dataTransfer: dt, cancelable: true,
                                           bubbles: true });
      document.getElementById("pick").dispatchEvent(drop);
      return [over.defaultPrevented, dt.dropEffect, drop.defaultPrevented];
    }""")
    assert got == [True, "none", True], got


def f_models_indeterminate(r):
    p = r.open(envReady=False, autoplay=False)
    p.on("pageerror", lambda e: errors.append(f"models pageerror {e}"))
    r.click("#setupStart", 400)
    r.progress(mode="setup", stage="fetch_python", pct=40.0, msg=None)
    row = "document.querySelectorAll('#setupSteps .step')"
    assert r.js(f"{row}[2].querySelector('.trail').textContent") == "40%"
    r.progress(mode="setup", stage="fetch_models", pct=63.2, msg=None)
    assert r.js(f"{row}[6].querySelector('.trail').textContent") == ""
    assert not r.js(f"{row}[6].querySelector('.bar').classList.contains('det')")


def f_copy_none(r):
    # copy_transcript answers {ok: false, error} when there is no transcript.
    p = r.open(pick="pertrack", speed=3)
    p.on("pageerror", lambda e: errors.append(f"copynone pageerror {e}"))
    r.click("#pick")
    r.click("#go", 300)
    wait_view(r, "done")
    r.js("""() => { window.pywebview.api.copy_transcript = async () =>
      ({ ok: false, error: "There is no transcript yet." }); }""")
    r.click("#copy", 300)
    named = lambda n: r.page.get_by_role("button", name=n, exact=True).count()
    assert named("Couldn't copy") == 1, "no failed-copy label"
    r.page.wait_for_timeout(2700)
    assert named("Copy transcript") == 1


def f_unknown_reason(r):
    p = r.open(pick="unknown")
    p.on("pageerror", lambda e: errors.append(f"unknown pageerror {e}"))
    r.click("#pick")
    said = r.js("document.getElementById('rSummary').textContent")
    assert said.startswith("Transcribe can't transcribe .pdf files."), said
    assert r.js("document.getElementById('go').classList.contains('hidden')")


def f_maxed_event(r):
    # The window reports maximize and restore however they happened.
    p = r.open(pick="pertrack")
    p.on("pageerror", lambda e: errors.append(f"maxed pageerror {e}"))
    title = "document.getElementById('btnMax').title"
    r.js("window.__on('maxed', true)")
    assert r.js(title) == "Restore", r.js(title)
    assert r.js("!document.querySelector('#btnMax .irestore')"
                ".classList.contains('hidden')")
    r.js("window.__mock.fitNeed = null")
    r.js("window.__on('maxed', false)")
    r.page.wait_for_timeout(600)
    assert r.js(title) == "Maximize"
    assert r.js("window.__mock.fitNeed") is not None, "no fit after restore"


def f_grip(r):
    # No resize before the window's size is known; then it follows.
    p = r.open()
    p.on("pageerror", lambda e: errors.append(f"grip pageerror {e}"))
    early = r.js("""async () => {
      const g = document.getElementById("grip");
      const opts = { bubbles: true, cancelable: true, pointerId: 1,
                     screenX: 500, screenY: 400 };
      const down = new PointerEvent("pointerdown", opts);
      g.dispatchEvent(down);
      g.dispatchEvent(new PointerEvent("pointermove",
        { ...opts, screenX: 520, screenY: 430 }));
      await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 50)));
      const before = window.__mock.calls.filter((c) => c === "win_resize").length;
      g.dispatchEvent(new PointerEvent("pointermove",
        { ...opts, screenX: 540, screenY: 450 }));
      await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 50)));
      const after = window.__mock.calls.filter((c) => c === "win_resize").length;
      g.dispatchEvent(new PointerEvent("pointerup", opts));
      return [down.defaultPrevented, before, after];
    }""")
    assert early == [True, 0, 1], early


FLOWS = [("pertrack", f_pertrack), ("mono-token", f_mono_token),
         ("cancel", f_cancel), ("fail-retry", f_fail_retry),
         ("setup", f_setup), ("setup-fail-retry", f_setup_fail_retry),
         ("setup-cancel", f_setup_cancel), ("double-start", f_double_start),
         ("start-rejects", f_start_rejects),
         ("setup-rejects", f_setup_rejects),
         ("calls-reject", f_calls_reject), ("copied-label", f_copied_label),
         ("token-terms", f_token_terms),
         ("back-clears-token-msg", f_back_clears_token_msg),
         ("refit", f_refit), ("drop-refused", f_drop_refused),
         ("models-indeterminate", f_models_indeterminate),
         ("copy-none", f_copy_none), ("unknown-reason", f_unknown_reason),
         ("maxed-event", f_maxed_event), ("grip", f_grip)]


def main():
    ui = ui_dir()
    shots = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = launch(p)
        for name, fn in FLOWS:
            run(b, ui, shots, name, fn)
        b.close()
    print("ERRORS:" if errors else "all flows passed", *errors, sep="\n")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
