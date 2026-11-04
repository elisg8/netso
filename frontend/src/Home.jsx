// Dashboard, PNETLab-like: top navigation, Workspace file manager (list + toolbar) and a large lab preview.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, splitEp } from './api.js'
import Icon, { DEVICE_COLORS, Ui, setIconStyle, useIconStyle } from './icons.jsx'
import { ContextMenu, ModalHead, StatusDialog, ThemeButton, linkDash, linkGeometry, portPoint, portState } from './LabView.jsx'
import { Logo } from './Login.jsx'
import { EXPORT_FORMATS, ImportDialog, downloadExport } from './Interop.jsx'
import CatalogDialog, { IconsDialog } from './Catalog.jsx'
import { GenerateLabDialog } from './Automation.jsx'
import { AgentsSettingsDialog } from './Agents.jsx'

const fmtStatus = { running: 'avviato', stopped: 'fermo', error: 'errore', deploying: 'avvio…' }
const FOLDER_RE = '[A-Za-z0-9][A-Za-z0-9 _.\\-]{0,39}'
const LAB_RE = '[A-Za-z][A-Za-z0-9 _\\-]{0,47}'
const DRAG = 'application/x-netso-items'

const parentOf = (p) => p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : ''
const baseOf = (p) => p.slice(p.lastIndexOf('/') + 1)
const inside = (p, f) => p === f || p.startsWith(`${f}/`)
const key = (r) => `${r.kind}:${r.name}`
const fromKey = (k) => ({ kind: k.slice(0, k.indexOf(':')), name: k.slice(k.indexOf(':') + 1) })
const fmtDate = (t) => t ? new Date(t * 1000).toLocaleString('it-IT', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''

const remember = (k, v) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }
const recall = (k) => { try { return localStorage.getItem(k) || '' } catch { return '' } }

