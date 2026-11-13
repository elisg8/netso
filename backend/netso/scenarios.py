"""Ready-made lab scenarios by topic (OSPF, BGP, IS-IS, EIGRP, static, RIP, MPLS, EVPN/VXLAN, VLAN).

Each build picks a topology and size at random, so you get a different lab every time, and for the
routing topics it writes a working FRRouting startup-config per node (IP addressing + the protocol),
applied on boot (the node's `startup_config`). Routers are FRR (open, no license), hosts are VPC,
switches are the Linux bridge. The generated configs target FRRouting's integrated frr.conf.
"""
from __future__ import annotations

import ipaddress
import math
import random
from collections import deque

from .automation import GenerateError, _Builder, _circle, _row
from .models import SHARED_KINDS, Lab, Template

ROUTER = "frr"
HOST = "vpc"
SWITCH = "bridge"

# topic catalogue the UI lists (grouped by category)
TOPICS = [
    # cat = category, level = 1 basic · 2 intermediate · 3 advanced
    {"id": "static", "label": "Static routing", "cat": "Basic", "level": 1, "router": ROUTER,
     "desc": "A chain of routers: static routes to the remote loopbacks."},
    {"id": "rip", "label": "RIP v2", "cat": "Basic", "level": 1, "router": ROUTER,
     "desc": "RIP version 2 between the routers."},
    {"id": "vlan", "label": "L2 VLAN (switch + hosts)", "cat": "Basic", "level": 1, "router": HOST,
     "desc": "One switch with hosts on a single network: a playground for VLANs and L2."},
    {"id": "ospf", "label": "OSPF (single area)", "cat": "IGP", "level": 1, "router": ROUTER,
     "desc": "OSPF area 0 on every link and loopback."},
    {"id": "ospf-multiarea", "label": "OSPF multi-area", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "Area 0 backbone with border routers (ABR) toward peripheral areas."},
    {"id": "isis", "label": "IS-IS", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "IS-IS level 2 between the routers."},
    {"id": "eigrp", "label": "EIGRP", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "EIGRP AS 1 (FRR's eigrpd)."},
    {"id": "redistribute", "label": "Redistribution + loopback", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "OSPF redistributing connected and static networks (routes between protocols)."},
    {"id": "ospf-auth", "label": "OSPF with authentication", "cat": "Security", "level": 2, "router": ROUTER,
     "desc": "OSPF area 0 with MD5 authentication on the adjacencies."},
    {"id": "bfd", "label": "OSPF + BFD (fast failover)", "cat": "IGP", "level": 3, "router": ROUTER,
     "desc": "OSPF with BFD to detect failures in milliseconds."},
    {"id": "ospf-cost", "label": "OSPF – cost and path selection", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "OSPF ring: raising a link's cost shifts traffic onto the other path."},
    {"id": "ospf-default", "label": "OSPF – default route (default-information)", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "One router injects the default route into OSPF: gateway of last resort for all the others."},
    {"id": "bgp-ebgp", "label": "BGP – eBGP", "cat": "BGP", "level": 2, "router": ROUTER,
     "desc": "Each router a different AS, eBGP sessions on the links."},
    {"id": "bgp-ibgp", "label": "BGP – iBGP + OSPF", "cat": "BGP", "level": 3, "router": ROUTER,
     "desc": "A single AS: OSPF as the underlay and iBGP full-mesh on the loopbacks."},
    {"id": "bgp-security", "label": "BGP security (filters + max-prefix)", "cat": "Security", "level": 3, "router": ROUTER,
     "desc": "eBGP with an inbound prefix-list and a maximum-prefix limit: basic anti-hijack."},
    {"id": "route-filter", "label": "Routing filters (ACL)", "cat": "Security", "level": 2, "router": ROUTER,
     "desc": "OSPF with an access-list/distribute-list that blocks a route."},
    {"id": "bgp-communities", "label": "BGP with communities", "cat": "BGP", "level": 3, "router": ROUTER,
     "desc": "eBGP that tags announcements with BGP communities and filters on them."},
    {"id": "bgp-rr", "label": "BGP route reflector", "cat": "BGP", "level": 3, "router": ROUTER,
     "desc": "iBGP with a route reflector instead of the full-mesh."},
    {"id": "bgp-localpref", "label": "BGP – local-preference (path selection)", "cat": "BGP", "level": 3, "router": ROUTER,
     "desc": "eBGP ring: a route-map raises the local-preference to prefer one exit path."},
    {"id": "bgp-aggregate", "label": "BGP – aggregation (summary-only)", "cat": "BGP", "level": 3, "router": ROUTER,
     "desc": "One router announces the aggregate 10.255.0.0/16 instead of the individual /32s (summarization)."},
    {"id": "vrrp", "label": "VRRP (redundant gateway)", "cat": "High availability", "level": 3, "router": ROUTER,
     "desc": "Two routers share a virtual IP as the hosts' gateway."},
    {"id": "mpls-ldp", "label": "MPLS core (LDP)", "cat": "MPLS", "level": 3, "router": ROUTER,
     "desc": "MPLS core with OSPF and label distribution via LDP."},
    {"id": "mpls-l3vpn", "label": "MPLS L3VPN (VRF + MP-BGP)", "cat": "MPLS", "level": 3, "router": ROUTER,
     "desc": "PE-P-PE with VRF and MP-BGP VPNv4 over an MPLS core."},
    {"id": "vxlan-evpn", "label": "VXLAN / EVPN (spine-leaf)", "cat": "Data center", "level": 3, "router": ROUTER,
     "desc": "CLOS spine-leaf with OSPF underlay and BGP EVPN overlay (VXLAN)."},
    {"id": "ipv6-ospf", "label": "IPv6 – OSPFv3", "cat": "IPv6", "level": 2, "router": ROUTER,
     "desc": "OSPFv3 (OSPF for IPv6) between the routers, with 2001:db8::/32 addresses."},
    {"id": "ipv6-bgp", "label": "IPv6 – eBGP", "cat": "IPv6", "level": 3, "router": ROUTER,
     "desc": "eBGP over IPv6 (address-family ipv6 unicast)."},
    {"id": "pbr", "label": "Policy routing (PBR)", "cat": "Advanced routing", "level": 3, "router": ROUTER,
     "desc": "Route traffic by source with a pbr-map (FRR's pbrd)."},
    {"id": "multicast", "label": "Multicast PIM-SM", "cat": "Advanced routing", "level": 3, "router": ROUTER,
     "desc": "PIM sparse-mode with an RP and OSPF as the underlay."},
    # Hacking / offensive security: educational sandbox labs, with the defense in the solution
    {"id": "bgp-hijack", "label": "BGP hijacking (attack and defense)", "cat": "Hacking", "level": 3, "router": ROUTER,
     "desc": "An AS hijacks someone else's prefix; you learn to spot it and defend with AS-path filters."},
    {"id": "mitm-arp", "label": "MITM / ARP spoofing (L2)", "cat": "Hacking", "level": 2, "router": HOST,
     "desc": "Attacker and victims on the same LAN: ARP spoofing and how to defend against it."},
    {"id": "recon-scan", "label": "Reconnaissance and scanning", "cat": "Hacking", "level": 1, "router": HOST,
     "desc": "An attacker and a network of servers: scanning (nmap) and hardening."},
]
TOPIC_IDS = {t["id"] for t in TOPICS}


class _Scn:
    """Thin wrapper over the topology builder that also records which interface each link used."""

    def __init__(self, templates: dict[str, Template], router: str = ROUTER):
        if router not in templates:
            raise GenerateError(f"The selected router '{router}' does not exist")
        self.router = router
        self.b = _Builder(Lab(name="gen"), templates)
        self.links: list[dict] = []  # {a, ia, b, ib}

    def add(self, prefix: str, tpl: str, count: int, positions) -> list[str]:
        return self.b.add(prefix, tpl, count, positions)

    def link(self, a: str, c: str) -> dict:
        self.b.link(a, c)
        (an, ia), (cn, ic) = (e.split(":", 1) for e in self.b.links[-1]["endpoints"])
        rec = {"a": an, "ia": ia, "b": cn, "ib": ic}
        self.links.append(rec)
        return rec

    def result(self) -> dict:
        return self.b.result()


def _p2p(k: int) -> ipaddress.IPv4Network:
    """The k-th point-to-point /30 out of 10.0.0.0/8."""
    return ipaddress.ip_network((0x0A000000 + k * 4, 30))


def _loopbacks(routers: list[str]) -> dict[str, str]:
    return {r: f"10.255.0.{i + 1}" for i, r in enumerate(routers)}


def _address(routers: list[str], links: list[dict]) -> tuple[dict, dict]:
    """loopbacks + per-interface IPs. ifips[router][iface] = (ip, prefix, network, peer_ip, peer)."""
    lo = _loopbacks(routers)
    ifips: dict[str, dict] = {r: {} for r in routers}
    for k, ln in enumerate(links):
        net = _p2p(k)
        a_ip, b_ip = (str(h) for h in net.hosts())
        ifips[ln["a"]][ln["ia"]] = (a_ip, 30, net, b_ip, ln["b"])
        ifips[ln["b"]][ln["ib"]] = (b_ip, 30, net, a_ip, ln["a"])
    return lo, ifips


def _address6(routers: list[str], links: list[dict]) -> tuple[dict, dict]:
    """IPv6: loopbacks /128 and per-link /64. if6[router][iface] = (ip, net, peer_ip, peer)."""
    lo = {r: f"2001:db8:ffff::{i + 1:x}" for i, r in enumerate(routers)}
    if6: dict[str, dict] = {r: {} for r in routers}
    for k, ln in enumerate(links):
        net = f"2001:db8:0:{k:x}::"
        if6[ln["a"]][ln["ia"]] = (net + "1", f"{net}/64", net + "2", ln["b"])
        if6[ln["b"]][ln["ib"]] = (net + "2", f"{net}/64", net + "1", ln["a"])
    return lo, if6


def _ifaces6(r: str, if6: dict, lo: dict, extra=lambda ifc: "") -> str:
    out = f"interface lo\n ipv6 address {lo[r]}/128\n{extra('lo')}!\n"
    for ifc in sorted(if6[r]):
        ip, net, peer_ip, peer = if6[r][ifc]
        out += f"interface {ifc}\n description to {peer}\n ipv6 address {ip}/64\n{extra(ifc)}!\n"
    return out


# ------------------------------------------------------------------ FRR config pieces

def _head(host: str) -> str:
    return (f"frr version 10.2\nfrr defaults traditional\nhostname {host}\n"
            "log syslog informational\nservice integrated-vtysh-config\n!\n")


def _ifaces(r: str, ifips: dict, lo: dict, extra=lambda ifc: "") -> str:
    out = f"interface lo\n ip address {lo[r]}/32\n{extra('lo')}!\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        out += f"interface {ifc}\n description to {peer}\n ip address {ip}/{pfx}\n{extra(ifc)}!\n"
    return out


def _nets(r: str, ifips: dict, lo: dict) -> list[str]:
    nets = [f"{lo[r]}/32"] + [ifips[r][ifc][2].with_prefixlen for ifc in sorted(ifips[r])]
    return nets


# ------------------------------------------------------------------ per-topic config

def _cfg_ospf(r, ifips, lo, routers, links, idx, area="0"):
    body = _head(r) + _ifaces(r, ifips, lo)
    body += f"router ospf\n ospf router-id {lo[r]}\n passive-interface lo\n"
    body += f" network {lo[r]}/32 area {area}\n"
    for ifc in sorted(ifips[r]):
        body += f" network {ifips[r][ifc][2].with_prefixlen} area {area}\n"
    return body + "!\n"


def _cfg_rip(r, ifips, lo, routers, links, idx):
    body = _head(r) + _ifaces(r, ifips, lo)
    body += "router rip\n version 2\n"
    for n in _nets(r, ifips, lo):
        body += f" network {n}\n"
    body += " passive-interface lo\n"
    return body + "!\n"


def _cfg_isis(r, ifips, lo, routers, links, idx):
    sysid = f"0000.0000.{idx + 1:04d}"
    body = _head(r)
    body += _ifaces(r, ifips, lo, extra=lambda ifc: " ip router isis 1\n isis circuit-type level-2-only\n" if ifc != "lo" else " ip router isis 1\n")
    body += f"router isis 1\n net 49.0001.{sysid}.00\n is-type level-2-only\n"
    return body + "!\n"


def _cfg_eigrp(r, ifips, lo, routers, links, idx):
    body = _head(r) + _ifaces(r, ifips, lo)
    body += "router eigrp 1\n"
    for n in _nets(r, ifips, lo):
        body += f" network {n}\n"
    return body + "!\n"


