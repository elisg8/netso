"""REST API + websocket consoles + static web UI."""
from __future__ import annotations

import asyncio
import hmac
import re
import socket

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import access as acl
from . import agents as agents_mod
from . import auth as users
from . import automation
from . import catalog
from . import diagnostics
from . import dockerimg
from . import icons as icons_mod
from . import images as imgs
from . import interop
from . import jobs
from . import labcatalog
from . import library as lib
from . import pnetimport
from . import savecfg
from . import scenarios
from . import servers as srv
from . import snapshots as snaps
from . import topology as topo
from .capture import PCAP_HEADER, CaptureError
from .config import settings
from .console import bridge_pty
from . import guac
from .drivers.iol import iourc_path
from .engine import GUEST_PORTS, EngineError, engine, host_info, load_state
from .models import Lab, Link, Node, Template


def _given(request: Request | WebSocket) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token")


def _user_of(given: str | None) -> str | None:
    """Who is calling: a logged-in user, "api" for the fixed NETSO_TOKEN, None if refused."""
    if settings.token and given and hmac.compare_digest(given, settings.token):
        return "api"
    if not settings.auth:
        return "admin" if not settings.token else None
    return users.session_user(given)


def _token_ok(given: str | None) -> bool:
    return _user_of(given) is not None


def require_auth(request: Request) -> None:
    if not _token_ok(_given(request)):
        raise HTTPException(401, "Accesso richiesto")


app = FastAPI(title="NetSO", version=__version__)
api = Depends(require_auth)


def _caller(request: Request | WebSocket) -> tuple[str | None, str]:
    user = _user_of(_given(request))
    return user, users.role_of(user)


@app.middleware("http")
async def access_guard(request: Request, call_next):
    """Roles and per-lab permissions for every API call (authentication itself stays in `require_auth`)."""
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        user, role = _caller(request)
        if user is not None:
            reason = acl.check(path, request.method, user, role)
            if reason:
                return JSONResponse({"detail": reason}, status_code=403)
    return await call_next(request)


def _ws_level(ws: WebSocket, lab: str) -> int:
    user, role = _caller(ws)
    return acl.level(lab, user, role) if user else 0


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, users.AuthError):
        return HTTPException(401, str(exc))
    if isinstance(exc, (topo.TopologyError, EngineError, CaptureError, interop.ImportError_, snaps.SnapshotError,
                        catalog.CatalogError, imgs.ImageError, dockerimg.DockerImageError, icons_mod.IconError,
                        agents_mod.AgentError, diagnostics.DiagError, automation.GenerateError, srv.ServerError, ValueError)):
        return HTTPException(400, str(exc))
    return HTTPException(500, str(exc))


def _lab_summary(name: str, folders: dict[str, str]) -> dict:
    state = engine.refresh(name)
    try:
        lab = topo.load_lab(name)
        desc, author, count, links = lab.description, lab.author, len(lab.nodes), len(lab.links)
    except Exception:
        desc, author, count, links = "(definizione non valida)", "", 0, 0
    try:
        modified = topo.lab_file(name).stat().st_mtime
    except OSError:
        modified = None
    running = sum(1 for n in state.nodes.values() if n.status == "running")
    return {"name": name, "description": desc, "author": author, "nodes": count, "links": links,
            "folder": folders.get(name, ""), "modified": modified, "running": running, "status": state.status}


# ------------------------------------------------------------------ system

@app.get("/api/info", dependencies=[api])
def info():
    return {"version": __version__, "host": host_info(), "auth": settings.auth or bool(settings.token)}


def _disk(p) -> dict | None:
    import shutil as _sh
    try:
        u = _sh.disk_usage(p)
    except OSError:
        return None
    return {"total_gb": round(u.total / 1e9, 1), "used_gb": round(u.used / 1e9, 1),
            "free_gb": round(u.free / 1e9, 1), "percent": round(u.used * 100 / u.total) if u.total else 0}