export default function Home({ labs, folders, info, templates, me, onOpen, onRefresh, onError, onLogout, onPassword }) {
  const [path, setPath] = useState(() => recall('netso.folder'))
  const [view, setView] = useState('files') // files | running
  const [sel, setSel] = useState(null) // row shown in the preview
  const [checked, setChecked] = useState(() => new Set())
  const [query, setQuery] = useState('')
  const [menu, setMenu] = useState(null)
  const [dialog, setDialog] = useState(null)
  const [dropAt, setDropAt] = useState(null)
  const [grid, setGrid] = useState(() => recall('netso.ws') !== 'list')
  const setGridPref = (g) => { setGrid(g); try { localStorage.setItem('netso.ws', g ? 'grid' : 'list') } catch { /* ignore */ } }
  const iconStyle = useIconStyle()
  const isAdmin = !me?.role || me.role === 'admin'
  const canCreate = me?.role !== 'viewer'

  // a folder may disappear (renamed elsewhere): fall back to the closest existing parent
  useEffect(() => {
    if (!info) return // folders not loaded yet
    let p = path
    while (p && !folders.includes(p)) p = parentOf(p)
    if (p !== path) setPath(p)
  }, [folders, path, info])
  useEffect(() => { remember('netso.folder', path) }, [path])

  // a double-click that enters a folder must not also open whatever lands under the pointer
  const navAt = useRef(0)
  const go = (p) => { setPath(p); setView('files'); setSel(null); setChecked(new Set()); setQuery(''); navAt.current = Date.now() }
  const openLab = (name) => { if (Date.now() - navAt.current > 600) onOpen(name) }
  const labCount = (f) => labs.filter((l) => inside(l.folder, f)).length
  const running = labs.filter((l) => l.running > 0 || l.status === 'running')

  const q = query.trim().toLowerCase()
  const rows = useMemo(() => {
    const labRow = (l) => ({ kind: 'lab', name: l.name, lab: l })
    if (view === 'running') return running.filter((l) => !q || l.name.toLowerCase().includes(q)).map(labRow)
    if (q) {
      return [
        ...folders.filter((f) => baseOf(f).toLowerCase().includes(q)).map((f) => ({ kind: 'folder', name: f })),
        ...labs.filter((l) => `${l.name} ${l.description}`.toLowerCase().includes(q)).map(labRow),
      ]
    }
    return [
      ...folders.filter((f) => parentOf(f) === path).map((f) => ({ kind: 'folder', name: f })),
      ...labs.filter((l) => l.folder === path).map(labRow),
    ]
  }, [q, folders, labs, path, view, running])

  // drop checks that no longer exist
  useEffect(() => {
    const ok = new Set(rows.map(key))
    setChecked((c) => { const n = new Set([...c].filter((k) => ok.has(k))); return n.size === c.size ? c : n })
  }, [rows])

  const selLab = sel?.kind === 'lab' ? labs.find((l) => l.name === sel.name) : null
  // toolbar targets: the ticked rows, otherwise the selected one
  const targets = checked.size ? [...checked].map(fromKey) : sel ? [sel] : []
  const one = targets.length === 1 ? targets[0] : null

  // ------------------------------------------------------------ actions
  const run = async (fn) => { try { await fn() } catch (e) { onError(e) } finally { await onRefresh() } }
  const moveItems = (items, dest) => run(async () => {
    for (const it of items) {
      if (it.kind === 'lab') {
        if (labs.find((l) => l.name === it.name)?.folder !== dest) await api(`/labs/${encodeURIComponent(it.name)}/move`, { method: 'POST', body: { folder: dest } })
      } else {
        const target = dest ? `${dest}/${baseOf(it.name)}` : baseOf(it.name)
        if (target === it.name) continue
        if (inside(dest, it.name)) throw new Error(`Non puoi spostare "${baseOf(it.name)}" dentro se stessa`)
        await api('/folders', { method: 'PUT', body: { path: it.name, new: target } })
        if (inside(path, it.name)) setPath(target + path.slice(it.name.length))
      }
    }
    setChecked(new Set())
  })
  const deleteItems = (items) => {
    const nl = items.filter((i) => i.kind === 'lab').length, nf = items.length - nl
    const what = [nl && `${nl} lab`, nf && `${nf} cartell${nf > 1 ? 'e' : 'a'}`].filter(Boolean).join(' e ')
    if (!confirm(items.length === 1 ? `Eliminare "${baseOf(items[0].name)}"${nl ? ' con tutti i dischi dei nodi' : ''}?` : `Eliminare ${what}?`)) return
    run(async () => {
      for (const it of items.filter((i) => i.kind === 'lab')) await api(`/labs/${encodeURIComponent(it.name)}`, { method: 'DELETE' })
      // deepest folders first
      for (const it of items.filter((i) => i.kind === 'folder').sort((a, b) => b.name.length - a.name.length)) {
        await api(`/folders?path=${encodeURIComponent(it.name)}`, { method: 'DELETE' })
        if (inside(path, it.name)) setPath(parentOf(it.name))
      }
      setSel(null); setChecked(new Set())
    })
  }
  const stopLabs = (names) => run(async () => {
    // fully power the lab off: stop every node and remove its networks
    for (const n of names) await api(`/labs/${encodeURIComponent(n)}/destroy`, { method: 'POST' })
    setChecked(new Set())
  })
  const exportLabs = (items, format = 'netso') => {
    items.filter((i) => i.kind === 'lab').forEach((it, i) => setTimeout(() => downloadExport(it.name, format), i * 300))
  }
  const exportItems = (items) => [
    { head: 'Esporta' },
    ...EXPORT_FORMATS.map((f) => ({ icon: f.icon, label: f.label, disabled: !items.some((i) => i.kind === 'lab'), onClick: () => exportLabs(items, f.id) })),
  ]
  const editItem = (it) => it.kind === 'folder'
    ? setDialog({ kind: 'rename', folder: it.name })
    : setDialog({ kind: 'meta', lab: labs.find((l) => l.name === it.name) })

  // ------------------------------------------------------------ drag & drop
  // Custom pointer-based drag-and-drop (HTML5 DnD was unreliable): full control, no browser ghost/glitch.
  const [drag, setDrag] = useState(null)   // { items, x, y } while actually dragging
  const dragInfo = useRef(null)            // { x0, y0, items, started } between pointerdown and pointerup
  const clickBlock = useRef(false)         // ignore the click that follows a real drag
  const hoverDest = useRef(null)           // destination currently hovered during a drag (set by the targets)
  const dragHandle = (r) => ({
    onMouseDown: (e) => {
      if (e.button !== 0 || e.target.closest('.sw, input, button, a, .row-more')) return
      dragInfo.current = { x0: e.clientX, y0: e.clientY, items: checked.has(key(r)) ? [...checked] : [key(r)], started: false }
    },
  })
  // props for every drop target: track hover so the drop works without elementFromPoint/coordinate math
  const dropTarget = (dest) => ({
    onMouseEnter: () => { if (dragInfo.current) { hoverDest.current = dest; setDropAt(dest) } },
    onMouseLeave: () => { if (hoverDest.current === dest) { hoverDest.current = null; setDropAt(null) } },
  })
  useEffect(() => {
    const move = (e) => {
      const di = dragInfo.current
      if (!di) return
      if (!di.started && Math.hypot(e.clientX - di.x0, e.clientY - di.y0) < 6) return
      di.started = true
      setDrag({ items: di.items, x: e.clientX, y: e.clientY })
    }
    const up = () => {
      const di = dragInfo.current
      dragInfo.current = null
      if (di && di.started) {
        clickBlock.current = true
        const dest = hoverDest.current
        if (dest !== null && dest !== undefined) moveItems(di.items.map(fromKey).filter((it) => it.name !== dest), dest)
      }
      hoverDest.current = null
      setDrag(null); setDropAt(null)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
  })  // no deps: re-bind each render so checked/path/moveItems stay fresh

  // ------------------------------------------------------------ menus
  const newItems = [
    { icon: 'folderPlus', label: 'Nuova cartella', onClick: () => setDialog({ kind: 'folder', parent: path }) },
    { icon: 'plus', label: 'Nuovo lab', onClick: () => setDialog({ kind: 'new' }) },
    { icon: 'links', label: 'Genera lab (topologia pronta)…', onClick: () => setDialog({ kind: 'generate' }) },
    { icon: 'upload', label: 'Importa lab (containerlab, CML, GNS3, EVE)…', onClick: () => setDialog({ kind: 'import' }) },
    { sep: true },
    { icon: 'refresh', label: 'Aggiorna', onClick: onRefresh },
  ]
  const rowMenu = (e, r) => {
    e.preventDefault(); e.stopPropagation()
    const multi = checked.has(key(r)) && checked.size > 1
    if (!multi) setSel(r)
    const items = multi ? [...checked].map(fromKey) : [r]
    const lab = r.kind === 'lab' ? r.lab : null
    setMenu({
      x: e.clientX, y: e.clientY, icon: multi ? 'select' : lab ? 'links' : 'folder',
      title: multi ? `${items.length} elementi` : baseOf(r.name),
      items: multi ? [
        items.some((i) => i.kind === 'lab') && { icon: 'stop', label: 'Ferma i lab', onClick: () => stopLabs(items.filter((i) => i.kind === 'lab').map((i) => i.name)) },
        { icon: 'move', label: 'Sposta in…', onClick: () => setDialog({ kind: 'move', items }) },
        ...exportItems(items),
        { sep: true },
        { icon: 'trash', label: 'Elimina', danger: true, onClick: () => deleteItems(items) },
      ] : lab ? [
        { icon: 'open', label: 'Apri lab', onClick: () => onOpen(lab.name) },
        lab.status !== 'stopped' && { icon: 'stop', label: 'Ferma lab', onClick: () => stopLabs([lab.name]) },
        { icon: 'edit', label: 'Modifica dettagli', onClick: () => editItem(r) },
        lab.access === 'owner' && { icon: 'user', label: 'Condividi…', onClick: () => setDialog({ kind: 'share', lab }) },
        { icon: 'move', label: 'Sposta in…', onClick: () => setDialog({ kind: 'move', items }) },
        ...exportItems(items),
        { sep: true },
        { icon: 'trash', label: 'Elimina lab', danger: true, disabled: lab.running > 0 || lab.access !== 'owner', onClick: () => deleteItems(items) },
      ] : [
        { icon: 'folder', label: 'Apri', onClick: () => go(r.name) },
        { icon: 'folderPlus', label: 'Nuova sottocartella', onClick: () => setDialog({ kind: 'folder', parent: r.name }) },
        { icon: 'edit', label: 'Rinomina', onClick: () => editItem(r) },
        { icon: 'move', label: 'Sposta in…', onClick: () => setDialog({ kind: 'move', items }) },
        { sep: true },
        { icon: 'trash', label: 'Elimina cartella', danger: true, disabled: labCount(r.name) > 0, onClick: () => deleteItems(items) },
      ],
    })
  }
  const dropdown = (e, title, icon, items, alignRight) => {
    const r = e.currentTarget.getBoundingClientRect()
    setMenu({ x: alignRight ? r.right - 240 : r.left, y: r.bottom + 6, title, icon, items })
  }

  useEffect(() => {
    const k = (e) => {
      if (dialog || menu || e.target.closest('input, textarea')) return
      if (e.key === 'Enter' && sel) sel.kind === 'folder' ? go(sel.name) : onOpen(sel.name)
      if (e.key === 'Backspace' && path && view === 'files') go(parentOf(path))
      if (e.key === 'Delete' && targets.length) deleteItems(targets)
    }
    addEventListener('keydown', k)
    return () => removeEventListener('keydown', k)
  })

  const toggle = (r) => setChecked((c) => { const n = new Set(c); n.has(key(r)) ? n.delete(key(r)) : n.add(key(r)); return n })
  const allOn = rows.length > 0 && rows.every((r) => checked.has(key(r)))
  const crumbs = path ? path.split('/').map((seg, i, a) => ({ name: seg, path: a.slice(0, i + 1).join('/') })) : []
  const blockedDelete = targets.some((t) => t.kind === 'lab' ? labs.find((l) => l.name === t.name)?.running > 0 : labCount(t.name) > 0)

  return (
    <div className="dash">
      {/* ------------------------------------------------ top navigation */}
      <header className="topnav">
        <button className="tn-brand" onClick={() => go('')}><Logo size={30} /><b>NetSO</b></button>
        <nav className="tn-links">
          <button className={view === 'files' ? 'on' : ''} onClick={() => go(path)}><Ui name="home" size={17} /> Workspace</button>
          <button className={view === 'running' ? 'on' : ''} onClick={() => { setView('running'); setSel(null); setChecked(new Set()) }}>
            <Ui name="play" size={15} /> Lab avviati <span className={`badge ${running.length ? 'hot' : ''}`}>{running.length}</span>
          </button>
          <button onClick={() => setDialog({ kind: 'devices' })}><Ui name="nodes" size={16} /> Dispositivi</button>
          <button onClick={() => setDialog({ kind: 'users' })}><Ui name="user" size={16} /> Utenti</button>
          <button onClick={(e) => dropdown(e, 'Sistema', 'status', [
            { icon: 'status', label: 'Stato del server', onClick: () => setDialog({ kind: 'status' }) },
            { icon: 'links', label: 'Server collegati…', onClick: () => setDialog({ kind: 'servers' }) },
            { icon: 'save', label: 'Immagini', onClick: () => setDialog({ kind: 'devices', tab: 'images' }) },
            me?.role === 'admin' && { icon: 'nerd', label: 'NetSO Assistant (provider AI)…', onClick: () => setDialog({ kind: 'agents' }) },
            { head: 'Icone dei dispositivi' },
            { icon: iconStyle === 'modern' ? 'select' : 'more', label: 'Moderne', onClick: () => setIconStyle('modern') },
            { icon: iconStyle === 'classic' ? 'select' : 'more', label: 'Classiche 3D', onClick: () => setIconStyle('classic') },
            { icon: iconStyle === 'line' ? 'select' : 'more', label: 'NetSO Line', onClick: () => setIconStyle('line') },
            { icon: iconStyle === 'rack' ? 'select' : 'more', label: 'NetSO Rack', onClick: () => setIconStyle('rack') },
            me?.role === 'admin' && { icon: 'upload', label: 'Icone personalizzate…', onClick: () => setDialog({ kind: 'icons' }) },
            { sep: true },
            { icon: 'refresh', label: 'Aggiorna tutto', onClick: onRefresh },
          ])}><Ui name="config" size={16} /> Sistema <Ui name="chev" size={12} /></button>
        </nav>
        <div className="spacer" />
        {info && <HostChips info={info} onClick={() => setDialog({ kind: 'status' })} />}
        <ThemeButton />
        {me && (
          <button className="user-btn" onClick={(e) => dropdown(e, me.user, 'user', [
            { icon: 'key', label: 'Cambia password', onClick: onPassword },
            { icon: 'user', label: 'Gestione utenti', onClick: () => setDialog({ kind: 'users' }) },
            { sep: true },
            { icon: 'exit', label: 'Esci', onClick: onLogout },
          ], true)}>
            <span className="avatar">{me.user.slice(0, 1).toUpperCase()}</span>{me.user}<Ui name="chev" size={12} />
          </button>
        )}
      </header>

      {/* ------------------------------------------------ workspace header */}
      <div className="ws-title">
        <h2><Ui name={view === 'running' ? 'play' : 'links'} size={20} /> {view === 'running' ? 'Lab avviati' : 'Workspace'}</h2>
        {view === 'files' && (
          <nav className="crumbs">
            <button className={`crumb ${!path ? 'cur' : ''}`} onClick={() => go('')}><Ui name="folder" size={15} /> root</button>
            {crumbs.map((c) => (
              <span key={c.path} className="crumb-wrap">
                <span className="crumb-sep">/</span>
                <button className={`crumb ${c.path === path ? 'cur' : ''}`} onClick={() => go(c.path)}>
                  <Ui name="folder" size={15} /> {c.name}
                </button>
              </span>
            ))}
          </nav>
        )}
      </div>

      <div className="ws-body">
        {/* ---------------------------------------------- file list */}
        <section className="ws-files">
          <label className="search">
            <Ui name="search" size={16} />
            <input placeholder={view === 'running' ? 'Cerca tra i lab avviati' : 'Cerca lab e cartelle'} value={query} onChange={(e) => setQuery(e.target.value)} />
            {query && <button className="icon-btn" onClick={() => setQuery('')} title="Pulisci"><Ui name="close" size={14} /></button>}
          </label>

          <div className="ws-tools">
            <label className="tb-check" title="Seleziona tutto">
              <input type="checkbox" checked={allOn} disabled={!rows.length}
                onChange={() => setChecked(allOn ? new Set() : new Set(rows.map(key)))} />
            </label>
            <span className="tb-sep" />
            <Tool icon="folderPlus" tip="Nuova cartella" disabled={view !== 'files' || !canCreate} onClick={() => setDialog({ kind: 'folder', parent: path })} />
            <Tool icon="plus" tip="Nuovo lab" disabled={view !== 'files' || !canCreate} onClick={() => setDialog({ kind: 'new' })} />
            <Tool icon="links" tip="Genera lab con una topologia pronta (CLOS, anello, stella…)" disabled={view !== 'files' || !canCreate} onClick={() => setDialog({ kind: 'generate' })} />
            <Tool icon="upload" tip="Importa lab da containerlab, CML, GNS3, EVE-NG/PNETLab o NetSO" disabled={view !== 'files' || !canCreate} onClick={() => setDialog({ kind: 'import' })} />
            <span className="tb-sep" />
            <Tool icon="edit" tip={one?.kind === 'folder' ? 'Rinomina cartella' : 'Modifica dettagli lab'} disabled={!one} onClick={() => editItem(one)} />
            <Tool icon="move" tip="Sposta in…" disabled={!targets.length} onClick={() => setDialog({ kind: 'move', items: targets })} />
            <Tool icon="download" tip="Esporta (NetSO, containerlab, CML)" disabled={!targets.some((t) => t.kind === 'lab')}
              onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setMenu({ x: r.left, y: r.bottom + 6, title: 'Esporta', icon: 'download', items: exportItems(targets).slice(1) }) }} />
            <Tool icon="trash" tip={blockedDelete ? 'Ferma i lab / svuota le cartelle prima di eliminarli' : 'Elimina'} danger
              disabled={!targets.length || blockedDelete} onClick={() => deleteItems(targets)} />
            <span className="tb-sep" />
            <Tool icon="refresh" tip="Aggiorna" onClick={onRefresh} />
            <span className="tb-sep" />
            <Tool icon="nodes" tip="Vista a griglia" on={grid} onClick={() => setGridPref(true)} />
            <Tool icon="config" tip="Vista a lista" on={!grid} onClick={() => setGridPref(false)} />
            <div className="spacer" />
            {checked.size > 0 && <span className="tb-count">{checked.size} selezionat{checked.size > 1 ? 'i' : 'o'} <button className="ghost mini" onClick={() => setChecked(new Set())}>annulla</button></span>}
          </div>

          <div className={grid ? 'fm-grid' : 'fm-list'} onClick={() => setSel(null)}
            onContextMenu={(e) => { e.preventDefault(); if (view === 'files') setMenu({ x: e.clientX, y: e.clientY, title: path ? baseOf(path) : 'root', icon: 'folder', items: newItems }) }}>
            {view === 'files' && path && !q && (grid
              ? <div className={`card up folder ${dropAt === parentOf(path) ? 'drop' : ''}`} onClick={(e) => { e.stopPropagation(); go(parentOf(path)) }} {...dropTarget(parentOf(path))}>
                <span className="card-ico folder"><Ui name="folder" size={28} /></span><b className="card-name">..</b><small className="card-meta">su</small></div>
              : <div className={`fm-row up ${dropAt === parentOf(path) ? 'drop' : ''}`}
                onClick={(e) => { e.stopPropagation(); go(parentOf(path)) }} {...dropTarget(parentOf(path))}>
                <span />
                <span className="fm-name"><span className="fm-ico folder"><Ui name="folder" size={16} /></span><b>..</b></span>
                <span /><span />
              </div>
            )}
            {rows.map((r) => {
              const on = sel && key(sel) === key(r)
              const ticked = checked.has(key(r))
              const common = {
                ...dragHandle(r), onContextMenu: (e) => rowMenu(e, r),
                onClick: (e) => { e.stopPropagation(); if (clickBlock.current) { clickBlock.current = false; return } setSel(r) },
              }
              if (grid) {
                return r.kind === 'folder' ? (
                  <div key={key(r)} className={`card folder ${on ? 'on' : ''} ${ticked ? 'ticked' : ''} ${dropAt === r.name ? 'drop' : ''}`} {...common}
                    {...dropTarget(r.name)} onDoubleClick={() => go(r.name)}>
                    <div className="card-head"><Check on={ticked} onChange={() => toggle(r)} /><RowMore onClick={(e) => rowMenu(e, r)} /></div>
                    <span className="card-ico folder"><Ui name="folder" size={28} /></span>
                    <b className="card-name">{baseOf(r.name)}</b>
                    <small className="card-meta">{labCount(r.name)} lab</small>
                  </div>
                ) : (
                  <div key={key(r)} className={`card lab ${r.lab.status} ${on ? 'on' : ''} ${ticked ? 'ticked' : ''}`} {...common} onDoubleClick={() => openLab(r.name)}>
                    <div className="card-head">
                      <Check on={ticked} onChange={() => toggle(r)} />
                      {r.lab.status !== 'stopped' && <span className={`pill ${r.lab.status}`}>{fmtStatus[r.lab.status] || r.lab.status}</span>}
                      <RowMore onClick={(e) => rowMenu(e, r)} />
                    </div>
                    <div className={`card-thumb ${r.lab.status}`} draggable={false}><MiniTopo name={r.name} ver={`${r.lab.modified}-${r.lab.status}-${r.lab.running}`} templates={templates} /></div>
                    <b className="card-name">{r.name}</b>
                    <small className="card-meta">{r.lab.nodes} nodi{r.lab.running ? ` · ${r.lab.running} attivi` : ''}</small>
                    <small className="card-date">{fmtDate(r.lab.modified)}</small>
                  </div>
                )
              }
              return r.kind === 'folder' ? (
                <div key={key(r)} className={`fm-row ${on ? 'on' : ''} ${ticked ? 'ticked' : ''} ${dropAt === r.name ? 'drop' : ''}`} {...common}
                  {...dropTarget(r.name)} onDoubleClick={() => go(r.name)}>
                  <Check on={ticked} onChange={() => toggle(r)} />
                  <span className="fm-name">
                    <span className="fm-ico folder"><Ui name="folder" size={16} /></span>
                    <span className="fm-txt"><b>{baseOf(r.name)}</b>{q && parentOf(r.name) && <small>{parentOf(r.name)}</small>}</span>
                  </span>
                  <span className="fm-meta">{labCount(r.name)} lab</span>
                  <span className="fm-end"><RowMore onClick={(e) => rowMenu(e, r)} /></span>
                </div>
              ) : (
                <div key={key(r)} className={`fm-row ${on ? 'on' : ''} ${ticked ? 'ticked' : ''}`} {...common} onDoubleClick={() => openLab(r.name)}>
                  <Check on={ticked} onChange={() => toggle(r)} />
                  <span className="fm-name">
                    <span className={`fm-ico lab ${r.lab.status}`}><Ui name="links" size={16} /></span>
                    <span className="fm-txt">
                      <b>{r.name}</b>
                      <small>{(q || view === 'running') && r.lab.folder ? `${r.lab.folder} · ` : ''}{r.lab.nodes} nodi{r.lab.running ? ` · ${r.lab.running} attivi` : ''}</small>
                    </span>
                  </span>
                  <span className="fm-meta">{fmtDate(r.lab.modified)}</span>
                  <span className="fm-end">
                    {r.lab.status !== 'stopped' && <span className={`pill ${r.lab.status}`}>{fmtStatus[r.lab.status] || r.lab.status}</span>}
                    <RowMore onClick={(e) => rowMenu(e, r)} />
                  </span>
                </div>
              )
            })}
            {rows.length === 0 && (
              <div className="fm-empty">
                <Ui name={q ? 'search' : view === 'running' ? 'play' : 'folder'} size={34} />
                <p>{q ? 'Nessun risultato.' : view === 'running' ? 'Nessun lab avviato.' : path ? 'Questa cartella è vuota.' : 'Nessun lab ancora.'}</p>
                {!q && view !== 'running' && <small className="muted">Usa i pulsanti in alto per creare una cartella o un lab.</small>}
              </div>
            )}
          </div>
        </section>

        {/* ---------------------------------------------- preview */}
        <aside className="ws-preview">
          <h3 className="pv-title"><Ui name="links" size={18} /> Anteprima lab</h3>
          {selLab ? <LabPreview key={selLab.name} lab={selLab} templates={templates} onOpen={() => onOpen(selLab.name)}
            onEdit={() => editItem({ kind: 'lab', name: selLab.name })} onDelete={() => deleteItems([{ kind: 'lab', name: selLab.name }])} onError={onError} />
            : sel?.kind === 'folder' ? <FolderPreview folder={sel.name} folders={folders} labs={labs} onOpen={() => go(sel.name)}
              onRename={() => editItem(sel)} onDelete={() => deleteItems([sel])} />
            : <><div className="topo-box empty"><span className="muted">Seleziona un lab per vederne la topologia</span></div>
              </>}
        </aside>
      </div>

      {menu && <ContextMenu {...menu} onClose={() => setMenu(null)} />}
      {dialog?.kind === 'folder' && <NameDialog title="Nuova cartella" sub={dialog.parent ? `dentro ${dialog.parent}` : 'in root'} icon="folderPlus"
        label="Nome cartella" pattern={FOLDER_RE} onClose={() => setDialog(null)}
        onSubmit={(n) => run(async () => { await api('/folders', { method: 'POST', body: { path: dialog.parent ? `${dialog.parent}/${n}` : n } }); setDialog(null) })} />}
      {dialog?.kind === 'rename' && <NameDialog title="Rinomina cartella" sub={dialog.folder} icon="edit" label="Nuovo nome" pattern={FOLDER_RE}
        initial={baseOf(dialog.folder)} onClose={() => setDialog(null)}
        onSubmit={(n) => { const f = dialog.folder; const p = parentOf(f); const t = p ? `${p}/${n}` : n
          run(async () => { await api('/folders', { method: 'PUT', body: { path: f, new: t } }); if (inside(path, f)) setPath(t + path.slice(f.length)); setDialog(null) }) }} />}
      {dialog?.kind === 'meta' && dialog.lab && <MetaDialog lab={dialog.lab} onClose={() => setDialog(null)}
        onSave={(body) => run(async () => { await api(`/labs/${encodeURIComponent(dialog.lab.name)}/meta`, { method: 'PUT', body }); setDialog(null) })} />}
      {dialog?.kind === 'move' && <MoveDialog folders={folders} items={dialog.items} labs={labs} onClose={() => setDialog(null)}
        onPick={(dest) => { setDialog(null); moveItems(dialog.items, dest) }} />}
      {dialog?.kind === 'new' && <LabDialog mode="new" folder={path} onClose={() => setDialog(null)}
        onError={onError} onCreated={(name) => { setDialog(null); onRefresh(); onOpen(name) }} />}
      {dialog?.kind === 'generate' && <GenerateLabDialog templates={templates} folder={path} onClose={() => setDialog(null)}
        onError={onError} onCreated={(name) => { setDialog(null); onRefresh(); onOpen(name) }} />}
      {dialog?.kind === 'agents' && <AgentsSettingsDialog onClose={() => setDialog(null)} onError={onError} />}
      {dialog?.kind === 'icons' && <IconsDialog onClose={() => setDialog(null)} onError={onError} />}
      {dialog?.kind === 'import' && <ImportDialog folder={path} templates={templates} onClose={() => setDialog(null)} onError={onError}
        onCreated={(name) => { setDialog(null); onRefresh(); onOpen(name) }} />}
      {dialog?.kind === 'share' && <ShareDialog lab={dialog.lab} me={me} onClose={() => setDialog(null)} onError={onError} onSaved={() => { setDialog(null); onRefresh() }} />}
      {dialog?.kind === 'servers' && <ServersDialog me={me} onClose={() => setDialog(null)} onError={onError} />}
      {dialog?.kind === 'users' && <UsersDialog me={me} onClose={() => setDialog(null)} onError={onError} />}
      {dialog?.kind === 'devices' && <CatalogDialog templates={templates} initialTab={dialog.tab} admin={isAdmin} onClose={() => setDialog(null)} onError={onError} onChanged={onRefresh} />}
      {dialog?.kind === 'status' && <StatusDialog onClose={() => setDialog(null)} onError={onError} />}
      {drag && <div className="drag-ghost" style={{ left: drag.x + 14, top: drag.y + 10 }}>
        <Ui name={drag.items.length > 1 ? 'select' : fromKey(drag.items[0]).kind === 'lab' ? 'links' : 'folder'} size={14} />
        {drag.items.length > 1 ? `${drag.items.length} elementi` : baseOf(fromKey(drag.items[0]).name)}
      </div>}
    </div>
  )
}

