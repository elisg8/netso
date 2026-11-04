"""Import / export of labs from the other simulators.

Read:  containerlab (.clab.yml), Cisco CML (lab YAML), GNS3 (.gns3 / .gns3project), EVE-NG / PNETLab (.unl),
       plus NetSO's own YAML.
Write: containerlab and CML.

Only the file *formats* are implemented here (no code from those projects). Foreign devices are mapped onto
NetSO templates by name; anything unknown becomes a generic VM or container and is reported as a warning.
"""
from __future__ import annotations

import base64
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import yaml

from .models import NAME_RE, Annotation, Lab, Link, Node, Template

FORMATS = {"netso": "NetSO", "clab": "containerlab", "cml": "Cisco CML", "gns3": "GNS3", "unl": "EVE-NG / PNETLab"}


class ImportError_(ValueError):
    pass


# ------------------------------------------------------------------ neutral model

@dataclass
class FIface:
    name: str                # name in the source file (Gi0/1, e1-1, eth2, port3 ...)
    index: int | None = None  # position on the device when the source knows it (slot / adapter)


@dataclass
class FNode:
    name: str
    hint: str                # what the source calls it: kind / node_definition / template / image
    container: bool = False  # the source runs it as a container
    image: str | None = None
    x: float | None = None
    y: float | None = None
    cpus: int | None = None
    ram: int | None = None
    nics: int | None = None
    config: str | None = None
    description: str | None = None


@dataclass
class Foreign:
    fmt: str
    name: str = "lab"
    description: str = ""
    author: str = ""
    nodes: list[FNode] = field(default_factory=list)
    links: list[tuple[tuple[str, FIface], tuple[str, FIface]]] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)  # NetSO Annotation fields
    warnings: list[str] = field(default_factory=list)


@dataclass
class Imported:
    fmt: str
    lab: Lab
    configs: dict[str, str]
    mapping: list[dict]
    warnings: list[str]


# ------------------------------------------------------------------ detection & parsing

def detect(filename: str, data: bytes) -> str:
    low = filename.lower()
    if low.endswith((".gns3project", ".zip")) or data[:2] == b"PK":
        return "gns3"
    if low.endswith(".gns3"):
        return "gns3"
    if low.endswith(".unl") or data.lstrip()[:5] in (b"<?xml", b"<lab "):
        return "unl"
    text = data.decode("utf-8", "replace")
    if text.lstrip().startswith("{"):
        return "gns3"
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ImportError_(f"File non riconosciuto: {exc}") from exc
    if not isinstance(doc, dict):
        raise ImportError_("File non riconosciuto")
    if isinstance(doc.get("topology"), dict):
        return "clab"
    if isinstance(doc.get("lab"), dict) or ("nodes" in doc and "links" in doc and any(isinstance(n, dict) and "node_definition" in n for n in doc.get("nodes") or [])):
        return "cml"
    if isinstance(doc.get("nodes"), list):
        return "netso"
    raise ImportError_("Formato non riconosciuto: attesi containerlab, CML, GNS3, EVE-NG/PNETLab o NetSO")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_clab(text: str) -> Foreign:
    doc = yaml.safe_load(text) or {}
    topo = doc.get("topology") or {}
    f = Foreign("clab", name=str(doc.get("name") or "clab"))
    defaults, kinds = topo.get("defaults") or {}, topo.get("kinds") or {}
    for name, n in (topo.get("nodes") or {}).items():
        n = n or {}
        kind = n.get("kind") or defaults.get("kind") or "linux"
        image = n.get("image") or (kinds.get(kind) or {}).get("image") or defaults.get("image")
        labels = n.get("labels") or {}
        cfg = n.get("startup-config")
        # a single-line value that looks like a path is an external file; anything else is an embedded config
        if isinstance(cfg, str) and "\n" not in cfg.strip() and re.fullmatch(r"[\w./~-]+\.(cfg|conf|txt|partial|json|yml|yaml)|[\w.~-]*/[\w./-]+", cfg.strip()):
            f.warnings.append(f"{name}: startup-config '{cfg}' è un file esterno, non incluso")
            cfg = None
        vm = kind.startswith("vr-") or kind.startswith(("cisco_c8000v", "cisco_csr", "cisco_vios", "fortinet", "generic_vm", "juniper_vmx"))
        f.nodes.append(FNode(name=str(name), hint=f"{kind} {image or ''}", container=not vm and kind not in ("bridge", "ovs-bridge", "host"),
                             image=image if not vm else None, x=_num(labels.get("graph-posX")), y=_num(labels.get("graph-posY")),
                             config=cfg, description=f"containerlab kind {kind}"))
    for ln in topo.get("links") or []:
        eps = ln.get("endpoints")
        if not eps or len(eps) != 2:
            f.warnings.append(f"Link non supportato (tipo {ln.get('type', '?')}) ignorato")
            continue
        pair = []
        for e in eps:
            if isinstance(e, dict):
                pair.append((str(e.get("node")), FIface(str(e.get("interface")))))
            else:
                node, _, ifc = str(e).partition(":")
                pair.append((node, FIface(ifc)))
        f.links.append((pair[0], pair[1]))
    return f