def _cfg_static(r, ifips, lo, routers, links, idx):
    body = _head(r) + _ifaces(r, ifips, lo)
    # a static route to every other loopback via the next hop on the shortest path (works on any topology)
    for other, nb in _first_hops(r, routers, links).items():
        nh = next((ifips[r][ifc][3] for ifc in ifips[r] if ifips[r][ifc][4] == nb), None)
        if nh:
            body += f"ip route {lo[other]}/32 {nh}\n"
    return body + "!\n"


def _cfg_ebgp(r, ifips, lo, routers, links, idx):
    asn = 65001 + idx
    body = _head(r) + _ifaces(r, ifips, lo)
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n no bgp ebgp-requires-policy\n"
    body += f" network {lo[r]}/32\n redistribute connected\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        peer_as = 65001 + routers.index(peer)
        body += f" neighbor {peer_ip} remote-as {peer_as}\n"
    return body + "!\n"


def _cfg_ibgp(r, ifips, lo, routers, links, idx):
    asn = 65000
    body = _head(r) + _ifaces(r, ifips, lo)
    # OSPF underlay so loopbacks reach each other
    body += f"router ospf\n ospf router-id {lo[r]}\n passive-interface lo\n network {lo[r]}/32 area 0\n"
    for ifc in sorted(ifips[r]):
        body += f" network {ifips[r][ifc][2].with_prefixlen} area 0\n"
    body += "!\n"
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n"
    for other in routers:
        if other != r:
            body += f" neighbor {lo[other]} remote-as {asn}\n neighbor {lo[other]} update-source lo\n"
    body += " address-family ipv4 unicast\n"
    body += f"  network {lo[r]}/32\n"
    body += " exit-address-family\n"
    return body + "!\n"


def _cfg_mpls_ldp(r, ifips, lo, routers, links, idx):
    body = _head(r) + _ifaces(r, ifips, lo, extra=lambda ifc: " mpls enable\n" if ifc != "lo" else "")
    body += f"router ospf\n ospf router-id {lo[r]}\n passive-interface lo\n network {lo[r]}/32 area 0\n"
    for ifc in sorted(ifips[r]):
        body += f" network {ifips[r][ifc][2].with_prefixlen} area 0\n"
    body += "!\n"
    body += f"mpls ldp\n router-id {lo[r]}\n address-family ipv4\n  discovery transport-address {lo[r]}\n"
    for ifc in sorted(ifips[r]):
        body += f"  interface {ifc}\n"
    body += " exit-address-family\n!\n"
    return body


def _ospf_block(r, ifips, lo, extra_iface="", extra_router=""):
    body = f"router ospf\n ospf router-id {lo[r]}\n passive-interface lo\n network {lo[r]}/32 area 0\n"
    for ifc in sorted(ifips[r]):
        body += f" network {ifips[r][ifc][2].with_prefixlen} area 0\n"
    return body + extra_router + "!\n"


def _cfg_ospf_auth(r, ifips, lo, routers, links, idx):
    body = _head(r)
    body += _ifaces(r, ifips, lo, extra=lambda ifc: "" if ifc == "lo" else " ip ospf authentication message-digest\n ip ospf message-digest-key 1 md5 NetSO2026\n")
    body += _ospf_block(r, ifips, lo, extra_router=" area 0 authentication message-digest\n")
    return body


def _cfg_bfd(r, ifips, lo, routers, links, idx):
    body = _head(r)
    body += _ifaces(r, ifips, lo, extra=lambda ifc: "" if ifc == "lo" else " ip ospf bfd\n")
    body += "bfd\n!\n" + _ospf_block(r, ifips, lo)
    return body


def _cfg_redistribute(r, ifips, lo, routers, links, idx):
    lan = f"172.16.{idx + 1}.0/24"
    body = _head(r) + _ifaces(r, ifips, lo)
    body += f"ip route {lan} Null0\n!\n"
    body += _ospf_block(r, ifips, lo, extra_router=" redistribute connected\n redistribute static\n")
    return body


def _cfg_bgp_security(r, ifips, lo, routers, links, idx):
    asn = 65001 + idx
    body = _head(r) + _ifaces(r, ifips, lo)
    body += ("ip prefix-list LOOPBACKS seq 5 permit 10.255.0.0/16 ge 32\n"
             "ip prefix-list LOOPBACKS seq 10 deny any\n!\n")
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n no bgp ebgp-requires-policy\n network {lo[r]}/32\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        body += f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n"
        body += f" neighbor {peer_ip} prefix-list LOOPBACKS in\n neighbor {peer_ip} maximum-prefix 100\n"
    return body + "!\n"


def _cfg_bgp_comm(r, ifips, lo, routers, links, idx):
    asn = 65001 + idx
    body = _head(r) + _ifaces(r, ifips, lo)
    body += f"route-map TAG permit 10\n set community {asn}:100\n!\n"
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n no bgp ebgp-requires-policy\n network {lo[r]}/32 route-map TAG\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        body += f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n neighbor {peer_ip} send-community\n"
    return body + "!\n"


def _cfg_route_filter(r, ifips, lo, routers, links, idx):
    blocked = lo[routers[-1]]   # everyone filters the last router's loopback out of OSPF
    body = _head(r) + _ifaces(r, ifips, lo)
    body += f"access-list FILTER seq 5 deny {blocked}/32\naccess-list FILTER seq 10 permit any\n!\n"
    extra = "" if r == routers[-1] else " distribute-list FILTER in\n"
    body += _ospf_block(r, ifips, lo, extra_router=extra)
    return body


def _cfg_bgp_rr(r, ifips, lo, routers, links, idx):
    asn, rr = 65000, routers[0]
    body = _head(r) + _ifaces(r, ifips, lo) + _ospf_block(r, ifips, lo)   # OSPF underlay
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n"
    if r == rr:   # the route reflector peers with every client
        for o in routers[1:]:
            body += f" neighbor {lo[o]} remote-as {asn}\n neighbor {lo[o]} update-source lo\n"
        body += " address-family ipv4 unicast\n"
        for o in routers[1:]:
            body += f"  neighbor {lo[o]} activate\n  neighbor {lo[o]} route-reflector-client\n"
        body += f"  network {lo[r]}/32\n exit-address-family\n!\n"
    else:         # a client only peers with the RR
        body += f" neighbor {lo[rr]} remote-as {asn}\n neighbor {lo[rr]} update-source lo\n"
        body += f" address-family ipv4 unicast\n  neighbor {lo[rr]} activate\n  network {lo[r]}/32\n exit-address-family\n!\n"
    return body


def _cfg_ospf_cost(r, ifips, lo, routers, links, idx):
    """OSPF everywhere; one exit of the first router has a high cost, so the second path is used toward the far end."""
    a = routers[0]
    a_ifs = sorted(ifips.get(a, {}))
    b = ifips[a][a_ifs[0]][4] if a_ifs else None   # the neighbour on the first interface of the first router

    def extra(ifc):
        if ifc == "lo" or b is None:
            return ""
        return " ip ospf cost 100\n" if {r, ifips[r][ifc][4]} == {a, b} else ""
    body = _head(r) + _ifaces(r, ifips, lo, extra=extra)
    body += _ospf_block(r, ifips, lo)
    return body


def _cfg_ospf_default(r, ifips, lo, routers, links, idx):
    """The first router injects the default route into OSPF (gateway of last resort for the others)."""
    body = _head(r) + _ifaces(r, ifips, lo)
    if r == routers[0]:
        body += "ip route 0.0.0.0/0 Null0\n!\n"
        body += _ospf_block(r, ifips, lo, extra_router=" default-information originate\n")
    else:
        body += _ospf_block(r, ifips, lo)
    return body


def _cfg_bgp_localpref(r, ifips, lo, routers, links, idx):
    """eBGP + local-preference: the routes learned from the first neighbour are preferred in path selection."""
    asn = 65001 + idx
    body = _head(r) + _ifaces(r, ifips, lo)
    body += "route-map PREFER permit 10\n set local-preference 200\n!\n"
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n no bgp ebgp-requires-policy\n"
    body += f" network {lo[r]}/32\n redistribute connected\n"
    for j, ifc in enumerate(sorted(ifips[r])):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        body += f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n"
        if j == 0:
            body += f" neighbor {peer_ip} route-map PREFER in\n"
    return body + "!\n"


def _cfg_bgp_aggregate(r, ifips, lo, routers, links, idx):
    """eBGP; the first router announces the aggregate 10.255.0.0/16 summary-only instead of the individual /32s."""
    asn = 65001 + idx
    body = _head(r) + _ifaces(r, ifips, lo)
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n no bgp ebgp-requires-policy\n"
    body += f" network {lo[r]}/32\n"
    if idx == 0:
        body += " aggregate-address 10.255.0.0/16 summary-only\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        body += f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n"
    return body + "!\n"


def _cfg_vrrp_gw(name, iface, lan_ip, vip, idx):
    body = _head(name)
    body += f"interface lo\n ip address 10.255.0.{idx + 1}/32\n!\n"
    body += (f"interface {iface}\n ip address {lan_ip}/24\n vrrp 1 ip {vip}\n"
             f" vrrp 1 priority {110 if idx == 0 else 100}\n no vrrp 1 shutdown\n!\n")
    return body


def _pe_vrf(pe_lo, peer_los, idx):
    """VRF + MP-BGP VPNv4 part of a PE (added after its core MPLS/LDP config).
    `peer_los` is the loopback of every other PE (iBGP VPNv4 full mesh)."""
    if isinstance(peer_los, str):
        peer_los = [peer_los]
    cust = f"172.16.{idx + 1}.1"
    body = "vrf CUST\n exit-vrf\n!\n"
    body += f"interface lo-cust vrf CUST\n ip address {cust}/32\n!\n"
    body += f"router bgp 65000\n bgp router-id {pe_lo}\n"
    for peer in peer_los:
        body += f" neighbor {peer} remote-as 65000\n neighbor {peer} update-source lo\n"
    body += " address-family ipv4 vpn\n"
    for peer in peer_los:
        body += f"  neighbor {peer} activate\n"
    body += " exit-address-family\n!\n"
    body += (f"router bgp 65000 vrf CUST\n address-family ipv4 unicast\n  rd vpn export 65000:{idx + 1}\n"
             "  rt vpn both 65000:100\n  redistribute connected\n  label vpn export auto\n"
             "  export vpn\n  import vpn\n exit-address-family\n!\n")
    return body


def _cfg_ospf6(r, if6, lo, routers, idx):
    body = _head(r) + _ifaces6(r, if6, lo, extra=lambda ifc: " ipv6 ospf6 area 0\n")
    body += f"router ospf6\n ospf6 router-id 0.0.0.{idx + 1}\n!\n"
    return body


def _cfg_bgp6(r, if6, lo, routers, idx):
    asn = 65001 + idx
    body = _head(r) + _ifaces6(r, if6, lo)
    body += f"router bgp {asn}\n bgp router-id 0.0.0.{idx + 1}\n no bgp ebgp-requires-policy\n"
    for ifc in sorted(if6[r]):
        ip, net, peer_ip, peer = if6[r][ifc]
        body += f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n"
    body += f" address-family ipv6 unicast\n  network {lo[r]}/128\n"
    for ifc in sorted(if6[r]):
        body += f"  neighbor {if6[r][ifc][2]} activate\n"
    body += " exit-address-family\n!\n"
    return body


def _cfg_pbr(r, ifips, lo, routers, links, idx):
    body = _head(r) + _ifaces(r, ifips, lo) + _ospf_block(r, ifips, lo)
    if idx == 0 and ifips[r]:                       # the first router gets a demo policy
        ifc = sorted(ifips[r])[0]
        nh = ifips[r][ifc][3]                       # send matched traffic via this neighbour
        body += f"nexthop-group NH1\n nexthop {nh}\nexit\n!\n"
        body += "pbr-map MAP seq 10\n match src-ip 10.255.0.0/16\n set nexthop-group NH1\nexit\n!\n"
        body += f"interface {ifc}\n pbr-policy MAP\n!\n"
    return body


def _cfg_pim(r, ifips, lo, routers, links, idx):
    rp = lo[routers[0]]   # the first router is the Rendez-vous Point
    body = _head(r) + _ifaces(r, ifips, lo, extra=lambda ifc: " ip pim\n")
    body += _ospf_block(r, ifips, lo)            # unicast underlay for RPF
    body += f"ip pim rp {rp} 224.0.0.0/4\n!\n"
    return body


def _cfg_bgp_hijack(r, ifips, lo, routers, links, idx):
    """eBGP where r1 (AS 65001) owns 203.0.113.0/24; the solution filters hijacks of that prefix."""
    asn = 65001 + idx
    body = _head(r) + _ifaces(r, ifips, lo)
    # anti-hijack: accept 203.0.113.0/24 only if it originates from the legit AS (65001)
    body += "bgp as-path access-list LEGIT permit _65001$\n!\n"
    body += "ip prefix-list PROTECTED seq 5 permit 203.0.113.0/24\n!\n"
    body += ("route-map ANTIHIJACK permit 10\n match as-path LEGIT\nexit\n"
             "route-map ANTIHIJACK deny 20\n match ip address prefix-list PROTECTED\nexit\n"
             "route-map ANTIHIJACK permit 30\nexit\n!\n")
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n no bgp ebgp-requires-policy\n network {lo[r]}/32\n"
    if idx == 0:
        body += " network 203.0.113.0/24\n"       # the victim legitimately announces the prefix
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        body += f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n neighbor {peer_ip} route-map ANTIHIJACK in\n"
    return body + "!\n"