function Tool({ icon, tip, onClick, disabled, danger, on }) {
  return <button className={`tb-btn ${danger ? 'danger-t' : ''} ${on ? 'on' : ''}`} title={tip} aria-label={tip} disabled={disabled} onClick={onClick}><Ui name={icon} size={17} /></button>
}

function Check({ on, onChange }) {
  return (
    <label className="fm-check" onClick={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
      <input type="checkbox" checked={on} onChange={onChange} />
    </label>
  )
}

function RowMore({ onClick }) {
  return <button className="icon-btn row-more" title="Azioni" onClick={(e) => { e.stopPropagation(); onClick(e) }}><Ui name="more" size={16} /></button>
}

function HostChips({ info, onClick }) {
  const h = info.host
  const chk = (ok, label) => <span className={`chk ${ok ? 'ok' : 'ko'}`}>{ok ? '✓' : '✗'} {label}</span>
  return (
    <button className="hostchips" onClick={onClick} title="Stato del server">
      <span><b>{h.hostname}</b></span>
      <span>{h.cpus} CPU · {(h.memory_mb / 1024).toFixed(0)} GB</span>
      {chk(h.kvm, 'KVM')}{chk(h.docker, 'Docker')}
      {h.dry_run && <span className="chk ko">dry-run</span>}
    </button>
  )
}

// ------------------------------------------------------------ preview
function LabPreview({ lab, templates, onOpen, onEdit, onDelete, onError }) {
  const [data, setData] = useState(null)
  useEffect(() => {
    let live = true
    api(`/labs/${encodeURIComponent(lab.name)}`).then((d) => live && setData(d)).catch((e) => live && onError(e))
    return () => { live = false }
  }, [lab.name, lab.status, lab.running, lab.modified, onError])
  const info = [
    ['Nome', lab.name],
    ['Cartella', lab.folder || 'root'],
    ['Autore', lab.author || '—'],
    ['Proprietario', `${lab.owner || 'admin'}${lab.access && lab.access !== 'owner' ? ` · tuo accesso: ${lab.access === 'edit' ? 'modifica' : 'sola lettura'}` : ''}`],
    ['Condiviso con', Object.entries(lab.shared || {}).map(([u, l]) => `${u} (${l === 'edit' ? 'modifica' : 'vede'})`).join(', ') || '—'],
    ['Descrizione', lab.description || '—'],
    ['Nodi', `${lab.nodes}${lab.running ? ` (${lab.running} attivi)` : ''}`],
    ['Collegamenti', lab.links],
    ['Stato', fmtStatus[lab.status] || lab.status],
    ['Modificato', fmtDate(lab.modified) || '—'],
  ]
  return (
    <div className="pv">
      <TopoView lab={data?.lab} state={data?.state} templates={templates} />
      <div className="pv-actions">
        <button className="primary" onClick={onOpen}><Ui name="open" size={16} /> Apri</button>
        <button onClick={onEdit}><Ui name="edit" size={16} /> Modifica</button>
        <button className="danger" onClick={onDelete} disabled={lab.running > 0} title={lab.running ? 'Ferma il lab prima di eliminarlo' : 'Elimina lab'}><Ui name="trash" size={16} /> Elimina</button>
      </div>
      {data?.warnings?.length > 0 && <div className="pv-warn">{data.warnings[0]}</div>}
      <dl className="pv-info">
        {info.map(([k, v]) => <div key={k}><dt>{k}</dt><dd className={k === 'Stato' ? `st-${lab.status}` : ''}>{v}</dd></div>)}
      </dl>
    </div>
  )
}

// Read-only topology with zoom (wheel / buttons) and pan (drag).
function TopoView({ lab, state, templates }) {
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const box = useRef(null)
  const drag = useRef(null)
  const geo = useMemo(() => {
    if (!lab?.nodes.length) return null
    const pos = Object.fromEntries(lab.nodes.map((n) => [n.name, { x: n.x + 43, y: n.y + 32 }]))
    const xs = Object.values(pos).map((p) => p.x), ys = Object.values(pos).map((p) => p.y)
    const pad = 70
    const minX = Math.min(...xs) - pad, minY = Math.min(...ys) - pad
    const w = Math.max(Math.max(...xs) - minX + pad, 420), h = Math.max(Math.max(...ys) - minY + pad, 300)
    return { pos, cx: minX + w / 2, cy: minY + h / 2, w, h }
  }, [lab])
  useEffect(() => { // wheel zoom must be non-passive to stop the page from scrolling
    const el = box.current
    if (!el) return
    const onWheel = (e) => { e.preventDefault(); setZoom((z) => Math.min(6, Math.max(0.4, z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)))) }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [geo])
  if (!lab) return <div className="topo-box"><span className="spin" /></div>
  if (!geo) return <div className="topo-box empty"><span className="muted">Lab vuoto: aprilo per aggiungere nodi</span></div>
  const tpl = Object.fromEntries(templates.map((t) => [t.name, t]))
  const vw = geo.w / zoom, vh = geo.h / zoom
  const vx = geo.cx - vw / 2 - pan.x, vy = geo.cy - vh / 2 - pan.y
  const R = 22
  const onDown = (e) => { drag.current = { x: e.clientX, y: e.clientY, pan, scale: vw / box.current.clientWidth }; e.currentTarget.setPointerCapture(e.pointerId) }
  const onMove = (e) => {
    const d = drag.current
    if (d) setPan({ x: d.pan.x + (e.clientX - d.x) * d.scale, y: d.pan.y + (e.clientY - d.y) * d.scale })
  }
  return (
    <div className="topo-box" ref={box}>
      <svg viewBox={`${vx} ${vy} ${vw} ${vh}`} preserveAspectRatio="xMidYMid meet"
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={() => { drag.current = null }}>
        {(lab.annotations || []).filter((a) => a.type !== 'image').map((a) => {
          const fill = a.fill ? { fill: a.fill, fillOpacity: a.opacity ?? 1 } : { fill: 'none' }
          const stroke = { stroke: a.color || '#e3e7f4', strokeWidth: a.border_width || 2, strokeDasharray: a.border === 'dashed' ? '8 5' : a.border === 'dotted' ? '2 5' : undefined, strokeOpacity: a.border === 'none' ? 0 : 0.8 }
          const w = a.w ?? 160, h = a.h ?? 80
          if (a.type === 'rect') return <rect key={a.id} x={a.x} y={a.y} width={w} height={h} rx={a.radius ?? 8} {...fill} {...stroke} />
          if (a.type === 'ellipse') return <ellipse key={a.id} cx={a.x + w / 2} cy={a.y + h / 2} rx={w / 2} ry={h / 2} {...fill} {...stroke} />
          if (a.type === 'line') return <line key={a.id} x1={a.flip ? a.x + w : a.x} y1={a.y} x2={a.flip ? a.x : a.x + w} y2={a.y + h} {...stroke} strokeOpacity={1} />
          return <text key={a.id} x={a.x + 8} y={a.y + (a.font_size || 14) + 4} fontSize={a.font_size || 14} fontWeight={a.bold ? 700 : 400} fill={a.color || '#e3e7f4'}>{a.text}</text>
        })}
        {lab.links.map((l, i) => {
          const [a, b] = l.endpoints.map((ep) => geo.pos[splitEp(ep)[0]])
          if (!a || !b) return null
          const w = l.width || 2
          const g = linkGeometry(a.x, a.y, b.x, b.y, l.shape)
          return (
            <g key={i}>
              <path d={g.path} className="mt-link"
                style={{ ...(l.color ? { stroke: l.color } : {}), strokeWidth: w, strokeDasharray: linkDash(l.style, w), strokeLinecap: l.style === 'dotted' ? 'round' : undefined }} />
              {[a, b].map((p, k) => {
                const [x, y] = portPoint(p.x, p.y, g.dirs[k], R, 5)
                return <circle key={k} cx={x} cy={y} r="4" className={`mt-port st-${portState(state?.nodes?.[splitEp(l.endpoints[k])[0]]?.status)}`} />
              })}
            </g>
          )
        })}
        {lab.nodes.map((n) => {
          const p = geo.pos[n.name]
          const run = state?.nodes?.[n.name]?.status === 'running'
          return (
            <g key={n.name} transform={`translate(${p.x} ${p.y})`}>
              <rect x={-R} y={-R} width={R * 2} height={R * 2} rx={10} className={`mt-node ${run ? 'run' : ''}`} />
              <svg x={-14} y={-14} width={28} height={28} viewBox="0 0 32 32" className="mt-ico"><Icon name={tpl[n.template]?.icon || 'router'} size={32} /></svg>
              <text y={R + 16} textAnchor="middle" className="mt-label">{n.name}</text>
            </g>
          )
        })}
      </svg>
      <div className="topo-zoom">
        <button className="icon-btn" title="Ingrandisci" onClick={() => setZoom((z) => Math.min(6, z * 1.25))}><Ui name="plus" size={15} /></button>
        <button className="icon-btn" title="Riduci" onClick={() => setZoom((z) => Math.max(0.4, z / 1.25))}><Ui name="minus" size={15} /></button>
        <button className="icon-btn" title="Adatta" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }) }}><Ui name="fit" size={15} /></button>
      </div>
    </div>
  )
}

