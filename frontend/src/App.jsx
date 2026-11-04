import { useCallback, useEffect, useState } from 'react'
import { api, AuthError, setToken } from './api.js'
import Home from './Home.jsx'
import LabView from './LabView.jsx'
import Login, { PasswordDialog } from './Login.jsx'
import { WiresharkPage } from './Wireshark.jsx'
import { ConsolePage } from './Console.jsx'
import { AgentFab } from './Agents.jsx'

export default function App() {
  const [me, setMe] = useState(null) // null: checking, false: login needed, {user}
  const [labs, setLabs] = useState([])
  const [folders, setFolders] = useState([])
  const [templates, setTemplates] = useState(null)
  const [info, setInfo] = useState(null)
  const [current, setCurrent] = useState(() => decodeURIComponent(location.hash.slice(2)) || null)
  const [error, setError] = useState(null)
  const [pwDialog, setPwDialog] = useState(false)

  const onError = useCallback((e) => {
    if (e instanceof AuthError) setMe(false)
    else setError(e.message || String(e))
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [l, f, t, i] = await Promise.all([api('/labs'), api('/folders'), api('/templates'), api('/info')])
      setLabs(l); setFolders(f); setTemplates(t); setInfo(i)
    } catch (e) { onError(e) }
  }, [onError])

  useEffect(() => {
    api('/auth/me').then(setMe).catch((e) => (e instanceof AuthError ? setMe(false) : onError(e)))
  }, [onError])
  useEffect(() => { if (me) refresh() }, [me, refresh])
  useEffect(() => { location.hash = current ? `#/${/^(wireshark|console)\//.test(current) ? current : encodeURIComponent(current)}` : '' }, [current])
  useEffect(() => {
    const onHash = () => setCurrent(decodeURIComponent(location.hash.slice(2)) || null)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  useEffect(() => { if (error) { const id = setTimeout(() => setError(null), 9000); return () => clearTimeout(id) } }, [error])

  const logout = async () => {
    try { await api('/auth/logout', { method: 'POST' }) } catch { /* ignore */ }
    setToken(''); setMe(false)
  }

  let page
  if (me === null) page = <div className="boot"><span className="spin" /></div>
  else if (me === false) page = <Login onLogin={(r) => { setToken(r.token); setMe({ user: r.user, role: r.role, default_password: r.default_password }) }} />
  else if (current?.startsWith('wireshark/')) {
    // stand-alone Wireshark tab opened from a lab: #/wireshark/<lab>/<session>
    const [, lab, sid] = current.split('/')
    page = <WiresharkPage lab={lab} sid={sid} />
  } else if (current?.startsWith('console/')) {
    // stand-alone console window (VNC, RDP, SPICE): #/console/<lab>/<node>
    const [, lab, node] = current.split('/')
    page = <ConsolePage lab={decodeURIComponent(lab)} node={decodeURIComponent(node)} />
  } else if (current) {
    page = templates
      ? <LabView key={current} name={current} templates={templates} onError={onError} onReloadTemplates={() => api('/templates').then(setTemplates).catch(onError)} onBack={() => { setCurrent(null); refresh() }} />
      : <div className="boot"><span className="spin" /></div>
  } else {
    page = <Home labs={labs} folders={folders} info={info} templates={templates || []} me={me} onOpen={setCurrent}
      onRefresh={refresh} onError={onError} onLogout={logout} onPassword={() => setPwDialog(true)} />
  }

  return (
    <div className="app">
      {page}
      {me && me.user && !/^(wireshark|console)\//.test(current || '') && <AgentFab lab={current || null} />}
      {pwDialog && <PasswordDialog onClose={() => setPwDialog(false)} onError={onError} />}
      {error && <div className="toast" onClick={() => setError(null)}>{error}</div>}
    </div>
  )
}
