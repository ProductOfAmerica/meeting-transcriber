// Stand-in for backend/app.py's Api (pywebview's js_api), so the real ui/
// runs in a plain browser. tests/ui/rig.py loads it before app.js; it then
// fires "pywebviewready", like pywebview does.
//
// Events go through window.__on exactly as backend/app.py sends them, with
// the same payload shapes (pct is a float or null; mono progress carries
// track 1 once the input starts; pertrack's load_model carries no track).
// The flows prove the page works against what this file says the backend
// does, so keep it in step with Api and its events.
(function () {
  if (window.__mock) return;

  const HOME = "C:\\Users\\you\\AppData\\Local\\Transcribe";
  const OUT = "C:\\Users\\you\\Transcripts\\";

  const PEOPLE = ["Maya Chen", "Jordan Ellis", "Priya Raman", "Tomás Herrera"];

  // What pick_input returns. The paths stay in Python.
  const PICKS = {
    pertrack: {
      name: "audioMayaChen11721414883.m4a", mode: "pertrack",
      reason: "per-participant recording (Audio Record found via the "
        + "chosen file's folder)",
      title: "2026-09-24 14.02.11 Weekly design review", speakers: PEOPLE,
      needs_token: false,
    },
    mono: {
      name: "Quarterly planning.mp4", mode: "mono",
      reason: "single media file", title: "Quarterly planning.mp4",
      speakers: [], needs_token: true,
    },
    unknown: {
      name: "notes.pdf", mode: "ask",
      reason: "Transcribe can't transcribe .pdf files. Pick an audio or "
        + "video recording, such as .m4a, .mp3, .wav or .mp4.",
      title: "notes.pdf", speakers: [], needs_token: false,
    },
  };

  // Full-length meetings, identical on every load (seeded): turns spread
  // over the whole duration, so transcript views get realistic data. The
  // done payloads are computed from the same text, as backend/transcript.py
  // would.
  const SAID = [
    "Can everyone see my screen?",
    "Let's look at the numbers from last week first.",
    "I think that's the right call, but we should check with support.",
    "The first version shipped on Tuesday.",
    "We're still waiting on the vendor for the final quote.",
    "That matches what we heard in the customer calls.",
    "I'd rather cut scope than move the date again.",
    "Can you share the doc after this?",
    "Two of the five teams have already migrated.",
    "The error rate dropped once we added the retry.",
    "Let me pull up the dashboard.",
    "I'm not sure the survey sample is big enough to tell.",
    "Good point. Let's park that for the next meeting.",
    "We tried that in March and it didn't stick.",
    "Honestly, the onboarding copy is the bigger problem.",
    "I'll follow up with legal today.",
    "The prototype is on the shared drive.",
    "What's the risk if we wait a sprint?",
    "Mostly the partner launch, which is fixed.",
    "Okay, so we agree on the first two items.",
    "I can take the analytics part.",
    "Budget is fine for this quarter.",
    "Let's make sure design reviews it before we build anything.",
    "It took about four seconds on the old laptop.",
    "That's a fair question.",
    "Right.",
    "Yes, exactly.",
    "Sounds good to me.",
    "We should write that down as a decision.",
    "Next week works better for me.",
  ];

  function stamp(sec) {                      // backend/transcript.py ts()
    sec = Math.round(sec);
    const h = Math.floor(sec / 3600);
    const m = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
    const s = String(sec % 60).padStart(2, "0");
    return h ? `${h}:${m}:${s}` : `${m}:${s}`;
  }

  function meeting({ source, duration, speakers, weights, opening, seed,
                     mixed }) {
    let st = seed;
    const rand = () => {                     // mulberry32
      st = (st + 0x6D2B79F5) | 0;
      let t = Math.imul(st ^ (st >>> 15), 1 | st);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    const total = weights.reduce((a, b) => a + b, 0);
    const pick = () => {
      let x = rand() * total;
      for (let i = 0; i < speakers.length; i++) {
        if ((x -= weights[i]) < 0) return speakers[i];
      }
      return speakers[speakers.length - 1];
    };
    const words = (text) => text.split(" ").length;
    const end = duration.split(":").reduce((a, v) => a * 60 + Number(v), 0);
    const turns = opening.map(([t, who, text]) => ({ t, who, text }));
    let last = turns[turns.length - 1];
    let t = last.t + words(last.text) / 2.6 + 1;
    while (t < end - 12) {
      let who = pick();
      if (who === last.who && rand() < 0.8) who = pick();
      const n = rand() < 0.12 ? 4 + Math.floor(rand() * 6)
        : 1 + Math.floor(rand() * 3);
      const text = Array.from({ length: n },
        () => SAID[Math.floor(rand() * SAID.length)]).join(" ");
      last = { t, who, text };
      turns.push(last);
      t += words(text) / 2.6 + 0.4
        + (rand() < 0.06 ? 8 + rand() * 25 : rand() * 3);
    }
    const present = [...new Set(turns.map((x) => x.who))].sort();
    const head = [
      "Meeting transcript", "Source: " + source, "Duration: " + duration,
      "Language: en",
      `Speakers (${present.length}): ${present.join(", ")}`,
      mixed ? "Transcribed with NVIDIA Parakeet TDT 0.6B v2; speakers "
        + "diarized automatically with pyannote speaker-diarization-"
        + "community-1. Turns rebuilt from word-level speaker labels; labels "
        + "are machine-assigned and may occasionally be wrong, treat them as "
        + "approximate."
        : "Speaker-separated from per-participant tracks; labels are "
        + "authoritative (one clean track per speaker).",
    ];
    if (mixed) {
      head.push("Speaker map (edit with real names if known): "
        + present.map((p) => p + " = ?").join("; "));
    }
    head.push("Format: [timestamp] SPEAKER: turn. Consecutive words by one "
      + "speaker are grouped into a single turn.", "---", "");
    const body = turns.flatMap((x) => [`[${stamp(x.t)}] ${x.who}: ${x.text}`,
      ""]);
    const count = turns.reduce((a, x) => a + words(x.text), 0);
    return {
      text: head.concat(body).join("\n").trimEnd() + "\n",
      stats: { duration, speakers: present, turns: turns.length,
               approx_tokens: Math.round(count * 1.35) },
    };
  }

  const MEETINGS = {
    pertrack: meeting({
      source: "2026-09-24 14.02.11 Weekly design review", duration: "47:12",
      speakers: PEOPLE, weights: [32, 27, 24, 17], seed: 7,
      opening: [
        [4, "Maya Chen", "Okay, looks like everyone's here. Let's start with "
          + "the onboarding flow, since that's the one that slipped last "
          + "week."],
        [12, "Jordan Ellis", "I pulled the numbers this morning. Drop-off on "
          + "the second screen went from thirty-one percent to nineteen."],
        [21, "Priya Raman", "That's the screen where we removed the account "
          + "picker, right?"],
        [24, "Jordan Ellis", "Right. Nothing else changed in that release."],
        [29, "Tomás Herrera", "Then I'd like to do the same on the settings "
          + "page. It has the same picker and the same complaints in "
          + "support."],
        [38, "Maya Chen", "Let's scope it before Friday. Priya, can you take "
          + "the first pass at the copy?"],
        [43, "Priya Raman", "Yes. I'll share a draft in the channel "
          + "tomorrow."],
      ],
    }),
    mono: meeting({
      source: "Quarterly planning.mp4", duration: "38:47", mixed: true,
      speakers: ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"],
      weights: [45, 35, 20], seed: 11,
      opening: [
        [2, "SPEAKER_00", "Thanks for joining. The goal today is to agree on "
          + "the three bets for next quarter."],
        [9, "SPEAKER_01", "Before we start, the hiring plan changed on "
          + "Tuesday, so one of the bets may need to wait."],
        [16, "SPEAKER_02", "Which one?"],
        [17, "SPEAKER_01", "The offline mode. It needs the two people we "
          + "haven't hired yet."],
        [23, "SPEAKER_00", "Then let's rank all four and see where it "
          + "lands."],
      ],
    }),
  };

  const DONE = {
    pertrack: { ...MEETINGS.pertrack.stats, output_path: OUT
      + "2026-09-24 14.02.11 Weekly design review.transcript.txt" },
    mono: { ...MEETINGS.mono.stats, output_path: OUT
      + "Quarterly planning.transcript.txt" },
  };

  const FAILS = {
    oom: "The GPU ran out of memory. Close other programs that use the GPU "
      + "and try again. (CUDA error: out of memory; failed to allocate "
      + "memory for requested buffer of size 402653184)",
    hf_gate: "Hugging Face refused the speaker-detection model. On the "
      + "recording card, check the Hugging Face token (Change) and that its "
      + "account accepted the conditions at huggingface.co/pyannote/"
      + "speaker-diarization-community-1. (403 Client Error: Forbidden)",
  };

  const PIP = [
    "Collecting torch==2.8.0+cu128",
    "Downloading torch-2.8.0+cu128-cp311-cp311-win_amd64.whl (3.2 GB)",
    "Collecting onnxruntime-gpu==1.23.0",
    "Downloading onnxruntime_gpu-1.23.0-cp311-cp311-win_amd64.whl (210 MB)",
    "Collecting pyannote.audio==4.0.1",
    "Installing collected packages: torch, onnxruntime-gpu, pyannote.audio",
    "Successfully installed 64 packages",
  ];

  const init = window.__mockInit || {};
  const M = {
    speed: 1,            // playback speed of the simulated jobs
    autoplay: true,      // start()/bootstrap_env() play a timed job
    pick: "pertrack",    // which PICKS entry pick_input returns ("none": cancel)
    token: null,         // Hugging Face token source: null | "saved" | "env"
    envReady: true,      // env_status().ready
    outcome: "ok",       // transcription: ok | oom | hf_gate
    setupOutcome: "ok",  // setup: ok | fail
    setupBlock: null,    // setup_check(): why setup can't start, or null
    setupCheckMs: 90,    // how long setup_check() takes (measured ~60-100 ms)
    fitNeed: null,       // last win_fit height, in CSS px
    maxed: false,
    readyFired: false,
    gen: 0,              // bumps on cancel, ending the playing job
    calls: [],
    ...init,
  };
  const RUN_LOG = HOME + "\\logs\\run-20260926-101544.log";
  window.__mock = M;
  M.PICKS = PICKS;
  M.DONE = DONE;
  M.MEETINGS = MEETINGS;
  M.FAILS = FAILS;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms / M.speed));
  const emit = (channel, payload) => {
    if (typeof window.__on === "function") window.__on(channel, payload);
  };
  M.emit = emit;
  const jitter = (p) => Math.min(100, p + Math.random() * 0.9);

  async function playRun(pick) {
    const g = ++M.gen;
    const live = () => g === M.gen;
    const mode = pick.mode;
    let meta = { track: null, tracks: null, name: null };
    const prog = (stage, pct = null) => {
      if (live()) emit("progress", { stage, mode, ...meta, pct });
    };
    const ramp = async (stage, ms) => {
      const steps = 40;
      for (let i = 0; i <= steps && live(); i++) {
        prog(stage, i === 0 ? 0 : jitter((100 * i) / steps));
        await sleep(ms / steps);
        if (M.outcome !== "ok" && i === 18) return false;
      }
      return live();
    };
    // A run error ends in its log's path, as backend/app.py's failed() adds.
    const fail = () => live() && emit("error", (FAILS[M.outcome] || "Failed.")
      + "\n\nLog: " + RUN_LOG);

    prog("load_model");
    await sleep(1800);
    if (!live()) return;
    if (mode === "pertrack") {
      const n = pick.speakers.length;
      for (let i = 1; i <= n; i++) {
        meta = { track: i, tracks: n, name: pick.speakers[i - 1] };
        prog("transcribe");
        await sleep(250);
        if (!(await ramp("transcribe", 2600))) return void fail();
      }
      meta = { track: n, tracks: n, name: "" };
      prog("Merging tracks");
      await sleep(900);
    } else {
      meta = { track: 1, tracks: 1, name: "" };
      prog("transcribe");
      await sleep(250);
      if (!(await ramp("transcribe", 5200))) return void fail();
      prog("load_diarize");
      await sleep(1600);
      if (!live()) return;
      prog("diarize");
      if (!(await ramp("diarize", 3200))) return void fail();
    }
    if (!live()) return;
    M.lastDone = DONE[mode];
    emit("done", M.lastDone);
  }

  async function playSetup() {
    const g = ++M.gen;
    const live = () => g === M.gen;
    const step = (stage, pct, msg) => {
      if (live()) emit("progress", { mode: "setup", stage, pct, msg });
    };
    const ramp = async (stage, ms) => {
      for (let i = 0; i <= 30 && live(); i++) {
        step(stage, i === 0 ? 0.0 : jitter((100 * i) / 30), null);
        await sleep(ms / 30);
      }
    };
    step("gpu_check", null, "checking the GPU");
    await sleep(1200);
    step("prepare", null, "preparing");
    await sleep(800);
    step("fetch_python", 0.0, "downloading Python");
    await ramp("fetch_python", 1800);
    step("fetch_ffmpeg", 0.0, "downloading ffmpeg");
    await ramp("fetch_ffmpeg", 1800);
    step("make_venv", null, "creating the environment");
    await sleep(1000);
    step("pip", null, "installing dependencies (several GB)");
    for (const line of PIP) {
      await sleep(700);
      step("pip", null, line);
    }
    if (M.setupOutcome !== "ok") {
      if (live()) emit("progress", { mode: "setup", stage: "failed",
        msg: "Setup failed while installing dependencies (exit code 1). "
          + "The network failed. Check your internet connection and try "
          + "again.\n\nLast output:\nERROR: Could not install packages due "
          + "to an OSError: [Errno 104] Connection reset by peer\n\nSetup "
          + "log: " + HOME + "\\logs\\setup-20260926-101544.log" });
      return;
    }
    step("fetch_models", 0.0, "downloading the speech model");
    await ramp("fetch_models", 2400);
    step("verify", null, "testing the GPU");
    await sleep(1800);
    if (!live()) return;
    M.envReady = true;
    emit("progress", { mode: "setup", stage: "ready" });
  }

  const api = {
    async pick_input() {
      M.calls.push("pick_input");
      await sleep(120);
      if (M.pick === "none") return null;
      return { ...PICKS[M.pick] };
    },
    async env_status() {
      return { ready: M.envReady };
    },
    async setup_check() {
      M.calls.push("setup_check");
      await sleep(M.setupCheckMs);
      return { reason: M.setupBlock };
    },
    async bootstrap_env() {
      M.calls.push("bootstrap_env");
      if (M.autoplay) playSetup();
    },
    async cancel() {
      M.calls.push("cancel");
      M.gen++;
      await sleep(300);
      M.cancelled();
    },
    async start() {
      M.calls.push("start");
      if (M.autoplay) playRun(PICKS[M.pick] || PICKS.pertrack);
    },
    async hf_token_status() {
      return { source: M.token };
    },
    async hf_token_save(raw) {
      M.calls.push("hf_token_save");
      await sleep(900);
      const t = String(raw || "").trim();
      if (!/^hf_[A-Za-z0-9]{20,}$/.test(t)) return { ok: false, status: "format" };
      if (/^hf_x+$/i.test(t)) return { ok: false, status: "invalid" };
      M.token = "saved";
      return { ok: true, status: "ok" };
    },
    async hf_token_clear() {
      M.token = null;
      return { source: null };
    },
    async open_link(name) { M.calls.push("open_link:" + name); },
    async copy_transcript() {
      M.calls.push("copy_transcript");
      if (!M.lastDone) return { ok: false, error: "There is no transcript yet." };
      const key = M.lastDone === DONE.mono ? "mono" : "pertrack";
      return { ok: true, text: MEETINGS[key].text };
    },
    async open_folder() {
      M.calls.push("open_folder");
      return null;
    },
    async win_minimize() { M.calls.push("win_minimize"); },
    async win_toggle_max() {
      M.maxed = !M.maxed;
      setTimeout(() => emit("maxed", M.maxed), 0);
      return M.maxed;
    },
    async win_close() { M.calls.push("win_close"); },
    async win_size() {
      return { w: Math.round(innerWidth * devicePixelRatio),
               h: Math.round(innerHeight * devicePixelRatio) };
    },
    async win_resize(w, h) {
      M.calls.push("win_resize");
    },
    async win_fit(need) {
      M.fitNeed = need / devicePixelRatio;
    },
  };

  // How many arguments each method of backend/app.py's Api takes, besides
  // self. pywebview calls Python with the page's arguments, so a call with
  // any other number fails there with a TypeError; the mock fails the same
  // way. tests/test_ui_mock.py checks this table against Api.
  const ARITY = {
    pick_input: 0, env_status: 0, setup_check: 0, bootstrap_env: 0,
    cancel: 0, start: 0, hf_token_status: 0, hf_token_save: 1,
    hf_token_clear: 0, open_link: 1, copy_transcript: 0, open_folder: 0,
    win_minimize: 0, win_toggle_max: 0, win_close: 0, win_size: 0,
    win_resize: 2, win_fit: 1,
  };
  for (const [name, n] of Object.entries(ARITY)) {
    const real = api[name];
    api[name] = (...args) => args.length === n ? real(...args)
      : Promise.reject(Object.assign(new Error(
          `Api.${name}() takes ${n + 1} positional argument${n ? "s" : ""} `
          + `but ${args.length + 1} ${args.length ? "were" : "was"} given`),
          { name: "TypeError" }));
  }

  // Drive the page exactly as the backend would, without timers.
  M.progress = (p) => emit("progress", p);
  M.done = (key) => {
    M.lastDone = DONE[key];
    emit("done", M.lastDone);
  };
  M.cancelled = () => emit("cancelled", M.envReady ? "transcribe" : "setup");
  M.error = (msg) => emit("error", msg);

  window.pywebview = { api };
  const ready = () => {
    M.readyFired = true;
    window.dispatchEvent(new CustomEvent("pywebviewready"));
  };
  if (document.readyState === "complete") setTimeout(ready, 0);
  else window.addEventListener("load", () => setTimeout(ready, 0));
})();