// small static topology thumbnail for lab cards; fetched once per lab version and cached
const topoCache = new Map()
function useTopo(name, ver) {
  const key = `${name}@${ver}`
  const [data, setData] = useState(() => topoCache.get(key) || null)
  useEffect(() => {
    if (topoCache.has(key)) { setData(topoCache.get(key)); return }
    let live = true
    api(`/labs/${encodeURIComponent(name)}`).then((d) => { topoCache.set(key, d); if (live) setData(d) }).catch(() => {})
    return () => { live = false }
  }, [key, name])
  return data
}

function MiniTopo({ name, ver, templates }) {
  const data = useTopo(name, ver)
  const lab = data?.lab
  const geo = useMemo(() => {
    if (!lab?.nodes?.length) return null
    const pos = Object.fromEntries(lab.nodes.map((n) => [n.name, { x: n.x + 43, y: n.y + 32 }]))
    const xs = Object.values(pos).map((p) => p.x), ys = Object.values(pos).map((p) => p.y)
    const pad = 40
    const minX = Math.min(...xs) - pad, minY = Math.min(...ys) - pad
    const w = Math.max(...xs) - minX + pad, h = Math.max(...ys) - minY + pad
    return { pos, minX, minY, w, h }
  }, [lab])
  if (!data) return <span className="spin" />
  if (!geo) return <span className="thumb-empty"><Ui name="links" size={20} /></span>
  const st = data.state?.nodes || {}
  const tpl = Object.fromEntries((templates || []).map((t) => [t.name, t]))
  const color = (name) => DEVICE_COLORS[(tpl[name]?.icon || 'router').replace(/^(line:|custom:)/, '')] || '#8b7bff'
  return (
    <svg className="thumb-svg" viewBox={`${geo.minX} ${geo.minY} ${geo.w} ${geo.h}`} preserveAspectRatio="xMidYMid meet">
      {lab.links.map((l, i) => {
        const [a, b] = l.endpoints.map((ep) => geo.pos[splitEp(ep)[0]])
        return a && b ? <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="thumb-link" /> : null
      })}
      {lab.nodes.map((n) => {
        const p = geo.pos[n.name]
        const run = st[n.name]?.status === 'running'
        return <circle key={n.name} cx={p.x} cy={p.y} r="11" fill={color(n.template)} className={`thumb-node ${run ? 'run' : ''}`} />
      })}
    </svg>
  )
}

