"""A fixed catalogue of ready-made labs shown in «Genera lab» (category «I miei lab»).

Each entry is a saved lab under <home>/scenario_labs/<id>/ (lab.yml + configs/default/*.cfg
+ instructions.md + meta.json). Picking one in the generator clones it into a brand-new lab.
Labs are added here by `netso import-labs` (see labconvert) or by hand.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import settings
from .models import Lab
from .topology import dump_lab_yaml, parse_lab_yaml


def catalog_dir() -> Path:
    return settings.home / "scenario_labs"


def _read_text(p: Path) -> str:
    """Read a catalogue text file tolerating non-UTF-8 bytes (configs exported from EVE-NG/PNETLab
    are often Windows-1252/Latin-1, e.g. byte 0xa3 = £), so a stray byte can't break «Genera lab»."""
    data = p.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.strip()).strip("-").lower()
    return s or "lab"


def list_catalog() -> list[dict]:
    """Metadata for every catalogue lab, sorted by category then label."""
    d = catalog_dir()
    out = []
    if d.is_dir():
        for sub in sorted(d.iterdir()):
            meta, lab = sub / "meta.json", sub / "lab.yml"
            if lab.exists() and meta.exists():
                try:
                    out.append({"id": sub.name, **json.loads(_read_text(meta))})
                except (ValueError, OSError):
                    continue
    return sorted(out, key=lambda m: (m.get("cat", ""), m.get("label", "")))


def load_catalog(cid: str) -> tuple[Lab, dict[str, str], str]:
    d = catalog_dir() / cid
    if not (d / "lab.yml").exists():
        raise FileNotFoundError(f"Lab di catalogo '{cid}' inesistente")
    lab = parse_lab_yaml(_read_text(d / "lab.yml"))
    base = d / "configs" / "default"
    cfgs = {f.stem: _read_text(f) for f in sorted(base.glob("*.cfg"))} if base.is_dir() else {}
    instr = _read_text(d / "instructions.md") if (d / "instructions.md").exists() else lab.instructions
    return lab, cfgs, instr


def save_catalog(cid: str, lab: Lab, configs: dict[str, str], meta: dict, instructions: str = "") -> None:
    d = catalog_dir() / cid
    (d / "configs" / "default").mkdir(parents=True, exist_ok=True)
    (d / "lab.yml").write_text(dump_lab_yaml(lab))
    for node, text in configs.items():
        (d / "configs" / "default" / f"{node}.cfg").write_text(text)
    if instructions:
        (d / "instructions.md").write_text(instructions)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
