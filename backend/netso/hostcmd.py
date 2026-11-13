"""Thin wrapper around host commands so everything can run in dry-run mode."""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings


class HostError(RuntimeError):
    pass


@dataclass
class Runner:
    # None = follow settings.dry_run at call time
    force_dry_run: bool | None = None
    log: list[str] = field(default_factory=list)

    @property
    def dry_run(self) -> bool:
        return settings.dry_run if self.force_dry_run is None else self.force_dry_run

    def run(self, cmd: list[str], check: bool = True, capture: bool = True, fake: str = "") -> str:
        line = shlex.join(cmd)
        self.log.append(line)
        if self.dry_run:
            return fake
        proc = subprocess.run(cmd, capture_output=capture, text=True)
        if check and proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()
            raise HostError(f"Command failed ({proc.returncode}): {line}\n{msg}")
        return (proc.stdout or "").strip()

    def write(self, path: str | Path, value: str) -> None:
        self.log.append(f"echo {value} > {path}")
        if self.dry_run:
            return
        try:
            Path(path).write_text(value)
        except OSError:
            pass  # best effort (e.g. sysfs knobs missing on some kernels)


runner = Runner()
