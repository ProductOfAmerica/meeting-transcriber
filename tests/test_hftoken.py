import json
import sys
import urllib.error
import uuid

import pytest

from backend import app as A
from backend import hftoken as H

TOKEN = "hf_" + "a1B2" * 8 + "xy"      # same shape as a real one: hf_ + 34
OTHER = "hf_" + "z" * 34


def test_normalize_accepts_tokens_and_trims_whitespace():
    assert H.normalize(TOKEN) == TOKEN
    assert H.normalize(f"  {TOKEN}\r\n") == TOKEN


@pytest.mark.parametrize("raw", ["", "hf_x\n", "hf_short", f"Bearer {TOKEN}",
                                 f"{TOKEN} extra", TOKEN[:20] + "\n" + TOKEN[20:],
                                 None, 42])
def test_normalize_rejects_anything_else(raw):
    assert H.normalize(raw) is None


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(answers):
    """Answers the n-th request with answers[n]: a status or an exception."""
    urls = []

    def open_(req, timeout=None):
        urls.append(req.full_url)
        answer = answers[len(urls) - 1]
        if isinstance(answer, BaseException):
            raise answer
        if answer >= 400:
            raise urllib.error.HTTPError(req.full_url, answer, "refused", {},
                                         None)
        return _Response(answer)
    return open_, urls


@pytest.mark.parametrize("answers, expected", [
    ([200, 200], "ok"),
    ([401], "invalid"),
    ([200, 403], "terms"),
    ([200, 404], "unverified"),
    ([500], "unverified"),
    ([urllib.error.URLError("offline")], "unverified"),
    ([ValueError(f"Invalid header value b'Bearer {TOKEN}'")], "unverified"),
])
def test_check_maps_hub_answers(answers, expected):
    opener, urls = _opener(answers)
    assert H.check(TOKEN, "org/model", opener=opener) == expected
    assert urls[0] == "https://huggingface.co/api/whoami-v2"
    if len(urls) > 1:
        assert urls[1] == "https://huggingface.co/api/models/org/model/auth-check"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_credential_manager_round_trip():
    target = f"Transcribe/test-{uuid.uuid4().hex}"
    try:
        assert H.load(target) is None
        assert H.save(TOKEN, target)
        assert H.load(target) == TOKEN
    finally:
        H.clear(target)
    assert H.load(target) is None


def test_resolve_prefers_the_saved_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", OTHER)
    monkeypatch.setattr(H, "load", lambda target=H.TARGET: TOKEN)
    assert H.resolve() == (TOKEN, "saved")
    monkeypatch.setattr(H, "load", lambda target=H.TARGET: None)
    assert H.resolve() == (OTHER, "environment")
    monkeypatch.setenv("HF_TOKEN", "not a token")
    assert H.resolve() == (None, None)


@pytest.fixture
def api(monkeypatch):
    store = {}

    def save(token, target=H.TARGET):
        store["token"] = token
        return True
    monkeypatch.setattr(H, "save", save)
    monkeypatch.setattr(H, "load", lambda target=H.TARGET: store.get("token"))
    monkeypatch.setattr(H, "clear", lambda target=H.TARGET: store.clear())
    monkeypatch.delenv("HF_TOKEN", raising=False)
    return A.Api(), store


def test_api_saves_a_good_token_and_never_hands_it_back(api, monkeypatch):
    a, store = api
    monkeypatch.setattr(H, "check", lambda token, repo, opener=None: "ok")
    assert a.hf_token_status() == {"source": None}
    assert a.hf_token_save(f" {TOKEN}\n") == {"ok": True, "status": "ok"}
    assert store["token"] == TOKEN
    assert a.hf_token_status() == {"source": "saved"}
    assert TOKEN not in repr(vars(a))           # nothing JS can reach holds it
    assert a.hf_token_clear() == {"source": None}


@pytest.mark.parametrize("raw", ["hf_x\n", f"Bearer {TOKEN}", 12])
def test_api_rejects_malformed_input_without_echoing_it(api, raw):
    a, store = api
    r = a.hf_token_save(raw)
    assert r == {"ok": False, "status": "format"} and not store


def test_api_does_not_save_a_token_the_hub_rejects(api, monkeypatch):
    a, store = api
    monkeypatch.setattr(H, "check", lambda token, repo, opener=None: "invalid")
    assert a.hf_token_save(TOKEN) == {"ok": False, "status": "invalid"}
    assert not store


def test_api_failures_come_back_as_fixed_words(api, monkeypatch):
    a, _store = api

    def boom(token, repo, opener=None):
        raise ValueError(f"Invalid header value b'Bearer {token}'")
    monkeypatch.setattr(H, "check", boom)
    r = a.hf_token_save(TOKEN)
    assert r == {"ok": False, "status": "error"}
    assert TOKEN not in json.dumps(r)


def test_open_link_only_opens_known_pages(monkeypatch):
    opened = []
    monkeypatch.setattr(A.os, "startfile", opened.append, raising=False)
    a = A.Api()
    a.open_link("hf_terms")
    a.open_link("https://example.com")
    assert opened == [A.LINKS["hf_terms"]]