def parse_cml(text: str) -> Foreign:
    doc = yaml.safe_load(text) or {}
    meta = doc.get("lab") or {}
    f = Foreign("cml", name=str(meta.get("title") or "cml-lab"), description=str(meta.get("description") or ""))
    ifmap: dict[tuple[str, str], tuple[str, FIface]] = {}
    for n in doc.get("nodes") or []:
        label = str(n.get("label") or n.get("id"))
        cfg = n.get("configuration")
        if isinstance(cfg, list):  # newer schema: [{name, content}]
            cfg = "\n".join(str(c.get("content") or "") for c in cfg if isinstance(c, dict)) or None
        nd = str(n.get("node_definition") or "")
        for i in n.get("interfaces") or []:
            if i.get("type") == "loopback":
                continue
            ifmap[(n.get("id"), i.get("id"))] = (label, FIface(str(i.get("label") or ""), _int(i.get("slot"))))
        if nd == "external_connector" and "nat" in str(cfg or "").lower():
            nd, cfg = "nat external_connector", None  # CML NAT connector = NetSO NAT cloud
        elif nd == "external_connector":
            cfg = None  # the bridge name, not a device config
        f.nodes.append(FNode(name=label, hint=f"{nd} {n.get('image_definition') or ''}", container=nd in ("alpine-docker",),
                             x=_num(n.get("x")), y=_num(n.get("y")), cpus=_int(n.get("cpus")), ram=_int(n.get("ram")),
                             config=cfg or None, description=f"CML {nd}"))
    for ln in doc.get("links") or []:
        a, b = ifmap.get((ln.get("n1"), ln.get("i1"))), ifmap.get((ln.get("n2"), ln.get("i2")))
        if a and b:
            f.links.append((a, b))
        else:
            f.warnings.append(f"Link {ln.get('id', '?')} con interfacce sconosciute ignorato")
    f.annotations = cml_annotations(doc.get("annotations"))
    return f


