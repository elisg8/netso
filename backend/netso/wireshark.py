"""Wireshark in the browser (like EVE-NG Pro's HTML5 Wireshark).

Each session is a `netso/wireshark` container on the host network that runs
Wireshark live on one link endpoint (tap/veth) inside a TigerVNC X server bound
to 127.0.0.1. The API proxies that VNC port over a websocket to noVNC in the
browser. Wireshark also records to `labs/<lab>/captures/<id>.pcapng`, so the
capture shows up in the capture list once it is closed.

A session with no viewer left is stopped after a short grace period, so a
closed browser tab does not leave containers behind.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from .capture import captures_dir, check_filter
from .config import settings
from .hostcmd import Runner, runner as default_runner

GRACE = 20  # seconds a session survives without viewers (time to reconnect / move to a new tab)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-") or "x"


@dataclass
class WiresharkSession:
    id: str
    lab: str
    node: str
    iface: str
    filter: str
    container: str
    port: int
    started: float
    readfile: str | None = None
    viewers: int = 0
    stopped: float | None = None
    _timer: threading.Timer | None = None

    def info(self) -> dict:
        return {"id": self.id, "node": self.node, "iface": self.iface, "filter": self.filter,
                "started": self.started, "running": self.stopped is None,
                "file": self.readfile or f"{self.id}.pcapng", "live": self.readfile is None,
                "dry_run": settings.dry_run}


class WiresharkManager:
    def __init__(self, runner: Runner | None = None):
        self.r = runner or default_runner
        self._sessions: dict[str, WiresharkSession] = {}
        self._lock = threading.Lock()

    def _free_port(self) -> int:
        from .engine import _port_free  # late import: engine owns the port helpers
        taken = {s.port for s in self._sessions.values() if s.stopped is None}
        p = settings.wireshark_port_start
        while p in taken or not (self.r.dry_run or _port_free(p)):
            p += 1
        return p

    def start(self, lab: str, node: str, iface: str, host_iface: str | None, expr: str = "",
              readfile: str | None = None) -> WiresharkSession:
        """Live capture on `host_iface`, or (readfile) open a saved capture of the lab."""
        expr = check_filter(expr)
        with self._lock:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            sid = f"{_slug(node)}_{_slug(iface)}_{stamp}_{'view' if readfile else 'ws'}"
            n = 1
            while sid in self._sessions or (captures_dir(lab) / f"{sid}.pcapng").exists():
                n += 1
                sid = f"{_slug(node)}_{_slug(iface)}_{stamp}-{n}_{'view' if readfile else 'ws'}"
            port = self._free_port()
            name = f"netso-ws-{lab}-{port}"
            d = captures_dir(lab)
            d.mkdir(parents=True, exist_ok=True)
            self.r.run(["docker", "rm", "-f", name], check=False)
            self.r.run([
                "docker", "run", "-d", "--rm", "--name", name,
                "--label", f"netso.lab={lab}", "--label", "netso.role=wireshark",
                "--network", "host", "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW",
                *(["-e", f"READFILE=/captures/{readfile}"] if readfile else
                  ["-e", f"IFACE={host_iface}", "-e", f"FILTER={expr}", "-e", f"OUTFILE=/captures/{sid}.pcapng"]),
                "-e", f"VNC_PORT={port}", "-e", f"DISPLAY_NUM={port - settings.wireshark_port_start + 100}",
                "-v", f"{d}:/captures",
                settings.wireshark_image,
            ])
            s = WiresharkSession(sid, lab, node, iface, expr, name, port, time.time(), readfile=readfile)
            self._sessions[sid] = s
            self._arm(s)  # nobody connects at all -> stop after the grace period
            return s

    def get(self, lab: str, sid: str) -> WiresharkSession | None:
        s = self._sessions.get(sid)
        return s if s and s.lab == lab else None

    def alive(self, s: WiresharkSession) -> bool:
        if s.stopped is not None:
            return False
        if self.r.dry_run:
            return True
        out = self.r.run(["docker", "ps", "-q", "-f", f"name=^{s.container}$"], check=False)
        if not out:  # Wireshark was closed from its own menu
            s.stopped = time.time()
        return bool(out)

    def list(self, lab: str) -> list[dict]:
        return [s.info() for s in self._sessions.values() if s.lab == lab and self.alive(s)]

    def stop(self, lab: str, sid: str) -> None:
        s = self.get(lab, sid)
        if not s or s.stopped is not None:
            return
        s.stopped = time.time()
        if s._timer:
            s._timer.cancel()
        # SIGTERM first so Wireshark closes the pcapng cleanly
        self.r.run(["docker", "stop", "-t", "3", s.container], check=False)
        self.r.run(["docker", "rm", "-f", s.container], check=False)

    def stop_where(self, lab: str, node: str | None = None) -> None:
        for s in list(self._sessions.values()):
            if s.lab == lab and (node is None or s.node == node):
                self.stop(lab, s.id)

    # -- viewers (websocket clients) and automatic cleanup
    def _arm(self, s: WiresharkSession) -> None:
        if s._timer:
            s._timer.cancel()
        s._timer = threading.Timer(GRACE, lambda: s.viewers == 0 and self.stop(s.lab, s.id))
        s._timer.daemon = True
        s._timer.start()

    def attach(self, s: WiresharkSession) -> None:
        s.viewers += 1
        if s._timer:
            s._timer.cancel()
            s._timer = None

    def detach(self, s: WiresharkSession) -> None:
        s.viewers = max(0, s.viewers - 1)
        if s.viewers == 0 and s.stopped is None:
            self._arm(s)