def _hosts_lab(templates, n_targets, attacker_prefix="kali", gateway=False):
    """A LAN (bridge) with one attacker host, some targets, optionally a gateway router. Returns res + roles."""
    b = _Builder(Lab(name="gen"), templates)
    sw = b.add("sw", SWITCH, 1, [(420, 110)])[0]
    atk = b.add(attacker_prefix, HOST, 1, [(180, 330)])[0]
    gw = b.add("gw", ROUTER, 1, [(640, 200)])[0] if gateway else None
    targets = b.add("srv" if gateway else "pc", HOST, n_targets, _row(n_targets, 300, 440, 120))
    b.link(sw, atk)
    if gw:
        b.link(sw, gw)
    for tgt in targets:
        b.link(sw, tgt)
    res = b.result()
    for ln in res["links"]:
        ln["label"] = "192.168.1.0/24"
    return res, atk, gw, targets


def _guide6(topic, meta, routers, if6, lo, solution):
    steps, verify = STEPS.get(topic, ([], []))
    out = [f"# Lab: {meta['label']}", "", "## Objective", meta["desc"],
           f"Configure **{meta['label']}** on the {len(routers)} FRR routers (IPv6) and verify reachability between the loopbacks."]
    if CONCEPT.get(topic):
        out += ["", "## How it works", CONCEPT[topic]]
    out += ["", "## IPv6 addresses (already set at boot)",
            "The IPv6 addresses are already configured: every link is a point-to-point /64 and every router has a "
            "/128 loopback. You only need to enable the protocol; use this table to know who is next to whom."]
    for r in routers:
        out.append(f"- **{r}** — loopback {lo[r]}/128")
        for ifc in sorted(if6[r]):
            ip, net, peer_ip, peer = if6[r][ifc]
            out.append(f"    - `{ifc}` {ip}/64  →  toward **{peer}** ({peer_ip})")
    out += ["", "## Steps",
            "Open the console of each FRR router (`vtysh` → `configure terminal`) and run these steps on "
            "**every** router, adapting interfaces and addresses to the table above:"]
    out += [f"{i + 1}. {s}" for i, s in enumerate(steps)]
    out += ["", "## Verification",
            "After configuring all the routers, check the adjacencies, tables and connectivity:"]
    out += [f"- {v}" for v in verify]
    out += ["", "## Troubleshooting"]
    out += [f"- {t}" for t in TROUBLE.get(topic, [])]
    out += [f"- {t}" for t in TROUBLE_GENERIC]
    out += ["", "## Solution", "The full config is also in the «solution» set. Per node:"]
    for r in routers:
        out.append(f"\n### {r}\n```\n{solution[r].strip()}\n```")
    return "\n".join(out)


def _cfg_evpn_spine(r, ifips, lo, routers, links, idx, leaves):
    body = _head(r) + _ifaces(r, ifips, lo)
    body += f"router ospf\n ospf router-id {lo[r]}\n passive-interface lo\n network {lo[r]}/32 area 0\n"
    for ifc in sorted(ifips[r]):
        body += f" network {ifips[r][ifc][2].with_prefixlen} area 0\n"
    body += "!\n"
    body += f"router bgp 65000\n bgp router-id {lo[r]}\n"
    for le in leaves:
        body += f" neighbor {lo[le]} remote-as 65000\n neighbor {lo[le]} update-source lo\n"
    body += " address-family l2vpn evpn\n"
    for le in leaves:
        body += f"  neighbor {lo[le]} activate\n"
    body += "  route-reflector-client\n exit-address-family\n!\n"
    return body


def _cfg_evpn_leaf(r, ifips, lo, routers, links, idx, spines, vni):
    body = _head(r) + _ifaces(r, ifips, lo)
    body += f"router ospf\n ospf router-id {lo[r]}\n passive-interface lo\n network {lo[r]}/32 area 0\n"
    for ifc in sorted(ifips[r]):
        body += f" network {ifips[r][ifc][2].with_prefixlen} area 0\n"
    body += "!\n"
    body += f"router bgp 65000\n bgp router-id {lo[r]}\n"
    for sp in spines:
        body += f" neighbor {lo[sp]} remote-as 65000\n neighbor {lo[sp]} update-source lo\n"
    body += " address-family l2vpn evpn\n"
    for sp in spines:
        body += f"  neighbor {lo[sp]} activate\n"
    body += "  advertise-all-vni\n exit-address-family\n!\n"
    body += f"vni {vni}\n!\n"
    return body


# ------------------------------------------------------------------ topology builders

def _router_chain_or_ring(scn: _Scn, rng: random.Random, n: int, pattern: str) -> list[str]:
    rt = scn.router
    if pattern == "ring":
        rs = scn.add("r", rt, n, _circle(n, 420, 320, max(160, n * 45)))
        for i in range(n):
            scn.link(rs[i], rs[(i + 1) % n])
    elif pattern == "mesh":
        rs = scn.add("r", rt, n, _circle(n, 420, 320, max(160, n * 45)))
        for i in range(n):
            for j in range(i + 1, n):
                scn.link(rs[i], rs[j])
    else:  # chain — a serpentine so long chains wrap into rows instead of one endless straight line
        cols = min(n, 6)
        pos = []
        for i in range(n):
            row, col = divmod(i, cols)
            if row % 2:               # odd rows run right-to-left, so the chain snakes without crossing
                col = cols - 1 - col
            pos.append((120 + col * 200, 160 + row * 190))
        rs = scn.add("r", rt, n, pos)
        for i in range(n - 1):
            scn.link(rs[i], rs[i + 1])
    return rs


def _realistic_topology(scn: _Scn, rng: random.Random, n: int, cores: int | None = None) -> list[str]:
    """A real-world shape instead of a line/ring: a small redundant core with branch/access routers
    hanging off it (some dual-homed), drawn as a core in the middle with the sites around it.
    Randomised (core size, homing, dual-homing, back-door links, positions) so no two labs look alike."""
    if cores is None:
        cores = 1 if n <= 2 else rng.randint(2, 3) if n <= 7 else rng.randint(3, 4)
    c = max(1, min(cores, n - 1 if n > 1 else 1, 4))
    cx, cy = 480, 350
    jig = lambda r: rng.uniform(-r, r)
    pos: list[tuple[float, float]] = [(cx, cy)] * n
    base = rng.uniform(0, 2 * math.pi)                  # whole picture is rotated a bit each time
    for i in range(c):                                  # core cluster near the centre
        ang = base + 2 * math.pi * i / c
        rr = 0 if c == 1 else 150
        pos[i] = (cx + rr * math.cos(ang) + jig(18), cy + rr * 0.7 * math.sin(ang) + jig(18))
    acc = list(range(c, n))                             # access / branch routers on an ellipse around the core
    rx, ry = max(400, len(acc) * 44), max(280, len(acc) * 30)
    a0 = rng.uniform(0, 2 * math.pi)
    for k, idx in enumerate(acc):
        ang = a0 + 2 * math.pi * k / max(1, len(acc))
        pos[idx] = (cx + rx * math.cos(ang) + jig(24), cy + ry * math.sin(ang) + jig(24))
    rs = scn.add("r", scn.router, n, pos)
    if c == 2:                                          # core interconnect: redundant among the cores
        scn.link(rs[0], rs[1])
    elif c >= 3:
        for i in range(c):
            scn.link(rs[i], rs[(i + 1) % c])            # ring (triangle for 3)
        if c >= 4 and rng.random() < 0.6:
            scn.link(rs[0], rs[2])                      # + a diagonal → partial mesh
    for idx in acc:                                     # each site homes to a (random) core…
        primary = rng.randrange(c)
        scn.link(rs[idx], rs[primary])
        if c >= 2 and rng.random() < 0.55:              # …and about half are dual-homed for redundancy
            scn.link(rs[idx], rs[rng.choice([x for x in range(c) if x != primary])])
    if len(acc) >= 2 and rng.random() < 0.35:           # once in a while a back-door link between two sites
        a, b = rng.sample(acc, 2)
        scn.link(rs[a], rs[b])
    return rs


def _twotier_topology(scn: _Scn, rng: random.Random, n: int) -> list[str]:
    """Three tiers (core → distribution → access): a 2-router core, a distribution row linked to both
    cores, and access routers under the distribution. Always fully connected."""
    core_n = 2
    d = max(1, min(rng.randint(2, 3), n - core_n - 1))
    a = n - core_n - d
    pos: list[tuple[float, float]] = []
    for i in range(core_n):                             # core row (top)
        pos.append((400 + i * 220 + rng.uniform(-15, 15), 150 + rng.uniform(-12, 12)))
    for j in range(d):                                  # distribution row (middle)
        x = 180 + j * (620 / max(1, d - 1)) if d > 1 else 510
        pos.append((x + rng.uniform(-18, 18), 360 + rng.uniform(-12, 12)))
    for k in range(a):                                  # access row (bottom)
        x = 150 + k * (680 / max(1, a - 1)) if a > 1 else 510
        pos.append((x + rng.uniform(-18, 18), 560 + rng.uniform(-12, 12)))
    rs = scn.add("r", scn.router, n, pos)
    core, dists, accs = rs[:core_n], rs[core_n:core_n + d], rs[core_n + d:]
    scn.link(core[0], core[1])
    for dd in dists:                                    # every distribution router to both cores
        scn.link(dd, core[0])
        scn.link(dd, core[1])
    for aa in accs:                                     # each access router to a distribution one
        scn.link(aa, dists[rng.randrange(d)])
        if d >= 2 and rng.random() < 0.4:
            scn.link(aa, dists[rng.randrange(d)])
    return rs


