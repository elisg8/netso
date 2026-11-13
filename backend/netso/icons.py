"""Custom node icons the user uploads from the UI (SVG/PNG…), stored in <home>/icons/.

Templates (and so nodes) point at them with `icon: custom:<name>`; the API serves the file to the
browser as a plain <img>. NetSO ships no vendor logos — these are the user's own images.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import settings

# extension -> media type served to the browser
ALLOWED = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
           ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_BYTES = 2 * 1024 * 1024  # an icon never needs more than a couple of MB

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,47}$")


class IconError(Exception):
    pass


def root() -> Path:
    return settings.home / "icons"


def check_name(name: str) -> str:
    name = (name or "").strip()
    if "/" in name or ".." in name or not _NAME.match(name):
        raise IconError("Invalid icon name (letters, numbers, space and . _ -, max 48)")
    return name


def _find(name: str) -> Path | None:
    name = check_name(name)
    d = root()
    for ext in ALLOWED:
        p = d / f"{name}{ext}"
        if p.is_file():
            return p
    return None


def listing() -> list[dict]:
    d = root()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix.lower() in ALLOWED:
            out.append({"name": p.stem, "ext": p.suffix.lower().lstrip("."), "mtime": p.stat().st_mtime})
    return out


def media_type(p: Path) -> str:
    return ALLOWED.get(p.suffix.lower(), "application/octet-stream")


def path(name: str) -> Path:
    p = _find(name)
    if not p:
        raise FileNotFoundError(f"Icon '{name}' does not exist")
    return p


def save(name: str, filename: str, data: bytes) -> dict:
    name = check_name(name)
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise IconError(f"Unsupported format: use {', '.join(sorted(ALLOWED))}")
    if not data:
        raise IconError("Empty file")
    if len(data) > MAX_BYTES:
        raise IconError("Icon too large (max 2 MB)")
    if ext == ".svg" and b"<script" in data.lower():  # served via <img> (no script run), but refuse it anyway
        raise IconError("SVG with <script> is not allowed")
    d = root()
    d.mkdir(parents=True, exist_ok=True)
    for other in ALLOWED:  # one file per name: drop a previous upload with another extension
        (d / f"{name}{other}").unlink(missing_ok=True)
    (d / f"{name}{ext}").write_bytes(data)
    return {"name": name, "ext": ext.lstrip(".")}


def delete(name: str) -> None:
    p = _find(name)
    if not p:
        raise FileNotFoundError(f"Icon '{name}' does not exist")
    p.unlink(missing_ok=True)
