"""Runs one Cisco IOL instance (the user's own binary and iourc) and connects it to the lab.

  python -m netso.iolwrap --dir D --bin B --id N --port P --ram MB --eth GROUPS [--config F] [--iourc F] \
      --tap 0/0=nstxxxx --tap 0/1=nstyyyy ...

* Console: IOL runs on a pseudo-terminal; its screen is served over telnet on --port (several viewers can
  attach, the last output is replayed to a new one).
* Network: IOL instances talk through unix datagram sockets in /tmp/netio<uid>/<id>, routed by the NETMAP
  file in their working directory. This process plays the peer instance <id>+512: for every interface it
  maps <id>:bay/unit to its own port k/k and moves frames between that socket and the interface's tap.
  Frame on the socket = 8-byte header (dst id, src id, dst port, src port, 1, 0) + ethernet frame; a port
  byte is bay | unit << 4. Our ports use bay == unit so they read the same either way, and the byte IOL
  puts as source port is learnt from its frames.
Written from the publicly documented format; no code taken from GNS3/EVE-NG.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import pty
import select
import signal
import socket
import struct
import sys
import tty
from pathlib import Path

BRIDGE_OFFSET = 512  # peer id = IOL id + 512 (IOL ids 1-511)
TUNSETIFF = 0x400454CA
IFF_TAP, IFF_NO_PI = 0x0002, 0x1000
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
REPLAY = 16 << 10  # bytes of console output shown to a viewer that attaches later


def port_byte(bay: int, unit: int) -> int:
    return (bay & 0x0F) | ((unit & 0x0F) << 4)


def netmap(iol_id: int, ifaces: list[tuple[int, int]]) -> str:
    """NETMAP lines: interface i of the IOL <-> port i/i of the bridge instance."""
    br = iol_id + BRIDGE_OFFSET
    return "".join(f"{iol_id}:{bay}/{unit} {br}:{k}/{k}\n" for k, (bay, unit) in enumerate(ifaces))


def iol_args(binary: str, iol_id: int, ram: int, eth: int, config: str | None) -> list[str]:
    return [binary, "-e", str(eth), "-s", "0", "-m", str(ram), "-n", "1024",
            *(["-c", config] if config else []), str(iol_id)]


class Telnet:
    """Strips telnet commands from what a viewer types; turns CR NUL / CR LF into CR."""

    def __init__(self):
        self.state = 0  # 0 data, 1 after IAC, 2 option byte, 3 subnegotiation, 4 IAC in subnegotiation
        self.cr = False

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            if self.state == 1:
                self.state = 2 if b in (WILL, WONT, DO, DONT) else 3 if b == SB else 0
                if b == IAC:
                    out.append(IAC)
                continue
            if self.state == 2:
                self.state = 0
                continue
            if self.state == 3:
                self.state = 4 if b == IAC else 3
                continue
            if self.state == 4:
                self.state = 0 if b == SE else 3
                continue
            if b == IAC:
                self.state = 1
                continue
            if self.cr and b in (0, 10):
                self.cr = False
                continue
            self.cr = b == 13
            out.append(b)
        return bytes(out)


def open_tap(name: str) -> int:
    fd = os.open("/dev/net/tun", os.O_RDWR)
    fcntl.ioctl(fd, TUNSETIFF, struct.pack("16sH", name.encode(), IFF_TAP | IFF_NO_PI))
    os.set_blocking(fd, False)
    return fd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="netso-iolwrap")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--bin", required=True)
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--ram", type=int, default=512)
    ap.add_argument("--eth", type=int, default=2)
    ap.add_argument("--config")
    ap.add_argument("--iourc")
    ap.add_argument("--tap", action="append", default=[], help="bay/unit=tap")
    a = ap.parse_args(argv)

    os.chdir(a.dir)
    ifaces, taps = [], []
    for spec in a.tap:
        where, tap = spec.split("=", 1)
        bay, unit = (int(x) for x in where.split("/"))
        ifaces.append((bay, unit))
        taps.append(tap)
    Path("NETMAP").write_text(netmap(a.id, ifaces))

    netio = Path(f"/tmp/netio{os.getuid()}")
    netio.mkdir(mode=0o755, exist_ok=True)
    me, peer = netio / str(a.id + BRIDGE_OFFSET), netio / str(a.id)
    # IOL's own socket and lock file, left behind by a crash: else "sock lock: Address already in use" at the next start
    lock = netio / f"{a.id}.lck"
    me.unlink(missing_ok=True)
    peer.unlink(missing_ok=True)
    lock.unlink(missing_ok=True)
    dgram = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    dgram.bind(str(me))
    dgram.setblocking(False)
    tap_fds = [open_tap(t) for t in taps]
    by_fd = {fd: k for k, fd in enumerate(tap_fds)}
    out_port = [port_byte(bay, unit) for bay, unit in ifaces]  # corrected by what IOL sends

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", a.port))
    srv.listen(8)

    env = dict(os.environ)
    if a.iourc:
        env["IOURC"] = a.iourc
    pid, master = pty.fork()
    if pid == 0:  # IOL
        try:
            tty.setraw(0)
            os.execve(a.bin, iol_args(a.bin, a.id, a.ram, a.eth, a.config), env)
        except OSError as exc:
            os.write(1, f"\r\nNetSO: impossibile avviare IOL: {exc}\r\n".encode())
        os._exit(127)

    def bye(*_):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        me.unlink(missing_ok=True)
        peer.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    viewers: dict[socket.socket, Telnet] = {}
    history = bytearray()
    while True:
        rl, _, _ = select.select([master, srv, dgram, *tap_fds, *viewers], [], [], 1.0)
        for s in rl:
            if s is master:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    data = b""
                if not data:  # IOL ended
                    Path("iol-exit.log").write_bytes(bytes(history[-4096:]))
                    me.unlink(missing_ok=True)
                    peer.unlink(missing_ok=True)
                    lock.unlink(missing_ok=True)
                    os.waitpid(pid, 0)
                    return 1
                history += data
                del history[:-REPLAY]
                for v in list(viewers):
                    try:
                        v.sendall(data.replace(b"\xff", b"\xff\xff"))
                    except OSError:
                        viewers.pop(v, None)
                        v.close()
            elif s is srv:
                conn, _ = srv.accept()
                try:
                    conn.sendall(bytes([IAC, WILL, 1, IAC, WILL, 3, IAC, DONT, 34]) + bytes(history).replace(b"\xff", b"\xff\xff"))
                    viewers[conn] = Telnet()
                except OSError:
                    conn.close()
            elif s is dgram:
                try:
                    frame = dgram.recv(65600)
                except OSError:
                    continue
                if len(frame) < 8:
                    continue
                k = frame[4] & 0x0F  # our port k/k
                if k < len(tap_fds):
                    out_port[k] = frame[5]
                    try:
                        os.write(tap_fds[k], frame[8:])
                    except OSError:
                        pass
            elif s in viewers:
                try:
                    data = s.recv(4096)
                except OSError:
                    data = b""
                if not data:
                    viewers.pop(s, None)
                    s.close()
                    continue
                keys = viewers[s].feed(data)
                if keys:
                    os.write(master, keys)
            else:  # a tap
                k = by_fd[s]
                try:
                    data = os.read(s, 65536)
                except OSError:
                    continue
                hdr = struct.pack(">HHBBBB", a.id, a.id + BRIDGE_OFFSET, out_port[k], port_byte(k, k), 1, 0)
                try:
                    dgram.sendto(hdr + data, str(peer))
                except OSError:
                    pass  # IOL not listening yet


if __name__ == "__main__":
    sys.exit(main())