def _mesh_topology(scn: _Scn, rng: random.Random, n: int) -> list[str]:
    """A partial mesh: a ring (guarantees connectivity) plus a handful of random chords."""
    cx, cy = 480, 360
    R = max(220, n * 26)
    base = rng.uniform(0, 2 * math.pi)
    pos = [(cx + R * math.cos(base + 2 * math.pi * i / n), cy + R * 0.78 * math.sin(base + 2 * math.pi * i / n))
           for i in range(n)]
    rs = scn.add("r", scn.router, n, pos)
    seen = set()
    for i in range(n):                                  # ring → connected
        scn.link(rs[i], rs[(i + 1) % n])
        seen |= {(i, (i + 1) % n), ((i + 1) % n, i)}
    chords, tries = rng.randint(max(1, n // 3), max(2, n // 2)), 0
    while chords > 0 and tries < 60:
        tries += 1
        a, b = rng.sample(range(n), 2)
        if (a, b) in seen:
            continue
        scn.link(rs[a], rs[b])
        seen |= {(a, b), (b, a)}
        chords -= 1
    return rs


def _varied_topology(scn: _Scn, rng: random.Random, n: int) -> tuple[list[str], str]:
    """Pick a random real-world shape (always fully connected) so each generated lab looks different."""
    choices = ["core"]
    if n >= 4:
        choices += ["mesh", "dualhub"]
    if n >= 5:
        choices += ["twotier"]
    style = rng.choice(choices)
    if style == "twotier":
        return _twotier_topology(scn, rng, n), "two tiers (core + distribution + access)"
    if style == "mesh":
        return _mesh_topology(scn, rng, n), "partial mesh"
    if style == "dualhub":
        return _realistic_topology(scn, rng, n, cores=2), "dual core + branches"
    return _realistic_topology(scn, rng, n), "redundant core + branches"


def _dualpath_topology(scn: _Scn, rng: random.Random, n: int) -> list[str]:
    """Two diverse paths between the two ends (A … B): a redundant design for path-selection labs."""
    top = (n - 2 + 1) // 2
    bot = (n - 2) - top
    cy = 340
    pos: list[tuple[float, float]] = [(170, cy), (880, cy)]      # A (left), B (right)
    for j in range(top):
        pos.append((170 + (880 - 170) * (j + 1) / (top + 1), cy - 170))
    for j in range(bot):
        pos.append((170 + (880 - 170) * (j + 1) / (bot + 1), cy + 170))
    rs = scn.add("r", scn.router, n, pos)
    a, b = rs[0], rs[1]
    for path in (rs[2:2 + top], rs[2 + top:2 + top + bot]):
        prev = a
        for x in path:
            scn.link(prev, x)
            prev = x
        scn.link(prev, b)
    return rs


def _first_hops(r: str, routers: list[str], links: list[dict]) -> dict[str, str]:
    """BFS over the link graph from r; returns {destination router: the neighbour of r on the shortest path}."""
    adj: dict[str, set] = {x: set() for x in routers}
    for ln in links:
        adj[ln["a"]].add(ln["b"])
        adj[ln["b"]].add(ln["a"])
    parent = {r: r}
    q = deque([r])
    while q:
        u = q.popleft()
        for v in sorted(adj[u]):
            if v not in parent:
                parent[v] = u
                q.append(v)
    hops = {}
    for o in routers:
        if o == r or o not in parent:
            continue
        cur = o
        while parent[cur] != r:
            cur = parent[cur]
        hops[o] = cur
    return hops


def _clamp(count, default: int, lo: int, hi: int) -> int:
    if count is None:
        return default
    try:
        return max(lo, min(hi, int(count)))
    except (TypeError, ValueError):
        return default


def _base_configs(routers: list[str], ifips: dict, lo: dict) -> dict[str, str]:
    """Just the IP addressing (interfaces + loopback): the lab boots with this, the student adds the protocol."""
    return {r: _head(r) + _ifaces(r, ifips, lo) for r in routers}


# ================================================================== multi-vendor
# The scenarios' configs are generated in the syntax ("dialect") of the chosen router.
# We only generate what we can write ourselves: FRRouting (frr.conf) and Cisco IOS
# (IOL, Dynamips c7200/c3725, vIOS/IOSv, CSR, c8000v — and Arista EOS, IOS-like for
# basic routing). Everything else has no generator, so it can't be a scenario router.

def dialect_of(tpl: Template) -> str | None:
    """'frr', 'ios', or None when we can't generate configs for this device."""
    name = (tpl.name or "").lower()
    img = (tpl.image or "").lower()
    if "l2" in name or tpl.kind in SHARED_KINDS:
        return None  # L2 switches, bridges, NAT… aren't scenario routers
    if tpl.name == ROUTER or "frr" in name:
        return "frr"
    if tpl.kind in ("iol", "dynamips"):
        return "ios"
    if tpl.kind == "qemu" and any(k in name for k in ("iosv", "csr", "c8000", "vios", "cat8000")):
        return "ios"
    if tpl.kind == "docker" and ("ceos" in name or "arista" in name or "eos" in img):
        return "ios"
    return None


# topics that don't use a router dialect at all (switch + hosts)
HOST_TOPICS = {"vlan", "mitm-arp", "recon-scan"}
# topics for which we only have an FRR generator (feature not available / reliable on Cisco IOL/IOSv)
FRR_ONLY_TOPICS = {"vxlan-evpn", "ospf-cost", "ospf-default", "bgp-localpref", "bgp-aggregate"}
# everything else that uses a router: we can write it in Cisco IOS too (IOL, IOSv, c7200/c3725, …)
IOS_TOPICS = {t["id"] for t in TOPICS if t["id"] not in HOST_TOPICS and t["id"] not in FRR_ONLY_TOPICS}


def _default_router(templates: dict[str, Template]) -> str:
    if ROUTER in templates:
        return ROUTER
    for n, t in templates.items():
        if dialect_of(t) == "frr":
            return n
    for n, t in templates.items():
        if dialect_of(t):
            return n
    return ROUTER  # nothing usable; the caller raises a clear error


def _mask(pfx: int) -> str:
    return str(ipaddress.ip_network(f"0.0.0.0/{pfx}").netmask)


# ---- Cisco IOS config pieces (mirror the FRR ones above) ----------------------

def _head_ios(host: str) -> str:
    return f"hostname {host}\n!\nno ip domain lookup\nip cef\n!\n"


def _ifaces_ios(r: str, ifips: dict, lo: dict, extra=lambda ifc: "") -> str:
    out = f"interface Loopback0\n ip address {lo[r]} 255.255.255.255\n{extra('lo')}!\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        out += (f"interface {ifc}\n description to {peer}\n"
                f" ip address {ip} {_mask(pfx)}\n no shutdown\n{extra(ifc)}!\n")
    return out


def _ios_ospf(r, ifips, lo, routers, links, idx, area="0"):
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    body += f"router ospf 1\n router-id {lo[r]}\n passive-interface Loopback0\n"
    body += f" network {lo[r]} 0.0.0.0 area {area}\n"
    for ifc in sorted(ifips[r]):
        net = ifips[r][ifc][2]
        body += f" network {net.network_address} {net.hostmask} area {area}\n"
    return body + "!\n"


def _ios_rip(r, ifips, lo, routers, links, idx):
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    # every address lives in 10.0.0.0/8, so one classful network covers them all
    body += "router rip\n version 2\n no auto-summary\n network 10.0.0.0\n passive-interface Loopback0\n"
    return body + "!\n"


def _ios_eigrp(r, ifips, lo, routers, links, idx):
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    body += "router eigrp 1\n no auto-summary\n"
    for n in _nets(r, ifips, lo):
        net = ipaddress.ip_network(n, strict=False)
        body += f" network {net.network_address} {net.hostmask}\n"
    body += " passive-interface Loopback0\n"
    return body + "!\n"


def _ios_isis(r, ifips, lo, routers, links, idx):
    sysid = f"0000.0000.{idx + 1:04d}"
    body = _head_ios(r) + _ifaces_ios(
        r, ifips, lo,
        extra=lambda ifc: " ip router isis 1\n" + ("" if ifc == "lo" else " isis circuit-type level-2-only\n"))
    body += f"router isis 1\n net 49.0001.{sysid}.00\n is-type level-2-only\n metric-style wide\n"
    return body + "!\n"


def _ios_static(r, ifips, lo, routers, links, idx):
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    for other, nb in _first_hops(r, routers, links).items():
        nh = next((ifips[r][ifc][3] for ifc in ifips[r] if ifips[r][ifc][4] == nb), None)
        if nh:
            body += f"ip route {lo[other]} 255.255.255.255 {nh}\n"
    return body + "!\n"


def _ios_ebgp(r, ifips, lo, routers, links, idx):
    asn = 65001 + idx
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n bgp log-neighbor-changes\n"
    body += f" network {lo[r]} mask 255.255.255.255\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        peer_as = 65001 + routers.index(peer)
        body += f" neighbor {peer_ip} remote-as {peer_as}\n"
    return body + "!\n"


def _ios_ibgp(r, ifips, lo, routers, links, idx):
    asn = 65000
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    body += f"router ospf 1\n router-id {lo[r]}\n passive-interface Loopback0\n network {lo[r]} 0.0.0.0 area 0\n"
    for ifc in sorted(ifips[r]):
        net = ifips[r][ifc][2]
        body += f" network {net.network_address} {net.hostmask} area 0\n"
    body += "!\n"
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n bgp log-neighbor-changes\n"
    for other in routers:
        if other != r:
            body += f" neighbor {lo[other]} remote-as {asn}\n neighbor {lo[other]} update-source Loopback0\n"
    body += f" network {lo[r]} mask 255.255.255.255\n"
    return body + "!\n"


def _base_configs_ios(routers: list[str], ifips: dict, lo: dict) -> dict[str, str]:
    return {r: _head_ios(r) + _ifaces_ios(r, ifips, lo) for r in routers}


# ---- advanced Cisco IOS pieces (mirror the FRR _cfg_* above) -------------------

def _ios_ospf_block(r, ifips, lo, extra_router=""):
    body = f"router ospf 1\n router-id {lo[r]}\n passive-interface Loopback0\n network {lo[r]} 0.0.0.0 area 0\n"
    for ifc in sorted(ifips[r]):
        net = ifips[r][ifc][2]
        body += f" network {net.network_address} {net.hostmask} area 0\n"
    return body + extra_router + "!\n"


def _ios_mpls_ldp(r, ifips, lo, routers, links, idx):
    body = _head_ios(r) + "mpls label protocol ldp\nmpls ldp router-id Loopback0 force\n!\n"
    body += _ifaces_ios(r, ifips, lo, extra=lambda ifc: "" if ifc == "lo" else " mpls ip\n")
    body += _ios_ospf_block(r, ifips, lo)
    return body


def _ios_redistribute(r, ifips, lo, routers, links, idx):
    lan = f"172.16.{idx + 1}.0"
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    body += f"ip route {lan} 255.255.255.0 Null0\n!\n"
    body += _ios_ospf_block(r, ifips, lo, extra_router=" redistribute connected subnets\n redistribute static subnets\n")
    return body


def _ios_bfd(r, ifips, lo, routers, links, idx):
    body = _head_ios(r)
    body += _ifaces_ios(r, ifips, lo, extra=lambda ifc: "" if ifc == "lo" else " bfd interval 300 min_rx 300 multiplier 3\n ip ospf bfd\n")
    body += _ios_ospf_block(r, ifips, lo)
    return body


def _ios_route_filter(r, ifips, lo, routers, links, idx):
    blocked = lo[routers[-1]]
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    body += f"access-list 50 deny {blocked}\naccess-list 50 permit any\n!\n"
    extra = "" if r == routers[-1] else " distribute-list 50 in\n"
    body += _ios_ospf_block(r, ifips, lo, extra_router=extra)
    return body


def _ios_bgp_rr(r, ifips, lo, routers, links, idx):
    asn, rr = 65000, routers[0]
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo) + _ios_ospf_block(r, ifips, lo)  # OSPF underlay
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n bgp log-neighbor-changes\n"
    body += f" network {lo[r]} mask 255.255.255.255\n"
    if r == rr:   # the route reflector peers with every client
        for o in routers[1:]:
            body += (f" neighbor {lo[o]} remote-as {asn}\n neighbor {lo[o]} update-source Loopback0\n"
                     f" neighbor {lo[o]} route-reflector-client\n")
    else:         # a client only peers with the RR
        body += f" neighbor {lo[rr]} remote-as {asn}\n neighbor {lo[rr]} update-source Loopback0\n"
    return body + "!\n"


def _ios_pim(r, ifips, lo, routers, links, idx):
    rp = lo[routers[0]]   # the first router is the Rendez-vous Point
    body = _head_ios(r) + "ip multicast-routing\n!\n"
    body += _ifaces_ios(r, ifips, lo, extra=lambda ifc: " ip pim sparse-mode\n")
    body += _ios_ospf_block(r, ifips, lo)            # unicast underlay for RPF
    body += f"ip pim rp-address {rp}\n!\n"
    return body


def _ios_pbr(r, ifips, lo, routers, links, idx):
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo) + _ios_ospf_block(r, ifips, lo)
    if idx == 0 and ifips[r]:                       # the first router gets a demo policy
        ifc = sorted(ifips[r])[0]
        nh = ifips[r][ifc][3]                       # send matched traffic via this neighbour
        body += "access-list 100 permit ip 10.255.0.0 0.0.255.255 any\n!\n"
        body += f"route-map MAP permit 10\n match ip address 100\n set ip next-hop {nh}\n!\n"
        body += f"interface {ifc}\n ip policy route-map MAP\n!\n"
    return body


def _ios_bgp_security(r, ifips, lo, routers, links, idx):
    asn = 65001 + idx
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    body += ("ip prefix-list LOOPBACKS seq 5 permit 10.255.0.0/16 ge 32\n"
             "ip prefix-list LOOPBACKS seq 10 deny 0.0.0.0/0 le 32\n!\n")
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n bgp log-neighbor-changes\n network {lo[r]} mask 255.255.255.255\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        body += (f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n"
                 f" neighbor {peer_ip} prefix-list LOOPBACKS in\n neighbor {peer_ip} maximum-prefix 100\n")
    return body + "!\n"


def _ios_bgp_comm(r, ifips, lo, routers, links, idx):
    asn = 65001 + idx
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    body += f"route-map TAG permit 10\n set community {asn}:100\n!\n"
    body += (f"router bgp {asn}\n bgp router-id {lo[r]}\n bgp log-neighbor-changes\n"
             f" network {lo[r]} mask 255.255.255.255 route-map TAG\n")
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        body += f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n neighbor {peer_ip} send-community\n"
    return body + "!\n"


def _ios_ospf_auth(r, ifips, lo, routers, links, idx):
    body = _head_ios(r)
    body += _ifaces_ios(r, ifips, lo, extra=lambda ifc: "" if ifc == "lo" else
                        " ip ospf authentication message-digest\n ip ospf message-digest-key 1 md5 NetSO2026\n")
    body += _ios_ospf_block(r, ifips, lo, extra_router=" area 0 authentication message-digest\n")
    return body


def _ios_bgp_hijack(r, ifips, lo, routers, links, idx):
    asn = 65001 + idx
    body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
    body += "ip as-path access-list 1 permit _65001$\n!\n"
    body += "ip prefix-list PROTECTED seq 5 permit 203.0.113.0/24\n!\n"
    body += ("route-map ANTIHIJACK permit 10\n match as-path 1\n!\n"
             "route-map ANTIHIJACK deny 20\n match ip address prefix-list PROTECTED\n!\n"
             "route-map ANTIHIJACK permit 30\n!\n")
    body += f"router bgp {asn}\n bgp router-id {lo[r]}\n bgp log-neighbor-changes\n network {lo[r]} mask 255.255.255.255\n"
    if idx == 0:
        body += " network 203.0.113.0 mask 255.255.255.0\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        body += f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n neighbor {peer_ip} route-map ANTIHIJACK in\n"
    return body + "!\n"


