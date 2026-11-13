"""Templates and lab definitions: loading, saving, validation, resolution."""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from .config import settings
from .models import NAME_RE, NODE_NAME_RE, SHARED_KINDS, Lab, Node, ResolvedNode, Template


class TopologyError(ValueError):
    pass


# ---------------------------------------------------------------- templates

def load_templates() -> dict[str, Template]:
    """Load templates; earlier directories override later ones (user > built-in)."""
    found: dict[str, Template] = {}
    for d in reversed(settings.all_template_dirs()):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.y*ml")):
            try:
                data = yaml.safe_load(f.read_text()) or {}
                data.setdefault("name", f.stem)
                tpl = Template(**data)
            except Exception as exc:  # pragma: no cover - surfaced in logs
                print(f"[netso] template ignorato {f}: {exc}")
                continue
            found[tpl.name] = tpl
    return dict(sorted(found.items()))


def resolve_node(node: Node, templates: dict[str, Template]) -> ResolvedNode:
    tpl = templates.get(node.template)
    if tpl is None:
        raise TopologyError(f"Node {node.name}: template '{node.template}' does not exist")
    nics = node.nics if node.nics is not None else tpl.nics
    return ResolvedNode(
        name=node.name,
        template=tpl.name,
        kind=tpl.kind,
        icon=node.icon or tpl.icon,
        image=node.image or tpl.image,
        cpus=node.cpus or tpl.cpus,
        ram=node.ram or tpl.ram,
        nics=nics,
        nic_model=node.nic_model or tpl.nic_model,
        disk_bus=tpl.disk_bus,
        console=node.console if node.console and tpl.kind == "qemu" else tpl.console,
        interfaces=tpl.interface_names(nics),
        machine=tpl.machine,
        cpu_model=tpl.cpu_model,
        extra_args=node.extra_args if node.extra_args is not None else tpl.extra_args,
        env={**tpl.env, **(node.env or {})},
        cmd=node.cmd if node.cmd is not None else tpl.cmd,
        privileged=tpl.privileged,
        clipboard=tpl.clipboard,
        host_bridge=node.host_bridge or tpl.host_bridge,
        mac=node.mac,
        shell=tpl.shell,
        assets=tpl.assets,
        ports=node.ports,
        subnet=node.subnet,
        mgmt_ip=node.mgmt_ip,
        mgmt_iface=node.mgmt_iface if tpl.kind == "qemu" else None,
        remote=node.remote,
        vni=node.vni,
        dstport=node.dstport,
        slots=tpl.slots,
        idle_pc=tpl.idle_pc,
        config_method=tpl.config_method if tpl.config_file else "none",
        config_file=tpl.config_file,
        config_label=tpl.config_label,
        startup_config=node.startup_config,
    )


def validate_lab(lab: Lab, templates: dict[str, Template] | None = None) -> dict[str, ResolvedNode]:
    """Check the whole topology; return the resolved nodes."""
    templates = templates if templates is not None else load_templates()
    resolved = {n.name: resolve_node(n, templates) for n in lab.nodes}
    used: dict[str, str] = {}
    for i, link in enumerate(lab.links, 1):
        a, b = link.parsed()
        if a.node == b.node:
            raise TopologyError(f"Link {i}: a node cannot be connected to itself ({a.node})")
        for ep in (a, b):
            rn = resolved.get(ep.node)
            if rn is None:
                raise TopologyError(f"Link {i}: node '{ep.node}' does not exist")
            if rn.kind in SHARED_KINDS:
                continue  # any number of links, port name is cosmetic
            if ep.iface not in rn.interfaces:
                raise TopologyError(
                    f"Link {i}: interface '{ep.iface}' not available on {ep.node} "
                    f"(available: {', '.join(rn.interfaces)})"
                )
            key = str(ep)
            if key in used:
                raise TopologyError(f"Interface {key} used in two links")
            used[key] = key
        kinds = {resolved[a.node].kind, resolved[b.node].kind}
        if kinds <= set(SHARED_KINDS):
            raise TopologyError(f"Link {i}: cannot connect two switches/networks together (bridge, external, NAT)")
    linked = {(e.node, e.iface) for link in lab.links for e in link.parsed()}
    for rn in resolved.values():
        if rn.mgmt_iface and rn.mgmt_iface not in rn.interfaces:
            raise TopologyError(f"Node {rn.name}: management interface '{rn.mgmt_iface}' does not exist")
        if rn.mgmt_iface and (rn.name, rn.mgmt_iface) in linked:
            raise TopologyError(f"Node {rn.name}: management interface {rn.mgmt_iface} is already used by a link")
    for rn in resolved.values():
        if rn.kind == "vxlan" and not (rn.remote and rn.vni):
            raise TopologyError(f"Node {rn.name}: the remote link (VXLAN) requires the remote server IP and VNI")
        if rn.kind == "external" and not rn.host_bridge:
            raise TopologyError(f"Node {rn.name}: external nodes require 'host_bridge'")
        if rn.kind in ("qemu", "docker", "dynamips", "iol") and not rn.image:
            raise TopologyError(f"Node {rn.name}: no image defined")
    return resolved


