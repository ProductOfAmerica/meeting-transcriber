"""Build an LLM-friendly transcript from WhisperX word-level speaker labels.

Import-safe by contract: importing this module performs no I/O, reads no argv,
writes no files, and prints nothing. Every side effect lives under the
`if __name__ == "__main__"` CLI shim at the bottom, a small dev helper.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MAX_FLICKER_WORDS = 2
MAX_FLICKER_SEC = 0.6
# Per-track turn split. Only affects how ONE speaker's continuous speech
# is chunked into turns (readability granularity), never correctness:
# pertrack splits each track independently, so another speaker's
# interjection can't fragment this speaker's sentence. Tunable.
GAP_SEC = 1.2


def ts(seconds) -> str:
    seconds = int(round(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def clean(text) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def carry_speakers(words) -> list:
    speakers = []
    last = None
    for w in words:
        sp = w.get("speaker") or last
        speakers.append(sp)
        if sp:
            last = sp
    first = next((sp for sp in speakers if sp), "SPEAKER_?")
    return [sp or first for sp in speakers]


def build_runs(spk) -> list:
    runs = []
    i = 0
    n = len(spk)
    while i < n:
        j = i
        while j + 1 < n and spk[j + 1] == spk[i]:
            j += 1
        runs.append([i, j, spk[i]])
        i = j + 1
    return runs


def run_duration(words, a: int, b: int) -> float:
    start = next((words[k].get("start") for k in range(a, b + 1)
                  if words[k].get("start") is not None), None)
    end = next((words[k].get("end") for k in range(b, a - 1, -1)
                if words[k].get("end") is not None), None)
    if start is None or end is None:
        return 0.0
    return end - start


def apply_flicker(words, speakers) -> list:
    speakers = list(speakers)
    while True:
        runs = build_runs(speakers)
        changed = False
        for r in range(1, len(runs) - 1):
            a, b, sp = runs[r]
            prev_sp = runs[r - 1][2]
            next_sp = runs[r + 1][2]
            tiny = ((b - a + 1) <= MAX_FLICKER_WORDS
                    and run_duration(words, a, b) <= MAX_FLICKER_SEC)
            if tiny and prev_sp == next_sp and prev_sp != sp:
                for k in range(a, b + 1):
                    speakers[k] = prev_sp
                changed = True
                break
        if not changed:
            break
    return speakers


def build_turns(words, apply_flicker_filter: bool = True) -> list:
    speakers = carry_speakers(words)
    if apply_flicker_filter:
        speakers = apply_flicker(words, speakers)
    turns = []
    for a, b, sp in build_runs(speakers):
        text = clean(" ".join(w.get("word", "") for w in words[a:b + 1]))
        if not text:
            continue
        start = next((words[k].get("start") for k in range(a, b + 1)
                      if words[k].get("start") is not None), 0.0)
        end = next((words[k].get("end") for k in range(b, a - 1, -1)
                    if words[k].get("end") is not None), start)
        turns.append({"speaker": sp, "start": start, "end": end, "text": text})
    return turns


def _flush_track_turn(turns, words, speaker) -> None:
    if not words:
        return
    text = clean(" ".join(w.get("word", "") for w in words))
    if not text:
        return
    start = next((w.get("start") for w in words
                  if w.get("start") is not None), 0.0)
    end = next((w.get("end") for w in reversed(words)
                if w.get("end") is not None), start)
    turns.append({"speaker": speaker, "start": start,
                  "end": end, "text": text})


def turns_from_track(words, speaker, gap: float = GAP_SEC) -> list:
    """A per-participant track is a single speaker. Split that speaker's
    OWN word stream into utterance turns on pauses > `gap` seconds.

    Pertrack uses this instead of globally sorting all tracks' words: an
    interjection on another track can no longer fragment this speaker's
    sentence. Callers interleave the per-track turns by start time.
    Missing word timings just don't trigger a split (no crash)."""
    turns, cur, prev_end = [], [], None
    for w in words:
        st = w.get("start")
        if (cur and prev_end is not None and st is not None
                and st - prev_end > gap):
            _flush_track_turn(turns, cur, speaker)
            cur = []
        cur.append(w)
        en = w.get("end")
        if en is not None:
            prev_end = en
        elif st is not None:
            prev_end = st
    _flush_track_turn(turns, cur, speaker)
    return turns


