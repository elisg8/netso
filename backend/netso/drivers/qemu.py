"""QEMU/KVM virtual machine nodes."""
from __future__ import annotations

import os
import shutil
import signal
import time
from pathlib import Path

from ..config import settings
from ..hostcmd import Runner
from ..models import NodeState, ResolvedNode
from .. import network as net

DISK_SUFFIXES = (".qcow2", ".img", ".vmdk", ".raw")


def base_disks(image: str) -> list[Path]:
    """Disks of an image folder, sorted (hda, hdb, ... or virtioa, virtiob ...)."""
    p = settings.images / image
    if p.is_file():
        return [p]
    if not p.is_dir():
        raise FileNotFoundError(
            f"Image '{image}' not found in {settings.images} "
            f"(create the folder {p} and put the .qcow2 disk inside it)"
        )
    disks = sorted(f for f in p.iterdir() if f.suffix in DISK_SUFFIXES)
    if not disks:
        raise FileNotFoundError(f"No disk in {p}")
    return disks


def list_images() -> list[dict]:
    out = []
    if settings.images.is_dir():
        for d in sorted(settings.images.iterdir()):
            if d.is_dir():
                disks = [f.name for f in sorted(d.iterdir()) if f.suffix in DISK_SUFFIXES]
                size = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
                out.append({"name": d.name, "disks": disks, "size": size})
    return out