def _ios_pe_vrf(pe_lo, peer_los, idx):
    """VRF + MP-BGP VPNv4 part of an IOS PE (added after its core MPLS/LDP config).
    `peer_los` is the loopback of every other PE (iBGP VPNv4 full mesh)."""
    if isinstance(peer_los, str):
        peer_los = [peer_los]
    cust = f"172.16.{idx + 1}.1"
    body = (f"vrf definition CUST\n rd 65000:{idx + 1}\n address-family ipv4\n"
            "  route-target export 65000:100\n  route-target import 65000:100\n exit-address-family\n!\n")
    body += f"interface Loopback1\n vrf forwarding CUST\n ip address {cust} 255.255.255.255\n!\n"
    body += f"router bgp 65000\n bgp router-id {pe_lo}\n bgp log-neighbor-changes\n"
    for peer in peer_los:
        body += f" neighbor {peer} remote-as 65000\n neighbor {peer} update-source Loopback0\n"
    body += " address-family vpnv4\n"
    for peer in peer_los:
        body += f"  neighbor {peer} activate\n  neighbor {peer} send-community extended\n"
    body += (" exit-address-family\n"
             " address-family ipv4 vrf CUST\n  redistribute connected\n exit-address-family\n!\n")
    return body


def _ifaces6_ios(r, if6, lo, extra=lambda ifc: "") -> str:
    out = f"interface Loopback0\n ipv6 address {lo[r]}/128\n{extra('lo')}!\n"
    for ifc in sorted(if6[r]):
        ip, net, peer_ip, peer = if6[r][ifc]
        out += f"interface {ifc}\n description to {peer}\n no shutdown\n ipv6 address {ip}/64\n{extra(ifc)}!\n"
    return out


def _ios_ospf6(r, if6, lo, routers, idx):
    body = _head_ios(r) + "ipv6 unicast-routing\n!\n"
    body += _ifaces6_ios(r, if6, lo, extra=lambda ifc: " ipv6 ospf 1 area 0\n")
    body += f"ipv6 router ospf 1\n router-id 0.0.0.{idx + 1}\n!\n"
    return body


def _ios_bgp6(r, if6, lo, routers, idx):
    asn = 65001 + idx
    body = _head_ios(r) + "ipv6 unicast-routing\n!\n" + _ifaces6_ios(r, if6, lo)
    body += f"router bgp {asn}\n bgp router-id 0.0.0.{idx + 1}\n bgp log-neighbor-changes\n no bgp default ipv4-unicast\n"
    for ifc in sorted(if6[r]):
        ip, net, peer_ip, peer = if6[r][ifc]
        body += f" neighbor {peer_ip} remote-as {65001 + routers.index(peer)}\n"
    body += f" address-family ipv6 unicast\n  network {lo[r]}/128\n"
    for ifc in sorted(if6[r]):
        body += f"  neighbor {if6[r][ifc][2]} activate\n"
    body += " exit-address-family\n!\n"
    return body


def _ios_vrrp_gw(name, iface, lan_ip, vip, idx):
    body = _head_ios(name)
    body += f"interface Loopback0\n ip address 10.255.0.{idx + 1} 255.255.255.255\n!\n"
    body += (f"interface {iface}\n ip address {lan_ip} 255.255.255.0\n no shutdown\n"
             f" vrrp 1 ip {vip}\n vrrp 1 priority {110 if idx == 0 else 100}\n!\n")
    return body


IOS_FN = {"static": _ios_static, "rip": _ios_rip, "ospf": _ios_ospf, "isis": _ios_isis,
          "eigrp": _ios_eigrp, "bgp-ebgp": _ios_ebgp, "bgp-ibgp": _ios_ibgp,
          "mpls-ldp": _ios_mpls_ldp, "redistribute": _ios_redistribute, "bfd": _ios_bfd,
          "route-filter": _ios_route_filter, "bgp-rr": _ios_bgp_rr, "multicast": _ios_pim,
          "pbr": _ios_pbr, "bgp-security": _ios_bgp_security, "bgp-communities": _ios_bgp_comm,
          "ospf-auth": _ios_ospf_auth, "bgp-hijack": _ios_bgp_hijack}


def _label_links(res: dict, links: list[dict]) -> None:
    """Show each point-to-point link's subnet on the canvas (helps configure and read the lab)."""
    for k, ln in enumerate(res["links"]):
        if k < len(links):
            ln["label"] = _p2p(k).with_prefixlen


# per-topic hints for the lab guide: (configuration steps, verification commands)
STEPS = {
    "static": (["On each router add a static route to the remote loopbacks with «ip route <ip>/32 <next-hop>»."],
               ["«show ip route» on each router", "ping between the loopbacks"]),
    "rip": (["Enable RIP: «router rip», «version 2», «network <network>» for every connected network."],
            ["«show ip rip», «show ip route rip»", "ping between the loopbacks"]),
    "ospf": (["Enable OSPF area 0: «router ospf», then «network <network> area 0» for every link and for the loopback."],
             ["«show ip ospf neighbor» (Full adjacencies)", "«show ip route ospf»", "ping between the loopbacks"]),
    "ospf-multiarea": (["Backbone in area 0; the peripheral networks in different areas, with the border routers (ABR) in both areas."],
                       ["«show ip ospf neighbor», «show ip ospf database»", "ping between the loopbacks"]),
    "isis": (["Enable IS-IS: «router isis 1», «net 49.0001.<sys>.00», and «ip router isis 1» on every interface and on the loopback."],
             ["«show isis neighbor», «show ip route isis»", "ping between the loopbacks"]),
    "eigrp": (["Enable EIGRP AS 1: «router eigrp 1», then «network <network>» for every network."],
              ["«show ip eigrp neighbors», «show ip route eigrp»", "ping between the loopbacks"]),
    "bgp-ebgp": (["Each router is a different AS. Configure «router bgp <as>», one «neighbor <ip> remote-as <neighbor-as>» per link, and announce the loopback."],
                 ["«show ip bgp summary» (Established sessions)", "«show ip bgp»", "ping between the loopbacks"]),
    "bgp-ibgp": (["A single AS 65000. First OSPF as the underlay (loopback reachability), then iBGP full-mesh: «neighbor <loopback> remote-as 65000» and «update-source lo»."],
                 ["first «show ip ospf neighbor», then «show ip bgp summary»", "ping between the loopbacks"]),
    "mpls-ldp": (["Enable OSPF, then MPLS LDP: «mpls ldp», «address-family ipv4», and «interface <ifc>» for every core link."],
                 ["«show mpls ldp neighbor», «show mpls table»", "traceroute between the loopbacks (see the MPLS labels)"]),
    "redistribute": (["Enable OSPF, add a static route (e.g. toward a LAN) and redistribute: «redistribute connected» and «redistribute static» inside «router ospf»."],
                     ["«show ip route ospf» (external O E2 routes)", "ping between the loopbacks and the LANs"]),
    "ospf-auth": (["Enable OSPF area 0.", "Add MD5 authentication: «ip ospf authentication message-digest» and «ip ospf message-digest-key 1 md5 <key>» on every interface; in «router ospf» add «area 0 authentication message-digest»."],
                  ["«show ip ospf neighbor» (Full adjacencies only if the keys match)", "«show ip ospf interface»"]),
    "bfd": (["Enable OSPF.", "Turn on BFD: a global «bfd» block and «ip ospf bfd» on every interface. Try unplugging a link and watch how fast the failover is."],
            ["«show bfd peers» (Up state)", "«show ip ospf neighbor»"]),
    "bgp-security": (["Configure eBGP (one AS per router).", "Protect the sessions: an «ip prefix-list» that accepts only the /32 loopbacks and apply it «prefix-list … in» on every neighbor; add «maximum-prefix»."],
                     ["«show ip bgp summary» and «show ip bgp neighbor <ip> filtered-routes»", "try announcing an unexpected network: it must be discarded"]),
    "bgp-communities": (["Configure eBGP.", "Tag the announcements with a «route-map» that does «set community <as>:100» and enable «send-community» toward the neighbors; then filter on the community."],
                        ["«show ip bgp <network>» (Community line)", "«show ip bgp community <as>:100»"]),
    "route-filter": (["Enable OSPF.", "Create an «access-list FILTER» that denies the target loopback and permits the rest; apply it with «distribute-list FILTER in» inside «router ospf». Check that the route disappears from the tables."],
                     ["«show ip route ospf» (the filtered route is gone)", "«show access-list»"]),
    "bgp-rr": (["Enable OSPF as the underlay.", "Pick a router as the route reflector: it peers iBGP with everyone and marks them «route-reflector-client»; the others peer only with the RR. No full-mesh."],
               ["«show ip bgp summary» on the RR (all Established)", "«show ip bgp» on the clients (they see the reflected routes)"]),
    "vrrp": (["Put the two gateways on the same LAN (e.g. 192.168.1.0/24) with IPs .2 and .3.", "On both, on the LAN interface: «vrrp 1 ip 192.168.1.1», higher priority on the master, «no vrrp 1 shutdown». The hosts use .1 as the gateway."],
             ["«show vrrp» (one Master, one Backup)", "shut down the master and check that the backup takes over the VIP"]),
    "mpls-l3vpn": (["On the core: OSPF + LDP between PE1-P-PE2 (loopbacks reachable with labels).", "On the PEs: a «CUST» VRF, MP-BGP «address-family ipv4 vpn» between the PE loopbacks, and inside the VRF rd/rt + «redistribute connected»."],
                   ["«show mpls ldp neighbor», «show bgp ipv4 vpn summary»", "«show ip route vrf CUST» on the PEs (the customer routes via MPLS)"]),
    "ipv6-ospf": (["Put the IPv6 addresses on the interfaces (already done).", "Enable OSPFv3: «ipv6 ospf6 area 0» on every interface and on the loopback, then «router ospf6» with «ospf6 router-id»."],
                  ["«show ipv6 ospf6 neighbor» (Full)", "«show ipv6 route ospf6»", "ping6 between the loopbacks"]),
    "ipv6-bgp": (["Configure IPv6 eBGP: «router bgp <as>», «neighbor <ipv6> remote-as <as>» per link, then «address-family ipv6 unicast» with «neighbor <ipv6> activate» and «network <loopback>/128»."],
                 ["«show bgp ipv6 unicast summary»", "«show bgp ipv6 unicast»", "ping6 between the loopbacks"]),
    "pbr": (["Enable OSPF (basic reachability).", "Create a «nexthop-group» with the desired next-hop, a «pbr-map» that does «match src-ip <network>» and «set nexthop-group», and apply it to the interface with «pbr-policy»."],
            ["«show pbr map», «show pbr nexthop-groups»", "send traffic from the chosen source and check the path with traceroute"]),
    "multicast": (["Enable OSPF as the underlay.", "Turn on PIM: «ip pim» on every interface and define the RP with «ip pim rp <RP-loopback> 224.0.0.0/4». Have a receiver join a group and transmit from the source."],
                  ["«show ip pim neighbor», «show ip pim rp-info»", "«show ip mroute» while the multicast traffic flows"]),
    "bgp-hijack": (["Configure eBGP; r1 (AS 65001) announces 203.0.113.0/24.", "ATTACK: on another router, announce 203.0.113.0/24 too (or the more specific 203.0.113.0/25) and watch how the traffic gets hijacked.", "DEFENSE: on the neighbors apply an AS-path/prefix-list filter that accepts that prefix only from the legitimate origin (route-map ANTIHIJACK in)."],
                   ["«show ip bgp 203.0.113.0/24» (which AS-path wins)", "after the defense: the fake announcement is discarded"]),
    "vxlan-evpn": (["Underlay: OSPF on spines and leaves to reach the loopbacks.",
                    "Overlay: «router bgp 65000», «address-family l2vpn evpn»; the leaves peer with the spines (route-reflector). On the leaves add «advertise-all-vni» and the VNI."],
                   ["«show bgp l2vpn evpn summary»", "«show evpn vni»"]),
    "ospf-cost": (["Enable OSPF area 0 on all the routers: between the two ends (A and B) there are two different paths.",
                   "On one exit of the first router (and on the other end of the same link) raise the cost: «interface <ifc>» → «ip ospf cost 100». Traffic toward the far end then takes the second path."],
                  ["«show ip ospf interface <ifc>» (Cost 100 on the modified interface)",
                   "«show ip route ospf»: the next-hop toward the far-end loopback has changed",
                   "«traceroute <loopback>» shows the alternative path"]),
    "ospf-default": (["Enable OSPF area 0 on all the routers.",
                      "On the first router create a default toward Null0 («ip route 0.0.0.0/0 Null0») and in «router ospf» add «default-information originate»."],
                     ["on the other routers «show ip route» shows «O*E2 0.0.0.0/0» (learned gateway of last resort)",
                      "«show ip ospf database external» (type 5 LSA for the default)"]),
    "bgp-localpref": (["Configure eBGP (one AS per router): between the two ends there are two paths, so two ways to the same destination.",
                       "Create «route-map PREFER permit 10» with «set local-preference 200» and apply it inbound («neighbor <ip> route-map PREFER in») on ONE of the two neighbors: those routes become preferred."],
                      ["«show ip bgp» (LocPrf = 200 on the preferred routes; the «>» marks the best-path)",
                       "«show ip bgp <remote-loopback>»: compare the two paths and see which wins by local-preference"]),
    "bgp-aggregate": (["Configure eBGP (one AS per router).",
                       "On the first router, in «router bgp», add «aggregate-address 10.255.0.0/16 summary-only»: it announces the summary block and hides the individual /32s."],
                      ["on the neighbors «show ip bgp» shows 10.255.0.0/16 and NOT the suppressed /32s",
                       "on the aggregating router «show ip bgp 10.255.0.0/16» (the «Aggregated by» line)"]),
}

