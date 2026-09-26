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


def test_render_mono_header_and_blank_speaker_map():
    turns = [
        {"speaker": "SPEAKER_00", "start": 5.0, "end": 6.0, "text": "Hello."},
        {"speaker": "SPEAKER_01", "start": 6.0, "end": 7.0, "text": "Hi."},
    ]
    text, stats = T.render(turns, "en", "sample.m4a", "mono")
    assert "Source: sample.m4a" in text
    assert "pyannote" in text
    assert ("Speaker map (edit with real names if known): "
            "SPEAKER_00 = ?; SPEAKER_01 = ?") in text
    assert "[00:05] SPEAKER_00: Hello." in text
    assert stats["turns"] == 2
    assert stats["speakers"] == ["SPEAKER_00", "SPEAKER_01"]
    assert stats["duration"] == "00:07"     # no recording length given


def test_render_duration_is_the_recording_length():
    turns = [{"speaker": "A", "start": 1.0, "end": 2.0, "text": "Hi."}]
    text, stats = T.render(turns, "en", "x", "pertrack", duration=3725.2)
    assert "Duration: 1:02:05" in text and stats["duration"] == "1:02:05"
    _text, stats = T.render([], "en", "x", "pertrack", duration=90)
    assert stats["duration"] == "01:30"     # silence still has a length
    assert T.render([], "en", "x", "pertrack")[1]["duration"] == "?"


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


def _fixture_words(data):
    """The fixture is a WhisperX-format JSON (from the previous engine)."""
    return data.get("word_segments") or [
        w for s in data.get("segments", []) for w in (s.get("words") or [])]


def test_words_from_result_reads_runner_output():
    data = {"words": [{"word": "a", "start": 0.0, "end": 0.1}],
            "duration": 1.0}
    assert T.words_from_result(data) == data["words"]
    assert T.words_from_result({}) == []


def test_strip_fillers_drops_filler_words_only():
    assert T.strip_fillers("so um we uh start") == "so we start"
    assert T.strip_fillers("Uh-huh, yes. Mhm.") == "Uh-huh, yes. Mhm."
    assert T.strip_fillers("hmm") == ""


def test_strip_fillers_moves_capital_forward_at_sentence_start():
    assert T.strip_fillers("Um, so we start.") == "So we start."
    assert T.strip_fillers("Done. Uh, next one.") == "Done. Next one."


def test_strip_fillers_moves_sentence_end_back():
    assert T.strip_fillers("and then, um.") == "and then."
    assert T.strip_fillers("right? uh.") == "right?"


def test_assign_speakers_largest_overlap_wins():
    turns = [(0.0, 1.0, "A"), (1.0, 3.0, "B")]
    words = [{"word": "x", "start": 0.8, "end": 1.6}]      # 0.2 A, 0.6 B
    assert T.assign_speakers(words, turns)[0]["speaker"] == "B"


def test_assign_speakers_gap_uses_nearest_within_tolerance():
    turns = [(0.0, 1.0, "A"), (5.0, 6.0, "B")]
    near = {"word": "n", "start": 1.3, "end": 1.4}          # 0.3 s after A
    far = {"word": "f", "start": 3.0, "end": 3.1}           # 2 s from both
    out = T.assign_speakers([near, far], turns, tolerance=0.5)
    assert out[0]["speaker"] == "A"
    assert "speaker" not in out[1]


def test_assign_speakers_zero_length_and_untimed_words():
    turns = [(0.0, 2.0, "A")]
    words = [{"word": "z", "start": 1.0, "end": 1.0}, {"word": "u"}]
    out = T.assign_speakers(words, turns)
    assert out[0]["speaker"] == "A" and "speaker" not in out[1]
    assert T.assign_speakers(words, []) == words        # no turns: unchanged


def test_assign_speakers_unsorted_turns_and_many_words():
    turns = [(i + 0.0, i + 1.0, "A" if i % 2 else "B") for i in range(50)][::-1]
    words = [{"word": str(i), "start": i + 0.4, "end": i + 0.6}
             for i in range(50)]
    out = T.assign_speakers(words, turns)
    assert [w["speaker"] for w in out] == [
        "A" if i % 2 else "B" for i in range(50)]


def test_render_mono_method_names_engine_and_diarizer():
    turns = [{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.0,
              "text": "Hello."}]
    text, _ = T.render(turns, "en", "rec.wav", "mono")
    assert "Parakeet" in text and "pyannote" in text
    assert "WhisperX" not in text


def test_render_strips_fillers_and_drops_empty_turns():
    turns = [{"speaker": "A", "start": 0.0, "end": 1.0, "text": "Um."},
             {"speaker": "B", "start": 1.0, "end": 2.0,
              "text": "Uh, hi there."}]
    text, stats = T.render(turns, "en", "x", "pertrack")
    assert "[00:01] B: Hi there." in text
    assert stats["speakers"] == ["B"] and stats["turns"] == 1
    assert stats["words"] == 2


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
    words = _fixture_words(data)
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
