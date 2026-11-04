"""Ready-made lab scenarios by topic (OSPF, BGP, IS-IS, EIGRP, static, RIP, MPLS, EVPN/VXLAN, VLAN).

Each build picks a topology and size at random, so you get a different lab every time, and for the
routing topics it writes a working FRRouting startup-config per node (IP addressing + the protocol),
applied on boot (the node's `startup_config`). Routers are FRR (open, no license), hosts are VPC,
switches are the Linux bridge. The generated configs target FRRouting's integrated frr.conf.
"""
from __future__ import annotations

import ipaddress
import random

from .automation import GenerateError, _Builder, _circle, _row
from .models import SHARED_KINDS, Lab, Template

ROUTER = "frr"
HOST = "vpc"
SWITCH = "bridge"

# topic catalogue the UI lists (grouped by category)
TOPICS = [
    # cat = categoria, level = 1 base · 2 intermedio · 3 avanzato
    {"id": "static", "label": "Routing statico", "cat": "Base", "level": 1, "router": ROUTER,
     "desc": "Catena di router: rotte statiche verso le loopback remote."},
    {"id": "rip", "label": "RIP v2", "cat": "Base", "level": 1, "router": ROUTER,
     "desc": "RIP versione 2 fra i router."},
    {"id": "vlan", "label": "VLAN L2 (switch + host)", "cat": "Base", "level": 1, "router": HOST,
     "desc": "Uno switch con host in una stessa rete: palestra per VLAN e L2."},
    {"id": "ospf", "label": "OSPF (area singola)", "cat": "IGP", "level": 1, "router": ROUTER,
     "desc": "OSPF area 0 su tutti i collegamenti e le loopback."},
    {"id": "ospf-multiarea", "label": "OSPF multiarea", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "Dorsale in area 0 con router di confine (ABR) verso aree periferiche."},
    {"id": "isis", "label": "IS-IS", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "IS-IS livello 2 fra i router."},
    {"id": "eigrp", "label": "EIGRP", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "EIGRP AS 1 (eigrpd di FRR)."},
    {"id": "redistribute", "label": "Ridistribuzione + loopback", "cat": "IGP", "level": 2, "router": ROUTER,
     "desc": "OSPF che ridistribuisce reti connesse e statiche (route tra protocolli)."},
    {"id": "ospf-auth", "label": "OSPF con autenticazione", "cat": "Sicurezza", "level": 2, "router": ROUTER,
     "desc": "OSPF area 0 con autenticazione MD5 sulle adiacenze."},
    {"id": "bfd", "label": "OSPF + BFD (failover rapido)", "cat": "IGP", "level": 3, "router": ROUTER,
     "desc": "OSPF con BFD per rilevare i guasti in millisecondi."},
    {"id": "bgp-ebgp", "label": "BGP – eBGP", "cat": "BGP", "level": 2, "router": ROUTER,
     "desc": "Ogni router un AS diverso, sessioni eBGP sui collegamenti."},
    {"id": "bgp-ibgp", "label": "BGP – iBGP + OSPF", "cat": "BGP", "level": 3, "router": ROUTER,
     "desc": "Un solo AS: OSPF come underlay e iBGP full-mesh sulle loopback."},
    {"id": "bgp-security", "label": "BGP security (filtri + max-prefix)", "cat": "Sicurezza", "level": 3, "router": ROUTER,
     "desc": "eBGP con prefix-list in ingresso e limite maximum-prefix: anti-hijack di base."},
    {"id": "route-filter", "label": "Filtri di routing (ACL)", "cat": "Sicurezza", "level": 2, "router": ROUTER,
     "desc": "OSPF con access-list/distribute-list che blocca una rotta."},
    {"id": "bgp-communities", "label": "BGP con community", "cat": "BGP", "level": 3, "router": ROUTER,
     "desc": "eBGP che marca gli annunci con BGP community e le filtra."},
    {"id": "bgp-rr", "label": "BGP route reflector", "cat": "BGP", "level": 3, "router": ROUTER,
     "desc": "iBGP con un route reflector al posto del full-mesh."},
    {"id": "vrrp", "label": "VRRP (gateway ridondante)", "cat": "Alta affidabilità", "level": 3, "router": ROUTER,
     "desc": "Due router condividono un IP virtuale come gateway degli host."},
    {"id": "mpls-ldp", "label": "MPLS core (LDP)", "cat": "MPLS", "level": 3, "router": ROUTER,
     "desc": "Core MPLS con OSPF e distribuzione label via LDP."},
    {"id": "mpls-l3vpn", "label": "MPLS L3VPN (VRF + MP-BGP)", "cat": "MPLS", "level": 3, "router": ROUTER,
     "desc": "PE-P-PE con VRF e MP-BGP VPNv4 sopra un core MPLS."},
    {"id": "vxlan-evpn", "label": "VXLAN / EVPN (spine-leaf)", "cat": "Data center", "level": 3, "router": ROUTER,
     "desc": "CLOS spine-leaf con underlay OSPF e overlay BGP EVPN (VXLAN)."},
    {"id": "ipv6-ospf", "label": "IPv6 – OSPFv3", "cat": "IPv6", "level": 2, "router": ROUTER,
     "desc": "OSPFv3 (OSPF per IPv6) fra i router, con indirizzi 2001:db8::/32."},
    {"id": "ipv6-bgp", "label": "IPv6 – eBGP", "cat": "IPv6", "level": 3, "router": ROUTER,
     "desc": "eBGP su IPv6 (address-family ipv6 unicast)."},
    {"id": "pbr", "label": "Policy routing (PBR)", "cat": "Routing avanzato", "level": 3, "router": ROUTER,
     "desc": "Instrada il traffico in base alla sorgente con pbr-map (pbrd di FRR)."},
    {"id": "multicast", "label": "Multicast PIM-SM", "cat": "Routing avanzato", "level": 3, "router": ROUTER,
     "desc": "PIM sparse-mode con un RP e OSPF come underlay."},
    # Hacking / sicurezza offensiva: lab didattici in sandbox, con la difesa nella soluzione
    {"id": "bgp-hijack", "label": "BGP hijacking (attacco e difesa)", "cat": "Hacking", "level": 3, "router": ROUTER,
     "desc": "Un AS dirotta un prefisso altrui; impari a vederlo e a difenderti con filtri AS-path."},
    {"id": "mitm-arp", "label": "MITM / ARP spoofing (L2)", "cat": "Hacking", "level": 2, "router": HOST,
     "desc": "Attaccante e vittime sulla stessa LAN: ARP spoofing e come difendersi."},
    {"id": "recon-scan", "label": "Ricognizione e scansione", "cat": "Hacking", "level": 1, "router": HOST,
     "desc": "Un attaccante e una rete di server: scansione (nmap) e hardening."},
]
TOPIC_IDS = {t["id"] for t in TOPICS}


