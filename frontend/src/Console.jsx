import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import RFB from '@novnc/novnc'
import Guacamole from 'guacamole-common-js'
import { api, consoleUrl, getToken, wsUrl } from './api.js'
import { Ui } from './icons.jsx'

// ------------------------------------------------------------------ clipboard between the Mac and the VM
const isMac = /Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent)
const CTRL = 0xffe3
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const keysymOf = (ch) => { const c = ch.codePointAt(0); return ch === '\n' ? 0xff0d : ch === '\t' ? 0xff09 : c < 0x100 ? c : 0x01000000 + c }

function copyExec(text) {
  const t = document.createElement('textarea')
  t.value = text
  t.style.cssText = 'position:fixed;left:-9999px;top:0'
  document.body.append(t)
  t.select()
  let ok = false
  try { ok = document.execCommand('copy') } catch { /* not allowed */ }
  t.remove()
  return ok
}

// Mac clipboard: async API on https/localhost, otherwise a hidden textarea + execCommand (needs a recent user action)
function copyLocal(text) {
  if (window.isSecureContext && navigator.clipboard?.writeText) return navigator.clipboard.writeText(text).then(() => true, () => copyExec(text))
  return Promise.resolve(copyExec(text))
}

// Cmd+V pastes the Mac clipboard into the VM, Cmd+C/X/A/Z become Ctrl+… in the VM, text copied in the VM goes back
// to the Mac. ctl = {setClipboard(text), key(keysym, down), focus()}; the VM's clipboard arrives through fromVm(text).
// "paste" mode: VM clipboard + Ctrl+V (needs RDP or the guest agent); "type" mode: the text is typed key by key.
function useClipboardBridge(box, ctl, active, defaultMode) {
  const store = `netso.pasteMode.${defaultMode}`
  const [mode, setModeState] = useState(() => { try { return localStorage.getItem(store) || defaultMode } catch { return defaultMode } })
  const setMode = (m) => { setModeState(m); try { localStorage.setItem(store, m) } catch { /* private window */ } }
  const [vmText, setVmText] = useState('')
  const [pending, setPending] = useState(null) // VM text the browser did not let us copy without a click
  const sink = useRef(null)
  const modeRef = useRef(mode)
  modeRef.current = mode

  const combo = (k) => { const c = ctl.current; if (!c) return; c.key(CTRL, true); c.key(k, true); c.key(k, false); c.key(CTRL, false) }
  const typeText = async (text) => {
    for (const ch of text.replace(/\r\n?/g, '\n')) {
      const k = keysymOf(ch)
      ctl.current?.key(k, true); ctl.current?.key(k, false)
      await sleep(8)
    }
  }
  const sendText = (text, m = modeRef.current) => {
    if (!text || !ctl.current) return
    ctl.current.setClipboard(text)
    if (m === 'type') typeText(text)
    else setTimeout(() => combo(0x76), 60) // Ctrl+V once the clipboard has arrived
  }
  const fromVm = (text) => {
    setVmText(text)
    copyLocal(text).then((ok) => setPending(ok ? null : text))
  }

  useEffect(() => {
    if (!active) return
    // only while the VM screen has the focus (not the fields of the clipboard panel)
    const mine = () => { const a = document.activeElement; return box.current?.contains(a) && !(/^(INPUT|TEXTAREA)$/.test(a.tagName) && a !== sink.current) }
    const onKey = (e) => {
      if (!mine()) return
      const k = e.key.toLowerCase()
      if (isMac && e.key === 'Meta') { e.stopPropagation(); return } // Cmd is ours, not the VM's Windows key
      if ((isMac ? e.metaKey : e.ctrlKey && !e.altKey) && k === 'v') {
        if (e.type === 'keydown') { // the browser pastes into the sink; give the focus back to the screen if nothing comes
          sink.current.value = ''; sink.current.focus()
          setTimeout(() => { if (document.activeElement === sink.current) ctl.current?.focus() }, 400)
        }
        e.stopPropagation()
        return
      }
      if (isMac && e.metaKey) {
        e.preventDefault(); e.stopPropagation()
        if (e.type === 'keydown' && ['c', 'x', 'a', 'z', 'y', 'f', 's'].includes(k)) combo(k.charCodeAt(0))
      }
    }
    addEventListener('keydown', onKey, true)
    addEventListener('keyup', onKey, true)
    return () => { removeEventListener('keydown', onKey, true); removeEventListener('keyup', onKey, true) }
  }, [active, box])

  const onSinkPaste = (e) => {
    e.preventDefault()
    sendText(e.clipboardData?.getData('text/plain') || '')
    ctl.current?.focus()
  }
  const sinkEl = <textarea ref={sink} className="paste-sink" aria-hidden="true" tabIndex={-1} onPaste={onSinkPaste} />
  return { mode, setMode, vmText, pending, setPending, sendText, fromVm, sinkEl }
}