function FolderPreview({ folder, folders, labs, onOpen, onRename, onDelete }) {
  const sub = folders.filter((f) => parentOf(f) === folder).length
  const direct = labs.filter((l) => l.folder === folder)
  const all = labs.filter((l) => inside(l.folder, folder))
  return (
    <div className="pv">
      <div className="topo-box folder">
        <Ui name="folder" size={54} />
        <b>{baseOf(folder)}</b>
        <span className="muted small">{sub} cartelle · {all.length} lab{all.some((l) => l.running) ? ` · ${all.filter((l) => l.running).length} avviati` : ''}</span>
      </div>
      <div className="pv-actions">
        <button className="primary" onClick={onOpen}><Ui name="folder" size={16} /> Apri</button>
        <button onClick={onRename}><Ui name="edit" size={16} /> Rinomina</button>
        <button className="danger" onClick={onDelete} disabled={all.length > 0} title={all.length ? 'Sposta o elimina prima i lab contenuti' : 'Elimina cartella'}><Ui name="trash" size={16} /> Elimina</button>
      </div>
      {direct.length > 0 && <ul className="pv-list">{direct.slice(0, 12).map((l) => <li key={l.name}><span className={`dot ${l.status}`} />{l.name}<span className="muted small">{l.nodes} nodi</span></li>)}</ul>}
    </div>
  )
}

