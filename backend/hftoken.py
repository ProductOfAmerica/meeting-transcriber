"""The Hugging Face token that mixed recordings need for speaker detection:
kept in Windows Credential Manager and checked against the Hub. Import-safe.

The token must never reach an exception message, a log or a return value:
pywebview hands str(exception) and the traceback to JavaScript and its log,
and http.client puts header values (the token included) into its errors. So
the functions here return fixed words and swallow their own failures.
"""
from __future__ import annotations

import ctypes
import os
import re
import urllib.error
import urllib.request
from ctypes import wintypes

TARGET = "Transcribe/huggingface"          # the Credential Manager entry
_TOKEN = re.compile(r"hf_[A-Za-z0-9]{20,}")
_WHOAMI = "https://huggingface.co/api/whoami-v2"
_AUTH_CHECK = "https://huggingface.co/api/models/{repo}/auth-check"


def normalize(raw) -> str | None:
    """The token if raw looks like a Hugging Face token (surrounding
    whitespace ignored), else None."""
    if not isinstance(raw, str):
        return None
    token = raw.strip()
    return token if _TOKEN.fullmatch(token) else None


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR)]


_GENERIC = 1                 # CRED_TYPE_GENERIC
_PERSIST_LOCAL_MACHINE = 2   # this user, this PC; not roamed


def _advapi():
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    a.CredWriteW.argtypes = (ctypes.POINTER(_Credential), wintypes.DWORD)
    a.CredWriteW.restype = wintypes.BOOL
    a.CredReadW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(ctypes.POINTER(_Credential)))
    a.CredReadW.restype = wintypes.BOOL
    a.CredDeleteW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
    a.CredDeleteW.restype = wintypes.BOOL
    a.CredFree.argtypes = (ctypes.c_void_p,)
    a.CredFree.restype = None
    return a


def load(target: str = TARGET) -> str | None:
    """The saved token, or None."""
    a = _advapi()
    cred = ctypes.POINTER(_Credential)()
    if not a.CredReadW(target, _GENERIC, 0, ctypes.byref(cred)):
        return None
    try:
        c = cred.contents
        blob = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
        return normalize(blob.decode("utf-16-le", errors="replace"))
    finally:
        a.CredFree(cred)


def save(token: str, target: str = TARGET) -> bool:
    blob = token.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _Credential(Type=_GENERIC, TargetName=target,
                       CredentialBlobSize=len(blob),
                       CredentialBlob=ctypes.cast(buf,
                                                  ctypes.POINTER(ctypes.c_ubyte)),
                       Persist=_PERSIST_LOCAL_MACHINE, UserName="huggingface")
    return bool(_advapi().CredWriteW(ctypes.byref(cred), 0))


def clear(target: str = TARGET) -> None:
    _advapi().CredDeleteW(target, _GENERIC, 0)


def resolve() -> tuple:
    """(token, source): the saved token first, then HF_TOKEN from the
    environment, else (None, None)."""
    token = load()
    if token:
        return token, "saved"
    token = normalize(os.environ.get("HF_TOKEN") or "")
    return (token, "environment") if token else (None, None)


def _status(url, token, opener) -> int:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "User-Agent": "Transcribe"})
    try:
        with opener(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def check(token: str, repo: str, opener=urllib.request.urlopen) -> str:
    """'ok'; 'invalid' (the Hub rejects the token); 'terms' (a valid token
    whose account has not accepted the model's conditions); or 'unverified'
    (no usable answer). The Hub answers 401 for a bad token and 403 for
    unaccepted conditions (observed 2026-09-26)."""
    try:
        who = _status(_WHOAMI, token, opener)
        if who == 401:
            return "invalid"
        if who != 200:
            return "unverified"
        access = _status(_AUTH_CHECK.format(repo=repo), token, opener)
        if access == 200:
            return "ok"
        return "terms" if access == 403 else "unverified"
    except Exception:
        return "unverified"
