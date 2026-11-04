"""Linux networking primitives: bridges, tap and veth interfaces.

Every lab link becomes a small Linux bridge; QEMU NICs attach through tap
interfaces, containers through veth pairs moved into their network namespace.
Interface names are hashed so they always fit the kernel's 15-char limit.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .hostcmd import Runner


def short_id(*parts: str, length: int = 10) -> str:
    return hashlib.sha1("/".join(parts).encode()).hexdigest()[:length]


def link_bridge_name(lab: str, endpoints: list[str]) -> str:
    # named after the (sorted) endpoints, so the bridge keeps its identity when other
    # links are added/removed around it — required for hot-plug (connect/disconnect while running).
    return f"nsb{short_id(lab, 'link', '/'.join(sorted(endpoints)))}"


def node_bridge_name(lab: str, node: str) -> str:
    return f"nss{short_id(lab, 'node', node)}"


def tap_name(lab: str, node: str, nic: int) -> str:
    return f"nst{short_id(lab, node, str(nic))}"


def veth_name(lab: str, node: str, iface: str) -> str:
    return f"nsv{short_id(lab, node, iface)}"


def mac_address(lab: str, node: str, nic: int) -> str:
    h = short_id(lab, node, f"mac{nic}", length=6)
    return f"52:54:00:{h[0:2]}:{h[2:4]}:{h[4:6]}"


def nth_mac(base: str, i: int) -> str:
    """The i-th MAC after `base` (aa:bb:cc:dd:ee:ff), wrapping inside the last 3 bytes."""
    head, tail = base[:9], int(base[9:].replace(":", ""), 16)
    t = f"{(tail + i) % 0x1000000:06x}"
    return f"{head}{t[0:2]}:{t[2:4]}:{t[4:6]}"


def iface_exists(r: Runner, name: str) -> bool:
    if r.dry_run:
        return False
    out = r.run(["ip", "-o", "link", "show", "dev", name], check=False)
    return bool(out)


def create_bridge(r: Runner, name: str) -> None:
    if not iface_exists(r, name):
        r.run(["ip", "link", "add", name, "type", "bridge", "stp_state", "0"])
    # Forward link-local frames (LLDP, LACP, STP BPDUs) like a real wire
    r.write(f"/sys/class/net/{name}/bridge/group_fwd_mask", "65528")
    # keep host firewalls (Docker sets FORWARD to DROP with br_netfilter) out of the lab wires
    for knob in ("nf_call_iptables", "nf_call_ip6tables", "nf_call_arptables"):
        r.write(f"/sys/class/net/{name}/bridge/{knob}", "0")
    r.run(["ip", "link", "set", name, "mtu", "9000"], check=False)
    r.run(["ip", "link", "set", name, "up"])


def delete_iface(r: Runner, name: str) -> None:
    r.run(["ip", "link", "del", name], check=False)


def create_tap(r: Runner, name: str, bridge: str | None) -> None:
    if not iface_exists(r, name):
        r.run(["ip", "tuntap", "add", "dev", name, "mode", "tap"])
    r.run(["ip", "link", "set", name, "mtu", "9000"], check=False)
    if bridge:
        r.run(["ip", "link", "set", name, "master", bridge])
    r.run(["ip", "link", "set", name, "up"])


def enslave(r: Runner, iface: str, bridge: str) -> None:
    """Plug an existing interface (a VM tap, say) into a bridge — hot-connect."""
    r.run(["ip", "link", "set", iface, "master", bridge])
    r.run(["ip", "link", "set", iface, "up"])


def release(r: Runner, iface: str) -> None:
    """Detach an interface from its bridge without deleting it — hot-disconnect a VM tap."""
    r.run(["ip", "link", "set", iface, "nomaster"], check=False)


def apply_netem(r: Runner, iface: str, *, delay_ms: float | None = None,
                jitter_ms: float | None = None, loss_pct: float | None = None,
                rate_kbit: int | None = None) -> None:
    """Shape egress traffic of `iface` with a single netem qdisc.

    netem on an interface affects frames leaving it toward the bridge, i.e. one
    direction of the wire. The engine applies the same profile on both endpoints
    so the condition is symmetric (RTT ends up ~2x the configured delay).
    """
    clear_netem(r, iface)
    args = ["tc", "qdisc", "add", "dev", iface, "root", "netem"]
    if delay_ms:
        args += ["delay", f"{delay_ms}ms"]
        if jitter_ms:
            args += [f"{jitter_ms}ms"]
    if loss_pct:
        args += ["loss", f"{loss_pct}%"]
    if rate_kbit:
        args += ["rate", f"{rate_kbit}kbit"]
    if args[-1] == "netem":  # nothing to shape
        return
    r.run(args, check=False)


def clear_netem(r: Runner, iface: str) -> None:
    r.run(["tc", "qdisc", "del", "dev", iface, "root"], check=False)


def plug_veth_into_container(r: Runner, host_end: str, pid: int, iface: str, bridge: str) -> None:
    """Create a veth pair, move one end into the container as `iface`, plug the other in `bridge`."""
    peer = host_end + "p"
    delete_iface(r, host_end)
    r.run(["ip", "link", "add", host_end, "mtu", "9000", "type", "veth", "peer", "name", peer, "mtu", "9000"])
    r.run(["ip", "link", "set", peer, "netns", str(pid)])
    ns = ["nsenter", "-t", str(pid), "-n"]
    r.run([*ns, "ip", "link", "set", peer, "name", iface])
    r.run([*ns, "ip", "link", "set", iface, "up"])
    r.run(["ip", "link", "set", host_end, "master", bridge])
    r.run(["ip", "link", "set", host_end, "up"])


# ------------------------------------------------------------------ switch VLANs (802.1Q on a Linux bridge)

def set_vlan_filtering(r: Runner, bridge: str, on: bool) -> None:
    r.run(["ip", "link", "set", bridge, "type", "bridge", "vlan_filtering", "1" if on else "0"], check=False)


def set_port_vlans(r: Runner, port: str, cfg: dict) -> None:
    """Access (one untagged VLAN) or trunk (tagged VLANs + untagged native) on a bridge port."""
    from .models import parse_vlans
    r.run(["bridge", "vlan", "del", "dev", port, "vid", "1-4094"], check=False)
    if cfg.get("mode", "access") == "trunk":
        native = int(cfg.get("native", 1))
        for lo, hi in parse_vlans(str(cfg.get("vlans", "1-4094"))):
            r.run(["bridge", "vlan", "add", "dev", port, "vid", f"{lo}-{hi}" if hi > lo else str(lo)])
        r.run(["bridge", "vlan", "add", "dev", port, "vid", str(native), "pvid", "untagged"])
    else:
        r.run(["bridge", "vlan", "add", "dev", port, "vid", str(int(cfg.get("vlan", 1))), "pvid", "untagged"])


# ------------------------------------------------------------------ NAT cloud: routed + masqueraded + DHCP

def default_nat_subnet(lab: str, node: str) -> str:
    return f"172.31.{int(short_id(lab, node, 'nat', length=6), 16) % 250 + 1}.0/24"


def _nat_rules(bridge: str, subnet: str) -> list[list[str]]:
    tag = ["-m", "comment", "--comment", f"netso-{bridge}"]
    return [
        ["-t", "nat", "POSTROUTING", "-s", subnet, "!", "-d", subnet, "-j", "MASQUERADE", *tag],
        ["FORWARD", "-i", bridge, "-j", "ACCEPT", *tag],
        ["FORWARD", "-o", bridge, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT", *tag],
    ]


def _ipt(r: Runner, op: str, rule: list[str]) -> None:
    table = rule[:2] if rule[0] == "-t" else []
    rest = rule[2:] if table else rule
    r.run(["iptables", *table, op, rest[0], *rest[1:]], check=False if op == "-D" else True)


def nat_up(r: Runner, bridge: str, subnet: str, workdir: Path) -> int | None:
    """Gateway address on the bridge, masquerade towards the host's uplink, DHCP + DNS with dnsmasq."""
    import ipaddress
    net = ipaddress.ip_network(subnet)
    hosts = list(net.hosts())
    gw, lo, hi = hosts[0], hosts[min(9, len(hosts) - 2)], hosts[-1]
    r.run(["ip", "addr", "replace", f"{gw}/{net.prefixlen}", "dev", bridge])
    r.write("/proc/sys/net/ipv4/ip_forward", "1")
    for rule in _nat_rules(bridge, subnet):
        _ipt(r, "-D", rule)  # idempotent
        _ipt(r, "-I" if rule[0] == "FORWARD" else "-A", rule)
    pidfile = workdir / "dnsmasq.pid"
    if not r.dry_run:
        workdir.mkdir(parents=True, exist_ok=True)
    r.run(["dnsmasq", "--conf-file=/dev/null", "--strict-order", "--bind-interfaces", f"--interface={bridge}",
           "--except-interface=lo", f"--listen-address={gw}", f"--dhcp-range={lo},{hi},12h",
           f"--dhcp-option=option:router,{gw}", f"--dhcp-leasefile={workdir / 'leases'}", f"--pid-file={pidfile}"])
    try:
        return int(pidfile.read_text().strip()) if not r.dry_run else 4343
    except (OSError, ValueError):
        return None


def nat_down(r: Runner, bridge: str, subnet: str | None, pid: int | None) -> None:
    if pid:
        r.run(["kill", str(pid)], check=False)
    if subnet:
        for rule in _nat_rules(bridge, subnet):
            _ipt(r, "-D", rule)


# ------------------------------------------------------------------ VXLAN to another server

def vxlan_name(lab: str, node: str) -> str:
    return f"nsx{short_id(lab, node, 'vxlan')}"


def vxlan_up(r: Runner, dev: str, bridge: str, vni: int, remote: str, dstport: int = 4789) -> None:
    """Tunnel the bridge to the same VNI on a remote server (the other side does the same towards us)."""
    if not iface_exists(r, dev):
        r.run(["ip", "link", "add", dev, "type", "vxlan", "id", str(vni), "remote", remote, "dstport", str(dstport), "nolearning"])
    r.run(["ip", "link", "set", dev, "mtu", "1450"], check=False)
    r.run(["ip", "link", "set", dev, "master", bridge])
    r.run(["ip", "link", "set", dev, "up"])
