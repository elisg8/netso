"""Dynamips nodes: classic Cisco routers (7200, 3725, 3745, 2691) running the user's own IOS image.

Each slot gets its adapter (-p), every interface a tap NIO (-s slot:port:tap:name); the console is telnet (-T).
Dynamips does not daemonize: it is started in the background from the node directory (nvram, logs).
"""
from __future__ import annotations

import shlex
from pathlib import Path

from .. import network as net
from ..config import settings
from ..hostcmd import Runner
from ..models import NodeState, ResolvedNode

PLATFORM_ARG = {"c7200": "7200", "c3725": "3725", "c3745": "3745", "c2691": "2691"}
IOS_SUFFIXES = (".image", ".bin", ".img")


def ios_image(image: str) -> Path:
    p = settings.images / image
    if p.is_file():
        return p
    if p.is_dir():
        files = sorted(f for f in p.iterdir() if f.suffix.lower() in IOS_SUFFIXES)
        if files:
            return files[0]
    raise FileNotFoundError(f"IOS image '{image}' not found in {settings.images} (.image/.bin file)")


class DynamipsDriver:
    def __init__(self, runner: Runner):
        self.r = runner

    def node_dir(self, lab: str, node: str) -> Path:
        return settings.labs_dir / lab / "nodes" / node

    def start(self, lab: str, node: ResolvedNode, state: NodeState, wiring: dict[str, str], config: str | None = None) -> None:
        d = self.node_dir(lab, node.name)
        if not self.r.dry_run:
            d.mkdir(parents=True, exist_ok=True)
        image = ios_image(node.image or "") if not self.r.dry_run else settings.images / (node.image or "") / "ios.image"
        args = ["dynamips", "-P", PLATFORM_ARG.get(node.machine, "7200"), "-r", str(node.ram), "-T", str(state.console_port), "-l", "dynamips.log"]
        if node.idle_pc:
            args += ["--idle-pc", node.idle_pc]
        if config:
            cfg = d / "startup-config.cfg"
            self.r.log.append(f"write {cfg}")
            if not self.r.dry_run:
                cfg.write_text(config)
            args += ["-C", str(cfg)]
        state.taps = {}
        port_index = 0
        names = node.interfaces
        for slot, adapter in enumerate(node.slots or []):
            if not adapter:
                continue
            onboard = slot == 0 and adapter == "GT96100-FE"  # built into the 3700/2691 motherboard
            if not onboard:
                args += ["-p", f"{slot}:{adapter}"]
            from ..models import DYNAMIPS_ADAPTERS
            for port in range(DYNAMIPS_ADAPTERS[adapter][1]):
                iface = names[port_index]
                port_index += 1
                tap = net.tap_name(lab, node.name, port_index - 1)
                net.create_tap(self.r, tap, wiring.get(iface))
                state.taps[iface] = tap
                args += ["-s", f"{slot}:{port}:tap:{tap}"]
        args += node.extra_args + [str(image)]
        line = f"cd {shlex.quote(str(d))} && nohup {shlex.join(args)} >console.log 2>&1 & echo $!"
        out = self.r.run(["sh", "-c", line], fake="5151")
        try:
            state.pid = int(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError("Dynamips did not start (check that the 'dynamips' package is installed)")

    def wipe(self, lab: str, node: str) -> None:
        d = self.node_dir(lab, node)
        for f in list(d.glob("*_nvram")) + list(d.glob("*_disk*")) + list(d.glob("*_rom")):
            self.r.log.append(f"rm {f}")
            if not self.r.dry_run:
                f.unlink(missing_ok=True)