@app.get("/api/status", dependencies=[api])
def status():
    """Live snapshot of how the server is actually doing (for the «Stato server» dialog)."""
    from pathlib import Path as _P
    host = host_info()
    load = None
    try:
        load = [float(x) for x in _P("/proc/loadavg").read_text().split()[:3]]
    except (OSError, ValueError):
        pass
    mem = None
    try:
        kv = {}
        for line in _P("/proc/meminfo").read_text().splitlines():
            parts = line.split()
            if parts and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
                kv[parts[0].rstrip(":")] = int(parts[1])  # kB
        tot, avail = kv.get("MemTotal", 0), kv.get("MemAvailable", 0)
        if tot:
            mem = {"total_mb": tot // 1024, "available_mb": avail // 1024, "used_mb": (tot - avail) // 1024,
                   "percent": round((tot - avail) * 100 / tot)}
    except (OSError, ValueError):
        pass
    uptime = None
    try:
        uptime = int(float(_P("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError):
        pass
    labs_total = running_labs = running_nodes = 0
    for name in topo.list_labs():
        labs_total += 1
        try:
            st = load_state(name)
        except Exception:
            continue
        nr = sum(1 for n in st.nodes.values() if n.status == "running")
        if nr:
            running_labs += 1
            running_nodes += nr
    return {"version": __version__, "host": host, "load": load, "memory": mem, "uptime_seconds": uptime,
            "disks": {"home": _disk(settings.home), "images": _disk(settings.images)},
            "labs": {"total": labs_total, "running": running_labs, "nodes_running": running_nodes}}


# -------------------------------------------------------------------- auth

class Login(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def auth_login(body: Login):
    try:
        token = users.login(body.username.strip(), body.password)
    except Exception as exc:
        raise _err(exc)
    return {"token": token, "user": body.username.strip(), "role": users.role_of(body.username.strip()),
            "default_password": users.is_default_password(body.username.strip())}


@app.get("/api/auth/hint")
def auth_hint():
    """Public: what the login page may show (version, whether admin still has the default password)."""
    return {"version": __version__, "default_account": settings.auth and users.is_default_password(users.DEFAULT_USER)}


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = _user_of(_given(request))
    if user is None:
        raise HTTPException(401, "Accesso richiesto")
    return {"user": user, "auth": settings.auth, "role": users.role_of(user), "default_password": users.is_default_password(user)}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    given = _given(request)
    if given:
        users.logout(given)
    return {"ok": True}


class Password(BaseModel):
    old: str
    new: str


@app.post("/api/auth/password")
def auth_password(body: Password, request: Request):
    user = _user_of(_given(request))
    if user is None or user == "api":
        raise HTTPException(401, "Accesso richiesto")
    if settings.auth and not users.check_password(user, body.old):
        raise HTTPException(400, "La password attuale non è corretta")
    try:
        users.set_password(user, body.new)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.get("/api/templates", dependencies=[api])
def templates():
    out = []
    folders = imgs.folders_with_disks()
    docker = [x["ref"] for x in dockerimg._local(engine.r)]
    iol_bad = None
    for t in topo.load_templates().values():
        d = t.model_dump()
        d["iface_names"] = t.interface_names()
        d["origin"] = catalog.origin(t.name)
        d["title"] = t.title or catalog.builtin_title(t.name)
        d["choices"] = imgs.choices(t, folders, docker)
        d["dialect"] = scenarios.dialect_of(t)  # 'frr' | 'ios' | None → can it be a scenario router
        if t.kind == "docker":  # "supported" = its image is on the server (always in a dry run)
            d["ready"] = settings.dry_run or dockerimg.normalize(t.image or "") in docker or t.name == "docker"
        elif t.kind in ("qemu", "dynamips", "iol"):
            d["ready"] = bool(d["choices"])
            if t.kind == "iol" and not d["ready"]:
                d["problems"] = iol_bad if iol_bad is not None else (iol_bad := imgs.iol_problems())
        else:
            d["ready"] = True
        out.append(d)
    return out


def _labs_using(template: str) -> list[str]:
    out = []
    for lab in topo.list_labs():
        try:
            if any(n.template == template for n in topo.load_lab(lab).nodes):
                out.append(lab)
        except Exception:
            continue
    return out


@app.post("/api/templates", dependencies=[api], status_code=201)
def create_template(tpl: Template):
    if tpl.name in topo.load_templates():
        raise HTTPException(409, f"Esiste già un dispositivo '{tpl.name}'")
    catalog.save_template(tpl)
    return {"ok": True, "name": tpl.name}


@app.put("/api/templates/{name}", dependencies=[api])
def update_template(name: str, tpl: Template):
    if name not in topo.load_templates():
        raise HTTPException(404, f"Dispositivo '{name}' inesistente")
    if tpl.name != name and _labs_using(name):
        raise HTTPException(400, "Il dispositivo è usato nei lab: non si può rinominare")
    try:
        catalog.save_template(tpl, old_name=name)
    except Exception as exc:
        raise _err(exc)
    return {"ok": True, "name": tpl.name}


@app.delete("/api/templates/{name}", dependencies=[api])
def delete_template(name: str):
    try:
        return {"result": catalog.delete_template(name, _labs_using(name))}
    except Exception as exc:
        raise _err(exc)


class DefFiles(BaseModel):
    files: list[dict]  # [{filename, data (base64)}]
    save: bool = False
    pick: list[str] | None = None  # names to save (default: all)


@app.post("/api/templates/import", dependencies=[api])
def import_templates(body: DefFiles):
    import base64
    files = [(str(f.get("filename") or "file"), base64.b64decode(str(f.get("data") or ""))) for f in body.files]
    res = catalog.parse_definitions(files)
    saved = []
    if body.save:
        for t in res["templates"]:
            if body.pick is None or t.name in body.pick:
                catalog.save_template(t)
                saved.append(t.name)
    return {"templates": [t.model_dump() | {"iface_names": t.interface_names()} for t in res["templates"]],
            "images": res["images"], "warnings": res["warnings"], "saved": saved}


# ------------------------------------------------------------------ images

@app.get("/api/images", dependencies=[api])
def images():
    return imgs.describe()


@app.get("/api/images/iso", dependencies=[api])
def images_iso():
    return imgs.isos()


@app.put("/api/images/{name}/upload", dependencies=[api])
async def upload_image(name: str, filename: str, request: Request):
    """Raw streamed body (no multipart): the browser sends the File as is."""
    try:
        d = imgs.folder(name)
        imgs.check_name(filename, "Nome file")
    except Exception as exc:
        raise _err(exc)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"Cartella immagini {d.parent} non scrivibile: {exc.strerror}")
    part = d / f"{filename}.part"
    try:
        with open(part, "wb") as fh:
            async for chunk in request.stream():
                fh.write(chunk)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    target = d / filename
    part.replace(target)
    if name == imgs.ISO_DIR:
        return {"ok": True, "job": None}
    job = jobs.start("convert", f"{name}/{filename}", lambda j: "Pronta: " + (", ".join(imgs.finalize(engine.r, target, j)) or filename))
    return {"ok": True, "job": job.id}


class FetchBody(BaseModel):
    name: str
    url: str
    filename: str | None = None


@app.post("/api/images/fetch", dependencies=[api], status_code=202)
def fetch_image(body: FetchBody):
    try:
        imgs.folder(body.name)
    except Exception as exc:
        raise _err(exc)
    job = jobs.start("download", f"{body.name} ← {body.url[:80]}", lambda j: imgs.fetch(engine.r, j, body.name, body.url, body.filename))
    return {"job": job.id}


@app.post("/api/images/{name}/checksum", dependencies=[api], status_code=202)
def checksum_image(name: str):
    try:
        imgs.folder(name)
    except Exception as exc:
        raise _err(exc)
    return {"job": jobs.start("md5", f"MD5 {name}", lambda j: imgs.checksum(j, name)).id}


@app.delete("/api/images/{name}", dependencies=[api])
def delete_image(name: str, force: bool = False):
    try:
        imgs.delete(name, force)
    except Exception as exc:
        raise _err(exc)
    return {"ok": True}


# ------------------------------------------------------------------ custom icons (user-uploaded)
@app.get("/api/icons", dependencies=[api])
def list_icons():
    return icons_mod.listing()


@app.put("/api/icons/{name}/upload", dependencies=[api])
async def upload_icon(name: str, filename: str, request: Request):
    """Raw streamed body (no multipart), like image uploads; the browser sends the File as is."""
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > icons_mod.MAX_BYTES + 4096:
            raise HTTPException(400, "Icona troppo grande (max 2 MB)")
    try:
        return icons_mod.save(name, filename, data)
    except Exception as exc:
        raise _err(exc)


@app.get("/api/icons/{name}", dependencies=[api])
def get_icon(name: str):
    """Served to an <img>: authenticated through the ?token= query that apiUrl() adds to the URL."""
    try:
        p = icons_mod.path(name)
    except Exception as exc:
        raise _err(exc)
    return FileResponse(p, media_type=icons_mod.media_type(p))


@app.delete("/api/icons/{name}", dependencies=[api])
def delete_icon(name: str):
    try:
        icons_mod.delete(name)
    except Exception as exc:
        raise _err(exc)
    return {"ok": True}


# ------------------------------------------------------------------ AI agents (LLM providers)
@app.get("/api/agents", dependencies=[api])
def list_agents():
    return {"agents": agents_mod.listing(), "kinds": agents_mod.kinds()}


class AgentBody(BaseModel):
    id: str | None = None
    name: str
    kind: str
    base_url: str = ""
    model: str = ""
    command: str = ""          # for the "cli" kind: a local, subscription-logged-in command
    api_key: str | None = None
    enabled: bool = True


@app.post("/api/agents", dependencies=[api])
def save_agent(body: AgentBody):
    try:
        return agents_mod.save(body.model_dump())
    except Exception as exc:
        raise _err(exc)


@app.delete("/api/agents/{aid}", dependencies=[api])
def delete_agent(aid: str):
    try:
        agents_mod.delete(aid)
    except Exception as exc:
        raise _err(exc)
    return {"ok": True}


class AskBody(BaseModel):
    prompt: str
    lab: str | None = None
    node: str | None = None
    live: bool = False   # let the assistant run read-only show/ping commands on the nodes
    history: list[dict] = []  # prior turns [{role: "user"|"assistant", text: "..."}] for a real chat


def _lab_context(lab_name: str, node_name: str | None) -> str | None:
    """Full brief about the lab (topology, state, every node's config) so the assistant can troubleshoot."""
    try:
        lab = topo.load_lab(lab_name)
    except Exception:
        return None
    tpls = topo.load_templates()
    try:
        state = engine.refresh(lab_name)
    except Exception:
        state = None
    configs = topo.read_configs(lab_name, lab.config_set)
    out = [f"=== Contesto del lab NetSO «{lab.name}» ==="]
    if lab.description:
        out.append(f"Obiettivo/descrizione: {lab.description}")

    out.append("\nNodi (dispositivo · stato):")
    for n in lab.nodes:
        tpl = tpls.get(n.template)
        ns = state.nodes.get(n.name) if state else None
        st = (ns.status if ns else None) or "fermo"
        ifaces = tpl.interface_names(n.nics or tpl.nics) if tpl else []
        extra = f" · mgmt {ns.mgmt_ip}" if ns and ns.mgmt_ip else ""
        out.append(f"- {n.name}: {n.template}{f' ({tpl.kind})' if tpl else ''} · {st}{extra} · interfacce: {', '.join(ifaces) or 'n/d'}")

    out.append("\nCollegamenti:")
    for ln in lab.links:
        out.append("- " + " <-> ".join(ln.endpoints))

    if configs:
        out.append(f"\nConfigurazioni di avvio (set «{lab.config_set}»):")
        for name, txt in configs.items():
            if txt and txt.strip():
                out.append(f"\n--- {name} ---\n{txt.strip()}")
    else:
        out.append("\n(Nessuna config di avvio salvata: i nodi partono vuoti.)")

    if node_name and any(n.name == node_name for n in lab.nodes):
        out.append(f"\nL'utente è bloccato soprattutto sul nodo «{node_name}».")
    return "\n".join(out)


_ASSISTANT_SYSTEM = (
    "Sei l'assistente di NetSO e aiuti l'utente MENTRE svolge un lab di rete. "
    "Se c'è il contesto del lab, usalo: conosci topologia, stato dei nodi e configurazioni. "
    "Quando l'utente è bloccato o «qualcosa non funziona», ragiona come un istruttore: "
    "1) individua la causa probabile guardando config e topologia; "
    "2) indica i comandi di verifica (es. per FRRouting «show ...», ping, traceroute); "
    "3) proponi la correzione precisa di configurazione. "
    "Sii conciso e pratico; usa la sintassi del dispositivo giusto (FRRouting se non specificato)."
)
_LIVE_PROTOCOL = (
    "\n\nMODALITÀ LIVE ATTIVA: i nodi del lab sono ACCESI e sei TU a eseguire i comandi di sola lettura "
    "e a vederne l'output reale. Questo ANNULLA il punto 2 qui sopra: NON dire all'utente «esegui show ...»; "
    "i comandi li lanci tu. "
    "COME: quando ti serve un dato dai nodi, rispondi con SOLO righe nel formato «RUN <nodo>: <comando>» "
    "(una per riga, la parola RUN a inizio riga, poi il nome ESATTO del nodo preso dal contesto, due punti, "
    "il comando) — per esempio una riga tipo «RUN <nodo>: show ip bgp summary» e una «RUN <nodo>: show running-config». "
    "In quel turno scrivi SOLO quelle righe, niente prosa e niente blocchi di codice. Io eseguo i comandi sui nodi "
    "reali e ti rimando l'output; poi, con i dati veri davanti, dai la verifica/diagnosi in prosa SENZA righe RUN. "
    "Sola lettura (show/ping/traceroute), sintassi FRR o Cisco IOS secondo il nodo. "
    "Per una verifica tipica parti da «show running-config» e «show ip bgp summary» sui nodi coinvolti."
)
_LIVE_ANSWER = (
    "\n\nQUANDO RISPONDI IN PROSA (dopo aver ricevuto gli output): rispondi SOLO a ciò che l'utente ha chiesto, "
    "in modo conciso. Mostra i dati raccolti e, se serve, un commento breve. "
    "Se l'utente ha solo chiesto di VEDERE un dato (es. «mostrami le route»), mostralo e FERMATI. "
    "VIETATO: suggerire altri comandi da far lanciare all'utente, scrivere blocchi «Esempio di comandi» o finte sessioni "
    "tipo «R01# ping ...», aggiungere piani di verifica, «passi successivi», «conclusioni» o diagnosi che l'utente non ha chiesto. "
    "Fai una diagnosi o proponi correzioni SOLO se l'utente ha segnalato un problema o te lo ha chiesto esplicitamente. "
    "Niente riempitivi: se hai la risposta, dalla e basta."
)
# tolerate leading markdown/bullets/backticks the model may add before RUN
_RUN_RE = re.compile(r"(?m)^[ \t>*`\-]*RUN\s+([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*:\s*`?(.+?)`?\s*$")
_MAX_ROUNDS = 3
_MAX_CMDS = 6
# in live mode, if the model answers WITHOUT running and instead invents device output or tells the
# user to run a command, force it to execute for real once (never trust fabricated output)
_FAKE_STRONG = re.compile(r"(?mi)(esempio di (output|comand)|BGP neighbor is|Gateway of last resort|"
                          r"neighbor is .*remote AS|Established, up for|is directly connected)")
_FAKE_SUGGEST = re.compile(r"(?mi)^\s*`?[A-Za-z0-9_.\-]+\s*[#>]\s*(show|ping|traceroute|sh\b)")


def _looks_fabricated(text: str) -> bool:
    return bool(_FAKE_STRONG.search(text) or (_FAKE_SUGGEST.search(text) and "```" in text))


_FORCE_RUN = (
    "\n\n[Sistema] Non hai eseguito nulla e NON devi inventare l'output: i nodi sono reali e accesi, "
    "non puoi sapere cosa rispondono finché non li interroghi. Rispondi ORA con SOLO righe «RUN <nodo>: <comando>» "
    "(una per riga, il nome ESATTO del nodo preso dal contesto) per ottenere i dati veri; poi ti rimando l'output. "
    "Niente prosa, niente blocchi «esempio di output», niente sessioni finte."
)


@app.post("/api/agents/{aid}/ask", dependencies=[api])
def ask_agent(aid: str, body: AskBody):
    ctx = _lab_context(body.lab, body.node) if body.lab else None
    convo = "\n".join(f"{'Utente' if m.get('role') == 'user' else 'Assistente'}: {m.get('text', '')}"
                      for m in (body.history or []) if m.get("text"))
    parts = ([ctx] if ctx else []) + ([f"--- Conversazione finora ---\n{convo}" if convo else ""])
    parts = [p for p in parts if p] + [f"--- Nuovo messaggio dell'utente ---\n{body.prompt}"]
    base = "\n\n".join(parts)
    live = body.live and bool(ctx)
    try:
        if not live:
            return {"text": agents_mod.ask(aid, base, system=_ASSISTANT_SYSTEM), "transcript": []}
        # agentic loop: the model asks for read-only commands, we run them and feed the output back
        prompt, transcript, forced = base, [], False
        for _ in range(_MAX_ROUNDS):
            reply = agents_mod.ask(aid, prompt, system=_ASSISTANT_SYSTEM + _LIVE_PROTOCOL + _LIVE_ANSWER)
            runs = _RUN_RE.findall(reply)
            if not runs:
                # answered without running: if it invented device output, force a real execution once
                if not transcript and not forced and _looks_fabricated(reply):
                    forced, prompt = True, base + _FORCE_RUN
                    continue
                return {"text": reply, "transcript": transcript}
            obs = []
            for node, cmd in runs[:_MAX_CMDS]:
                try:
                    out = diagnostics.run(body.lab, node, cmd)
                except Exception as exc:  # report the failure to the model, don't abort
                    out = f"ERRORE: {exc}"
                transcript.append({"node": node, "cmd": cmd, "output": out})
                obs.append(f"$ {node}: {cmd}\n{out}")
            prompt = (f"{prompt}\n\n[Le tue direttive]\n{reply}\n\n[Output dei comandi]\n" + "\n\n".join(obs)
                      + "\n\nProsegui: altre righe RUN se ti serve altro, altrimenti la diagnosi finale.")
        final = agents_mod.ask(aid, prompt + "\n\nOra rispondi all'utente in prosa, senza altre righe RUN.",
                               system=_ASSISTANT_SYSTEM + _LIVE_ANSWER)
        return {"text": final, "transcript": transcript}
    except Exception as exc:
        raise _err(exc)


@app.get("/api/images/refplat/{iso}", dependencies=[api])
def refplat_scan(iso: str):
    try:
        found = imgs.scan_refplat(iso)
    except Exception as exc:
        raise _err(exc)
    have = {i["name"] for i in imgs.describe()}
    return {"nodes": [{"id": n.get("id"), "label": (n.get("ui") or {}).get("label")} for n in found["nodes"]],
            "images": [{"id": i.get("id"), "label": i.get("label"), "node_definition_id": i.get("node_definition_id"),
                        "present": i.get("id") in have} for i in found["images"]]}


class RefplatBody(BaseModel):
    images: list[str]


@app.post("/api/images/refplat/{iso}", dependencies=[api], status_code=202)
def refplat_import(iso: str, body: RefplatBody):
    return {"job": jobs.start("refplat", f"CML refplat {iso}", lambda j: imgs.import_refplat(engine.r, j, iso, body.images)).id}


# ------------------------------------------------------------------ IOL license

@app.get("/api/images/iol-license", dependencies=[api])
def iol_license():
    f = iourc_path()
    return {"present": f.is_file(), "hostname": socket.gethostname(), "text": f.read_text(errors="replace") if f.is_file() else ""}


class IourcBody(BaseModel):
    text: str = ""


@app.put("/api/images/iol-license", dependencies=[api])
def set_iol_license(body: IourcBody):
    """The user's own iourc ([license] + "<host> = <key>;"); empty text removes it. NetSO never generates one."""
    f = iourc_path()
    text = body.text.strip()
    if not text:
        f.unlink(missing_ok=True)
        return iol_license()
    if "[license]" not in text or len(text) > 4096:
        raise HTTPException(400, "Non sembra un file iourc: deve contenere [license] e una riga '<nome host> = <chiave>;'")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text + "\n")
    f.chmod(0o600)
    return iol_license()


# ------------------------------------------------------------------ docker images

@app.get("/api/images/docker", dependencies=[api])
def docker_images():
    return dockerimg.describe(engine.r)


class DockerRef(BaseModel):
    ref: str
    force: bool = False


@app.post("/api/images/docker/pull", dependencies=[api], status_code=202)
def docker_pull(body: DockerRef):
    try:
        ref = dockerimg.check_ref(body.ref)
    except Exception as exc:
        raise _err(exc)
    return {"job": jobs.start("docker", f"docker pull {ref}", lambda j: dockerimg.pull(engine.r, j, ref)).id}


@app.post("/api/images/docker/remove", dependencies=[api])
def docker_remove(body: DockerRef):
    try:
        dockerimg.remove(engine.r, body.ref, body.force)
    except Exception as exc:
        raise _err(exc)
    return {"ok": True}


@app.put("/api/images/docker-load", dependencies=[api], status_code=202)
async def docker_upload(filename: str, request: Request, tag: str | None = None):
    """Raw streamed tar (docker save, or a root filesystem like cEOS-lab.tar.xz with `tag`), then docker load/import.
    Not /docker/upload: PUT /api/images/{name}/upload would catch it."""
    try:
        imgs.check_name(filename, "Nome file")
        if tag:
            dockerimg.check_ref(tag)
        d = dockerimg.upload_dir()
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"Cartella {dockerimg.upload_dir()} non scrivibile: {exc.strerror}")
    except Exception as exc:
        raise _err(exc)
    target = d / filename
    part = d / f"{filename}.part"
    try:
        with open(part, "wb") as fh:
            async for chunk in request.stream():
                fh.write(chunk)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    part.replace(target)
    return {"job": jobs.start("docker", f"docker load {filename}", lambda j: dockerimg.load(engine.r, j, target, tag)).id}


class PnetBody(BaseModel):
    source: str
    pick: list[str] | None = None
    move: bool = False


@app.post("/api/images/pnet/scan", dependencies=[api])
def pnet_scan(body: PnetBody):
    """Images found on an EVE-NG / PNETLab server (local folder or user@host:/path over SSH)."""
    try:
        return pnetimport.scan(body.source, engine.r)
    except Exception as exc:
        raise _err(exc)


@app.post("/api/images/pnet/import", dependencies=[api], status_code=202)
def pnet_import(body: PnetBody):
    job = jobs.start("pnet", f"PNETLab {body.source[:80]}", lambda j: pnetimport.run_import(engine.r, j, body.source, body.pick, body.move))
    return {"job": job.id}


@app.get("/api/jobs", dependencies=[api])
def list_jobs():
    return jobs.list_jobs()


@app.delete("/api/jobs/{job_id}", dependencies=[api])
def dismiss_job(job_id: str):
    jobs.dismiss(job_id)
    return {"ok": True}


# -------------------------------------------------------------------- labs

@app.get("/api/labs", dependencies=[api])
def labs(request: Request):
    user, role = _caller(request)
    folders = lib.folder_map()
    out = []
    for n in topo.list_labs():
        lv = acl.level(n, user, role)
        if lv:
            m = acl.meta(n)
            out.append({**_lab_summary(n, folders), "owner": m["owner"], "shared": m["shared"], "access": acl.level_name(lv)})
    return out


class ShareBody(BaseModel):
    shared: dict[str, str]  # user -> view | edit


@app.get("/api/labs/{name}/share", dependencies=[api])
def get_share(name: str):
    return acl.meta(name)


@app.put("/api/labs/{name}/share", dependencies=[api])
def put_share(name: str, body: ShareBody):
    if name not in topo.list_labs():
        raise HTTPException(404, f"Lab '{name}' inesistente")
    known = users.users()
    bad = [u for u in body.shared if u not in known]
    if bad:
        raise HTTPException(400, f"Utenti inesistenti: {', '.join(bad)}")
    return {"shared": acl.set_shared(name, body.shared), "owner": acl.meta(name)["owner"]}


# ------------------------------------------------------------------- users

@app.get("/api/users", dependencies=[api])
def list_users():
    return [{"name": n, "role": u.get("role", "admin")} for n, u in sorted(users.users().items())]


class NewUser(BaseModel):
    username: str
    password: str | None = None
    role: str | None = None


@app.post("/api/users", dependencies=[api], status_code=201)
def create_user(body: NewUser):
    if body.username in users.users():
        raise HTTPException(409, f"Esiste già un utente '{body.username}'")
    if not body.password:
        raise HTTPException(400, "Password mancante")
    if (body.role or "user") not in acl.ROLES:
        raise HTTPException(400, "Ruolo sconosciuto (admin, user, viewer)")
    try:
        users.set_password(body.username, body.password, body.role or "user")
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.put("/api/users/{username}", dependencies=[api])
def update_user(username: str, body: NewUser):
    """New password and/or new role."""
    if username not in users.users():
        raise HTTPException(404, f"Utente '{username}' inesistente")
    try:
        if body.password:
            users.set_password(username, body.password)
        if body.role:
            users.set_role(username, body.role)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.delete("/api/users/{username}", dependencies=[api])
def delete_user(username: str, request: Request):
    if username == _user_of(_given(request)):
        raise HTTPException(400, "Non puoi eliminare l'utente con cui sei collegato")
    try:
        users.delete_user(username)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    acl.forget_user(username)
    return {"ok": True}


# ----------------------------------------------------------------- folders

@app.get("/api/folders", dependencies=[api])
def folders():
    return lib.folders()


class FolderBody(BaseModel):
    path: str
    new: str | None = None


@app.post("/api/folders", dependencies=[api], status_code=201)
def create_folder(body: FolderBody):
    try:
        return {"path": lib.create_folder(body.path)}
    except Exception as exc:
        raise _err(exc)


@app.put("/api/folders", dependencies=[api])
def rename_folder(body: FolderBody):
    try:
        return {"path": lib.rename_folder(body.path, body.new or "")}
    except Exception as exc:
        raise _err(exc)


@app.delete("/api/folders", dependencies=[api])
def delete_folder(path: str):
    try:
        lib.delete_folder(path, topo.list_labs())
    except Exception as exc:
        raise _err(exc)
    return {"ok": True}


class NewLab(BaseModel):
    name: str | None = None
    description: str = ""
    yaml: str | None = None
    folder: str = ""
    author: str = ""


@app.post("/api/labs", dependencies=[api], status_code=201)
def create_lab(body: NewLab, request: Request):
    try:
        lab = topo.parse_lab_yaml(body.yaml) if body.yaml else Lab(name=body.name or "", description=body.description)
    except Exception as exc:
        raise _err(exc)
    user = _user_of(_given(request))
    if not lab.author:
        lab.author = body.author or (user if user and user != "api" else "")
    acl.set_owner(lab.name, user if user and user != "api" else "admin")
    if lab.name in topo.list_labs():
        raise HTTPException(409, f"Esiste già un lab '{lab.name}'")
    try:
        lib.set_lab_folder(lab.name, body.folder)
    except Exception as exc:
        raise _err(exc)
    topo.save_lab(lab)
    return lab


class GenLab(BaseModel):
    name: str
    folder: str = ""
    author: str = ""
    description: str = ""
    pattern: str = "clos"
    params: dict = {}
    scenario: str | None = None   # topic (ospf, bgp-ebgp, …): a ready lab with configs
    count: int | None = None      # scenario: how many nodes (routers/leaves) the user wants
    router: str | None = None     # scenario: which installed router template to build with (frr, cisco-iol, …)
    image: str | None = None      # scenario: which image/version of that router to use (from its «choices»)


@app.get("/api/scenarios", dependencies=[api])
def list_scenarios():
    """Ready lab topics by protocol, for the «Genera lab» dialog."""
    out = []
    for t in scenarios.TOPICS:
        d = {k: t[k] for k in ("id", "label", "cat", "desc", "level")}
        d["ios"] = t["id"] in scenarios.IOS_TOPICS   # can be built on a Cisco IOS router too
        d["host"] = t["id"] in scenarios.HOST_TOPICS  # router-agnostic (switch + hosts)
        out.append(d)
    # custom catalogue labs («I miei lab»): fixed ready labs you cloned in
    for m in labcatalog.list_catalog():
        out.append({"id": f"cat:{m['id']}", "label": m.get("label", m["id"]),
                    "cat": m.get("cat", "I miei lab"), "desc": m.get("desc", ""),
                    "level": m.get("level", 3), "custom": True})
    return out


@app.post("/api/labs/generate", dependencies=[api], status_code=201)
def generate_lab(body: GenLab, request: Request):
    """Create a brand-new lab: either a bare topology (clos/ring/…) or a ready protocol scenario."""
    import time
    if body.name in topo.list_labs():
        raise HTTPException(409, f"Esiste già un lab '{body.name}'")
    user = _user_of(_given(request))
    configs: dict[str, str] = {}
    solution: dict[str, str] = {}
    try:
        if body.scenario and body.scenario.startswith("cat:"):
            # a lab from the custom catalogue («I miei lab»): clone it under the new name
            clab, configs, instr = labcatalog.load_catalog(body.scenario[4:])
            lab = Lab(name=body.name, description=body.description or clab.description,
                      instructions=instr, generated=True)
            lab.nodes, lab.links, lab.annotations = clab.nodes, clab.links, clab.annotations
            solution = {}
        elif body.scenario:
            spec = scenarios.build(body.scenario, topo.load_templates(),
                                   seed=int(time.time() * 1000) & 0xFFFFFFFF, count=body.count,
                                   router=body.router)
            lab = Lab(name=body.name, description=body.description or spec["description"],
                      instructions=spec.get("instructions", ""), generated=True)
            configs = spec["configs"]           # base (IP addressing): the lab boots with this
            solution = spec.get("solution", {})  # full working config, kept in the «soluzione» set
            lab.nodes = [Node(**n) for n in spec["nodes"]]
            lab.links = [Link(**ln) for ln in spec["links"]]
            if body.image and body.router:       # chosen image/version on the generated router nodes
                for n in lab.nodes:
                    if n.template == body.router:
                        n.image = body.image
        else:
            lab = Lab(name=body.name, description=body.description)
            spec = automation.generate(lab, topo.load_templates(), body.pattern, body.params)
            lab.nodes = [Node(**n) for n in spec["nodes"]]
            lab.links = [Link(**ln) for ln in spec["links"]]
    except Exception as exc:
        raise _err(exc)
    lab.author = body.author or (user if user and user != "api" else "")
    acl.set_owner(lab.name, user if user and user != "api" else "admin")
    try:
        lib.set_lab_folder(lab.name, body.folder)
    except Exception as exc:
        raise _err(exc)
    topo.save_lab(lab)
    for node, text in configs.items():
        topo.write_config(lab.name, lab.config_set, node, text)
    for node, text in solution.items():
        topo.write_config(lab.name, "soluzione", node, text)
    return lab


class LabMeta(BaseModel):
    description: str = ""
    author: str = ""


@app.put("/api/labs/{name}/meta", dependencies=[api])
def lab_meta(name: str, body: LabMeta):
    try:
        lab = topo.load_lab(name)
    except Exception as exc:
        raise _err(exc)
    lab.description, lab.author = body.description, body.author
    topo.save_lab(lab)
    return {"ok": True}


# ----------------------------------------------------- import / export

MAX_IMPORT = 40 * 1024 * 1024


class ImportBody(BaseModel):
    filename: str
    data: str                      # file content, base64
    name: str | None = None        # lab name (default: from the file)
    folder: str = ""
    overrides: dict[str, str] = {}  # node -> NetSO template


def _parse_import(body: ImportBody) -> interop.Imported:
    import base64
    import binascii
    try:
        raw = base64.b64decode(body.data, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, f"Contenuto del file non valido: {exc}")
    if len(raw) > MAX_IMPORT:
        raise HTTPException(400, "File troppo grande (max 40 MB): esporta il progetto senza immagini")
    name = body.name.strip() if body.name else None
    if name and not interop.valid_name(name):
        raise HTTPException(400, "Nome lab non valido")
    try:
        return interop.import_file(body.filename, raw, topo.load_templates(), body.overrides, name)
    except Exception as exc:
        raise _err(exc)


def _import_summary(imp: interop.Imported) -> dict:
    return {"format": imp.fmt, "format_label": interop.FORMATS[imp.fmt], "name": imp.lab.name,
            "exists": imp.lab.name in topo.list_labs(), "nodes": imp.mapping, "links": len(imp.lab.links),
            "configs": sorted(imp.configs), "warnings": imp.warnings}


@app.post("/api/import/preview", dependencies=[api])
def import_preview(body: ImportBody):
    return _import_summary(_parse_import(body))


@app.post("/api/import", dependencies=[api], status_code=201)
def import_lab(body: ImportBody, request: Request):
    imp = _parse_import(body)
    if imp.lab.name in topo.list_labs():
        raise HTTPException(409, f"Esiste già un lab '{imp.lab.name}': scegli un altro nome")
    try:
        lib.set_lab_folder(imp.lab.name, body.folder)
    except Exception as exc:
        raise _err(exc)
    user = _user_of(_given(request))
    if not imp.lab.author:
        imp.lab.author = user if user and user != "api" else ""
    acl.set_owner(imp.lab.name, user if user and user != "api" else "admin")
    topo.save_lab(imp.lab)
    for node, text in imp.configs.items():
        topo.write_config(imp.lab.name, topo.DEFAULT_SET, node, text)
    return _import_summary(imp)


@app.get("/api/labs/{name}/export", dependencies=[api], response_class=PlainTextResponse)
def export_lab(name: str, format: str = "netso"):
    try:
        lab = topo.load_lab(name)
    except Exception as exc:
        raise _err(exc)
    if format == "netso":
        text, fname = topo.dump_lab_yaml(lab), f"{name}.yml"
    elif format in ("clab", "cml"):
        configs = topo.read_configs(name, lab.config_set)
        fn = interop.export_clab if format == "clab" else interop.export_cml
        text, warns = fn(lab, topo.load_templates(), configs)
        if warns:
            text = "".join(f"# NOTA: {w}\n" for w in warns) + text
        fname = f"{name}.clab.yml" if format == "clab" else f"{name}.cml.yaml"
    else:
        raise HTTPException(400, "Formato di esportazione sconosciuto (netso, clab, cml)")
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---------------------------------------------------------- other servers

@app.get("/api/servers", dependencies=[api])
def list_servers():
    return srv.list_servers()


class ServerBody(BaseModel):
    name: str
    url: str


@app.post("/api/servers", dependencies=[api], status_code=201)
def add_server(body: ServerBody):
    try:
        return srv.add(body.name, body.url)
    except Exception as exc:
        raise _err(exc)


@app.delete("/api/servers/{name}", dependencies=[api])
def remove_server(name: str):
    srv.remove(name)
    return {"ok": True}


@app.get("/api/servers/{name}/ping", dependencies=[api])
def ping_server(name: str):
    try:
        return srv.ping(name)
    except Exception as exc:
        raise _err(exc)


# ---------------------------------------------------------- automation

class SaveCfgBody(BaseModel):
    nodes: list[str] | None = None   # None = every running VM/container
    set: str | None = None           # destination config set (default: the active one)
    new_set: bool = False            # create `set` as a copy of the active set first


@app.post("/api/labs/{name}/save-configs", dependencies=[api], status_code=202)
def save_configs(name: str, body: SaveCfgBody, request: Request):
    try:
        lab = topo.load_lab(name)
        dest = body.set or lab.config_set
        if body.new_set:
            topo.create_config_set(name, dest, lab.config_set)
        elif dest not in topo.config_sets(name):
            raise HTTPException(400, f"Config set '{dest}' inesistente")
    except HTTPException:
        raise
    except Exception as exc:
        raise _err(exc)
    host = "127.0.0.1"

    def work(job):
        res = savecfg.save_configs(engine.r, job, name, body.nodes, dest, engine.refresh(name), host)
        ok = [n for n, v in res.items() if v == "ok"]
        bad = [f"{n}: {v}" for n, v in res.items() if v != "ok"]
        job.result = {"set": dest, "results": res}
        if not ok and bad:
            raise RuntimeError("; ".join(bad))
        return f"Salvate in '{dest}': {', '.join(ok) or 'nessuna'}" + (f" · non salvate: {'; '.join(bad)}" if bad else "")
    job = jobs.start("savecfg", f"Config di {name}", work)
    return {"job": job.id, "set": dest}


@app.get("/api/jobs/{job_id}", dependencies=[api])
def get_job(job_id: str):
    j = next((x for x in jobs.list_jobs() if x["id"] == job_id), None)
    if not j:
        raise HTTPException(404, "Lavoro inesistente")
    return j


@app.get("/api/labs/{name}/inventory", dependencies=[api])
def inventory(name: str, request: Request, format: str = "json"):
    try:
        lab = topo.load_lab(name)
    except Exception as exc:
        raise _err(exc)
    host = request.url.hostname or "localhost"
    rows = automation.node_rows(lab, engine.refresh(name), topo.load_templates(), host)
    if format == "json":
        return rows
    fn = {"ansible": (automation.ansible_inventory, f"{name}-inventory.yml"), "hosts": (automation.hosts_file, f"{name}.hosts"),
          "ssh": (automation.ssh_config, f"{name}.ssh_config")}.get(format)
    if not fn:
        raise HTTPException(400, "Formato sconosciuto (json, ansible, hosts, ssh)")
    return PlainTextResponse(fn[0](lab, rows), headers={"Content-Disposition": f'attachment; filename="{fn[1]}"'})


class GenBody(BaseModel):
    pattern: str
    params: dict = {}


@app.post("/api/labs/{name}/generate", dependencies=[api])
def generate(name: str, body: GenBody):
    try:
        return automation.generate(topo.load_lab(name), topo.load_templates(), body.pattern, body.params)
    except Exception as exc:
        raise _err(exc)


# ---------------------------------------------------------- snapshots

def _running_vms(name: str) -> list[str]:
    return [n for n, s in engine.refresh(name).nodes.items() if s.kind == "qemu" and s.status == "running"]


class SnapBody(BaseModel):
    name: str
    description: str = ""


@app.get("/api/labs/{name}/snapshots", dependencies=[api])
def list_snaps(name: str):
    return snaps.list_snapshots(name)


@app.post("/api/labs/{name}/snapshots", dependencies=[api], status_code=201)
def create_snap(name: str, body: SnapBody):
    try:
        topo.load_lab(name)
        return snaps.create(engine.r, name, body.name.strip(), body.description.strip(), _running_vms(name))
    except Exception as exc:
        raise _err(exc)


@app.post("/api/labs/{name}/snapshots/{snap}/restore", dependencies=[api])
def restore_snap(name: str, snap: str):
    try:
        return snaps.restore(engine.r, name, snap, _running_vms(name))
    except Exception as exc:
        raise _err(exc)


@app.delete("/api/labs/{name}/snapshots/{snap}", dependencies=[api])
def delete_snap(name: str, snap: str):
    try:
        snaps.delete(engine.r, name, snap)
    except Exception as exc:
        raise _err(exc)
    return {"ok": True}


class MoveLab(BaseModel):
    folder: str = ""


@app.post("/api/labs/{name}/move", dependencies=[api])
def move_lab(name: str, body: MoveLab):
    if name not in topo.list_labs():
        raise HTTPException(404, f"Lab '{name}' inesistente")
    try:
        return {"folder": lib.set_lab_folder(name, body.folder)}
    except Exception as exc:
        raise _err(exc)


@app.get("/api/labs/{name}", dependencies=[api])
def get_lab(name: str, request: Request):
    try:
        lab = topo.load_lab(name)
    except Exception as exc:
        raise _err(exc)
    warnings = []
    try:
        topo.validate_lab(lab)
    except Exception as exc:
        warnings.append(str(exc))
    user, role = _caller(request)
    return {"lab": lab, "state": engine.refresh(name), "warnings": warnings, "access": acl.level_name(acl.level(name, user, role))}


@app.put("/api/labs/{name}", dependencies=[api])
def put_lab(name: str, lab: Lab):
    if lab.name != name:
        raise HTTPException(400, "Il nome del lab non si può cambiare qui")
    topo.save_lab(lab)
    try:
        topo.validate_lab(lab)
        return {"ok": True, "warnings": []}
    except Exception as exc:
        return {"ok": True, "warnings": [str(exc)]}


class DisconnectBody(BaseModel):
    index: int


@app.post("/api/labs/{name}/links/connect", dependencies=[api])
def connect_link(name: str, link: Link):
    """Add a cable and, if the lab is running, wire it live (hot-plug)."""
    try:
        return engine.connect_link(name, link)
    except Exception as exc:
        raise _err(exc)


@app.post("/api/labs/{name}/links/disconnect", dependencies=[api])
def disconnect_link(name: str, body: DisconnectBody):
    """Remove the cable at `index`, tearing down its live wiring if the lab is running."""
    try:
        return engine.disconnect_link(name, body.index)
    except Exception as exc:
        raise _err(exc)


@app.get("/api/labs/{name}/yaml", dependencies=[api], response_class=PlainTextResponse)
def get_yaml(name: str, download: bool = False):
    try:
        text = topo.dump_lab_yaml(topo.load_lab(name))
    except Exception as exc:
        raise _err(exc)
    headers = {"Content-Disposition": f'attachment; filename="{name}.yml"'} if download else None
    return PlainTextResponse(text, headers=headers)


class YamlBody(BaseModel):
    yaml: str


@app.put("/api/labs/{name}/yaml", dependencies=[api])
def put_yaml(name: str, body: YamlBody):
    try:
        lab = topo.parse_lab_yaml(body.yaml)
        if lab.name != name:
            raise topo.TopologyError("Il campo 'name' deve restare uguale al nome del lab")
        topo.validate_lab(lab)
    except Exception as exc:
        raise _err(exc)
    topo.save_lab(lab)
    return {"ok": True}


@app.delete("/api/labs/{name}", dependencies=[api])
def delete_lab(name: str):
    state = engine.refresh(name)
    if any(n.status == "running" for n in state.nodes.values()):
        raise HTTPException(400, "Fai destroy del lab prima di eliminarlo")
    topo.delete_lab(name)
    lib.forget_lab(name)
    acl.forget(name)
    return {"ok": True}


@app.post("/api/labs/{name}/deploy", dependencies=[api])
def deploy(name: str):
    try:
        return engine.deploy(name)
    except Exception as exc:
        raise _err(exc)


@app.post("/api/labs/{name}/stop", dependencies=[api])
def stop_lab(name: str):
    """Stop all running nodes (keeps the networks); the lab becomes «fermo»."""
    try:
        return engine.stop_lab(name)
    except Exception as exc:
        raise _err(exc)


@app.post("/api/labs/{name}/destroy", dependencies=[api])
def destroy(name: str):
    try:
        return engine.destroy(name)
    except Exception as exc:
        raise _err(exc)


@app.post("/api/labs/{name}/conditioning", dependencies=[api])
def reapply_conditioning(name: str):
    try:
        return engine.reapply_conditioning(name)
    except Exception as exc:
        raise _err(exc)


class OpenBody(BaseModel):
    user: str = ""
    password: str = ""
    width: int = 1280
    height: int = 800


@app.post("/api/labs/{name}/nodes/{node}/open", dependencies=[api])
async def open_console(name: str, node: str, body: OpenBody):
    """Graphical/network consoles: RDP ticket for the guacd tunnel, forwarded port for HTTP/HTTPS/WinBox, SPICE port."""
    try:
        ns = load_state(name).nodes.get(node)
        if ns is None or ns.status != "running":
            raise EngineError("Il nodo non è in esecuzione")
        kind = ns.console or "telnet"
        if kind == "spice":
            return {"type": kind, "port": ns.spice_port}
        if kind not in GUEST_PORTS or kind == "ssh":
            raise EngineError(f"La consolle {kind} si apre nel terminale")
        ip = engine.guest_ip(name, node)
        if kind in ("rdp", "rdp-tls"):
            await asyncio.to_thread(guac.ensure_guacd, engine.r)
            params = guac.rdp_params(kind, ip, body.user.strip(), body.password)
            params.update(width=str(body.width), height=str(body.height))
            return {"type": kind, "ticket": guac.new_ticket(name, node, params), "ip": ip}
        return {"type": kind, "port": await guac.forward(ip, GUEST_PORTS[kind]), "ip": ip}
    except Exception as exc:
        raise _err(exc)


@app.post("/api/labs/{name}/nodes/{node}/{action}", dependencies=[api])
def node_action(name: str, node: str, action: str):
    try:
        if action == "start":
            return engine.start_node(name, node)
        if action == "stop":
            return engine.stop_node(name, node)
        if action == "wipe":
            engine.wipe_node(name, node)
            return {"ok": True}
    except Exception as exc:
        raise _err(exc)
    raise HTTPException(404, f"Azione sconosciuta: {action}")


# --------------------------------------------------------- startup configs

def _set_active(name: str, cfg_set: str) -> None:
    lab = topo.load_lab(name)
    lab.config_set = cfg_set
    topo.save_lab(lab)


@app.get("/api/labs/{name}/configs", dependencies=[api])
def get_configs(name: str):
    try:
        lab = topo.load_lab(name)
        return {"active": lab.config_set,
                "sets": {s: topo.read_configs(name, s) for s in topo.config_sets(name)}}
    except Exception as exc:
        raise _err(exc)


class ConfigBody(BaseModel):
    config: str = ""


@app.put("/api/labs/{name}/configs/{cfg_set}/nodes/{node}", dependencies=[api])
def put_config(name: str, cfg_set: str, node: str, body: ConfigBody):
    try:
        topo.load_lab(name)
        topo.write_config(name, cfg_set, node, body.config)
        return {"ok": True}
    except Exception as exc:
        raise _err(exc)


class NewConfigSet(BaseModel):
    name: str
    copy_from: str | None = None


@app.post("/api/labs/{name}/configs", dependencies=[api], status_code=201)
def create_config_set(name: str, body: NewConfigSet):
    try:
        topo.load_lab(name)
        topo.create_config_set(name, body.name, body.copy_from)
        return {"ok": True}
    except Exception as exc:
        raise _err(exc)


class RenameBody(BaseModel):
    new: str


@app.post("/api/labs/{name}/configs/{cfg_set}/rename", dependencies=[api])
def rename_config_set(name: str, cfg_set: str, body: RenameBody):
    try:
        topo.rename_config_set(name, cfg_set, body.new)
        if topo.load_lab(name).config_set == cfg_set:
            _set_active(name, body.new)
        return {"ok": True}
    except Exception as exc:
        raise _err(exc)


@app.delete("/api/labs/{name}/configs/{cfg_set}", dependencies=[api])
def delete_config_set(name: str, cfg_set: str):
    try:
        topo.delete_config_set(name, cfg_set)
        if topo.load_lab(name).config_set == cfg_set:
            _set_active(name, topo.DEFAULT_SET)
        return {"ok": True}
    except Exception as exc:
        raise _err(exc)


@app.post("/api/labs/{name}/configs/{cfg_set}/activate", dependencies=[api])
def activate_config_set(name: str, cfg_set: str):
    try:
        if cfg_set not in topo.config_sets(name):
            raise FileNotFoundError(f"Config set '{cfg_set}' inesistente")
        _set_active(name, cfg_set)
        return {"ok": True}
    except Exception as exc:
        raise _err(exc)


class EnableBody(BaseModel):
    nodes: dict[str, bool]


@app.post("/api/labs/{name}/configs/enable", dependencies=[api])
def enable_startup_config(name: str, body: EnableBody):
    """Per-node startup-config ON/OFF, written straight to lab.yml."""
    try:
        lab = topo.load_lab(name)
        for n in lab.nodes:
            if n.name in body.nodes:
                n.startup_config = body.nodes[n.name]
        topo.save_lab(lab)
        return {"ok": True}
    except Exception as exc:
        raise _err(exc)


@app.post("/api/labs/{name}/configs/rename-node", dependencies=[api])
def rename_node_configs(name: str, body: dict[str, str]):
    try:
        topo.rename_node_configs(name, body["old"], body["new"])
        return {"ok": True}
    except Exception as exc:
        raise _err(exc)


@app.get("/api/labs/{name}/configs/{cfg_set}/export", dependencies=[api], response_class=PlainTextResponse)
def export_config_set(name: str, cfg_set: str):
    try:
        data = {"config_set": cfg_set, "configs": topo.read_configs(name, cfg_set)}
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_style=None, width=1000)
    except Exception as exc:
        raise _err(exc)