class _Scn:
    """Thin wrapper over the topology builder that also records which interface each link used."""

    def __init__(self, templates: dict[str, Template], router: str = ROUTER):
        if router not in templates:
            raise GenerateError(f"Il router scelto '{router}' non esiste")
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
        out += f"interface {ifc}\n description verso {peer}\n ipv6 address {ip}/64\n{extra(ifc)}!\n"
    return out


# ------------------------------------------------------------------ FRR config pieces

def _head(host: str) -> str:
    return (f"frr version 10.2\nfrr defaults traditional\nhostname {host}\n"
            "log syslog informational\nservice integrated-vtysh-config\n!\n")


def _ifaces(r: str, ifips: dict, lo: dict, extra=lambda ifc: "") -> str:
    out = f"interface lo\n ip address {lo[r]}/32\n{extra('lo')}!\n"
    for ifc in sorted(ifips[r]):
        ip, pfx, net, peer_ip, peer = ifips[r][ifc]
        out += f"interface {ifc}\n description verso {peer}\n ip address {ip}/{pfx}\n{extra(ifc)}!\n"
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
    # chain: route to every other loopback via the neighbour that sits on the way
    pos = {name: i for i, name in enumerate(routers)}
    for other in routers:
        if other == r:
            continue
        step = 1 if pos[other] > idx else -1
        nb = routers[idx + step]
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


def _cfg_vrrp_gw(name, iface, lan_ip, vip, idx):
    body = _head(name)
    body += f"interface lo\n ip address 10.255.0.{idx + 1}/32\n!\n"
    body += (f"interface {iface}\n ip address {lan_ip}/24\n vrrp 1 ip {vip}\n"
             f" vrrp 1 priority {110 if idx == 0 else 100}\n no vrrp 1 shutdown\n!\n")
    return body


