"""Import the user's own images from an EVE-NG / PNETLab server (or a copy of its folders).

Layout read (formats only, no code copied):
  <addons>/qemu/<type>-<version>/{virtioa,hda,sataa,...}.qcow2   VM images, folder prefix = node type
  <addons>/dynamips/<platform>-*.image|.bin                        Dynamips IOS images
  <addons>/iol/bin/*.bin                                            IOL binaries (run natively; iourc copied only if it
                                                                    has a line for this server's host name)
  <unetlab>/html/templates/{intel,amd}/<type>.yml                   node defaults (cpu, ram, ethernet...)

The source is a local path (e.g. a copied or mounted /opt/unetlab/addons) or `[user@]host:/path` read over
SSH with key authentication; files go straight into <images>/<folder>/ (rsync for remote sources).
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from . import catalog, topology as topo
from .config import settings
from .drivers.dynamips import IOS_SUFFIXES
from .drivers.iol import iol_libdir, iourc_path
from .drivers.qemu import DISK_SUFFIXES
from .hostcmd import Runner
from .images import IMG_RE, ImageError
from .jobs import Job
from .models import VM_CONSOLES, Template

REMOTE_RE = re.compile(r"^(?:([A-Za-z0-9][A-Za-z0-9._-]*)@)?([A-Za-z0-9][A-Za-z0-9.-]*):(/.*)$")
RSYNC_TRIES = 4  # attempts per file; each one resumes from the .part file
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]

# EVE-NG/PNETLab folder prefix -> built-in NetSO device
KNOWN = {"vios": "cisco-iosv", "viosl2": "cisco-iosvl2", "c8000v": "cisco-c8000v", "fortinet": "fortigate",
         "fmc": "cisco-fmc", "vyos": "vyos", "linux": "linux-vm"}
DYN_KNOWN = {"c7200": "cisco-c7200", "c3725": "cisco-c3725"}
# Defaults for common types without a NetSO device, used when the server's own template file is not found
# (icon, cpus, ram MB, nics, interface prefix, first index, nic model). Values to check on first boot.
GUESS = {
    "asav": ("firewall", 1, 2048, 8, "Gi0/", 0, "e1000"),
    "firepower": ("firewall", 4, 8192, 8, "G0/", 0, "e1000"),   # Cisco FTD (names via IFACE_NAMES below)
    "ftdv": ("firewall", 4, 8192, 8, "G0/", 0, "e1000"),
    "ftd": ("firewall", 4, 8192, 8, "G0/", 0, "e1000"),
    "fmc": ("server", 4, 8192, 1, "eth", 0, "e1000"),  # Firepower Management Center: 1 NIC mgmt (8 GB/e1000 come EVE-NG/PNETLab)
    "csr1000vng": ("router", 1, 4096, 4, "Gi", 1, "virtio-net-pci"),
    "csr1000v": ("router", 1, 4096, 4, "Gi", 1, "virtio-net-pci"),
    "xrv": ("router", 1, 3072, 4, "Gi0/0/0/", 0, "e1000"),
    "xrv9k": ("router", 4, 16384, 4, "Gi0/0/0/", 0, "virtio-net-pci"),
    "nxosv9k": ("l3switch", 2, 8192, 8, "Eth1/", 1, "e1000"),
    "titanium": ("l3switch", 2, 3072, 8, "Eth2/", 1, "e1000"),
    "veos": ("l3switch", 1, 2048, 8, "Eth", 1, "e1000"),
    "paloalto": ("firewall", 2, 6656, 4, "eth1/", 1, "virtio-net-pci"),
    "vsrxng": ("firewall", 2, 4096, 4, "ge-0/0/", 0, "virtio-net-pci"),
    "vsrx": ("firewall", 2, 4096, 4, "ge-0/0/", 0, "virtio-net-pci"),
    "mikrotik": ("router", 1, 256, 4, "ether", 1, "e1000"),
    "pfsense": ("firewall", 1, 2048, 4, "vtnet", 0, "virtio-net-pci"),
    "cumulus": ("switch", 1, 1024, 4, "swp", 1, "virtio-net-pci"),
    "win": ("host", 2, 4096, 1, "eth", 0, "e1000"),
    "winserver": ("server", 2, 4096, 1, "eth", 0, "e1000"),
}
# Devices whose NICs don't follow one prefix: the first NIC is management, data ports use another scheme.
# Explicit names, auto-continued by the numbering in the last entry (e.g. G0/0 -> G0/1, G0/2 …),
# matching the PNETLab device_* drivers (Cisco FTD/Firepower: eth0 is mgmt, then G0/0, G0/1 …).
IFACE_NAMES = {
    "firepower": ["eth0 / mgmnt", "G0/0"],
    "ftdv": ["eth0 / mgmnt", "G0/0"],
    "ftd": ["eth0 / mgmnt", "G0/0"],
}
# Linux VMs (EVE "linux" type and the custom folders built from it, e.g. ccie): server icon, graphical console
LINUX = {"linux", "ccie", "ubuntu", "debian", "centos", "kali", "rocky", "alma", "fedora", "redhat", "rhel", "opensuse"}
EVE_ICONS = (("switch l3", "l3switch"), ("switch", "switch"), ("firewall", "firewall"), ("server", "server"),
             ("desktop", "host"), ("pc", "host"), ("cloud", "cloud"), ("router", "router"))


class PnetError(ValueError):
    pass


# ------------------------------------------------------------------ sources

@dataclass
class Source:
    """A local folder or `[user@]host:/path` over SSH."""
    spec: str
    r: Runner

    def __post_init__(self):
        m = REMOTE_RE.match(self.spec.strip())
        self.remote = bool(m) and not Path(self.spec).exists()
        if self.remote:
            user, host, path = m.groups()
            self.target = f"{user}@{host}" if user else host
            self.root = PurePosixPath(path)
        else:
            if not self.spec.startswith("/"):
                raise PnetError("Indica un percorso assoluto (es. /opt/unetlab/addons) oppure utente@host:/percorso")
            self.root = Path(self.spec).resolve()
            if not self.root.is_dir() and not self.r.dry_run:
                raise PnetError(f"Cartella {self.root} inesistente sul server NetSO")

    def _ssh(self, cmd: str) -> str:
        if self.r.dry_run:
            self.r.log.append(f"ssh {self.target} {cmd}")
            return ""
        p = subprocess.run(["ssh", *SSH_OPTS, self.target, cmd], capture_output=True, text=True, timeout=120)
        if p.returncode == 255:
            raise PnetError(f"SSH verso {self.target} non riuscito: {p.stderr.strip()[:300]}. "
                            "Serve l'accesso con chiave: sul server NetSO esegui `sudo ssh-keygen` e "
                            f"`sudo ssh-copy-id {self.target}`")
        return p.stdout

    def files(self, sub: str, depth: int) -> list[tuple[str, int]]:
        """(path relative to root/sub, size) of the files found up to `depth` levels below root/sub."""
        if self.remote:
            d = shlex.quote(str(self.root / sub))
            out = self._ssh(f"[ -d {d} ] && find -L {d} -mindepth 1 -maxdepth {depth} -type f -printf '%P\\t%s\\n'")
            rows = [ln.split("\t") for ln in out.splitlines() if "\t" in ln]
            return [(p, int(s)) for p, s in rows if s.isdigit()]
        base = self.root / sub
        if not base.is_dir():
            return []
        out = []
        for dirpath, dirs, names in os.walk(base):
            rel = Path(dirpath).relative_to(base)
            if len(rel.parts) >= depth:
                dirs[:] = []
            if len(rel.parts) < depth:
                out += [(str(rel / n) if rel.parts else n, (Path(dirpath) / n).stat().st_size) for n in names]
        return out

    def read(self, rel: str) -> str | None:
        if self.remote:
            return self._ssh(f"cat {shlex.quote(str(self.root / rel))} 2>/dev/null") or None
        p = self.root / rel
        try:
            return p.read_text(errors="replace") if p.is_file() else None
        except OSError:
            return None

    def fetch(self, job: Job, rel: str, dest: Path, move: bool, done: int, total: int) -> int:
        """Copy (or move) file root/rel into dest; returns bytes done so far."""
        if self.r.dry_run:
            self.r.log.append(f"{'rsync ' + self.target + ':' if self.remote else ('mv ' if move else 'cp ')}{self.root / rel} -> {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"")
            return done
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        if self.remote:
            # --timeout: a stalled connection fails instead of hanging forever; --partial + --append-verify: a
            # retry (or a later run) continues from the .part file instead of starting over
            cmd = ["rsync", "-s", "-L", "--info=progress2", "--timeout=120", "--partial", "--append-verify",
                   "-e", shlex.join(["ssh", *SSH_OPTS]), f"{self.target}:{self.root / rel}", str(part)]
            for attempt in range(1, RSYNC_TRIES + 1):
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                buf, tail = "", []
                while ch := proc.stdout.read(1):
                    if ch in "\r\n":
                        m = re.match(r"\s*[\d,]+\s+\d+%\s+(\S+/s)?", buf)
                        if m:
                            n = part.stat().st_size if part.exists() else 0
                            retry = f" · tentativo {attempt}/{RSYNC_TRIES}" if attempt > 1 else ""
                            job.update((done + n) / total, f"{dest.parent.name}/{dest.name} · {n >> 20} MB · {m.group(1) or ''}{retry}")
                        elif buf.strip() and not buf.startswith("**"):  # skip OpenSSH's post-quantum banner
                            tail = (tail + [buf.strip()])[-4:]
                        buf = ""
                    else:
                        buf += ch
                if proc.wait() == 0:
                    part.rename(dest)
                    return done + dest.stat().st_size
                if attempt < RSYNC_TRIES:
                    job.update(None, f"{dest.parent.name}/{dest.name} · connessione persa, riprovo tra 15 s ({' '.join(tail)[:120]})")
                    time.sleep(15)
            raise PnetError(f"rsync di {rel} non riuscito dopo {RSYNC_TRIES} tentativi: {' '.join(tail)[:300]}. "
                            "La parte già copiata resta: rilancia l'import per continuare da lì")
        src = self.root / rel
        size = src.stat().st_size
        if move:
            try:
                src.rename(dest)
                return done + size
            except OSError:
                pass  # other filesystem: copy, then delete
        start = done
        with open(src, "rb") as fi, open(part, "wb") as fo:
            while chunk := fi.read(8 << 20):
                fo.write(chunk)
                done += len(chunk)
                job.update(done / total, f"{dest.parent.name}/{dest.name} · {(done - start) >> 20} MB")
        part.rename(dest)
        if move:
            src.unlink()
        return done


# ------------------------------------------------------------------ scan

def _folder_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9._+-]+", "-", name).strip("-.")[:100]
    if not IMG_RE.match(n or ""):
        raise PnetError(f"Nome '{name}' non utilizzabile come cartella")
    return n


def _eve_template(src: Source, prefix: str) -> dict | None:
    for rel in [f"{up}html/templates/{sub}{prefix}.yml" for up in ("../", "") for sub in ("intel/", "amd/", "")] + [f"templates/{prefix}.yml"]:
        txt = src.read(rel)
        if txt:
            try:
                doc = yaml.safe_load(txt)
            except yaml.YAMLError:
                continue
            if isinstance(doc, dict):
                return doc
    return None


def _addons(src: Source) -> str:
    """Relative path of the addons folder: accepts /opt/unetlab, /opt/unetlab/addons or a copy of qemu/."""
    for sub in ("", "addons"):
        if src.files(f"{sub}/qemu".lstrip("/"), 2) or src.files(f"{sub}/dynamips".lstrip("/"), 1) or src.files(f"{sub}/iol/bin".lstrip("/"), 1):
            return sub
    if any("/" in p for p, _ in src.files("", 2)):
        return "."  # the qemu folder itself
    return ""


def _sibling_base(base: str) -> str:
    """Where dynamips/ and iol/ are: next to qemu/ when the source is the qemu folder itself."""
    return ".." if base == "." else base


def scan(spec: str, r: Runner) -> dict:
    src = Source(spec, r)
    templates = topo.load_templates()

    def complete(folder: str, files: list[tuple[str, int]]) -> bool:
        """Every file already copied in full (a copy interrupted half-way is not "present")."""
        d = settings.images / folder
        return all((d / Path(f).name).is_file() and (r.dry_run or (d / Path(f).name).stat().st_size == n) for f, n in files)

    base = _addons(src)
    qdir = "." if base == "." else f"{base}/qemu".lstrip("/")
    base = _sibling_base(base)
    items, notes = [], []
    groups: dict[str, list] = {}
    for rel, size in src.files(qdir, 2):
        if "/" not in rel:
            continue
        folder, fname = rel.split("/", 1)
        if Path(fname).suffix.lower() in DISK_SUFFIXES:
            groups.setdefault(folder, []).append((fname, size))
    for folder in sorted(groups):
        prefix = folder.split("-", 1)[0].lower()
        tpl = KNOWN.get(prefix)
        items.append({"kind": "qemu", "src": folder if qdir == "." else f"{qdir}/{folder}",
                      "folder": _folder_name(folder), "prefix": prefix,
                      "files": [f for f, _ in sorted(groups[folder])], "size": sum(s for _, s in groups[folder]),
                      "template": tpl if tpl in templates else None, "present": complete(_folder_name(folder), groups[folder])})
    for rel, size in src.files(f"{base}/dynamips".lstrip("/"), 1):
        if Path(rel).suffix.lower() not in IOS_SUFFIXES:
            continue
        platform = rel.split("-", 1)[0].lower()
        folder = _folder_name(Path(rel).stem)
        tpl = DYN_KNOWN.get(platform)
        items.append({"kind": "dynamips", "src": f"{base}/dynamips/{rel}".lstrip("/"), "folder": folder, "prefix": platform,
                      "files": [rel], "size": size, "template": tpl if tpl in templates else None, "present": complete(folder, [(rel, size)])})
    for rel, size in src.files(f"{base}/iol/bin".lstrip("/"), 1):
        if not rel.endswith(".bin"):
            continue
        folder = "iol-" + _folder_name(Path(rel).stem)
        tpl = "cisco-iol-l2" if re.search(r"l2|switch", rel, re.I) else "cisco-iol"
        items.append({"kind": "iol", "src": f"{base}/iol/bin/{rel}".lstrip("/"), "folder": folder, "prefix": "iol",
                      "files": [rel], "size": size, "template": tpl if tpl in templates else None,
                      "present": complete(folder, [(rel, size)])})
    iourc = None
    iol_lib = [(rel, size) for rel, size in src.files(f"{base}/iol/lib".lstrip("/"), 1) if "/" not in rel]
    if any(i["kind"] == "iol" for i in items):
        hosts = re.findall(r"(?m)^\s*([^\s=#;\[]+)\s*=\s*[0-9a-fA-F]+\s*;", src.read(f"{base}/iol/bin/iourc".lstrip("/")) or "")
        me = socket.gethostname()
        if me in hosts:
            iourc = src.read(f"{base}/iol/bin/iourc".lstrip("/"))
            notes.append(f"IOL: copio anche la licenza iourc di PNETLab (ha la riga per questo server, '{me}').")
        else:
            notes.append(f"IOL: la licenza iourc di PNETLab {'è per ' + ', '.join(repr(h) for h in hosts) if hosts else 'non è stata trovata'}: "
                         f"vale solo per quel nome host, non per questo server ('{me}'). Carica la tua licenza per '{me}': "
                         "Dispositivi → Immagini → Licenza IOL.")
        if iol_lib:
            notes.append(f"IOL: copio anche le librerie di {base}/iol/lib ({', '.join(r for r, _ in iol_lib)}) "
                         "e le uso all'avvio dei nodi IOL.")
    for it in items:
        if it["template"]:
            it["action"] = f"usa {it['template']}"
        elif it["kind"] in ("dynamips", "iol"):
            it["action"] = "solo immagine (piattaforma senza dispositivo NetSO)"
        else:
            it["action"] = f"nuovo dispositivo '{it['prefix']}'"
    if not items and not notes:
        raise PnetError(f"Nessuna immagine trovata in {spec}: indica /opt/unetlab/addons (o la cartella qemu)")
    return {"items": items, "notes": notes, "remote": src.remote, "iol_lib": iol_lib, "iourc": iourc}


# ------------------------------------------------------------------ import

# options NetSO builds itself in the QEMU command (machine/cpu/mem/display/disks/NICs/console):
# never copy these from PNETLab's qemu_options or they would clash or duplicate devices.
_QEMU_SKIP_VAL = {"-machine", "-cpu", "-smp", "-m", "-name", "-display", "-serial", "-vnc", "-spice",
                  "-pidfile", "-monitor", "-drive", "-netdev", "-cdrom", "-hda", "-hdb", "-hdc", "-hdd",
                  "-chardev", "-device"}
_QEMU_SKIP_FLAG = {"-nographic", "-daemonize", "-enable-kvm"}


def _qemu_from_options(opts: str, machine: str, cpu_model: str) -> tuple[str, str, list[str]]:
    """Map PNETLab/EVE «qemu_options» to NetSO's machine / cpu_model / extra_args (keeping only
    the appliance-specific flags NetSO doesn't already add, e.g. -nodefaults -vga std -rtc base=utc)."""
    toks = (opts or "").split()
    extra: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        has_val = nxt is not None and not nxt.startswith("-")
        if t == "-machine" and has_val:
            m = re.search(r"type=([^,]+)", nxt)
            machine = m.group(1) if m else machine
            i += 2; continue
        if t == "-cpu" and has_val:
            cpu_model = nxt; i += 2; continue
        if t in _QEMU_SKIP_VAL:
            i += 2 if has_val else 1; continue
        if t in _QEMU_SKIP_FLAG:
            i += 1; continue
        extra.append(t)                     # keep this flag…
        if has_val:
            extra.append(nxt); i += 2       # …and its value
        else:
            i += 1
    return machine, cpu_model, extra


