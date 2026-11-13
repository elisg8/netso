# NetSO

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![React](https://img.shields.io/badge/ui-React%2019-61dafb.svg)

Open-source platform for network labs: run **QEMU/KVM VMs** (Cisco, Fortinet, VyOS, Linux…) and **Docker containers** (FRR, Linux hosts…) in the same lab. Draw the topology in your browser or define it in YAML, and every node gets its own console in the browser. It can all be driven from the command line or via the REST API too — a self-hosted network simulation.

![NetSO — topology editor](docs/screenshot.png)

## Features

**Node types**
- **QEMU/KVM VMs** with a copy-on-write overlay (the base image is never touched), telnet serial console, VNC, and SPICE.
- **Docker containers** (FRR, Linux hosts, cEOS, SR Linux, cRPD, SONiC…), with a dedicated management network.
- **Bridge switch** with per-port VLANs (802.1Q via `bridge vlan`).
- **NAT node** to the outside world (bridge + IP + iptables MASQUERADE + dnsmasq DHCP).
- Lightweight **VPC** in the VPCS style, to generate traffic and ping.
- Native **Cisco IOL** (your own `.bin` and your own `iourc` license) and **Dynamips** (c7200/c3725).
- **VXLAN** to join labs across different servers.

**Topology editor**
- Browser canvas (draw and connect nodes) **or** definition in **YAML**.
- Automatic interface assignment; **hot-plug**: connect and disconnect cables with the lab running.
- Drawings and annotations (text, rectangles, ellipses, arrows, images), line styles and curvature.
- Original icons in several styles (Modern, Classic 3D, Line, Rack).

**Console in the browser**
- **telnet** serial (xterm), **VNC** (noVNC), **RDP** (Apache Guacamole), **SSH**, **SPICE**, and forwarding for HTTP/HTTPS/WinBox.
- Floating windows on the sheet or docked at the bottom; several consoles open at once.
- **Mac↔VM clipboard** bridge (copy/paste between host and guest).

**Images**
- Image manager: streaming upload, conversion to qcow2, download from a URL, CML refplat ISOs, MD5 checksums, with background jobs.
- Image import from **EVE-NG / PNETLab** (local folder or over SSH+rsync) and **Docker** image management (`pull` with progress, `load` of `.tar`).

**Import / Export**
- Import from **Containerlab, CML, GNS3, EVE-NG (.unl)**; export to **Containerlab** and **CML**.
- Import of **CML** node/image definitions and **GNS3** appliances (`.gns3a`) as templates.

**Automation and scenarios**
- Topology generators: **clos, ring, mesh, star, chain**.
- **Generate lab**: ready-made teaching scenarios (OSPF, BGP, MPLS L3VPN, VRRP, multicast, filters, BGP security…), **multi-vendor** (FRR and Cisco IOS).
- **Ansible** inventory / hosts / SSH generated from the lab.

**Config, snapshots, and multi-user**
- **startup-config** sets and saving of the running-config (via `docker exec` or the serial console).
- Lab **snapshots** (topology + config + internal qcow2 snapshots).
- User login (PBKDF2), **admin / user / viewer** roles, per-lab permissions (owner/edit/view) and sharing; multiple NetSO servers.

**Tools**
- **Packet capture** with Wireshark in the browser.
- **NetSO Assistant** (AI): an assistant that reads the topology and **runs read-only commands on the nodes** of a running lab (local providers such as Ollama).
- `netso` CLI and **REST API + WebSocket**.

> ⚠️ **Project status.** NetSO is young and has mostly been tried in *dry-run* mode during development: the first runs on real hardware may need fixes. Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Requirements

- Bare-metal Linux server: Ubuntu 22.04/24.04 or Debian 12
- CPU with VT-x/AMD-V enabled in the BIOS (`/dev/kvm` must exist)
- Enough RAM for your nodes (e.g. an IOSv uses 768 MB, a Cat8000v 4 GB)

## Installation

```bash
unzip netso.zip && cd netso
sudo ./install.sh
```

When it finishes, the script prints the address (`http://SERVER-IP:8080`) and the **access token**, which you can also find in `/etc/netso.env`. The service is called `netso` (`systemctl status netso`, `journalctl -u netso -f`).

## VM images

Images are not included: get them from the vendor with the appropriate license (for example IOSv/IOSvL2 from a CML subscription, FortiGate from the Fortinet portal). Each image goes in its own folder, named after the template:

```
/opt/netso/images/
├── cisco-iosv/      virtioa.qcow2
├── cisco-iosvl2/    virtioa.qcow2
├── cisco-c8000v/    virtioa.qcow2
├── fortigate/       virtioa.qcow2
├── vyos/            virtioa.qcow2
└── linux-vm/        virtioa.qcow2
```

If a folder contains several disks, they are attached in alphabetical order. Each node works on a copy-on-write overlay: the base image is never touched, and "Wipe disks" returns the node to factory state. Docker images, on the other hand, are downloaded automatically on first start.

## Using it from the browser

1. Create a new lab and drag devices from the palette on the left.
2. To connect two nodes, drag from the dot at the top of one node to the other: free interfaces are assigned automatically, and clicking the link lets you change them.
3. Press **Deploy** and double-click a running node to open its console.
4. VMs with a graphical console are reachable via VNC at the address shown in the node panel.

Shortcuts: `Del` removes the selection, `Ctrl+S` saves.

## Using it from the command line (labs as code)

```bash
netso templates                    # available node types
netso images                       # QEMU images found
netso deploy /opt/netso/containers-only.yml
netso list
netso console containers-only h1   # console in the terminal
netso inspect containers-only      # status, telnet/VNC ports
netso destroy containers-only      # add --delete to remove it
```

Format of a lab:

```yaml
name: ospf-mix
description: Two IOSv, one FRR, and two hosts on a switch
nodes:
  - {name: r1,   template: cisco-iosv}
  - {name: r2,   template: cisco-iosv, ram: 1024}   # template override
  - {name: frr1, template: frr}
  - {name: sw1,  template: bridge}
  - {name: pc1,  template: linux-host}
  - {name: pc2,  template: linux-host}
links:
  - endpoints: [r1:Gi0/0, r2:Gi0/0]
  - endpoints: [r1:Gi0/1, frr1:eth1]
  - endpoints: [r2:Gi0/1, frr1:eth2]
  - endpoints: [frr1:eth3, sw1:port1]
  - endpoints: [pc1:eth1, sw1:port2]
  - endpoints: [pc2:eth1, sw1:port3]
```

Every link is a point-to-point cable. To put several nodes on the same segment use a `bridge` node, and to reach the real network use an `external` node bound to a bridge already present on the host (e.g. `br0`).

### Link conditioning

Every link can simulate a real network with delay, jitter, packet loss, and limited bandwidth (via `tc netem`):

```yaml
links:
  - endpoints: [r1:Gi0/0, r2:Gi0/0]
    delay_ms: 50        # latency added per direction (RTT ≈ 100 ms)
    jitter_ms: 10       # random variation around the delay
    loss_pct: 2         # 2% of packets dropped
    rate_kbit: 1000     # maximum bandwidth 1 Mbit/s
```

The fields are all optional (empty = ideal cable). In the browser they are set in the link panel; with the lab running, the «Apply now» button activates them live without restarting the nodes.

In containers, `eth0` is reserved for the Docker management network (`netso-mgmt`, with Internet access) and the lab interfaces start from `eth1`.

## Custom templates

Each `.yml` file in `/opt/netso/templates/` adds a node type or overrides the built-in one with the same name. Example:

```yaml
name: cisco-ftdv
kind: qemu
description: Cisco FTDv
icon: firewall          # router | switch | firewall | server | host | cloud
image: cisco-ftdv       # folder in /opt/netso/images
cpus: 4
ram: 8192
nics: 6
nic_model: virtio-net-pci   # or e1000, vmxnet3...
disk_bus: virtio            # virtio | ide | sata
console: telnet             # telnet | vnc
iface_prefix: Gi0/
iface_start: 0
extra_args: []              # extra QEMU arguments
```

For a container use `kind: docker` instead, specifying `image:` (Docker image), `cmd:`, and any `env:`.

## REST API

The interactive documentation is at `http://SERVER-IP:8080/docs`. Calls require the `Authorization: Bearer <token>` header.

| Method | Path | Function |
|---|---|---|
| GET | `/api/labs` | list of labs |
| POST | `/api/labs` | create a lab (`{"name":...}` or `{"yaml":...}`) |
| GET/PUT | `/api/labs/{lab}` and `/api/labs/{lab}/yaml` | read or edit the definition |
| POST | `/api/labs/{lab}/deploy` and `/destroy` | start or stop the whole lab |
| POST | `/api/labs/{lab}/nodes/{node}/start`, `stop`, `wipe` | single node |
| WS | `/api/labs/{lab}/nodes/{node}/console?token=...` | console |

## How it works

- **Network**: every link is a small Linux bridge. VMs attach to it through tap interfaces, containers through veth pairs moved into their namespace. The bridges also forward LLDP, LACP, and STP, so they behave like a real cable.
- **VMs**: `qemu-system-x86_64` with KVM, a serial console on telnet (ports from 32769) and VNC (ports from 5910).
- **State**: `/var/lib/netso/labs/<lab>/` contains `lab.yml`, `state.json`, and the disk overlays.

## Security

NetSO runs as root, because it has to create network interfaces and use KVM and Docker. So keep it on a trusted lab network and leave the token enabled. For remote access it is better to go through a VPN or an SSH tunnel (`ssh -L 8080:localhost:8080 server`).

## Development

```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
pytest                                                # tests run in dry-run, no root needed
NETSO_DRY_RUN=1 NETSO_HOME=/tmp/ln netso serve  # simulate without touching the host
cd ../frontend && npm install && npm run dev          # UI at http://localhost:5173
```

In `NETSO_DRY_RUN=1` mode, commands to the host are only logged and not executed: handy for trying the UI on any PC.

## Contributing

NetSO is open source and happily accepts contributions: see [CONTRIBUTING.md](CONTRIBUTING.md). The main rule is simple — **no vendor images, binaries, or licenses in the repository**, and no code copied from other projects.

## License

NetSO is distributed under the **Apache-2.0** license: see the [LICENSE](LICENSE) and [NOTICE](NOTICE) files.

## Disclaimer

NetSO is an independent project and is **not affiliated with or endorsed by** Cisco, Juniper, Fortinet, Arista, Nokia, VyOS, GNS3, EVE-NG, PNETLab, or any other vendor. All trademarks belong to their respective owners and are referenced only to describe compatibility. **NetSO does not include, distribute, or link to any vendor image, firmware, or license**: each image must be obtained by the user, who must hold a valid license for it. NetSO merely runs images the user already owns. The software is provided "as is", without warranty.