def parse_gns3(data: bytes) -> Foreign:
    configs: dict[str, str] = {}
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.endswith(".gns3")]
            if not names:
                raise ImportError_("Nessun file .gns3 nel progetto")
            doc = json.loads(z.read(names[0]))
            for n in z.namelist():  # startup configs shipped in the project (vpcs, iou, dynamips)
                if re.search(r"(startup-config\.cfg|startup\.vpc|_startup-config\.cfg)$", n):
                    uid = next((p for p in n.split("/") if re.fullmatch(r"[0-9a-f-]{36}", p)), None)
                    if uid:
                        configs[uid] = z.read(n).decode("utf-8", "replace")
    else:
        doc = json.loads(data)
    topo = doc.get("topology") or {}
    f = Foreign("gns3", name=str(doc.get("name") or "gns3-lab"))
    by_id: dict[str, tuple[str, dict]] = {}
    for n in topo.get("nodes") or []:
        props = n.get("properties") or {}
        nt = str(n.get("node_type") or "")
        image = props.get("image") if nt == "docker" else props.get("hda_disk_image") or props.get("image") or props.get("path")
        if nt == "dynamips":
            image = f"{props.get('platform', '')} {image or ''}"
        ports = {(p.get("adapter_number"), p.get("port_number")): p.get("name") for p in n.get("ports") or []}
        by_id[n.get("node_id")] = (str(n.get("name")), ports)
        f.nodes.append(FNode(name=str(n.get("name")), hint=f"{nt} {image or ''} {n.get('symbol') or ''}", container=nt == "docker",
                             image=image if nt == "docker" else None, x=_num(n.get("x")), y=_num(n.get("y")),
                             cpus=_int(props.get("cpus")), ram=_int(props.get("ram")), nics=_int(props.get("adapters")),
                             config=configs.get(n.get("node_id")), description=f"GNS3 {nt}"))
        if nt == "iou":
            f.warnings.append(f"{n.get('name')}: IOU/IOL gira come container vrnetlab: costruisci l'immagine con il tuo IOL")
    for ln in topo.get("links") or []:
        ends = []
        for e in ln.get("nodes") or []:
            node = by_id.get(e.get("node_id"))
            if not node:
                break
            name, ports = node
            pn = ports.get((e.get("adapter_number"), e.get("port_number"))) or ((e.get("label") or {}).get("text"))
            ends.append((name, FIface(str(pn or f"{e.get('adapter_number')}/{e.get('port_number')}"), _int(e.get("adapter_number")))))
        if len(ends) == 2:
            f.links.append((ends[0], ends[1]))
    f.annotations = gns3_drawings(topo.get("drawings"))
    return f


def parse_unl(data: bytes) -> Foreign:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ImportError_(f"File .unl non valido: {exc}") from exc
    f = Foreign("unl", name=root.get("name") or "eve-lab", description=root.get("description") or "", author=root.get("author") or "")
    configs = {}
    for c in root.iter("config"):
        try:
            configs[c.get("id")] = base64.b64decode(c.text or "").decode("utf-8", "replace")
        except ValueError:
            pass
    members: dict[str, list[tuple[str, FIface]]] = {}
    for n in root.iter("node"):
        name, typ, tpl = n.get("name") or f"n{n.get('id')}", n.get("type") or "", n.get("template") or ""
        f.nodes.append(FNode(name=name, hint=f"{typ} {tpl} {n.get('image') or ''}", container=typ == "docker",
                             image=n.get("image") or None, x=_num(n.get("left")), y=_num(n.get("top")),
                             cpus=_int(n.get("cpu")), ram=_int(n.get("ram")), nics=_int(n.get("ethernet")),
                             config=configs.get(n.get("id")),
                             description=f"EVE {tpl or typ}"))
        if typ == "iol":
            f.warnings.append(f"{name}: IOL gira come container vrnetlab: costruisci l'immagine con il tuo IOL")
        for i in n.iter("interface"):
            if i.get("network_id") and i.get("network_id") != "0":
                members.setdefault(i.get("network_id"), []).append((name, FIface(i.get("name") or "", _int(i.get("id")))))
    nets = {net.get("id"): net for net in root.iter("network")}
    for nid, eps in members.items():
        net = nets.get(nid)
        ntype = (net.get("type") if net is not None else "bridge") or "bridge"
        visible = net is not None and net.get("visibility") == "1"
        if len(eps) == 2 and ntype == "bridge" and not visible:
            f.links.append((eps[0], eps[1]))
            continue
        # a shared segment: a NetSO bridge (or external network for pnet/cloud)
        bname = (net.get("name") if net is not None else None) or f"net{nid}"
        ext = ntype.startswith(("pnet", "cloud"))
        f.nodes.append(FNode(name=bname, hint=f"{'external' if ext else 'bridge'} {ntype}", x=_num(net.get("left")) if net is not None else None,
                             y=_num(net.get("top")) if net is not None else None, description=f"EVE network {ntype}"))
        for k, ep in enumerate(eps):
            f.links.append((ep, (bname, FIface(f"port{k + 1}", k))))
        if ext:
            f.warnings.append(f"{bname}: rete esterna EVE '{ntype}' → controlla il bridge dell'host nel nodo")
    if root.find(".//textobjects") is not None and len(root.find(".//textobjects")):
        f.warnings.append("Testi e forme di EVE non sono ancora importati")
    return f