function ClipPanel({ clip, disabled }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  return (
    <span className="clip-wrap">
      <button className={`ghost ${open ? 'on' : ''}`} disabled={disabled} title="Appunti tra il Mac e la VM" onClick={() => setOpen((v) => !v)}><Ui name="copy" size={13} /> Appunti</button>
      {clip.pending && <button className="ghost clip-pending" title={clip.pending.slice(0, 200)} onClick={() => { copyExec(clip.pending); clip.setPending(null) }}>Copia sul Mac</button>}
      {open && (
        <div className="clip-pop">
          <div className="seg small">
            <span className="muted">{isMac ? '⌘V' : 'Ctrl+V'}</span>
            <button className={clip.mode === 'paste' ? 'on' : ''} onClick={() => clip.setMode('paste')} title="Appunti della VM + Ctrl+V (RDP, o VM con spice-vdagent)">Incolla</button>
            <button className={clip.mode === 'type' ? 'on' : ''} onClick={() => clip.setMode('type')} title="Scrive il testo tasto per tasto: funziona sempre">Digita</button>
          </div>
          <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="Testo da mandare alla VM" />
          <div className="row-end">
            <button onClick={() => { clip.sendText(text, 'type') }} disabled={!text}>Digita</button>
            <button className="primary" onClick={() => { clip.sendText(text, 'paste') }} disabled={!text}>Incolla</button>
          </div>
          {clip.vmText && <>
            <small className="muted">Copiato nella VM</small>
            <textarea rows={3} readOnly value={clip.vmText} />
            <div className="row-end"><button onClick={() => copyExec(clip.vmText)}>Copia sul Mac</button></div>
          </>}
        </div>
      )}
    </span>
  )
}