class QemuDriver:
    def __init__(self, runner: Runner):
        self.r = runner

    def node_dir(self, lab: str, node: str) -> Path:
        return settings.labs_dir / lab / "nodes" / node

    def prepare_disks(self, lab: str, node: ResolvedNode) -> list[Path]:
        """Copy-on-write overlays on top of the base images (created once, kept until wipe)."""
        d = self.node_dir(lab, node.name)
        if not self.r.dry_run:
            d.mkdir(parents=True, exist_ok=True)
        overlays = []
        bases = base_disks(node.image or "") if not self.r.dry_run else [settings.images / (node.image or "") / "disk.qcow2"]
        for i, base in enumerate(bases):
            ov = d / f"disk{i}.qcow2"
            if not ov.exists():
                fmt = "raw" if base.suffix in (".img", ".raw") else base.suffix.lstrip(".")
                self.r.run(["qemu-img", "create", "-f", "qcow2", "-F", fmt, "-b", str(base), str(ov)])
            overlays.append(ov)
        return overlays

    def wipe(self, lab: str, node: str) -> None:
        d = self.node_dir(lab, node)
        for f in d.glob("disk*.qcow2"):
            self.r.log.append(f"rm {f}")
            if not self.r.dry_run:
                f.unlink(missing_ok=True)

    def _write(self, path: Path, text: str) -> None:
        self.r.log.append(f"write {path}")
        if not self.r.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def _remove(self, path: Path) -> None:
        self.r.log.append(f"rm -rf {path}")
        if not self.r.dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

    def config_media(self, lab: str, node: ResolvedNode, config: str) -> Path | None:
        """Build the disk/ISO that hands the startup-config to the guest (rebuilt at every start)."""
        d = self.node_dir(lab, node.name)
        name = (node.config_file or "").lstrip("/")
        if node.config_method == "disk":
            # FAT image with the config at its root, e.g. ios_config.txt for IOSv
            src, img = d / "startup-config.txt", d / "config.img"
            self._write(src, config)
            self._remove(img)
            self.r.run(["mkfs.vfat", "-C", "-n", node.config_label[:11].upper(), str(img), "16384"])
            self.r.run(["mcopy", "-i", str(img), str(src), f"::/{name}"])
            return img
        if node.config_method == "iso":
            stage, iso = d / "config-iso", d / "config.iso"
            self._remove(stage)
            self._write(stage / name, config)
            self._remove(iso)
            self.r.run(["genisoimage", "-quiet", "-J", "-r", "-V", node.config_label, "-o", str(iso), str(stage)])
            return iso
        return None

    def start(self, lab: str, node: ResolvedNode, state: NodeState,
              iface_bridges: dict[str, str], config: str | None = None) -> None:
        overlays = self.prepare_disks(lab, node)
        d = self.node_dir(lab, node.name)
        pidfile = d / "qemu.pid"
        media = self.config_media(lab, node, config) if config else None

        args = [
            settings.qemu_binary,
            "-name", f"{lab}-{node.name}",
            "-machine", f"{node.machine},accel=kvm:tcg",
            "-cpu", node.cpu_model if self._kvm() else "max",
            "-smp", str(node.cpus),
            "-m", str(node.ram),
            "-display", "none",
            "-pidfile", str(pidfile),
            "-daemonize",
            "-monitor", f"unix:{d / 'monitor.sock'},server,nowait",
        ]
        if node.disk_bus == "sata":
            args += ["-device", "ahci,id=ahci0"]
        for i, ov in enumerate(overlays):
            if node.disk_bus == "virtio":
                args += ["-drive", f"file={ov},if=virtio,index={i},cache=writeback"]
            elif node.disk_bus == "sata":
                args += ["-drive", f"file={ov},if=none,id=d{i}",
                         "-device", f"ide-hd,drive=d{i},bus=ahci0.{i}"]
            else:
                args += ["-drive", f"file={ov},if=ide,index={i}"]
        if media is not None and node.config_method == "disk":
            # extra disk right after the system disks, on the same bus
            i = len(overlays)
            if node.disk_bus == "virtio":
                args += ["-drive", f"file={media},if=virtio,index={i},format=raw"]
            elif node.disk_bus == "sata":
                args += ["-drive", f"file={media},if=none,id=cfg,format=raw",
                         "-device", f"ide-hd,drive=cfg,bus=ahci0.{i}"]
            else:
                args += ["-drive", f"file={media},if=ide,index={i},format=raw"]
        elif media is not None:
            args += ["-cdrom", str(media)]

        # One tap per NIC, plugged into the link bridge when connected
        state.taps = {}
        for i, iface in enumerate(node.interfaces):
            tap = net.tap_name(lab, node.name, i)
            net.create_tap(self.r, tap, iface_bridges.get(iface))
            state.taps[iface] = tap
            args += [
                "-netdev", f"tap,id=n{i},ifname={tap},script=no,downscript=no",
                "-device", f"{node.nic_model},netdev=n{i},mac={net.nth_mac(node.mac, i) if node.mac else net.mac_address(lab, node.name, i)}",
            ]

        if node.console == "telnet":
            args += ["-serial", f"telnet:0.0.0.0:{state.console_port},server,nowait"]
        if state.vnc_display is not None:
            args += ["-vnc", f"0.0.0.0:{state.vnc_display}"]
        if node.console == "spice" and state.spice_port:
            args += ["-spice", f"port={state.spice_port},addr=0.0.0.0,disable-ticketing=on"]
        if node.clipboard and node.console in ("vnc", "spice"):
            # shared clipboard: the guest's spice-vdagent talks to QEMU (VNC clipboard, QEMU >= 6.1) or to SPICE
            agent = "spicevmc,id=vdagent,name=vdagent" if node.console == "spice" else "qemu-vdagent,id=vdagent,name=vdagent,clipboard=on"
            args += ["-chardev", agent, "-device", "virtio-serial-pci,id=vdserial",
                     "-device", "virtserialport,bus=vdserial.0,chardev=vdagent,name=com.redhat.spice.0"]
        args += node.extra_args

        self.r.run(args)
        state.pid = self._read_pid(pidfile)

    def _read_pid(self, pidfile: Path) -> int | None:
        if self.r.dry_run:
            return 4343
        for _ in range(20):
            if pidfile.exists():
                return int(pidfile.read_text().strip())
            time.sleep(0.1)
        return None

    @staticmethod
    def _kvm() -> bool:
        return os.access("/dev/kvm", os.R_OK | os.W_OK)

    def stop(self, state: NodeState) -> None:
        if state.pid and not self.r.dry_run:
            try:
                os.kill(state.pid, signal.SIGTERM)
                for _ in range(30):
                    os.kill(state.pid, 0)
                    time.sleep(0.1)
                os.kill(state.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.r.log.append(f"kill {state.pid}")
        for tap in state.taps.values():
            net.delete_iface(self.r, tap)
        state.taps = {}
        state.pid = None

    def is_running(self, state: NodeState) -> bool:
        if not state.pid:
            return False
        if self.r.dry_run:
            return True
        try:
            os.kill(state.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def console_command(state: NodeState) -> list[str]:
        return ["telnet", "127.0.0.1", str(state.console_port)]
