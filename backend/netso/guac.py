"""Graphical consoles of guests in the browser: RDP through Apache guacd (container `guacamole/guacd`, Apache 2.0),
plus plain TCP forwards from the NetSO server for HTTP/HTTPS/WinBox.

Guacamole protocol (public spec): instructions are `len.value,len.value,...;` (len in characters). The handshake
with guacd is select -> args -> size/audio/video/image/timezone + connect -> ready; after that bytes are relayed
between guacd and the browser's WebSocketTunnel (subprotocol "guacamole"), which first expects the tunnel id as
an internal instruction (empty opcode) and sends its own internal pings that must not reach guacd.
"""
from __future__ import annotations

import asyncio
import codecs
import secrets
import time
import uuid
from dataclasses import dataclass, field

from .config import settings
from .hostcmd import Runner

GUACD_IMAGE = "guacamole/guacd:1.5.5"
GUACD_NAME = "netso-guacd"
GUACD_PORT = 4822  # published on 127.0.0.1 only


def encode(*elements) -> str:
    return ",".join(f"{len(str(e))}.{e}" for e in elements) + ";"


def split(buf: str) -> tuple[list[list[str]], list[str], str]:
    """Complete instructions in buf -> (parsed, raw texts, rest still incomplete)."""
    parsed, raw, start = [], [], 0
    while True:
        j, elems = start, []
        while True:
            dot = buf.find(".", j)
            if dot < 0:
                return parsed, raw, buf[start:]
            n = int(buf[j:dot])
            end = dot + 1 + n
            if end >= len(buf):
                return parsed, raw, buf[start:]
            elems.append(buf[dot + 1:end])
            j = end + 1
            if buf[end] == ";":
                break
            if buf[end] != ",":
                raise ValueError("istruzione Guacamole non valida")
        parsed.append(elems)
        raw.append(buf[start:j])
        start = j


def ensure_guacd(r: Runner) -> None:
    """Start the guacd container once (on the management network, so it reaches the guests' mgmt IPs)."""
    from .drivers.docker import DockerDriver
    if r.run(["docker", "inspect", "-f", "{{.State.Running}}", GUACD_NAME], check=False, fake="true").strip() == "true":
        return
    r.run(["docker", "rm", "-f", GUACD_NAME], check=False)
    DockerDriver(r).ensure_mgmt_network()
    r.run(["docker", "run", "-d", "--name", GUACD_NAME, "--restart", "unless-stopped", "--network", settings.docker_mgmt_network,
           "-p", f"127.0.0.1:{GUACD_PORT}:4822", GUACD_IMAGE])


# ------------------------------------------------------------------ RDP tickets (credentials never go in a URL)

@dataclass
class Ticket:
    lab: str
    node: str
    params: dict
    expires: float = field(default_factory=lambda: time.time() + 60)


_tickets: dict[str, Ticket] = {}


def new_ticket(lab: str, node: str, params: dict) -> str:
    now = time.time()
    for k in [k for k, t in _tickets.items() if t.expires < now]:
        _tickets.pop(k, None)
    key = secrets.token_urlsafe(24)
    _tickets[key] = Ticket(lab, node, params)
    return key


def take_ticket(key: str, lab: str, node: str) -> Ticket | None:
    t = _tickets.pop(key or "", None)
    return t if t and t.lab == lab and t.node == node and t.expires >= time.time() else None


def rdp_params(console: str, ip: str, user: str = "", password: str = "") -> dict:
    """guacd RDP settings: without credentials the Windows login screen is shown (no NLA)."""
    sec = ("tls" if console == "rdp-tls" else "rdp") if not user else "any"
    return {"hostname": ip, "port": "3389", "security": sec, "ignore-cert": "true", "username": user, "password": password,
            "resize-method": "display-update", "enable-wallpaper": "false", "server-layout": "it-it-qwerty"}


async def handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, protocol: str, params: dict,
                    width: int, height: int):
    """Do the guacd handshake; returns (text guacd already sent after `args`, the utf-8 decoder) for the relay."""
    writer.write(encode("select", protocol).encode())
    await writer.drain()
    dec, buf = codecs.getincrementaldecoder("utf-8")(), ""
    while True:
        data = await asyncio.wait_for(reader.read(65536), 15)
        if not data:
            raise ConnectionError("guacd ha chiuso la connessione")
        buf += dec.decode(data)
        parsed, _, rest = split(buf)
        if parsed:
            break
    args = parsed[0]
    if args[0] == "error":
        raise ConnectionError(f"guacd: {args[1] if len(args) > 1 else 'errore'}")
    names = args[1:]
    values = [n if n.startswith("VERSION_") else params.get(n, "") for n in names]
    out = (encode("size", width, height, 96) + encode("audio") + encode("video")
           + encode("image", "image/png", "image/jpeg", "image/webp") + encode("timezone", "Europe/Rome")
           + encode("connect", *values))
    writer.write(out.encode())
    await writer.drain()
    return rest, dec


def tunnel_uuid_instruction() -> str:
    return encode("", str(uuid.uuid4()))


# ------------------------------------------------------------------ TCP forwards (HTTP, HTTPS, WinBox)

_forwards: dict[tuple[str, int], asyncio.base_events.Server] = {}


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        dst.close()


async def forward(ip: str, port: int) -> int:
    """A port on the NetSO server that leads to ip:port (reused while NetSO runs)."""
    srv = _forwards.get((ip, port))
    if srv is None or not srv.is_serving():
        async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter):
            try:
                tr, tw = await asyncio.wait_for(asyncio.open_connection(ip, port), 10)
            except (OSError, asyncio.TimeoutError):
                w.close()
                return
            await asyncio.gather(_pipe(r, tw), _pipe(tr, w))

        srv = await asyncio.start_server(handle, "0.0.0.0", 0)
        _forwards[(ip, port)] = srv
    return srv.sockets[0].getsockname()[1]