# per-topic troubleshooting hints added to every guide (plus the generic ones below)
TROUBLE = {
    "ospf": ["Adjacency stuck in ExStart/Exchange? Usually an MTU mismatch between the interfaces.",
             "No neighbor? Check that the interfaces are on the same subnet and that they aren't «passive» by mistake."],
    "ospf-multiarea": ["An ABR must be in area 0 AND in the peripheral area: check the «network ... area N» statements."],
    "ospf-auth": ["Adjacency won't come up: the MD5 keys must match on both sides, same key-id."],
    "ospf-cost": ["The path doesn't change? The cost must be raised on BOTH ends of the link to affect both directions."],
    "ospf-default": ["The others don't see the default? There must be a default route present on the router that originates it (here toward Null0)."],
    "isis": ["No adjacency: the NET must have the same area and the level type (level-2) must match."],
    "eigrp": ["Neighbors coming and going: different AS or misaligned K-values."],
    "bgp-ebgp": ["Session Idle/Active? The neighbor's IP and the «remote-as» must match and be reachable.",
                 "Routes not getting through? On FRR you need «no bgp ebgp-requires-policy» or an in/out route-map."],
    "bgp-ibgp": ["iBGP session down: the loopbacks used as neighbors must be reachable via the IGP and you need «update-source lo».",
                 "iBGP routes not installed: remember the next-hop rule (often «next-hop-self» on the edges)."],
    "bgp-localpref": ["Local-preference matters only INSIDE the AS and beats almost everything: if the best-path doesn't change, check where you applied the route-map (inbound)."],
    "bgp-aggregate": ["No aggregate: there must be at least one more specific route in the BGP table for the aggregate to be generated."],
    "mpls-ldp": ["No labels? Check «mpls» is enabled on the core interfaces and that LDP sees the neighbors on the loopbacks."],
    "mpls-l3vpn": ["Customer routes missing on the PEs: check the VRF rd/rt and that MP-BGP VPNv4 is «activate» between the PE loopbacks."],
    "vrrp": ["Two masters at once? The hosts see flapping: use different priorities and the same VRID/same VIP on the two routers."],
}
# always shown, whatever the topic
TROUBLE_GENERIC = [
    "Interface up/down: «show interface brief» (or «show ip interface brief») — it must be up/up and with the right IP.",
    "Duplicate or missing router-ids break OSPF/BGP: here each router uses its loopback as a unique router-id.",
    "After the changes save with «do write» (or NetSO's «Save config» button) so you don't lose them on reboot.",
]

# Explanation of the concept (what/why/how): it's the heart of a real guide, shown before the steps.
CONCEPT = {
    "static": "Static routing is the simplest form of routing: you write by hand, on each router, how to "
              "reach every remote network by specifying the *next-hop*, i.e. the neighbour to forward toward. There is "
              "no protocol exchanging information, so it is lightweight and predictable but does not adapt on its own: "
              "if a link goes down, the routes stay wrong until you fix them yourself. In this lab each router must "
              "know the loopbacks of all the others by adding one /32 route for each, with the next-hop being the "
              "neighbour on the shortest path.",
    "rip": "RIP (Routing Information Protocol) is a classic *distance-vector* protocol: each router announces to its "
           "neighbours the networks it knows with the distance in «hops» (number of hops) and picks the path with the "
           "fewest hops, up to a maximum of 15. It is very simple but converges slowly and ignores bandwidth. "
           "Here you use RIP v2, which also carries the subnet mask (classless), so all the routers learn the connected networks.",
    "ospf": "OSPF (Open Shortest Path First) is a *link-state* protocol: each router describes its own "
            "links, floods this information to everyone, and each one builds a complete map of the network, "
            "from which it computes the shortest path (the SPF algorithm) based on *cost*, tied to bandwidth. It converges "
            "quickly and is the standard among enterprise IGPs. In this single area (area 0) all the routers are "
            "peers and share the same database.",
    "ospf-multiarea": "When the network grows, a single OSPF area becomes heavy: the solution is to split it into areas. "
                      "Area 0 is the backbone and every other area must attach to it through the border "
                      "routers (ABR, Area Border Router), which sit in both areas and summarize the routes "
                      "from one to the other. This way each area has a smaller database and a local failure doesn't force "
                      "the whole network to recompute.",
    "isis": "IS-IS is a *link-state* protocol like OSPF, much loved by large providers. Instead of IP "
            "addresses it uses NSAP addresses (the «NET») to identify routers and works directly at layer 2. Each "
            "router operates at a level (L1 within the area, L2 between areas); here we use level-2 so all "
            "the routers talk to each other.",
    "eigrp": "EIGRP (originally Cisco, here through FRR) is an advanced *distance-vector*: it measures distance with a "
             "composite metric (bandwidth and delay) and keeps a backup path ready (*feasible successor*) to "
             "converge almost instantly. The routers in the same «AS» (here AS 1) become neighbours and "
             "exchange routes. It is efficient and quick to configure.",
    "redistribute": "Every routing protocol lives in its own world: static routes, connected networks and the various IGPs don't "
                    "talk to each other by themselves. *Redistribution* acts as the bridge, injecting the routes of one source into "
                    "another protocol. Here OSPF redistributes the connected networks and a static route, so they appear "
                    "on the other routers as external routes (type O E2).",
    "ospf-auth": "Without protection, anyone on the same network could inject fake OSPF information and hijack "
                 "the traffic. Authentication ensures that two routers form the adjacency only if they share the same "
                 "key. Here you use MD5 (message-digest) authentication: every packet carries a hash computed with the "
                 "key, and a router with the wrong key is simply ignored.",
    "bfd": "When a link goes down, OSPF only notices when its timers expire, i.e. after seconds. BFD "
           "(Bidirectional Forwarding Detection) is a very lightweight mechanism that sends very frequent packets between "
           "two neighbours and detects the failure in milliseconds, then immediately tells OSPF to recompute. The result is an "
           "almost instant failover. In this lab you pair BFD with OSPF and unplug a link to see the difference.",
    "ospf-cost": "In OSPF the path with the lowest total *cost* is chosen, and an interface's cost is "
                 "inversely proportional to bandwidth. By raising a link's cost by hand you make it «less "
                 "attractive», so SPF prefers the other path: it is the clean way to do *traffic engineering* "
                 "in OSPF. Here there are two paths between the two ends; you raise the cost on one link and check that the "
                 "traffic goes over the other.",
    "ospf-default": "Instead of configuring a route for every external destination, it's better to have a single default "
                    "route (0.0.0.0/0) toward the exit. With «default-information originate» a router injects this "
                    "default into OSPF, and all the others learn it automatically as the «gateway of last "
                    "resort». Here one router originates the default and you check that it appears (O*E2) on the others.",
    "bgp-ebgp": "BGP is the protocol that holds the Internet together: it connects different autonomous systems (AS), "
                "i.e. networks run by different organizations. *eBGP* is the session between two neighbouring ASes: each router "
                "announces to its neighbour the prefixes it wants to make reachable, with the relevant AS-path. Here each router is "
                "an AS of its own: you configure an eBGP session on each link and announce the loopback.",
    "bgp-ibgp": "Within a single AS, the edge routers that speak eBGP must also exchange routes among themselves: "
                "this is *iBGP*. Unlike eBGP, iBGP does not modify the AS-path, so as a rule a full "
                "mesh is needed between all the iBGP routers. Here you first build an OSPF underlay to reach the "
                "loopbacks, then establish iBGP on the loopbacks (with update-source) so the session stays up even if "
                "a link goes down.",
    "bgp-security": "Accepting any announcement from a BGP neighbour is dangerous: a mistake or an attack can hijack "
                    "traffic. Here you add two basic defenses: an inbound *prefix-list* that lets through only the "
                    "legitimate prefixes (the /32 loopbacks) and discards the rest, and a *maximum-prefix* that closes the "
                    "session if the neighbour announces too many routes (a sign of a leak). It is the first step in hardening "
                    "an edge router.",
    "route-filter": "Sometimes you want a certain network not to be learned or propagated, for security or by design "
                    "choice. A routing filter does exactly this: an access-list identifies the prefixes and a "
                    "*distribute-list* applied to the protocol decides what to accept or announce. Here, with OSPF "
                    "running, you filter a specific loopback and check that it disappears from the other routers' tables.",
    "bgp-communities": "*BGP communities* are tags (in the format AS:value) that you attach to announcements to "
                       "group them and treat them consistently along the path. They don't change routing on their own, "
                       "but they let you apply clean policies («I prefer these», «I don't export these»). "
                       "Here you tag the announcements with a route-map and then select/filter them based on the community.",
    "bgp-rr": "The iBGP full-mesh doesn't scale: with N routers you need N·(N-1)/2 sessions. The *route reflector* solves the "
              "problem by acting as a hub: a central router reflects the iBGP announcements received from a client to the "
              "others, so each client peers only with the reflector. Here you pick an RR, mark the others as clients and "
              "check that the routes propagate without a full mesh.",
    "bgp-localpref": "When there are several exits for the same destination, *local-preference* decides which one to "
                     "prefer: it is among the first attributes BGP looks at and applies only within the AS. Higher = "
                     "preferred. Here there are two paths toward the far end; with an inbound route-map you raise the "
                     "local-preference on one of the neighbours to funnel the outbound traffic over that path.",
    "bgp-aggregate": "Announcing many specific routes weighs down everyone's tables. *Aggregation* "
                     "(summarization) condenses them into a single broader prefix. With «aggregate-address … "
                     "summary-only» the router announces only the summary block and suppresses the individual components, "
                     "shrinking the tables and hiding the internal detail. Here you aggregate the /32s into a /16.",
    "vrrp": "If the hosts have a single router as their gateway and it goes down, they are cut off. VRRP (Virtual Router "
            "Redundancy Protocol) lets two or more routers share a *virtual IP*: the hosts use that as their "
            "gateway, while among the routers one acts as master and the other as backup. If the master fails, the backup takes "
            "over the virtual IP automatically and the hosts never notice. Here two gateways share the VIP and you test "
            "the failover by shutting down the master.",
    "mpls-ldp": "MPLS forwards packets based on *labels* instead of the destination IP, making "
                "the core fast and independent of individual prefixes. LDP (Label Distribution Protocol) automatically "
                "distributes these labels among the core routers, relying on the IGP (here OSPF) to know "
                "where the loopbacks are. Here you build an MPLS core and check that the traffic between the loopbacks "
                "travels labeled.",
    "mpls-l3vpn": "MPLS L3VPN lets a provider carry the networks of several customers over the same core, "
                  "keeping them separate. Each customer lives in a *VRF* (an isolated routing table) on the edge "
                  "routers (PE), and the customer routes travel across the core inside MP-BGP VPNv4, labeled by MPLS; the "
                  "inner router (P) only does transit and knows nothing about the customers. Here you configure the «CUST» VRF on the PEs and "
                  "MP-BGP between their loopbacks.",
    "vxlan-evpn": "In spine-leaf data centers you want to extend L2 networks over a scalable L3 transport: *VXLAN* "
                  "encapsulates the Ethernet frames in UDP and identifies them with a VNI, while *BGP EVPN* acts as the control "
                  "plane, distributing MACs and routes among the leaves. The spines act as the backbone and as route-reflectors. "
                  "Here you build an OSPF underlay and a BGP EVPN overlay with «advertise-all-vni».",
    "ipv6-ospf": "OSPFv3 is the IPv6 version of OSPF: same link-state logic and same SPF algorithm, but it runs "
                 "over IPv6 and is enabled per interface. The router-id stays a 32-bit number (in IPv4 format) "
                 "even though the network is IPv6. Here, with the 2001:db8::/32 addresses already in place, you enable OSPFv3 on every "
                 "interface and verify reachability between the loopbacks with ping6.",
    "ipv6-bgp": "BGP also carries IPv6 prefixes using *address-families*: the session can be built over an "
                "IPv6 address and, inside «address-family ipv6 unicast», the neighbours are activated and the "
                "/128 loopback prefixes are announced. The logic (AS, AS-path, best-path) is identical to IPv4. Here you establish "
                "IPv6 eBGP between the routers and check the table with «show bgp ipv6 unicast».",
    "pbr": "Normally a router decides where to send a packet based only on the destination. Policy-Based "
           "Routing (PBR) breaks this rule: you can route based on the *source*, the port or other "
           "criteria, forcing a next-hop different from the one in the routing table. Here you define a pbr-map that "
           "intercepts the traffic of a certain source and sends it to a chosen next-hop, applying it "
           "to the ingress interface.",
    "multicast": "Multicast delivers the same stream to many receivers without duplicating it for each one, using "
                 "group addresses (224.0.0.0/4). PIM-SM (Sparse Mode) builds the distribution trees "
                 "starting from a meeting point, the *Rendezvous Point* (RP): the sources register with the RP and the "
                 "receivers reach it, then the tree is optimized. Here, with OSPF as the underlay, you enable PIM, "
                 "define the RP and watch the mroute table while the traffic flows.",
    "bgp-hijack": "A *BGP hijack* happens when an AS announces a prefix that isn't its own (or a more specific one): "
                  "because BGP prefers the more specific prefix and trusts announcements, someone else's traffic gets "
                  "hijacked toward the attacker. It is one of the most serious incidents on the Internet. Here, in a sandbox, you first "
                  "cause the hijack, then defend yourself with a filter (prefix-list / AS-path) that accepts that "
                  "prefix only from the legitimate origin.",
    "vlan": "A VLAN logically splits a physical network into several distinct networks: two hosts on the same VLAN talk "
            "as if they were on a dedicated switch, two hosts on different VLANs don't, even if connected to the same "
            "switch. You assign a VLAN to each port (*access* mode) and use *trunks* to carry several "
            "VLANs over a single cable. To make different VLANs communicate you need a router or an L3 switch (inter-VLAN routing).",
    "mitm-arp": "On Ethernet LANs hosts use ARP to discover the MAC associated with an IP and trust the "
                "replies blindly. In an *ARP spoofing* attack the attacker sends fake ARP replies associating its "
                "own MAC with the gateway's IP (and vice versa): from that moment the traffic between victim and gateway "
                "passes through it, which can read or alter it (*man-in-the-middle*). The defense is to not trust "
                "dynamic ARP: static entries, Dynamic ARP Inspection, DHCP snooping and encrypted traffic.",
    "recon-scan": "Reconnaissance is the first phase of an attack — and of a security test: before striking you "
                  "map the network to know which hosts are alive and which services they expose. Tools like nmap do "
                  "a *ping sweep* to find the hosts and then a *port scan* to identify services and "
                  "versions, from which known vulnerabilities are looked up. Defending means reducing the exposed surface "
                  "(closing services, updating), filtering with the firewall and making the scan noisy and monitored.",
}


