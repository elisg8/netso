"""Command line: `netso serve | deploy | destroy | list | inspect | console | templates | images | passwd`."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import topology as topo
from .config import settings


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*map(str, r)))


def cmd_serve(a: argparse.Namespace) -> None:
    import uvicorn
    if a.host not in ("127.0.0.1", "localhost") and not settings.auth and not settings.token:
        print("[netso] ATTENZIONE: API esposta in rete senza login (NETSO_AUTH=0) né NETSO_TOKEN", file=sys.stderr)
    uvicorn.run("netso.api:app", host=a.host, port=a.port, log_level="info")


def cmd_deploy(a: argparse.Namespace) -> None:
    from .engine import engine
    if a.lab.endswith((".yml", ".yaml")) or Path(a.lab).is_file():
        lab = topo.parse_lab_yaml(Path(a.lab).read_text())
        topo.validate_lab(lab)
        topo.save_lab(lab)
        name = lab.name
        print(f"Lab '{name}' importato da {a.lab}")
    else:
        name = a.lab
    state = engine.deploy(name)
    rows = []
    for n, s in state.nodes.items():
        access = ""
        if s.console_port:
            access = f"telnet :{s.console_port}"
        if s.vnc_display is not None:
            access += f"  vnc :{5900 + s.vnc_display}"
        if s.container:
            access = f"netso console {name} {n}"
        rows.append([n, s.kind, s.status, access.strip(), s.error or ""])
    _print_table(rows, ["NODO", "TIPO", "STATO", "ACCESSO", "ERRORE"])
    if state.error:
        sys.exit(1)


def cmd_destroy(a: argparse.Namespace) -> None:
    from .engine import engine
    engine.destroy(a.lab)
    print(f"Lab '{a.lab}' fermato")
    if a.delete:
        topo.delete_lab(a.lab)
        print(f"Lab '{a.lab}' eliminato")


def cmd_list(_a: argparse.Namespace) -> None:
    from .engine import engine
    rows = []
    for name in topo.list_labs():
        st = engine.refresh(name)
        running = sum(1 for s in st.nodes.values() if s.status == "running")
        rows.append([name, st.status, f"{running}/{len(st.nodes)}"])
    _print_table(rows, ["LAB", "STATO", "NODI ATTIVI"]) if rows else print("Nessun lab")


def cmd_inspect(a: argparse.Namespace) -> None:
    from .engine import engine
    st = engine.refresh(a.lab)
    print(st.model_dump_json(indent=2))


def cmd_console(a: argparse.Namespace) -> None:
    from .engine import engine
    cmd = engine.console_command(a.lab, a.node)
    os.execvp(cmd[0], cmd)


def cmd_templates(_a: argparse.Namespace) -> None:
    rows = [[t.name, t.kind, t.image or "-", f"{t.cpus}/{t.ram}MB", t.description]
            for t in topo.load_templates().values()]
    _print_table(rows, ["TEMPLATE", "TIPO", "IMMAGINE", "CPU/RAM", "DESCRIZIONE"])


def cmd_passwd(a: argparse.Namespace) -> None:
    import getpass
    from . import auth
    pw = a.password or getpass.getpass(f"Nuova password per {a.user}: ")
    auth.set_password(a.user, pw)
    print(f"Password di '{a.user}' aggiornata")


def cmd_images(_a: argparse.Namespace) -> None:
    from .drivers.qemu import list_images
    rows = [[i["name"], ", ".join(i["disks"]), f"{i['size'] / 2**30:.1f} GB"] for i in list_images()]
    _print_table(rows, ["IMMAGINE", "DISCHI", "DIMENSIONE"]) if rows else print(
        f"Nessuna immagine in {settings.images}")


class _CliJob:
    """Job stand-in for long CLI commands: progress on one terminal line (percentage + current file)."""

    def __init__(self):
        self.last = 0.0
        self.progress = None

    def update(self, progress=None, message=None):
        import time
        if progress is not None:
            self.progress = progress
        now = time.monotonic()
        if now - self.last < 0.5 and progress != 1:
            return
        self.last = now
        pct = f"{self.progress * 100:5.1f}% " if self.progress is not None else ""
        sys.stdout.write(f"\r\033[K  {pct}{(message or '')[:100]}")
        sys.stdout.flush()


def cmd_pull(a: argparse.Namespace) -> None:
    from . import dockerimg
    from .hostcmd import runner
    refs = a.refs or [t["ref"] for t in dockerimg.describe(runner)["templates"] if t["pullable"] and not t["present"]]
    if not refs:
        print("Tutte le immagini Docker dei dispositivi sono già presenti")
    for ref in refs:
        print(f"docker pull {ref} …", flush=True)
        try:
            print("\r\033[K" + dockerimg.pull(runner, _CliJob(), ref))
        except ValueError as exc:
            print(f"  errore: {exc}", file=sys.stderr)


def cmd_import_pnet(a: argparse.Namespace) -> None:
    from . import pnetimport
    from .hostcmd import runner
    found = pnetimport.scan(a.source, runner)
    if a.only:
        import fnmatch
        found["items"] = [i for i in found["items"] if any(fnmatch.fnmatch(i["folder"].lower(), p.lower()) for p in a.only)]
    rows = [[i["folder"], i["kind"], f"{i['size'] / 2**30:.1f} GB", "già presente" if i["present"] else i["action"]] for i in found["items"]]
    _print_table(rows, ["IMMAGINE", "TIPO", "DIMENSIONE", "AZIONE"]) if rows else None
    todo = sum(i["size"] for i in found["items"] if not i["present"])
    where = settings.images
    while not where.exists():
        where = where.parent
    free = shutil.disk_usage(where).free
    print(f"Da copiare: {todo / 2**30:.1f} GB · liberi in {settings.images}: {free / 2**30:.1f} GB")
    for n in found["notes"]:
        print(n)
    if not a.yes and input("Importare? [s/N] ").strip().lower() not in ("s", "si", "sì", "y"):
        return
    print("Copia in corso: avanzamento qui sotto (non chiudere il terminale)")
    print("\r\033[K" + pnetimport.run_import(runner, _CliJob(), a.source, [i["src"] for i in found["items"]], a.move))


def cmd_import_labs(a: argparse.Namespace) -> None:
    import re as _re
    from pathlib import Path
    from . import labcatalog, labconvert, topology as topo
    tpls = topo.load_templates()
    if a.router not in tpls:
        print(f"Router '{a.router}' non installato. Disponibili: {', '.join(sorted(tpls))}")
        return
    src = Path(a.source).expanduser()
    files = sorted(p for p in src.rglob("*.txt") if p.is_file())
    if not files:
        print(f"Nessun file .txt in {src}")
        return
    rows, done = [], 0
    for f in files:
        rel = f.relative_to(src)
        topic = _re.sub(r"^\d+\s*", "", rel.parts[0]) if len(rel.parts) > 1 else "Lab"
        name_part = " / ".join([*rel.parts[1:-1], f.stem])
        label = f"{topic} · {name_part}"
        cid = labcatalog.slug(str(rel.with_suffix("")))      # unique even across nested task folders
        try:
            name = _re.sub(r"[^A-Za-z0-9]+", "-", f"{topic}-{name_part}").strip("-")[:48] or "lab"
            lab, configs, instr = labconvert.convert(name, f.read_text(errors="replace"), tpls, a.router, topic=label)
            if not lab.nodes:
                rows.append([label, "—", "saltato (nessun router)"]); continue
            nr = sum(1 for n in lab.nodes if n.template == a.router)
            meta = {"label": label, "cat": f"I miei lab · {topic}", "desc": f"{nr} router · config iniziali pronte", "router": a.router}
            labcatalog.save_catalog(cid, lab, configs, meta, instructions=instr)
            rows.append([label, f"{nr} router, {len(lab.links)} link", "importato"]); done += 1
        except Exception as exc:  # noqa: BLE001 — report and keep going
            rows.append([label, "—", f"errore: {exc}"])
    _print_table(rows, ["LAB", "TOPOLOGIA", "ESITO"])
    print(f"\nImportati {done}/{len(files)} lab in «Genera lab» → categoria «I miei lab» (router: {a.router}).")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="netso", description="NetSO - lab di rete all-in-one")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="avvia API e interfaccia web")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("deploy", help="avvia un lab (nome o file .yml)")
    s.add_argument("lab")
    s.set_defaults(func=cmd_deploy)

    s = sub.add_parser("destroy", help="ferma un lab")
    s.add_argument("lab")
    s.add_argument("--delete", action="store_true", help="elimina anche definizione e dischi")
    s.set_defaults(func=cmd_destroy)

    sub.add_parser("list", help="elenca i lab").set_defaults(func=cmd_list)

    s = sub.add_parser("inspect", help="stato dettagliato di un lab")
    s.add_argument("lab")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("console", help="apre la consolle di un nodo")
    s.add_argument("lab")
    s.add_argument("node")
    s.set_defaults(func=cmd_console)

    sub.add_parser("templates", help="elenca i template").set_defaults(func=cmd_templates)
    sub.add_parser("images", help="elenca le immagini QEMU").set_defaults(func=cmd_images)
    s = sub.add_parser("pull", help="scarica immagini Docker (senza argomenti: quelle dei dispositivi che mancano)")
    s.add_argument("refs", nargs="*")
    s.set_defaults(func=cmd_pull)
    s = sub.add_parser("import-pnet", help="importa le immagini da un server EVE-NG/PNETLab")
    s.add_argument("source", help="cartella (es. /opt/unetlab/addons) oppure root@host:/opt/unetlab/addons")
    s.add_argument("--only", nargs="+", metavar="MODELLO", help="solo queste immagini, es. --only 'vios*' 'c7200*' 'asav*'")
    s.add_argument("--move", action="store_true", help="sposta invece di copiare (solo cartelle locali)")
    s.add_argument("-y", "--yes", action="store_true", help="non chiedere conferma")
    s.set_defaults(func=cmd_import_pnet)
    s = sub.add_parser("import-labs", help="converte una cartella di config iniziali (.txt) in lab di «Genera lab»")
    s.add_argument("source", help="cartella con i .txt (anche in sottocartelle per argomento)")
    s.add_argument("--router", default="cisco-iosv", help="template router su cui mappare i nodi (default: cisco-iosv)")
    s.set_defaults(func=cmd_import_labs)
    s = sub.add_parser("passwd", help="crea un utente o ne cambia la password")
    s.add_argument("user", nargs="?", default="admin")
    s.add_argument("--password", help="se omessa viene chiesta")
    s.set_defaults(func=cmd_passwd)

    a = p.parse_args(argv)
    try:
        a.func(a)
    except (topo.TopologyError, FileNotFoundError, ValueError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        from .engine import EngineError
        from .hostcmd import HostError
        if isinstance(exc, (EngineError, HostError)):
            print(f"Errore: {exc}", file=sys.stderr)
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