def _pe_vrf(pe_lo, peer_lo, idx):
    """VRF + MP-BGP VPNv4 part of a PE (added after its core MPLS/LDP config)."""
    cust = f"172.16.{idx + 1}.1"
    body = "vrf CUST\n exit-vrf\n!\n"
    body += f"interface lo-cust vrf CUST\n ip address {cust}/32\n!\n"
    body += (f"router bgp 65000\n bgp router-id {pe_lo}\n neighbor {peer_lo} remote-as 65000\n"
             f" neighbor {peer_lo} update-source lo\n address-family ipv4 vpn\n  neighbor {peer_lo} activate\n exit-address-family\n!\n")
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
    out = [f"# Lab: {meta['label']}", "", "## Obiettivo", meta["desc"],
           f"Configura **{meta['label']}** sui {len(routers)} router FRR (IPv6) e verifica la raggiungibilità fra le loopback.",
           "", "## Indirizzi IPv6 (già impostati all'avvio)"]
    for r in routers:
        out.append(f"- **{r}** — loopback {lo[r]}/128")
        for ifc in sorted(if6[r]):
            ip, net, peer_ip, peer = if6[r][ifc]
            out.append(f"    - `{ifc}` {ip}/64  →  verso **{peer}** ({peer_ip})")
    out += ["", "## Passi"] + [f"{i + 1}. {s}" for i, s in enumerate(steps)]
    out += ["", "## Verifica"] + [f"- {v}" for v in verify]
    out += ["", "## Soluzione", "Config completa anche nel set «soluzione». Per nodo:"]
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
    else:  # chain
        rs = scn.add("r", rt, n, [(80 + i * 200, 300) for i in range(n)])
        for i in range(n - 1):
            scn.link(rs[i], rs[i + 1])
    return rs


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
FRR_ONLY_TOPICS = {"vxlan-evpn"}
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
        out += (f"interface {ifc}\n description verso {peer}\n"
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
    pos = {name: i for i, name in enumerate(routers)}
    for other in routers:
        if other == r:
            continue
        step = 1 if pos[other] > idx else -1
        nb = routers[idx + step]
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


def _ios_pe_vrf(pe_lo, peer_lo, idx):
    """VRF + MP-BGP VPNv4 part of an IOS PE (added after its core MPLS/LDP config)."""
    cust = f"172.16.{idx + 1}.1"
    body = (f"vrf definition CUST\n rd 65000:{idx + 1}\n address-family ipv4\n"
            "  route-target export 65000:100\n  route-target import 65000:100\n exit-address-family\n!\n")
    body += f"interface Loopback1\n vrf forwarding CUST\n ip address {cust} 255.255.255.255\n!\n"
    body += (f"router bgp 65000\n bgp router-id {pe_lo}\n bgp log-neighbor-changes\n"
             f" neighbor {peer_lo} remote-as 65000\n neighbor {peer_lo} update-source Loopback0\n"
             f" address-family vpnv4\n  neighbor {peer_lo} activate\n  neighbor {peer_lo} send-community extended\n exit-address-family\n"
             " address-family ipv4 vrf CUST\n  redistribute connected\n exit-address-family\n!\n")
    return body


def _ifaces6_ios(r, if6, lo, extra=lambda ifc: "") -> str:
    out = f"interface Loopback0\n ipv6 address {lo[r]}/128\n{extra('lo')}!\n"
    for ifc in sorted(if6[r]):
        ip, net, peer_ip, peer = if6[r][ifc]
        out += f"interface {ifc}\n description verso {peer}\n no shutdown\n ipv6 address {ip}/64\n{extra(ifc)}!\n"
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
    "static": (["Su ogni router aggiungi una rotta statica verso le loopback remote con «ip route <ip>/32 <next-hop>»."],
               ["«show ip route» su ogni router", "ping fra le loopback"]),
    "rip": (["Abilita RIP: «router rip», «version 2», «network <rete>» per ogni rete connessa."],
            ["«show ip rip», «show ip route rip»", "ping fra le loopback"]),
    "ospf": (["Abilita OSPF area 0: «router ospf», poi «network <rete> area 0» per ogni collegamento e per la loopback."],
             ["«show ip ospf neighbor» (adiacenze Full)", "«show ip route ospf»", "ping fra le loopback"]),
    "ospf-multiarea": (["Dorsale in area 0; le reti periferiche in aree diverse, con i router di confine (ABR) in entrambe le aree."],
                       ["«show ip ospf neighbor», «show ip ospf database»", "ping fra le loopback"]),
    "isis": (["Abilita IS-IS: «router isis 1», «net 49.0001.<sys>.00», e «ip router isis 1» su ogni interfaccia e sulla loopback."],
             ["«show isis neighbor», «show ip route isis»", "ping fra le loopback"]),
    "eigrp": (["Abilita EIGRP AS 1: «router eigrp 1», poi «network <rete>» per ogni rete."],
              ["«show ip eigrp neighbors», «show ip route eigrp»", "ping fra le loopback"]),
    "bgp-ebgp": (["Ogni router è un AS diverso. Configura «router bgp <as>», un «neighbor <ip> remote-as <as-vicino>» per ogni collegamento, e annuncia la loopback."],
                 ["«show ip bgp summary» (sessioni Established)", "«show ip bgp»", "ping fra le loopback"]),
    "bgp-ibgp": (["Un solo AS 65000. Prima OSPF come underlay (raggiungibilità delle loopback), poi iBGP full-mesh: «neighbor <loopback> remote-as 65000» e «update-source lo»."],
                 ["prima «show ip ospf neighbor», poi «show ip bgp summary»", "ping fra le loopback"]),
    "mpls-ldp": (["Abilita OSPF, poi MPLS LDP: «mpls ldp», «address-family ipv4», e «interface <ifc>» per ogni collegamento del core."],
                 ["«show mpls ldp neighbor», «show mpls table»", "traceroute fra le loopback (vedi le etichette MPLS)"]),
    "redistribute": (["Abilita OSPF, aggiungi una rotta statica (es. verso una LAN) e ridistribuisci: «redistribute connected» e «redistribute static» dentro «router ospf»."],
                     ["«show ip route ospf» (rotte O E2 esterne)", "ping fra le loopback e le LAN"]),
    "ospf-auth": (["Abilita OSPF area 0.", "Metti l'autenticazione MD5: «ip ospf authentication message-digest» e «ip ospf message-digest-key 1 md5 <chiave>» su ogni interfaccia; in «router ospf» aggiungi «area 0 authentication message-digest»."],
                  ["«show ip ospf neighbor» (adiacenze Full solo se le chiavi combaciano)", "«show ip ospf interface»"]),
    "bfd": (["Abilita OSPF.", "Attiva BFD: blocco «bfd» globale e «ip ospf bfd» su ogni interfaccia. Prova a staccare un link e guarda quanto è veloce il failover."],
            ["«show bfd peers» (stato Up)", "«show ip ospf neighbor»"]),
    "bgp-security": (["Configura eBGP (un AS per router).", "Proteggi le sessioni: «ip prefix-list» che accetta solo le loopback /32 e la applichi «prefix-list … in» su ogni neighbor; aggiungi «maximum-prefix»."],
                     ["«show ip bgp summary» e «show ip bgp neighbor <ip> filtered-routes»", "prova ad annunciare una rete non prevista: deve essere scartata"]),
    "bgp-communities": (["Configura eBGP.", "Marca gli annunci con una «route-map» che fa «set community <as>:100» e abilita «send-community» verso i vicini; poi filtra in base alla community."],
                        ["«show ip bgp <rete>» (riga Community)", "«show ip bgp community <as>:100»"]),
    "route-filter": (["Abilita OSPF.", "Crea una «access-list FILTER» che nega la loopback bersaglio e permette il resto; applicala con «distribute-list FILTER in» dentro «router ospf». Verifica che quella rotta sparisca dalle tabelle."],
                     ["«show ip route ospf» (la rotta filtrata non c'è)", "«show access-list»"]),
    "bgp-rr": (["Abilita OSPF come underlay.", "Scegli un router come route reflector: fa peer iBGP con tutti e li marca «route-reflector-client»; gli altri fanno peer solo col RR. Niente full-mesh."],
               ["«show ip bgp summary» sul RR (tutti Established)", "«show ip bgp» sui client (vedono le rotte riflesse)"]),
    "vrrp": (["Metti i due gateway sulla stessa LAN (es. 192.168.1.0/24) con IP .2 e .3.", "Su entrambi, sull'interfaccia LAN: «vrrp 1 ip 192.168.1.1», priorità più alta sul master, «no vrrp 1 shutdown». Gli host usano .1 come gateway."],
             ["«show vrrp» (uno Master, uno Backup)", "spegni il master e verifica che il backup prenda il VIP"]),
    "mpls-l3vpn": (["Sul core: OSPF + LDP fra PE1-P-PE2 (loopback raggiungibili con etichette).", "Sui PE: una VRF «CUST», MP-BGP «address-family ipv4 vpn» fra le loopback dei PE, e nella VRF rd/rt + «redistribute connected»."],
                   ["«show mpls ldp neighbor», «show bgp ipv4 vpn summary»", "«show ip route vrf CUST» sui PE (rotte del cliente via MPLS)"]),
    "ipv6-ospf": (["Metti gli indirizzi IPv6 sulle interfacce (già fatto).", "Abilita OSPFv3: «ipv6 ospf6 area 0» su ogni interfaccia e sulla loopback, poi «router ospf6» con «ospf6 router-id»."],
                  ["«show ipv6 ospf6 neighbor» (Full)", "«show ipv6 route ospf6»", "ping6 fra le loopback"]),
    "ipv6-bgp": (["Configura eBGP IPv6: «router bgp <as>», «neighbor <ipv6> remote-as <as>» per ogni link, poi «address-family ipv6 unicast» con «neighbor <ipv6> activate» e «network <loopback>/128»."],
                 ["«show bgp ipv6 unicast summary»", "«show bgp ipv6 unicast»", "ping6 fra le loopback"]),
    "pbr": (["Abilita OSPF (raggiungibilità di base).", "Crea una «nexthop-group» col next-hop desiderato, una «pbr-map» che fa «match src-ip <rete>» e «set nexthop-group», e applicala all'interfaccia con «pbr-policy»."],
            ["«show pbr map», «show pbr nexthop-groups»", "manda traffico dalla sorgente scelta e verifica il percorso con traceroute"]),
    "multicast": (["Abilita OSPF come underlay.", "Attiva PIM: «ip pim» su ogni interfaccia e definisci l'RP con «ip pim rp <loopback-RP> 224.0.0.0/4». Fai unire un ricevitore a un gruppo e trasmetti dalla sorgente."],
                  ["«show ip pim neighbor», «show ip pim rp-info»", "«show ip mroute» mentre scorre il traffico multicast"]),
    "bgp-hijack": (["Configura eBGP; r1 (AS 65001) annuncia 203.0.113.0/24.", "ATTACCO: su un altro router annuncia anche tu 203.0.113.0/24 (o 203.0.113.0/25 più specifico) e osserva come il traffico viene dirottato.", "DIFESA: sui vicini applica un filtro AS-path/prefix-list che accetta quel prefisso solo dall'origine legittima (route-map ANTIHIJACK in)."],
                   ["«show ip bgp 203.0.113.0/24» (quale AS-path vince)", "dopo la difesa: l'annuncio falso viene scartato"]),
    "vxlan-evpn": (["Underlay: OSPF su spine e leaf per raggiungere le loopback.",
                    "Overlay: «router bgp 65000», «address-family l2vpn evpn»; i leaf fanno peer con gli spine (route-reflector). Sui leaf aggiungi «advertise-all-vni» e il VNI."],
                   ["«show bgp l2vpn evpn summary»", "«show evpn vni»"]),
}