// ------------------------------------------------------------ dialogs
function NameDialog({ title, sub, icon, label, pattern, initial = '', onClose, onSubmit }) {
  const [v, setV] = useState(initial)
  const ref = useRef(null)
  useEffect(() => { ref.current?.select() }, [])
  return (
    <div className="modal-bg" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); onSubmit(v.trim()) }}>
        <ModalHead icon={icon} title={title} sub={sub} onClose={onClose} />
        <label className="field"><span>{label}</span>
          <input ref={ref} autoFocus required pattern={pattern} value={v} onChange={(e) => setV(e.target.value)}
            title="Lettere, numeri, spazio, _ . - (max 40)" />
        </label>
        <div className="row-end">
          <button type="button" className="ghost" onClick={onClose}>Annulla</button>
          <button className="primary" type="submit">Salva</button>
        </div>
      </form>
    </div>
  )
}

function MetaDialog({ lab, onClose, onSave }) {
  const [description, setDescription] = useState(lab.description || '')
  const [author, setAuthor] = useState(lab.author || '')
  return (
    <div className="modal-bg" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); onSave({ description, author }) }}>
        <ModalHead icon="edit" title="Modifica lab" sub={lab.name} onClose={onClose} />
        <label className="field"><span>Nome</span><input value={lab.name} disabled title="Il nome identifica le reti del lab sul server" /></label>
        <label className="field"><span>Autore</span><input autoFocus value={author} onChange={(e) => setAuthor(e.target.value)} /></label>
        <label className="field"><span>Descrizione</span><textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <div className="row-end">
          <button type="button" className="ghost" onClick={onClose}>Annulla</button>
          <button className="primary" type="submit">Salva</button>
        </div>
      </form>
    </div>
  )
}

