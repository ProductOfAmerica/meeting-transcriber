"""Build an LLM-friendly transcript from word-level timings and speakers.

Import-safe by contract: importing this module performs no I/O, reads no argv,
writes no files, and prints nothing.
"""
from __future__ import annotations

import bisect
import re

MAX_FLICKER_WORDS = 2
MAX_FLICKER_SEC = 0.6
# Pure filler words, dropped from the transcript (the user's choice for
# LLM-ready transcripts). Backchannels that carry meaning (uh-huh, mhm) stay.
FILLERS = frozenset({"um", "uh", "er", "erm", "hmm", "mm"})
_EDGE_PUNCT = ".,!?;:\"'()"


def ts(seconds) -> str:
    seconds = int(round(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def clean(text) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _drop_fillers(tokens) -> list:
    """[index, token] for each token that isn't a filler word. Sentence-ending
    punctuation on a dropped filler moves to the previous token; a capitalized
    filler that opened a sentence passes its capital to the next token."""
    out = []
    cap_next = False
    for i, tok in enumerate(tokens):
        if tok.strip(_EDGE_PUNCT).lower() in FILLERS:
            end = tok[-1] if tok[-1] in ".?!" else ""
            if end and out:
                out[-1][1] = out[-1][1].rstrip(",;:")
                if not out[-1][1].endswith((".", "?", "!")):
                    out[-1][1] += end
            if tok[0].isupper() and (
                    not out or out[-1][1].endswith((".", "?", "!"))):
                cap_next = True
            continue
        if cap_next:
            tok = tok[0].upper() + tok[1:]
            cap_next = False
        out.append([i, tok])
    return out


def strip_fillers(text: str) -> str:
    """Drop filler words from text (see _drop_fillers)."""
    return " ".join(tok for _i, tok in _drop_fillers(text.split()))


def assign_speakers(words, turns, tolerance: float = 0.5) -> list:
    """Label words with speakers from an exclusive diarization.

    turns: (start, end, speaker) with at most one speaker at any time. A word
    takes the turn overlapping its [start, end] the most; a word overlapping
    none takes the nearest turn within `tolerance` seconds; otherwise it stays
    unlabeled and carry_speakers fills it from its neighbors."""
    turns = sorted(turns)
    starts = [t[0] for t in turns]
    out = []
    for w in words:
        s = w.get("start")
        label = None
        if s is not None and turns:
            e = w.get("end") if w.get("end") is not None else s
            k = bisect.bisect_left(starts, e)   # turns[:k] start before e
            best, j = 0.0, k - 1
            while j >= 0 and turns[j][1] > s:
                overlap = min(e, turns[j][1]) - max(s, turns[j][0])
                if overlap > best:
                    best, label = overlap, turns[j][2]
                j -= 1
            if label is None:
                near = [turns[i] for i in (k - 1, k) if 0 <= i < len(turns)]
                dist, speaker = min(
                    (max(t[0] - s, s - t[1], 0.0), t[2]) for t in near)
                if dist <= tolerance:
                    label = speaker
        out.append(dict(w, speaker=label) if label is not None else dict(w))
    return out


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


def merge_tracks(tracks) -> list:
    """Per-participant (speaker, words) tracks as one list of words in time
    order, each labeled with its track's speaker. build_turns then starts a
    new turn at every change of speaker, so words said over someone else
    land where they were said.

    Each track first loses its empty and filler words, so an "uh" can't
    split another speaker's turn. Words that start together keep the tracks'
    order; a word without a start stays with the words around it."""
    merged = []
    for speaker, words in tracks:
        words = [w for w in words if clean(w.get("word"))]
        at = next((w["start"] for w in words if w.get("start") is not None),
                  0.0)
        for i, tok in _drop_fillers([clean(w["word"]) for w in words]):
            if words[i].get("start") is not None:
                at = words[i]["start"]
            merged.append((at, dict(words[i], word=tok, speaker=speaker)))
    merged.sort(key=lambda pair: pair[0])       # stable
    return [w for _at, w in merged]


def render(turns, language, source_name, mode, duration=None):
    """duration: the recording's length in seconds; without it, the end of
    the last turn."""
    turns = [dict(t, text=strip_fillers(t["text"])) for t in turns]
    turns = [t for t in turns if t["text"]]
    present = sorted({t["speaker"] for t in turns})
    if duration is None and turns:
        duration = turns[-1]["end"]
    duration = ts(duration) if duration is not None else "?"
    speaker_map = "; ".join(f"{s} = ?" for s in present)
    if mode == "pertrack":
        method = ("Speaker-separated from per-participant tracks; labels are "
                  "authoritative (one clean track per speaker).")
    else:
        method = ("Transcribed with NVIDIA Parakeet TDT 0.6B v2; speakers "
                  "diarized automatically with pyannote "
                  "speaker-diarization-community-1. Turns rebuilt from "
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
        "approx_tokens": round(word_count * 1.35),
    }
    return text, stats


def words_from_result(data: dict) -> list:
    """Words from one backend.runner result JSON."""
    return list(data.get("words") or [])


def build_transcript(words, *, language="?", source_name="audio",
                     mode="mono", apply_flicker=True, duration=None):
    turns = build_turns(words, apply_flicker_filter=apply_flicker)
    return render(turns, language, source_name, mode, duration)