class ImportBody(BaseModel):
    yaml: str
    name: str | None = None


@app.post("/api/labs/{name}/configs/import", dependencies=[api], status_code=201)
def import_config_set(name: str, body: ImportBody):
    """Import a set exported with /export (overwrites the set's configs if it already exists)."""
    try:
        data = yaml.safe_load(body.yaml) or {}
        if not isinstance(data, dict) or not isinstance(data.get("configs"), dict):
            raise topo.TopologyError("File non valido: serve un oggetto con 'configs: {nodo: testo}'")
        cfg_set = body.name or data.get("config_set") or topo.DEFAULT_SET
        if cfg_set not in topo.config_sets(name):
            topo.create_config_set(name, cfg_set)
        for node, text in data["configs"].items():
            topo.write_config(name, cfg_set, str(node), str(text or ""))
        return {"ok": True, "config_set": cfg_set}
    except Exception as exc:
        raise _err(exc)


# ---------------------------------------------------------- packet capture

@app.get("/api/labs/{name}/captures", dependencies=[api])
def list_captures(name: str):
    try:
        topo.load_lab(name)
        live = {s["id"] for s in engine.wireshark.list(name)}
        out = engine.captures.list(name)
        for c in out:  # pcapng still being recorded by an open Wireshark
            if c["id"] in live:
                c.update(running=True, wireshark=True)
        return out
    except Exception as exc:
        raise _err(exc)


