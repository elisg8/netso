"""Save the running configuration of the devices back into a lab config set
(containerlab `save`, CML "extract configs", EVE-NG/PNETLab "Export CFG").

Containers: the template's `save_cmd` runs with docker exec. VMs: the template's `save_cli` commands are typed on
the serial console (telnet to the QEMU port) and the output of the last one is kept.
"""
from __future__ import annotations

import re
import socket
import time

from . import topology as topo
from .hostcmd import Runner
from .jobs import Job

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
PROMPT = re.compile(rb"(?:^|[\r\n])([A-Za-z0-9_.\-()/:@~]+ ?[#>$%]) ?$")
LOGIN = re.compile(rb"(Username|login|Password)\s*:\s*$", re.I)
DIALOG = re.compile(rb"initial configuration dialog\?.*:\s*$|\[yes/no\]:?\s*$", re.I | re.S)
MORE = re.compile(rb"--\s*More\s*--|<--- More --->")
NOISE = re.compile(r"^(Building configuration\.\.\.|Current configuration\s*:.*)$")


class SaveError(RuntimeError):
    pass


class Console:
    """Minimal telnet client for a QEMU serial console (no telnetlib: removed in Python 3.13)."""

    def __init__(self, host: str, port: int, timeout: float = 5):
        try:
            self.s = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise SaveError(f"console not reachable ({exc.strerror or exc})") from exc
        self.s.settimeout(0.3)
        self.buf = b""

    def _filter(self, data: bytes) -> bytes:
        out, i = bytearray(), 0
        while i < len(data):
            b = data[i]
            if b == IAC and i + 1 < len(data):
                cmd = data[i + 1]
                if cmd in (DO, DONT, WILL, WONT) and i + 2 < len(data):
                    opt = data[i + 2]
                    reply = {DO: WONT, DONT: WONT, WILL: DO if opt in (1, 3) else DONT, WONT: DONT}[cmd]  # echo/SGA on
                    self.s.sendall(bytes([IAC, reply, opt]))
                    i += 3
                    continue
                if cmd == SB:
                    end = data.find(bytes([IAC, SE]), i)
                    i = end + 2 if end >= 0 else len(data)
                    continue
                i += 2
                continue
            out.append(b)
            i += 1
        return bytes(out)

    def read_some(self) -> bytes:
        try:
            data = self.s.recv(65536)
        except (TimeoutError, socket.timeout):
            return b""
        if not data:
            raise SaveError("the console closed the connection")
        data = self._filter(data)
        self.buf += data
        return data

    def send(self, text: str) -> None:
        self.s.sendall(text.encode() + b"\r")

    def wait(self, timeout: float, poke: float | None = None) -> bytes:
        """Read until the buffer ends with a prompt (or a login / dialog question). Returns the buffer and clears it."""
        end, last_poke = time.time() + timeout, time.time()
        while time.time() < end:
            self.read_some()
            tail = self.buf[-300:].rstrip(b" ")
            if MORE.search(self.buf[-80:]):
                self.buf = MORE.sub(b"", self.buf)
                self.s.sendall(b" ")
                continue
            if PROMPT.search(tail) or LOGIN.search(tail) or DIALOG.search(tail):
                out, self.buf = self.buf, b""
                return out
            if poke and time.time() - last_poke > poke:
                self.s.sendall(b"\r")
                last_poke = time.time()
        raise SaveError("no prompt from the console (is the device still booting?)")

    def close(self) -> None:
        try:
            self.s.close()
        except OSError:
            pass


def _clean(out: bytes, command: str) -> str:
    text = out.decode("utf-8", "replace").replace("\r", "")
    lines = text.split("\n")
    # drop the echoed command and the final prompt
    while lines and command.strip() and command.strip() not in lines[0] and len(lines) > 1:
        lines.pop(0)
    if lines and command.strip() in lines[0]:
        lines.pop(0)
    if lines and PROMPT.search(lines[-1].encode()):
        lines.pop()
    lines = [ln.rstrip() for ln in lines if not NOISE.match(ln.strip())]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def console_config(host: str, port: int, commands: list[str], enable: bool = False, boot_wait: float = 30) -> str:
    c = Console(host, port)
    try:
        c.s.sendall(b"\r")
        first = c.wait(boot_wait, poke=3)
        if DIALOG.search(first.rstrip()):
            c.send("no")
            first = c.wait(30, poke=5)
        if LOGIN.search(first.rstrip()):
            raise SaveError("the device is asking for username/password on the console")
        prompt = (PROMPT.search(first.rstrip()) or [None, b""])[1]
        if prompt.rstrip().endswith(b">") and enable:
            c.send("enable")
            out = c.wait(10)
            if LOGIN.search(out.rstrip()):
                raise SaveError("'enable' is asking for a password")
        result = ""
        for cmd in commands:
            c.send(cmd)
            result = _clean(c.wait(60), cmd)
        return result
    finally:
        c.close()


def container_config(r: Runner, container: str, cmd: list[str]) -> str:
    out = r.run(["docker", "exec", container, *cmd], fake="! configurazione di prova\nhostname dry-run\n")
    lines = [ln.rstrip() for ln in out.replace("\r", "").split("\n") if not NOISE.match(ln.strip())]
    return "\n".join(lines).strip() + "\n"


def save_configs(r: Runner, job: Job | None, lab_name: str, nodes: list[str] | None, cfg_set: str,
                 state, host: str = "127.0.0.1") -> dict[str, str]:
    """Returns {node: "ok" | error message}."""
    lab = topo.load_lab(lab_name)
    templates = topo.load_templates()
    targets = [n for n in lab.nodes if nodes is None or n.name in nodes]
    results: dict[str, str] = {}
    for k, n in enumerate(targets):
        tpl = templates.get(n.template)
        ns = state.nodes.get(n.name)
        if job:
            job.update(k / max(1, len(targets)), f"{n.name}…")
        if tpl is None or tpl.kind not in ("qemu", "docker", "dynamips", "iol"):
            continue
        if not ns or ns.status != "running":
            results[n.name] = "powered off"
            continue
        try:
            if tpl.kind == "docker":
                if not tpl.save_cmd:
                    raise SaveError(f"the template {tpl.name} cannot export the config")
                text = container_config(r, ns.container or "", tpl.save_cmd)
            else:
                if not tpl.save_cli or not ns.console_port:
                    raise SaveError(f"the template {tpl.name} cannot export the config from the console")
                if r.dry_run:
                    r.log.append(f"console {host}:{ns.console_port} <- {tpl.save_cli}")
                    text = f"! test config for {n.name}\nhostname {n.name}\n"
                else:
                    text = console_config(host, ns.console_port, tpl.save_cli, tpl.save_enable)
            if not text.strip():
                raise SaveError("empty configuration")
            topo.write_config(lab_name, cfg_set, n.name, text)
            results[n.name] = "ok"
        except Exception as exc:
            results[n.name] = str(exc)
    return results
