# PyInstaller spec for Transcribe.exe (windowed, no console, onefile).
#
# The exe is a thin launcher: the pywebview GUI plus ui/ assets. It bundles
# the backend/ sources and requirements.lock as data; on first run
# backend.firstrun copies backend/ to %LOCALAPPDATA%\Transcribe\code\<key>\
# and builds a venv there from requirements.lock. The heavy stack (ONNX
# Runtime, torch, pyannote) runs only in that venv, in the backend.runner
# subprocess, so it is excluded here. Next to the exe it writes
# THIRD_PARTY_LICENSES.txt for everything the exe bundles.
#
# Build:  build.cmd
import fnmatch
import importlib.metadata
import os
import platform
import re
import sys
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(".")

# Every distribution in requirements-build.lock is one or the other. Shipped ones
# are bundled into the exe; build-only ones must never be (PyInstaller outside its
# bootloader and loader is GPL, and none of them is app code).
SHIPPED = {"pywebview", "pythonnet", "clr-loader", "cffi", "pycparser", "bottle",
           "proxy-tools", "typing-extensions"}
BUILD_ONLY = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile",
              "pywin32-ctypes", "setuptools", "packaging", "pytest", "pluggy",
              "iniconfig", "pygments", "colorama", "playwright", "greenlet",
              "pyee"}


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_packages(path):
    with open(path, encoding="utf-8") as f:
        return {_norm(m.group(1)) for m in re.finditer(r"^([A-Za-z0-9][\w.-]*)==",
                                                       f.read(), re.M)}


_unclassified = _lock_packages("requirements-build.lock") - SHIPPED - BUILD_ONLY
if _unclassified:
    raise SystemExit("Transcribe.spec: classify these requirements-build.lock "
                     "packages as SHIPPED or BUILD_ONLY: "
                     + ", ".join(sorted(_unclassified)))


def _tree(src, dest):
    """(file, folder) pairs for a source tree, without bytecode caches."""
    pairs = []
    for folder, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".pyc"):
                continue
            rel = os.path.relpath(folder, src)
            pairs.append((os.path.join(folder, name),
                          dest if rel == "." else os.path.join(dest, rel)))
    return pairs


datas = _tree("ui", "ui") + _tree("backend", "backend") + [
    ("requirements.lock", "."),
]
binaries = []
hiddenimports = [
    "backend.app", "backend.pipeline", "backend.appapi",
    "backend.transcript", "backend.writeprobe", "backend.firstrun",
    "backend.procs", "backend.models", "backend.fetch", "backend.fsutil",
    "webview.platforms.winforms", "clr", "proxy_tools",
]
# pywebview and its Windows EdgeChromium assets. webview.__pyinstaller is the
# PyInstaller hook pywebview provides for build time; as a hidden import it would
# drag PyInstaller itself into the exe.
_d, _b, _h = collect_all(
    "webview",
    filter_submodules=lambda name: not name.startswith("webview.__pyinstaller"))
datas += _d
binaries += _b
hiddenimports += _h

