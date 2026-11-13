"""Data models: templates, lab topology, runtime state."""
from __future__ import annotations

import re
import secrets
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
Kind = Literal["qemu", "docker", "bridge", "external", "nat", "vxlan", "dynamips", "iol"]
# Console of a VM (EVE-NG/PNETLab "primary console"): telnet = serial, vnc/spice = screen,
# the others reach a service of the guest on its management IP (RDP in the browser via guacd, SSH in a terminal,
# HTTP/HTTPS/WinBox through a port forwarded by the NetSO server)
VM_CONSOLES = ("telnet", "vnc", "rdp", "rdp-tls", "ssh", "http", "https", "winbox", "spice")
VmConsole = Literal["telnet", "vnc", "rdp", "rdp-tls", "ssh", "http", "https", "winbox", "spice"]
PROCESS_KINDS = ("qemu", "dynamips", "iol")  # host processes with taps and a telnet/VNC console
SHARED_KINDS = ("bridge", "external", "nat", "vxlan")  # many links, cosmetic port names


# Dynamips adapters: name -> (interface prefix, ports)
DYNAMIPS_ADAPTERS = {
    "C7200-IO-FE": ("Fa", 1), "C7200-IO-2FE": ("Fa", 2), "C7200-IO-GE-E": ("Gi", 1),
    "PA-FE-TX": ("Fa", 1), "PA-2FE-TX": ("Fa", 2), "PA-GE": ("Gi", 1), "PA-4E": ("E", 4), "PA-8E": ("E", 8),
    "GT96100-FE": ("Fa", 2), "NM-1FE-TX": ("Fa", 1), "NM-4E": ("E", 4), "NM-16ESW": ("Fa", 16),
}
DYNAMIPS_PLATFORMS = ("c7200", "c3725", "c3745", "c2691")


def parse_vlans(text: str) -> list[tuple[int, int]]:
    """"10,20-30" -> [(10, 10), (20, 30)]"""
    out = []
    for part in [p.strip() for p in text.replace(" ", "").split(",") if p.strip()]:
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if not (1 <= lo <= hi <= 4094):
            raise ValueError(f"VLAN '{part}' invalid (1-4094)")
        out.append((lo, hi))
    if not out:
        raise ValueError("Empty VLAN list")
    return out


def _check_name(value: str, what: str) -> str:
    if not NAME_RE.match(value):
        raise ValueError(
            f"{what} '{value}' invalid: must start with a letter and contain "
            "only letters, numbers, '-' or '_' (max 32 characters)"
        )
    return value


# lab names are only directory names / URL params (bridges and interfaces are hashed), so they may
# contain spaces; '/' stays forbidden (it is the folder separator in the library)
LAB_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,47}$")


def _check_lab_name(value: str) -> str:
    v = (value or "").strip()
    if not LAB_NAME_RE.match(v) or "  " in v:
        raise ValueError(
            f"Lab name '{value}' invalid: starts with a letter; letters, numbers, single spaces, "
            "'-' and '_' (max 48 characters)"
        )
    return v


# node names may carry spaces too (link endpoints split on ':', taps/container names are sanitised)
NODE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,31}$")


def _check_node_name(value: str) -> str:
    v = (value or "").strip()
    if not NODE_NAME_RE.match(v) or "  " in v or ":" in v:
        raise ValueError(
            f"Node name '{value}' invalid: starts with a letter; letters, numbers, single spaces, "
            "'-' and '_' (max 32 characters)"
        )
    return v


