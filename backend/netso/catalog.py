"""Device catalog: user templates (create / edit / delete) and import of device definitions from
Cisco CML (node + image definitions, YAML) and GNS3 appliances (.gns3a, JSON).

Only the definition *formats* are read; no images are downloaded or shipped.
"""
from __future__ import annotations

import json
import re

import yaml

from . import topology as topo
from .config import PACKAGE_DIR, settings
from .models import Template


class CatalogError(ValueError):
    pass


# ------------------------------------------------------------------ user templates

def builtin_names() -> set[str]:
    return {f.stem for f in (PACKAGE_DIR / "templates").glob("*.y*ml")}


def builtin_title(name: str) -> str | None:
    """Display name of a built-in device (kept when the user overrides it with an older file)."""
    f = PACKAGE_DIR / "templates" / f"{name}.yml"
    try:
        return (yaml.safe_load(f.read_text()) or {}).get("title") if f.is_file() else None
    except yaml.YAMLError:
        return None


def user_file(name: str):
    return settings.user_template_dir() / f"{name}.yml"


def origin(name: str) -> str:
    if user_file(name).exists():
        return "modificato" if name in builtin_names() else "utente"
    return "integrato"


def dump_template(t: Template) -> str:
    data = t.model_dump(exclude_none=True, exclude_defaults=True)
    data = {"name": t.name, "kind": t.kind, **{k: v for k, v in data.items() if k not in ("name", "kind")}}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200)


def save_template(t: Template, old_name: str | None = None) -> None:
    d = settings.user_template_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CatalogError(f"Cartella dei template {d} non scrivibile: {exc.strerror}") from exc
    if old_name and old_name != t.name:
        if t.name in topo.load_templates():
            raise CatalogError(f"Esiste già un dispositivo '{t.name}'")
        user_file(old_name).unlink(missing_ok=True)
    user_file(t.name).write_text(dump_template(t))


def delete_template(name: str, in_use: list[str]) -> str:
    f = user_file(name)
    if not f.exists():
        raise CatalogError("I dispositivi integrati non si possono eliminare (solo modificare)")
    if in_use and name not in builtin_names():
        raise CatalogError(f"Usato nei lab: {', '.join(in_use[:5])}{'…' if len(in_use) > 5 else ''}")
    f.unlink()
    return "ripristinato" if name in builtin_names() else "eliminato"


def free_name(base: str, taken: set[str]) -> str:
    n = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-_").lower() or "device"
    if not n[0].isalpha():
        n = "d-" + n
    n = n[:32]
    root, k = n, 1
    while n in taken:
        k += 1
        n = f"{root[:28]}-{k}"
    return n


# ------------------------------------------------------------------ CML

CML_ICONS = {"router": "router", "switch": "switch", "server": "server", "host": "host", "firewall": "firewall",
             "cloud": "cloud", "access_point": "host", "wan_emulator": "server", "hub": "bridge", "l3switch": "l3switch"}
NIC = {"virtio": "virtio-net-pci", "e1000": "e1000", "vmxnet3": "vmxnet3", "rtl8139": "rtl8139", "e1000e": "e1000e"}


def cml_node_definition(doc: dict, taken: set[str], images: list[dict]) -> Template | None:
    nid = str(doc.get("id") or "")
    sim = (doc.get("sim") or {}).get("linux_native") or {}
    driver = str(sim.get("driver") or "")
    if driver in ("external_connector", "unmanaged_switch") or not nid:
        return None
    ui, gen = doc.get("ui") or {}, doc.get("general") or {}
    ifs = ((doc.get("device") or {}).get("interfaces")) or {}
    physical = [str(x) for x in ifs.get("physical") or []]
    count = int(ifs.get("default_count") or len(physical) or 4)
    prov = ((doc.get("configuration") or {}).get("provisioning")) or {}
    files = prov.get("files") or []
    media = str(prov.get("media_type") or "")
    my_images = [i for i in images if i.get("node_definition_id") == nid]
    t = Template(
        name=free_name(nid, taken), kind="qemu",
        description=str(ui.get("description") or gen.get("description") or ui.get("label") or nid).strip().splitlines()[0][:200],
        icon=CML_ICONS.get(str(ui.get("icon") or gen.get("nature") or ""), "router"),
        image=my_images[0]["id"] if my_images else nid,
        cpus=int(sim.get("cpus") or 1), ram=int(sim.get("ram") or 512),
        nics=max(1, min(count, len(physical)) if physical else count),
        nic_model=NIC.get(str(sim.get("nic_driver") or ""), str(sim.get("nic_driver") or "virtio-net-pci")),
        disk_bus=str(sim.get("disk_driver") or "virtio") if str(sim.get("disk_driver") or "virtio") in ("virtio", "ide", "sata") else "virtio",
        console="vnc" if sim.get("video") and not physical else "telnet",
        interfaces=physical or None,
        config_method=("iso" if media == "iso" else "disk") if files else "none",
        config_file=str(files[0].get("name")) if files else None,
        config_label=str(prov.get("volume_name") or "config")[:32],
        image_files=[{"filename": f, "version": i.get("label") or i["id"]} for i in my_images for f in _cml_disks(i)] or None,
        source="CML node definition", vendor="Cisco CML" if nid in ("iosv", "iosvl2", "csr1000v", "cat8000v", "iosxrv9000", "nxosv9000", "asav") else None,
    )
    taken.add(t.name)
    return t


def _cml_disks(img: dict) -> list[str]:
    return [str(img[k]) for k in sorted(img) if k.startswith("disk_image") and img[k]]