// Graphical console (VNC display of a QEMU node, e.g. Windows or a Linux desktop) through the API's websocket proxy.
function VncConsole({ lab, node, visible, spice }) {
  const box = useRef(null)
  const rfb = useRef(null)
  const [status, setStatus] = useState('connecting')
  const [attempt, setAttempt] = useState(0)
  const [fit, setFit] = useState(true)   // true = adatta allo schermo; false = dimensione reale (1:1)
  const wrap = useRef(null)
  const ctl = useRef(null)
  const clip = useClipboardBridge(wrap, ctl, visible && status === 'on', 'type')
  const fromVm = useRef(clip.fromVm)
  fromVm.current = clip.fromVm

  useEffect(() => {
    setStatus('connecting')
    const r = new RFB(box.current, wsUrl(`/labs/${encodeURIComponent(lab)}/nodes/${encodeURIComponent(node)}/vnc`), { wsProtocols: ['binary'] })
    r.scaleViewport = fit
    r.clipViewport = !fit            // 1:1 mode: scroll/pan instead of scaling
    r.resizeSession = true           // ask the guest to match the window, where it supports it (Linux desktops)
    r.qualityLevel = 8
    r.background = '#0a0c14'
    let closed = false
    r.addEventListener('connect', () => { setStatus('on'); r.focus() })
    r.addEventListener('disconnect', () => { closed = true; setStatus('ended') })
    r.addEventListener('clipboard', (e) => fromVm.current(e.detail.text))
    rfb.current = r
    ctl.current = { setClipboard: (t) => r.clipboardPasteFrom(t), key: (k, down) => r.sendKey(k, null, down), focus: () => r.focus() }
    return () => { if (!closed) r.disconnect(); rfb.current = null; ctl.current = null }
  }, [lab, node, attempt]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (rfb.current) { rfb.current.scaleViewport = fit; rfb.current.clipViewport = !fit } }, [fit])
  useEffect(() => { if (visible && status === 'on') rfb.current?.focus() }, [visible, status])

  const fullscreen = () => {
    const el = wrap.current
    if (document.fullscreenElement) document.exitFullscreen?.()
    else el?.requestFullscreen?.().then(() => rfb.current?.focus()).catch(() => {})
  }

  return (
    <div className="term vnc-term" ref={wrap} style={{ display: visible ? 'flex' : 'none' }}>
      {clip.sinkEl}
      <div className="vnc-bar">
        <button className="ghost" disabled={status !== 'on'} title="Invia Ctrl+Alt+Canc (login di Windows)" onClick={() => rfb.current?.sendCtrlAltDel()}>Ctrl+Alt+Canc</button>
        <ClipPanel clip={clip} disabled={status !== 'on'} />
        <button className="ghost" title={fit ? 'Dimensione reale (1:1)' : 'Adatta allo schermo'} onClick={() => setFit((v) => !v)}><Ui name="fit" size={13} /> {fit ? 'Adatta' : '1:1'}</button>
        <button className="ghost" title="Schermo intero" onClick={fullscreen}><Ui name="expand" size={13} /> Schermo intero</button>
        <button className="ghost" title="Riconnetti" onClick={() => setAttempt((a) => a + 1)}><Ui name="refresh" size={13} /> Riconnetti</button>
        {spice && <SpiceLink lab={lab} node={node} />}
      </div>
      <div className="vnc-screen" ref={box} />
      {status !== 'on' && (
        <div className="ws-overlay">
          {status === 'connecting' ? <><span className="spin" /><p>Connessione allo schermo di {node}…</p></> : <>
            <p>Schermo di {node} non raggiungibile (nodo spento o VM ancora in avvio).</p>
            <button onClick={() => setAttempt((a) => a + 1)}><Ui name="refresh" size={14} /> Riconnetti</button>
          </>}
        </div>
      )}
    </div>
  )
}

// SPICE has no browser client here: the screen is shown over VNC, this opens the real SPICE viewer (remote-viewer)
function SpiceLink({ lab, node }) {
  const [port, setPort] = useState(null)
  useEffect(() => { api(`/labs/${encodeURIComponent(lab)}/nodes/${encodeURIComponent(node)}/open`, { method: 'POST', body: {} }).then((r) => setPort(r.port)).catch(() => {}) }, [lab, node])
  return port ? <a className="btn ghost" href={`spice://${location.hostname}:${port}`} title="Apri con remote-viewer (virt-viewer)">SPICE {location.hostname}:{port}</a> : null
}

const KEYS = { ctrl: 0xffe3, alt: 0xffe9, del: 0xffff }