# --------------------------------------------------------------- lab store

def lab_dir(name: str) -> Path:
    return settings.labs_dir / name


def lab_file(name: str) -> Path:
    return lab_dir(name) / "lab.yml"


def parse_lab_yaml(text: str) -> Lab:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise TopologyError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TopologyError("The lab file must be a YAML object")
    try:
        return Lab(**data)
    except Exception as exc:
        raise TopologyError(str(exc)) from exc


def dump_lab_yaml(lab: Lab) -> str:
    data = lab.model_dump(exclude_none=True)
    if data.get("config_set") == "default":
        data.pop("config_set")
    if not data.get("author"):
        data.pop("author", None)
    for n in data["nodes"]:
        # keep the file tidy: drop empty overrides
        for k in [k for k, v in n.items() if v in ({}, [])]:
            n.pop(k)
        if not n.get("startup_config"):
            n.pop("startup_config", None)
        if not n.get("pinned"):
            n.pop("pinned", None)
    if lab.annotations:  # keep drawings short: only what differs from the defaults
        data["annotations"] = [{"id": a.id, "type": a.type, **a.model_dump(exclude_defaults=True, exclude={"id", "type"})} for a in lab.annotations]
    else:
        data.pop("annotations", None)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def list_labs() -> list[str]:
    if not settings.labs_dir.is_dir():
        return []
    return sorted(p.parent.name for p in settings.labs_dir.glob("*/lab.yml"))


def load_lab(name: str) -> Lab:
    f = lab_file(name)
    if not f.exists():
        raise FileNotFoundError(f"Lab '{name}' does not exist")
    return parse_lab_yaml(f.read_text())


def save_lab(lab: Lab) -> None:
    f = lab_file(lab.name)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(dump_lab_yaml(lab))
    tmp.replace(f)


def delete_lab(name: str) -> None:
    shutil.rmtree(lab_dir(name), ignore_errors=True)


# ------------------------------------------------------- startup configs
# Config sets (EVE-NG style): labs/<lab>/configs/<set>/<node>.cfg, "default" always exists.

DEFAULT_SET = "default"


def _check_set(name: str) -> str:
    if not NAME_RE.match(name):
        raise TopologyError(f"Invalid config set name '{name}'")
    return name


def configs_dir(lab: str, cfg_set: str | None = None) -> Path:
    d = lab_dir(lab) / "configs"
    return d / _check_set(cfg_set) if cfg_set else d


def config_sets(lab: str) -> list[str]:
    d = configs_dir(lab)
    found = {p.name for p in d.iterdir() if p.is_dir()} if d.is_dir() else set()
    return [DEFAULT_SET, *sorted(found - {DEFAULT_SET})]


def read_configs(lab: str, cfg_set: str) -> dict[str, str]:
    d = configs_dir(lab, cfg_set)
    if not d.is_dir():
        return {}
    return {f.stem: f.read_text() for f in sorted(d.glob("*.cfg"))}


def read_config(lab: str, cfg_set: str, node: str) -> str | None:
    f = configs_dir(lab, cfg_set) / f"{node}.cfg"
    return f.read_text() if f.is_file() else None


def write_config(lab: str, cfg_set: str, node: str, text: str) -> None:
    if not NODE_NAME_RE.match(node):
        raise TopologyError(f"Invalid node name '{node}'")
    f = configs_dir(lab, cfg_set) / f"{node}.cfg"
    if not text.strip():
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text if text.endswith("\n") else text + "\n")


def create_config_set(lab: str, name: str, copy_from: str | None = None) -> None:
    if name in config_sets(lab):
        raise TopologyError(f"Config set '{name}' already exists")
    d = configs_dir(lab, name)
    if copy_from and configs_dir(lab, copy_from).is_dir():
        shutil.copytree(configs_dir(lab, copy_from), d)
    else:
        d.mkdir(parents=True)


def rename_config_set(lab: str, old: str, new: str) -> None:
    if old == DEFAULT_SET:
        raise TopologyError("The 'default' config set cannot be renamed")
    if new in config_sets(lab):
        raise TopologyError(f"Config set '{new}' already exists")
    if not configs_dir(lab, old).is_dir():
        raise FileNotFoundError(f"Config set '{old}' does not exist")
    configs_dir(lab, old).rename(configs_dir(lab, new))


def delete_config_set(lab: str, name: str) -> None:
    if name == DEFAULT_SET:
        raise TopologyError("The 'default' config set cannot be deleted")
    shutil.rmtree(configs_dir(lab, name), ignore_errors=True)


def rename_node_configs(lab: str, old: str, new: str) -> None:
    for s in config_sets(lab):
        f = configs_dir(lab, s) / f"{old}.cfg"
        if f.is_file():
            f.rename(f.with_name(f"{new}.cfg"))
