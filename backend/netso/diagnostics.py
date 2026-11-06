"""Live diagnostics and config: run commands on a running lab node and return the output.

The NetSO Assistant uses `run()` (READ-ONLY) to troubleshoot ("why isn't the OSPF adjacency up?"): only
read-only commands pass the filter (show…, ping, traceroute, ip show…), with no shell chaining/redirection.
`apply_config()` WRITES configuration lines the assistant proposed — callers must get the user's explicit
confirmation first; it is never reached from the read-only path.
Docker nodes (e.g. FRRouting) run through the container's CLI; VM / Cisco IOL / Dynamips nodes run on their
serial console (telnet), the same channel used to save the running-config.
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


def apply_config(lab_name: str, node_name: str, lines: list[str], timeout: int = 60) -> str:
    """Apply configuration LINES (device CLI, without «configure terminal»/«end» — added here) on a running node.

    This WRITES to the node: callers must have the user's explicit confirmation before calling it.
    """
    lines = [ln.rstrip() for ln in (lines or []) if ln and ln.strip()]
    if not lines:
        raise DiagError("Nessuna riga di configurazione da applicare")
    for ln in lines:
        if "\n" in ln or "\r" in ln:
            raise DiagError("Riga di configurazione non valida (ritorno a capo nel testo)")
    lab = topo.load_lab(lab_name)
    node = next((x for x in lab.nodes if x.name == node_name), None)
    if node is None:
        raise DiagError(f"Nodo «{node_name}» inesistente nel lab")
    tpl = topo.load_templates().get(node.template)
    state = engine.refresh(lab_name)
    ns = state.nodes.get(node_name)
    if not ns or ns.status != "running":
        raise DiagError(f"Il nodo «{node_name}» è spento: accendilo per configurarlo")
    # Docker nodes (FRR…): one vtysh session, enter config mode, apply every line
    if tpl and tpl.kind == "docker" and ns.container:
        argv = ["vtysh", "-c", "configure terminal"]
        for ln in lines:
            argv += ["-c", ln]
        out = engine.r.run(["docker", "exec", ns.container, *argv], check=False,
                           fake=f"(dry-run) «{'; '.join(lines)}» non applicato su {node_name}")
        return (out or "").strip() or "(configurazione applicata)"
    # VM / Cisco IOL / Dynamips: type the lines on the serial console, wrapped in «conf t … end»
    if tpl and tpl.kind in ("qemu", "iol", "dynamips") and ns.console_port:
        if engine.r.dry_run:
            return f"(dry-run) consolle :{ns.console_port} <- conf t; {'; '.join(lines)}; end"
        try:
            text = savecfg.console_config("127.0.0.1", ns.console_port,
                                          ["configure terminal", *lines, "end"], enable=True)
        except savecfg.SaveError as exc:
            raise DiagError(str(exc)) from exc
        return text.strip() or "(configurazione applicata)"
    raise DiagError(f"Configurazione non disponibile su «{node_name}»: nessuna consolle (serve un nodo FRR, o una VM/IOL/Dynamips con consolle telnet)")
