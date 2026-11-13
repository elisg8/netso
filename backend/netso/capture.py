"""Packet capture on link endpoints.

A capture runs `tcpdump` on the host-side interface of a node's link endpoint
(tap for VMs, veth for containers), so it sees both directions of the wire.
tcpdump writes a pcap file under `labs/<lab>/captures/` and, at the same time,
prints a one-line summary per packet that is parsed and kept in a ring buffer
for the live view in the browser. Captures keep running when nobody watches.

`stream_command` is the "Wireshark live" variant: raw pcap on stdout, meant to
be piped into `wireshark -k -i -`.
"""
from __future__ import annotations

import re
import shlex
import struct
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import network as net
from . import topology as topo
from .hostcmd import Runner, runner as default_runner

BUFFER = 20000          # packets kept in memory per capture for the live view
MAX_FILTER = 300
# pcap global header: magic, v2.4, tz 0, sigfigs 0, snaplen 262144, Ethernet
PCAP_HEADER = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, 1)


class CaptureError(RuntimeError):
    pass


def captures_dir(lab: str) -> Path:
    return topo.lab_dir(lab) / "captures"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-") or "x"


def check_filter(expr: str | None) -> str:
    expr = (expr or "").strip()
    if len(expr) > MAX_FILTER or any(c in expr for c in "\n\r\0"):
        raise CaptureError("Invalid filter")
    if expr.startswith("-"):
        raise CaptureError("The filter cannot start with '-'")
    return expr


# ------------------------------------------------------------------ parsing

_HEAD = re.compile(r"^(\d+\.\d+) (\S+) > (\S+), (.*)$")
_ETHERTYPE = re.compile(r"^ethertype (\S+)(?: \(0x[0-9a-f]+\))?, ")
_VLAN = re.compile(r"^vlan (\d+), p \d+, ethertype (\S+?)(?: \(0x[0-9a-f]+\))?, ")
_FLOW = re.compile(r"^(\S+) > (\S+?):\s(.*)$")
_PORTS = {
    20: "FTP", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 49: "TACACS+", 53: "DNS",
    67: "DHCP", 68: "DHCP", 69: "TFTP", 80: "HTTP", 123: "NTP", 161: "SNMP", 162: "SNMP",
    179: "BGP", 443: "TLS", 500: "ISAKMP", 514: "Syslog", 520: "RIP", 546: "DHCPv6",
    547: "DHCPv6", 646: "LDP", 830: "NETCONF", 1812: "RADIUS", 1813: "RADIUS", 1985: "HSRP",
    3784: "BFD", 4500: "IPsec", 4789: "VXLAN", 5353: "mDNS", 6633: "OpenFlow",
}
_KEYWORDS = [
    ("ICMP6", "ICMPv6"), ("ICMP", "ICMP"), ("OSPF", "OSPF"), ("EIGRP", "EIGRP"),
    ("VRRP", "VRRP"), ("PIM", "PIM"), ("IGMP", "IGMP"), ("GRE", "GRE"), ("ESP", "ESP"),
    ("BGP", "BGP"), ("NTP", "NTP"), ("RIP", "RIP"), ("HSRP", "HSRP"), ("BFD", "BFD"),
    ("bootp", "DHCP"), ("BOOTP", "DHCP"), ("DHCP", "DHCP"), ("VXLAN", "VXLAN"),
    ("LDP", "LDP"), ("SNMP", "SNMP"), ("syslog", "Syslog"), ("RADIUS", "RADIUS"),
]
_L2 = [("STP", "STP"), ("CDP", "CDP"), ("LLDP", "LLDP"), ("LACP", "LACP"),
       ("IS-IS", "IS-IS"), ("ISIS", "IS-IS"), ("DTP", "DTP"), ("VTP", "VTP"),
       ("Loopback", "LOOP"), ("PVST", "STP")]


def _split_port(addr: str, ipv6: bool) -> tuple[str, int | None]:
    host, _, port = addr.rpartition(".")
    if port.isdigit() and host and (ipv6 or host.count(".") == 3):
        return host, int(port)
    return addr, None


def _guess(payload: str, ports: list[int | None], default: str) -> str:
    known = [_PORTS[p] for p in ports if p in _PORTS]
    if payload.startswith("Flags ["):
        # TCP: name it after the well-known port when there is one
        return known[0] if known and not payload.rstrip().endswith("length 0") else "TCP"
    for key, name in _KEYWORDS:
        if payload.startswith(key) or f": {key}" in payload:
            return name
    if known:
        return known[0]
    if payload.startswith("UDP"):
        return "UDP"
    return default