# ------------------------------------------------------------------ drawings

def _hex(c, default="#e3e7f4"):
    m = re.match(r"#([0-9a-fA-F]{6})", str(c or ""))
    return f"#{m.group(1).lower()}" if m else default


def _dash(style) -> str:
    st = str(style or "")
    return "dotted" if st in ("2,2", "1,1", "dotted") else "dashed" if st and st not in ("solid", "") else "solid"


def cml_annotations(items: list) -> list[dict]:
    out = []
    for k, a in enumerate(items or []):
        t = a.get("type")
        x1, y1, x2, y2 = (_num(a.get(f)) or 0 for f in ("x1", "y1", "x2", "y2"))
        common = {"id": f"a{k}", "z": -1 if int(a.get("z_index") or 0) <= 0 else 1}
        if t == "text":
            size = int(a.get("text_size") or 12)
            text = str(a.get("text_content") or "")
            out.append({**common, "type": "text", "x": x1, "y": y1, "text": text, "color": _hex(a.get("color")),
                        "font_size": size, "bold": bool(a.get("text_bold")), "italic": bool(a.get("text_italic")), "align": "left",
                        "w": max(60, min(900, len(max(text.splitlines() or [""], key=len)) * size * 0.62)), "h": size * 1.5 * max(1, len(text.splitlines()))})
        elif t in ("rectangle", "ellipse"):
            box = {"x": x1, "y": y1, "w": max(10, x2), "h": max(10, y2)} if t == "rectangle" else \
                  {"x": x1 - abs(x2), "y": y1 - abs(y2), "w": max(10, 2 * abs(x2)), "h": max(10, 2 * abs(y2))}
            out.append({**common, **box, "type": "rect" if t == "rectangle" else "ellipse", "fill": _hex(a.get("color"), "#8b7bff") if a.get("color") else None,
                        "color": _hex(a.get("border_color")), "border": _dash(a.get("border_style")), "border_width": int(a.get("thickness") or 1),
                        "radius": int(a.get("border_radius") or 0), "opacity": 0.35 if a.get("color") else 1.0})
        elif t == "line":
            arrow = {(True, True): "both", (False, True): "end", (True, False): "start"}.get((a.get("line_start") == "arrow", a.get("line_end") == "arrow"), "none")
            out.append({**common, "type": "line", "x": min(x1, x2), "y": min(y1, y2), "w": max(4, abs(x2 - x1)), "h": max(4, abs(y2 - y1)),
                        "flip": (x2 - x1) * (y2 - y1) < 0,
                        "color": _hex(a.get("border_color") or a.get("color")), "border_width": int(a.get("thickness") or 2), "arrow": arrow})
    return out


def gns3_drawings(items: list) -> list[dict]:
    out = []
    for k, d in enumerate(items or []):
        try:
            root = ET.fromstring(d.get("svg") or "")
        except ET.ParseError:
            continue
        el = next(iter(root), None)
        if el is None:
            continue
        tag = el.tag.split("}")[-1]
        x, y = _num(d.get("x")) or 0, _num(d.get("y")) or 0
        w, h = _num(root.get("width")) or 100, _num(root.get("height")) or 40
        common = {"id": f"a{k}", "x": x, "y": y, "w": w, "h": h, "z": -1 if int(d.get("z") or 0) <= 0 else 1}
        if tag == "text":
            out.append({**common, "type": "text", "text": "".join(el.itertext()), "color": _hex(el.get("fill")), "align": "left",
                        "font_size": int(float(el.get("font-size") or 12)), "bold": el.get("font-weight") == "bold",
                        "italic": el.get("font-style") == "italic"})
        elif tag in ("rect", "ellipse"):
            fill = el.get("fill")
            out.append({**common, "type": "rect" if tag == "rect" else "ellipse", "fill": _hex(fill) if fill and fill != "none" else None,
                        "opacity": float(el.get("fill-opacity") or 1) if fill and fill != "none" else 1.0,
                        "color": _hex(el.get("stroke")), "border_width": int(float(el.get("stroke-width") or 2)),
                        "border": "dashed" if el.get("stroke-dasharray") else "solid", "radius": int(float(el.get("rx") or 0)) if tag == "rect" else 0})
        elif tag == "line":
            x1, y1, x2, y2 = (_num(el.get(f)) or 0 for f in ("x1", "y1", "x2", "y2"))
            out.append({**common, "type": "line", "x": x + min(x1, x2), "y": y + min(y1, y2), "w": max(4, abs(x2 - x1)), "h": max(4, abs(y2 - y1)),
                        "flip": (x2 - x1) * (y2 - y1) < 0, "arrow": "none", "color": _hex(el.get("stroke")),
                        "border_width": int(float(el.get("stroke-width") or 2))})
    return out


