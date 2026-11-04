"""Live diagnostics: run a READ-ONLY command on a running lab node and return its output.

The NetSO Assistant uses these to troubleshoot ("why isn't the OSPF adjacency up?"). Only read-only
commands pass the filter (show…, ping, traceroute, ip show…), with no shell chaining/redirection.
Docker nodes (e.g. FRRouting) run the command in the container; VM / Cisco IOL / Dynamips nodes run it
on their serial console (telnet), the same channel used to save the running-config.
"""
from __future__ import annotations

import re
import shlex

from . import savecfg
from . import topology as topo
from .engine import engine


class DiagError(Exception):
    pass


# read-only shell verbs allowed on a node's shell (device CLI `show …` is handled separately)
SAFE_SHELL = {"ping", "ping6", "traceroute", "traceroute6", "tracepath", "ip", "ss", "netstat", "bridge",
              "cat", "ls", "ps", "ifconfig", "route", "arp", "mtr", "nslookup", "dig", "uptime", "hostname"}
_WRITE_IP = re.compile(r"^ip\b.*\b(add|del|delete|change|replace|flush|set|up|down)\b", re.I)
_BAD = (";", "|", "&", "`", "$(", ">", "<", "\n")


def is_readonly(command: str) -> bool:
    c = (command or "").strip()
    if not c or any(tok in c for tok in _BAD):
        return False
    if c.lower().startswith("show "):     # FRR/IOS-style device CLI
        return True
    try:
        verb = shlex.split(c)[0]
    except ValueError:
        return False
    if verb not in SAFE_SHELL:
        return False
    return not _WRITE_IP.match(c)


def run(lab_name: str, node_name: str, command: str, timeout: int = 15) -> str:
    """Run one read-only command on a running Docker node and return its output (raises DiagError)."""
    command = (command or "").strip()
    if not is_readonly(command):
        raise DiagError(f"Comando non di sola lettura, rifiutato: «{command}»")
    lab = topo.load_lab(lab_name)
    node = next((x for x in lab.nodes if x.name == node_name), None)
    if node is None:
        raise DiagError(f"Nodo «{node_name}» inesistente nel lab")
    tpl = topo.load_templates().get(node.template)
    state = engine.refresh(lab_name)
    ns = state.nodes.get(node_name)
    if not ns or ns.status != "running":
        raise DiagError(f"Il nodo «{node_name}» è spento: accendilo per la diagnostica live")
    # Docker nodes (FRR…): run through the container's CLI
    if tpl and tpl.kind == "docker" and ns.container:
        argv = ["vtysh", "-c", command] if command.lower().startswith("show ") else shlex.split(command)
        out = engine.r.run(["docker", "exec", ns.container, *argv], check=False,
                           fake=f"(dry-run) nessun nodo reale: «{command}» non eseguito")
        return (out or "").strip() or "(nessun output)"
    # VM / Cisco IOL / Dynamips: type the command on the serial console (telnet), like saving the config
    if tpl and tpl.kind in ("qemu", "iol", "dynamips") and ns.console_port:
        if engine.r.dry_run:
            return f"(dry-run) consolle :{ns.console_port} <- «{command}» non eseguito"
        try:  # «terminal length 0» first so long outputs don't page; enable mode for «show running-config»
            text = savecfg.console_config("127.0.0.1", ns.console_port, ["terminal length 0", command], enable=True)
        except savecfg.SaveError as exc:
            raise DiagError(str(exc)) from exc
        return text.strip() or "(nessun output)"
    raise DiagError(f"Diagnostica live non disponibile su «{node_name}»: nessuna consolle (serve un nodo FRR, o una VM/IOL/Dynamips con consolle telnet)")
