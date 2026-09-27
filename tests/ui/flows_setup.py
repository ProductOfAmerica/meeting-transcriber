"""The setup screen's check-before-Install flows, in Edge against mock.js.

    .venv-build\\Scripts\\python.exe tests\\ui\\flows_setup.py [UI_DIR]

Exits 1 if any check fails.
"""
import sys

from playwright.sync_api import sync_playwright

from rig import Rig, launch, ui_dir

# What firstrun.check_gpu says about a GPU Transcribe can't use.
GPU_BLOCK = ("NVIDIA GeForce GT 1030 (compute capability 6.1, 2048 MiB) can't "
             "run Transcribe. It needs an NVIDIA GTX 16 or RTX 20 series GPU "
             "or newer with at least 6 GB of memory.")

fails = []


def check(what, ok):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


def visible(r, sel):
    return r.page.locator(sel).is_visible()


def open_setup(r, **mock):
    """Open with setup needed, and wait for the setup view to be on screen."""
    r.open(envReady=False, autoplay=False, **mock)
    r.page.wait_for_function("document.body.dataset.view === 'setup'")
    r.settle(700)


with sync_playwright() as p:
    browser = launch(p)
    r = Rig(browser, ui_dir())

    # 1. A supported PC: Install at once, nothing else.
    open_setup(r)
    check("supported: Install shows", visible(r, "#setupStart"))
    check("supported: no reason, no Try again",
          not visible(r, "#setupBlock") and not visible(r, "#setupAgain"))
    check("supported: nothing focused", r.js(
        "document.activeElement === document.body"))

    # 2. A blocked PC: the reason replaces the intro and Install.
    open_setup(r, setupBlock=GPU_BLOCK)
    check("blocked: reason shows", visible(r, "#setupBlock")
          and "GT 1030" in r.page.inner_text("#setupBlock"))
    check("blocked: no intro, no Install",
          not visible(r, "#setupIntro") and not visible(r, "#setupStart"))
    check("blocked: Try again shows", visible(r, "#setupAgain"))
    check("blocked: nothing focused before a click", r.js(
        "document.activeElement === document.body"))

    # 3. Try again while still blocked: same screen, the box shakes.
    r.js("window.__mock.setupCheckMs = 600")
    r.page.click("#setupAgain")
    r.page.wait_for_timeout(150)
    check("recheck: button says Checking and is disabled",
          r.page.inner_text("#setupAgain") == "Checking…"
          and r.page.locator("#setupAgain").is_disabled())
    r.page.wait_for_timeout(700)
    check("recheck: back to Try again", r.page.inner_text("#setupAgain")
          == "Try again" and not r.page.locator("#setupAgain").is_disabled())
    check("recheck: still blocked", visible(r, "#setupBlock")
          and not visible(r, "#setupStart"))
    shook = r.js("""document.getElementById('setupBlock').getAnimations()
                    .some(a => a.id === 'v')""")
    check("recheck: the reason shook", shook)

    # 4. Try again once the cause is fixed: Install comes back, focused.
    r.js("window.__mock.setupBlock = null; window.__mock.setupCheckMs = 90")
    r.page.click("#setupAgain")
    r.page.wait_for_timeout(500)
    check("fixed: Install back, reason gone", visible(r, "#setupStart")
          and visible(r, "#setupIntro") and not visible(r, "#setupBlock")
          and not visible(r, "#setupAgain"))
    check("fixed: Install focused", r.js(
        "document.activeElement.id === 'setupStart'"))
    r.page.click("#setupStart")
    r.page.wait_for_timeout(300)
    check("fixed: Install starts setup", r.js(
        "window.__mock.calls.includes('bootstrap_env')"))

    # 5. Install clicked before a slow answer: it waits and says so.
    open_setup(r, setupCheckMs=4000)
    r.page.click("#setupStart")
    r.page.wait_for_timeout(600)
    check("early click: waits, says Checking", r.page.inner_text("#setupStart")
          == "Checking…" and not r.js(
              "window.__mock.calls.includes('bootstrap_env')"))
    r.page.wait_for_function(
        "window.__mock.calls.includes('bootstrap_env')", timeout=6000)
    check("early click: setup starts once the answer comes", r.js(
        "window.__mock.calls.includes('bootstrap_env')"))

    # 6. Install clicked before a slow answer that blocks: no setup.
    open_setup(r, setupCheckMs=3000, setupBlock=GPU_BLOCK)
    r.page.click("#setupStart")
    r.page.wait_for_selector("#setupBlock", state="visible", timeout=6000)
    r.page.wait_for_timeout(300)
    check("early click, blocked: no setup, reason shows", not r.js(
        "window.__mock.calls.includes('bootstrap_env')")
          and visible(r, "#setupBlock"))
    browser.close()

print(f"{len(fails)} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