def cml_export_annotations(anns: list[Annotation]) -> list[dict]:
    out = []
    for a in anns:
        z = 0 if a.z < 0 else 1
        style = {"solid": "", "dashed": "4,2", "dotted": "2,2", "none": ""}[a.border]
        if a.type == "text":
            out.append({"type": "text", "x1": a.x, "y1": a.y, "text_content": a.text, "text_size": a.font_size, "text_bold": a.bold,
                        "text_italic": a.italic, "text_font": "monospace", "text_unit": "pt", "color": a.color, "border_color": "#00000000",
                        "border_style": "", "thickness": 1, "rotation": 0, "z_index": z})
        elif a.type in ("rect", "ellipse"):
            geo = {"x1": a.x, "y1": a.y, "x2": a.w, "y2": a.h} if a.type == "rect" else \
                  {"x1": a.x + a.w / 2, "y1": a.y + a.h / 2, "x2": a.w / 2, "y2": a.h / 2}
            out.append({"type": "rectangle" if a.type == "rect" else "ellipse", **geo, "color": (a.fill or "#FFFFFF") + ("" if a.fill else "00"),
                        "border_color": a.color, "border_style": style, "thickness": a.border_width,
                        **({"border_radius": a.radius} if a.type == "rect" else {}), "rotation": 0, "z_index": z})
        elif a.type == "line":
            x1, x2 = (a.x + a.w, a.x) if a.flip else (a.x, a.x + a.w)
            out.append({"type": "line", "x1": x1, "y1": a.y, "x2": x2, "y2": a.y + a.h, "border_color": a.color, "border_style": style,
                        "thickness": a.border_width, "color": a.color, "line_start": "arrow" if a.arrow in ("start", "both") else None,
                        "line_end": "arrow" if a.arrow in ("end", "both") else None, "z_index": z})
    return out


# ------------------------------------------------------------------ mapping onto NetSO

RULES = [  # (regex on the source hint, NetSO template)
    (r"^nat\b|cloud nat|\bnat\b", "nat"),
    (r"external|cloud|pnet|^host\b|ext-container", "external"),
    (r"c7200", "cisco-c7200"),
    (r"c3725|c3745|c2691|c3640|c3660|c2600", "cisco-c3725"),
    (r"\biou\b|\biol\b|i86bi|cisco_iol", "cisco-iol"),
    (r"iosvl2|viosl2|vios_l2|l2-|cisco_viosl2", "cisco-iosvl2"),
    (r"\bvios|iosv|cisco_vios", "cisco-iosv"),
    (r"c8000v|cat8000v|cat8kv|csr|iosxe|cisco_c8000v", "cisco-c8000v"),
    (r"forti|fgt", "fortigate"),
    (r"vyos", "vyos"),
    (r"srlinux|nokia_srl|\bsrl\b", "nokia-srlinux"),
    (r"ceos", "arista-ceos"),
    (r"crpd", "juniper-crpd"),
    (r"sonic", "sonic-vs"),
    (r"frr", "frr"),
    (r"\bvpcs\b", "vpc"),
    (r"netshoot|linux_host", "linux-host"),
    (r"ethernet_switch|ethernet_hub|unmanaged_switch|\bbridge\b|ovs-bridge|\bhub\b", "bridge"),
    (r"alpine", "alpine"),
]


