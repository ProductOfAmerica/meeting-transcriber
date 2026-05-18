const $ = (id) => document.getElementById(id);
let chosen = null;
let lastOutput = null;

// Mono is one pass through these stages. Per-track instead steps through
// the participants (one whisperx run each) then a merge, so its stages
// repeat per person and are demoted to a substatus on the active row.
const MONO_STEPS = ["Loading model", "Detecting speech",
  "Transcribing", "Aligning", "Diarizing"];
// backend.runner emits canonical phases. Map each to the mono row it
// lights (mono rows are the fixed stages; several phases share a row).
const MONO_ROW = {
  load_model: 0, vad: 1, transcribe: 2,
  load_align: 3, align: 3,
  load_diarize: 4, diarize: 4, write: 4,
};
const SUBSTATUS = {
  load_model: "loading model…",
  vad: "detecting speech…",
  transcribe: "transcribing…",
  load_align: "loading alignment model…",
  align: "aligning…",
  load_diarize: "loading diarization model…",
  diarize: "diarizing…",
  write: "writing transcript…",
};

function buildSteps(ol, labels) {
  ol.innerHTML = "";
  for (const text of labels) {
    const li = document.createElement("li");
    li.className = "step todo";
    li.dataset.label = text;
    li.innerHTML = '<span class="ico"></span><span class="body">' +
      '<span class="lbl"></span><span class="ssub"></span>' +
      '<span class="barwrap"><i></i></span></span>';
    li.querySelector(".lbl").textContent = text;
    ol.appendChild(li);
  }
}

function renderSteps(mode) {
  let labels;
  if (mode === "pertrack") {
    const who = (chosen && chosen.speakers) || [];
    labels = [...who, "Merge transcripts"];
    $("trackline").textContent =
      who.length + (who.length === 1 ? " participant" : " participants");
    $("trackline").classList.remove("hidden");
  } else {
    labels = MONO_STEPS;
    $("trackline").classList.add("hidden");
  }
  buildSteps($("steps"), labels);
}

// The runner sends a real pct only for phases that have a genuine hook
// (transcribe/align/diarize). Determinate iff a pct is present; load/
// vad/write carry none -> indeterminate pulse (honest, no faking).
function barOf(stage, pct) {
  return pct != null ? pct : null;
}

function markStep(ol, activeIdx, substatus, barPct) {
  const steps = ol.children;
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i];
    s.classList.remove("done", "active", "todo");
    s.classList.add(i < activeIdx ? "done"
      : i === activeIdx ? "active" : "todo");
    s.querySelector(".ico").textContent = i < activeIdx ? "✓" : "";
    s.querySelector(".ssub").textContent =
      (i === activeIdx && substatus) ? substatus : "";
    if (i === activeIdx) {
      const bw = s.querySelector(".barwrap");
      const fill = bw.querySelector("i");
      if (typeof barPct === "number") {
        bw.classList.add("det");
        fill.style.width = Math.max(0, Math.min(100, barPct)) + "%";
      } else {
        bw.classList.remove("det");
        fill.style.width = "";
      }
      // keep the active participant visible when many tracks overflow;
      // "nearest" is a no-op when it's already on screen (no jank).
      s.scrollIntoView({ block: "nearest" });
    }
  }
}

function show(which) {
  for (const id of ["idle", "setup", "running", "done", "errbox"]) {
    $(id).classList.toggle("hidden", id !== which);
  }
}

// First-run installer (frozen exe only). Phases come over the SAME
// progress protocol as transcription, discriminated by mode:"setup".
const SETUP_STEPS = ["Prepare", "Download Python", "Download ffmpeg",
  "Create environment", "Install dependencies", "Verify GPU"];
const SETUP_ROW = {
  prepare: 0, fetch_python: 1, fetch_ffmpeg: 2,
  make_venv: 3, pip: 4, verify: 5,
};
const SETUP_SUB = {
  prepare: "preparing…", fetch_python: "downloading…",
  fetch_ffmpeg: "downloading…", make_venv: "creating venv…",
  pip: "installing… (the long step, ~5 GB)", verify: "checking CUDA…",
};