// RDP / RDP-TLS: Guacamole client on the API tunnel to guacd (Windows login screen, or straight in with credentials)
function RdpConsole({ lab, node, visible, kind }) {
  const box = useRef(null)
  const client = useRef(null)
  const [cred, setCred] = useState({ user: '', password: '' })
  const [status, setStatus] = useState('form') // form | connecting | on | ended
  const [err, setErr] = useState('')
  const ctl = useRef(null)
  const clip = useClipboardBridge(box, ctl, visible && status === 'on', 'paste')
  const fromVm = useRef(clip.fromVm)
  fromVm.current = clip.fromVm

  const disconnect = () => { try { client.current?.disconnect() } catch { /* gone */ } client.current = null }
  useEffect(() => disconnect, [])

  const connect = async (e) => {
    e?.preventDefault()
    setErr(''); setStatus('connecting')
    const el = box.current
    let info
    try {
      info = await api(`/labs/${encodeURIComponent(lab)}/nodes/${encodeURIComponent(node)}/open`, { method: 'POST', body: { ...cred, width: el.clientWidth, height: el.clientHeight } })
    } catch (ex) { setErr(ex.message); setStatus('form'); return }
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const c = new Guacamole.Client(new Guacamole.WebSocketTunnel(`${proto}://${location.host}/api/labs/${encodeURIComponent(lab)}/nodes/${encodeURIComponent(node)}/guac`))
    client.current = c
    const display = c.getDisplay()
    el.querySelector('.rdp-screen').replaceChildren(display.getElement())
    const fit = () => { const w = display.getWidth(), h = display.getHeight(); if (w && h) display.scale(Math.min(el.clientWidth / w, (el.clientHeight - 34) / h, 1)) }
    display.onresize = fit
    c.onstatechange = (st) => { if (st === 3) { setStatus('on'); el.focus() } if (st === 5) setStatus((s) => (s === 'form' ? s : 'ended')) }
    c.onerror = (st) => { setErr(st.message || 'Connessione RDP non riuscita'); setStatus('ended') }
    const mouse = new Guacamole.Mouse(display.getElement())
    mouse.onEach(['mousedown', 'mouseup', 'mousemove'], (ev) => c.sendMouseState(ev.state, true))
    const kb = new Guacamole.Keyboard(el)
    kb.onkeydown = (k) => { c.sendKeyEvent(1, k); return false }
    kb.onkeyup = (k) => c.sendKeyEvent(0, k)
    c.onclipboard = (stream, mimetype) => {
      if (!/^text\//.test(mimetype)) return
      const reader = new Guacamole.StringReader(stream)
      let data = ''
      reader.ontext = (t) => { data += t }
      reader.onend = () => fromVm.current(data)
    }
    ctl.current = {
      setClipboard: (t) => { const w = new Guacamole.StringWriter(c.createClipboardStream('text/plain')); w.sendText(t); w.sendEnd() },
      key: (k, down) => c.sendKeyEvent(down ? 1 : 0, k),
      focus: () => el.focus(),
    }
    c.connect(new URLSearchParams({ ticket: info.ticket, ...(getToken() ? { token: getToken() } : {}) }).toString())
  }
  const cad = () => { const c = client.current; if (!c) return; [KEYS.ctrl, KEYS.alt, KEYS.del].forEach((k) => c.sendKeyEvent(1, k)); [KEYS.del, KEYS.alt, KEYS.ctrl].forEach((k) => c.sendKeyEvent(0, k)) }

  return (
    <div className="term vnc-term rdp-term" ref={box} tabIndex={0} style={{ display: visible ? 'flex' : 'none' }}>
      {clip.sinkEl}
      <div className="vnc-bar">
        <button className="ghost" disabled={status !== 'on'} onClick={cad}>Ctrl+Alt+Canc</button>
        <ClipPanel clip={clip} disabled={status !== 'on'} />
        <button className="ghost" onClick={() => { disconnect(); setStatus('form') }}><Ui name="refresh" size={13} /> Riconnetti</button>
        <span className="muted small">{kind.toUpperCase()}</span>
      </div>
      <div className="vnc-screen rdp-screen" />
      {status !== 'on' && (
        <div className="ws-overlay">
          {status === 'connecting' ? <><span className="spin" /><p>Connessione RDP a {node}…</p></> : (
            <form className="rdp-login" onSubmit={connect}>
              <b>{node} · {kind.toUpperCase()}</b>
              <input placeholder="Utente (facoltativo)" value={cred.user} onChange={(e) => setCred({ ...cred, user: e.target.value })} autoComplete="off" />
              <input type="password" placeholder="Password" value={cred.password} onChange={(e) => setCred({ ...cred, password: e.target.value })} autoComplete="new-password" />
              {err && <span className="err small">{err}</span>}
              <button className="primary">Connetti</button>
            </form>
          )}
        </div>
      )}
    </div>
  )
}

// WinBox (MikroTik): the NetSO server forwards a port to the router; connect the WinBox app to it
function ForwardInfo({ lab, node, visible, kind }) {
  const [info, setInfo] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api(`/labs/${encodeURIComponent(lab)}/nodes/${encodeURIComponent(node)}/open`, { method: 'POST', body: {} }).then(setInfo).catch((e) => setErr(e.message)) }, [lab, node])
  const addr = info && `${location.hostname}:${info.port}`
  return (
    <div className="term vnc-term" style={{ display: visible ? 'flex' : 'none' }}>
      <div className="ws-overlay">
        {err ? <p className="err">{err}</p> : !info ? <span className="spin" /> : <>
          <p>{kind === 'winbox' ? 'WinBox' : kind.toUpperCase()} · {node} ({info.ip})</p>
          <code className="fwd-addr">{addr}</code>
          <button onClick={() => navigator.clipboard?.writeText(addr)}>Copia</button>
        </>}
      </div>
    </div>
  )
}

