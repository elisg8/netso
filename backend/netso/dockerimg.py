"""Docker images for container nodes: list, pull from a registry, load a tar (docker save / vendor tarballs
like cEOS that need `docker import`), remove. The user picks what to pull; NetSO ships no image."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import topology as topo
from .config import settings
from .hostcmd import Runner
from .jobs import Job

REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,254}$")
UPLOAD_DIR = "_docker"  # under the images dir: uploaded tarballs waiting to be loaded
# vendor images that are not on a public registry: they come from the vendor's site as a tar
NOT_PULLABLE = ("Arista", "Juniper", "Cisco", "SONiC")


class DockerImageError(ValueError):
    pass


def check_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not REF_RE.match(ref):
        raise DockerImageError(f"Nome immagine Docker non valido: '{ref}' (es. quay.io/frrouting/frr:10.2.1)")
    return ref


def normalize(ref: str) -> str:
    """`alpine` -> `alpine:latest`, as `docker images` shows it."""
    last = ref.rsplit("/", 1)[-1]
    return ref if ":" in last or "@" in ref else f"{ref}:latest"


def _local(r: Runner) -> list[dict]:
    out = r.run(["docker", "images", "--format", "{{json .}}"], check=False)
    rows = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ref = f"{d.get('Repository')}:{d.get('Tag')}" if d.get("Tag") not in (None, "<none>") else d.get("ID")
        rows.append({"ref": ref, "id": d.get("ID"), "size": d.get("Size"), "created": d.get("CreatedSince")})
    return rows


def describe(r: Runner) -> dict:
    templates = topo.load_templates()
    used: dict[str, set[str]] = {}
    wanted: list[dict] = []
    for t in templates.values():
        if t.kind == "docker" and t.image:
            used.setdefault(normalize(t.image), set()).add(f"template {t.name}")
    for lab in topo.list_labs():
        try:
            lb = topo.load_lab(lab)
        except Exception:
            continue
        for n in lb.nodes:
            t = templates.get(n.template)
            if t and t.kind == "docker" and (n.image or t.image):
                used.setdefault(normalize(n.image or t.image), set()).add(f"lab {lab}")
    local = _local(r)
    have = {x["ref"] for x in local}
    for t in sorted(templates.values(), key=lambda t: t.name):
        if t.kind == "docker" and t.image:
            ref = normalize(t.image)
            wanted.append({"template": t.name, "ref": t.image, "present": ref in have,
                           "pullable": (t.vendor or "") not in NOT_PULLABLE, "vendor": t.vendor})
    for x in local:
        x["used_by"] = sorted(used.get(x["ref"], set()))
    return {"images": local, "templates": wanted, "available": r.dry_run or bool(shutil.which("docker"))}


# ------------------------------------------------------------------ pull

def pull(r: Runner, job: Job, ref: str) -> str:
    ref = check_ref(ref)
    if r.dry_run:
        r.log.append(f"docker pull {ref}")
        return f"Scaricata {ref}"
    proc = subprocess.Popen(["docker", "pull", ref], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    layers: dict[str, str] = {}
    tail = []
    for line in proc.stdout:
        line = line.strip()
        tail = (tail + [line])[-5:]
        m = re.match(r"^([0-9a-f]{12}): (.+)$", line)
        if m:
            layers[m.group(1)] = m.group(2)
            done = sum(1 for s in layers.values() if s in ("Pull complete", "Already exists"))
            job.update(done / len(layers) if layers else None, f"{done}/{len(layers)} livelli · {m.group(2)}")
    if proc.wait() != 0:
        msg = " ".join(tail)
        if "denied" in msg or "not found" in msg or "unauthorized" in msg:
            msg += " — immagine inesistente o privata (per registry privati: `sudo docker login <registry>` sul server)"
        raise DockerImageError(f"docker pull {ref} non riuscito: {msg[:400]}")
    return f"Scaricata {ref}"


# ------------------------------------------------------------------ load a tarball

def upload_dir() -> Path:
    return settings.images / UPLOAD_DIR


def load(r: Runner, job: Job, path: Path, tag: str | None) -> str:
    """`docker load` (images saved with docker save, e.g. cRPD, vrnetlab builds); if the tar is a plain root
    filesystem (e.g. Arista cEOS-lab.tar.xz) fall back to `docker import` with the given name:tag."""
    tag = check_ref(tag) if tag else None
    try:
        job.update(None, f"docker load {path.name}…")
        out = r.run(["docker", "load", "-i", str(path)], fake=f"Loaded image: {tag or 'image:latest'}")
        loaded = re.findall(r"Loaded image(?: ID)?: (\S+)", out)
        if tag and loaded and loaded[0] != tag:
            r.run(["docker", "tag", loaded[0], tag])
        return f"Caricata {tag or ', '.join(loaded) or path.name}"
    except Exception as exc:
        if not tag:
            raise DockerImageError(f"docker load non riuscito ({str(exc)[-200:]}). Se è un tar di filesystem (es. cEOS) "
                                   "indica il nome:tag da dare all'immagine e riprova") from exc
        job.update(None, f"docker import {path.name} {tag}…")
        r.run(["docker", "import", str(path), tag])
        return f"Importata {tag} (docker import)"
    finally:
        if not r.dry_run:
            path.unlink(missing_ok=True)


def remove(r: Runner, ref: str, force: bool = False) -> None:
    ref = check_ref(ref)
    if not force:
        users = [u for x in describe(r)["images"] if x["ref"] == ref for u in x["used_by"] if u.startswith("lab ")]
        if users:
            raise DockerImageError(f"Usata da {', '.join(users)}")
    r.run(["docker", "rmi", *(["-f"] if force else []), ref])
