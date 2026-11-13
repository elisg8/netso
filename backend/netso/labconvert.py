"""Turn a folder of plain-text IOS "initial configs" into NetSO labs.

Each source file holds the starting config of every device in one lab, split by a
`--------------- On R1` style header. From the interface IP addresses we rebuild the
topology: two interfaces on the same subnet are a point-to-point link, three or more
become a shared segment through a Linux bridge. The cleaned configs become the lab's
`default` set, so the lab boots already addressed and the student adds the protocol —
exactly like the generated scenarios.
"""
from __future__ import annotations

import ipaddress
import math
import re

from .models import Lab, Link, Node, Template

# configs are hand-typed with IOS abbreviations (int g0/2, ip addr, Host R1) and CRLF
_DEV = re.compile(r"-{3,}\s*On\s+(\S+)", re.I)
_IFACE = re.compile(r"^\s*int[a-z]*\s+(\S+)", re.I)          # int / inte / interface
_IP = re.compile(r"^\s*ip\s+add?(?:r[a-z]*)?\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", re.I)  # ip ad / add / addr / address
_IP6 = re.compile(r"^\s*ipv6\s+add?(?:r[a-z]*)?\s+([0-9A-Fa-f:]+)/(\d+)", re.I)  # ipv6 ad / add / addr 12::1/64
_DROP = re.compile(r"^\s*(en(able)?|conf(ig)?\s+t(erminal)?|end)\s*$", re.I)


def _brname(net) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", "-", str(net.network_address)).strip("-")
    return ("br-" + s)[:30]


def _is_loop(ifc: str) -> bool:
    return ifc.lower().startswith("lo")


# virtual/logical interfaces: not physical NICs, so they never become topology links
_VIRT = ("lo", "tun", "vlan", "vl", "po", "bvi", "null", "nve", "dialer", "loop", "virtual", "mgmt")


def _is_phys(ifc: str) -> bool:
    s = ifc.lower()
    return not any(s.startswith(p) for p in _VIRT)


def _port(ifc: str) -> int:
    m = re.search(r"(\d+)$", ifc)
    return int(m.group(1)) if m else 0


def parse(text: str) -> dict[str, dict]:
    """{device: {'ifaces': {iface:(ip,mask)}, 'ifaces6': {iface:(addr,prefix)}, 'lines': [...]}}."""
    devs: dict[str, dict] = {}
    cur = curif = None
    for ln in text.splitlines():
        m = _DEV.search(ln)
        if m:
            cur, curif = m.group(1).strip(":.-"), None   # «On R1:» → R1
            devs.setdefault(cur, {"ifaces": {}, "ifaces6": {}, "lines": []})
            continue
        if cur is None:
            continue
        devs[cur]["lines"].append(ln)
        mi = _IFACE.match(ln)
        if mi:
            curif = mi.group(1)
            continue
        ma = _IP.match(ln)
        if ma and curif and "secondary" not in ln.lower():
            devs[cur]["ifaces"].setdefault(curif, (ma.group(1), ma.group(2)))  # keep the primary
            continue
        m6 = _IP6.match(ln)
        if m6 and curif and not m6.group(1).lower().startswith("fe80"):   # skip link-local
            devs[cur]["ifaces6"].setdefault(curif, (m6.group(1), int(m6.group(2))))
    return devs


def _clean_config(lines: list[str]) -> str:
    """Drop the paste-into-terminal wrapper (en / config t / end) → a startup-config."""
    out = [ln for ln in lines if not _DROP.match(ln)]
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


# generic Cisco verification commands per topic (common CLI, not from any source material)
_VERIFY = {
    "eigrp": ["show ip eigrp neighbors", "show ip eigrp topology", "show ip route eigrp"],
    "ospf": ["show ip ospf neighbor", "show ip ospf database", "show ip route ospf"],
    "bgp": ["show ip bgp summary", "show ip bgp", "show ip route bgp"],
    "rip": ["show ip protocols", "show ip route rip"],
    "isis": ["show isis neighbors", "show ip route isis"],
    "mpls": ["show mpls ldp neighbor", "show mpls forwarding-table", "show ip route vrf *"],
    "l3vpn": ["show bgp vpnv4 unicast all summary", "show ip route vrf *"],
    "dmvpn": ["show dmvpn", "show ip nhrp", "show crypto isakmp sa"],
    "ipv6": ["show ipv6 interface brief", "show ipv6 route", "show ipv6 ospf neighbor"],
    "prefix": ["show ip prefix-list", "show route-map", "show ip route"],
}


def _verify_for(topic: str) -> list[str]:
    t = topic.lower()
    for key, cmds in _VERIFY.items():
        if key in t:
            return cmds
    return ["show ip interface brief", "show ip route", "ping <ip-remoto>"]