function MoveDialog({ folders, items, labs, onClose, onPick }) {
  const folderOf = (it) => it.kind === 'lab' ? labs.find((l) => l.name === it.name)?.folder ?? '' : parentOf(it.name)
  const from = new Set(items.map(folderOf))
  const [dest, setDest] = useState(from.size === 1 ? [...from][0] : null)
  const moving = items.filter((i) => i.kind === 'folder').map((i) => i.name)
  const choices = ['', ...folders.filter((f) => !moving.some((m) => inside(f, m)))]
  const what = items.length === 1 ? `“${baseOf(items[0].name)}”` : `${items.length} elementi`
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="move" title={`Sposta ${what}`} sub="Scegli la cartella di destinazione" onClose={onClose} />
        <div className="move-list">
          {choices.map((f) => (
            <button key={f || '/'} className={`tree-item ${dest === f ? 'on' : ''}`} style={{ paddingLeft: 10 + (f ? f.split('/').length : 0) * 16 }}
              onClick={() => setDest(f)} onDoubleClick={() => onPick(f)}>
              <Ui name="folder" size={16} /><span className="grow">{f ? baseOf(f) : 'root'}</span>
              {from.size === 1 && from.has(f) && <small>attuale</small>}
            </button>
          ))}
        </div>
        <div className="row-end">
          <button type="button" className="ghost" onClick={onClose}>Annulla</button>
          <button className="primary" disabled={dest === null || (from.size === 1 && from.has(dest))} onClick={() => onPick(dest)}>Sposta qui</button>
        </div>
      </div>
    </div>
  )
}

function LabDialog({ mode, folder, onClose, onCreated, onError }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [yaml, setYaml] = useState('name: mio-lab\ndescription: ""\nnodes: []\nlinks: []\n')
  const submit = async (e) => {
    e.preventDefault()
    try {
      const lab = await api('/labs', { method: 'POST', body: mode === 'new' ? { name, description, folder } : { yaml, folder } })
      onCreated(lab.name)
    } catch (err) { onError(err) }
  }
  const loadFile = async (e) => { const f = e.target.files[0]; if (f) setYaml(await f.text()) }
  return (
    <div className="modal-bg" onClick={onClose}>
      <form className={`modal ${mode === 'import' ? 'wide' : ''}`} onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <ModalHead icon={mode === 'new' ? 'plus' : 'upload'} title={mode === 'new' ? 'Nuovo lab' : 'Importa lab da YAML'}
          sub={`in ${folder || 'root'}`} onClose={onClose} />
        {mode === 'new' ? <>
          <label className="field"><span>Nome</span><input autoFocus required pattern={LAB_RE} value={name} onChange={(e) => setName(e.target.value)} placeholder="es. ccna-ospf"
            title="Inizia con una lettera; lettere, numeri, _ e - (max 32)" /></label>
          <label className="field"><span>Descrizione</span><input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        </> : <>
          <input type="file" accept=".yml,.yaml" onChange={loadFile} />
          <textarea className="code" value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} />
        </>}
        <div className="row-end">
          <button type="button" className="ghost" onClick={onClose}>Annulla</button>
          <button className="primary" type="submit">Crea</button>
        </div>
      </form>
    </div>
  )
}

const ROLE_LABEL = { admin: 'Amministratore', user: 'Utente', viewer: 'Sola lettura' }
const ROLE_HINT = { admin: 'tutto: utenti, dispositivi, immagini, ogni lab', user: 'crea lab e lavora sui suoi e su quelli condivisi', viewer: 'guarda solo i lab condivisi con lui' }