def _guide(topic: str, meta: dict, routers: list[str], ifips: dict, lo: dict, solution: dict,
           pattern: str = "", intro: str = "", router_label: str = "FRR") -> str:
    steps, verify = STEPS.get(topic, ([], []))
    out = [f"# Lab: {meta['label']}", "", "## Objective",
           f"{meta['desc']} {intro}".strip(),
           f"Configure **{meta['label']}** on the {len(routers)} {router_label} routers ({pattern or 'realistic topology'}) "
           "and verify reachability between the loopbacks."]
    if CONCEPT.get(topic):
        out += ["", "## How it works", CONCEPT[topic]]
    out += ["", "## IP addresses (already set at boot)",
            "The IPs are already configured: every link is a point-to-point /30 and every router has a /32 loopback "
            "(its router-id). You only need to get the protocol running; this table says who is next to whom "
            "and which next-hops to use.", "",
            "| Router | Loopback | Interface | IP / prefix | Toward | Neighbor IP |",
            "|---|---|---|---|---|---|"]
    for r in routers:
        ifs = sorted(ifips.get(r, {}))
        if not ifs:
            out.append(f"| **{r}** | {lo[r]}/32 | — | — | — | — |")
            continue
        for j, ifc in enumerate(ifs):
            ip, pfx, _net, peer_ip, peer = ifips[r][ifc]
            out.append(f"| {f'**{r}**' if j == 0 else ''} | {f'{lo[r]}/32' if j == 0 else ''} | `{ifc}` | {ip}/{pfx} | **{peer}** | {peer_ip} |")
    out += ["", "## Steps",
            f"Open the console of each {router_label} router and enter configuration mode (on FRR `vtysh` then "
            "`configure terminal`, on IOS `configure terminal`). Run these steps on **every** router, "
            "adapting interfaces and addresses to the table above:"]
    out += [f"{i + 1}. {s}" for i, s in enumerate(steps)] or ["1. Configure the required protocol."]
    out += [f"{len(steps) + 1}. Save the configuration (`write memory` on IOS, `write` on FRR) so it survives a reboot."]
    out += ["", "## Verification",
            "After configuring all the routers, check that the protocol is working and that there is end-to-end "
            "connectivity:"]
    out += [f"- {v}" for v in verify] or ["- Check the routing tables and ping."]
    out += ["", "## Troubleshooting"]
    out += [f"- {t}" for t in TROUBLE.get(topic, [])]
    out += [f"- {t}" for t in TROUBLE_GENERIC]
    out += ["", "## Solution",
            "The full configuration is also in the «solution» config set (Config menu in the lab). Below, per node:"]
    for r in routers:
        if solution.get(r):
            out.append(f"\n### {r}\n```\n{solution[r].strip()}\n```")
    return "\n".join(out)


def _finish(hint: str, desc: str, res: dict, base: dict, solution: dict, instr: str) -> dict:
    for nd in res["nodes"]:
        if nd["name"] in base:
            nd["startup_config"] = True
    return {"name_hint": hint, "description": desc, "nodes": res["nodes"], "links": res["links"],
            "configs": base, "solution": solution, "instructions": instr}