class Template(BaseModel):
    """A node type: defaults merged into every node that references it."""

    name: str
    title: str | None = None  # display name, e.g. "Cisco vIOS Router"
    kind: Kind
    description: str = ""
    icon: str = "router"  # router | switch | l3switch | bridge | firewall | server | host | container | cloud
    image: str | None = None  # docker image, or folder under the images dir for qemu
    cpus: int = 1
    ram: int = 512  # MB
    nics: int = 4
    nic_model: str = "virtio-net-pci"
    disk_bus: str = "virtio"  # virtio | ide | sata
    console: Literal["telnet", "vnc", "rdp", "rdp-tls", "ssh", "http", "https", "winbox", "spice", "exec", "none"] = "telnet"
    iface_prefix: str = "eth"
    iface_start: int = 0
    machine: str = "pc"
    cpu_model: str = "host"
    extra_args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cmd: list[str] | None = None
    privileged: bool = True
    clipboard: bool = True  # VMs with VNC/SPICE screen: shared clipboard through the guest agent (spice-vdagent)
    host_bridge: str | None = None  # external: existing bridge on the host
    # Startup-config delivery: how the node receives its boot configuration
    #   disk = small FAT disk with config_file on it (IOSv / IOSvL2 read ios_config.txt)
    #   iso  = CD-ROM with config_file on it (CSR/C8000v iosxe_config.txt, config-drive...)
    #   copy = docker cp the config to the absolute path config_file before starting
    config_method: Literal["none", "disk", "iso", "copy", "nvram"] = "none"  # nvram = Dynamips -C / IOL -c
    config_file: str | None = None
    config_label: str = "config"  # volume label of the disk/iso
    # Explicit interface names (CML node definitions, GNS3 port formats); otherwise prefix + number
    interfaces: list[str] | None = None
    # Files the image folder is expected to hold (from a GNS3 appliance / CML image definition): for checksums
    image_files: list[dict] | None = None
    source: str | None = None  # where the template came from (CML node definition, GNS3 appliance...)
    assets: dict[str, str] | None = None  # containers: {path in container: file in netso/assets} copied before start
    # Saving the running config back into the lab:
    #   save_cmd  containers: command run with docker exec, its output is the config (e.g. vtysh -c "show running-config")
    #   save_cli  VMs: commands typed on the serial console; the output of the last one is the config
    slots: list[str] | None = None   # Dynamips: adapter per slot, e.g. [C7200-IO-FE, PA-2FE-TX]
    idle_pc: str | None = None      # Dynamips: idle-PC value for this IOS image (keeps the CPU low)
    save_cmd: list[str] | None = None
    save_cli: list[str] | None = None
    save_enable: bool = False  # VMs: type "enable" first if the prompt ends with ">"
    ansible_os: str | None = None  # ansible_network_os for the generated inventory
    shell: list[str] | None = None  # containers: what the console runs (e.g. ["sr_cli"], ["Cli"]); default bash/sh
    console_user: str | None = None  # SSH user for the "console in the PC terminal" launcher (default root)
    vendor: str | None = None

    @field_validator("name")
    @classmethod
    def _tname(cls, v: str) -> str:
        return _check_name(v, "Template name")

    @model_validator(mode="after")
    def _dynamips(self):
        if self.kind == "dynamips":
            if self.machine not in DYNAMIPS_PLATFORMS:
                raise ValueError(f"Dynamips platform '{self.machine}' (use {', '.join(DYNAMIPS_PLATFORMS)})")
            bad = [a for a in self.slots or [] if a and a not in DYNAMIPS_ADAPTERS]
            if bad:
                raise ValueError(f"Unknown Dynamips adapters: {', '.join(bad)}")
            self.nics = len(self.interface_names())
        if self.kind == "iol" and not 1 <= self.nics <= 16:
            raise ValueError("IOL: 1 to 16 interfaces (4 per group, e0/0 … e3/3)")
        return self

    @model_validator(mode="after")
    def _ifaces(self):
        if self.interfaces is not None:
            if len(set(self.interfaces)) != len(self.interfaces) or not all(self.interfaces):
                raise ValueError("Interface names must all be distinct and non-empty")
            if self.kind == "docker" and not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", i) for i in self.interfaces):
                raise ValueError("Container interfaces: max 15 characters from letters, numbers, _ . - (no /)")
        return self

    def interface_names(self, nics: int | None = None) -> list[str]:
        n = nics if nics is not None else self.nics
        if self.kind == "dynamips":  # fixed by the adapters in the slots
            return [f"{p}{slot}/{port}" for slot, a in enumerate(self.slots or []) if a
                    for p, count in [DYNAMIPS_ADAPTERS[a]] for port in range(count)]
        if self.kind in SHARED_KINDS:
            return [f"port{i}" for i in range(1, n + 1)]
        if self.kind == "iol":  # Ethernet<bay>/<unit>, 4 per bay
            return [f"e{i // 4}/{i % 4}" for i in range(n)]
        if self.interfaces:
            names = list(self.interfaces[:n])
            # more NICs than named interfaces: continue the numbering of the last name
            last = self.interfaces[-1]
            m = re.match(r"(.*?)(\d+)$", last)
            k = 1
            while len(names) < n:
                names.append(f"{m.group(1)}{int(m.group(2)) + k}" if m else f"{last}-{k}")
                k += 1
            return names
        if self.kind == "docker" and self.iface_prefix == "eth" and self.iface_start == 0:
            # eth0 is the docker management interface
            return [f"eth{i}" for i in range(1, n + 1)]
        return [f"{self.iface_prefix}{self.iface_start + i}" for i in range(n)]


