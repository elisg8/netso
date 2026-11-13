"""Lab library: folders (EVE-NG style file manager) and which folder each lab lives in.

Lab names stay globally unique (they name bridges and containers on the host), so a folder
is only an organisational path kept in <home>/library.json:
    {"folders": ["CCNA", "CCNA/OSPF"], "labs": {"ospf-base": "CCNA/OSPF"}}
"""
from __future__ import annotations

import json
import re
import threading

from .config import settings

_SEG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,39}$")
MAX_DEPTH = 6
_lock = threading.Lock()


class LibraryError(ValueError):
    pass


def _file():
    return settings.home / "library.json"


def _load() -> dict:
    try:
        data = json.loads(_file().read_text())
    except (OSError, ValueError):
        data = {}
    return {"folders": list(data.get("folders", [])), "labs": dict(data.get("labs", {}))}


def _save(data: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    data["folders"] = sorted(set(data["folders"]), key=str.lower)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(f)


def check_path(path: str | None) -> str:
    """Normalise a folder path ("" is the root)."""
    path = (path or "").strip().strip("/")
    if not path:
        return ""
    parts = [p.strip() for p in path.split("/")]
    if len(parts) > MAX_DEPTH:
        raise LibraryError(f"At most {MAX_DEPTH} folder levels")
    for p in parts:
        if not _SEG.match(p) or p in (".", ".."):
            raise LibraryError(f"Invalid folder name: '{p}' (letters, numbers, space, _ . -)")
    return "/".join(parts)


def _parent(path: str) -> str:
    return path.rpartition("/")[0]


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "/")


def folders() -> list[str]:
    return _load()["folders"]


def folder_of(lab: str) -> str:
    return _load()["labs"].get(lab, "")


def folder_map() -> dict[str, str]:
    return _load()["labs"]


def create_folder(path: str) -> str:
    path = check_path(path)
    if not path:
        raise LibraryError("Missing folder name")
    with _lock:
        data = _load()
        if path in data["folders"]:
            raise LibraryError(f"The folder '{path}' already exists")
        # create missing parents too
        p = path
        while p:
            data["folders"].append(p)
            p = _parent(p)
        _save(data)
    return path


def rename_folder(path: str, new: str) -> str:
    path, new = check_path(path), check_path(new)
    if not path or not new:
        raise LibraryError("Missing folder name")
    with _lock:
        data = _load()
        if path not in data["folders"]:
            raise LibraryError(f"Folder '{path}' does not exist")
        if new != path and new in data["folders"]:
            raise LibraryError(f"The folder '{new}' already exists")
        if _inside(new, path) and new != path:
            raise LibraryError("You cannot move a folder inside itself")
        move = lambda p: new + p[len(path):] if _inside(p, path) else p  # noqa: E731
        data["folders"] = [move(f) for f in data["folders"]]
        p = _parent(new)
        while p:
            data["folders"].append(p)
            p = _parent(p)
        data["labs"] = {lab: move(f) for lab, f in data["labs"].items()}
        _save(data)
    return new


def delete_folder(path: str, existing_labs: list[str]) -> None:
    path = check_path(path)
    with _lock:
        data = _load()
        if path not in data["folders"]:
            raise LibraryError(f"Folder '{path}' does not exist")
        inside = [lab for lab, f in data["labs"].items() if lab in existing_labs and _inside(f, path)]
        if inside:
            raise LibraryError(f"The folder contains {len(inside)} labs: move or delete them first")
        data["folders"] = [f for f in data["folders"] if not _inside(f, path)]
        data["labs"] = {lab: f for lab, f in data["labs"].items() if not _inside(f, path)}
        _save(data)


def set_lab_folder(lab: str, folder: str) -> str:
    folder = check_path(folder)
    with _lock:
        data = _load()
        if folder and folder not in data["folders"]:
            raise LibraryError(f"Folder '{folder}' does not exist")
        if folder:
            data["labs"][lab] = folder
        else:
            data["labs"].pop(lab, None)
        _save(data)
    return folder


def forget_lab(lab: str) -> None:
    with _lock:
        data = _load()
        if data["labs"].pop(lab, None) is not None:
            _save(data)