class CaptureBody(BaseModel):
    node: str
    iface: str
    filter: str = ""


@app.post("/api/labs/{name}/captures", dependencies=[api], status_code=201)
def start_capture(name: str, body: CaptureBody):
    try:
        host = engine.capture_iface(name, body.node, body.iface)
        return engine.captures.start(name, body.node, body.iface, host, body.filter).info()
    except Exception as exc:
        raise _err(exc)


@app.post("/api/labs/{name}/captures/{cid}/stop", dependencies=[api])
def stop_capture(name: str, cid: str):
    engine.captures.stop(name, cid)
    return {"ok": True}


@app.delete("/api/labs/{name}/captures/{cid}", dependencies=[api])
def delete_capture(name: str, cid: str):
    try:
        engine.captures.delete(name, cid)
        return {"ok": True}
    except Exception as exc:
        raise _err(exc)


@app.get("/api/labs/{name}/captures/{cid}/pcap", dependencies=[api])
def download_capture(name: str, cid: str):
    try:
        f = engine.captures.file(name, cid)
    except Exception as exc:
        raise _err(exc)
    if not f.exists():
        raise HTTPException(404, "Cattura inesistente")
    media = "application/x-pcapng" if f.suffix == ".pcapng" else "application/vnd.tcpdump.pcap"
    return FileResponse(f, media_type=media, filename=f"{name}_{f.name}")


