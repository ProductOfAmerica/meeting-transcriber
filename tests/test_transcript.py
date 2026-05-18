from backend import transcript as T


def test_ts_minutes_seconds():
    assert T.ts(0) == "00:00"
    assert T.ts(5.7) == "00:06"
    assert T.ts(2295) == "38:15"


def test_ts_hours():
    assert T.ts(3661) == "1:01:01"


def test_ts_none_is_zero():
    assert T.ts(None) == "00:00"


def test_clean_collapses_whitespace():
    assert T.clean("  a\n b\t c ") == "a b c"
    assert T.clean(None) == ""
    assert T.clean("") == ""


def test_carry_speakers_fills_gaps_forward():
    words = [
        {"word": "a", "speaker": "SPEAKER_00"},
        {"word": "b"},
        {"word": "c", "speaker": "SPEAKER_01"},
    ]
    assert T.carry_speakers(words) == ["SPEAKER_00", "SPEAKER_00", "SPEAKER_01"]


def test_carry_speakers_backfills_leading_unlabeled():
    words = [{"word": "a"}, {"word": "b", "speaker": "SPEAKER_02"}]
    assert T.carry_speakers(words) == ["SPEAKER_02", "SPEAKER_02"]


def test_carry_speakers_all_unlabeled_uses_placeholder():
    words = [{"word": "a"}, {"word": "b"}]
    assert T.carry_speakers(words) == ["SPEAKER_?", "SPEAKER_?"]


def test_build_runs_groups_consecutive_speakers():
    spk = ["A", "A", "B", "A", "A", "A"]
    assert T.build_runs(spk) == [[0, 1, "A"], [2, 2, "B"], [3, 5, "A"]]


def test_run_duration_uses_first_and_last_known_times():
    words = [
        {"start": 1.0, "end": 1.5},
        {"start": 1.5, "end": 2.0},
        {"start": 2.0, "end": 3.4},
    ]
    assert T.run_duration(words, 0, 2) == 2.4


def test_run_duration_missing_times_is_zero():
    words = [{"word": "a"}, {"word": "b"}]
    assert T.run_duration(words, 0, 1) == 0.0


def _w(word, start, end, speaker):
    return {"word": word, "start": start, "end": end, "speaker": speaker}


def test_flicker_collapses_tiny_sandwiched_excursion():
    words = [
        _w("hello", 0.0, 0.4, "A"),
        _w("there", 0.4, 0.8, "A"),
        _w("yeah", 0.8, 1.0, "B"),
        _w("so", 1.0, 1.4, "A"),
        _w("anyway", 1.4, 1.9, "A"),
    ]
    speakers = ["A", "A", "B", "A", "A"]
    out = T.apply_flicker(words, speakers)
    assert out == ["A", "A", "A", "A", "A"]


def test_flicker_leaves_real_transition_alone():
    words = [
        _w("one", 0.0, 0.4, "A"),
        _w("two", 0.4, 0.8, "A"),
        _w("ok", 0.8, 1.0, "B"),
        _w("three", 1.0, 1.4, "C"),
        _w("four", 1.4, 1.9, "C"),
    ]
    speakers = ["A", "A", "B", "C", "C"]
    out = T.apply_flicker(words, speakers)
    assert out == ["A", "A", "B", "C", "C"]


def test_flicker_keeps_long_excursion():
    words = [
        _w("a", 0.0, 1.0, "A"),
        _w("big", 1.0, 2.0, "B"),
        _w("turn", 2.0, 3.5, "B"),
        _w("here", 3.5, 4.0, "B"),
        _w("z", 4.0, 5.0, "A"),
    ]
    speakers = ["A", "B", "B", "B", "A"]
    out = T.apply_flicker(words, speakers)
    assert out == ["A", "B", "B", "B", "A"]


def test_build_turns_basic_runs():
    words = [
        _w("hi", 0.0, 0.3, "A"),
        _w("there", 0.3, 0.6, "A"),
        _w("hello", 0.6, 1.0, "B"),
    ]
    turns = T.build_turns(words, apply_flicker_filter=False)
    assert turns == [
        {"speaker": "A", "start": 0.0, "end": 0.6, "text": "hi there"},
        {"speaker": "B", "start": 0.6, "end": 1.0, "text": "hello"},
    ]


