// Wireshark in the browser: noVNC client on the API's websocket proxy (EVE-NG Pro style).
import { useEffect, useRef, useState } from 'react'
import RFB from '@novnc/novnc'
import { api, wsUrl } from './api.js'
import { Ui } from './icons.jsx'
import { t, useLang } from './i18n.js'

// The remote Wireshark screen. The X server follows the size of this box.
export function WiresharkScreen({ lab, session, hidden }) {
  useLang()
  const box = useRef(null)
  const rfb = useRef(null)
  const [status, setStatus] = useState('connecting')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    setStatus('connecting')
    const r = new RFB(box.current, wsUrl(`/labs/${encodeURIComponent(lab)}/wireshark/${encodeURIComponent(session.id)}/vnc`), { wsProtocols: ['binary'] })
    r.resizeSession = true
    r.scaleViewport = true // only kicks in if the server cannot follow the window size
    r.background = '#0a0c14'
    let closed = false
    r.addEventListener('connect', () => { setStatus('on'); r.focus() })
    // dry run: there is no container behind, unless something answers on the VNC port
    r.addEventListener('disconnect', () => { closed = true; setStatus(session.dry_run ? 'dry' : 'ended') })
    rfb.current = r
    return () => { if (!closed) r.disconnect(); rfb.current = null }
  }, [lab, session.id, session.dry_run, attempt])

  useEffect(() => { if (!hidden && status === 'on') rfb.current?.focus() }, [hidden, status])

  return (
    <div className="ws-screen">
      <div className="ws-vnc" ref={box} />
      {status !== 'on' && (
        <div className="ws-overlay">
          {status === 'connecting' && <><span className="spin" /><p>{t('wireshark.starting')}</p></>}
          {status === 'ended' && <>
            <Ui name="capture" size={36} />
            <p>{t('wireshark.closedUnreachable')}</p>
            <button onClick={() => setAttempt((a) => a + 1)}><Ui name="refresh" size={14} /> {t('wireshark.reconnect')}</button>
          </>}
          {status === 'dry' && <>
            <Ui name="capture" size={40} />
            <p><b>{t('wireshark.dryTitle')}</b>: {t('wireshark.dryIntro')}<br />
              {t('wireshark.dryLiveOn')} <code>{session.node} {session.iface}</code>{session.filter ? <> {t('wireshark.dryWithFilter')} <code>{session.filter}</code></> : ''}.</p>
            <p className="muted small">{t('wireshark.dryHintPre')} <code>netso/wireshark</code> {t('wireshark.dryHintPost')}</p>
          </>}
        </div>
      )}
    </div>
  )
}

function title(s) {
  return s.live === false ? `${s.file}` : `${s.node} ${s.iface}`
}

// Floating window over the editor; can be minimized to a pill so you keep working on the topology.
export function WiresharkWindows({ lab, wins, onMinimize, onRestore, onClose, onNewTab }) {
  useLang()
  const open = wins.find((w) => !w.min)
  return (
    <>
      {wins.map((w) => (
        <div key={w.session.id} className={`ws-win ${w.min ? 'min' : ''}`}>
          <div className="ws-head">
            <span className="ws-logo"><Ui name="capture" size={16} /></span>
            <b>Wireshark</b>
            <span className="muted">{w.session.live === false ? 'file' : 'live'} · <code>{title(w.session)}</code>{w.peer && <> ⟷ {w.peer}</>}</span>
            {w.session.filter && <code className="ws-filter">{w.session.filter}</code>}
            <div className="spacer" />
            <button className="ghost" title={t('wireshark.openNewTab')} onClick={() => onNewTab(w)}><Ui name="exit" size={15} /> {t('wireshark.newTab')}</button>
            <button className="icon-btn" title={t('wireshark.minimize')} onClick={() => onMinimize(w)}><Ui name="minus" size={16} /></button>
            <button className="icon-btn danger-t" title={t('wireshark.closeKeep')} onClick={() => onClose(w)}><Ui name="close" size={16} /></button>
          </div>
          <WiresharkScreen lab={lab} session={w.session} hidden={w.min} />
        </div>
      ))}
      {wins.some((w) => w.min) && (
        <div className="ws-pills">
          {wins.filter((w) => w.min).map((w) => (
            <button key={w.session.id} className="ws-pill" onClick={() => onRestore(w)} title={t('wireshark.show')}>
              {w.session.live !== false && <span className="rec on" />}<Ui name="capture" size={14} /> {title(w.session)}
            </button>
          ))}
        </div>
      )}
      {open && <div className="ws-dim" onClick={() => onMinimize(open)} />}
    </>
  )
}

// Stand-alone page: #/wireshark/<lab>/<session>
export function WiresharkPage({ lab, sid }) {
  useLang()
  const [session, setSession] = useState(null)
  const [missing, setMissing] = useState(false)
  useEffect(() => {
    api(`/labs/${encodeURIComponent(lab)}/wireshark`)
      .then((list) => { const s = list.find((x) => x.id === sid); s ? setSession(s) : setMissing(true) })
      .catch(() => setMissing(true))
  }, [lab, sid])
  useEffect(() => { document.title = session ? `Wireshark · ${title(session)} · ${lab}` : 'Wireshark' }, [session, lab])
  const stop = async () => {
    if (!confirm(t('wireshark.confirmClose'))) return
    try { await api(`/labs/${encodeURIComponent(lab)}/wireshark/${encodeURIComponent(sid)}`, { method: 'DELETE' }) } catch { /* ignore */ }
    window.close()
    setMissing(true)
  }
  return (
    <div className="ws-page">
      <div className="ws-head">
        <span className="ws-logo"><Ui name="capture" size={16} /></span>
        <b>Wireshark</b>
        <span className="muted">{lab}{session && <> · <code>{title(session)}</code></>}</span>
        {session?.filter && <code className="ws-filter">{session.filter}</code>}
        <div className="spacer" />
        {session && <button className="ghost danger-t" onClick={stop}><Ui name="close" size={15} /> {t('wireshark.close')}</button>}
      </div>
      {session ? <WiresharkScreen lab={lab} session={session} />
        : <div className="ws-overlay static">{missing ? <p>{t('wireshark.sessionEnded')}</p> : <span className="spin" />}</div>}
    </div>
  )
}