class Node(BaseModel):
    name: str
    template: str
    # Optional per-node overrides of the template
    image: str | None = None
    cpus: int | None = None
    ram: int | None = None
    nics: int | None = None
    env: dict[str, str] | None = None
    cmd: list[str] | None = None
    extra_args: list[str] | None = None
    host_bridge: str | None = None
    description: str | None = None
    icon: str | None = None          # icon override (router, switch, firewall, ...)
    nic_model: str | None = None     # QEMU NIC model override (e1000, virtio-net-pci, vmxnet3...)
    console: VmConsole | None = None  # QEMU console override
    mac: str | None = None           # first MAC; the others follow (+1 per interface)
    # Boot with the config of the lab's active config set (EVE-NG "startup-config ON")
    startup_config: bool = False
    # Canvas (web UI only)
    x: float = 0
    y: float = 0
    ports: dict[str, dict] | None = None  # switch: {port1: {mode: access, vlan: 10}, port2: {mode: trunk, vlans: "10,20-30", native: 1}}
    remote: str | None = None        # VXLAN: IP of the other NetSO server
    vni: int | None = None           # VXLAN: network id, the same on both servers
    dstport: int | None = None       # VXLAN: UDP port (default 4789)
    mgmt_ip: str | None = None       # management address (containers: static IP on the mgmt network; VMs: as configured inside)
    mgmt_iface: str | None = None    # VMs: interface plugged into the management network
    subnet: str | None = None        # NAT cloud: lab-side network, e.g. 10.99.0.0/24 (default 172.31.x.0/24)
    size: int | None = None          # icon size in % (default 100)
    pinned: bool = False             # position locked on the canvas

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_node_name(v)

    @field_validator("mac")
    @classmethod
    def _mac(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = v.strip().lower().replace("-", ":")
        if not re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", v):
            raise ValueError("Invalid MAC (format aa:bb:cc:dd:ee:ff)")
        if int(v[:2], 16) & 1:
            raise ValueError("The first MAC cannot be multicast (odd first byte)")
        return v

    @field_validator("ports")
    @classmethod
    def _ports(cls, v: dict | None) -> dict | None:
        if not v:
            return None
        for port, cfg in v.items():
            mode = (cfg or {}).get("mode", "access")
            if mode == "access":
                vid = int(cfg.get("vlan", 1))
                if not 1 <= vid <= 4094:
                    raise ValueError(f"{port}: VLAN {vid} out of range 1-4094")
            elif mode == "trunk":
                parse_vlans(str(cfg.get("vlans", "1-4094")))
                if not 1 <= int(cfg.get("native", 1)) <= 4094:
                    raise ValueError(f"{port}: native VLAN out of range 1-4094")
            else:
                raise ValueError(f"{port}: unknown mode '{mode}' (access or trunk)")
        return v

    @field_validator("remote")
    @classmethod
    def _remote(cls, v: str | None) -> str | None:
        if not v:
            return None
        import ipaddress
        return str(ipaddress.ip_address(v))

    @field_validator("vni")
    @classmethod
    def _vni(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 16777215:
            raise ValueError("VNI between 1 and 16777215")
        return v

    @field_validator("mgmt_ip")
    @classmethod
    def _mgmt_ip(cls, v: str | None) -> str | None:
        if not v:
            return None
        import ipaddress
        return str(ipaddress.IPv4Address(v.split("/")[0]))

    @field_validator("subnet")
    @classmethod
    def _subnet(cls, v: str | None) -> str | None:
        if not v:
            return None
        import ipaddress
        net = ipaddress.ip_network(v, strict=False)
        if net.version != 4 or net.prefixlen > 29:
            raise ValueError("NAT subnet: IPv4 with prefix up to /29 (e.g. 10.99.0.0/24)")
        return str(net)

    @field_validator("size")
    @classmethod
    def _size(cls, v: int | None) -> int | None:
        if v is not None and not (40 <= v <= 300):
            raise ValueError("Icon size between 40% and 300%")
        return v


class Endpoint(BaseModel):
    node: str
    iface: str

    @classmethod
    def parse(cls, raw: str) -> "Endpoint":
        if ":" not in raw:
            raise ValueError(f"Endpoint '{raw}' invalid: use the format node:interface")
        node, iface = raw.split(":", 1)
        return cls(node=node.strip(), iface=iface.strip())

    def __str__(self) -> str:
        return f"{self.node}:{self.iface}"


class Link(BaseModel):
    endpoints: list[str]
    # Optional link conditioning (applied with `tc netem` on each endpoint's
    # host-side interface). None = leave the wire untouched.
    delay_ms: float | None = None   # one-way latency added on each direction
    jitter_ms: float | None = None  # random variation around the delay
    loss_pct: float | None = None   # packet loss percentage (0-100)
    rate_kbit: int | None = None    # bandwidth cap in kbit/s
    # Look on the canvas only (no effect on the host)
    color: str | None = None        # "#rrggbb"; None = theme colour
    style: Literal["solid", "dashed", "dotted"] | None = None
    width: int | None = None        # stroke width 1-8
    shape: Literal["straight", "curved", "elbow"] | None = None
    bend: int | None = None         # curvature for curved/elbow shapes, −100..100 (None = default)
    label: str | None = None        # free text shown in the middle of the link

    @field_validator("endpoints")
    @classmethod
    def _two(cls, v: list[str]) -> list[str]:
        if len(v) != 2:
            raise ValueError("Each link must have exactly 2 endpoints (for more nodes use a 'bridge' node)")
        for e in v:
            Endpoint.parse(e)
        return v

    @field_validator("loss_pct")
    @classmethod
    def _loss(cls, v: float | None) -> float | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("loss_pct must be between 0 and 100")
        return v

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("invalid link color (use #rrggbb)")
        return v

    @field_validator("width")
    @classmethod
    def _width(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 8):
            raise ValueError("link width between 1 and 8")
        return v

    @field_validator("label")
    @classmethod
    def _label(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if len(v) > 40:
            raise ValueError("link label: maximum 40 characters")
        return v or None

    @field_validator("delay_ms", "jitter_ms", "rate_kbit")
    @classmethod
    def _positive(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("link parameters cannot be negative")
        return v

    @property
    def has_impairments(self) -> bool:
        return any(x is not None for x in (self.delay_ms, self.jitter_ms, self.loss_pct, self.rate_kbit))

    def parsed(self) -> tuple[Endpoint, Endpoint]:
        a, b = self.endpoints
        return Endpoint.parse(a), Endpoint.parse(b)


class Annotation(BaseModel):
    """Drawing on the canvas: text, rectangle, ellipse, line/arrow or picture (no effect on the network)."""
    id: str = Field(default_factory=lambda: "a" + secrets.token_hex(6))
    type: Literal["text", "rect", "ellipse", "line", "image"]
    x: float = 0
    y: float = 0
    w: float = 160
    h: float = 80
    text: str = ""
    color: str = "#e3e7f4"        # text / border / line colour
    fill: str | None = None        # background (rect, ellipse, text)
    border: Literal["solid", "dashed", "dotted", "none"] = "solid"
    border_width: int = 2
    radius: int = 8                # rectangle corners
    font_size: int = 14
    bold: bool = False
    italic: bool = False
    align: Literal["left", "center", "right"] = "center"
    opacity: float = 1.0
    arrow: Literal["none", "end", "start", "both"] = "end"  # line
    flip: bool = False             # line: from top-right to bottom-left instead
    image: str | None = None       # data: URL of a picture
    z: int = -1                    # -1 behind the devices, 1 above

    @model_validator(mode="before")
    @classmethod
    def _text_borderless(cls, data):
        if isinstance(data, dict) and data.get("type") == "text" and "border" not in data:
            data = {**data, "border": "none"}  # plain text: no frame unless asked
        return data

    @field_validator("image")
    @classmethod
    def _img(cls, v: str | None) -> str | None:
        if v and (not v.startswith("data:image/") or len(v) > 3_000_000):
            raise ValueError("Invalid or too large image (max ~2 MB)")
        return v


class Lab(BaseModel):
    name: str
    description: str = ""
    author: str = ""
    config_set: str = "default"  # active startup-config set
    instructions: str = ""       # lab guide (objective, steps, solution) shown in the UI
    notes: str = ""              # free notes on a hand-built lab (shown instead of the guide)
    generated: bool = False      # built by the scenario generator (so it carries a guide)
    nodes: list[Node] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_lab_name(v)

    @field_validator("config_set")
    @classmethod
    def _set(cls, v: str) -> str:
        return _check_name(v, "Config set name")

    @model_validator(mode="after")
    def _unique(self) -> "Lab":
        seen: set[str] = set()
        for n in self.nodes:
            if n.name in seen:
                raise ValueError(f"Duplicate node: {n.name}")
            seen.add(n.name)
        return self

    def node(self, name: str) -> Node:
        for n in self.nodes:
            if n.name == name:
                return n
        raise KeyError(name)


class ResolvedNode(BaseModel):
    """A node with its template merged in: everything the drivers need."""

    name: str
    template: str
    kind: Kind
    icon: str
    image: str | None
    cpus: int
    ram: int
    nics: int
    nic_model: str
    disk_bus: str
    console: str
    interfaces: list[str]
    machine: str
    cpu_model: str
    extra_args: list[str]
    env: dict[str, str]
    cmd: list[str] | None
    privileged: bool
    host_bridge: str | None
    clipboard: bool = True
    shell: list[str] | None = None
    assets: dict[str, str] | None = None
    ports: dict[str, dict] | None = None
    subnet: str | None = None
    mgmt_ip: str | None = None
    mgmt_iface: str | None = None
    remote: str | None = None
    vni: int | None = None
    dstport: int | None = None
    slots: list[str] | None = None
    idle_pc: str | None = None
    mac: str | None = None
    config_method: str = "none"
    config_file: str | None = None
    config_label: str = "config"
    startup_config: bool = False


class NodeState(BaseModel):
    kind: Kind
    status: Literal["stopped", "running", "error"] = "stopped"
    pid: int | None = None
    container: str | None = None
    console_port: int | None = None
    vnc_display: int | None = None
    console: str | None = None  # console type the node was started with
    spice_port: int | None = None
    iol_id: int | None = None  # IOL instance number (1-511), unique on the host
    taps: dict[str, str] = Field(default_factory=dict)  # iface -> tap name
    veths: dict[str, str] = Field(default_factory=dict)  # iface -> host-side veth
    bridge: str | None = None  # bridge/external nodes
    subnet: str | None = None  # NAT cloud network
    dnsmasq_pid: int | None = None
    mgmt_ip: str | None = None  # address on the management network
    tunnel: str | None = None  # VXLAN device
    error: str | None = None


class LabState(BaseModel):
    status: Literal["stopped", "deploying", "running", "error"] = "stopped"
    nodes: dict[str, NodeState] = Field(default_factory=dict)
    link_bridges: list[str] = Field(default_factory=list)  # bridges owned by the lab
    error: str | None = None
