"""Other NetSO servers this one knows about: quick links, status and VXLAN peers (labs joined across servers)."""
from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import urlparse

from .config import settings


class ServerError(ValueError):
    pass


def _file():
    return settings.home / "servers.json"


def list_servers() -> list[dict]:
    try:
        return json.loads(_file().read_text())
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(items, indent=2))


def add(name: str, url: str) -> dict:
    name, url = name.strip(), url.strip().rstrip("/")
    if not re.fullmatch(r"[\w .-]{1,40}", name):
        raise ServerError("Nome non valido")
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ServerError("Indirizzo non valido (es. http://192.168.1.20:8080)")
    items = list_servers()
    if any(s["name"] == name for s in items):
        raise ServerError(f"Esiste già un server '{name}'")
    item = {"name": name, "url": url, "host": u.hostname}
    _save(items + [item])
    return item


def remove(name: str) -> None:
    _save([s for s in list_servers() if s["name"] != name])


def ping(name: str) -> dict:
    s = next((x for x in list_servers() if x["name"] == name), None)
    if not s:
        raise ServerError(f"Server '{name}' sconosciuto")
    try:
        with urllib.request.urlopen(f"{s['url']}/api/auth/hint", timeout=3) as r:
            data = json.loads(r.read())
        return {"online": True, "version": data.get("version")}
    except Exception as exc:  # unreachable, refused, timeout...
        return {"online": False, "error": str(getattr(exc, "reason", exc))}