// One node console in the dock: terminal (telnet, docker exec, SSH), screen (VNC, SPICE) or RDP.
export default function Console({ lab, node, visible, kind }) {
  if (kind === 'vnc' || kind === 'spice') return <VncConsole lab={lab} node={node} visible={visible} spice={kind === 'spice'} />
  if (kind === 'rdp' || kind === 'rdp-tls') return <RdpConsole lab={lab} node={node} visible={visible} kind={kind} />
  if (kind === 'winbox') return <ForwardInfo lab={lab} node={node} visible={visible} kind={kind} />
  return <TextConsole lab={lab} node={node} visible={visible} />
}

// EVE-NG style: each console as its own window, floating over the canvas — drag the bar, resize the corner.
// one movable/resizable window holding every open console as a tab; switching a tab keeps the
// others connected in the background, and a newly opened console comes up focused
export function ConsoleWindows({ lab, items, kindOf, onClose }) {
  const [win, setWin] = useState({ x: 90, y: 72, w: 720, h: 460, min: false })
  const [active, setActive] = useState(null)
  const seen = useRef(new Set())
  useEffect(() => {
    const ids = items.map((it) => it.id)
    const fresh = ids.filter((id) => !seen.current.has(id))
    seen.current = new Set(ids)
    setActive((a) => (fresh.length ? fresh[fresh.length - 1] : ids.includes(a) ? a : (ids[ids.length - 1] ?? null)))
    if (fresh.length) setWin((w) => (w.min ? { ...w, min: false } : w))  // una nuova consolle riapre la finestra ridotta
  }, [items])
  const upd = (patch) => setWin((w) => ({ ...w, ...patch }))
  const grab = (e, mode) => {
    e.preventDefault()
    const b = { ...win }, sx = e.clientX, sy = e.clientY
    const move = (ev) => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy
      if (mode === 'move') upd({ x: Math.max(0, Math.min(b.x + dx, innerWidth - 120)), y: Math.max(48, Math.min(b.y + dy, innerHeight - 40)) })
      else upd({ w: Math.max(360, b.w + dx), h: Math.max(220, b.h + dy) })
    }
    const up = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', up) }
    addEventListener('mousemove', move); addEventListener('mouseup', up)
  }
  if (!items.length) return null
  if (win.min) return (
    <div className="cwin-pills">
      <button className="cwin-pill" title="Ripristina le consolle" onClick={() => upd({ min: false })}><Ui name="console" size={13} /> Consolle ({items.length})</button>
    </div>
  )
  return (
    <div className="cwin" style={{ left: win.x, top: win.y, width: win.w, height: win.h, zIndex: 40 }}>
      <div className="cwin-head" title="Trascina per spostare la finestra" onMouseDown={(e) => grab(e, 'move')}>
        <span className="cwin-drag"><Ui name="console" size={14} /></span>
        {items.length === 1 ? (
          <span className="cwin-title grow">{items[0].node}</span>
        ) : (
          <div className="cwin-tabs">
            {items.map((it) => (
              <div key={it.id} className={`tab ${it.id === active ? 'on' : ''}`} title={it.node} onMouseDown={(e) => { e.stopPropagation(); setActive(it.id) }}>
                <span className="grow">{it.node}</span>
                <button className="x" title="Chiudi scheda" onMouseDown={(e) => e.stopPropagation()} onClick={() => onClose(it.id)}>×</button>
              </div>
            ))}
          </div>
        )}
        <button className="cwin-btn" title="Riduci a icona" onMouseDown={(e) => e.stopPropagation()} onClick={() => upd({ min: true })}><Ui name="minus" size={14} /></button>
        <button className="cwin-btn" title={items.length > 1 ? 'Chiudi tutte le consolle' : 'Chiudi'} onMouseDown={(e) => e.stopPropagation()} onClick={() => items.forEach((it) => onClose(it.id))}><Ui name="close" size={14} /></button>
      </div>
      <div className="cwin-body">
        {items.map((it) => <Console key={it.id} lab={lab} node={it.node} visible={it.id === active} kind={kindOf(it.node)} />)}
      </div>
      <div className="cwin-grip" title="Ridimensiona" onMouseDown={(e) => grab(e, 'resize')} />
    </div>
  )
}