def _new_template(src: Source, prefix: str, folder: str, files: list[str], taken: set[str]) -> Template:
    disk = files[0].lower() if files else ""
    bus = "ide" if disk.startswith("hd") else "sata" if disk.startswith("sata") else "virtio"
    eve = _eve_template(src, prefix)
    icon, cpus, ram, nics, ifp, start, nic = GUESS.get(prefix, ("router", 1, 1024, 4, "eth", 0, "e1000"))
    desc = ""
    title = prefix
    interfaces = None
    console = "telnet"
    machine, cpu_model, extra = "pc", "host", []
    if eve:
        title = str(eve.get("description") or eve.get("name") or prefix)[:60]
        cpus, ram = int(eve.get("cpu") or cpus), int(eve.get("ram") or ram)
        nics = int(eve.get("ethernet") or nics)
        nic = str(eve.get("qemu_nic") or nic)
        console = str(eve.get("console") or "telnet").lower()
        console = console if console in VM_CONSOLES else "telnet"  # EVE: telnet, vnc, rdp…
        low = str(eve.get("icon") or "").lower()
        icon = next((v for k, v in EVE_ICONS if k in low), icon)
        m = re.match(r"^(.*)\{(\d)\}$", str(eve.get("eth_format") or ""))
        if m:
            ifp, start = m.group(1), int(m.group(2))
        if isinstance(eve.get("eth_name"), list) and eve["eth_name"]:
            interfaces = [str(x) for x in eve["eth_name"]]
        # the appliance's own QEMU options (needed to boot FTD/firepower, ASAv, …)
        if eve.get("qemu_options"):
            machine, cpu_model, extra = _qemu_from_options(str(eve["qemu_options"]), machine, cpu_model)
    if interfaces is None and prefix in IFACE_NAMES:  # device-specific names (first NIC = management)
        interfaces = list(IFACE_NAMES[prefix])
    if prefix in LINUX:
        icon, console = "server", console if console != "telnet" else "vnc"
        if not eve:
            cpus, ram, nics, ifp, start, nic = 2, 4096, 4, "eth", 0, "virtio-net-pci"
    return Template(name=catalog.free_name(prefix, taken), title=title, kind="qemu", description=desc, icon=icon, image=folder,
                    cpus=cpus, ram=ram, nics=max(1, min(32, nics)), nic_model=nic, disk_bus=bus, console=console,
                    iface_prefix=ifp, iface_start=start, interfaces=interfaces, source="EVE-NG/PNETLab",
                    machine=machine, cpu_model=cpu_model, extra_args=extra)