def test_build_turns_skips_empty_and_punct_only():
    words = [
        _w("real", 0.0, 0.3, "A"),
        {"word": "", "start": 0.3, "end": 0.4, "speaker": "A"},
        {"word": "   ", "start": 0.4, "end": 0.5, "speaker": "A"},
    ]
    turns = T.build_turns(words, apply_flicker_filter=False)
    assert len(turns) == 1
    assert turns[0]["text"] == "real"


def test_build_turns_missing_timestamps_default_to_zero():
    words = [{"word": "x", "speaker": "A"}, {"word": "y", "speaker": "A"}]
    turns = T.build_turns(words, apply_flicker_filter=False)
    assert turns == [{"speaker": "A", "start": 0.0, "end": 0.0, "text": "x y"}]


def test_build_turns_preserves_word_order_and_count():
    words = [_w(str(i), i * 0.1, i * 0.1 + 0.05,
               "A" if i % 2 == 0 else "B") for i in range(20)]
    turns = T.build_turns(words, apply_flicker_filter=False)
    rebuilt = " ".join(t["text"] for t in turns).split()
    original = [T.clean(w["word"]) for w in words if T.clean(w["word"])]
    assert rebuilt == original


def test_parse_speaker_map_reads_prior_line():
    prior = (
        "Meeting transcript\n"
        "Speaker map (edit with real names if known): "
        "SPEAKER_00 = Alice; SPEAKER_01 = Bob\n"
        "---\n"
    )
    assert T.parse_speaker_map(prior) == {
        "SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}


def test_parse_speaker_map_empty_when_absent():
    assert T.parse_speaker_map("no map here") == {}
    assert T.parse_speaker_map(None) == {}


def test_render_mono_header_and_map_preserved():
    turns = [
        {"speaker": "SPEAKER_00", "start": 5.0, "end": 6.0, "text": "Hello."},
        {"speaker": "SPEAKER_01", "start": 6.0, "end": 7.0, "text": "Hi."},
    ]
    prior = ("Speaker map (edit with real names if known): "
             "SPEAKER_00 = Alice\n")
    text, stats = T.render(turns, "en", "sample.m4a", "mono", prior)
    assert "Source: sample.m4a" in text
    assert "pyannote" in text
    assert "SPEAKER_00 = Alice; SPEAKER_01 = ?" in text
    assert "[00:05] SPEAKER_00: Hello." in text
    assert stats["turns"] == 2
    assert stats["speakers"] == ["SPEAKER_00", "SPEAKER_01"]
    assert stats["duration"] == "00:07"


def test_render_pertrack_header_differs():
    turns = [{"speaker": "Alice", "start": 0.0, "end": 1.0, "text": "Hi."}]
    text, _ = T.render(turns, "en", "Alice + Bob", "pertrack", None)
    assert "per-participant" in text
    assert "pyannote" not in text
    assert "Source: Alice + Bob" in text
    # names are already real on this path: no rename-hint line
    assert "Speaker map" not in text


import json
from pathlib import Path

# Structure-preserving, content-free: real timestamps/speakers/word
# count, words replaced by tokens. No real conversation is stored.
FIXTURE = Path(__file__).parent / "fixtures" / "transcript_regression.json"


def test_words_from_whisperx_json_prefers_word_segments():
    data = {"word_segments": [{"word": "a"}], "segments": [{"words": [{"word": "b"}]}]}
    assert T.words_from_whisperx_json(data) == [{"word": "a"}]


def test_words_from_whisperx_json_falls_back_to_segments():
    data = {"segments": [{"words": [{"word": "b"}]}, {"words": None}]}
    assert T.words_from_whisperx_json(data) == [{"word": "b"}]


def test_build_transcript_returns_text_and_stats():
    words = [
        {"word": "Hi", "start": 0.0, "end": 0.3, "speaker": "SPEAKER_00"},
        {"word": "there", "start": 0.3, "end": 0.6, "speaker": "SPEAKER_00"},
    ]
    text, stats = T.build_transcript(
        words, language="en", source_name="x.m4a", mode="mono")
    assert text.startswith("Meeting transcript\n")
    assert stats["turns"] == 1


def test_regression_real_fixture_no_word_loss_and_order():
    if not FIXTURE.exists():
        import pytest
        pytest.skip("real fixture not present")
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    words = T.words_from_whisperx_json(data)
    original = [T.clean(w.get("word", "")) for w in words
                if T.clean(w.get("word", ""))]
    _text, stats = T.build_transcript(
        words, language=data.get("language", "?"),
        source_name="sample.m4a", mode="mono")
    turns = T.build_turns(words, apply_flicker_filter=True)
    rebuilt = " ".join(t["text"] for t in turns).split()
    assert rebuilt == original  # no word loss, order preserved
    assert len(words) >= 6000  # sanity bound from the prior real run
    assert stats["turns"] >= 100  # sanity bound, not a hardcoded count
    assert len(stats["speakers"]) == 3


from backend import pipeline as P


def _two_track_overlap():
    """Alice says one contiguous sentence; Carol back-channels into it.
    Tracks share one clock (per-participant invariant)."""
    alice = [
        {"word": "so", "start": 1.0, "end": 1.2},
        {"word": "I", "start": 1.3, "end": 1.4},
        {"word": "was", "start": 1.5, "end": 1.7},
        {"word": "thinking", "start": 1.8, "end": 2.1},
        {"word": "that", "start": 2.2, "end": 2.4},
    ]
    carol = [
        {"word": "yeah", "start": 1.45, "end": 1.55},
        {"word": "right", "start": 1.85, "end": 1.95},
    ]
    return [("Alice", alice), ("Carol", carol)]


def test_pertrack_overlap_not_shredded():
    """Overlapping back-channels must NOT shred the dominant speaker's
    sentence into multiple turns.

    Regression: under the old global per-word sort + consecutive-speaker
    runs (merge_track_words -> build_turns) Carol's "yeah"/"right" landed
    mid-stream and split Alice's one sentence into 3 turns
    (['so I', 'was thinking', 'that']). The per-track-turns + interleave
    path below keeps each speaker's stream intact.
    """
    track_words = _two_track_overlap()
    turns = []
    for speaker, words in track_words:
        turns += T.turns_from_track(words, speaker)
    turns.sort(key=lambda t: t["start"])
    _text, stats = T.build_transcript_from_turns(
        turns, language="en", source_name="M", mode="pertrack")
    alice_turns = [t for t in turns if t["speaker"] == "Alice"]
    assert len(alice_turns) == 1, (
        "Alice's one sentence was shredded into "
        f"{len(alice_turns)} turns: {[t['text'] for t in alice_turns]}")
    assert alice_turns[0]["text"] == "so I was thinking that"
    carol_turns = [t for t in turns if t["speaker"] == "Carol"]
    assert carol_turns and " ".join(
        w for t in carol_turns for w in t["text"].split()) == "yeah right"
    assert [t["start"] for t in turns] == sorted(t["start"] for t in turns)
    assert stats["turns"] == len(turns)


def test_turns_from_track_splits_on_pause():
    """One speaker's own stream: contiguous words = one turn; a pause
    longer than GAP_SEC starts a new turn; missing timings never split
    and never crash."""
    contiguous = [
        {"word": "a", "start": 0.0, "end": 0.5},
        {"word": "b", "start": 0.6, "end": 1.0},
        {"word": "c", "start": 1.1, "end": 1.6},
    ]
    one = T.turns_from_track(contiguous, "Ann")
    assert one == [{"speaker": "Ann", "start": 0.0, "end": 1.6,
                    "text": "a b c"}]

    gapped = [
        {"word": "first", "start": 0.0, "end": 0.5},
        {"word": "part", "start": 0.5, "end": 0.9},
        {"word": "second", "start": 0.9 + T.GAP_SEC + 0.5,
         "end": 0.9 + T.GAP_SEC + 1.0},
    ]
    two = T.turns_from_track(gapped, "Ann")
    assert [t["text"] for t in two] == ["first part", "second"]

    untimed = [{"word": "x"}, {"word": "y"}, {"word": "z"}]
    out = T.turns_from_track(untimed, "Ann")
    assert len(out) == 1 and out[0]["text"] == "x y z"
