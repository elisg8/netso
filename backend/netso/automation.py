"""Automation helpers: inventories (Ansible, /etc/hosts, SSH config) and topology generators
(containerlab `generate` style CLOS, plus ring, star, full mesh and chain)."""
from __future__ import annotations

import math

import yaml

from .models import NAME_RE, Lab, Template


# ------------------------------------------------------------------ inventory

def node_rows(lab: Lab, state, templates: dict[str, Template], host: str) -> list[dict]:
    rows = []
    for n in lab.nodes:
        tpl = templates.get(n.template)
        if tpl is None or tpl.kind not in ("qemu", "docker", "dynamips", "iol"):
            continue
        ns = state.nodes.get(n.name)
        up = bool(ns and ns.status == "running")
        ip = (ns.mgmt_ip if ns and ns.mgmt_ip else None) or n.mgmt_ip
        console = None
        if up and ns.console_port:
            console = f"telnet {host} {ns.console_port}"
        elif up and ns.container:
            console = f"docker exec -it {ns.container} {' '.join(tpl.shell or ['sh'])}"
        rows.append({"name": n.name, "template": n.template, "kind": tpl.kind, "running": up, "mgmt_ip": ip,
                     "mgmt_iface": n.mgmt_iface, "console": console, "ansible_os": tpl.ansible_os})
    return rows


def ansible_inventory(lab: Lab, rows: list[dict]) -> str:
    groups: dict[str, dict] = {}
    missing = []
    for r in rows:
        if not r["mgmt_ip"]:
            missing.append(r["name"])
            continue
        g = groups.setdefault(r["template"].replace("-", "_"), {"hosts": {}, "vars": {}})
        g["hosts"][r["name"]] = {"ansible_host": r["mgmt_ip"]}
        if r["ansible_os"]:
            g["vars"] = {"ansible_network_os": r["ansible_os"], "ansible_connection": "ansible.netcommon.network_cli"}
        elif r["kind"] == "docker":
            g["vars"] = {"ansible_connection": "community.docker.docker", "ansible_docker_extra_args": ""}
    for g in groups.values():
        if not g["vars"]:
            del g["vars"]
    doc = {"all": {"vars": {"netso_lab": lab.name}, "children": groups}}
    head = f"# Inventario Ansible del lab {lab.name} (generato da NetSO)\n"
    if missing:
        head += f"# Senza IP di gestione (aggiungilo nel nodo): {', '.join(missing)}\n"
    return head + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def hosts_file(lab: Lab, rows: list[dict]) -> str:
    out = [f"# lab {lab.name} — da aggiungere a /etc/hosts"]
    out += [f"{r['mgmt_ip']}\t{r['name']} {r['name']}.{lab.name}" for r in rows if r["mgmt_ip"]]
    return "\n".join(out) + "\n"


def ssh_config(lab: Lab, rows: list[dict]) -> str:
    out = [f"# lab {lab.name} — da aggiungere a ~/.ssh/config"]
    for r in rows:
        if r["mgmt_ip"]:
            out += [f"Host {r['name']}.{lab.name}", f"    HostName {r['mgmt_ip']}",
                    "    StrictHostKeyChecking no", "    UserKnownHostsFile /dev/null", ""]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ generators

class GenerateError(ValueError):
    pass


def _names(prefix: str, count: int, taken: set[str]) -> list[str]:
    out, k = [], 1
    while len(out) < count:
        n = f"{prefix}{k}"
        if n not in taken:
            out.append(n)
            taken.add(n)
        k += 1
    if not all(NAME_RE.match(n) for n in out):
        raise GenerateError(f"Invalid prefix '{prefix}'")
    return out


class _Builder:
    def __init__(self, lab: Lab, templates: dict[str, Template]):
        self.templates = templates
        self.taken = {n.name for n in lab.nodes}
        self.used = {n.name: {e.split(":", 1)[1] for ln in lab.links for e in ln.endpoints if e.split(":", 1)[0] == n.name} for n in lab.nodes}
        self.nodes: list[dict] = []
        self.links: list[dict] = []
        self.nics: dict[str, int] = {}

    def add(self, prefix: str, tpl: str, count: int, positions) -> list[str]:
        if tpl not in self.templates:
            raise GenerateError(f"Device '{tpl}' does not exist")
        names = _names(prefix, count, self.taken)
        for n, (x, y) in zip(names, positions):
            self.nodes.append({"name": n, "template": tpl, "x": round(x), "y": round(y)})
            self.used[n] = set()
        return names

    def _tpl(self, node: str) -> Template:
        return self.templates[next(x["template"] for x in self.nodes if x["name"] == node)]

    def _free(self, node: str) -> str:
        tpl = self._tpl(node)
        nics = self.nics.get(node, tpl.nics)
        while True:
            free = next((i for i in tpl.interface_names(nics) if i not in self.used[node]), None)
            if free:
                self.used[node].add(free)
                if nics != tpl.nics:
                    self.nics[node] = nics
                return free
            if nics >= 32:
                raise GenerateError(f"{node}: interfacce esaurite (max 32)")
            nics += 1

    def link(self, a: str, b: str) -> None:
        self.links.append({"endpoints": [f"{a}:{self._free(a)}", f"{b}:{self._free(b)}"]})

    def result(self) -> dict:
        for n in self.nodes:
            if n["name"] in self.nics:
                n["nics"] = self.nics[n["name"]]
        return {"nodes": self.nodes, "links": self.links}