def parse_speaker_map(prior_text) -> dict:
    names = {}
    if not prior_text:
        return names
    for line in prior_text.splitlines():
        if line.startswith("Speaker map"):
            body = line.split(":", 1)[1] if ":" in line else ""
            for entry in body.split(";"):
                if " = " in entry:
                    key, val = entry.split(" = ", 1)
                    names[key.strip()] = val.strip()
            break
    return names


def render(turns, language, source_name, mode, prior_text=None):
    present = sorted({t["speaker"] for t in turns})
    duration = ts(turns[-1]["end"]) if turns else "?"
    names = parse_speaker_map(prior_text)
    speaker_map = "; ".join(f"{s} = {names.get(s, '?')}" for s in present)
    if mode == "pertrack":
        method = ("Speaker-separated from per-participant tracks; labels are "
                  "authoritative (one clean track per speaker).")
    else:
        method = ("Diarized automatically with WhisperX (pyannote "
                  "speaker-diarization-community-1). Turns rebuilt from "
                  "word-level speaker labels; labels are machine-assigned and "
                  "may occasionally be wrong, treat them as approximate.")
    head = [
        "Meeting transcript",
        f"Source: {source_name}",
        f"Duration: {duration}",
        f"Language: {language}",
        f"Speakers ({len(present)}): {', '.join(present)}",
        method,
    ]
    if mode != "pertrack":
        # Per-participant names are already real (from the track files);
        # the rename hint only helps the mono SPEAKER_xx case.
        head.append(
            f"Speaker map (edit with real names if known): {speaker_map}")
    head += [
        ("Format: [timestamp] SPEAKER: turn. Consecutive words by one "
         "speaker are grouped into a single turn."),
        "---",
        "",
    ]
    body = []
    for t in turns:
        body.append(f"[{ts(t['start'])}] {t['speaker']}: {t['text']}")
        body.append("")
    text = "\n".join(head + body).rstrip() + "\n"
    word_count = sum(len(t["text"].split()) for t in turns)
    stats = {
        "words": word_count,
        "turns": len(turns),
        "speakers": present,
        "duration": duration,
        "speaker_map": speaker_map,
        "approx_tokens": round(word_count * 1.35),
    }
    return text, stats


def words_from_whisperx_json(data: dict) -> list:
    words = data.get("word_segments")
    if not words:
        words = [w for s in data.get("segments", []) for w in (s.get("words") or [])]
    return words


def build_transcript(words, *, language="?", source_name="audio",
                     mode="mono", apply_flicker=True, prior_output=None):
    turns = build_turns(words, apply_flicker_filter=apply_flicker)
    return render(turns, language, source_name, mode, prior_text=prior_output)


def build_transcript_from_turns(turns, *, language="?",
                                source_name="audio", mode="mono",
                                prior_output=None):
    """Render pre-built turns (pertrack: per-track turns interleaved by
    start). Same (text, stats) contract as build_transcript; render
    computes all stats from the turns list."""
    return render(turns, language, source_name, mode,
                  prior_text=prior_output)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent.parent
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "audio.json"
    dst = (Path(sys.argv[2]) if len(sys.argv) > 2
           else here / "audio.transcript.txt")
    data = json.loads(src.read_text(encoding="utf-8"))
    words = words_from_whisperx_json(data)
    prior = dst.read_text(encoding="utf-8") if dst.exists() else None
    text, stats = build_transcript(
        words, language=data.get("language", "?"),
        source_name=f"{src.stem}.m4a", mode="mono", prior_output=prior)
    dst.write_text(text, encoding="utf-8")
    print(f"wrote {dst}")
    print(f"words: {len(words)} -> turns: {stats['turns']}")
    print(f"speakers: {stats['speakers']}")
    print(f"speaker map: {stats['speaker_map']}")