# ------------------------------------------------------------------ GNS3 appliances

GNS3_ICONS = {"router": "router", "switch": "switch", "multilayer_switch": "l3switch", "firewall": "firewall", "guest": "host"}


def _gns3_ports(app: dict, n: int) -> list[str] | None:
    fmt = app.get("port_name_format")
    if not fmt:
        return None
    seg = int(app.get("port_segment_size") or 0)
    first = app.get("first_port_name")
    out = [first] if first else []
    i = 0
    while len(out) < n:
        s, p = (i // seg, i % seg) if seg else (0, i)
        try:
            out.append(fmt.format(i, port0=p, port1=p + 1, segment0=s, segment1=s + 1))
        except (KeyError, IndexError, ValueError):
            return None
        i += 1
    return out if len(set(out)) == len(out) else None


def gns3_appliance(app: dict, taken: set[str]) -> Template | None:
    name = free_name(str(app.get("name") or "appliance"), taken)
    icon = GNS3_ICONS.get(str(app.get("category") or ""), "router")
    desc = str(app.get("description") or app.get("name") or "").strip().splitlines()[0][:200] if app.get("description") or app.get("name") else ""
    vendor = app.get("vendor_name")
    if "docker" in app:
        d = app["docker"] or {}
        env = d.get("environment")
        env = dict(line.split("=", 1) for line in env.splitlines() if "=" in line) if isinstance(env, str) else (env or {})
        t = Template(name=name, kind="docker", description=desc, icon=icon if icon != "host" else "container", image=d.get("image"),
                     nics=max(1, int(d.get("adapters") or 1)), console="exec", env=env,
                     cmd=d["start_command"].split() if d.get("start_command") else None, source="GNS3 appliance", vendor=vendor)
    elif "qemu" in app:
        q = app["qemu"] or {}
        n = max(1, int(q.get("adapters") or 1))
        versions = app.get("versions") or []
        files = {i.get("filename"): i for i in app.get("images") or []}
        image_files = []
        for v in versions:
            for slot, fname in sorted((v.get("images") or {}).items()):
                if slot.endswith("disk_image") and fname in files:
                    i = files[fname]
                    image_files.append({"filename": fname, "md5": i.get("md5sum"), "size": i.get("filesize"), "version": v.get("name")})
        folder = free_name(f"{name}-{versions[0].get('name')}", set()) if versions else name
        bus = str(q.get("hda_disk_interface") or "virtio")
        t = Template(name=name, kind="qemu", description=desc, icon=icon, image=folder,
                     cpus=int(q.get("cpus") or 1), ram=int(q.get("ram") or 512), nics=n,
                     nic_model=str(q.get("adapter_type") or "e1000"), disk_bus=bus if bus in ("virtio", "ide", "sata") else "virtio",
                     console="vnc" if q.get("console_type") == "vnc" else "telnet",
                     extra_args=str(q.get("options") or "").split(), interfaces=_gns3_ports(app, n),
                     image_files=image_files or None, source="GNS3 appliance", vendor=vendor)
    else:
        return None
    taken.add(t.name)
    return t


# ------------------------------------------------------------------ import entry point

def parse_definitions(files: list[tuple[str, bytes]]) -> dict:
    """Parse one or more definition files. Returns {templates: [Template], images: [cml image defs], warnings}."""
    taken = set(topo.load_templates())
    docs_cml_nodes, docs_cml_images, apps, natives, warnings = [], [], [], [], []
    for fname, data in files:
        text = data.decode("utf-8", "replace")
        try:
            doc = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
        except (ValueError, yaml.YAMLError) as exc:
            warnings.append(f"{fname}: non leggibile ({exc})")
            continue
        if not isinstance(doc, dict):
            warnings.append(f"{fname}: formato non riconosciuto")
        elif "appliance_id" in doc or ("registry_version" in doc and ("qemu" in doc or "docker" in doc)):
            apps.append(doc)
        elif "node_definition_id" in doc:
            docs_cml_images.append(doc)
        elif "sim" in doc and "id" in doc:
            docs_cml_nodes.append(doc)
        elif "kind" in doc and "name" in doc:
            natives.append(doc)
        else:
            warnings.append(f"{fname}: non è una node/image definition CML, un'appliance GNS3 o un template NetSO")
    out = []
    for doc in docs_cml_nodes:
        t = cml_node_definition(doc, taken, docs_cml_images)
        if t:
            out.append(t)
        else:
            warnings.append(f"{doc.get('id')}: tipo CML non necessario in NetSO (c'è già switch/rete esterna)")
    for app in apps:
        t = gns3_appliance(app, taken)
        if t:
            out.append(t)
            if t.kind == "qemu" and t.image_files:
                warnings.append(f"{t.name}: copia i dischi nella cartella immagine '{t.image}' (versioni: "
                                f"{', '.join(sorted({f['version'] for f in t.image_files if f.get('version')}))})")
        else:
            warnings.append(f"{app.get('name')}: appliance senza sezione qemu/docker, ignorata")
    for doc in natives:
        try:
            t = Template(**doc)
            if t.name in taken:
                t.name = free_name(t.name, taken)
            taken.add(t.name)
            out.append(t)
        except Exception as exc:
            warnings.append(f"{doc.get('name')}: template non valido ({exc})")
    nodes = {d.get("id") for d in docs_cml_nodes}
    for img in docs_cml_images:
        if img.get("node_definition_id") not in nodes:
            warnings.append(f"Image definition {img.get('id')}: importa anche la node definition '{img.get('node_definition_id')}'")
    return {"templates": out, "images": docs_cml_images, "warnings": warnings}
