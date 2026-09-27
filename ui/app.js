const $ = (id) => document.getElementById(id);
const root = document.documentElement;
const calm = matchMedia("(prefers-reduced-motion: reduce)");
let chosen = null;
let view = "idle";
// One job at a time until it ends in done, error or cancel: a second
// start() or bootstrap_env() would replace the job and end its processes.
let running = false;
let installing = false;

// Mono is one pass through these stages. Per-track instead steps through
// the participants (the runner transcribes each track in turn) then a
// merge, so its stages repeat per person and are demoted to a substatus on
// the active row.
const MONO_STEPS = ["Loading model", "Transcribing", "Diarizing"];
const MONO_ICONS = ["i-chip", "i-lines", "i-people"];
// backend.runner emits canonical phases. Map each to the mono row it
// lights (mono rows are the fixed stages; several phases share a row).
const MONO_ROW = {
  load_model: 0, transcribe: 1, load_diarize: 2, diarize: 2,
};
const SUBSTATUS = {
  load_model: "loading model…",
  transcribe: "transcribing…",
  load_diarize: "loading speaker model…",
  diarize: "detecting speakers…",
};

// The springs in app.css, shared with the Web Animations below.
const style = getComputedStyle(root);
const SPRING = {};
for (const k of ["smooth", "snappy", "bouncy"]) {
  SPRING[k] = {
    easing: style.getPropertyValue("--spring-" + k).trim(),
    duration: parseFloat(style.getPropertyValue("--spring-" + k + "-dur")),
  };
}

function api() {
  return (window.pywebview && window.pywebview.api) || null;
}

// A Python exception in an API call rejects its promise with the
// exception's type and message; shown the way backend/app.py words
// unexpected errors.
const failure = (e) => (e && e.name && e.name !== "Error" ? e.name + ": " : "")
  + ((e && e.message) || String(e));

// ---- small helpers ----
function svgUse(id, cls = "i") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", "#" + id);
  svg.appendChild(use);
  return svg;
}

// A check drawn as one stroke, so CSS can draw it in (pathLength 1).
function checkmark() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", "M6 12.6l3.9 3.9 8.2-8.5");
  path.setAttribute("pathLength", "1");
  svg.appendChild(path);
  return svg;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const pad2 = (n) => String(n).padStart(2, "0");
