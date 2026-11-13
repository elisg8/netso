"""Command line: `netso serve | deploy | destroy | list | inspect | console | templates | images | passwd`."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import topology as topo
from .config import settings


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*map(str, r)))


def cmd_serve(a: argparse.Namespace) -> None:
    import uvicorn
    if a.host not in ("127.0.0.1", "localhost") and not settings.auth and not settings.token:
        print("[netso] WARNING: API exposed on the network without login (NETSO_AUTH=0) or NETSO_TOKEN", file=sys.stderr)
    uvicorn.run("netso.api:app", host=a.host, port=a.port, log_level="info")


def cmd_deploy(a: argparse.Namespace) -> None:
    from .engine import engine
    if a.lab.endswith((".yml", ".yaml")) or Path(a.lab).is_file():
        lab = topo.parse_lab_yaml(Path(a.lab).read_text())
        topo.validate_lab(lab)
        topo.save_lab(lab)
        name = lab.name
        print(f"Lab '{name}' imported from {a.lab}")
    else:
        name = a.lab
    state = engine.deploy(name)
    rows = []
    for n, s in state.nodes.items():
        access = ""
        if s.console_port:
            access = f"telnet :{s.console_port}"
        if s.vnc_display is not None:
            access += f"  vnc :{5900 + s.vnc_display}"
        if s.container:
            access = f"netso console {name} {n}"
        rows.append([n, s.kind, s.status, access.strip(), s.error or ""])
    _print_table(rows, ["NODE", "TYPE", "STATE", "ACCESS", "ERROR"])
    if state.error:
        sys.exit(1)


def cmd_destroy(a: argparse.Namespace) -> None:
    from .engine import engine
    engine.destroy(a.lab)
    print(f"Lab '{a.lab}' stopped")
    if a.delete:
        topo.delete_lab(a.lab)
        print(f"Lab '{a.lab}' deleted")


def cmd_list(_a: argparse.Namespace) -> None:
    from .engine import engine
    rows = []
    for name in topo.list_labs():
        st = engine.refresh(name)
        running = sum(1 for s in st.nodes.values() if s.status == "running")
        rows.append([name, st.status, f"{running}/{len(st.nodes)}"])
    _print_table(rows, ["LAB", "STATE", "ACTIVE NODES"]) if rows else print("No labs")


def cmd_inspect(a: argparse.Namespace) -> None:
    from .engine import engine
    st = engine.refresh(a.lab)
    print(st.model_dump_json(indent=2))


def cmd_console(a: argparse.Namespace) -> None:
    from .engine import engine
    cmd = engine.console_command(a.lab, a.node)
    os.execvp(cmd[0], cmd)


def cmd_templates(_a: argparse.Namespace) -> None:
    rows = [[t.name, t.kind, t.image or "-", f"{t.cpus}/{t.ram}MB", t.description]
            for t in topo.load_templates().values()]
    _print_table(rows, ["TEMPLATE", "TYPE", "IMAGE", "CPU/RAM", "DESCRIPTION"])


def cmd_passwd(a: argparse.Namespace) -> None:
    import getpass
    from . import auth
    pw = a.password or getpass.getpass(f"New password for {a.user}: ")
    auth.set_password(a.user, pw)
    print(f"Password for '{a.user}' updated")


def cmd_images(_a: argparse.Namespace) -> None:
    from .drivers.qemu import list_images
    rows = [[i["name"], ", ".join(i["disks"]), f"{i['size'] / 2**30:.1f} GB"] for i in list_images()]
    _print_table(rows, ["IMAGE", "DISKS", "SIZE"]) if rows else print(
        f"No images in {settings.images}")


class _CliJob:
    """Job stand-in for long CLI commands: progress on one terminal line (percentage + current file)."""

    def __init__(self):
        self.last = 0.0
        self.progress = None

    def update(self, progress=None, message=None):
        import time
        if progress is not None:
            self.progress = progress
        now = time.monotonic()
        if now - self.last < 0.5 and progress != 1:
            return
        self.last = now
        pct = f"{self.progress * 100:5.1f}% " if self.progress is not None else ""
        sys.stdout.write(f"\r\033[K  {pct}{(message or '')[:100]}")
        sys.stdout.flush()


def cmd_pull(a: argparse.Namespace) -> None:
    from . import dockerimg
    from .hostcmd import runner
    refs = a.refs or [t["ref"] for t in dockerimg.describe(runner)["templates"] if t["pullable"] and not t["present"]]
    if not refs:
        print("All device Docker images are already present")
    for ref in refs:
        print(f"docker pull {ref} …", flush=True)
        try:
            print("\r\033[K" + dockerimg.pull(runner, _CliJob(), ref))
        except ValueError as exc:
            print(f"  error: {exc}", file=sys.stderr)


def cmd_import_pnet(a: argparse.Namespace) -> None:
    from . import pnetimport
    from .hostcmd import runner
    found = pnetimport.scan(a.source, runner)
    if a.only:
        import fnmatch
        found["items"] = [i for i in found["items"] if any(fnmatch.fnmatch(i["folder"].lower(), p.lower()) for p in a.only)]
    rows = [[i["folder"], i["kind"], f"{i['size'] / 2**30:.1f} GB", "already present" if i["present"] else i["action"]] for i in found["items"]]
    _print_table(rows, ["IMAGE", "TYPE", "SIZE", "ACTION"]) if rows else None
    todo = sum(i["size"] for i in found["items"] if not i["present"])
    where = settings.images
    while not where.exists():
        where = where.parent
    free = shutil.disk_usage(where).free
    print(f"To copy: {todo / 2**30:.1f} GB · free in {settings.images}: {free / 2**30:.1f} GB")
    for n in found["notes"]:
        print(n)
    if not a.yes and input("Import? [y/N] ").strip().lower() not in ("y", "yes"):
        return
    print("Copying: progress below (do not close the terminal)")
    print("\r\033[K" + pnetimport.run_import(runner, _CliJob(), a.source, [i["src"] for i in found["items"]], a.move))


def cmd_import_labs(a: argparse.Namespace) -> None:
    import re as _re
    from pathlib import Path
    from . import labcatalog, labconvert, topology as topo
    tpls = topo.load_templates()
    if a.router not in tpls:
        print(f"Router '{a.router}' not installed. Available: {', '.join(sorted(tpls))}")
        return
    src = Path(a.source).expanduser()
    files = sorted(p for p in src.rglob("*.txt") if p.is_file())
    if not files:
        print(f"No .txt files in {src}")
        return
    rows, done = [], 0
    for f in files:
        rel = f.relative_to(src)
        topic = _re.sub(r"^\d+\s*", "", rel.parts[0]) if len(rel.parts) > 1 else "Lab"
        name_part = " / ".join([*rel.parts[1:-1], f.stem])
        label = f"{topic} · {name_part}"
        cid = labcatalog.slug(str(rel.with_suffix("")))      # unique even across nested task folders
        try:
            name = _re.sub(r"[^A-Za-z0-9]+", "-", f"{topic}-{name_part}").strip("-")[:48] or "lab"
            lab, configs, instr = labconvert.convert(name, f.read_text(errors="replace"), tpls, a.router, topic=label)
            if not lab.nodes:
                rows.append([label, "—", "skipped (no router)"]); continue
            nr = sum(1 for n in lab.nodes if n.template == a.router)
            meta = {"label": label, "cat": f"My labs · {topic}", "desc": f"{nr} routers · initial configs ready", "router": a.router}
            labcatalog.save_catalog(cid, lab, configs, meta, instructions=instr)
            rows.append([label, f"{nr} routers, {len(lab.links)} links", "imported"]); done += 1
        except Exception as exc:  # noqa: BLE001 — report and keep going
            rows.append([label, "—", f"error: {exc}"])
    _print_table(rows, ["LAB", "TOPOLOGY", "RESULT"])
    print(f"\nImported {done}/{len(files)} labs into «Generate lab» → category «My labs» (router: {a.router}).")


def cmd_doctor(_a: argparse.Namespace) -> None:
    """Controlli read-only dell'ambiente: utile prima del primo deploy reale sul server."""
    import subprocess
    OK, WARN, BAD = "OK", "!!", "XX"
    rows: list[list[str]] = []

    def add(state: bool | None, name: str, detail: str = "") -> None:
        rows.append([OK if state is True else BAD if state is False else WARN, name, detail])

    has = lambda c: shutil.which(c) is not None  # noqa: E731
    linux = sys.platform.startswith("linux")
    add(linux, "Linux system", sys.platform if linux else f"NetSO runs on Linux (now: {sys.platform})")

    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    add(None if not linux else is_root, "Root user",
        "ok" if is_root else "run as root (systemd) for network, tap and KVM")

    kvm = Path("/dev/kvm")
    kvm_ok = kvm.exists() and os.access(kvm, os.R_OK | os.W_OK)
    add(True if kvm_ok else None, "KVM (/dev/kvm)",
        "hardware acceleration active" if kvm_ok else "missing: VMs slow or unbootable (enable VT-x/AMD-V, modprobe kvm)")

    nested = ""
    for p in ("/sys/module/kvm_intel/parameters/nested", "/sys/module/kvm_amd/parameters/nested"):
        try:
            nested = Path(p).read_text().strip()
            break
        except OSError:
            continue
    add(None, "Nested virtualization", {"Y": "active", "1": "active"}.get(nested, nested or "n/a (ok if NetSO is on bare metal)"))

    tun = Path("/dev/net/tun")
    add(True if tun.exists() else None, "TAP (/dev/net/tun)", "ok" if tun.exists() else "missing: modprobe tun (needed for VM NICs)")

    # core=True → errore se manca; altrimenti avviso (serve solo per quella funzione)
    for cmd, core, why in [("ip", True, "iproute2: bridges and interfaces"), ("qemu-system-x86_64", False, "QEMU/KVM VMs"),
                           ("docker", False, "containers"), ("iptables", False, "NAT to the Internet"), ("dnsmasq", False, "NAT node DHCP")]:
        add(True if has(cmd) else (False if core else None), cmd, "ok" if has(cmd) else f"missing: {why}")

    if has("docker"):
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=6)
            add(True if r.returncode == 0 else None, "Docker running", "ok" if r.returncode == 0 else "the daemon is not responding (systemctl start docker)")
        except Exception:  # noqa: BLE001
            add(None, "Docker running", "the daemon is not responding (systemctl start docker)")

    iourc = settings.home / "iourc"
    add(None, "IOL license (iourc)", "present" if iourc.is_file() else "missing (only if you use Cisco IOL)")
    iol_lib = settings.home / "iol-lib"
    iol_ok = iol_lib.is_dir() and any(iol_lib.iterdir())
    add(None, "IOL libraries (iol-lib)", "present" if iol_ok else "missing (only if you use Cisco IOL)")

    for label, d in [("home", settings.home), ("images", settings.images), ("templates", settings.user_template_dir())]:
        ok = d.is_dir() and os.access(d, os.W_OK)
        add(ok, f"{label} folder", str(d) if ok else f"does not exist or not writable: {d}")

    try:
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
        avail = int(info["MemAvailable"].split()[0]) // 1024
        add(avail > 1024, "RAM available", f"{avail} MB" + ("" if avail > 1024 else " (too little for VMs)"))
    except (OSError, KeyError, ValueError):
        pass

    _print_table(rows, ["", "Check", "Detail"])
    bad = sum(1 for r in rows if r[0] == BAD)
    print()
    print("All set for a real run." if not bad else f"{bad} problem(s) to fix before a real deploy.")
    if bad:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="netso", description="NetSO - all-in-one network lab")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the API and web interface")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("deploy", help="start a lab (name or .yml file)")
    s.add_argument("lab")
    s.set_defaults(func=cmd_deploy)

    s = sub.add_parser("destroy", help="stop a lab")
    s.add_argument("lab")
    s.add_argument("--delete", action="store_true", help="also delete definition and disks")
    s.set_defaults(func=cmd_destroy)

    sub.add_parser("list", help="list labs").set_defaults(func=cmd_list)

    s = sub.add_parser("inspect", help="detailed status of a lab")
    s.add_argument("lab")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("console", help="open a node's console")
    s.add_argument("lab")
    s.add_argument("node")
    s.set_defaults(func=cmd_console)

    sub.add_parser("doctor", help="check the environment before the first real deploy (KVM, tap, docker, IOL…)").set_defaults(func=cmd_doctor)
    sub.add_parser("templates", help="list templates").set_defaults(func=cmd_templates)
    sub.add_parser("images", help="list QEMU images").set_defaults(func=cmd_images)
    s = sub.add_parser("pull", help="download Docker images (no arguments: the missing device ones)")
    s.add_argument("refs", nargs="*")
    s.set_defaults(func=cmd_pull)
    s = sub.add_parser("import-pnet", help="import images from an EVE-NG/PNETLab server")
    s.add_argument("source", help="folder (e.g. /opt/unetlab/addons) or root@host:/opt/unetlab/addons")
    s.add_argument("--only", nargs="+", metavar="MODEL", help="only these images, e.g. --only 'vios*' 'c7200*' 'asav*'")
    s.add_argument("--move", action="store_true", help="move instead of copy (local folders only)")
    s.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    s.set_defaults(func=cmd_import_pnet)
    s = sub.add_parser("import-labs", help="convert a folder of initial configs (.txt) into «Generate lab» labs")
    s.add_argument("source", help="folder with the .txt files (subfolders per topic allowed)")
    s.add_argument("--router", default="cisco-iosv", help="router template to map the nodes onto (default: cisco-iosv)")
    s.set_defaults(func=cmd_import_labs)
    s = sub.add_parser("passwd", help="create a user or change its password")
    s.add_argument("user", nargs="?", default="admin")
    s.add_argument("--password", help="asked for if omitted")
    s.set_defaults(func=cmd_passwd)

    a = p.parse_args(argv)
    try:
        a.func(a)
    except (topo.TopologyError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        from .engine import EngineError
        from .hostcmd import HostError
        if isinstance(exc, (EngineError, HostError)):
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