@app.get("/api/labs/{name}/capture-stream", dependencies=[api])
async def capture_stream(name: str, node: str, iface: str, filter: str = ""):
    """Live raw pcap for Wireshark: curl -sN '<url>' | wireshark -k -i -"""
    try:
        host = engine.capture_iface(name, node, iface)
        cmd = engine.captures.stream_command(host, filter)
    except Exception as exc:
        raise _err(exc)
    engine.r.log.append(" ".join(cmd))

    async def body():
        if settings.dry_run:
            yield PCAP_HEADER
            return
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        try:
            while chunk := await proc.stdout.read(65536):
                yield chunk
        finally:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()

    return StreamingResponse(body(), media_type="application/vnd.tcpdump.pcap")


@app.websocket("/api/labs/{name}/captures/{cid}/live")
async def capture_live(ws: WebSocket, name: str, cid: str):
    """Push new packets of a capture as JSON: {"packets": [...], "info": {...}}."""
    if not _token_ok(_given(ws)) or _ws_level(ws, name) < 1:
        await ws.close(code=4401)
        return
    await ws.accept()
    cap = engine.captures.get(name, cid)
    if cap is None:
        await ws.send_json({"packets": [], "info": None, "error": "Cattura non attiva"})
        await ws.close()
        return
    last = int(ws.query_params.get("since", "0") or 0)
    try:
        while True:
            pkts = cap.since(last)
            if pkts:
                last = pkts[-1]["n"]
            await ws.send_json({"packets": pkts, "info": cap.info()})
            if not cap.running and not pkts:
                await ws.close()
                return
            await asyncio.sleep(0.05 if len(pkts) >= 500 else 0.4)
    except (WebSocketDisconnect, RuntimeError):
        pass