def _guide(name: str, topic: str, devs: dict, routers: list[str], bridges: list[str], nets: dict) -> str:
    """An original guide built only from the lab's own addressing + generic verification commands."""
    label = topic or name
    out = [f"# Lab: {label}", "", "## Objective",
           f"Configure **{label}** on this topology and verify end-to-end reachability "
           "(the interfaces start already addressed: the protocol is up to you).", ""]
    out.append(f"## Devices ({len(routers)} routers" + (f" + {len(bridges)} switches" if bridges else "") + ")")
    out += [f"- **{', '.join(routers)}**"] + ([f"- switches: {', '.join(bridges)}"] if bridges else [])
    out += ["", "## IP addresses (already set)", "",
            "| Device | Interface | Address |", "|---|---|---|"]
    for d in routers:
        addrs = {}  # iface -> ["ip/pfx", ...]
        for i, (ip, m) in devs[d]["ifaces"].items():
            addrs.setdefault(i, []).append(f"{ip}/{ipaddress.IPv4Network(f'0.0.0.0/{m}').prefixlen}")
        for i, (a6, p6) in devs[d]["ifaces6"].items():
            addrs.setdefault(i, []).append(f"{a6}/{p6}")
        order = [i for i in addrs if not _is_loop(i)] + [i for i in addrs if _is_loop(i)]
        if not order:
            out.append(f"| **{d}** | — | — |")
            continue
        for j, i in enumerate(order):
            out.append(f"| {f'**{d}**' if j == 0 else ''} | `{i}` | {' '.join(addrs[i])} |")
    out += ["", "## Links"]
    for net in sorted(nets, key=str):
        eps = nets[net]
        if len(eps) >= 2:
            out.append(f"- `{net.with_prefixlen}` — " + " ↔ ".join(f"{d}:{i}" for d, i in eps))
    out += ["", "## Verification"] + [f"- `{c}`" for c in _verify_for(topic)]
    out += ["", "_Guide generated automatically by NetSO from the lab's initial configs._"]
    return "\n".join(out)


def convert(name: str, text: str, templates: dict[str, Template], router: str = "cisco-iosv",
            topic: str = "") -> tuple[Lab, dict[str, str], str]:
    if router not in templates:
        raise ValueError(f"The router '{router}' is not installed")
    rtpl = templates[router]
    prefix, start = rtpl.iface_prefix, rtpl.iface_start

    def tpl_iface(raw: str) -> str:                       # g0/9 → Gi0/9 (the template's own naming)
        return f"{prefix}{start + _port(raw)}"

    devs = parse(text)
    # a device is a router if it carries any address (IPv4 or IPv6)
    routers = [d for d, v in devs.items() if v["ifaces"] or v["ifaces6"]]

    # group physical interfaces by subnet, IPv4 and IPv6 together (loopbacks never form links)
    nets: dict = {}
    for d in routers:
        for ifc, (ip, mask) in devs[d]["ifaces"].items():
            if _is_phys(ifc):
                nets.setdefault(ipaddress.ip_network(f"{ip}/{mask}", strict=False), []).append((d, ifc))
        for ifc, (addr, pfx) in devs[d]["ifaces6"].items():
            if _is_phys(ifc):
                nets.setdefault(ipaddress.IPv6Interface(f"{addr}/{pfx}").network, []).append((d, ifc))

    lab = Lab(name=name, generated=True)
    links: list[Link] = []
    bridges: list[str] = []
    for net in sorted(nets, key=str):
        eps = nets[net]
        if len(eps) == 2:
            (a, ia), (b, ib) = eps
            links.append(Link(endpoints=[f"{a}:{tpl_iface(ia)}", f"{b}:{tpl_iface(ib)}"],
                              label=net.with_prefixlen))
        elif len(eps) > 2:
            br = _brname(net)
            bridges.append(br)
            for k, (d, ifc) in enumerate(eps, 1):
                links.append(Link(endpoints=[f"{d}:{tpl_iface(ifc)}", f"{br}:port{k}"],
                                  label=net.with_prefixlen))

    # how many NICs each router needs (highest port used + 1)
    def _ports(d):
        return [_port(i) for i in {**devs[d]["ifaces"], **devs[d]["ifaces6"]} if _is_phys(i)]
    nic_need = {d: max(_ports(d), default=0) + 1 for d in routers}

    # Safety net: a lab must never ship with an unreachable node. Subnet inference can
    # strand a device when its real link runs through an L2 switch that carries no IP, or
    # when the peer address wasn't in the paste. Stitch every orphan piece to the main
    # topology's hub with an extra L1 link (the addresses won't match — the student
    # re-addresses it) so «Generate lab» always produces a connected lab.
    adj: dict[str, set] = {}
    deg: dict[str, int] = {}
    for lk in links:
        a, b = (e.split(":")[0] for e in lk.endpoints)
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    seen: set = set()
    comps: list[set] = []
    for s in set(routers) | set(bridges):
        if s in seen:
            continue
        stack, comp = [s], set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.add(x)
            stack += [y for y in adj.get(x, ()) if y not in seen]
        comps.append(comp)
    if len(comps) > 1:
        def _anchor(comp: set) -> str:            # best-connected router, else any node
            pool = [d for d in comp if d in routers] or list(comp)
            return max(pool, key=lambda d: deg.get(d, 0))

        def _free(d: str) -> str:                 # next spare NIC on a router, bumping its count
            p = nic_need[d]
            nic_need[d] = p + 1
            return f"{prefix}{start + p}"

        main = max(comps, key=len)
        hub = _anchor(main)
        for comp in comps:
            if comp is main:
                continue
            u = _anchor(comp)
            if u in routers and hub in routers:   # only stitch real routers (lone switches stay as-is)
                links.append(Link(endpoints=[f"{u}:{_free(u)}", f"{hub}:{_free(hub)}"],
                                  style="dashed", label="added by NetSO · re-address"))

    # layout: routers on a circle, bridges in the middle
    n = len(routers)
    for i, d in enumerate(routers):
        ang = 2 * math.pi * i / max(1, n)
        x, y = 430 + 300 * math.cos(ang), 340 + 260 * math.sin(ang)
        nd = Node(name=d, template=router, x=round(x), y=round(y), startup_config=True)
        if nic_need[d] > rtpl.nics:
            nd.nics = min(nic_need[d], 16)
        lab.nodes.append(nd)
    for j, br in enumerate(bridges):
        lab.nodes.append(Node(name=br, template="bridge", x=380 + j * 180, y=340))
    lab.links = links

    configs = {d: _clean_config(devs[d]["lines"]) for d in routers}
    instructions = _guide(name, topic, devs, routers, bridges, nets)
    lab.instructions = instructions
    return lab, configs, instructions
