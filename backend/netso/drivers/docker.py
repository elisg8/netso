"""Docker container nodes (driven through the docker CLI)."""
from __future__ import annotations

import re

from ..config import PACKAGE_DIR, settings
from ..hostcmd import HostError, Runner
from ..models import NodeState, ResolvedNode


def container_name(lab: str, node: str) -> str:
    # Docker names allow only [A-Za-z0-9_.-], so lab/node names with spaces are sanitised here
    safe = lambda s: re.sub(r"[^A-Za-z0-9_.-]", "_", s)
    return f"netso-{safe(lab)}-{safe(node)}"


class DockerDriver:
    def __init__(self, runner: Runner):
        self.r = runner

    def ensure_mgmt_network(self) -> None:
        net = settings.docker_mgmt_network
        if self.r.run(["docker", "network", "ls", "-q", "-f", f"name=^{net}$"], fake=""):
            return
        self.r.run(["docker", "network", "create", "--driver", "bridge", "--subnet", settings.docker_mgmt_subnet, net])

    def mgmt_bridge(self) -> str:
        """Linux bridge behind the management network (VM management NICs plug into it)."""
        self.ensure_mgmt_network()
        nid = self.r.run(["docker", "network", "inspect", "-f", "{{.Id}}", settings.docker_mgmt_network], fake="0123456789abcdef").strip()
        return f"br-{nid[:12]}"

    def mgmt_ip(self, name: str) -> str | None:
        out = self.r.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", name],
                         check=False, fake="172.20.20.2").split()
        return out[0] if out else None

    def ensure_image(self, image: str) -> None:
        """Pull a missing image up front, so a vendor image nobody built/loaded gets a useful error."""
        if self.r.run(["docker", "images", "-q", image], check=False, fake="x"):
            return
        try:
            self.r.run(["docker", "pull", image])
        except HostError as exc:
            how = ("build it with vrnetlab (srl-labs/vrnetlab) using your own files" if image.startswith("vrnetlab/")
                   else "load it from Devices → Docker → Load .tar")
            raise RuntimeError(f"The Docker image '{image}' is not on the server and cannot be downloaded: {how}. "
                               "If you already have it under another name:tag, set it in Edit node → Image "
                               "(the list is in Devices → Docker)") from exc

    def start(self, lab: str, node: ResolvedNode, state: NodeState, config: str | None = None) -> int:
        """Start the container and return its PID (needed to plug veths)."""
        name = container_name(lab, node.name)
        self.ensure_image(node.image or "")
        self.r.run(["docker", "rm", "-f", name], check=False)
        self.ensure_mgmt_network()
        # with a startup-config: create, copy the file in, then start
        with_cfg = bool(config) and node.config_method == "copy" and bool(node.config_file)
        staged = with_cfg or bool(node.assets)  # files to copy in before the first start
        cmd = [
            "docker", "create" if staged else "run", *([] if staged else ["-d"]),
            "--name", name,
            "--hostname", node.name,
            "--network", settings.docker_mgmt_network,
            *(["--ip", node.mgmt_ip] if node.mgmt_ip else []),
            "--label", f"netso.lab={lab}",
            "--label", f"netso.node={node.name}",
            "--cpus", str(node.cpus),
            "--memory", f"{node.ram}m",
        ]
        if node.privileged:
            cmd.append("--privileged")
        for k, v in node.env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd.append(node.image or "")
        if node.cmd:
            cmd += node.cmd
        self.r.run(cmd)
        for path, asset in (node.assets or {}).items():  # helper scripts shipped with NetSO (e.g. the VPC shell)
            self.r.run(["docker", "cp", str(PACKAGE_DIR / "assets" / asset), f"{name}:{path}"])
        if with_cfg:
            src = settings.labs_dir / lab / "nodes" / node.name / "startup-config.txt"
            self.r.log.append(f"write {src}")
            if not self.r.dry_run:
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_text(config or "")
            self.r.run(["docker", "cp", str(src), f"{name}:{node.config_file}"])
        if staged:
            self.r.run(["docker", "start", name])
        pid = int(self.r.run(["docker", "inspect", "-f", "{{.State.Pid}}", name], fake="4242") or 0)
        if pid <= 0:
            raise RuntimeError(f"Container {name} did not start (check 'docker logs {name}')")
        state.container = name
        state.mgmt_ip = self.mgmt_ip(name)
        state.pid = pid
        return pid

    def stop(self, state: NodeState) -> None:
        if state.container:
            self.r.run(["docker", "rm", "-f", state.container], check=False)
        state.pid = None

    def is_running(self, state: NodeState) -> bool:
        if not state.container:
            return False
        out = self.r.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", state.container], check=False, fake="true"
        )
        return out.strip() == "true"

    @staticmethod
    def console_command(state: NodeState, shell: list[str] | None = None) -> list[str]:
        if shell:  # the device's own CLI (sr_cli, Cli, vtysh...)
            return ["docker", "exec", "-it", state.container or "", *shell]
        shell = "command -v bash >/dev/null 2>&1 && exec bash || exec sh"
        return ["docker", "exec", "-it", state.container or "", "sh", "-c", shell]