# ------------------------------------------------- Wireshark in the browser

@app.get("/api/labs/{name}/wireshark", dependencies=[api])
def list_wireshark(name: str):
    return engine.wireshark.list(name)


@app.post("/api/labs/{name}/wireshark", dependencies=[api], status_code=201)
def start_wireshark(name: str, body: CaptureBody):
    try:
        host = engine.capture_iface(name, body.node, body.iface)
        return engine.wireshark.start(name, body.node, body.iface, host, body.filter).info()
    except Exception as exc:
        raise _err(exc)


class OpenCaptureBody(BaseModel):
    capture: str


@app.post("/api/labs/{name}/wireshark/open", dependencies=[api], status_code=201)
def open_in_wireshark(name: str, body: OpenCaptureBody):
    """Open a saved capture (pcap/pcapng) in the in-browser Wireshark."""
    try:
        f = engine.captures.file(name, body.capture)
        if not f.exists():
            raise FileNotFoundError("Cattura inesistente")
        node, iface = (body.capture.split("_") + ["", ""])[:2]
        return engine.wireshark.start(name, node, iface, None, readfile=f.name).info()
    except Exception as exc:
        raise _err(exc)


@app.delete("/api/labs/{name}/wireshark/{sid}", dependencies=[api])
def stop_wireshark(name: str, sid: str):
    engine.wireshark.stop(name, sid)
    return {"ok": True}