def parse_line(line: str) -> dict | None:
    """Turn a `tcpdump -n -e -tt` line into {t, src, dst, proto, len, info}."""
    m = _HEAD.match(line.strip())
    if not m:
        return None
    ts, smac, dmac, rest = m.groups()
    length = 0
    lm = re.search(r"length (\d+): ", rest)
    if lm:
        length = int(lm.group(1))
        head, info = rest[:lm.start()], rest[lm.end():]
    else:
        head, info = rest, rest
    em = _ETHERTYPE.match(head + ", ")
    ethertype = em.group(1) if em else "802.3"
    vlan = None
    vm = _VLAN.match(info)
    if vm:
        vlan, ethertype, info = int(vm.group(1)), vm.group(2), info[vm.end():]
    src, dst, proto = smac, dmac, ethertype

    if ethertype in ("IPv4", "IPv6"):
        fm = _FLOW.match(info)
        if fm:
            v6 = ethertype == "IPv6"
            src, sp = _split_port(fm.group(1), v6)
            dst, dp = _split_port(fm.group(2), v6)
            payload = fm.group(3)
            proto = _guess(payload, [sp, dp], ethertype)
            if sp is not None:
                src, dst = f"{src}:{sp}", f"{dst}:{dp}"
            info = payload
    elif ethertype == "ARP":
        proto = "ARP"
        info = re.sub(r", length \d+$", "", info)
    else:
        info = re.sub(r"^LLC, .*?ctrl 0x[0-9a-f]+: ", "", info)
        info = re.sub(r"^oui .*?length \d+: ", "", info)
        for key, name in _L2:
            if key in info[:60]:
                proto = name
                break
    if vlan is not None:
        info = f"[vlan {vlan}] {info}"
    return {"t": float(ts), "src": src, "dst": dst, "proto": proto, "len": length,
            "info": info.strip()[:400]}


# ------------------------------------------------------------- sessions

@dataclass
class Capture:
    id: str
    lab: str
    node: str
    iface: str
    host_iface: str
    filter: str
    file: Path
    started: float
    proc: subprocess.Popen | None = None
    stopped: float | None = None
    count: int = 0
    error: str | None = None
    packets: deque = field(default_factory=lambda: deque(maxlen=BUFFER))
    _stop: threading.Event = field(default_factory=threading.Event)

    @property
    def running(self) -> bool:
        return self.stopped is None

    def push(self, pkt: dict) -> None:
        self.count += 1
        pkt["n"] = self.count
        self.packets.append(pkt)

    def since(self, n: int, limit: int = 500) -> list[dict]:
        out = [p for p in self.packets if p["n"] > n]
        return out[:limit]

    def info(self) -> dict:
        size = self.file.stat().st_size if self.file.exists() else 0
        return {"id": self.id, "node": self.node, "iface": self.iface, "filter": self.filter,
                "running": self.running, "format": "pcap", "started": self.started, "stopped": self.stopped,
                "packets": self.count, "size": size, "error": self.error}


def _fake_traffic(cap: Capture) -> None:
    """Dry run: synthesize a plausible packet stream so the UI can be previewed."""
    ma = net.mac_address(cap.lab, cap.node, 0)
    mb = net.mac_address(cap.lab, cap.node + "-peer", 0)
    a, b = "10.0.0.1", "10.0.0.2"
    seq, i = 0, 0
    pattern = [
        f"{ma} > ff:ff:ff:ff:ff:ff, ethertype ARP (0x0806), length 42: Request who-has {b} tell {a}, length 28",
        f"{mb} > {ma}, ethertype ARP (0x0806), length 60: Reply {b} is-at {mb}, length 46",
        "ping",
        f"{ma} > 01:00:5e:00:00:05, ethertype IPv4 (0x0800), length 94: {a} > 224.0.0.5: OSPFv2, Hello, length 60",
        f"{ma} > {mb}, ethertype IPv4 (0x0800), length 74: {a}.40122 > {b}.22: Flags [S], seq 1180, win 64240, options [mss 1460], length 0",
        f"{mb} > {ma}, ethertype IPv4 (0x0800), length 74: {b}.22 > {a}.40122: Flags [S.], seq 7731, ack 1181, win 65160, length 0",
        f"{ma} > 01:80:c2:00:00:0e, ethertype LLDP (0x88cc), length 160: LLDP, length 146: {cap.node}",
        f"{mb} > 01:00:0c:cc:cc:cc, 802.3, length 400: LLC, dsap SNAP (0xaa) Individual, ssap SNAP (0xaa) Command, ctrl 0x03: oui Cisco (0x00000c), pid CDP (0x2000), length 386: CDPv2, ttl: 180s, Device-ID 'peer'",
        f"{mb} > {ma}, ethertype IPv4 (0x0800), length 90: {b}.123 > {a}.123: NTPv4, Server, length 48",
    ]
    while not cap._stop.wait(0.35):
        line = pattern[i % len(pattern)]
        i += 1
        if line == "ping":
            seq += 1
            for s, d, ms, md, kind in ((a, b, ma, mb, "request"), (b, a, mb, ma, "reply")):
                cap.push(parse_line(f"{time.time():.6f} {ms} > {md}, ethertype IPv4 (0x0800), length 98: "
                                    f"{s} > {d}: ICMP echo {kind}, id 7, seq {seq}, length 64"))
            continue
        cap.push(parse_line(f"{time.time():.6f} {line}"))


