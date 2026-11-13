// Login page and change-password dialog.
import { useEffect, useId, useState } from 'react'
import { api } from './api.js'
import { Ui } from './icons.jsx'
import { ModalHead, ThemeButton, LangButton } from './LabView.jsx'
import { t, useLang } from './i18n.js'

// App mark: an "N" drawn as a network path through four nodes
export function Logo({ size = 32 }) {
  const id = useId()
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#a08cff" /><stop offset=".5" stopColor="#6a7dff" /><stop offset="1" stopColor="#2dd4bf" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${id})`} />
      <path d="M10 22V10l12 12V10" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx="10" cy="22" r="3" fill="#fff" /><circle cx="10" cy="10" r="3" fill="#fff" /><circle cx="22" cy="22" r="3" fill="#fff" />
      <circle cx="22" cy="10" r="2.7" fill="#1b1d4a" stroke="#fff" strokeWidth="2" />
    </svg>
  )
}

export default function Login({ onLogin }) {
  useLang()
  const [user, setUser] = useState('')
  const [pass, setPass] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState(null)
  useEffect(() => { api('/auth/hint', { noAuth: true }).then(setHint).catch(() => {}) }, [])
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setErr(null)
    try {
      onLogin(await api('/auth/login', { method: 'POST', body: { username: user, password: pass }, noAuth: true }))
    } catch (ex) {
      setErr(ex.message || t('login.failed')); setPass('')
    } finally { setBusy(false) }
  }
  return (
    <div className="login-bg">
      <header className="login-top"><Logo size={34} /><b>NetSO</b><span className="spacer" /><LangButton /><ThemeButton /></header>
      <div className="login-center">
        <form className="login" onSubmit={submit}>
          <div className="login-logo">
            <div className="login-mark"><Logo size={72} /></div>
            <h1>NetSO</h1>
            <small className="muted">{t('login.tagline')}</small>
            {hint && <div className="login-meta">
              <span>{t('login.version', { v: hint.version })}</span>
              {hint.default_account && <span>{t('login.defaultAccount')} <b>admin</b> / <b>Netso</b></span>}
            </div>}
          </div>
          <label className="field"><span>{t('login.user')}</span>
            <div className="in-ico"><Ui name="user" size={16} /><input autoFocus autoComplete="username" required value={user} onChange={(e) => setUser(e.target.value)} /></div>
          </label>
          <label className="field"><span>{t('login.password')}</span>
            <div className="in-ico"><Ui name="lock" size={16} /><input type="password" autoComplete="current-password" required value={pass} onChange={(e) => setPass(e.target.value)} /></div>
          </label>
          {err && <div className="login-err">{err}</div>}
          <button className="primary wide" disabled={busy}>{busy ? t('login.signingIn') : t('login.signIn')}</button>
        </form>
      </div>
    </div>
  )
}

export function PasswordDialog({ onClose, onError }) {
  useLang()
  const [old, setOld] = useState('')
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [done, setDone] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    if (pw !== pw2) return onError(new Error(t('pw.mismatch')))
    try { await api('/auth/password', { method: 'POST', body: { old, new: pw } }); setDone(true) } catch (ex) { onError(ex) }
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <ModalHead icon="key" title={t('pw.title')} onClose={onClose} />
        {done ? <>
          <p>{t('pw.updated')}</p>
          <div className="row-end"><button type="button" className="primary" onClick={onClose}>{t('common.close')}</button></div>
        </> : <>
          <label className="field"><span>{t('pw.current')}</span><input type="password" autoFocus required autoComplete="current-password" value={old} onChange={(e) => setOld(e.target.value)} /></label>
          <label className="field"><span>{t('pw.new')}</span><input type="password" required minLength={4} autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
          <label className="field"><span>{t('pw.repeat')}</span><input type="password" required minLength={4} autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} /></label>
          <div className="row-end">
            <button type="button" className="ghost" onClick={onClose}>{t('common.cancel')}</button>
            <button className="primary">{t('common.save')}</button>
          </div>
        </>}
      </form>
    </div>
  )
}