def build(topic: str, templates: dict[str, Template], seed: int | None = None,
          count: int | None = None, router: str | None = None) -> dict:
    if topic not in TOPIC_IDS:
        raise GenerateError(f"Unknown topic '{topic}'")
    rng = random.Random(seed)
    meta = next(t for t in TOPICS if t["id"] == topic)

    # which router to build with, and in which config dialect
    router_tpl = router if (router and router in templates) else _default_router(templates)
    dia = dialect_of(templates[router_tpl]) if router_tpl in templates else None
    rlabel = (templates[router_tpl].title or router_tpl) if router_tpl in templates else router_tpl
    if topic not in HOST_TOPICS:
        if dia is None:
            raise GenerateError("No suitable router installed: FRR or a Cisco IOS router "
                                "(IOL, c7200/c3725, vIOS/IOSv, CSR) is required.")
        if dia != "frr" and topic not in IOS_TOPICS:
            raise GenerateError(
                f"The «{meta['label']}» scenario can currently only be generated with FRR "
                "(the feature is not reliably available on Cisco IOL/IOSv). "
                "Choose FRR as the router for this topic.")

    # ---- VLAN: switch + hosts (configured by hand on the switch)
    if topic == "vlan":
        if HOST not in templates or SWITCH not in templates:
            raise GenerateError("The 'vpc' and/or 'bridge' devices are missing for the VLAN scenario")
        n = _clamp(count, rng.randint(3, 6), 2, 20)
        b = _Builder(Lab(name="gen"), templates)
        sw = b.add("sw", SWITCH, 1, [(420, 180)])[0]
        hosts = b.add("pc", HOST, n, _circle(n, 420, 360, max(180, n * 45)))
        for h in hosts:
            b.link(sw, h)
        res = b.result()
        for ln in res["links"]:
            ln["label"] = "192.168.10.0/24"
        desc = f"VLAN playground: one switch with {n} hosts on the 192.168.10.0/24 network."
        instr = ("# Lab: L2 VLAN\n\n## Objective\nA switch with hosts on the 192.168.10.0/24 network. "
                 "Create the per-port VLANs on the switch and check which hosts talk to each other.\n\n"
                 "## How it works\n" + CONCEPT["vlan"] + "\n\n"
                 "## Steps\n1. Assign the hosts IPs in 192.168.10.0/24 (e.g. .11, .12, …).\n"
                 "2. On the switch (the «bridge» node) set the per-port VLANs (access/trunk).\n"
                 "3. Put some hosts in different VLANs.\n\n## Verification\n- ping between hosts on the same VLAN (ok) and on different VLANs (ko).\n\n"
                 "## Solution\nHosts on the same VLAN reach each other; those on different VLANs don't, "
                 "until you add a router/SVI between the VLANs.")
        return {"name_hint": "vlan-l2", "description": desc, "nodes": res["nodes"], "links": res["links"],
                "configs": {}, "solution": {}, "instructions": instr}

    # ---- EVPN/VXLAN spine-leaf
    if topic == "vxlan-evpn":
        scn = _Scn(templates)
        nspine, nleaf = 2, _clamp(count, rng.randint(2, 4), 2, 10)
        gap = 200
        spines = scn.add("spine", ROUTER, nspine, _row(nspine, 120, 420, gap * 1.6))
        leaves = scn.add("leaf", ROUTER, nleaf, _row(nleaf, 360, 420, gap))
        for sp in spines:
            for le in leaves:
                scn.link(sp, le)
        res = scn.result()
        routers = spines + leaves
        lo, ifips = _address(routers, scn.links)
        vni = 10 + rng.randint(0, 90)
        solution = {}
        for sp in spines:
            solution[sp] = _cfg_evpn_spine(sp, ifips, lo, routers, scn.links, routers.index(sp), leaves)
        for le in leaves:
            solution[le] = _cfg_evpn_leaf(le, ifips, lo, routers, scn.links, routers.index(le), spines, vni)
        _label_links(res, scn.links)
        desc = f"VXLAN/EVPN spine-leaf: {nspine} spines, {nleaf} leaves. OSPF underlay, BGP EVPN overlay, VNI {vni}."
        instr = _guide(topic, meta, routers, ifips, lo, solution, intro=f"VNI {vni}.")
        return _finish("vxlan-evpn", desc, res, _base_configs(routers, ifips, lo), solution, instr)

    # ---- VRRP: a LAN (bridge) with two gateway routers sharing a virtual IP, plus hosts
    if topic == "vrrp":
        if HOST not in templates or SWITCH not in templates:
            raise GenerateError("The 'vpc' and/or 'bridge' devices are missing for the VRRP scenario")
        nhosts = _clamp(count, 3, 1, 10)
        b = _Builder(Lab(name="gen"), templates)
        sw = b.add("sw", SWITCH, 1, [(420, 120)])[0]
        gws = b.add("gw", router_tpl, 2, [(300, 300), (540, 300)])
        hosts = b.add("pc", HOST, nhosts, _row(nhosts, 460, 420, 120))
        for g in gws:
            b.link(sw, g)
        for h in hosts:
            b.link(sw, h)
        res = b.result()
        for ln in res["links"]:
            ln["label"] = "192.168.1.0/24"
        vip = "192.168.1.1"
        gw_iface = {}   # the LAN interface each gateway actually got (eth1 on FRR, Gi0/0… on IOS)
        for ln in res["links"]:
            for ep in ln["endpoints"]:
                node, ifc = ep.split(":", 1)
                if node in gws:
                    gw_iface[node] = ifc
        if dia == "ios":
            base = {g: _head_ios(g) + f"interface Loopback0\n ip address 10.255.0.{i + 1} 255.255.255.255\n!\n"
                       f"interface {gw_iface[g]}\n ip address 192.168.1.{2 + i} 255.255.255.0\n no shutdown\n!\n"
                    for i, g in enumerate(gws)}
            solution = {g: _ios_vrrp_gw(g, gw_iface[g], f"192.168.1.{2 + i}", vip, i) for i, g in enumerate(gws)}
        else:
            base = {g: _head(g) + f"interface lo\n ip address 10.255.0.{i + 1}/32\n!\ninterface {gw_iface[g]}\n ip address 192.168.1.{2 + i}/24\n!\n"
                    for i, g in enumerate(gws)}
            solution = {g: _cfg_vrrp_gw(g, gw_iface[g], f"192.168.1.{2 + i}", vip, i) for i, g in enumerate(gws)}
        desc = f"VRRP: 2 gateways ({', '.join(gws)}) with virtual IP {vip} for {nhosts} hosts on the 192.168.1.0/24 LAN."
        steps, verify = STEPS["vrrp"]
        instr = ("# Lab: VRRP (redundant gateway)\n\n## Objective\n"
                 f"Two routers ({gws[0]}, {gws[1]}) share the virtual gateway **{vip}** for the hosts on the 192.168.1.0/24 LAN.\n\n"
                 "## How it works\n" + CONCEPT["vrrp"] + "\n\n"
                 "## Addresses\n"
                 f"- **{gws[0]}** {gw_iface[gws[0]]} 192.168.1.2/24 (master)\n- **{gws[1]}** {gw_iface[gws[1]]} 192.168.1.3/24 (backup)\n"
                 f"- Virtual IP (the hosts' gateway): **{vip}**\n- Hosts: 192.168.1.11, .12, … with gateway {vip}\n\n"
                 "## Steps\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) +
                 "\n\n## Verification\n" + "\n".join(f"- {v}" for v in verify) +
                 "\n\n## Solution\n" + "".join(f"\n### {g}\n```\n{solution[g].strip()}\n```\n" for g in gws))
        return _finish("vrrp", desc, res, base, solution, instr)

    # ---- MPLS L3VPN: a P core (OSPF+LDP) with PE edges (VRF CUST + MP-BGP VPNv4 full mesh)
    if topic == "mpls-l3vpn":
        n = _clamp(count, rng.randint(5, 8), 3, 20)
        ncore = max(1, round(n / 3))          # ~1/3 transit P routers, the rest PE
        npe = max(2, n - ncore)
        ncore = max(1, n - npe)               # keep the total == n after the PE floor
        scn = _Scn(templates, router_tpl)
        core = scn.add("p", router_tpl, ncore,
                       _circle(ncore, 420, 340, 130) if ncore > 1 else [(420, 340)])
        pes = scn.add("pe", router_tpl, npe, _circle(npe, 420, 340, 320))
        # core: ring when 3+, single wire when 2 (always connected)
        if ncore >= 3:
            for i in range(ncore):
                scn.link(core[i], core[(i + 1) % ncore])
        elif ncore == 2:
            scn.link(core[0], core[1])
        # each PE hangs off one core router (round-robin) → the graph is always connected
        for j, pe in enumerate(pes):
            scn.link(pe, core[j % ncore])
        names = core + pes
        res = scn.result()
        lo, ifips = _address(names, scn.links)
        _label_links(res, scn.links)
        cfg_core = _ios_mpls_ldp if dia == "ios" else _cfg_mpls_ldp
        pe_vrf = _ios_pe_vrf if dia == "ios" else _pe_vrf
        pe_los = [lo[pe] for pe in pes]
        solution = {}
        for r in core:
            solution[r] = cfg_core(r, ifips, lo, names, scn.links, names.index(r))
        for j, pe in enumerate(pes):
            peers = [l for l in pe_los if l != lo[pe]]      # iBGP VPNv4 full mesh
            solution[pe] = cfg_core(pe, ifips, lo, names, scn.links, names.index(pe)) + pe_vrf(lo[pe], peers, j)
        base = _base_configs_ios(names, ifips, lo) if dia == "ios" else _base_configs(names, ifips, lo)
        desc = (f"MPLS L3VPN: core of {ncore} P routers (OSPF+LDP) and {npe} PEs with a CUST VRF "
                f"and MP-BGP VPNv4 in full mesh ({rlabel}).")
        instr = _guide("mpls-l3vpn", meta, names, ifips, lo, solution,
                       pattern=f"core of {ncore} P + {npe} PE",
                       intro="The P routers only do MPLS transit; the PEs hold the customer VRF and exchange "
                             "the VPNv4 routes in full mesh.", router_label=rlabel)
        return _finish("mpls-l3vpn", desc, res, base, solution, instr)

    # ---- Hacking: MITM / ARP spoofing on a shared LAN (topology + guide, host-based)
    if topic == "mitm-arp":
        if HOST not in templates or SWITCH not in templates:
            raise GenerateError("The 'vpc' and/or 'bridge' devices are missing for this lab")
        nvict = _clamp(count, 2, 2, 8)
        res, atk, _gw, vics = _hosts_lab(templates, nvict, attacker_prefix="kali")
        desc = f"MITM/ARP spoofing: 1 attacker ({atk}) and {nvict} victims on the 192.168.1.0/24 LAN."
        instr = ("# Lab: MITM / ARP spoofing (L2)\n\n> Educational sandbox lab: use it only here to learn both the attack **and** the defense.\n\n"
                 "## Objective\n"
                 f"The attacker **{atk}** and the victims ({', '.join(vics)}) are on the same 192.168.1.0/24 LAN. "
                 "Understand how an attacker intercepts the traffic with ARP spoofing and how to defend against it.\n\n"
                 "## How it works\n" + CONCEPT["mitm-arp"] + "\n\n"
                 "## Preparation\n- Assign the IPs: attacker 192.168.1.10, victims .11, .12, …; virtual gateway 192.168.1.1.\n"
                 "- For the real tools you need an attacker image (e.g. Kali) on the attacker node.\n\n"
                 "## Attack (to learn)\n1. Observe the victims' ARP table (`arp -a`).\n"
                 "2. Poison the ARP cache so the attacker gets in the middle (e.g. `arpspoof`/`ettercap` between victim and gateway).\n"
                 "3. Capture the traffic that now passes through the attacker (Wireshark in the lab).\n\n"
                 "## Defense (solution)\n- **Static** ARP entries on the critical clients.\n- On the switch: **Dynamic ARP Inspection** and **DHCP snooping**; **port-security** to limit the MACs.\n"
                 "- Segment with VLANs and use encrypted traffic (HTTPS/SSH) so the interception is useless.")
        return {"name_hint": "mitm-arp", "description": desc, "nodes": res["nodes"], "links": res["links"],
                "configs": {}, "solution": {}, "instructions": instr}

    # ---- Hacking: recon / scanning of a small server network
    if topic == "recon-scan":
        if HOST not in templates or SWITCH not in templates:
            raise GenerateError("The 'vpc' and/or 'bridge' devices are missing for this lab")
        nsrv = _clamp(count, 3, 1, 8)
        res, atk, gw, srvs = _hosts_lab(templates, nsrv, attacker_prefix="kali", gateway=True)
        desc = f"Reconnaissance: 1 attacker ({atk}), a gateway and {nsrv} servers on the 192.168.1.0/24 LAN."
        instr = ("# Lab: Reconnaissance and scanning\n\n> Educational sandbox lab: scan only the nodes of this lab.\n\n"
                 "## Objective\n"
                 f"From the attacker **{atk}**, map the 192.168.1.0/24 network (gateway **{gw}**, servers {', '.join(srvs)}), "
                 "find hosts and services, then harden.\n\n"
                 "## How it works\n" + CONCEPT["recon-scan"] + "\n\n"
                 "## Steps (to learn)\n1. Discover the active hosts: ping sweep / `nmap -sn 192.168.1.0/24`.\n"
                 "2. Scan the servers' ports and services: `nmap -sV <ip>`.\n"
                 "3. Note the exposed versions and services.\n\n"
                 "## Defense (solution)\n- Close the unnecessary services and update the versions.\n"
                 "- Put a **firewall** on the gateway (allow only the useful ports).\n"
                 "- Rate-limit/segment to make the scan noisy and slow; monitor the logs.")
        return {"name_hint": "recon-scan", "description": desc, "nodes": res["nodes"], "links": res["links"],
                "configs": {}, "solution": {}, "instructions": instr}

    # ---- IPv6 scenarios (OSPFv3 / eBGP IPv6)
    if topic in ("ipv6-ospf", "ipv6-bgp"):
        scn = _Scn(templates, router_tpl)
        n = _clamp(count, rng.randint(4, 6), 2, 20)
        routers, pattern = _varied_topology(scn, rng, n)
        res = scn.result()
        lo6, if6 = _address6(routers, scn.links)
        for k, ln in enumerate(res["links"]):
            if k < len(scn.links):
                ln["label"] = f"2001:db8:0:{k:x}::/64"
        if dia == "ios":
            cfg6 = _ios_ospf6 if topic == "ipv6-ospf" else _ios_bgp6
            base = {r: _head_ios(r) + "ipv6 unicast-routing\n!\n" + _ifaces6_ios(r, if6, lo6) for r in routers}
        else:
            cfg6 = _cfg_ospf6 if topic == "ipv6-ospf" else _cfg_bgp6
            base = {r: _head(r) + _ifaces6(r, if6, lo6) for r in routers}
        solution = {r: cfg6(r, if6, lo6, routers, i) for i, r in enumerate(routers)}
        desc = f"{meta['label']} · '{pattern}' topology with {n} {rlabel} routers. Loopbacks 2001:db8:ffff::/48, links /64."
        instr = _guide6(topic, meta, routers, if6, lo6, solution)
        return _finish(topic, desc, res, base, solution, instr)

    # ---- router scenarios (IGP / BGP / MPLS / static / RIP)
    scn = _Scn(templates, router_tpl)
    min_n = 4 if topic in ("ospf-multiarea", "ospf-cost", "bgp-localpref") else 2
    n = _clamp(count, rng.randint(4, 6), min_n, 20)
    if topic in ("ospf-cost", "bgp-localpref"):      # path-selection: two diverse paths between the ends
        pattern = "dual path"
        routers = _dualpath_topology(scn, rng, n)
    elif topic == "ospf-multiarea":                  # backbone (area 0) + peripheral areas, on a ring
        pattern = "multi-area ring"
        routers = _router_chain_or_ring(scn, rng, n, "ring")
    else:                                            # real-world shape, varied each time (always connected)
        routers, pattern = _varied_topology(scn, rng, n)
    res = scn.result()
    lo, ifips = _address(routers, scn.links)

    solution = {}
    if dia == "ios":
        if topic == "ospf-multiarea":
            region = {r: (0 if i < (n + 1) // 2 else 1) for i, r in enumerate(routers)}
            for r in routers:
                body = _head_ios(r) + _ifaces_ios(r, ifips, lo)
                body += f"router ospf 1\n router-id {lo[r]}\n passive-interface Loopback0\n network {lo[r]} 0.0.0.0 area 0\n"
                for ifc in sorted(ifips[r]):
                    net, peer = ifips[r][ifc][2], ifips[r][ifc][4]
                    area = 0 if region[r] == 0 or region[peer] == 0 else 1
                    body += f" network {net.network_address} {net.hostmask} area {area}\n"
                solution[r] = body + "!\n"
        else:
            for i, r in enumerate(routers):
                solution[r] = IOS_FN[topic](r, ifips, lo, routers, scn.links, i)
        base = _base_configs_ios(routers, ifips, lo)
    elif topic == "ospf-multiarea":
        region = {r: (0 if i < (n + 1) // 2 else 1) for i, r in enumerate(routers)}
        for r in routers:
            body = _head(r) + _ifaces(r, ifips, lo)
            body += f"router ospf\n ospf router-id {lo[r]}\n passive-interface lo\n network {lo[r]}/32 area 0\n"
            for ifc in sorted(ifips[r]):
                net, peer = ifips[r][ifc][2], ifips[r][ifc][4]
                area = 0 if region[r] == 0 or region[peer] == 0 else 1
                body += f" network {net.with_prefixlen} area {area}\n"
            solution[r] = body + "!\n"
        base = _base_configs(routers, ifips, lo)
    else:
        fn = {"static": _cfg_static, "rip": _cfg_rip, "ospf": _cfg_ospf, "isis": _cfg_isis,
              "eigrp": _cfg_eigrp, "bgp-ebgp": _cfg_ebgp, "bgp-ibgp": _cfg_ibgp, "mpls-ldp": _cfg_mpls_ldp,
              "ospf-auth": _cfg_ospf_auth, "bfd": _cfg_bfd, "redistribute": _cfg_redistribute,
              "bgp-security": _cfg_bgp_security, "bgp-communities": _cfg_bgp_comm,
              "route-filter": _cfg_route_filter, "bgp-rr": _cfg_bgp_rr, "pbr": _cfg_pbr,
              "multicast": _cfg_pim, "bgp-hijack": _cfg_bgp_hijack,
              "ospf-cost": _cfg_ospf_cost, "ospf-default": _cfg_ospf_default,
              "bgp-localpref": _cfg_bgp_localpref, "bgp-aggregate": _cfg_bgp_aggregate}[topic]
        for i, r in enumerate(routers):
            solution[r] = fn(r, ifips, lo, routers, scn.links, i)
        base = _base_configs(routers, ifips, lo)

    _label_links(res, scn.links)
    desc = f"{meta['label']} · '{pattern}' topology with {n} {rlabel} routers. Loopbacks 10.255.0.x/32, links /30 in 10.0.0.0/8."
    instr = _guide(topic, meta, routers, ifips, lo, solution, pattern, router_label=rlabel)
    return _finish(topic, desc, res, base, solution, instr)