function setupProgress(p) {
  if (p.stage === "ready") {           // env built in this session
    resetIdle();
    refreshBanner();
    return;
  }
  if (p.stage === "failed") {
    $("setupErrMsg").textContent = p.msg || "Setup failed.";
    $("setupErr").classList.remove("hidden");
    $("setupCancel").classList.add("hidden");
    return;
  }
  const row = SETUP_ROW[p.stage];
  if (row == null) return;
  let sub = SETUP_SUB[p.stage] || "working…";
  if (p.stage === "pip" && p.msg) sub = p.msg.slice(0, 90);
  markStep($("setupSteps"), row, sub, barOf(p.stage, p.pct));
}

window.__on = (channel, payload) => {
  if (channel === "progress") {
    const p = (typeof payload === "string") ? { stage: payload } : payload;
    if (p.mode === "setup") { setupProgress(p); return; }
    const ol = $("steps");
    if (!ol.children.length) renderSteps(p.mode);
    if (p.mode === "pertrack") {
      if (p.stage === "Merging tracks") {
        markStep(ol, ol.children.length - 1, "");  // all done, merging
      } else if (p.track) {
        markStep(ol, p.track - 1, SUBSTATUS[p.stage] || "working…",
          barOf(p.stage, p.pct));
      }
    } else {
      const i = MONO_ROW[p.stage];
      if (i != null) {
        markStep(ol, i, SUBSTATUS[p.stage] || "working…",
          barOf(p.stage, p.pct));
      }
    }
  } else if (channel === "done") {
    $("stats").innerHTML =
      `<div><b>${payload.duration}</b>duration</div>` +
      `<div><b>${payload.speakers.length}</b>speakers</div>` +
      `<div><b>${payload.turns}</b>turns</div>` +
      `<div><b>~${payload.approx_tokens}</b>tokens</div>`;
    $("outpath").textContent = payload.output_path;
    lastOutput = payload.output_path;
    $("copy").textContent = "Copy transcript";
    show("done");
  } else if (channel === "error") {
    $("errmsg").textContent = payload;
    show("errbox");
  }
};

async function doPick() {
  applyChoice(await window.pywebview.api.pick_input());
}

function applyChoice(r) {
  if (!r) return;
  chosen = r;
  $("rTitle").textContent = r.title || r.name;
  let summary;
  if (r.mode === "pertrack") {
    const n = r.speakers.length;
    summary = "Per-participant recording · " + n
      + (n === 1 ? " person: " : " people: ") + r.speakers.join(", ")
      + " · transcribed separately, no token needed";
  } else if (r.mode === "mono") {
    summary = "Single mixed recording · speakers auto-detected "
      + "(needs your HF token)";
  } else {
    summary = "Couldn't tell the recording type. Pick the mixed audio "
      + "file, or a track inside the Audio Record folder.";
  }
  $("rSummary").textContent = summary;
  const known = r.mode === "pertrack" || r.mode === "mono";
  $("pick").classList.add("hidden");
  $("ready").classList.remove("hidden");
  $("go").classList.toggle("hidden", !known);
}

function resetIdle() {
  chosen = null;
  $("pick").classList.remove("hidden");
  for (const id of ["ready", "go"]) {
    $(id).classList.add("hidden");
  }
  $("steps").innerHTML = "";
  $("trackline").classList.add("hidden");
  show("idle");
}

$("pick").onclick = doPick;
$("change").onclick = doPick;

$("go").onclick = () => {
  renderSteps(chosen.mode);
  // pertrack rows are people: show what the first one is doing now
  // (model load) instead of a blank pulse until backend events arrive.
  markStep($("steps"), 0,
    chosen.mode === "pertrack" ? "loading model…" : undefined);
  show("running");
  window.pywebview.api.start({ path: chosen.path });
};