def _read_output(cap: Capture) -> None:
    assert cap.proc and cap.proc.stdout
    for line in cap.proc.stdout:
        pkt = parse_line(line)
        if pkt:
            cap.push(pkt)
    rc = cap.proc.wait()
    if cap.stopped is None:  # tcpdump died by itself (interface gone, bad filter...)
        cap.stopped = time.time()
        err = (cap.proc.stderr.read() if cap.proc.stderr else "").strip()
        lines = [x for x in err.splitlines() if "listening on" not in x and "packets " not in x]
        if rc not in (0, -15) and lines:
            cap.error = lines[-1]


class CaptureManager:
    def __init__(self, runner: Runner | None = None):
        self.r = runner or default_runner
        self._caps: dict[str, Capture] = {}
        self._lock = threading.Lock()

    def start(self, lab: str, node: str, iface: str, host_iface: str, expr: str = "") -> Capture:
        expr = check_filter(expr)
        with self._lock:
            for cap in self._caps.values():
                if cap.lab == lab and cap.node == node and cap.iface == iface and cap.running:
                    return cap
            stamp = time.strftime("%Y%m%d-%H%M%S")
            cid = f"{_slug(node)}_{_slug(iface)}_{stamp}"
            n = 1
            while (captures_dir(lab) / f"{cid}.pcap").exists() or cid in self._caps:
                n += 1
                cid = f"{_slug(node)}_{_slug(iface)}_{stamp}-{n}"
            f = captures_dir(lab) / f"{cid}.pcap"
            f.parent.mkdir(parents=True, exist_ok=True)
            cap = Capture(cid, lab, node, iface, host_iface, expr, f, time.time())
            # -U -w: pcap flushed per packet; --print: also one summary line per packet
            cmd = ["tcpdump", "-i", host_iface, "-n", "-e", "-tt", "-l", "-s", "0",
                   "-U", "-w", str(f), "--print", *([expr] if expr else [])]
            self.r.log.append(shlex.join(cmd))
            if self.r.dry_run:
                f.write_bytes(PCAP_HEADER)
                threading.Thread(target=_fake_traffic, args=(cap,), daemon=True).start()
            else:
                try:
                    cap.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                text=True, bufsize=1)
                except OSError as exc:
                    raise CaptureError(f"Could not start tcpdump: {exc}") from exc
                threading.Thread(target=_read_output, args=(cap,), daemon=True).start()
            self._caps[cid] = cap
            return cap

    def get(self, lab: str, cid: str) -> Capture | None:
        cap = self._caps.get(cid)
        return cap if cap and cap.lab == lab else None

    def stop(self, lab: str, cid: str) -> None:
        cap = self.get(lab, cid)
        if not cap or not cap.running:
            return
        cap.stopped = time.time()
        cap._stop.set()
        if cap.proc and cap.proc.poll() is None:
            cap.proc.terminate()
            try:
                cap.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                cap.proc.kill()

    def stop_where(self, lab: str, node: str | None = None) -> None:
        for cap in list(self._caps.values()):
            if cap.lab == lab and (node is None or cap.node == node):
                self.stop(lab, cap.id)

    def delete(self, lab: str, cid: str) -> None:
        self.stop(lab, cid)
        with self._lock:
            self._caps.pop(cid, None)
        f = self.file(lab, cid)
        if f.exists():
            f.unlink()

    def file(self, lab: str, cid: str) -> Path:
        """pcap written by tcpdump, or pcapng recorded by the in-browser Wireshark."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", cid):
            raise CaptureError("Invalid capture")
        f = captures_dir(lab) / f"{cid}.pcap"
        ng = f.with_suffix(".pcapng")
        return ng if not f.exists() and ng.exists() else f

    def list(self, lab: str) -> list[dict]:
        out = {c.id: c.info() for c in self._caps.values() if c.lab == lab}
        d = captures_dir(lab)
        if d.is_dir():
            for f in [*d.glob("*.pcap"), *d.glob("*.pcapng")]:
                if f.stem in out:
                    continue
                node, iface = (f.stem.split("_") + ["", ""])[:2]
                st = f.stat()
                out[f.stem] = {"id": f.stem, "node": node, "iface": iface, "filter": "",
                               "running": False, "format": f.suffix[1:], "started": st.st_ctime, "stopped": st.st_mtime,
                               "packets": None, "size": st.st_size, "error": None}
        return sorted(out.values(), key=lambda c: c["started"], reverse=True)

    def stream_command(self, host_iface: str, expr: str = "") -> list[str]:
        """Raw pcap on stdout, for `curl ... | wireshark -k -i -`."""
        expr = check_filter(expr)
        cmd = ["tcpdump", "-i", host_iface, "-n", "-s", "0", "-U", "-w", "-"]
        return cmd + ([expr] if expr else [])
