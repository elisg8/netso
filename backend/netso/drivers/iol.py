"""Cisco IOL nodes run natively (no container): the user's own IOL binary and iourc license, like EVE-NG/GNS3.

Every node is a `netso.iolwrap` process (console on telnet + frames between IOL and the taps), started in
the background from the node directory, where IOL keeps its NVRAM (nvram_<id>).
"""
from __future__ import annotations

import os
import re
import shlex
import sys
import time
from pathlib import Path

from .. import network as net
from ..config import settings
from ..hostcmd import Runner
from ..models import NodeState, ResolvedNode
from .dynamips import IOS_SUFFIXES

IOL_IDS = range(1, 512)  # the wrapper uses id + 512 for its side


def iourc_path() -> Path:
    """License file supplied by the user (NetSO never generates one)."""
    return settings.home / "iourc"


def iol_libdir() -> Path:
    """Libraries IOL needs (libcrypto.so.4…), copied from PNETLab's addons/iol/lib by the import."""
    return settings.home / "iol-lib"


def is_iol_file(f: Path) -> bool:
    """An IOL binary: .bin (as PNETLab names them) or any ELF executable, not a copy still in progress."""
    if not f.is_file() or f.suffix == ".part":
        return False
    if f.suffix.lower() in IOS_SUFFIXES:
        return True
    try:
        with open(f, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def iol_problem(d: Path) -> str:
    """Why an iol-* folder has no usable binary."""
    files = sorted(f.name for f in d.iterdir()) if d.is_dir() else []
    if any(n.endswith(".part") for n in files):
        return f"{d.name}: copia non finita (.part), rilancia l'import da PNETLab"
    return f"{d.name}: nessun file IOL ({', '.join(files[:4]) or 'cartella vuota'})"


def iol_binary(image: str, l2: bool) -> Path:
    """The node's IOL .bin; if its image is not there (e.g. the default iol-l3), any IOL image on the server:
    the L2 ones for a switch, the others for a router."""
    p = settings.images / image if image else None
    if p and p.is_file():
        return p
    if p and p.is_dir():
        bins = sorted(f for f in p.iterdir() if is_iol_file(f))
        if bins:
            return bins[-1]
    found = sorted(f for d in settings.images.glob("iol*") if d.is_dir() for f in d.iterdir()
                   if is_iol_file(f)) if settings.images.is_dir() else []
    is_l2 = lambda f: bool(re.search(r"l2|switch", f.name, re.I))  # noqa: E731
    match = [f for f in found if is_l2(f) == l2]
    if match or found:
        return (match or found)[-1]
    raise FileNotFoundError(f"Nessuna immagine IOL (.bin) in {settings.images}: importala da PNETLab "
                            "(Dispositivi → Immagini → PNETLab) o caricala in una cartella iol-<nome>")


def bay_unit(i: int) -> tuple[int, int]:
    """Interface i -> Ethernet<bay>/<unit> (4 per bay)."""
    return i // 4, i % 4


class IolDriver:
    def __init__(self, runner: Runner):
        self.r = runner

    def node_dir(self, lab: str, node: str) -> Path:
        return settings.labs_dir / lab / "nodes" / node

    def start(self, lab: str, node: ResolvedNode, state: NodeState, wiring: dict[str, str], config: str | None = None) -> None:
        d = self.node_dir(lab, node.name)
        iol_id = state.iol_id or 0
        if self.r.dry_run:
            binary = settings.images / (node.image or "") / "iol.bin"
        else:
            d.mkdir(parents=True, exist_ok=True)
            binary = iol_binary(node.image or "", "l2" in f"{node.template} {node.image}".lower())
            binary.chmod(binary.stat().st_mode | 0o111)
            # IOL keeps the saved config in nvram_<id>: follow the node if its id changed
            mine = d / f"nvram_{iol_id:05d}"
            old = sorted(p for p in d.glob("nvram_*") if p != mine)
            if old and not mine.exists():
                old[-1].rename(mine)
        args = [sys.executable, "-m", "netso.iolwrap", "--dir", str(d), "--bin", str(binary), "--id", str(iol_id),
                "--port", str(state.console_port), "--ram", str(node.ram), "--eth", str(max(1, (len(node.interfaces) + 3) // 4))]
        if iourc_path().is_file():
            args += ["--iourc", str(iourc_path())]
        if config:
            cfg = d / "startup-config.cfg"
            self.r.log.append(f"write {cfg}")
            if not self.r.dry_run:
                cfg.write_text(config)
                (d / f"nvram_{iol_id:05d}").unlink(missing_ok=True)  # boot from the startup config, not the old NVRAM
            args += ["--config", cfg.name]
        state.taps = {}
        for i, iface in enumerate(node.interfaces):
            tap = net.tap_name(lab, node.name, i)
            if not self.r.dry_run:
                net.delete_iface(self.r, tap)  # drop a tap left by a crashed run: a reused one gives TUNSETIFF EBUSY
            net.create_tap(self.r, tap, wiring.get(iface))
            state.taps[iface] = tap
            bay, unit = bay_unit(i)
            args += ["--tap", f"{bay}/{unit}={tap}"]
        if iol_libdir().is_dir():  # PNETLab's own libcrypto.so.4 before the host's
            args = ["env", f"LD_LIBRARY_PATH={iol_libdir()}"] + args
        line = f"cd {shlex.quote(str(d))} && nohup {shlex.join(args)} >iolwrap.log 2>&1 & echo $!"
        out = self.r.run(["sh", "-c", line], fake="6161")
        try:
            state.pid = int(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError("IOL non è partito (vedi iolwrap.log nella cartella del nodo)")
        if not self.r.dry_run:
            time.sleep(1.5)  # a missing library or a bad license makes IOL quit at once
            try:
                os.kill(state.pid, 0)
            except ProcessLookupError:
                state.pid = None
                raise RuntimeError(self._why(d))

    def _why(self, d: Path) -> str:
        text = ""
        for f in ("iol-exit.log", "iolwrap.log"):
            try:
                text += (d / f).read_text(errors="replace")
            except OSError:
                pass
        tail = " ".join(text.split())[-300:]
        hint = ""
        low = text.lower()
        if "license" in low or "iourc" in low:
            hint = (f" — licenza IOL non valida o mancante: carica il tuo iourc per l'host '{os.uname().nodename}' "
                    "(Dispositivi → Immagini → Licenza IOL)")
        elif "error while loading shared libraries" in low or "no such file" in low:
            hint = (" — mancano librerie: rilancia l'import da PNETLab (copia addons/iol/lib) "
                    "o install.sh (librerie i386)")
        return f"IOL si è chiuso subito: {tail or 'nessun messaggio'}{hint}"

    def wipe(self, lab: str, node: str) -> None:
        d = self.node_dir(lab, node)
        for f in list(d.glob("nvram_*")) + list(d.glob("vlan.dat*")):
            self.r.log.append(f"rm {f}")
            if not self.r.dry_run:
                f.unlink(missing_ok=True)