async def _proxy_tcp(ws: WebSocket, port: int, tries: int, alive, unreachable: str) -> None:
    """Binary websocket <-> a loopback TCP port (VNC for noVNC); `tries` connection attempts 0.25 s apart."""
    reader = writer = None
    for _ in range(tries):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            break
        except OSError:
            if not alive():
                break
            await asyncio.sleep(0.25)
    if writer is None:
        await ws.close(code=4502, reason=unreachable)
        return

    async def up():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            data = msg.get("bytes") or (msg.get("text") or "").encode()
            writer.write(data)
            await writer.drain()

    async def down():
        while data := await reader.read(65536):
            await ws.send_bytes(data)

    tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, ConnectionError):
        pass
    for t in tasks:
        t.cancel()
    writer.close()
    try:
        await ws.close()
    except RuntimeError:
        pass


@app.websocket("/api/labs/{name}/wireshark/{sid}/vnc")
async def wireshark_vnc(ws: WebSocket, name: str, sid: str):
    """Binary websocket <-> the session's loopback VNC port (for noVNC)."""
    if not _token_ok(_given(ws)) or _ws_level(ws, name) < 2:
        await ws.close(code=4401)
        return
    s = engine.wireshark.get(name, sid)
    if s is None or s.stopped is not None:
        await ws.close(code=4404, reason="Sessione Wireshark terminata")
        return
    await ws.accept(subprotocol="binary" if "binary" in ws.scope.get("subprotocols", []) else None)
    engine.wireshark.attach(s)
    try:
        # the container needs a moment to bring Xvnc up
        await _proxy_tcp(ws, s.port, 4 if settings.dry_run else 40, lambda: s.stopped is None, "Wireshark non raggiungibile")
    finally:
        engine.wireshark.detach(s)


