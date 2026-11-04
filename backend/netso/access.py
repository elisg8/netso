"""Who can do what (multi-user, CML/PNETLab style).

Roles:  admin   everything (users, devices, images, every lab)
        user    creates labs, works on own labs and on labs shared with them
        viewer  only looks at labs shared with them
Per lab: owner (full, can share and delete), edit (change and run), view (read only).
Ownership and sharing live in <home>/access.json; labs without a record belong to the first admin.
"""
from __future__ import annotations

import json
import re
import threading
from urllib.parse import unquote

from .config import settings

LEVELS = {"none": 0, "view": 1, "edit": 2, "owner": 3}
ROLES = ("admin", "user", "viewer")
_lock = threading.Lock()


def _file():
    return settings.home / "access.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(f)


def meta(lab: str) -> dict:
    m = _load().get(lab) or {}
    return {"owner": m.get("owner") or "admin", "shared": dict(m.get("shared") or {})}


def level(lab: str, user: str, role: str) -> int:
    if role == "admin":
        return LEVELS["owner"]
    m = meta(lab)
    if m["owner"] == user:
        return LEVELS["owner"]
    lvl = LEVELS.get(m["shared"].get(user, "none"), 0)
    return min(lvl, LEVELS["view"]) if role == "viewer" else lvl


def level_name(n: int) -> str:
    return next(k for k, v in LEVELS.items() if v == n)


def set_owner(lab: str, user: str) -> None:
    with _lock:
        data = _load()
        data.setdefault(lab, {})["owner"] = user
        _save(data)


def set_shared(lab: str, shared: dict[str, str]) -> dict:
    clean = {u: lv for u, lv in shared.items() if lv in ("view", "edit")}
    with _lock:
        data = _load()
        data.setdefault(lab, {})["shared"] = clean
        _save(data)
    return clean


def forget(lab: str) -> None:
    with _lock:
        data = _load()
        if data.pop(lab, None) is not None:
            _save(data)


def forget_user(user: str) -> None:
    """A deleted user's labs go to the admin, their shares disappear."""
    with _lock:
        data = _load()
        for m in data.values():
            if m.get("owner") == user:
                m["owner"] = "admin"
            (m.get("shared") or {}).pop(user, None)
        _save(data)


# ------------------------------------------------------------------ request rules

_LAB = re.compile(r"^/api/labs/([^/]+)(/.*)?$")
ADMIN_ONLY = ("/api/templates", "/api/images", "/api/icons", "/api/servers", "/api/agents")


def check(path: str, method: str, user: str, role: str) -> str | None:
    """None if allowed, otherwise the reason (HTTP 403)."""
    write = method not in ("GET", "HEAD", "OPTIONS")
    if role == "admin":
        return None
    if path.startswith("/api/users"):
        return "Solo un amministratore gestisce gli utenti" if write else None
    if path.startswith(ADMIN_ONLY) and write:
        return "Solo un amministratore può cambiare dispositivi, immagini e server"
    if write and (path in ("/api/labs", "/api/labs/generate", "/api/import", "/api/import/preview") or path.startswith("/api/folders")):
        return "Il tuo utente è in sola lettura" if role == "viewer" else None
    m = _LAB.match(path)
    if not m:
        return None
    lab, rest = unquote(m.group(1)), m.group(2) or ""   # lab name may contain spaces (%20 in the URL)
    have = level(lab, user, role)
    if rest in ("/share",) or (method == "DELETE" and rest == ""):
        need = "owner" if write else "view"
    elif write or rest.startswith("/capture-stream"):
        need = "edit"
    else:
        need = "view"
    if have >= LEVELS[need]:
        return None
    if have == 0:
        return "Lab non condiviso con te"
    return "Serve il permesso di modifica su questo lab" if need == "edit" else "Solo il proprietario può farlo"
