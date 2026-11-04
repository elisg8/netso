"""Browser consoles: a websocket bridged to a process running in a pseudo-terminal.

Client -> server messages are JSON:  {"t": "i", "d": "<keys>"}  or  {"t": "r", "c": cols, "r": rows}
Server -> client messages are plain text (terminal output).
"""
from __future__ import annotations

import asyncio
import codecs
import fcntl
import json
import os
import pty
import signal
import struct
import termios
import threading
import time

from fastapi import WebSocket, WebSocketDisconnect


def _set_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def bridge_pty(ws: WebSocket, cmd: list[str]) -> None:
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.environ["TERM"] = "xterm-256color"
        try:
            os.execvp(cmd[0], cmd)
        except OSError as exc:
            os.write(1, f"\r\nImpossibile avviare {cmd[0]}: {exc}\r\n".encode())
            os._exit(127)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def on_readable() -> None:
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        queue.put_nowait(data or None)
        if not data:
            loop.remove_reader(fd)

    loop.add_reader(fd, on_readable)

    async def pump_out() -> None:
        while True:
            data = await queue.get()
            if data is None:
                await ws.send_text("\r\n\x1b[33m[sessione terminata]\x1b[0m\r\n")
                await ws.close()
                return
            await ws.send_text(decoder.decode(data))

    async def pump_in() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("t") == "i":
                os.write(fd, msg.get("d", "").encode())
            elif msg.get("t") == "r":
                _set_size(fd, int(msg.get("c", 80)), int(msg.get("r", 24)))

    tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        try:
            loop.remove_reader(fd)
        except Exception:
            pass
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        threading.Thread(target=_reap, args=(pid,), daemon=True).start()


def _reap(pid: int) -> None:
    """Collect the child; force-kill it if SIGHUP was ignored."""
    for _ in range(30):
        try:
            done, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if done:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass
