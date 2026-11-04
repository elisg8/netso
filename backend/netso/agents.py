"""AI agents: the user's own LLM providers (Anthropic, OpenAI, Google, Perplexity, Ollama, Hermes…)
and subscription CLIs (Claude Pro/Max, Gemini…) already logged in on the server.

Only the connection details live here (in <home>/agents.json): kind, base URL, model, and either an
API key or a local command. NetSO never ships a key and never calls a provider on its own — only when
the user asks, from the UI. The key stays on the server and is never sent back to the browser.

Three auth modes:
  - "key":  cloud API with the user's API key (Anthropic, OpenAI, Google, Perplexity).
  - "http": a local HTTP endpoint with no key (Ollama, OpenAI-compatible servers).
  - "cli":  a local command already authenticated with a *subscription* (e.g. the `claude` CLI on a
            Pro/Max plan, the `gemini` CLI, or any script); NetSO pipes the prompt to it. Web-only
            subscriptions (ChatGPT Plus web) have no usable API, so their official CLI is the way in.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .config import settings


class AgentError(Exception):
    pass


# provider kind -> defaults the UI offers; `auth` says how it is called (key / http / cli)
KINDS = {
    "anthropic": {"label": "Anthropic (Claude) · API", "auth": "key", "base": "https://api.anthropic.com", "model": "claude-sonnet-4-5"},
    "openai": {"label": "OpenAI · API", "auth": "key", "base": "https://api.openai.com", "model": "gpt-4o-mini"},
    "google": {"label": "Google (Gemini) · API", "auth": "key", "base": "https://generativelanguage.googleapis.com", "model": "gemini-1.5-flash"},
    "perplexity": {"label": "Perplexity · API", "auth": "key", "base": "https://api.perplexity.ai", "model": "sonar"},
    "ollama": {"label": "Ollama (locale, gratis)", "auth": "http", "base": "http://localhost:11434", "model": "llama3.1"},
    "openai-compat": {"label": "Compatibile OpenAI (Hermes, LM Studio, vLLM…)", "auth": "http", "base": "http://localhost:1234", "model": "hermes"},
    "cli": {"label": "Comando / CLI con abbonamento (Claude Pro, Gemini…)", "auth": "cli", "base": "", "model": "", "command": "claude -p"},
}


def _auth(kind: str) -> str:
    return KINDS.get(kind, {}).get("auth", "key")


def _file() -> Path:
    return settings.home / "agents.json"


def _load() -> list[dict]:
    try:
        return json.loads(_file().read_text()).get("agents", [])
    except (OSError, ValueError):
        return []


def _save(agents: list[dict]) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps({"agents": agents}, indent=2))


def _public(a: dict) -> dict:
    return {"id": a["id"], "name": a["name"], "kind": a["kind"], "base_url": a.get("base_url", ""),
            "model": a.get("model", ""), "command": a.get("command", ""), "enabled": a.get("enabled", True),
            "auth": _auth(a["kind"]), "has_key": bool(a.get("api_key"))}


def listing() -> list[dict]:
    return [_public(a) for a in _load()]


def kinds() -> list[dict]:
    return [{"id": k, **v} for k, v in KINDS.items()]


def save(data: dict) -> dict:
    kind = data.get("kind")
    if kind not in KINDS:
        raise AgentError(f"Tipo provider sconosciuto: {kind}")
    name = (data.get("name") or "").strip()
    if not name:
        raise AgentError("Dai un nome all'agente")
    agents = _load()
    aid = data.get("id")
    cur = next((a for a in agents if a["id"] == aid), None) if aid else None
    rec = cur or {"id": uuid.uuid4().hex[:12]}
    rec.update({"name": name, "kind": kind,
                "base_url": (data.get("base_url") or KINDS[kind]["base"]).rstrip("/"),
                "model": data.get("model") or KINDS[kind]["model"],
                "enabled": bool(data.get("enabled", True))})
    if _auth(kind) == "cli":
        cmd = (data.get("command") or "").strip()
        if not cmd:
            raise AgentError("Indica il comando da eseguire (es. «claude -p» o «ollama run llama3»)")
        rec["command"] = cmd
    key = data.get("api_key")
    if key:  # an empty/omitted key on edit keeps the stored one
        rec["api_key"] = key
    if cur is None:
        agents.append(rec)
    _save(agents)
    return _public(rec)


def delete(aid: str) -> None:
    agents = _load()
    if not any(a["id"] == aid for a in agents):
        raise FileNotFoundError(f"Agente '{aid}' inesistente")
    _save([a for a in agents if a["id"] != aid])


def _get(aid: str) -> dict:
    a = next((x for x in _load() if x["id"] == aid), None)
    if not a:
        raise FileNotFoundError(f"Agente '{aid}' inesistente")
    return a


def build_request(a: dict, prompt: str, system: str | None = None) -> tuple[str, dict, dict]:
    """(url, headers, json-body) for the agent's provider. Kept separate so it is unit-testable."""
    kind, base, model = a["kind"], a.get("base_url") or KINDS[a["kind"]]["base"], a.get("model") or KINDS[a["kind"]]["model"]
    key = a.get("api_key") or ""
    if _auth(kind) == "key" and not key:
        raise AgentError(f"Manca la chiave API per '{a['name']}'")
    if kind == "anthropic":
        headers = {"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}
        body = {"model": model, "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        return f"{base}/v1/messages", headers, body
    if kind == "google":
        msgs = {"contents": [{"parts": [{"text": prompt}]}]}
        if system:
            msgs["systemInstruction"] = {"parts": [{"text": system}]}
        return f"{base}/v1beta/models/{model}:generateContent?key={key}", {"content-type": "application/json"}, msgs
    # openai, perplexity, ollama, openai-compat: OpenAI chat-completions shape
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    path = "/api/chat" if kind == "ollama" else "/v1/chat/completions"
    if kind == "ollama":
        return f"{base}/v1/chat/completions", headers, {"model": model, "messages": messages, "stream": False}
    return f"{base}{path}", headers, {"model": model, "messages": messages}


def parse_response(kind: str, data: dict) -> str:
    try:
        if kind == "anthropic":
            return "".join(b.get("text", "") for b in data.get("content", []))
        if kind == "google":
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AgentError(f"Risposta del provider non interpretabile: {json.dumps(data)[:200]}")


def run_cli(a: dict, prompt: str, system: str | None = None, timeout: float = 120) -> str:
    """Pipe the prompt to a local command already logged in with the user's subscription (claude, gemini…).

    The command runs on the server with NetSO's privileges; only an admin can configure it. Put the prompt
    where the CLI wants it: a `{prompt}` placeholder in the command receives it as an argument, otherwise it
    is fed on stdin.
    """
    cmd = (a.get("command") or KINDS["cli"]["command"]).strip()
    text = (f"{system}\n\n{prompt}" if system else prompt)
    try:
        toks = shlex.split(cmd)
    except ValueError as exc:
        raise AgentError(f"Comando non valido: {exc}")
    if not toks:
        raise AgentError("Comando vuoto")
    on_stdin = "{prompt}" not in toks
    toks = [text if t == "{prompt}" else t for t in toks]
    try:
        r = subprocess.run(toks, input=(text if on_stdin else None), capture_output=True,  # noqa: S603 — admin-configured command
                           text=True, timeout=timeout)
    except FileNotFoundError:
        raise AgentError(f"Comando non trovato: {toks[0]} (installalo e fai il login col tuo abbonamento)")
    except subprocess.TimeoutExpired:
        raise AgentError("Il comando non ha risposto in tempo")
    except OSError as exc:
        raise AgentError(f"Non riesco a eseguire il comando: {exc}")
    if r.returncode != 0:
        raise AgentError(f"Il comando è uscito con errore: {(r.stderr or r.stdout).strip()[:300] or r.returncode}")
    return (r.stdout or "").strip()


def ask(aid: str, prompt: str, system: str | None = None, timeout: float = 120) -> str:
    a = _get(aid)
    if not a.get("enabled", True):
        raise AgentError(f"L'agente '{a['name']}' è disattivato")
    if _auth(a["kind"]) == "cli":
        return run_cli(a, prompt, system, timeout)
    url, headers, body = build_request(a, prompt, system)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — user-configured provider URL
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise AgentError(f"Il provider ha risposto {exc.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AgentError(f"Non raggiungo il provider ({a.get('base_url')}): {exc}")
    return parse_response(a["kind"], data).strip()