def run_import(r: Runner, job: Job, spec: str, pick: list[str] | None, move: bool) -> str:
    src = Source(spec, r)
    found = scan(spec, r)
    # images already copied (e.g. by a run that stopped halfway) still get their devices
    selected = [i for i in found["items"] if pick is None or i["src"] in pick or i["present"]]
    chosen = [i for i in selected if not i["present"]]
    if not selected:
        return "Niente da importare (nessuna immagine selezionata)"
    if src.remote and not r.dry_run and not shutil.which("rsync"):
        raise ImageError("Serve 'rsync' sul server NetSO (sudo apt install rsync)")
    total = sum(i["size"] for i in chosen) or 1
    if not (move and not src.remote):  # a full disk makes Proxmox stop/pause the VM: check first
        settings.images.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(settings.images).free
        if total + (2 << 30) > free:
            raise PnetError(f"Spazio insufficiente in {settings.images}: servono {total / 2**30:.1f} GB (+2 GB di margine), "
                            f"liberi {free / 2**30:.1f} GB. Scegli meno immagini o ingrandisci il disco")
    done = 0
    for i in chosen:
        dest = settings.images / i["folder"]
        for f in i["files"]:
            rel = i["src"] if i["kind"] in ("dynamips", "iol") else f"{i['src']}/{f}"
            done = src.fetch(job, rel, dest / Path(f).name, move and not src.remote, done, total)
    # the libraries IOL needs (libcrypto.so.4…) as PNETLab ships them: always copied, never moved
    libs = []
    if found.get("iourc") and not iourc_path().is_file():  # only when it has a line for this host (see scan)
        r.log.append(f"write {iourc_path()}")
        if not r.dry_run:
            iourc_path().parent.mkdir(parents=True, exist_ok=True)
            iourc_path().write_text(found["iourc"].strip() + "\n")
            iourc_path().chmod(0o600)
        libs.append("iourc")
    if any(i["kind"] == "iol" for i in selected):
        base = _sibling_base(_addons(src))
        for rel, size in found.get("iol_lib", []):
            dest = iol_libdir() / rel
            if not (dest.is_file() and (r.dry_run or dest.stat().st_size == size)):
                src.fetch(job, f"{base}/iol/lib/{rel}".lstrip("/"), dest, False, done, total)
                libs.append(rel)
    # devices: point built-in ones at the imported image when theirs is missing, create the others
    # (also for images copied by an earlier run that stopped before this step)
    templates = topo.load_templates()
    taken = set(templates)
    updated, created = [], []
    by_tpl: dict[str, list[str]] = {}
    for i in selected:
        if i["template"]:
            by_tpl.setdefault(i["template"], []).append(i["folder"])
    for name, folders in by_tpl.items():
        t = templates[name]
        if not (settings.images / (t.image or "")).exists():
            catalog.save_template(t.model_copy(update={"image": sorted(folders)[-1]}))
            updated.append(name)
    for t in templates.values():  # devices made by an older import that took a Linux VM for a router
        if t.source == "EVE-NG/PNETLab" and t.kind == "qemu" and (t.image or "").split("-", 1)[0].lower() in LINUX \
                and (t.icon == "router" or t.console == "telnet"):
            catalog.save_template(t.model_copy(update={"icon": "server", "console": "vnc" if t.console == "telnet" else t.console}))
            updated.append(t.name)
    newest: dict[str, dict] = {}
    for i in selected:
        if i["kind"] == "qemu" and not i["template"] and not any(t.source == "EVE-NG/PNETLab" and t.image and t.image.split("-", 1)[0].lower() == i["prefix"]
                                                                    for t in templates.values()):
            if i["prefix"] not in newest or i["folder"] > newest[i["prefix"]]["folder"]:
                newest[i["prefix"]] = i
    for prefix, i in newest.items():
        t = _new_template(src, prefix, i["folder"], i["files"], taken)
        taken.add(t.name)
        catalog.save_template(t)
        created.append(t.name)
    if not chosen and not updated and not created and not libs:
        return "Niente da importare: immagini e dispositivi già presenti"
    msg = f"{len(chosen)} immagini {'spostate' if move and not src.remote else 'copiate'}"
    if updated:
        msg += f"; dispositivi collegati: {', '.join(updated)}"
    if created:
        msg += f"; nuovi dispositivi: {', '.join(created)}"
    if libs:
        msg += f"; librerie IOL: {', '.join(libs)}"
    return msg