def guess_template(fn: FNode, templates: dict[str, Template]) -> str:
    hint = fn.hint.lower()
    for rx, tpl in RULES:
        if re.search(rx, hint) and tpl in templates:
            return tpl
    if fn.container:
        return "alpine" if "alpine" in templates else next(t for t in templates if templates[t].kind == "docker")
    return "linux-vm" if "linux-vm" in templates else next(t for t in templates if templates[t].kind == "qemu")


_PREFIX = [("tengigabitethernet", "te"), ("gigabitethernet", "gi"), ("fastethernet", "fa"), ("ethernet", "eth"), ("management", "mgmt")]


def norm_iface(name: str) -> str:
    n = re.sub(r"\s+", "", name.lower())
    for long, short in _PREFIX:
        if n.startswith(long):
            n = short + n[len(long):]
            break
    m = re.fullmatch(r"e(\d+)", n)  # EVE linux style e0
    return f"eth{m.group(1)}" if m else n


def _clean_name(raw: str, taken: set[str]) -> str:
    n = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_") or "n"
    if not n[0].isalpha():
        n = "n" + n
    n = n[:32]
    base, k = n, 1
    while n in taken:
        k += 1
        n = f"{base[:28]}-{k}"
    taken.add(n)
    return n


def build(f: Foreign, templates: dict[str, Template], overrides: dict[str, str] | None = None,
          lab_name: str | None = None) -> Imported:
    overrides = overrides or {}
    warnings = list(f.warnings)
    taken: set[str] = set()
    rename = {fn.name: _clean_name(fn.name, taken) for fn in f.nodes}
    nodes: dict[str, Node] = {}
    mapping, configs = [], {}
    auto = [id(fn) for fn in f.nodes if fn.x is None or fn.y is None]
    for k, fn in enumerate(f.nodes):
        name = rename[fn.name]
        tpl_name = overrides.get(name) or guess_template(fn, templates)
        if tpl_name not in templates:
            raise ImportError_(f"Template '{tpl_name}' inesistente")
        tpl = templates[tpl_name]
        node = Node(name=name, template=tpl_name, description=fn.description)
        if id(fn) in auto:  # no position in the source: a simple grid
            i = auto.index(id(fn))
            node.x, node.y = 120 + (i % 5) * 170, 100 + (i // 5) * 150
        else:
            node.x, node.y = fn.x, fn.y
        if tpl.kind == "docker" and fn.image and fn.image != tpl.image:
            node.image = fn.image
        if tpl.kind == "qemu" and fn.image and fn.image != tpl.image and not fn.container:
            node.image = fn.image  # EVE/PNET image folder name (same layout as NetSO's images dir)
        if tpl.kind in ("qemu", "docker"):
            if fn.cpus and fn.cpus != tpl.cpus:
                node.cpus = fn.cpus
            if fn.ram and fn.ram != tpl.ram:
                node.ram = fn.ram
        if fn.config and tpl.config_file:
            configs[name] = fn.config
            node.startup_config = True
        elif fn.config:
            warnings.append(f"{name}: la config importata non si può applicare al template {tpl_name}")
        nodes[name] = node
        mapping.append({"name": name, "source": fn.hint.strip(), "template": tpl_name, "image": node.image})
        if name != fn.name:
            warnings.append(f"'{fn.name}' rinominato in '{name}'")

    used: dict[str, set[str]] = {n: set() for n in nodes}

    def pick(node_name: str, fi: FIface) -> str | None:
        node, tpl = nodes[node_name], templates[nodes[node_name].template]
        want = norm_iface(fi.name)
        nics = node.nics or tpl.nics
        for _ in range(2):
            names = tpl.interface_names(nics)
            hit = next((x for x in names if norm_iface(x) == want and x not in used[node_name]), None)
            if hit is None and tpl.kind == "docker":  # containers: keep the number (e1-1, Ethernet1, eth1 -> eth1)
                m = re.search(r"(\d+)\D*$", fi.name)
                if m and f"eth{int(m.group(1))}" in names and f"eth{int(m.group(1))}" not in used[node_name]:
                    hit = f"eth{int(m.group(1))}"
            if hit is None and fi.index is not None and 0 <= fi.index < len(names) and names[fi.index] not in used[node_name]:
                hit = names[fi.index]
            if hit is None:
                hit = next((x for x in names if x not in used[node_name]), None)
            if hit:
                used[node_name].add(hit)
                return hit
            if nics >= 32:
                return None
            nics = min(32, max(nics + 1, (fi.index or 0) + 1))
            node.nics = nics
        return None

    links = []
    for (an, ai), (bn, bi) in f.links:
        if an not in rename or bn not in rename:
            warnings.append(f"Link {an}:{ai.name} – {bn}:{bi.name} verso un nodo sconosciuto ignorato")
            continue
        a, b = rename[an], rename[bn]
        ia, ib = pick(a, ai), pick(b, bi)
        if not ia or not ib:
            warnings.append(f"Link {a}:{ai.name} – {b}:{bi.name}: interfacce esaurite, ignorato")
            continue
        links.append(Link(endpoints=[f"{a}:{ia}", f"{b}:{ib}"]))

    name = lab_name or f.name
    try:
        anns = []
        for a in f.annotations:
            try:
                anns.append(Annotation(**a))
            except Exception:
                warnings.append(f"Disegno {a.get('id')} non importabile")
        lab = Lab(name=_clean_name(name, set()), description=f.description[:500], author=f.author, nodes=list(nodes.values()),
                  links=links, annotations=anns)
    except Exception as exc:
        raise ImportError_(str(exc)) from exc
    return Imported(f.fmt, lab, configs, mapping, warnings)


def import_file(filename: str, data: bytes, templates: dict[str, Template], overrides: dict[str, str] | None = None,
                lab_name: str | None = None) -> Imported:
    fmt = detect(filename, data)
    if fmt == "netso":
        from .topology import parse_lab_yaml
        lab = parse_lab_yaml(data.decode("utf-8", "replace"))
        if lab_name:
            lab.name = lab_name
        return Imported("netso", lab, {}, [{"name": n.name, "source": n.template, "template": n.template, "image": n.image} for n in lab.nodes], [])
    parser = {"clab": lambda: parse_clab(data.decode("utf-8", "replace")), "cml": lambda: parse_cml(data.decode("utf-8", "replace")),
              "gns3": lambda: parse_gns3(data), "unl": lambda: parse_unl(data)}[fmt]
    try:
        foreign = parser()
    except ImportError_:
        raise
    except Exception as exc:
        raise ImportError_(f"Errore leggendo il file {FORMATS[fmt]}: {exc}") from exc
    if not foreign.nodes:
        raise ImportError_("Il file non contiene nodi")
    return build(foreign, templates, overrides, lab_name)


# ------------------------------------------------------------------ export

class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, value):  # multi-line text (configs) as readable "|" blocks
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value.rstrip() + "\n", style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_Dumper.add_representer(str, _str)