function fmtTime(sec) {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${pad2(m)}:${pad2(s % 60)}` : `${m}:${pad2(s % 60)}`;
}
const fmtInt = (n) => Number(n).toLocaleString("en-US");

// A client-side stopwatch for a running job (the backend sends no times).
function stopwatch(node) {
  let t0 = 0;
  let timer = 0;
  const tick = () => { node.textContent = fmtTime((performance.now() - t0) / 1000); };
  return {
    start() {
      clearInterval(timer);
      t0 = performance.now();
      tick();
      timer = setInterval(tick, 250);
    },
    stop() {
      if (!timer) return 0;
      clearInterval(timer);
      timer = 0;
      tick();
      return (performance.now() - t0) / 1000;
    },
  };
}
const runClock = stopwatch($("runClock"));
const setupClock = stopwatch($("setupClock"));

// ---- speakers: a stable color per name ----
// Apple's dark-mode system colors that stay distinct side by side (no
// red: it reads as an error; no teal or mint: too close to green and cyan).
const PALETTE = ["#0a84ff", "#30d158", "#ff9f0a", "#ff375f", "#bf5af2",
  "#ffd60a", "#64d2ff", "#5e5ce6"];
let colors = new Map();

function hash(s) {
  let h = 2166136261;
  for (const ch of s) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

// Each name keeps its hashed color unless a name earlier in sorted order
// took it; then it moves to the next free one. Sorting makes the choice
// the same for the ready card, the progress rows and the done preview.
function assignColors(names) {
  const used = new Set(colors.values());
  for (const name of [...new Set(names)].sort()) {
    if (colors.has(name)) continue;
    let i = hash(name) % PALETTE.length;
    for (let k = 0; k < PALETTE.length && used.has(PALETTE[i]); k++) {
      i = (i + 1) % PALETTE.length;
    }
    colors.set(name, PALETTE[i]);
    used.add(PALETTE[i]);
  }
}
const colorOf = (name) => colors.get(name) || PALETTE[hash(name) % PALETTE.length];

// A second track with the same name comes as "Alice (2)": initials "A2".
function initials(name) {
  const dup = /\s+\((\d+)\)$/.exec(name);
  const words = (dup ? name.slice(0, dup.index) : name).trim().split(/\s+/)
    .filter(Boolean);
  if (!words.length) return "?";
  const first = Array.from(words[0])[0];
  if (dup) return first.toUpperCase() + dup[1];
  const last = words.length > 1 ? Array.from(words[words.length - 1])[0] : "";
  return (first + last).toUpperCase();
}

function avatar(name, cls = "av") {
  const a = el("span", cls, initials(name));
  a.style.setProperty("--c", colorOf(name));
  return a;
}

// Zoom names meeting folders "2026-09-24 14.02.11 Topic": show the topic,
// with the start time underneath.
const ZOOM_DIR = /^(\d{4})-(\d{2})-(\d{2}) (\d{2})\.(\d{2})\.(\d{2}) (.+)$/;
const WHEN = new Intl.DateTimeFormat(undefined, {
  weekday: "short", month: "short", day: "numeric", year: "numeric",
  hour: "numeric", minute: "2-digit",
});

function describe(r) {
  const raw = r.title || r.name || "";
  const m = r.mode === "pertrack" ? ZOOM_DIR.exec(raw) : null;
  if (m) {
    const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
    if (!isNaN(d)) return { title: m[7], when: WHEN.format(d), raw };
  }
  if (r.mode === "mono") {
    const ext = /\.([A-Za-z0-9]{2,5})$/.exec(raw);
    if (ext && ext.index > 0) {
      return { title: raw.slice(0, ext.index), ext: ext[1].toUpperCase(), raw };
    }
  }
  return { title: raw, raw };
}

// ---- views ----
const VIEWS = ["idle", "setup", "running", "done", "errbox"];
let swapSeq = 0;

// Web Animations the views run; state changes cancel only these, never the
// CSS animations (rings, bars) inside.
function ours(node) {
  return node.getAnimations({ subtree: true }).filter((a) => a.id === "v");
}

function leave(node) {
  node.style.pointerEvents = "none";
  const a = node.animate(
    [{ opacity: 1, transform: "none" },
      { opacity: 0, transform: "translateY(-6px) scale(.985)" }],
    { duration: 130, easing: "cubic-bezier(.4, 0, 1, 1)", fill: "forwards",
      id: "v" });
  return a.finished.catch(() => {}).then(() => {
    node.style.pointerEvents = "";
  });
}

// Staggered entrance: each visible part rises into place on a spring
// while it fades in quickly, so a view never sits empty.
function rise(nodes, delay = 0, gap = 45, dist = 10) {
  if (calm.matches) return;
  nodes.forEach((n, i) => {
    const at = delay + i * gap;
    n.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: 260, easing: "cubic-bezier(.2, .7, .3, 1)", delay: at,
        fill: "backwards", id: "v" });
    n.animate([{ transform: `translateY(${dist}px)` }, { transform: "none" }],
      { duration: SPRING.smooth.duration, easing: SPRING.smooth.easing,
        delay: at, fill: "backwards", id: "v" });
  });
}

function visibleKids(node) {
  return [...node.children].filter((c) => c.getClientRects().length);
}

function enter(node) {
  ours(node).forEach((a) => a.cancel());
  if (calm.matches) {
    node.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: 160, easing: "ease-out", id: "v" });
    return;
  }
  const kids = visibleKids(node);
  rise(kids);
  const list = node.querySelector(".steps:not(.hidden)");
  if (list && list.getClientRects().length) {
    rise([...list.children].slice(0, 12), 80, 35, 6);
  }
}

// then(): runs once the view is on screen (its own entrance moments).
function show(which, focusEl, then) {
  const seq = ++swapSeq;
  const from = view !== which ? $(view) : null;
  view = which;
  const swap = () => {
    if (seq !== swapSeq) return;
    for (const id of VIEWS) $(id).classList.toggle("hidden", id !== which);
    document.body.dataset.view = which;
    fitWindow();
    enter($(which));
    if (then) then();
    if (focusEl) focusEl.focus({ preventScroll: true });
  };
  if (from && !from.classList.contains("hidden") && !calm.matches) {
    leave(from).then(swap);
  } else {
    swap();
  }
}

// While a job runs, the aurora behind the page brightens a little.
const setWorking = (on) => document.body.classList.toggle("working", on);

// ---- progress rows ----
// rows: [{label, icon?, name?, num?}]; a name makes a person row.
function buildSteps(ol, rows) {
  ol.textContent = "";
  // A new list: place the highlight without gliding in from the old one.
  ol.classList.remove("lit-row", "halt");
  ol.classList.add("snap");
  ol.style.setProperty("--i", 0);
  requestAnimationFrame(() => requestAnimationFrame(
    () => ol.classList.remove("snap")));
  for (const row of rows) {
    const li = el("li", "step todo");
    li.dataset.state = "todo";
    const lead = el("span", "lead");
    const glyph = el("span", "glyph");
    if (row.name != null) {
      li.classList.add("person");
      glyph.textContent = initials(row.name);
      glyph.style.setProperty("--c", colorOf(row.name));
    } else if (row.icon) {
      glyph.appendChild(svgUse(row.icon));
    } else {
      glyph.textContent = String(row.num);
      glyph.classList.add("num-glyph");
    }
    const tick = el("span", "tick");
    tick.appendChild(checkmark());
    lead.append(glyph, el("span", "ring"), tick);
    const body = el("span", "body");
    const bar = el("span", "bar");
    bar.appendChild(el("i"));
    body.append(el("span", "lbl", row.label), el("span", "ssub"), bar);
    li.append(lead, body, el("span", "trail"));
    ol.appendChild(li);
  }
}

function renderSteps(mode) {
  if (mode === "pertrack") {
    const who = (chosen && chosen.speakers) || [];
    buildSteps($("steps"), [
      ...who.map((name) => ({ label: name, name })),
      { label: "Merge transcripts", icon: "i-merge" },
    ]);
  } else {
    buildSteps($("steps"), MONO_STEPS.map((label, i) => ({
      label, icon: MONO_ICONS[i] })));
  }
}

// The runner sends a real pct only for phases that have a genuine hook
// (transcribe/align/diarize). Determinate iff a pct is present; load/
// vad/write carry none -> indeterminate sweep (honest, no faking).
function barOf(stage, pct) {
  return pct != null ? pct : null;
}

function markStep(ol, activeIdx, substatus, barPct) {
  const steps = ol.children;
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i];
    const state = i < activeIdx ? "done" : i === activeIdx ? "active" : "todo";
    if (s.dataset.state !== state) {
      s.classList.remove("done", "active", "todo", "failed");
      s.classList.add(state);
      s.dataset.state = state;
      if (state === "active") {
        ol.style.setProperty("--i", i);
        ol.classList.add("lit-row");
      }
    }
    const sub = s.querySelector(".ssub");
    const said = (i === activeIdx && substatus) ? substatus : "";
    // Mono's "Transcribing" row doesn't need "transcribing…" under it.
    const echo = said.replace(/…$/, "").toLowerCase()
      === s.querySelector(".lbl").textContent.toLowerCase();
    sub.textContent = echo ? "" : said;
    const trail = s.querySelector(".trail");
    if (i !== activeIdx) {
      trail.textContent = "";
      continue;
    }
    const bw = s.querySelector(".bar");
    const fill = bw.firstElementChild;
    if (typeof barPct === "number") {
      const v = Math.max(0, Math.min(100, barPct));
      bw.classList.add("det");
      fill.style.width = v + "%";
      trail.textContent = Math.floor(v) + "%";
    } else {
      bw.classList.remove("det");
      fill.style.width = "";
      trail.textContent = "";
    }
    // keep the active participant visible when many tracks overflow;
    // "nearest" is a no-op when it's already on screen (no jank).
    if (s.dataset.seen !== "1") {
      s.dataset.seen = "1";
      s.scrollIntoView({ block: "nearest",
        behavior: calm.matches || view !== ol.closest("section").id
          ? "auto" : "smooth" });
    }
  }
}

// First-run installer (frozen exe only). Phases come over the SAME
// progress protocol as transcription, discriminated by mode:"setup".
const SETUP_STEPS = ["Check GPU", "Prepare", "Download Python",
  "Download ffmpeg", "Create environment", "Install dependencies",
  "Download speech model", "Test GPU"];
const SETUP_ROW = {
  gpu_check: 0, prepare: 1, fetch_python: 2, fetch_ffmpeg: 3,
  make_venv: 4, pip: 5, fetch_models: 6, verify: 7,
};
const SETUP_SUB = {
  gpu_check: "checking…", prepare: "preparing…",
  fetch_python: "downloading…", fetch_ffmpeg: "downloading…",
  make_venv: "creating venv…", pip: "installing… (the long step)",
  fetch_models: "downloading…", verify: "transcribing a test clip…",
};

function setupProgress(p) {
  if (p.stage === "ready") {           // env built in this session
    installing = false;
    setupClock.stop();
    setWorking(false);
    resetIdle();
    return;
  }
  if (p.stage === "failed") {
    setupStopped(p.msg || "Setup failed.");
    return;
  }
  const row = SETUP_ROW[p.stage];
  if (row == null) return;
  let sub = SETUP_SUB[p.stage] || "working…";
  if (p.stage === "pip" && p.msg) sub = p.msg.slice(0, 90);
  const ol = $("setupSteps");
  // models.fetch reports each of the model's files from 0 to 100, so that
  // step has no single number to show.
  const pct = p.stage === "fetch_models" ? null : p.pct;
  markStep(ol, row, sub, barOf(p.stage, pct));
  ol.children[row].querySelector(".ssub").classList.toggle(
    "code", p.stage === "pip" && !!p.msg);
}

// Setup stopped: it failed with msg, or it was cancelled (msg null).
function setupStopped(msg) {
  installing = false;
  setupClock.stop();
  setWorking(false);
  const cancelled = msg == null;
  const s = $("setup");
  s.classList.add("failed");
  s.classList.toggle("calm", cancelled);
  $("setupTitle").textContent = cancelled ? "Setup cancelled"
    : "Setup didn't finish";
  $("setupSub").textContent =
    "Finished downloads and installs are skipped when you try again.";
  // The row that was running stops: marked where it failed, or simply
  // back to waiting after a cancel.
  const active = $("setupSteps").querySelector(".step.active");
  if (active) {
    active.querySelector(".ssub").textContent = cancelled ? "" : "stopped here";
    active.querySelector(".trail").textContent = "";
    const state = cancelled ? "todo" : "failed";
    active.classList.replace("active", state);
    active.dataset.state = state;
    if (cancelled) {
      $("setupSteps").classList.remove("lit-row");
    } else {
      $("setupSteps").classList.add("halt");
      active.querySelector(".glyph").replaceChildren(svgUse("i-alert"));
    }
  }
  if (!cancelled) showMessage($("setupErrMsg"), $("setupLog"), msg);
  $("setupErr").classList.toggle("hidden", cancelled);
  $("setupCancel").classList.add("hidden");
  $("setupRetry").classList.remove("hidden");
  fitWindow();
  rise(cancelled ? [$("setupRetry")] : [$("setupErr"), $("setupRetry")], 0, 50);
  $("setupRetry").focus({ preventScroll: true });
}

// Failures end with the log they wrote ("Log: <path>", or "Setup log:
// <path>"); that line shows as a detail under the message, not in it.
const LOG_LINE = /\n\n((?:Setup )?[Ll]og): ([^\n]+?)\s*$/;
function showMessage(msgNode, logNode, text) {
  const m = LOG_LINE.exec(text);
  msgNode.textContent = m ? text.slice(0, m.index) : text;
  logNode.textContent = "";
  logNode.classList.toggle("hidden", !m);
  if (m) logNode.append(svgUse("i-doc"), el("span", null, m[1] + ": " + m[2]));
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
    onDone(payload);
  } else if (channel === "error") {
    onError(String(payload));
  } else if (channel === "cancelled") {
    if (payload === "setup") setupStopped(null);
    else onCancelled();
  } else if (channel === "maxed") {
    // However the window got there: the button, Win+Up, a snap.
    showMaxed(!!payload);
    if (!payload) refitSoon();
  }
};

// ---- done ----
function onDone(p) {
  running = false;
  const took = runClock.stop();
  setWorking(false);
  const speakers = p.speakers || [];
  assignColors(speakers);
  const d = chosen ? describe(chosen) : null;
  $("doneSub").textContent = [d && d.title,
    took >= 1 ? "done in " + fmtTime(took) : ""].filter(Boolean).join(" · ");
  const stats = $("stats");
  stats.textContent = "";
  for (const [value, label] of [
    [String(p.duration), "Duration"],
    [String(speakers.length), speakers.length === 1 ? "Speaker" : "Speakers"],
    [fmtInt(p.turns), "Turns"],
    ["~" + fmtInt(p.approx_tokens), "Tokens"],
  ]) {
    const tile = el("div", "stat");
    const b = el("b");
    digits(b, value);
    tile.append(b, el("span", "lab", label));
    stats.appendChild(tile);
  }
  const path = $("outpath");
  path.textContent = "";
  path.append(svgUse("i-folder"), el("span", null, p.output_path));
  resetCopy();
  loadPreview();
  show("done", $("copy"), celebrate);
}

// Digits sit in 0-9 strips so they can roll into place (celebrate()).
function digits(node, text) {
  node.textContent = "";
  node.setAttribute("aria-label", text);
  for (const ch of text) {
    if (ch >= "0" && ch <= "9") {
      const d = el("span", "dig");
      d.setAttribute("aria-hidden", "true");
      const strip = el("span");
      for (let k = 0; k <= 9; k++) strip.appendChild(el("span", null, String(k)));
      strip.style.transform = `translateY(${-ch * 10}%)`;
      strip.dataset.to = ch;
      d.appendChild(strip);
      node.appendChild(d);
    } else {
      const c = el("span", null, ch);
      c.setAttribute("aria-hidden", "true");
      node.appendChild(c);
    }
  }
}

function celebrate() {
  const done = $("done");
  done.classList.remove("enter");
  void done.offsetWidth;               // restart the check's CSS animations
  done.classList.add("enter");
  if (calm.matches) return;
  let k = 0;
  for (const strip of done.querySelectorAll(".dig > span")) {
    const to = -strip.dataset.to * 10;
    strip.animate(
      [{ transform: "translateY(0%)" }, { transform: `translateY(${to}%)` }],
      { duration: 900, easing: "cubic-bezier(.16, 1, .3, 1)",
        delay: 260 + k++ * 30, fill: "backwards", id: "v" });
  }
}

function parseTurns(text) {
  const at = text.indexOf("\n---\n");
  if (at < 0) return [];
  const turns = [];
  for (const line of text.slice(at + 5).split("\n")) {
    const m = /^\[(\d+(?::\d\d){1,2})\] (.+?): (.+)$/.exec(line);
    if (m) turns.push({ ts: m[1], who: m[2], said: m[3] });
    if (turns.length === 5) break;
  }
  return turns;
}

// The first turns of the transcript the run just wrote, read back through
// the backend. Space for them is kept from the start, so the window doesn't
// jump when they arrive.
let previewSeq = 0;
async function loadPreview() {
  const seq = ++previewSeq;
  const box = $("preview");
  const body = $("pvBody");
  body.textContent = "";
  box.classList.remove("hidden");
  box.style.visibility = "hidden";
  let turns = [];
  try {
    const r = await api().copy_transcript();
    if (r && r.ok) turns = parseTurns(r.text);
  } catch (_) { /* no preview */ }
  if (seq !== previewSeq) return;
  if (!turns.length) {
    box.classList.add("hidden");
    box.style.visibility = "";
    fitWindow();
    return;
  }
  assignColors(turns.map((t) => t.who));
  for (const t of turns) {
    const who = el("span", "who", t.who);
    who.style.color = colorOf(t.who);
    body.append(el("span", "ts", t.ts), who, el("span", "said", t.said));
  }
  box.style.visibility = "";
  if (!calm.matches && view === "done") {
    box.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: 300, easing: "ease-out", id: "v" });
  }
}

let copyTimer = 0;
// state: "" | "copied" | "nocopy". Only the label on screen is in the
// button's name (aria-hidden must say "true": an empty value hides nothing).
function setCopy(state) {
  const b = $("copy");
  b.classList.toggle("copied", state === "copied");
  b.classList.toggle("nocopy", state === "nocopy");
  const shown = { copied: "l2", nocopy: "l3" }[state] || "l1";
  for (const label of b.querySelectorAll(".lbl > span")) {
    if (label.classList.contains(shown)) label.removeAttribute("aria-hidden");
    else label.setAttribute("aria-hidden", "true");
  }
}
function resetCopy() {
  clearTimeout(copyTimer);
  setCopy("");
}

async function copyOut() {
  let copied = false;
  try {
    const r = await api().copy_transcript();
    if (r && r.ok) {
      await navigator.clipboard.writeText(r.text);
      copied = true;
    }
  } catch (_) { /* shown as a copy that didn't happen */ }
  setCopy(copied ? "copied" : "nocopy");
  clearTimeout(copyTimer);
  copyTimer = setTimeout(resetCopy, copied ? 1800 : 2400);
}
$("copy").onclick = copyOut;
$("openf").onclick = () => api().open_folder().catch(() => {});

// A refused entry shakes once, the way a wrong password does.
const SHAKE = [{ translate: "0" }, { translate: "-9px" }, { translate: "8px" },
  { translate: "-5px" }, { translate: "3px" }, { translate: "0" }];
function shake(node, delay = 0) {
  if (calm.matches) return;
  node.animate(SHAKE, { duration: 460, delay, easing: "ease-out", id: "v" });
}

// ---- error and cancel ----
function onError(msg) {
  running = false;
  runClock.stop();
  setWorking(false);
  const box = $("errbox");
  box.classList.remove("calm");
  $("errTitle").textContent = "Couldn't transcribe";
  $("errSub").textContent = chosen ? describe(chosen).title : "";
  showMessage($("errmsg"), $("errlog"), msg);
  $("errmsg").scrollTop = 0;
  show("errbox", $("errback"), () => shake(box.querySelector(".head"), 200));
}

// A cancel isn't an error: a calm screen with no message and no shake.
function onCancelled() {
  running = false;
  runClock.stop();
  setWorking(false);
  $("errbox").classList.add("calm");
  $("errTitle").textContent = "Cancelled";
  $("errSub").textContent = "The transcription stopped before it finished.";
  $("errmsg").textContent = "";
  $("errlog").classList.add("hidden");
  show("errbox", $("errback"));
}

// ---- choosing a recording ----
let picking = false;
async function doPick() {
  const a = api();
  if (!a || picking) return;
  picking = true;
  let r = null;
  try {
    r = await a.pick_input();
  } catch (_) {
    /* the dialog failed: nothing was chosen */
  } finally {
    picking = false;
  }
  applyChoice(r);
}

async function applyChoice(r) {
  if (!r) return;
  const first = $("ready").classList.contains("hidden");
  chosen = r;
  colors = new Map();
  assignColors(r.speakers || []);
  const d = describe(r);
  $("rTitle").textContent = d.title;
  $("rTitle").title = d.raw;
  $("rWhen").textContent = d.when || "";
  const icon = $("rIcon");
  icon.textContent = "";
  icon.appendChild(svgUse(r.mode === "pertrack" ? "i-people"
    : r.mode === "mono" ? "i-wave" : "i-question"));
  icon.classList.toggle("unknown", r.mode !== "pertrack" && r.mode !== "mono");
  // An unrecognized file has one way forward, the button under the card.
  $("change").classList.toggle("hidden",
    r.mode !== "pertrack" && r.mode !== "mono");

  const chips = $("rChips");
  chips.textContent = "";
  const chip = (text, cls, iconId) => {
    const c = el("span", "chip" + (cls ? " " + cls : ""));
    if (iconId) c.appendChild(svgUse(iconId));
    c.appendChild(document.createTextNode(text));
    chips.appendChild(c);
  };
  const people = $("rPeople");
  const summary = $("rSummary");
  summary.textContent = "";
  if (r.mode === "pertrack") {
    const n = r.speakers.length;
    chip("Per-participant recording");
    chip(n + (n === 1 ? " person" : " people"));
    chip("No token needed", "good", "i-check");
    const avs = $("rAvatars");
    avs.textContent = "";
    const shown = n > 6 ? r.speakers.slice(0, 5) : r.speakers;
    for (const name of shown) avs.appendChild(avatar(name));
    if (n > shown.length) avs.appendChild(el("span", "av more", "+" + (n - shown.length)));
    // A name never breaks across lines; a long list ends in "and 6 more".
    const keep = (name) => name.replace(/ /g, "\u00a0");
    const names = n > 6
      ? [...r.speakers.slice(0, 5).map(keep), `${n - 5}\u00a0more`]
      : r.speakers.map(keep);
    const list = new Intl.ListFormat("en", { type: "conjunction" }).format(names);
    $("rNames").textContent = n > 6 ? list + "."
      : list + (n === 1 ? ", transcribed from their own track."
        : ", each transcribed from their own track.");
    $("rNames").title = r.speakers.join("\n");
    people.classList.remove("hidden");
  } else if (r.mode === "mono") {
    chip("Mixed recording");
    if (d.ext) chip(d.ext);
    chip("Speakers detected automatically");
    people.classList.add("hidden");
  } else {
    people.classList.add("hidden");
    // The backend writes this reason for the user.
    summary.append(svgUse("i-alert"), el("span", null,
      r.reason || "Pick an audio or video recording."));
  }
  const known = r.mode === "pertrack" || r.mode === "mono";
  setTokenMsg("");

  if (first) {
    // The card and its button appear together once the token state is
    // known, so nothing shows early or pops in late.
    const out = $("pick");
    await Promise.all([renderToken(false, false),
      calm.matches ? null : leave(out)]);
    if (chosen !== r) return;
    ours(out).forEach((a) => a.cancel());
    out.classList.add("hidden");
    $("ready").classList.remove("hidden");
    $("go").classList.toggle("hidden", !known);
    $("pickAgain").classList.toggle("hidden", known);
    fitWindow();
    rise([$("ready"), $("go"), $("pickAgain")]
      .filter((n) => n.getClientRects().length), 0, 70);
    rise(visibleKids($("ready")), 60, 40, 6);
  } else {
    $("go").classList.toggle("hidden", !known);
    $("pickAgain").classList.toggle("hidden", known);
    await renderToken(false);
    if (chosen !== r) return;
    rise(visibleKids($("ready")), 0, 35, 5);
  }
  const go = $("go");
  const target = !go.classList.contains("hidden") && !go.disabled ? go
    : !known ? $("pickAgain")
      : !$("tokenForm").classList.contains("hidden") ? $("tokenInput")
        : $("change");
  target.focus({ preventScroll: true });
}

// Mixed recordings need a Hugging Face token. The backend reports only where
// the token comes from, never the token itself.
const TOKEN_MSG = {
  ok: "Saved. The token works for speaker detection.",
  terms: "Saved, but this account hasn't accepted the model's terms yet. "
    + "Do step 1, then Transcribe.",
  unverified: "Saved. Couldn't reach Hugging Face to check it; it's checked "
    + "again when you transcribe.",
  format: "That doesn't look like a Hugging Face token (they start with hf_).",
  invalid: "Hugging Face rejected that token. Create a new read token "
    + "(step 2) and paste it.",
  store: "Couldn't save the token in Windows Credential Manager.",
  error: "Couldn't save the token.",
};
const TOKEN_TONE = {
  ok: "ok", terms: "warn", unverified: "", format: "bad", invalid: "bad",
  store: "bad", error: "bad",
};

function setTokenMsg(text, tone = "") {
  const m = $("tokenMsg");
  m.className = "tmsg" + (tone ? " " + tone : "");
  m.textContent = "";
  if (!text) return;
  if (tone === "busy") m.appendChild(el("span", "spin"));
  else if (tone === "ok") m.appendChild(svgUse("i-check"));
  else if (tone === "warn" || tone === "bad") m.appendChild(svgUse("i-alert"));
  m.appendChild(el("span", null, text));
}

async function renderToken(showForm, fit = true) {
  const needs = !!(chosen && chosen.needs_token);
  $("token").classList.toggle("hidden", !needs);
  if (!needs) {
    $("go").disabled = false;
    if (fit) fitWindow();
    return;
  }
  let st = { source: null };
  try {
    st = await api().hf_token_status();
  } catch (_) { /* treated as no token */ }
  const have = !!st.source;
  $("token").classList.toggle("have", have);
  $("tokenState").textContent = !have
    ? "Speaker detection needs a free Hugging Face token."
    : st.source === "saved" ? "Hugging Face token saved."
      : "Using the HF_TOKEN environment variable.";
  $("tokenChange").classList.toggle("hidden", !have || showForm);
  $("tokenForm").classList.toggle("hidden", have && !showForm);
  $("tokenForget").classList.toggle("hidden", st.source !== "saved");
  $("go").disabled = !have;
  if (fit) fitWindow();
}

$("tokenChange").onclick = async () => {
  setTokenMsg("");
  await renderToken(true);
  rise([$("tokenForm")], 0, 0, 6);
  $("tokenInput").focus({ preventScroll: true });
};
$("hfTerms").onclick = () => api().open_link("hf_terms").catch(() => {});
$("hfTokens").onclick = () => api().open_link("hf_tokens").catch(() => {});
$("tokenSave").onclick = async () => {
  const input = $("tokenInput");
  const raw = input.value;
  input.value = "";
  $("tokenSave").disabled = true;
  setTokenMsg("Checking the token…", "busy");
  fitWindow();
  let r;
  try {
    r = await api().hf_token_save(raw);
  } catch (_) {
    r = { ok: false, status: "error" };
  } finally {
    $("tokenSave").disabled = false;
  }
  setTokenMsg(TOKEN_MSG[r.status] || TOKEN_MSG.error,
    TOKEN_TONE[r.status] ?? "bad");
  // "terms" saved the token but asks for step 1, so the steps stay open.
  const terms = r.status === "terms";
  await renderToken(!r.ok || terms);
  if (!r.ok) shake(document.querySelector(".trow"));
  if (terms) $("hfTerms").focus({ preventScroll: true });
  else if (r.ok && !$("go").disabled) $("go").focus({ preventScroll: true });
  else if (!r.ok) input.focus({ preventScroll: true });
};
$("tokenInput").onkeydown = (e) => {
  if (e.key === "Enter") $("tokenSave").click();
};
$("tokenForget").onclick = async () => {
  try {
    await api().hf_token_clear();
  } catch (_) { /* the status below says what is left */ }
  setTokenMsg("");
  await renderToken(true);
};

function resetIdle() {
  chosen = null;
  colors = new Map();
  $("pick").classList.remove("hidden");
  for (const id of ["ready", "go", "pickAgain", "token"]) {
    $(id).classList.add("hidden");
  }
  $("go").disabled = false;
  setTokenMsg("");
  $("steps").textContent = "";
  show("idle", $("pick"));
}

// Back from an error: the same recording, ready to try again.
function backToReady() {
  if (!chosen) {
    resetIdle();
    return;
  }
  setTokenMsg("");
  show("idle", $("go").disabled || $("go").classList.contains("hidden")
    ? $("change") : $("go"));
  renderToken(false);
}

$("pick").onclick = doPick;
$("change").onclick = doPick;
$("pickAgain").onclick = doPick;

$("go").onclick = () => {
  if (!chosen || running) return;
  running = true;
  renderSteps(chosen.mode);
  // pertrack rows are people: show what the first one is doing now
  // (model load) instead of a blank pulse until backend events arrive.
  markStep($("steps"), 0,
    chosen.mode === "pertrack" ? "loading model…" : undefined);
  const d = describe(chosen);
  const n = (chosen.speakers || []).length;
  $("runSub").textContent = chosen.mode === "pertrack"
    ? d.title + " · " + n + (n === 1 ? " participant" : " participants")
    : d.title;
  $("cancel").disabled = false;
  $("cancel").textContent = "Cancel";
  runClock.start();
  setWorking(true);
  show("running");
  // The backend starts the recording pick_input returned. start() can fail
  // before its worker exists; no event would follow, so its error shows here.
  api().start().catch((e) => onError(failure(e)));
};

$("cancel").onclick = () => {
  $("cancel").disabled = true;
  $("cancel").textContent = "Cancelling…";
  api().cancel().catch(() => {
    $("cancel").disabled = false;
    $("cancel").textContent = "Cancel";
  });
};
$("again").onclick = resetIdle;
$("errback").onclick = backToReady;

// ---- first-run installer wiring ----
// Before offering Install, the page asks whether setup would stop at its
// first checks (the GPU, its driver, free space). The answer takes a
// fraction of a second, so Install shows at once, and the reason takes its
// place only when there is one.
let setupAnswer = Promise.resolve(null);  // why setup can't start, or null

function askSetup() {
  const a = api();
  setupAnswer = (a && a.setup_check ? a.setup_check() : Promise.resolve(null))
    .then((r) => (r && r.reason) || null, () => null);
  return setupAnswer;
}

// reason: why setup can't start, or null to offer Install.
function setBlock(reason) {
  const blocked = reason != null;
  $("setupIntro").classList.toggle("hidden", blocked);
  $("setupStart").classList.toggle("hidden", blocked);
  $("setupAgain").classList.toggle("hidden", !blocked);
  const box = $("setupBlock");
  box.classList.toggle("hidden", !blocked);
  box.textContent = "";
  if (blocked) box.append(svgUse("i-alert"), el("span", null, reason));
}

// An answer came back: the reason replaces Install, or after Try again,
// Install comes back. A check that fails again shakes, like a refused entry.
// Focus moves only after Try again, so nothing lights up before a click.
function answered(answer, reason, retried) {
  if (answer !== setupAnswer || view !== "setup" || installing) return;
  if (reason == null && !retried) return;   // Install is already showing
  const wasBlocked = !$("setupBlock").classList.contains("hidden");
  setBlock(reason);
  fitWindow();
  if (reason == null) {
    rise([$("setupIntro"), $("setupStart")], 0, 50);
  } else if (wasBlocked) {
    shake($("setupBlock"));
  } else {
    rise([$("setupBlock"), $("setupAgain")], 0, 50);
  }
  if (retried) {
    (reason == null ? $("setupStart") : $("setupAgain"))
      .focus({ preventScroll: true });
  }
}

function renderSetup() {
  $("setupSteps").textContent = "";
  const s = $("setup");
  s.classList.remove("installing", "failed", "calm");
  $("setupErr").classList.add("hidden");
  $("setupCancel").classList.add("hidden");
  $("setupRetry").classList.add("hidden");
  setBlock(null);
}

function startSetup() {
  if (installing) return;
  installing = true;
  buildSteps($("setupSteps"), SETUP_STEPS.map((label, i) => ({
    label, num: i + 1 })));
  markStep($("setupSteps"), 0, "starting…");
  const s = $("setup");
  const first = !s.classList.contains("installing");
  s.classList.add("installing");
  s.classList.remove("failed", "calm");
  $("setupTitle").textContent = "Installing";
  $("setupSub").textContent = "One-time setup of the speech engine";
  $("setupErr").classList.add("hidden");
  $("setupRetry").classList.add("hidden");
  $("setupCancel").classList.remove("hidden");
  $("setupCancel").disabled = false;
  $("setupCancel").textContent = "Cancel";
  setupClock.start();
  setWorking(true);
  fitWindow();
  if (first) enter(s);
  api().bootstrap_env().catch((e) => setupProgress({
    mode: "setup", stage: "failed", msg: failure(e) }));
}
// Clicked before the answer came back: wait for it (the button says so if
// that takes a while); answered() shows the reason if there is one.
$("setupStart").onclick = async () => {
  const b = $("setupStart");
  if (installing || b.disabled) return;
  b.disabled = true;
  const label = b.firstElementChild;
  const slow = setTimeout(() => { label.textContent = "Checking…"; }, 400);
  const reason = await setupAnswer;
  clearTimeout(slow);
  label.textContent = "Install";
  b.disabled = false;
  if (reason == null) startSetup();
};
$("setupAgain").onclick = async () => {
  const b = $("setupAgain");
  if (b.disabled) return;
  b.disabled = true;
  b.textContent = "Checking…";
  const answer = askSetup();
  const reason = await answer;
  b.disabled = false;
  b.textContent = "Try again";
  answered(answer, reason, true);
};
$("setupRetry").onclick = startSetup;
$("setupCancel").onclick = () => {
  $("setupCancel").disabled = true;
  $("setupCancel").textContent = "Cancelling…";
  api().cancel().catch(() => {
    $("setupCancel").disabled = false;
    $("setupCancel").textContent = "Cancel";
  });
};

// The page stays empty until it knows which view comes first: a first run
// used to show the drop panel for about a tenth of a second on its way to
// setup. If pywebview never answers, the drop panel comes anyway.
let booted = false;
function boot(setup) {
  if (booted) return;
  booted = true;
  document.body.classList.remove("booting");
  if (setup) $("idle").classList.add("hidden");   // setup enters without a swap
  else rise([$("pick")], 60, 0, 8);              // the drop panel settles in
}
const bootLate = setTimeout(() => boot(false), 2000);

window.addEventListener("pywebviewready", async () => {
  let st = null;
  try {
    st = await api().env_status();
  } catch (_) { /* old/dev shell without env_status: behave as ready */ }
  const setup = !!(st && st.ready === false);
  clearTimeout(bootLate);
  boot(setup);
  if (setup) {
    // First run: install before anything else.
    renderSetup();
    const answer = askSetup();
    show("setup", null, () => answer.then((r) => answered(answer, r, false)));
  }
});

// ---- keyboard ----
document.addEventListener("keydown", (e) => {
  const key = e.key.toLowerCase();
  if (e.ctrlKey && !e.altKey && !e.shiftKey && key === "o" && view === "idle") {
    e.preventDefault();
    doPick();
  } else if (e.ctrlKey && !e.altKey && !e.shiftKey && key === "c"
             && view === "done" && !String(getSelection())) {
    e.preventDefault();
    copyOut();
  }
});

// ---- pointer light on glass ----
// Pointer position feeds the specular highlight; the drop panel also
// tilts a few degrees toward the pointer, like a focused Apple TV card.
for (const node of document.querySelectorAll(".lit")) {
  node.addEventListener("pointermove", (e) => {
    const r = node.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    node.style.setProperty("--mx", x + "px");
    node.style.setProperty("--my", y + "px");
    if (!calm.matches && node.classList.contains("drop")) {
      node.style.setProperty("--ry", ((x / r.width - 0.5) * 3).toFixed(2) + "deg");
      node.style.setProperty("--rx", ((0.5 - y / r.height) * 3).toFixed(2) + "deg");
    }
  });
  node.addEventListener("pointerleave", () => {
    node.style.setProperty("--rx", "0deg");
    node.style.setProperty("--ry", "0deg");
  });
}

// ---- custom (frameless) window chrome ----
// Measuring un-scrolls the lists, so their positions are put back.
function fitWindow() {
  const a = api();
  if (!a || !a.win_fit) return;
  const lists = [...document.querySelectorAll(".steps, .msg")];
  const tops = lists.map((n) => n.scrollTop);
  root.classList.add("measure");
  const need = document.body.getBoundingClientRect().height;
  root.classList.remove("measure");
  lists.forEach((n, i) => { n.scrollTop = tops[i]; });
  a.win_fit(Math.ceil(need * window.devicePixelRatio));
}

$("btnMin").onclick = () => { const a = api(); if (a && a.win_minimize) a.win_minimize(); };
$("btnClose").onclick = () => { const a = api(); if (a && a.win_close) a.win_close(); };

function showMaxed(maxed) {
  const b = $("btnMax");
  b.querySelector(".imax").classList.toggle("hidden", maxed);
  b.querySelector(".irestore").classList.toggle("hidden", !maxed);
  b.title = maxed ? "Restore" : "Maximize";
  b.setAttribute("aria-label", b.title);
}

// The "maxed" event reports the result (and re-fits on a restore).
async function toggleMax() {
  const a = api();
  if (!a || !a.win_toggle_max) return;
  try {
    showMaxed(await a.win_toggle_max());
  } catch (_) { /* nothing changed */ }
}
$("btnMax").onclick = toggleMax;
$("tbdrag").ondblclick = toggleMax;

// win_fit leaves a maximized or minimized window alone, so a view that
// changed meanwhile (a run finishing while minimized) is fitted once the
// window is back: when it reports it's no longer maximized, or the page is
// visible again; after the restore's resize lands, or a moment later.
function refitSoon() {
  let done = false;
  const go = () => {
    if (done) return;
    done = true;
    window.removeEventListener("resize", go);
    requestAnimationFrame(fitWindow);
  };
  window.addEventListener("resize", go);
  setTimeout(go, 300);
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refitSoon();
});

// Dropped files would open in their default app; the page takes none.
window.addEventListener("dragover", (e) => {
  e.preventDefault();
  e.dataTransfer.dropEffect = "none";
});
window.addEventListener("drop", (e) => e.preventDefault());

// Like Windows' caption buttons, these never take the focus (and index.html
// keeps them out of the Tab order), so a click leaves it where it was.
$("wbtns").onmousedown = (e) => e.preventDefault();

// Minimized from its button and later restored, a caption button kept its
// hover look: Chromium never saw the pointer leave the window.
window.addEventListener("blur", () => $("wbtns").classList.add("nohover"));

// Like a Mac window, the title quiets while another window is active.
window.addEventListener("blur", () => document.body.classList.add("inactive"));
window.addEventListener("focus",
  () => document.body.classList.remove("inactive"));
document.addEventListener("pointermove",
  () => $("wbtns").classList.remove("nohover"));

// Focus rings only while moving through the page with Tab. Chromium also
// draws one on a clicked button after a later key press, such as a letter.
document.addEventListener("pointerdown",
  () => root.classList.add("pointer"), true);
document.addEventListener("keydown", (e) => {
  if (e.key === "Tab") root.classList.remove("pointer");
}, true);

(function () {
  const grip = $("grip");
  if (!grip) return;
  let sx, sy, sw, sh, down = false, active = false, queued = false;
  let nextW, nextH;
  const flush = () => {
    queued = false;
    const a = api();
    if (a && a.win_resize) a.win_resize(nextW, nextH);
  };
  grip.addEventListener("pointerdown", async (e) => {
    const a = api();
    if (!a) return;
    e.preventDefault();         // before the await, or it comes too late
    down = true;
    try { grip.setPointerCapture(e.pointerId); } catch (_) {}
    sx = e.screenX; sy = e.screenY;
    const sz = a.win_size ? await a.win_size()
                          : { w: window.outerWidth, h: window.outerHeight };
    sw = sz.w; sh = sz.h;
    active = down;              // resize only with the window's size known
  });
  grip.addEventListener("pointermove", (e) => {
    if (!active) return;
    nextW = Math.max(480, sw + (e.screenX - sx));
    nextH = Math.max(360, sh + (e.screenY - sy));
    if (!queued) { queued = true; requestAnimationFrame(flush); }
  });
  const end = (e) => {
    down = active = false;
    try { grip.releasePointerCapture(e.pointerId); } catch (_) {}
  };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
})();