$("cancel").onclick = () => window.pywebview.api.cancel();
$("again").onclick = resetIdle;
$("errback").onclick = resetIdle;

// ---- first-run installer wiring ----
function renderSetup(st) {
  $("setupHome").textContent = (st && st.home) || "";
  $("setupSteps").innerHTML = "";
  $("setupErr").classList.add("hidden");
  $("setupStart").classList.remove("hidden");
  $("setupCancel").classList.add("hidden");
}

function startSetup() {
  buildSteps($("setupSteps"), SETUP_STEPS);
  markStep($("setupSteps"), 0, "starting…");
  $("setupStart").classList.add("hidden");
  $("setupErr").classList.add("hidden");
  $("setupCancel").classList.remove("hidden");
  window.pywebview.api.bootstrap_env();
}
$("setupStart").onclick = startSetup;
$("setupRetry").onclick = startSetup;
$("setupCancel").onclick = () => window.pywebview.api.cancel();

async function copyOut() {
  const r = await window.pywebview.api.copy_transcript(lastOutput);
  if (!r.ok) return;
  try {
    await navigator.clipboard.writeText(r.text);
    $("copy").textContent = "Copied";
  } catch (_) {
    $("copy").textContent = "Copy transcript";
  }
}
$("copy").onclick = copyOut;
$("openf").onclick = () => window.pywebview.api.open_folder(lastOutput);

async function refreshBanner() {
  const v = await window.pywebview.api.update_banner();
  if (v) {
    $("banner").textContent =
      "WhisperX " + v + " is available. It may include better models.";
    $("banner").classList.remove("hidden");
  } else {
    $("banner").classList.add("hidden");
  }
}

window.addEventListener("pywebviewready", async () => {
  let st = null;
  try {
    st = await window.pywebview.api.env_status();
  } catch (_) { /* old/dev shell without env_status: behave as ready */ }
  if (st && st.ready === false) {
    renderSetup(st);
    show("setup");
    return;                       // first run: install before anything else
  }
  refreshBanner();
});

// ---- custom (frameless) window chrome ----
function winApi() {
  return (window.pywebview && window.pywebview.api) || null;
}

$("btnMin").onclick = () => { const a = winApi(); if (a && a.win_minimize) a.win_minimize(); };
$("btnClose").onclick = () => { const a = winApi(); if (a && a.win_close) a.win_close(); };

async function toggleMax() {
  const a = winApi();
  if (!a || !a.win_toggle_max) return;
  const maxed = await a.win_toggle_max();
  $("btnMax").innerHTML = maxed ? "&#10064;" : "&#9633;";
}
$("btnMax").onclick = toggleMax;
$("tbdrag").ondblclick = toggleMax;

(function () {
  const grip = $("grip");
  if (!grip) return;
  let sx, sy, sw, sh, active = false, queued = false, nextW, nextH;
  const flush = () => {
    queued = false;
    const a = winApi();
    if (a && a.win_resize) a.win_resize(nextW, nextH);
  };
  grip.addEventListener("pointerdown", async (e) => {
    const a = winApi();
    if (!a) return;
    active = true;
    try { grip.setPointerCapture(e.pointerId); } catch (_) {}
    sx = e.screenX; sy = e.screenY;
    const sz = a.win_size ? await a.win_size()
                          : { w: window.outerWidth, h: window.outerHeight };
    sw = sz.w; sh = sz.h;
    e.preventDefault();
  });
  grip.addEventListener("pointermove", (e) => {
    if (!active) return;
    nextW = Math.max(480, sw + (e.screenX - sx));
    nextH = Math.max(360, sh + (e.screenY - sy));
    if (!queued) { queued = true; requestAnimationFrame(flush); }
  });
  const end = (e) => {
    active = false;
    try { grip.releasePointerCapture(e.pointerId); } catch (_) {}
  };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
})();
