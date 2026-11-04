"""Orchestration: deploy / destroy labs, start / stop / wipe nodes."""
from __future__ import annotations

import json
import os
import shutil
import socket
import threading
from collections import defaultdict
from pathlib import Path

from . import network as net
from . import topology as topo
from .capture import CaptureManager
from .wireshark import WiresharkManager
from .config import settings
from .drivers import DockerDriver, QemuDriver
from .drivers.dynamips import DynamipsDriver
from .drivers.iol import IOL_IDS, IolDriver
from .hostcmd import Runner, runner as default_runner
from .models import PROCESS_KINDS, Lab, Link, LabState, NodeState, ResolvedNode

_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_port_lock = threading.Lock()


class EngineError(RuntimeError):
    pass


# ------------------------------------------------------------------- state

def state_file(lab: str) -> Path:
    return topo.lab_dir(lab) / "state.json"


def load_state(lab: str) -> LabState:
    f = state_file(lab)
    if f.exists():
        return LabState(**json.loads(f.read_text()))
    return LabState()


def save_state(lab: str, state: LabState) -> None:
    f = state_file(lab)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(state.model_dump_json(indent=2))
    tmp.replace(f)


# ---------------------------------------------------------- port allocation

def _used_ports() -> tuple[set[int], set[int]]:
    ports, displays = set(), set()
    for name in topo.list_labs():
        for ns in load_state(name).nodes.values():
            ports.update(x for x in (ns.console_port, ns.spice_port) if x)
            if ns.vnc_display is not None:
                displays.add(ns.vnc_display)
    return ports, displays


def _used_iol_ids() -> set[int]:
    return {ns.iol_id for name in topo.list_labs() for ns in load_state(name).nodes.values()
            if ns.iol_id and ns.status == "running"}


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def allocate_port(taken: set[int]) -> int:
    p = settings.console_port_start
    while p in taken or not _port_free(p):
        p += 1
    taken.add(p)
    return p


def allocate_display(taken: set[int]) -> int:
    d = settings.vnc_display_start
    while d in taken or not _port_free(5900 + d):
        d += 1
    taken.add(d)
    return d


# ---------------------------------------------------------------- planning