@app.websocket("/api/labs/{name}/nodes/{node}/console")
async def console(ws: WebSocket, name: str, node: str):
    if not _token_ok(_given(ws)) or _ws_level(ws, name) < 2:
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        cmd = engine.console_command(name, node)
    except Exception as exc:
        await ws.send_text(f"\x1b[31m{exc}\x1b[0m\r\n")
        await ws.close()
        return
    await bridge_pty(ws, cmd)


@app.websocket("/api/labs/{name}/nodes/{node}/vnc")
async def node_vnc(ws: WebSocket, name: str, node: str):
    """Graphical console of a QEMU node (its VNC display) for noVNC in the browser."""
    if not _token_ok(_given(ws)) or _ws_level(ws, name) < 2:
        await ws.close(code=4401)
        return
    ns = load_state(name).nodes.get(node)
    if ns is None or ns.status != "running" or ns.kind != "qemu" or ns.vnc_display is None:
        await ws.close(code=4404, reason="Il nodo non è in esecuzione")
        return
    await ws.accept(subprotocol="binary" if "binary" in ws.scope.get("subprotocols", []) else None)
    await _proxy_tcp(ws, 5900 + ns.vnc_display, 4, lambda: True, "VNC del nodo non raggiungibile")


@app.websocket("/api/labs/{name}/nodes/{node}/guac")
async def node_guac(ws: WebSocket, name: str, node: str):
    """Guacamole WebSocketTunnel <-> guacd, for the RDP console in the browser."""
    if not _token_ok(_given(ws)) or _ws_level(ws, name) < 2:
        await ws.close(code=4401)
        return
    t = guac.take_ticket(ws.query_params.get("ticket", ""), name, node)
    if t is None:
        await ws.close(code=4403, reason="Richiesta scaduta: riapri la consolle")
        return
    await ws.accept(subprotocol="guacamole" if "guacamole" in ws.scope.get("subprotocols", []) else None)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", guac.GUACD_PORT)
        rest, dec = await guac.handshake(reader, writer, "rdp", t.params, int(t.params.get("width", 1280)), int(t.params.get("height", 800)))
    except Exception as exc:
        await ws.send_text(guac.tunnel_uuid_instruction() + guac.encode("error", f"guacd non raggiungibile: {exc}", "519"))
        await ws.close()
        return
    await ws.send_text(guac.tunnel_uuid_instruction())

    async def down():
        buf = rest
        while True:
            _, raw, buf = guac.split(buf)
            if raw:  # the browser parses whole instructions only
                await ws.send_text("".join(raw))
            data = await reader.read(65536)
            if not data:
                return
            buf += dec.decode(data)

    async def up():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            text = msg.get("text") or (msg.get("bytes") or b"").decode(errors="replace")
            if text.startswith("0.,"):  # the tunnel's own pings: answered here, never sent to guacd
                await ws.send_text(text)
                continue
            writer.write(text.encode())
            await writer.drain()

    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, ConnectionError):
        pass
    for task in tasks:
        task.cancel()
    writer.close()
    try:
        await ws.close()
    except RuntimeError:
        pass


# ------------------------------------------------------------------ web UI

if settings.web_dir.is_dir() and (settings.web_dir / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=settings.web_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        root = settings.web_dir.resolve()
        f = (root / path).resolve()
        if path and f.is_file() and root in f.parents:  # static files of the UI (icons, favicon...), never outside it
            return FileResponse(f)
        # never cache the page itself, so a new build shows up on a plain reload
        return FileResponse(settings.web_dir / "index.html", headers={"Cache-Control": "no-cache"})
