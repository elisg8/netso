"""Users and login sessions.

Users live in <home>/users.json with PBKDF2 password hashes. On first start an "admin" user
with password "Netso" is created. Login returns a random session token; only its SHA-256 is
stored (<home>/sessions.json), so sessions survive a restart of the service.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from .config import settings

DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "Netso"
SESSION_TTL = 30 * 24 * 3600
ITERATIONS = 240_000
_USER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,31}$")
_lock = threading.Lock()


class AuthError(ValueError):
    pass


def _read(name: str) -> dict:
    try:
        return json.loads((settings.home / name).read_text())
    except (OSError, ValueError):
        return {}


def _write(name: str, data: dict) -> None:
    f = settings.home / name
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(f)


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def _record(password: str, role: str) -> dict:
    salt = secrets.token_hex(16)
    return {"salt": salt, "hash": _hash(password, salt), "role": role}


def users() -> dict:
    with _lock:
        data = _read("users.json")
        if not data:
            data = {DEFAULT_USER: _record(DEFAULT_PASSWORD, "admin")}
            _write("users.json", data)
        return data


def set_password(username: str, password: str, role: str | None = None) -> None:
    if not _USER_RE.match(username or ""):
        raise AuthError("Invalid username")
    if len(password) < 4:
        raise AuthError("The password must be at least 4 characters long")
    data = users()
    with _lock:
        old = data.get(username, {})
        data[username] = _record(password, role or old.get("role", "admin"))
        _write("users.json", data)


def role_of(username: str | None) -> str:
    """Role of a caller: the fixed API token and a server without login act as admin."""
    from .config import settings
    if username == "api" or not settings.auth:
        return "admin"
    return (users().get(username or "") or {}).get("role", "viewer")


def set_role(username: str, role: str) -> None:
    from .access import ROLES
    if role not in ROLES:
        raise AuthError(f"Unknown role '{role}'")
    data = users()
    if username not in data:
        raise AuthError(f"User '{username}' does not exist")
    if data[username].get("role") == "admin" and role != "admin" and sum(1 for u in data.values() if u.get("role") == "admin") == 1:
        raise AuthError("At least one administrator must remain")
    with _lock:
        data[username]["role"] = role
        _write("users.json", data)


def delete_user(username: str) -> None:
    data = users()
    if username not in data:
        raise AuthError(f"User '{username}' does not exist")
    if len(data) == 1:
        raise AuthError("You cannot delete the last user")
    if data[username].get("role") == "admin" and sum(1 for u in data.values() if u.get("role") == "admin") == 1:
        raise AuthError("You cannot delete the last administrator")
    with _lock:
        data.pop(username)
        _write("users.json", data)


def check_password(username: str, password: str) -> bool:
    rec = users().get(username)
    if not rec:
        _hash(password, "00" * 16)  # same cost whether or not the user exists
        return False
    return hmac.compare_digest(_hash(password, rec["salt"]), rec["hash"])


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _live_sessions() -> dict:
    now = time.time()
    return {k: v for k, v in _read("sessions.json").items() if v.get("expires", 0) > now}


def login(username: str, password: str) -> str:
    if not check_password(username, password):
        raise AuthError("Wrong username or password")
    token = secrets.token_urlsafe(32)
    with _lock:
        sessions = _live_sessions()
        sessions[_digest(token)] = {"user": username, "expires": time.time() + SESSION_TTL}
        _write("sessions.json", sessions)
    return token


def session_user(token: str | None) -> str | None:
    if not token:
        return None
    s = _read("sessions.json").get(_digest(token))
    if not s or s.get("expires", 0) < time.time() or s.get("user") not in users():
        return None
    return s["user"]


def logout(token: str) -> None:
    with _lock:
        sessions = _live_sessions()
        if sessions.pop(_digest(token), None) is not None:
            _write("sessions.json", sessions)


def is_default_password(username: str) -> bool:
    return username == DEFAULT_USER and check_password(username, DEFAULT_PASSWORD)