function UsersDialog({ me, onClose, onError }) {
  const [list, setList] = useState([])
  const [form, setForm] = useState(null) // {mode: 'new'|'reset', username, password, role}
  const admin = me?.role === 'admin'
  const load = () => api('/users').then(setList).catch(onError)
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const save = async (e) => {
    e.preventDefault()
    try {
      if (form.mode === 'new') await api('/users', { method: 'POST', body: { username: form.username, password: form.password, role: form.role } })
      else await api(`/users/${encodeURIComponent(form.username)}`, { method: 'PUT', body: { username: form.username, password: form.password } })
      setForm(null); load()
    } catch (ex) { onError(ex) }
  }
  const setRole = async (u, role) => {
    try { await api(`/users/${encodeURIComponent(u)}`, { method: 'PUT', body: { username: u, role } }); load() } catch (ex) { onError(ex) }
  }
  const remove = async (u) => {
    if (!confirm(`Eliminare l'utente "${u}"? I suoi lab passano all'amministratore.`)) return
    try { await api(`/users/${encodeURIComponent(u)}`, { method: 'DELETE' }); load() } catch (ex) { onError(ex) }
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="user" title="Utenti" sub="Chi può accedere a NetSO e cosa può fare" onClose={onClose} />
        <table className="tbl">
          <thead><tr><th>Utente</th><th>Ruolo</th><th /></tr></thead>
          <tbody>
            {list.map((u) => (
              <tr key={u.name}>
                <td><span className="avatar sm">{u.name.slice(0, 1).toUpperCase()}</span> {u.name}{u.name === me?.user && <span className="muted small"> (tu)</span>}</td>
                <td>{admin && u.name !== me?.user
                  ? <select className="cell" value={u.role} onChange={(e) => setRole(u.name, e.target.value)}>{Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                  : <span className={`role r-${u.role}`}>{ROLE_LABEL[u.role] || u.role}</span>}</td>
                <td className="row-end">
                  {(admin || u.name === me?.user) && <button className="mini" onClick={() => setForm({ mode: 'reset', username: u.name, password: '' })}><Ui name="key" size={13} /> Password</button>}
                  {admin && <button className="mini danger" disabled={u.name === me?.user} onClick={() => remove(u.name)}><Ui name="trash" size={13} /></button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <ul className="role-help">{Object.entries(ROLE_LABEL).map(([k, v]) => <li key={k}><span className={`role r-${k}`}>{v}</span> {ROLE_HINT[k]}</li>)}</ul>
        {form ? (
          <form className="user-form" onSubmit={save}>
            <input placeholder="Nome utente" required pattern="[A-Za-z][A-Za-z0-9_.\-]{0,31}" disabled={form.mode === 'reset'} autoFocus={form.mode === 'new'}
              value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
            <input type="password" placeholder={form.mode === 'new' ? 'Password' : `Nuova password per ${form.username}`} required minLength={4} autoFocus={form.mode === 'reset'}
              autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            {form.mode === 'new' && <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>}
            <button type="button" className="ghost" onClick={() => setForm(null)}>Annulla</button>
            <button className="primary">{form.mode === 'new' ? 'Crea' : 'Salva'}</button>
          </form>
        ) : admin && (
          <div className="row-end"><button className="primary" onClick={() => setForm({ mode: 'new', username: '', password: '', role: 'user' })}><Ui name="plus" size={15} /> Nuovo utente</button></div>
        )}
      </div>
    </div>
  )
}

function ShareDialog({ lab, me, onClose, onError, onSaved }) {
  const [users, setUsers] = useState(null)
  const [shared, setShared] = useState({ ...(lab.shared || {}) })
  useEffect(() => { api('/users').then(setUsers).catch(onError) }, [onError])
  const save = async () => {
    try { await api(`/labs/${encodeURIComponent(lab.name)}/share`, { method: 'PUT', body: { shared } }); onSaved() } catch (e) { onError(e) }
  }
  const others = (users || []).filter((u) => u.name !== lab.owner)
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="user" title={`Condividi «${lab.name}»`} sub={`Proprietario: ${lab.owner}${lab.owner === me?.user ? ' (tu)' : ''}`} onClose={onClose} />
        <div className="share-list">
          {users === null && <span className="spin" />}
          {others.map((u) => (
            <div key={u.name} className="share-row">
              <span className="avatar sm">{u.name.slice(0, 1).toUpperCase()}</span>
              <span className="grow"><b>{u.name}</b> <span className="muted small">{ROLE_LABEL[u.role]}</span></span>
              {u.role === 'admin' ? <span className="muted small">vede già tutto</span> : (
                <div className="seg-mini">
                  {[['', 'Nessuno'], ['view', 'Vede'], ['edit', 'Modifica']].map(([v, l]) => (
                    <button key={v} disabled={v === 'edit' && u.role === 'viewer'} className={(shared[u.name] || '') === v ? 'on' : ''}
                      onClick={() => setShared((s) => { const n = { ...s }; if (v) n[u.name] = v; else delete n[u.name]; return n })}>{l}</button>
                  ))}
                </div>
              )}
            </div>
          ))}
          {users && !others.length && <p className="muted">Non ci sono altri utenti: creali in Utenti.</p>}
        </div>
        <p className="hint">«Vede»: apre il lab in sola lettura. «Modifica»: lo cambia, lo avvia, apre le consolle. Solo il proprietario può eliminarlo e condividerlo.</p>
        <div className="row-end"><button className="ghost" onClick={onClose}>Annulla</button><button className="primary" onClick={save}>Salva</button></div>
      </div>
    </div>
  )
}

function ServersDialog({ me, onClose, onError }) {
  const [list, setList] = useState(null)
  const [status, setStatus] = useState({})
  const [form, setForm] = useState({ name: '', url: '' })
  const admin = me?.role === 'admin'
  const load = useCallback(() => api('/servers').then((l) => {
    setList(l)
    l.forEach((s) => api(`/servers/${encodeURIComponent(s.name)}/ping`).then((st) => setStatus((o) => ({ ...o, [s.name]: st }))).catch(() => {}))
  }).catch(onError), [onError])
  useEffect(() => { load() }, [load])
  const add = async (e) => {
    e.preventDefault()
    try { await api('/servers', { method: 'POST', body: form }); setForm({ name: '', url: '' }); load() } catch (ex) { onError(ex) }
  }
  const remove = async (n) => { try { await api(`/servers/${encodeURIComponent(n)}`, { method: 'DELETE' }); load() } catch (ex) { onError(ex) } }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="links" title="Server collegati" sub="Altri server NetSO: aprili da qui e collega i loro lab ai tuoi con un nodo VXLAN" onClose={onClose} />
        <div className="share-list">
          {list === null && <span className="spin" />}
          {list?.map((s) => {
            const st = status[s.name]
            return (
              <div key={s.name} className="share-row">
                <span className={`dot ${st?.online ? 'running' : st ? 'error' : ''}`} />
                <span className="grow"><b>{s.name}</b> <span className="muted small mono">{s.url}</span>
                  <small className="muted">{st ? st.online ? `online · NetSO ${st.version}` : `non raggiungibile (${st.error})` : 'controllo…'} · IP per il VXLAN: <code>{s.host}</code></small></span>
                <a className="btn ghost" href={s.url} target="_blank" rel="noreferrer"><Ui name="open" size={14} /> Apri</a>
                {admin && <button className="mini danger" onClick={() => remove(s.name)}><Ui name="trash" size={13} /></button>}
              </div>
            )
          })}
          {list?.length === 0 && <p className="muted">Nessun server ancora.</p>}
        </div>
        {admin && <form className="user-form" onSubmit={add}>
          <input required placeholder="Nome (es. server-2)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input required placeholder="http://192.168.12.50:8080" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
          <span />
          <button className="primary"><Ui name="plus" size={15} /> Aggiungi</button>
        </form>}
        <p className="hint">Per unire due lab: in entrambi aggiungi il dispositivo <b>vxlan</b>, metti l'IP dell'altro server e lo stesso VNI, e collega i dispositivi al nodo VXLAN.</p>
      </div>
    </div>
  )
}