a = Analysis(
    ["backend/bootstrap.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # The GUI process must never pull the multi-GB ML stack. mako, pygments,
    # PIL and numpy arrive through bottle's optional template engine (via
    # webview.http) when the build venv also holds the runtime stack.
    # setuptools arrives through cffi's distutils shim; clr_loader uses cffi
    # without compiling anything.
    excludes=[
        "onnx_asr", "onnxruntime", "torch", "torchaudio", "torchcodec",
        "pyannote", "lightning", "huggingface_hub", "pandas", "scipy",
        "sklearn", "matplotlib", "mako", "pygments", "PIL", "numpy",
        "PyInstaller", "setuptools", "pkg_resources", "_distutils_hack",
        "distutils",
    ],
    noarchive=False,
)

_owners = importlib.metadata.packages_distributions()
_leaked = sorted({name for name, _src, _kind in a.pure
                  if any(_norm(d) in BUILD_ONLY
                         for d in _owners.get(name.split(".")[0], ()))})
if _leaked:
    raise SystemExit("Transcribe.spec: build-only modules would ship in the exe: "
                     + ", ".join(_leaked))

# Windows 10 and 11 always load their own Universal CRT. PyInstaller otherwise
# bundles whatever copies it finds on the builder's PATH.
a.binaries = [b for b in a.binaries
              if not os.path.basename(b[0]).lower().startswith("api-ms-win-")
              and os.path.basename(b[0]).lower() != "ucrtbase.dll"]


# --- THIRD_PARTY_LICENSES.txt ------------------------------------------------
# Every bundled file must be attributed, or the build fails. A license source is
# "dist:<name>" (that distribution's own license files) or a file path.
_CPYTHON = "CPython " + platform.python_version()
_CPYTHON_LICENSES = [os.path.join(sys.base_prefix, "LICENSE.txt"),
                     "licenses/cpython-incorporated.txt"]
_WEBVIEW2 = "Microsoft Edge WebView2 SDK (bundled by pywebview)"
_DOTNET = ".NET Standard 2.0 library facades (bundled by pythonnet)"
_RULES = [  # (glob over the bundle path, component); first match wins
    ("webview/lib/Microsoft.Web.WebView2.*", _WEBVIEW2),
    ("webview/lib/runtimes/*/WebView2Loader.dll", _WEBVIEW2),
    ("pythonnet/runtime/Python.Runtime.*", "pythonnet"),
    ("pythonnet/runtime/*", _DOTNET),
]
_LICENSES = {
    _CPYTHON: _CPYTHON_LICENSES,
    _WEBVIEW2: ["licenses/Microsoft.Web.WebView2.txt"],
    _DOTNET: ["licenses/NETStandard.Library.txt"],
    "proxy-tools": ["licenses/proxy_tools.txt"],   # its sdist ships no license
}
_TEXT_SUFFIXES = {"", ".txt", ".md", ".rst"}


def _file_owners(names):
    owned = {}
    for name in names:
        dist = importlib.metadata.distribution(name)
        for f in dist.files or ():
            owned[os.path.normcase(str(dist.locate_file(f)))] = _norm(name)
    return owned


def _dist_license_files(name):
    dist = importlib.metadata.distribution(name)
    found = []
    for f in dist.files or ():
        parts = f.parts
        in_licenses = len(parts) > 2 and parts[0].endswith(".dist-info") \
            and parts[1] == "licenses"
        named = re.match(r"(?i)(licen[cs]e|copying|notice)", f.name)
        if (in_licenses or named) and os.path.splitext(f.name)[1].lower() \
                in _TEXT_SUFFIXES:
            found.append(str(dist.locate_file(f)))
    return sorted(found)


def _write_licenses(a, out_path):
    base = os.path.normcase(sys.base_prefix) + os.sep
    owned = _file_owners(SHIPPED)
    runtime = _file_owners({"pyinstaller", "pyinstaller-hooks-contrib"})
    comps, unmatched = {}, []

    def claim(comp, what):
        comps.setdefault(comp, []).append(what)

    for dest, src, _kind in list(a.binaries) + list(a.datas):
        d = dest.replace("\\", "/")
        if d.startswith(("backend/", "ui/")) or d == "requirements.lock":
            continue
        rule = next((c for g, c in _RULES if fnmatch.fnmatch(d, g)), None)
        path = os.path.normcase(os.path.abspath(src)) if src else ""
        if rule:
            claim(rule, d)
        elif path.startswith(base) or d == "base_library.zip":
            claim(_CPYTHON, d)
        elif path in owned:
            claim(owned[path], d)
        else:
            unmatched.append(d)
    for name, _src, _kind in a.pure:
        top = name.split(".")[0]
        owners = {_norm(o) for o in _owners.get(top, ())} & SHIPPED
        if top == "backend":
            continue
        if top in sys.stdlib_module_names:
            claim(_CPYTHON, name)
        elif owners:
            claim(sorted(owners)[0], name)
        else:
            unmatched.append(name)
    for name, src, _kind in a.scripts:
        path = os.path.normcase(os.path.abspath(src)) if src else ""
        if path == os.path.normcase(os.path.join(ROOT, "backend", "bootstrap.py")):
            continue
        if path in runtime:
            claim(runtime[path], name)
        else:
            unmatched.append(name)
    comps.setdefault("pyinstaller", []).append("bootloader")
    if unmatched:
        raise SystemExit("Transcribe.spec: no license attribution for: "
                         + ", ".join(sorted(unmatched)))
    missing = {"pywebview", "pythonnet"} - set(comps)
    if missing:
        raise SystemExit("Transcribe.spec: license attribution looks broken, "
                         "no files for " + ", ".join(sorted(missing)))

    sections = []
    for comp in sorted(comps, key=str.lower):
        sources = _LICENSES.get(comp) or _dist_license_files(comp)
        if not sources:
            raise SystemExit(f"Transcribe.spec: no license text for {comp}; "
                             "vendor it under licenses/")
        title = comp
        if comp in SHIPPED or comp.startswith("pyinstaller"):
            title = f"{comp} {importlib.metadata.version(comp)}"
        texts = []
        for s in sources:
            with open(s, encoding="utf-8", errors="replace") as f:
                texts.append(f"--- {os.path.basename(s)} ---\n{f.read().strip()}")
        n = len(comps[comp])
        sections.append("=" * 79 + f"\n{title}  ({n} bundled "
                        f"file{'' if n == 1 else 's'})\n" + "=" * 79 + "\n\n"
                        + "\n\n".join(texts))
        print(f"licenses: {title}: {len(comps[comp])} files, "
              f"{len(sources)} license file(s)")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("Third-party software in Transcribe.exe\n\n"
                "Transcribe itself is MIT licensed (LICENSE in its source "
                "repository). The exe bundles the\ncomponents below, each "
                "distributed under the license texts that follow it.\n\n"
                + "\n\n\n".join(sections) + "\n")


_write_licenses(a, os.path.join(DISTPATH, "THIRD_PARTY_LICENSES.txt"))

pyz = PYZ(a.pure)

# onefile: everything in EXE, no COLLECT.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Transcribe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                # windowed -> no cmd window
    disable_windowed_traceback=False,
    icon=os.path.join("ui", "app.ico"),
)