def _dump(doc) -> str:
    return yaml.dump(doc, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=200)


CLAB_KIND = {"cisco-iosv": "cisco_vios", "cisco-iosvl2": "cisco_viosl2", "cisco-c8000v": "cisco_c8000v",
             "fortigate": "fortinet_fortigate", "linux-vm": "generic_vm", "vyos": "generic_vm"}
CLAB_CONTAINER = {"nokia-srlinux": "nokia_srlinux", "arista-ceos": "arista_ceos", "juniper-crpd": "juniper_crpd", "sonic-vs": "sonic-vs"}
CML_DEF = {"nat": "external_connector", "vpc": "alpine", "cisco-iosv": "iosv", "cisco-iosvl2": "iosvl2", "cisco-c8000v": "cat8000v", "external": "external_connector",
           "bridge": "unmanaged_switch", "alpine": "alpine", "linux-host": "alpine", "linux-vm": "ubuntu"}
_LONG = {"Gi": "GigabitEthernet", "Te": "TenGigabitEthernet", "Fa": "FastEthernet"}


def _long_iface(name: str) -> str:
    m = re.match(r"([A-Za-z]+)(.*)", name)
    return _LONG.get(m.group(1), m.group(1)) + m.group(2) if m else name


def export_clab(lab: Lab, templates: dict[str, Template], configs: dict[str, str]) -> tuple[str, list[str]]:
    warns, nodes, links = [], {}, []
    rename_if: dict[tuple[str, str], str] = {}
    for n in lab.nodes:
        tpl = templates.get(n.template)
        if tpl is None:
            warns.append(f"{n.name}: template sconosciuto, saltato")
            continue
        d: dict = {}
        if tpl.kind == "docker":
            d = {"kind": CLAB_CONTAINER.get(n.template, "linux"), "image": n.image or tpl.image}
        elif tpl.kind == "qemu":
            kind = CLAB_KIND.get(n.template, "generic_vm")
            d = {"kind": kind, "image": f"vrnetlab/{kind}:{n.image or tpl.image}"}
            warns.append(f"{n.name}: VM → containerlab kind '{kind}' (serve l'immagine vrnetlab corrispondente)")
            for i, ifc in enumerate(tpl.interface_names(n.nics)):
                rename_if[(n.name, ifc)] = f"eth{i + 1}"
        elif tpl.kind == "bridge":
            d = {"kind": "bridge"}
            warns.append(f"{n.name}: kind bridge di containerlab richiede un bridge '{n.name}' già creato sull'host")
        else:
            d = {"kind": "host"}
            warns.append(f"{n.name}: rete esterna → kind 'host' (verifica le interfacce)")
        if configs.get(n.name):
            d["startup-config"] = configs[n.name]
        d["labels"] = {"graph-posX": str(int(n.x)), "graph-posY": str(int(n.y))}
        nodes[n.name] = d
    for ln in lab.links:
        eps = []
        for e in ln.endpoints:
            node, _, ifc = e.partition(":")
            if node not in nodes:
                break
            eps.append(f"{node}:{rename_if.get((node, ifc), ifc)}")
        if len(eps) == 2:
            links.append({"endpoints": eps})
    doc = {"name": lab.name, "topology": {"nodes": nodes, "links": links}}
    head = f"# Esportato da NetSO — lab {lab.name}\n"
    return head + _dump(doc), warns