function TextConsole({ lab, node, visible }) {
  const host = useRef(null)
  const fitRef = useRef(null)

  useEffect(() => {
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: '"JetBrains Mono", "Cascadia Mono", Menlo, Consolas, monospace',
      fontSize: 13,
      scrollback: 10000,
      theme: { background: '#0a0c14', foreground: '#e3e7f4', cursor: '#8b7bff', selectionBackground: '#8b7bff55' },
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host.current)
    fitRef.current = fit
    term.writeln(`\x1b[36mConnessione a ${node}...\x1b[0m`)

    const ws = new WebSocket(consoleUrl(lab, node))
    const sendSize = () => {
      try { fit.fit() } catch { /* hidden */ }
      if (ws.readyState === 1) ws.send(JSON.stringify({ t: 'r', c: term.cols, r: term.rows }))
    }
    ws.onopen = () => { sendSize(); term.focus() }
    ws.onmessage = (e) => term.write(e.data)
    ws.onclose = () => term.writeln('\r\n\x1b[90m[disconnesso]\x1b[0m')
    const sub = term.onData((d) => ws.readyState === 1 && ws.send(JSON.stringify({ t: 'i', d })))
    const ro = new ResizeObserver(sendSize)
    ro.observe(host.current)

    return () => { ro.disconnect(); sub.dispose(); ws.close(); term.dispose() }
  }, [lab, node])

  useEffect(() => {
    if (visible) setTimeout(() => { try { fitRef.current?.fit() } catch { /* ignore */ } }, 0)
  }, [visible])

  return <div className="term" ref={host} style={{ display: visible ? 'block' : 'none' }} />
}

// how a node's console opens (same rule as the editor)
export const consoleKind = (ns) => (!ns ? null : ns.container ? 'exec' : ns.kind === 'qemu' ? (ns.console || (ns.console_port ? 'telnet' : 'vnc')) : 'telnet')
export const POPUP_KINDS = ['vnc', 'spice', 'rdp', 'rdp-tls']

// stand-alone console window (popup) opened from a lab: #/console/<lab>/<node>
export function ConsolePage({ lab, node }) {
  const [kind, setKind] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    document.title = `${node} · ${lab}`
    api(`/labs/${encodeURIComponent(lab)}`).then((r) => {
      const ns = r.state?.nodes?.[node]
      ns?.status === 'running' ? setKind(consoleKind(ns)) : setErr(`${node} non è acceso`)
    }).catch((e) => setErr(e.message))
  }, [lab, node])
  return (
    <div className="ws-page console-page">
      {err ? <div className="ws-overlay"><p>{err}</p></div> : kind ? <Console lab={lab} node={node} visible kind={kind} /> : <div className="ws-overlay"><span className="spin" /></div>}
    </div>
  )
}

// opens (or brings back) the popup window of a node's graphical console
export function openConsoleWindow(lab, node) {
  const url = `${location.origin}${location.pathname}#/console/${encodeURIComponent(lab)}/${encodeURIComponent(node)}`
  const w = window.open(url, `netso-${lab}-${node}`, 'popup,width=1180,height=800')
  w?.focus()
  return !!w
}