def _guide(topic: str, meta: dict, routers: list[str], ifips: dict, lo: dict, solution: dict,
           pattern: str = "", intro: str = "", router_label: str = "FRR") -> str:
    steps, verify = STEPS.get(topic, ([], []))
    out = [f"# Lab: {meta['label']}", "", "## Obiettivo",
           f"{meta['desc']} {intro}".strip(),
           f"Configura **{meta['label']}** sui {len(routers)} router {router_label} e verifica la raggiungibilità fra le loopback."]
    out += ["", "## Indirizzi IP (già impostati all'avvio)"]
    for r in routers:
        out.append(f"- **{r}** — loopback {lo[r]}/32")
        for ifc in sorted(ifips.get(r, {})):
            ip, pfx, net, peer_ip, peer = ifips[r][ifc]
            out.append(f"    - `{ifc}` {ip}/{pfx}  →  verso **{peer}** ({peer_ip})")
    out += ["", "## Passi"]
    out += [f"{i + 1}. {s}" for i, s in enumerate(steps)] or ["1. Configura il protocollo richiesto."]
    out += ["", "## Verifica"]
    out += [f"- {v}" for v in verify] or ["- Verifica le tabelle di routing e fai ping."]
    out += ["", "## Soluzione",
            "La configurazione completa è anche nel set di config «soluzione» (menu Config nel lab). Qui sotto, per nodo:"]
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
        raise GenerateError(f"Argomento '{topic}' sconosciuto")
    rng = random.Random(seed)
    meta = next(t for t in TOPICS if t["id"] == topic)

    # which router to build with, and in which config dialect
    router_tpl = router if (router and router in templates) else _default_router(templates)
    dia = dialect_of(templates[router_tpl]) if router_tpl in templates else None
    rlabel = (templates[router_tpl].title or router_tpl) if router_tpl in templates else router_tpl
    if topic not in HOST_TOPICS:
        if dia is None:
            raise GenerateError("Nessun router adatto installato: serve FRR o un router Cisco IOS "
                                "(IOL, c7200/c3725, vIOS/IOSv, CSR).")
        if dia != "frr" and topic not in IOS_TOPICS:
            raise GenerateError(
                f"Lo scenario «{meta['label']}» per ora si genera solo con FRR "
                "(funzione non disponibile in modo affidabile su Cisco IOL/IOSv). "
                "Scegli FRR come router per questo argomento.")

    # ---- VLAN: switch + hosts (configured by hand on the switch)
    if topic == "vlan":
        if HOST not in templates or SWITCH not in templates:
            raise GenerateError("Mancano i dispositivi 'vpc' e/o 'bridge' per lo scenario VLAN")
        n = _clamp(count, rng.randint(3, 6), 2, 20)
        b = _Builder(Lab(name="gen"), templates)
        sw = b.add("sw", SWITCH, 1, [(420, 180)])[0]
        hosts = b.add("pc", HOST, n, _circle(n, 420, 360, max(180, n * 45)))
        for h in hosts:
            b.link(sw, h)
        res = b.result()
        for ln in res["links"]:
            ln["label"] = "192.168.10.0/24"
        desc = f"Palestra VLAN: uno switch con {n} host nella rete 192.168.10.0/24."
        instr = ("# Lab: VLAN L2\n\n## Obiettivo\nUno switch con host nella rete 192.168.10.0/24. "
                 "Crea le VLAN per porta sullo switch e verifica quali host si parlano.\n\n"
                 "## Passi\n1. Assegna agli host IP in 192.168.10.0/24 (es. .11, .12, …).\n"
                 "2. Sullo switch (nodo «bridge») imposta le VLAN per porta (access/trunk).\n"
                 "3. Metti alcuni host in VLAN diverse.\n\n## Verifica\n- ping fra host della stessa VLAN (ok) e di VLAN diverse (ko).\n\n"
                 "## Soluzione\nGli host nella stessa VLAN si raggiungono; quelli in VLAN diverse no, "
                 "finché non aggiungi un router/SVI fra le VLAN.")
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
        desc = f"VXLAN/EVPN spine-leaf: {nspine} spine, {nleaf} leaf. Underlay OSPF, overlay BGP EVPN, VNI {vni}."
        instr = _guide(topic, meta, routers, ifips, lo, solution, intro=f"VNI {vni}.")
        return _finish("vxlan-evpn", desc, res, _base_configs(routers, ifips, lo), solution, instr)

    # ---- VRRP: a LAN (bridge) with two gateway routers sharing a virtual IP, plus hosts
    if topic == "vrrp":
        if HOST not in templates or SWITCH not in templates:
            raise GenerateError("Mancano i dispositivi 'vpc' e/o 'bridge' per lo scenario VRRP")
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
        desc = f"VRRP: 2 gateway ({', '.join(gws)}) con IP virtuale {vip} per {nhosts} host sulla LAN 192.168.1.0/24."
        steps, verify = STEPS["vrrp"]
        instr = ("# Lab: VRRP (gateway ridondante)\n\n## Obiettivo\n"
                 f"Due router ({gws[0]}, {gws[1]}) condividono il gateway virtuale **{vip}** per gli host della LAN 192.168.1.0/24.\n\n"
                 "## Indirizzi\n"
                 f"- **{gws[0]}** {gw_iface[gws[0]]} 192.168.1.2/24 (master)\n- **{gws[1]}** {gw_iface[gws[1]]} 192.168.1.3/24 (backup)\n"
                 f"- IP virtuale (gateway degli host): **{vip}**\n- Host: 192.168.1.11, .12, … con gateway {vip}\n\n"
                 "## Passi\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) +
                 "\n\n## Verifica\n" + "\n".join(f"- {v}" for v in verify) +
                 "\n\n## Soluzione\n" + "".join(f"\n### {g}\n```\n{solution[g].strip()}\n```\n" for g in gws))
        return _finish("vrrp", desc, res, base, solution, instr)

    # ---- MPLS L3VPN: PE1 - P - PE2 core with per-PE VRF and MP-BGP VPNv4
    if topic == "mpls-l3vpn":
        scn = _Scn(templates, router_tpl)
        pes = scn.add("pe", router_tpl, 2, [(150, 300), (690, 300)])
        p = scn.add("p", router_tpl, 1, [(420, 300)])[0]
        pe1, pe2 = pes
        scn.link(pe1, p)
        scn.link(p, pe2)
        names = [pe1, p, pe2]
        res = scn.result()
        lo, ifips = _address(names, scn.links)
        _label_links(res, scn.links)
        if dia == "ios":
            solution = {
                p: _ios_mpls_ldp(p, ifips, lo, names, scn.links, 1),
                pe1: _ios_mpls_ldp(pe1, ifips, lo, names, scn.links, 0) + _ios_pe_vrf(lo[pe1], lo[pe2], 0),
                pe2: _ios_mpls_ldp(pe2, ifips, lo, names, scn.links, 2) + _ios_pe_vrf(lo[pe2], lo[pe1], 1),
            }
            base = _base_configs_ios(names, ifips, lo)
        else:
            solution = {
                p: _cfg_mpls_ldp(p, ifips, lo, names, scn.links, 1),
                pe1: _cfg_mpls_ldp(pe1, ifips, lo, names, scn.links, 0) + _pe_vrf(lo[pe1], lo[pe2], 0),
                pe2: _cfg_mpls_ldp(pe2, ifips, lo, names, scn.links, 2) + _pe_vrf(lo[pe2], lo[pe1], 1),
            }
            base = _base_configs(names, ifips, lo)
        desc = f"MPLS L3VPN: {pe1} - {p} - {pe2}, core OSPF+LDP, VRF CUST e MP-BGP VPNv4 fra i PE ({rlabel})."
        instr = _guide("mpls-l3vpn", meta, names, ifips, lo, solution,
                       intro="Il P è solo transito MPLS; i PE hanno la VRF del cliente.", router_label=rlabel)
        return _finish("mpls-l3vpn", desc, res, base, solution, instr)

    # ---- Hacking: MITM / ARP spoofing on a shared LAN (topology + guide, host-based)
    if topic == "mitm-arp":
        if HOST not in templates or SWITCH not in templates:
            raise GenerateError("Mancano i dispositivi 'vpc' e/o 'bridge' per questo lab")
        nvict = _clamp(count, 2, 2, 8)
        res, atk, _gw, vics = _hosts_lab(templates, nvict, attacker_prefix="kali")
        desc = f"MITM/ARP spoofing: 1 attaccante ({atk}) e {nvict} vittime sulla LAN 192.168.1.0/24."
        instr = ("# Lab: MITM / ARP spoofing (L2)\n\n> Lab didattico in sandbox: usalo solo qui per imparare attacco **e** difesa.\n\n"
                 "## Obiettivo\n"
                 f"L'attaccante **{atk}** e le vittime ({', '.join(vics)}) sono sulla stessa LAN 192.168.1.0/24. "
                 "Capire come un attaccante intercetta il traffico con ARP spoofing e come ci si difende.\n\n"
                 "## Preparazione\n- Assegna gli IP: attaccante 192.168.1.10, vittime .11, .12, …; gateway virtuale 192.168.1.1.\n"
                 "- Per gli strumenti veri serve un'immagine attaccante (es. Kali) sul nodo attaccante.\n\n"
                 "## Attacco (da imparare)\n1. Osserva la tabella ARP delle vittime (`arp -a`).\n"
                 "2. Avvela la cache ARP così l'attaccante si mette in mezzo (es. `arpspoof`/`ettercap` tra vittima e gateway).\n"
                 "3. Cattura il traffico che ora passa dall'attaccante (Wireshark nel lab).\n\n"
                 "## Difesa (soluzione)\n- Voci ARP **statiche** sui client critici.\n- Sullo switch: **Dynamic ARP Inspection** e **DHCP snooping**; **port-security** per limitare i MAC.\n"
                 "- Segmentare con VLAN e usare traffico cifrato (HTTPS/SSH) così l'intercettazione è inutile.")
        return {"name_hint": "mitm-arp", "description": desc, "nodes": res["nodes"], "links": res["links"],
                "configs": {}, "solution": {}, "instructions": instr}

    # ---- Hacking: recon / scanning of a small server network
    if topic == "recon-scan":
        if HOST not in templates or SWITCH not in templates:
            raise GenerateError("Mancano i dispositivi 'vpc' e/o 'bridge' per questo lab")
        nsrv = _clamp(count, 3, 1, 8)
        res, atk, gw, srvs = _hosts_lab(templates, nsrv, attacker_prefix="kali", gateway=True)
        desc = f"Ricognizione: 1 attaccante ({atk}), un gateway e {nsrv} server sulla LAN 192.168.1.0/24."
        instr = ("# Lab: Ricognizione e scansione\n\n> Lab didattico in sandbox: scansiona solo i nodi di questo lab.\n\n"
                 "## Obiettivo\n"
                 f"Dall'attaccante **{atk}** mappare la rete 192.168.1.0/24 (gateway **{gw}**, server {', '.join(srvs)}), "
                 "trovare host e servizi, poi irrobustire.\n\n"
                 "## Passi (da imparare)\n1. Scopri gli host attivi: ping sweep / `nmap -sn 192.168.1.0/24`.\n"
                 "2. Scansiona le porte e i servizi dei server: `nmap -sV <ip>`.\n"
                 "3. Annota versioni e servizi esposti.\n\n"
                 "## Difesa (soluzione)\n- Chiudi i servizi non necessari e aggiorna le versioni.\n"
                 "- Metti un **firewall** sul gateway (consenti solo le porte utili).\n"
                 "- Rate-limit/segmenta per rendere la scansione rumorosa e lenta; monitora i log.")
        return {"name_hint": "recon-scan", "description": desc, "nodes": res["nodes"], "links": res["links"],
                "configs": {}, "solution": {}, "instructions": instr}

    # ---- IPv6 scenarios (OSPFv3 / eBGP IPv6)
    if topic in ("ipv6-ospf", "ipv6-bgp"):
        scn = _Scn(templates, router_tpl)
        n = _clamp(count, rng.randint(3, 5), 2, 20)
        pattern = rng.choice(["ring", "chain"] + (["mesh"] if n <= 6 else []))
        routers = _router_chain_or_ring(scn, rng, n, pattern)
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
        desc = f"{meta['label']} · topologia '{pattern}' con {n} router {rlabel}. Loopback 2001:db8:ffff::/48, link /64."
        instr = _guide6(topic, meta, routers, if6, lo6, solution)
        return _finish(topic, desc, res, base, solution, instr)

    # ---- router scenarios (IGP / BGP / MPLS / static / RIP)
    scn = _Scn(templates, router_tpl)
    n = _clamp(count, rng.randint(3, 5), (4 if topic == "ospf-multiarea" else 2), 20)
    # full mesh explodes with many nodes (N·(N-1)/2 links), so keep it only for small labs
    choices = ["ring", "chain"] + (["mesh"] if n <= 6 else [])
    pattern = ({"static": "chain", "ospf-multiarea": "ring",
                "mpls-ldp": rng.choice(["ring", "chain"])}.get(topic) or rng.choice(choices))
    routers = _router_chain_or_ring(scn, rng, n, pattern)
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
              "multicast": _cfg_pim, "bgp-hijack": _cfg_bgp_hijack}[topic]
        for i, r in enumerate(routers):
            solution[r] = fn(r, ifips, lo, routers, scn.links, i)
        base = _base_configs(routers, ifips, lo)

    _label_links(res, scn.links)
    desc = f"{meta['label']} · topologia '{pattern}' con {n} router {rlabel}. Loopback 10.255.0.x/32, link /30 in 10.0.0.0/8."
    instr = _guide(topic, meta, routers, ifips, lo, solution, pattern, router_label=rlabel)
    return _finish(topic, desc, res, base, solution, instr)