def export_cml(lab: Lab, templates: dict[str, Template], configs: dict[str, str]) -> tuple[str, list[str]]:
    warns, nodes, links, ids = [], [], [], {}
    for k, n in enumerate(lab.nodes):
        tpl = templates.get(n.template)
        if tpl is None:
            continue
        nd = CML_DEF.get(n.template, n.template)
        if n.template not in CML_DEF:
            warns.append(f"{n.name}: in CML serve una node definition '{nd}' (non è nella reference platform)")
        ifaces = []
        for i, ifc in enumerate(tpl.interface_names(n.nics)):
            label = _long_iface(ifc) if n.template.startswith("cisco") else (f"port{i}" if tpl.kind in ("bridge", "external") else ifc)
            ifaces.append({"id": f"i{i}", "slot": i, "label": label, "type": "physical"})
            ids[(n.name, ifc)] = (f"n{k}", f"i{i}")
        node = {"id": f"n{k}", "label": n.name, "node_definition": nd, "x": int(n.x), "y": int(n.y), "tags": [], "interfaces": ifaces}
        if n.template == "nat":
            node["configuration"] = "NAT"
        elif configs.get(n.name):
            node["configuration"] = configs[n.name]
        if tpl.kind in ("qemu", "docker"):
            node["cpus"] = n.cpus or tpl.cpus
            node["ram"] = n.ram or tpl.ram
        nodes.append(node)
    for j, ln in enumerate(lab.links):
        a, b = (tuple(e.split(":", 1)) for e in ln.endpoints)
        if a in ids and b in ids:
            links.append({"id": f"l{j}", "n1": ids[a][0], "i1": ids[a][1], "n2": ids[b][0], "i2": ids[b][1], "label": f"{a[0]}-{a[1]}<->{b[0]}-{b[1]}"})
    doc = {"lab": {"title": lab.name, "description": lab.description, "notes": "Esportato da NetSO", "version": "0.2.2"},
           "nodes": nodes, "links": links}
    if lab.annotations:
        doc["annotations"] = cml_export_annotations(lab.annotations)
    return _dump(doc), warns


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))
