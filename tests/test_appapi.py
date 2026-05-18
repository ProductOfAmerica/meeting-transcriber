import json

from backend import appapi as A


def test_settings_load_missing_returns_defaults(tmp_path):
    s = A.load_settings(tmp_path / "settings.json")
    assert s == {"last_output_dir": "", "update_check_enabled": True}


def test_settings_load_corrupt_returns_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{ not json", encoding="utf-8")
    assert A.load_settings(p) == {
        "last_output_dir": "", "update_check_enabled": True}


def test_settings_reads_hand_edited_escape_hatch(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"last_output_dir": "C:\\Out",
                             "update_check_enabled": False}),
                 encoding="utf-8")
    assert A.load_settings(p) == {
        "last_output_dir": "C:\\Out", "update_check_enabled": False}


def test_settings_ignores_unknown_keys(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"last_output_dir": "X", "junk": 1}),
                 encoding="utf-8")
    s = A.load_settings(p)
    assert s == {"last_output_dir": "X", "update_check_enabled": True}
    assert "junk" not in s
