"""Image manager: QEMU disk images under <images>/<folder>/ (same layout as EVE-NG/PNETLab).

Upload (streamed by the API), download from a URL the user gives, conversion to qcow2 (vmdk, vdi, vhd(x),
raw/img, ova), MD5 checksums checked against GNS3 appliance / CML image definitions, and import of the
Cisco CML reference-platform ISO (node + image definitions + disks). No vendor image is shipped or fetched
by NetSO itself.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

import yaml

from . import topology as topo
from .config import settings
from .drivers.dynamips import IOS_SUFFIXES
from .drivers.iol import iol_problem, is_iol_file
from .drivers.qemu import DISK_SUFFIXES
from .hostcmd import Runner
from .jobs import Job

IMG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
CONVERT = (".vmdk", ".vdi", ".vhd", ".vhdx")
ISO_DIR = "_iso"  # uploaded ISOs (e.g. CML refplat), not an image itself


class ImageError(ValueError):
    pass


def check_name(name: str, what: str = "Nome immagine") -> str:
    if name != ISO_DIR and not IMG_RE.match(name or ""):
        raise ImageError(f"{what} non valido: lettere, numeri, . _ + - (max 100)")
    return name


def folder(name: str) -> Path:
    return settings.images / check_name(name)


# ------------------------------------------------------------------ listing

def _md5_db(d: Path) -> dict:
    try:
        return json.loads((d / ".netso-md5.json").read_text())
    except (OSError, ValueError):
        return {}


def describe(templates: dict | None = None) -> list[dict]:
    templates = templates if templates is not None else topo.load_templates()
    expected = {}  # md5 -> (template, version)
    for t in templates.values():
        for f in t.image_files or []:
            if f.get("md5"):
                expected[f["md5"].lower()] = (t.name, f.get("version"))
    used: dict[str, set[str]] = {}
    for t in templates.values():
        if t.kind in ("qemu", "dynamips", "iol") and t.image:
            used.setdefault(t.image, set()).add(f"template {t.name}")
    for lab in topo.list_labs():
        try:
            for n in topo.load_lab(lab).nodes:
                if n.image:
                    used.setdefault(n.image, set()).add(f"lab {lab}")
        except Exception:
            continue
    out = []
    root = settings.images
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        db = _md5_db(d)
        files = []
        for f in sorted(d.iterdir()):
            if f.name.startswith(".") or not f.is_file():
                continue
            st = f.stat()
            rec = db.get(f.name) if db.get(f.name, {}).get("mtime") == st.st_mtime else None
            md5 = rec["md5"] if rec else None
            files.append({"name": f.name, "size": st.st_size, "disk": f.suffix in DISK_SUFFIXES or f.suffix.lower() in IOS_SUFFIXES, "partial": f.suffix == ".part",
                          "md5": md5, "verified": expected.get(md5) and {"template": expected[md5][0], "version": expected[md5][1]}})
        out.append({"name": d.name, "disks": [f["name"] for f in files if f["disk"]], "files": files,
                    "size": sum(f["size"] for f in files), "used_by": sorted(used.get(d.name, set()))})
    return out


def folders_with_disks() -> list[str]:
    root = settings.images
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_dir() and not d.name.startswith((".", "_")) and any(
                f.is_file() and (f.suffix in DISK_SUFFIXES or f.suffix.lower() in IOS_SUFFIXES)
                or d.name.lower().startswith("iol") and is_iol_file(f) for f in d.iterdir()):
            out.append(d.name)
    return out


def iol_problems() -> list[str]:
    """iol-* folders that a Cisco IOL node cannot use, and why (shown when adding an IOL node)."""
    root = settings.images
    return [iol_problem(d) for d in sorted(root.glob("iol*")) if d.is_dir() and not any(is_iol_file(f) for f in d.iterdir())] \
        if root.is_dir() else []


def choices(t, folders: list[str], docker: list[str]) -> list[str]:
    """Images a node of this template can use, as in EVE-NG/PNETLab: folders named <type>-<version>."""
    if t.kind == "docker":
        return docker
    if t.kind not in ("qemu", "dynamips", "iol") or not t.image:
        return []
    if t.kind == "iol":  # every IOL image (folders iol-*); the driver also finds them by itself
        return [f for f in folders if f.lower().startswith("iol")]
    if t.image not in folders:  # default name not there yet (e.g. cisco-c8000v): nothing to guess from
        return [f for f in folders if t.kind == "dynamips" and f.lower().startswith(t.machine + "-")]
    prefix = t.image.split("-", 1)[0]
    return [f for f in folders if f == t.image or f.startswith(prefix + "-")]


def isos() -> list[dict]:
    d = settings.images / ISO_DIR
    return [{"name": f.name, "size": f.stat().st_size} for f in sorted(d.glob("*.iso"))] if d.is_dir() else []


# ------------------------------------------------------------------ conversion

def _fmt(r: Runner, path: Path) -> str:
    if r.dry_run:
        return "qcow2" if path.suffix == ".qcow2" else "raw"
    out = r.run(["qemu-img", "info", "--output=json", str(path)])
    return json.loads(out).get("format", "raw")


def finalize(r: Runner, path: Path, job: Job | None = None) -> list[str]:
    """Turn an uploaded / downloaded file into usable qcow2 disks. Returns the resulting disk names."""
    d = path.parent
    suf = path.suffix.lower()
    if suf == ".ova":
        if job:
            job.update(message="Estrazione OVA…")
        disks = []
        with tarfile.open(path) as tar:
            for m in tar.getmembers():
                if m.isfile() and m.name.lower().endswith(".vmdk") and "/" not in m.name.strip("./"):
                    tar.extract(m, d, filter="data")
                    disks += finalize(r, d / m.name, job)
        path.unlink(missing_ok=True)
        if not disks:
            raise ImageError("Nessun disco .vmdk nell'OVA")
        return disks
    if suf == ".iso":
        return []  # CD images stay as they are (e.g. CML refplat, installers)
    if suf in CONVERT or suf in (".img", ".raw"):
        fmt = _fmt(r, path) if suf in (".img", ".raw") else suf.lstrip(".").replace("vhd", "vpc").replace("vpcx", "vhdx")
        dst = path.with_suffix(".qcow2")
        if fmt == "qcow2":
            if not r.dry_run:
                path.rename(dst)
            return [dst.name]
        if job:
            job.update(None, f"Conversione {path.name} in qcow2…")
        r.run(["qemu-img", "convert", "-O", "qcow2", "-f", fmt, str(path), str(dst)])
        if not r.dry_run:
            path.unlink(missing_ok=True)
        return [dst.name]
    if suf == ".qcow2":
        return [path.name]
    raise ImageError(f"Formato {suf} non supportato (qcow2, vmdk, vdi, vhd, vhdx, img, raw, ova, iso)")


# ------------------------------------------------------------------ download from a user-given URL

def fetch(r: Runner, job: Job, name: str, url: str, filename: str | None = None) -> str:
    if not re.match(r"^https?://", url):
        raise ImageError("Solo indirizzi http:// o https://")
    d = folder(name)
    fname = filename or url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "disk.qcow2"
    check_name(fname, "Nome file")
    d.mkdir(parents=True, exist_ok=True)
    part = d / f"{fname}.part"
    if r.dry_run:
        r.log.append(f"download {url} -> {d / fname}")
        (d / fname).write_bytes(b"")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "NetSO"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                job.update(done / total if total else None, f"{done >> 20} MB" + (f" di {total >> 20} MB" if total else ""))
        part.rename(d / fname)
    disks = finalize(r, d / fname, job)
    return f"Pronta: {', '.join(disks) or fname}"


# ------------------------------------------------------------------ checksums

def checksum(job: Job, name: str) -> str:
    d = folder(name)
    db = _md5_db(d)
    files = [f for f in sorted(d.iterdir()) if f.is_file() and not f.name.startswith(".") and f.suffix != ".part"]
    total = sum(f.stat().st_size for f in files) or 1
    done = 0
    for f in files:
        h = hashlib.md5()
        with open(f, "rb") as fh:
            while chunk := fh.read(1 << 22):
                h.update(chunk)
                done += len(chunk)
                job.update(done / total, f.name)
        db[f.name] = {"md5": h.hexdigest(), "mtime": f.stat().st_mtime}
    (d / ".netso-md5.json").write_text(json.dumps(db, indent=2))
    return f"{len(files)} file controllati"


def delete(name: str, force: bool = False) -> None:
    d = folder(name)
    if not d.is_dir():
        raise FileNotFoundError(f"Immagine '{name}' inesistente")
    users = [u for u in describe() if u["name"] == name]
    labs = [u for u in (users[0]["used_by"] if users else []) if u.startswith("lab ")]
    if labs and not force:
        raise ImageError(f"Usata da {', '.join(labs)}: i dischi dei nodi dipendono da questa immagine")
    shutil.rmtree(d)


# ------------------------------------------------------------------ CML reference platform ISO

def _bsdtar() -> str:
    exe = shutil.which("bsdtar")
    if not exe:
        raise ImageError("Serve 'bsdtar' (pacchetto libarchive-tools) per leggere la ISO di CML")
    return exe


def _iso_path(iso: str) -> Path:
    p = settings.images / ISO_DIR / check_name(iso, "Nome ISO")
    if not p.is_file():
        raise ImageError(f"ISO '{iso}' non trovata in {settings.images / ISO_DIR}")
    return p


def scan_refplat(iso: str) -> dict:
    """Node and image definitions found in a CML refplat ISO."""
    p, tar = _iso_path(iso), _bsdtar()
    names = subprocess.run([tar, "-tf", str(p)], capture_output=True, text=True, check=True).stdout.split()
    read = lambda m: yaml.safe_load(subprocess.run([tar, "-xOf", str(p), m], capture_output=True, check=True).stdout) or {}  # noqa: E731
    nodes = [read(m) for m in names if re.search(r"node-definitions/[^/]+\.ya?ml$", m)]
    imgs = []
    for m in names:
        if re.search(r"virl-base-images/[^/]+/[^/]+\.ya?ml$", m):
            doc = read(m)
            doc["_dir"] = m.rsplit("/", 1)[0]
            imgs.append(doc)
    return {"nodes": nodes, "images": imgs}


def import_refplat(r: Runner, job: Job, iso: str, image_ids: list[str]) -> str:
    from . import catalog
    found = scan_refplat(iso)
    p, tar = _iso_path(iso), _bsdtar()
    chosen = [i for i in found["images"] if i.get("id") in image_ids]
    if not chosen:
        raise ImageError("Nessuna immagine selezionata")
    for k, img in enumerate(chosen):
        job.update(k / len(chosen), f"Estrazione {img['id']}…")
        dest = folder(str(img["id"]))
        dest.mkdir(parents=True, exist_ok=True)
        for disk in [img[x] for x in sorted(img) if x.startswith("disk_image") and img[x]]:
            out = dest / Path(str(disk)).name
            with open(out, "wb") as fh:
                subprocess.run([tar, "-xOf", str(p), f"{img['_dir']}/{disk}"], stdout=fh, check=True)
    # one template per node definition used by the chosen images (unless it exists already)
    wanted = {i.get("node_definition_id") for i in chosen}
    templates = topo.load_templates()
    created = []
    taken = set(templates)
    for nd in found["nodes"]:
        if nd.get("id") not in wanted:
            continue
        t = catalog.cml_node_definition(nd, taken, [{k: v for k, v in i.items() if k != "_dir"} for i in chosen])
        if t and not any(x.source == "CML node definition" and x.image == t.image for x in templates.values()):
            catalog.save_template(t)
            created.append(t.name)
    return f"{len(chosen)} immagini importate" + (f", dispositivi creati: {', '.join(created)}" if created else "")
