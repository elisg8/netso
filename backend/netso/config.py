"""Runtime configuration, driven by environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def _paths(value: str) -> list[Path]:
    return [Path(p) for p in value.split(":") if p]


@dataclass
class Settings:
    # Where labs (definitions, overlays, runtime state) live
    home: Path = field(default_factory=lambda: Path(os.environ.get("NETSO_HOME", "/var/lib/netso")))
    # Base disk images for QEMU nodes: <images>/<image-name>/*.qcow2
    images: Path = field(default_factory=lambda: Path(os.environ.get("NETSO_IMAGES", "/opt/netso/images")))
    # Template directories, first match wins (user dir before built-in)
    template_dirs: list[Path] = field(default_factory=lambda: _paths(
        os.environ.get("NETSO_TEMPLATES", "/opt/netso/templates")
    ))
    # Login with user/password (users in <home>/users.json); NETSO_AUTH=0 turns it off
    auth: bool = os.environ.get("NETSO_AUTH", "1") != "0"
    # Optional fixed bearer token (scripts, CLI); accepted alongside user sessions
    token: str | None = field(default_factory=lambda: os.environ.get("NETSO_TOKEN") or None)
    # Built web UI
    web_dir: Path = field(default_factory=lambda: Path(os.environ.get("NETSO_WEB", "/opt/netso/web")))
    # Console / VNC port ranges
    console_port_start: int = int(os.environ.get("NETSO_CONSOLE_PORT_START", "32769"))
    vnc_display_start: int = int(os.environ.get("NETSO_VNC_DISPLAY_START", "10"))
    # Docker management network
    docker_mgmt_network: str = os.environ.get("NETSO_DOCKER_MGMT", "netso-mgmt")
    docker_mgmt_subnet: str = os.environ.get("NETSO_MGMT_SUBNET", "172.20.20.0/24")
    qemu_binary: str = os.environ.get("NETSO_QEMU", "qemu-system-x86_64")
    # Wireshark in the browser: image and loopback VNC port range for its containers
    wireshark_image: str = os.environ.get("NETSO_WIRESHARK_IMAGE", "netso/wireshark")
    wireshark_port_start: int = int(os.environ.get("NETSO_WIRESHARK_PORT_START", "15900"))
    # Dry run: print host commands instead of executing them (tests, previews)
    dry_run: bool = os.environ.get("NETSO_DRY_RUN", "0") == "1"

    @property
    def labs_dir(self) -> Path:
        return self.home / "labs"

    def user_template_dir(self) -> Path:
        """Where templates created from the UI are written (the first template dir)."""
        return self.template_dirs[0] if self.template_dirs else self.home / "templates"

    def all_template_dirs(self) -> list[Path]:
        dirs = list(self.template_dirs)
        if self.user_template_dir() not in dirs:
            dirs.insert(0, self.user_template_dir())
        return [*dirs, PACKAGE_DIR / "templates"]


settings = Settings()