def plan_links(lab: Lab, resolved: dict[str, ResolvedNode]) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Return (bridges owned by the lab, {node: {iface: bridge}})."""
    owned: list[str] = []
    wiring: dict[str, dict[str, str]] = defaultdict(dict)
    for rn in resolved.values():
        if rn.kind in ("bridge", "nat", "vxlan"):
            owned.append(net.node_bridge_name(lab.name, rn.name))
    for link in lab.links:
        a, b = link.parsed()
        ka, kb = resolved[a.node].kind, resolved[b.node].kind
        if ka in ("bridge", "nat", "vxlan") or kb in ("bridge", "nat", "vxlan"):
            hub = a if ka in ("bridge", "nat", "vxlan") else b
            br = net.node_bridge_name(lab.name, hub.node)
        elif ka == "external" or kb == "external":
            ext = a if ka == "external" else b
            br = resolved[ext.node].host_bridge or ""
        else:
            br = net.link_bridge_name(lab.name, link.endpoints)
            owned.append(br)
        for ep in (a, b):
            if resolved[ep.node].kind in ("docker", *PROCESS_KINDS):
                wiring[ep.node][ep.iface] = br
    return owned, dict(wiring)


# ------------------------------------------------------------------ engine

class Engine:
    def __init__(self, runner: Runner | None = None):
        self.r = runner or default_runner
        self.docker = DockerDriver(self.r)
        self.qemu = QemuDriver(self.r)
        self.dynamips = DynamipsDriver(self.r)
        self.iol = IolDriver(self.r)
        self.captures = CaptureManager(self.r)
        self.wireshark = WiresharkManager(self.r)

    # -- helpers
    def _context(self, name: str):
        lab = topo.load_lab(name)
        resolved = topo.validate_lab(lab)
        owned, wiring = plan_links(lab, resolved)
        return lab, resolved, owned, wiring

    def _ensure_bridges(self, owned: list[str]) -> None:
        for br in owned:
            net.create_bridge(self.r, br)

    @staticmethod
    def _startup_config(lab: Lab, rn: ResolvedNode) -> str | None:
        """Config of the active set for a node that has startup-config ON."""
        if not rn.startup_config or rn.config_method == "none":
            return None
        return topo.read_config(lab.name, lab.config_set, rn.name) or None

    def _start_node(self, lab: str, rn: ResolvedNode, ns: NodeState, wiring: dict[str, str],
                    config: str | None = None) -> None:
        ns.error = None
        if rn.kind in PROCESS_KINDS:
            with _port_lock:
                ports, displays = _used_ports()
                ports.discard(ns.console_port or -1)
                ports.discard(ns.spice_port or -1)
                ns.console = rn.console
                ns.spice_port = allocate_port(ports) if rn.console == "spice" else None
                displays.discard(ns.vnc_display if ns.vnc_display is not None else -1)
                if rn.console == "telnet":
                    ns.console_port = ns.console_port if ns.console_port and _port_free(ns.console_port) \
                        else allocate_port(ports)
                else:  # no serial: a port left from an earlier telnet setting would send the console nowhere
                    ns.console_port = None
                if rn.kind == "iol":  # keep its number (the NVRAM file is named after it) unless taken
                    ns.vnc_display = None
                    used = _used_iol_ids() - {ns.iol_id}
                    if not ns.iol_id or ns.iol_id in used:
                        ns.iol_id = next((i for i in IOL_IDS if i not in used), None)
                        if ns.iol_id is None:
                            raise EngineError("Troppi nodi IOL accesi (massimo 511)")
                elif ns.vnc_display is None or not _port_free(5900 + ns.vnc_display):
                    ns.vnc_display = allocate_display(displays)
            if rn.kind == "iol":
                self.iol.start(lab, rn, ns, wiring, config)
                ns.status = "running"
                return
            if rn.kind == "dynamips":
                self.dynamips.start(lab, rn, ns, wiring, config)
                ns.status = "running"
                return
            if rn.mgmt_iface:  # management NIC on the same network as the containers' eth0
                wiring = {**wiring, rn.mgmt_iface: self.docker.mgmt_bridge()}
            ns.mgmt_ip = rn.mgmt_ip
            self.qemu.start(lab, rn, ns, wiring, config)
        elif rn.kind == "docker":
            pid = self.docker.start(lab, rn, ns, config)
            ns.veths = {}
            for iface, br in wiring.items():
                host_end = net.veth_name(lab, rn.name, iface)
                net.plug_veth_into_container(self.r, host_end, pid, iface, br)
                ns.veths[iface] = host_end
        elif rn.kind == "bridge":
            ns.bridge = net.node_bridge_name(lab, rn.name)
            net.create_bridge(self.r, ns.bridge)
        elif rn.kind == "nat":
            ns.bridge = net.node_bridge_name(lab, rn.name)
            net.create_bridge(self.r, ns.bridge)
            ns.subnet = rn.subnet or net.default_nat_subnet(lab, rn.name)
            ns.dnsmasq_pid = net.nat_up(self.r, ns.bridge, ns.subnet, topo.lab_dir(lab) / "nodes" / rn.name)
        elif rn.kind == "vxlan":
            ns.bridge = net.node_bridge_name(lab, rn.name)
            net.create_bridge(self.r, ns.bridge)
            ns.tunnel = net.vxlan_name(lab, rn.name)
            net.vxlan_up(self.r, ns.tunnel, ns.bridge, rn.vni or 0, rn.remote or "", rn.dstport or 4789)
        elif rn.kind == "external":
            ns.bridge = rn.host_bridge
            if not self.r.dry_run and not net.iface_exists(self.r, ns.bridge or ""):
                raise EngineError(f"Il bridge host '{ns.bridge}' non esiste (nodo {rn.name})")
        ns.status = "running"

    def _host_iface(self, ns: NodeState, iface: str) -> str | None:
        """Host-side interface carrying a node's link endpoint (tap or veth)."""
        if ns.kind in PROCESS_KINDS:
            return ns.taps.get(iface)
        if ns.kind == "docker":
            return ns.veths.get(iface)
        return None

    # -- hot-plug: connect/disconnect a single link while the lab is running
    def _link_bridge(self, lab: Lab, resolved: dict[str, ResolvedNode], link: Link) -> tuple[str, bool]:
        """(bridge, owned) for one link. owned=True when the lab creates its own point-to-point bridge."""
        a, b = link.parsed()
        ka, kb = resolved[a.node].kind, resolved[b.node].kind
        if ka in ("bridge", "nat", "vxlan") or kb in ("bridge", "nat", "vxlan"):
            hub = a if ka in ("bridge", "nat", "vxlan") else b
            return net.node_bridge_name(lab.name, hub.node), False
        if ka == "external" or kb == "external":
            ext = a if ka == "external" else b
            return resolved[ext.node].host_bridge or "", False
        return net.link_bridge_name(lab.name, link.endpoints), True

    def _attach_endpoint(self, lab: str, resolved: dict[str, ResolvedNode], ns: NodeState, ep, br: str) -> None:
        rn = resolved[ep.node]
        if rn.kind in ("bridge", "nat", "vxlan", "external"):
            return  # the hub node *is* the bridge; nothing to plug on its side
        host = self._host_iface(ns, ep.iface)
        if host:
            net.enslave(self.r, host, br)            # VM tap (always created at boot) or an existing veth: move it onto the bridge
        elif rn.kind == "docker" and ns.pid:          # a free container interface: create its veth now
            host = net.veth_name(lab, rn.name, ep.iface)
            net.plug_veth_into_container(self.r, host, ns.pid, ep.iface, br)
            ns.veths[ep.iface] = host
        else:
            raise EngineError(f"Impossibile collegare a caldo {ep.node}:{ep.iface} (interfaccia non pronta)")

    def _detach_endpoint(self, resolved: dict[str, ResolvedNode], ns: NodeState, ep) -> None:
        rn = resolved[ep.node]
        if rn.kind in ("bridge", "nat", "vxlan", "external"):
            return
        host = self._host_iface(ns, ep.iface)
        if not host:
            return
        net.clear_netem(self.r, host)
        if rn.kind == "docker":
            net.delete_iface(self.r, host)           # drop the veth so the interface frees up in the container
            ns.veths.pop(ep.iface, None)
        else:
            net.release(self.r, host)                # keep the VM tap (the NIC is fixed), just unbridge it

    def connect_link(self, name: str, link: Link) -> LabState:
        """Add a link to the lab and, if it is running, wire it live (hot-connect)."""
        with _locks[name]:
            lab = topo.load_lab(name)
            a, b = link.parsed()
            for ep in (a, b):
                if not any(n.name == ep.node for n in lab.nodes):
                    raise EngineError(f"Nodo '{ep.node}' inesistente")
                if any(str(ep) in l.endpoints for l in lab.links):
                    raise EngineError(f"{ep.node}:{ep.iface} è già collegata")
            state = load_state(name)
            if state.status == "running":
                ctx_lab, resolved, _owned, _wiring = self._context(name)
                br, owned = self._link_bridge(ctx_lab, resolved, link)
                if owned:
                    net.create_bridge(self.r, br)
                    if br not in state.link_bridges:
                        state.link_bridges.append(br)
                for ep in (a, b):
                    self._attach_endpoint(name, resolved, state.nodes[ep.node], ep, br)
                if link.has_impairments:
                    for ep in (a, b):
                        host = self._host_iface(state.nodes[ep.node], ep.iface)
                        if host:
                            net.apply_netem(self.r, host, delay_ms=link.delay_ms, jitter_ms=link.jitter_ms,
                                            loss_pct=link.loss_pct, rate_kbit=link.rate_kbit)
                save_state(name, state)
            # persist the cable only once the live wiring (if any) succeeded, so a failure leaves no phantom link
            lab.links.append(link)
            topo.save_lab(lab)
            return load_state(name)

    def disconnect_link(self, name: str, index: int) -> LabState:
        """Remove the link at `index` and, if the lab is running, tear its wiring down live."""
        with _locks[name]:
            lab = topo.load_lab(name)
            if not 0 <= index < len(lab.links):
                raise EngineError("Collegamento inesistente")
            link = lab.links[index]
            state = load_state(name)
            if state.status == "running":
                ctx_lab, resolved, _owned, _wiring = self._context(name)
                br, owned = self._link_bridge(ctx_lab, resolved, link)
                a, b = link.parsed()
                for ep in (a, b):
                    if ep.node in state.nodes:
                        self._detach_endpoint(resolved, state.nodes[ep.node], ep)
                if owned and br:
                    net.delete_iface(self.r, br)
                    if br in state.link_bridges:
                        state.link_bridges.remove(br)
                save_state(name, state)
            lab.links.pop(index)
            topo.save_lab(lab)
            return load_state(name)

    def capture_iface(self, name: str, node: str, iface: str) -> str:
        """Host interface to sniff for a node's link endpoint; the node must be running."""
        ns = load_state(name).nodes.get(node)
        if ns is None or ns.status != "running":
            raise EngineError(f"Il nodo '{node}' non è in esecuzione")
        host = self._host_iface(ns, iface)
        if not host:
            raise EngineError(f"Nessuna interfaccia da catturare per {node}:{iface} (collegata?)")
        return host

    def _apply_impairments(self, lab: Lab, state: LabState) -> None:
        """Shape every link that declares conditioning, on both endpoints."""
        for link in lab.links:
            a, b = link.parsed()
            for ep in (a, b):
                ns = state.nodes.get(ep.node)
                if ns is None or ns.status != "running":
                    continue
                host = self._host_iface(ns, ep.iface)
                if not host:
                    continue
                if link.has_impairments:
                    net.apply_netem(self.r, host, delay_ms=link.delay_ms,
                                    jitter_ms=link.jitter_ms, loss_pct=link.loss_pct,
                                    rate_kbit=link.rate_kbit)
                else:
                    net.clear_netem(self.r, host)

    def _apply_vlans(self, lab: Lab, state: LabState) -> None:
        """802.1Q on switch nodes: per-port access/trunk on the host interfaces of the connected devices."""
        try:
            resolved = topo.validate_lab(lab)
        except Exception:
            return
        for rn in resolved.values():
            if rn.kind != "bridge":
                continue
            br = net.node_bridge_name(lab.name, rn.name)
            ports = rn.ports or {}
            net.set_vlan_filtering(self.r, br, bool(ports))
            if not ports:
                continue
            for link in lab.links:
                a, b = link.parsed()
                me, peer = (a, b) if a.node == rn.name else (b, a) if b.node == rn.name else (None, None)
                if me is None or me.iface not in ports:
                    continue
                pns = state.nodes.get(peer.node)
                host = self._host_iface(pns, peer.iface) if pns and pns.status == "running" else None
                if host:
                    net.set_port_vlans(self.r, host, ports[me.iface])

    def _stop_node(self, ns: NodeState) -> None:
        if ns.kind in PROCESS_KINDS:
            self.qemu.stop(ns)  # same pid + taps bookkeeping
        elif ns.kind == "docker":
            self.docker.stop(ns)
            for v in ns.veths.values():
                net.delete_iface(self.r, v)
            ns.veths = {}
        elif ns.kind == "vxlan" and ns.tunnel:
            net.delete_iface(self.r, ns.tunnel)
            ns.tunnel = None
        elif ns.kind == "nat" and ns.bridge:
            net.nat_down(self.r, ns.bridge, ns.subnet, ns.dnsmasq_pid)
            ns.dnsmasq_pid = None
        ns.status = "stopped"

    # -- lab operations
    def deploy(self, name: str) -> LabState:
        with _locks[name]:
            lab, resolved, owned, wiring = self._context(name)
            state = load_state(name)
            if state.status in ("running", "deploying"):
                raise EngineError(f"Il lab '{name}' è già avviato: fai prima destroy")
            state.status = "deploying"
            state.error = None
            state.link_bridges = owned
            for n in resolved.values():
                prev = state.nodes.get(n.name)
                state.nodes[n.name] = NodeState(
                    kind=n.kind,
                    console_port=prev.console_port if prev and prev.kind == n.kind else None,
                    vnc_display=prev.vnc_display if prev and prev.kind == n.kind else None,
                    iol_id=prev.iol_id if prev and prev.kind == n.kind else None,
                )
            # forget nodes that no longer exist in the definition
            for gone in set(state.nodes) - set(resolved):
                state.nodes.pop(gone)
            save_state(name, state)
            try:
                self._ensure_bridges(owned)
                # bridges/externals first, then VMs and containers
                order = sorted(resolved.values(), key=lambda n: n.kind not in ("bridge", "external", "nat", "vxlan"))
                for rn in order:
                    ns = state.nodes[rn.name]
                    try:
                        self._start_node(name, rn, ns, wiring.get(rn.name, {}), self._startup_config(lab, rn))
                    except Exception as exc:
                        ns.status = "error"
                        ns.error = str(exc)
                    save_state(name, state)
                self._apply_impairments(lab, state)
                self._apply_vlans(lab, state)
                errors = [f"{n}: {s.error}" for n, s in state.nodes.items() if s.status == "error"]
                state.status = "running"
                state.error = "\n".join(errors) or None
            except Exception as exc:
                state.status = "error"
                state.error = str(exc)
                save_state(name, state)
                raise EngineError(str(exc)) from exc
            save_state(name, state)
            return state

    def destroy(self, name: str) -> LabState:
        with _locks[name]:
            self.captures.stop_where(name)
            self.wireshark.stop_where(name)
            state = load_state(name)
            for ns in state.nodes.values():
                try:
                    self._stop_node(ns)
                except Exception as exc:  # keep going, best effort
                    ns.error = str(exc)
            # leftover containers (e.g. state file lost)
            if shutil.which("docker") or self.r.dry_run:
                ids = self.r.run(["docker", "ps", "-aq", "-f", f"label=netso.lab={name}"],
                                 check=False, fake="")
                for cid in ids.split():
                    self.r.run(["docker", "rm", "-f", cid], check=False)
            for br in state.link_bridges:
                net.delete_iface(self.r, br)
            state.link_bridges = []
            state.status = "stopped"
            state.error = None
            save_state(name, state)
            return state

    # -- node operations
    def start_node(self, name: str, node: str) -> NodeState:
        with _locks[name]:
            lab, resolved, owned, wiring = self._context(name)
            if node not in resolved:
                raise EngineError(f"Nodo '{node}' inesistente")
            state = load_state(name)
            self._ensure_bridges(owned)
            state.link_bridges = sorted(set(state.link_bridges) | set(owned))
            ns = state.nodes.get(node) or NodeState(kind=resolved[node].kind)
            if ns.status == "running":
                self._stop_node(ns)
            try:
                self._start_node(name, resolved[node], ns, wiring.get(node, {}),
                                 self._startup_config(lab, resolved[node]))
            except Exception as exc:
                ns.status = "error"
                ns.error = str(exc)
            state.nodes[node] = ns
            if ns.status == "running":
                self._apply_impairments(lab, state)
                self._apply_vlans(lab, state)
            if state.status == "stopped":
                state.status = "running"
            save_state(name, state)
            if ns.status == "error":
                raise EngineError(ns.error or "errore")
            return ns

    def stop_node(self, name: str, node: str) -> NodeState:
        with _locks[name]:
            state = load_state(name)
            ns = state.nodes.get(node)
            if ns is None:
                raise EngineError(f"Nodo '{node}' non avviato")
            self.captures.stop_where(name, node)
            self.wireshark.stop_where(name, node)
            self._stop_node(ns)
            save_state(name, state)
            return ns

    def reapply_conditioning(self, name: str) -> LabState:
        """Re-read the lab and push link conditioning onto the running topology."""
        with _locks[name]:
            lab = topo.load_lab(name)
            state = load_state(name)
            if state.status == "running":
                self._apply_impairments(lab, state)
                self._apply_vlans(lab, state)
                save_state(name, state)
            return state

    def wipe_node(self, name: str, node: str) -> None:
        with _locks[name]:
            state = load_state(name)
            ns = state.nodes.get(node)
            if ns and ns.status == "running":
                raise EngineError("Ferma il nodo prima di azzerarlo")
            self.qemu.wipe(name, node)
            self.dynamips.wipe(name, node)
            self.iol.wipe(name, node)

    def stop_lab(self, name: str) -> LabState:
        """Stop every running node (leaves the networks in place); the lab becomes «fermo»."""
        with _locks[name]:
            state = load_state(name)
            for node, ns in list(state.nodes.items()):
                if ns.status == "running":
                    try:
                        self.captures.stop_where(name, node)
                        self.wireshark.stop_where(name, node)
                        self._stop_node(ns)
                    except Exception:  # keep going even if one node fails to stop
                        ns.status = "stopped"
            state.status = "stopped"
            save_state(name, state)
            return state

    # -- status
    @staticmethod
    def _lab_status(state: LabState) -> str:
        """A lab is «avviato» only while something is actually running."""
        if state.status == "deploying":
            return "deploying"
        if any(n.status == "running" for n in state.nodes.values()):
            return "running"
        if any(n.status == "error" for n in state.nodes.values()):
            return "error"
        return "stopped"

    def refresh(self, name: str) -> LabState:
        state = load_state(name)
        changed = False
        for ns in state.nodes.values():
            if ns.status != "running":
                continue
            alive = True
            if ns.kind in PROCESS_KINDS:
                alive = self.qemu.is_running(ns)
            elif ns.kind == "docker":
                alive = self.docker.is_running(ns)
            if not alive:
                ns.status = "stopped"
                changed = True
        new_status = self._lab_status(state)   # derive the lab status from the nodes
        if new_status != state.status:
            state.status = new_status
            changed = True
        if changed:
            save_state(name, state)
        return state

    def guest_ip(self, name: str, node: str) -> str:
        ns = load_state(name).nodes.get(node)
        if ns is None or ns.status != "running":
            raise EngineError("Il nodo non è in esecuzione")
        return _guest_ip(name, node, ns)

    def console_command(self, name: str, node: str) -> list[str]:
        state = load_state(name)
        ns = state.nodes.get(node)
        if ns is None or ns.status != "running":
            raise EngineError("Il nodo non è in esecuzione")
        if ns.kind == "docker":
            shell = None
            try:
                lab = topo.load_lab(name)
                n = next(x for x in lab.nodes if x.name == node)
                shell = topo.load_templates()[n.template].shell
            except Exception:
                pass
            return self.docker.console_command(ns, shell)
        if ns.kind in PROCESS_KINDS and ns.console_port:
            return self.qemu.console_command(ns)
        if ns.console == "ssh":
            ip = self.guest_ip(name, node)
            # PuTTY-like: ask the user name, then ssh to the guest's management address
            return ["sh", "-c", 'printf "login as: "; read u; exec ssh -o StrictHostKeyChecking=no '
                    '-o UserKnownHostsFile=/dev/null -l "$u" "$0"', ip]
        raise EngineError("Questo nodo non ha una consolle testuale (usa VNC)")


GUEST_PORTS = {"rdp": 3389, "rdp-tls": 3389, "ssh": 22, "http": 80, "https": 443, "winbox": 8291}


def _guest_ip(lab: str, node: str, ns: NodeState) -> str:
    ip = ns.mgmt_ip
    if not ip:
        try:
            ip = topo.load_lab(lab).node(node).mgmt_ip
        except Exception:
            ip = None
    if not ip:
        raise EngineError(f"{node}: per questa consolle serve l'IP di gestione del nodo (Modifica nodo → Gestione: "
                          "IP e interfaccia collegata alla rete di gestione)")
    return ip


def host_info() -> dict:
    mem_kb = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_kb = int(line.split()[1])
    except OSError:
        pass
    return {
        "hostname": socket.gethostname(),
        "cpus": os.cpu_count(),
        "memory_mb": mem_kb // 1024,
        "kvm": os.access("/dev/kvm", os.R_OK | os.W_OK),
        "docker": shutil.which("docker") is not None,
        "qemu": shutil.which(settings.qemu_binary) is not None,
        "telnet": shutil.which("telnet") is not None,
        "dry_run": settings.dry_run,
    }


engine = Engine()