def _row(n: int, y: float, cx: float, gap: float):
    return [(cx + (i - (n - 1) / 2) * gap, y) for i in range(n)]


def _circle(n: int, cx: float, cy: float, r: float):
    return [(cx + r * math.cos(2 * math.pi * i / n - math.pi / 2), cy + r * math.sin(2 * math.pi * i / n - math.pi / 2)) for i in range(n)]


def generate(lab: Lab, templates: dict[str, Template], pattern: str, p: dict) -> dict:
    """New nodes + links to add to `lab`. p: counts, templates and the origin (x, y) of the drawing."""
    b = _Builder(lab, templates)
    ox, oy = float(p.get("x", 0)), float(p.get("y", 0))
    cnt = lambda k, d, lo=1, hi=64: max(lo, min(hi, int(p.get(k, d))))  # noqa: E731
    if pattern == "clos":
        sup, spn, lf, hpl = cnt("superspines", 0, 0, 16), cnt("spines", 2), cnt("leaves", 4), cnt("hosts", 1, 0, 16)
        gap = 170
        width = max(spn, lf, lf * max(1, hpl)) * gap
        y = oy
        ss = b.add(p.get("superspine_prefix", "super"), p.get("superspine_tpl", p.get("spine_tpl", "frr")), sup, _row(sup, y, ox + width / 2, gap * 2)) if sup else []
        y += 170 if sup else 0
        spines = b.add(p.get("spine_prefix", "spine"), p.get("spine_tpl", "frr"), spn, _row(spn, y, ox + width / 2, gap * 1.6))
        leaves = b.add(p.get("leaf_prefix", "leaf"), p.get("leaf_tpl", "frr"), lf, _row(lf, y + 190, ox + width / 2, gap * max(1, hpl)))
        for s in ss:
            for sp in spines:
                b.link(s, sp)
        for sp in spines:
            for le in leaves:
                b.link(sp, le)
        if hpl:
            for i, le in enumerate(leaves):
                lx = ox + width / 2 + (i - (lf - 1) / 2) * gap * max(1, hpl)
                hosts = b.add(p.get("host_prefix", f"h{i + 1}-"), p.get("host_tpl", "vpc"), hpl, _row(hpl, y + 380, lx, gap * 0.95))
                for h in hosts:
                    b.link(le, h)
    elif pattern in ("ring", "mesh"):
        n = cnt("count", 4, 2)
        r = max(160, n * 45)
        nodes = b.add(p.get("prefix", "r"), p.get("tpl", "frr"), n, _circle(n, ox + r, oy + r, r))
        pairs = [(nodes[i], nodes[(i + 1) % n]) for i in range(n if n > 2 else 1)] if pattern == "ring" else \
            [(nodes[i], nodes[j]) for i in range(n) for j in range(i + 1, n)]
        for a, c in pairs:
            b.link(a, c)
    elif pattern == "star":
        n = cnt("count", 4)
        r = max(180, n * 40)
        center = b.add(p.get("center_prefix", "sw"), p.get("center_tpl", "bridge"), 1, [(ox + r, oy + r)])[0]
        for leaf in b.add(p.get("prefix", "pc"), p.get("tpl", "vpc"), n, _circle(n, ox + r, oy + r, r)):
            b.link(center, leaf)
    elif pattern == "chain":
        n = cnt("count", 4, 2)
        nodes = b.add(p.get("prefix", "r"), p.get("tpl", "frr"), n, [(ox + i * 190, oy) for i in range(n)])
        for a, c in zip(nodes, nodes[1:]):
            b.link(a, c)
    else:
        raise GenerateError(f"Unknown pattern '{pattern}' (clos, ring, mesh, star, chain)")
    shared = {"bridge", "external", "nat"}
    for ln in b.links:
        a, c = (e.split(":", 1)[0] for e in ln["endpoints"])
        if b._tpl(a).kind in shared and b._tpl(c).kind in shared:
            raise GenerateError("Two switches/networks cannot be connected directly: choose a router or host")
    return b.result()
