"""Lab snapshots (GNS3 style): topology, startup configs and the VM disks at a point in time.

<lab>/snapshots/<name>/  lab.yml + configs/ + meta.json
VM disks use qcow2 internal snapshots on the node overlays (`qemu-img snapshot -c/-a/-d netso-<name>`), so a
snapshot costs almost no space and no copy. Containers are recreated from their image: their state is not saved.
"""
from __future__ import annotations

import json
import shutil
import time

from . import topology as topo
from .hostcmd import Runner
from .models import NAME_RE


class SnapshotError(ValueError):
    pass


def snap_root(lab: str):
    return topo.lab_dir(lab) / "snapshots"


def _dir(lab: str, name: str):
    if not NAME_RE.match(name):
        raise SnapshotError("Nome istantanea non valido (lettere, numeri, _ e -, max 32)")
    return snap_root(lab) / name


def _overlays(lab: str):
    nodes = topo.lab_dir(lab) / "nodes"
    return sorted(nodes.glob("*/disk*.qcow2")) if nodes.is_dir() else []


def list_snapshots(lab: str) -> list[dict]:
    out = []
    root = snap_root(lab)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        try:
            out.append(json.loads((d / "meta.json").read_text()))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda m: m.get("created", 0), reverse=True)


def _require_stopped(running: list[str]) -> None:
    if running:
        raise SnapshotError(f"Ferma prima le VM accese ({', '.join(running)}): i dischi si fotografano a macchina spenta")


def create(r: Runner, lab: str, name: str, description: str, running_vms: list[str]) -> dict:
    d = _dir(lab, name)
    if d.exists():
        raise SnapshotError(f"Esiste già un'istantanea '{name}'")
    _require_stopped(running_vms)
    d.mkdir(parents=True)
    shutil.copy2(topo.lab_file(lab), d / "lab.yml")
    if topo.configs_dir(lab).is_dir():
        shutil.copytree(topo.configs_dir(lab), d / "configs")
    disks = []
    for ov in _overlays(lab):
        r.run(["qemu-img", "snapshot", "-c", f"netso-{name}", str(ov)])
        disks.append(str(ov.relative_to(topo.lab_dir(lab))))
    meta = {"name": name, "description": description, "created": time.time(), "disks": disks,
            "nodes": len(topo.load_lab(lab).nodes)}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def restore(r: Runner, lab: str, name: str, running_vms: list[str]) -> dict:
    d = _dir(lab, name)
    if not (d / "meta.json").exists():
        raise SnapshotError(f"Istantanea '{name}' inesistente")
    _require_stopped(running_vms)
    meta = json.loads((d / "meta.json").read_text())
    shutil.copy2(d / "lab.yml", topo.lab_file(lab))
    shutil.rmtree(topo.configs_dir(lab), ignore_errors=True)
    if (d / "configs").is_dir():
        shutil.copytree(d / "configs", topo.configs_dir(lab))
    saved = set(meta.get("disks", []))
    for ov in _overlays(lab):
        rel = str(ov.relative_to(topo.lab_dir(lab)))
        if rel in saved:
            r.run(["qemu-img", "snapshot", "-a", f"netso-{name}", str(ov)])
        else:  # disk created after the snapshot: back to a clean disk
            r.log.append(f"rm {ov}")
            if not r.dry_run:
                ov.unlink(missing_ok=True)
    return meta


def delete(r: Runner, lab: str, name: str) -> None:
    d = _dir(lab, name)
    if not d.exists():
        raise SnapshotError(f"Istantanea '{name}' inesistente")
    for ov in _overlays(lab):
        r.run(["qemu-img", "snapshot", "-d", f"netso-{name}", str(ov)], check=False)
    shutil.rmtree(d, ignore_errors=True)
