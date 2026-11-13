// Automation: save running configs, management inventory, topology generator and automatic layout.
import { useEffect, useState } from 'react'
import { api, apiUrl } from './api.js'
import { t, useLang } from './i18n.js'
import Icon, { Ui } from './icons.jsx'
import { ModalHead } from './LabView.jsx'

const today = () => new Date().toISOString().slice(0, 10).replace(/-/g, '')
const canSave = (tpl) => !!tpl && ((tpl.kind === 'docker' && tpl.save_cmd?.length) || (['qemu', 'dynamips', 'iol'].includes(tpl.kind) && tpl.save_cli?.length))

export async function waitJob(id, onTick) {
  for (;;) {
    const j = await api(`/jobs/${id}`)
    onTick?.(j)
    if (j.status !== 'running') return j
    await new Promise((r) => setTimeout(r, 600))
  }
}

// ------------------------------------------------------------------ save configs
export function SaveConfigsDialog({ name, lab, state, tplByName, initial, onClose, onError, onOpenConfigs, onSaved }) {
  useLang()
  const rows = lab.nodes.filter((n) => ['qemu', 'docker', 'dynamips', 'iol'].includes(tplByName[n.template]?.kind)).map((n) => {
    const tpl = tplByName[n.template], up = state?.nodes?.[n.name]?.status === 'running'
    return { name: n.name, tpl, up, ok: up && canSave(tpl), why: !up ? t('auto.off') : !canSave(tpl) ? t('auto.cantExport') : '' }
  })
  const [pick, setPick] = useState(() => new Set(rows.filter((r) => r.ok && (!initial || initial.includes(r.name))).map((r) => r.name)))
  const [dest, setDest] = useState('active') // active | new
  const [newSet, setNewSet] = useState(t('auto.savedSetDefault', { d: today() }))
  const [job, setJob] = useState(null)
  const go = async () => {
    try {
      const body = { nodes: [...pick], ...(dest === 'new' ? { set: newSet, new_set: true } : {}) }
      const r = await api(`/labs/${encodeURIComponent(name)}/save-configs`, { method: 'POST', body })
      setJob({ status: 'running', message: t('auto.starting') })
      const j = await waitJob(r.job, setJob)
      setJob(j)
      onSaved?.(j.result?.set)
    } catch (e) { onError(e); setJob(null) }
  }
  const res = job?.result?.results || {}
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="download" title={t('auto.saveConfigsTitle')} sub={t('auto.saveConfigsSub')} onClose={onClose} />
        <div className="def-list">
          {rows.map((r) => (
            <label key={r.name} className={`def-row ${pick.has(r.name) ? 'on' : ''} ${r.ok ? '' : 'dis'}`}>
              <input type="checkbox" disabled={!r.ok || job?.status === 'running'} checked={pick.has(r.name)}
                onChange={() => setPick((p) => { const n = new Set(p); n.has(r.name) ? n.delete(r.name) : n.add(r.name); return n })} />
              <Icon name={r.tpl?.icon} size={24} />
              <span className="grow"><b>{r.name}</b> <span className="muted small">{r.tpl?.name} · {r.tpl?.kind === 'docker' ? 'docker exec' : t('auto.fromSerial')}</span>
                {r.why && <small className="muted">{r.why}</small>}</span>
              {res[r.name] && <span className={`pill ${res[r.name] === 'ok' ? 'running' : 'error'}`} title={res[r.name]}>{res[r.name] === 'ok' ? t('auto.saved') : t('auto.error')}</span>}
            </label>
          ))}
          {!rows.length && <p className="muted">{t('auto.noVmOrContainer')}</p>}
        </div>
        <div className="save-dest">
          <label className="check"><input type="radio" checked={dest === 'active'} onChange={() => setDest('active')} /> {t('auto.inActiveSet')} <code>{lab.config_set || 'default'}</code> {t('auto.overwrites')}</label>
          <label className="check"><input type="radio" checked={dest === 'new'} onChange={() => setDest('new')} /> {t('auto.inNewSet')}
            <input className="mono" value={newSet} disabled={dest !== 'new'} onChange={(e) => setNewSet(e.target.value)} pattern="[A-Za-z][A-Za-z0-9_\-]{0,31}" /></label>
        </div>
        <p className="hint">{t('auto.saveConfigsHint')}</p>
        {job && <div className={`job ${job.status}`}>{job.status === 'running' ? <span className="spin" /> : <Ui name={job.status === 'done' ? 'select' : 'destroy'} size={14} />}
          <span className="grow small">{job.message}</span></div>}
        {Object.entries(res).filter(([, v]) => v !== 'ok').map(([n, v]) => <div key={n} className="warning small">{n}: {v}</div>)}
        <div className="row-end">
          {job?.status === 'done' && <button onClick={() => onOpenConfigs(job.result?.set)}><Ui name="config" size={15} /> {t('auto.openConfigs')}</button>}
          <button className="ghost" onClick={onClose}>{t('common.close')}</button>
          <button className="primary" disabled={!pick.size || job?.status === 'running'} onClick={go}><Ui name="download" size={15} /> {t('auto.saveNConfigs', { n: pick.size })}</button>
        </div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ inventory
export function InventoryDialog({ name, onClose, onError, onEditNode }) {
  useLang()
  const [rows, setRows] = useState(null)
  useEffect(() => { api(`/labs/${encodeURIComponent(name)}/inventory`).then(setRows).catch(onError) }, [name, onError])
  const dl = (format) => { const a = document.createElement('a'); a.href = apiUrl(`/labs/${encodeURIComponent(name)}/inventory`, { format }); a.click() }
  const copy = (txt) => navigator.clipboard?.writeText(txt)
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal xl" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="nodes" title={t('auto.inventoryTitle')} sub={t('auto.inventorySub')} onClose={onClose} />
        <div className="cat-bar">
          <span className="muted small grow">{t('auto.inventoryIntro')}</span>
          <button onClick={() => dl('ansible')}><Ui name="download" size={15} /> Ansible</button>
          <button onClick={() => dl('hosts')}><Ui name="download" size={15} /> /etc/hosts</button>
          <button onClick={() => dl('ssh')}><Ui name="download" size={15} /> SSH config</button>
        </div>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>{t('auto.colNode')}</th><th>{t('auto.colMgmtIp')}</th><th>{t('auto.colInterface')}</th><th>{t('auto.colConsole')}</th><th>Ansible</th><th /></tr></thead>
            <tbody>
              {rows === null && <tr><td colSpan={6}><span className="spin" /></td></tr>}
              {rows?.map((r) => (
                <tr key={r.name}>
                  <td><span className={`dot ${r.running ? 'running' : ''}`} /> <b>{r.name}</b> <span className="muted small">{r.template}</span></td>
                  <td className="mono">{r.mgmt_ip || <span className="muted">—</span>}</td>
                  <td className="mono small">{r.kind === 'docker' ? 'eth0' : r.mgmt_iface || <span className="muted">—</span>}</td>
                  <td className="mono small">{r.console ? <button className="link-btn" title={t('auto.copy')} onClick={() => copy(r.console)}>{r.console}</button> : <span className="muted">{t('auto.off')}</span>}</td>
                  <td className="small muted">{r.ansible_os || (r.kind === 'docker' ? 'docker' : '—')}</td>
                  <td className="row-acts"><button title={t('auto.editNodeMgmt')} onClick={() => onEditNode(r.name)}><Ui name="edit" size={15} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ topology generator
const PATTERNS = ['clos', 'ring', 'mesh', 'star', 'chain']

function PatternIcon({ id }) {
  const c = 'var(--accent)'
  const dot = (x, y, k) => <circle key={k} cx={x} cy={y} r="4" fill={c} />
  const ln = (a, b, k) => <line key={k} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="var(--muted)" strokeWidth="1.5" />
  let pts = [], lines = []
  if (id === 'clos') { const s = [[30, 12], [70, 12]], l = [[14, 40], [38, 40], [62, 40], [86, 40]]; pts = [...s, ...l]; lines = s.flatMap((a) => l.map((b) => [a, b])) }
  if (id === 'ring' || id === 'mesh') { pts = [0, 1, 2, 3, 4].map((i) => [50 + 20 * Math.cos(i * 1.2566 - 1.57), 27 + 20 * Math.sin(i * 1.2566 - 1.57)]); lines = id === 'ring' ? pts.map((p, i) => [p, pts[(i + 1) % 5]]) : pts.flatMap((a, i) => pts.slice(i + 1).map((b) => [a, b])) }
  if (id === 'star') { const o = [50, 27]; const r = [0, 1, 2, 3, 4].map((i) => [50 + 22 * Math.cos(i * 1.2566 - 1.57), 27 + 20 * Math.sin(i * 1.2566 - 1.57)]); pts = [o, ...r]; lines = r.map((p) => [o, p]) }
  if (id === 'chain') { pts = [[14, 27], [38, 27], [62, 27], [86, 27]]; lines = pts.slice(1).map((p, i) => [pts[i], p]) }
  return <svg viewBox="0 0 100 54" width="100" height="54">{lines.map(([a, b], i) => ln(a, b, i))}{pts.map(([x, y], i) => dot(x, y, `p${i}`))}</svg>
}

const DEFAULT_P = { spines: 2, leaves: 4, hosts: 1, superspines: 0, count: 4, spine_tpl: 'frr', leaf_tpl: 'frr', host_tpl: 'vpc', tpl: 'frr', center_tpl: 'bridge', prefix: 'r' }

function estimate(pattern, p) {
  if (pattern === 'clos') return { n: p.superspines + p.spines + p.leaves + p.leaves * p.hosts, l: p.superspines * p.spines + p.spines * p.leaves + p.leaves * p.hosts }
  if (pattern === 'mesh') return { n: p.count, l: (p.count * (p.count - 1)) / 2 }
  if (pattern === 'star') return { n: p.count + 1, l: p.count }
  if (pattern === 'ring') return { n: p.count, l: p.count }
  return { n: p.count, l: p.count - 1 }
}

// how prefixes are chosen before posting, shared by both dialogs
function genParams(pattern, p, origin = { x: 0, y: 0 }) {
  const params = { ...p, x: Math.round(origin.x), y: Math.round(origin.y) }
  if (pattern === 'ring' || pattern === 'mesh' || pattern === 'chain') params.prefix = p.prefix || 'r'
  if (pattern === 'star') params.prefix = p.prefix || 'pc'
  return params
}

// pattern chooser + per-pattern fields; `devs` are the node choices, `centers` the star centre choices
function PatternFields({ pattern, setPattern, p, set, devs, centers }) {
  useLang()
  const tplSel = (k, label, list = devs) => (
    <label className="field"><span>{label}</span>
      <select value={p[k]} onChange={set(k)}>{list.map((tpl) => <option key={tpl.name} value={tpl.name}>{tpl.name}</option>)}</select></label>
  )
  const num = (k, label, min = 0, max = 64) => <label className="field"><span>{label}</span><input type="number" min={min} max={max} value={p[k]} onChange={set(k)} /></label>
  return (
    <>
      <div className="pat-grid">
        {PATTERNS.map((id) => (
          <button key={id} type="button" className={`pat ${pattern === id ? 'on' : ''}`} onClick={() => setPattern(id)} title={t(`auto.pat.${id}.desc`)}>
            <PatternIcon id={id} /><b>{t(`auto.pat.${id}.label`)}</b>
          </button>
        ))}
      </div>
      <p className="hint">{t(`auto.pat.${pattern}.desc`)}</p>
      {pattern === 'clos' && <>
        <div className="grid3">{num('superspines', t('auto.superspine'), 0, 16)}{num('spines', t('auto.spine'), 1, 32)}{num('leaves', t('auto.leaf'), 1, 64)}</div>
        <div className="grid3">{tplSel('spine_tpl', t('auto.spineDevice'))}{tplSel('leaf_tpl', t('auto.leafDevice'))}{num('hosts', t('auto.hostsPerLeaf'), 0, 16)}</div>
        {p.hosts > 0 && <div className="grid3">{tplSel('host_tpl', t('auto.hostDevice'))}</div>}
      </>}
      {(pattern === 'ring' || pattern === 'mesh' || pattern === 'chain') && <div className="grid3">{num('count', t('auto.howManyNodes'), 2, 32)}{tplSel('tpl', t('auto.device'))}
        <label className="field"><span>{t('auto.namePrefix')}</span><input value={p.prefix} onChange={set('prefix')} /></label></div>}
      {pattern === 'star' && <div className="grid3">{tplSel('center_tpl', t('auto.center'), centers)}{num('count', t('auto.nodesAround'), 1, 32)}{tplSel('tpl', t('auto.deviceAround'))}</div>}
    </>
  )
}

export function GenerateDialog({ name, templates, origin, onClose, onError, onAdd }) {
  useLang()
  const [pattern, setPattern] = useState('clos')
  const [p, setP] = useState(DEFAULT_P)
  const set = (k) => (e) => setP((o) => ({ ...o, [k]: e.target.type === 'number' ? Number(e.target.value) : e.target.value }))
  const devs = templates.filter((tpl) => ['qemu', 'docker'].includes(tpl.kind))
  const centers = templates.filter((tpl) => tpl.kind !== 'external')
  const est = estimate(pattern, p)
  const go = async () => {
    try {
      onAdd(await api(`/labs/${encodeURIComponent(name)}/generate`, { method: 'POST', body: { pattern, params: genParams(pattern, p, origin) } }))
    } catch (e) { onError(e) }
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="links" title={t('auto.genTopoTitle')} sub={t('auto.genTopoSub')} onClose={onClose} />
        <PatternFields pattern={pattern} setPattern={setPattern} p={p} set={set} devs={devs} centers={centers} />
        <div className="row-end">
          <span className="muted small grow">{t('auto.willAddPre')} <b>{est.n}</b> {t('auto.willAddNodesAnd')} <b>{est.l}</b> {t('auto.willAddLinks')}</span>
          <button className="ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="primary" onClick={go}><Ui name="plus" size={15} /> {t('auto.addToLab')}</button>
        </div>
      </div>
    </div>
  )
}

const LVL = { 1: 'lv1', 2: 'lv2', 3: 'lv3' } // CSS class by level; the name comes from t('auto.lvl.<n>')
const lvlName = (lv) => (LVL[lv] ? t(`auto.lvl.${lv}`) : '')

// Create a brand-new lab from a ready protocol scenario (OSPF, BGP, sicurezza…), with guide, configs and IPs
export function GenerateLabDialog({ folder, onClose, onError, onCreated }) {
  useLang()
  const [scenarios, setScenarios] = useState([])
  const [scenario, setScenario] = useState('')
  const [count, setCount] = useState(4)
  const [name, setName] = useState('')
  const [routers, setRouters] = useState([]) // installed routers we can generate configs for
  const [router, setRouter] = useState('')
  const [image, setImage] = useState('')     // '' = the template's default image
  const [q, setQ] = useState('')             // argument search box
  const [catFilter, setCatFilter] = useState('')  // '' = all categories
  const [lvlFilter, setLvlFilter] = useState(0)   // 0 = all levels
  useEffect(() => { api('/scenarios').then((s) => { setScenarios(s); setScenario((v) => v || s[0]?.id || '') }).catch(() => {}) }, [])
  useEffect(() => {
    api('/templates').then((ts) => {
      const rs = ts.filter((tpl) => tpl.dialect && tpl.ready !== false)
        .map((tpl) => ({ name: tpl.name, title: tpl.title || tpl.name, dialect: tpl.dialect, image: tpl.image || '', choices: tpl.choices || [] }))
      setRouters(rs)
      setRouter((v) => v || (rs.find((r) => r.dialect === 'frr')?.name) || rs[0]?.name || '')
    }).catch(() => {})
  }, [])
  const cats = [...new Set(scenarios.map((s) => s.cat))]
  const cur = scenarios.find((s) => s.id === scenario)
  const rtpl = routers.find((r) => r.name === router)
  const rdia = rtpl?.dialect
  // images available for the chosen router (its default + the installed versions)
  const imgs = rtpl ? [...new Set([rtpl.image, ...rtpl.choices].filter(Boolean))] : []
  useEffect(() => { setImage('') }, [router]) // reset the image when the router changes
  // custom catalogue labs («I miei lab») come with their own nodes: no router/image choice
  const pickRouter = cur && !cur.custom && !cur.host
  // a Cisco IOS router can't build the FRR-only scenarios (MPLS, EVPN, IPv6, PBR, security…)
  const incompat = pickRouter && rdia === 'ios' && !cur.ios
  const go = async (e) => {
    e.preventDefault()
    try {
      onCreated((await api('/labs/generate', { method: 'POST', body: {
        name, folder, scenario,
        count: cur?.custom ? undefined : (Number(count) || undefined),
        router: pickRouter ? (router || undefined) : undefined,
        image: pickRouter ? (image || undefined) : undefined,
      } })).name)
    } catch (err) { onError(err) }
  }
  const lc = (x) => (x || '').toLowerCase()
  const filtered = scenarios.filter((s) =>
    (!catFilter || s.cat === catFilter) &&
    (!lvlFilter || s.custom || s.level === lvlFilter) &&
    (!q || lc(`${s.label} ${s.desc} ${s.cat}`).includes(lc(q))))
  // tieni la selezione (e la descrizione qui sotto) coerente coi filtri: se lo scenario scelto
  // non è più fra quelli mostrati, seleziona il primo visibile
  useEffect(() => {
    if (filtered.length && !filtered.some((s) => s.id === scenario)) setScenario(filtered[0].id)
  }, [q, catFilter, lvlFilter, scenarios]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="modal-bg" onClick={onClose}>
      <form className="modal wide gen-lab" onClick={(e) => e.stopPropagation()} onSubmit={go}>
        <ModalHead icon="links" title={t('auto.genLabTitle')} sub={t('auto.genLabSub')} onClose={onClose} />
        <label className="field"><span>{t('auto.labName')}</span><input autoFocus required pattern="[A-Za-z][A-Za-z0-9 _\-]{0,47}" value={name}
          onChange={(e) => setName(e.target.value)} placeholder={t('auto.labNamePlaceholder')} title={t('auto.labNameTitle')} /></label>

        <div className="scn-filters">
          <div className="scn-search"><Ui name="search" size={14} />
            <input placeholder={t('auto.searchTopic')} value={q} onChange={(e) => setQ(e.target.value)} />
            {q && <button type="button" className="scn-clear" title={t('auto.clear')} onClick={() => setQ('')}><Ui name="close" size={13} /></button>}
          </div>
          <div className="scn-chips">
            <button type="button" className={`scn-chip ${!catFilter ? 'on' : ''}`} onClick={() => setCatFilter('')}>{t('auto.all')}</button>
            {cats.map((c) => <button type="button" key={c} className={`scn-chip ${catFilter === c ? 'on' : ''}`} onClick={() => setCatFilter(catFilter === c ? '' : c)}>{c}</button>)}
            <span className="scn-sep" />
            {[1, 2, 3].map((lv) => <button type="button" key={lv} className={`scn-chip lvl ${LVL[lv]} ${lvlFilter === lv ? 'on' : ''}`} onClick={() => setLvlFilter(lvlFilter === lv ? 0 : lv)}>{lvlName(lv)}</button>)}
          </div>
        </div>

        <label className="field scn-pick"><span>{t('auto.topic')} <small className="muted">· {filtered.length}</small></span>
          <select value={filtered.some((s) => s.id === scenario) ? scenario : ''} onChange={(e) => setScenario(e.target.value)} required>
            {!filtered.length && <option value="" disabled>{t('auto.noTopicFound')}</option>}
            {cats.filter((c) => filtered.some((s) => s.cat === c)).map((c) => (
              <optgroup key={c} label={c}>
                {filtered.filter((s) => s.cat === c).map((s) => (
                  <option key={s.id} value={s.id}>{s.label}{s.custom ? ` · ${t('auto.readyCopy')}` : ` · ${lvlName(s.level)}`}</option>
                ))}
              </optgroup>
            ))}
          </select></label>

        <div className="grid2">
          {!cur?.custom && <label className="field"><span>{t('auto.numNodes')}</span><input type="number" min="2" max="20" value={count} onChange={(e) => setCount(e.target.value)} /></label>}
          {pickRouter && <label className="field"><span>{t('auto.routerInstalled')}</span>
            {routers.length ? (
              <select value={router} onChange={(e) => setRouter(e.target.value)}>
                {routers.map((r) => <option key={r.name} value={r.name}>{r.title}{r.dialect === 'frr' ? '' : ' · IOS'}</option>)}
              </select>
            ) : <input value={t('auto.noRouterInstalled')} disabled />}
          </label>}
          {pickRouter && imgs.length > 0 && (
            <label className="field"><span>{t('auto.routerImage')}</span>
              <select value={image} onChange={(e) => setImage(e.target.value)}>
                <option value="">{rtpl?.image || t('auto.default')} · {t('auto.default')}</option>
                {imgs.filter((i) => i !== rtpl?.image).map((i) => <option key={i} value={i}>{i}</option>)}
              </select></label>
          )}
        </div>

        {cur?.custom && <p className="hint">{cur.desc} <b>{t('auto.readyCopyFull')}</b></p>}
        {cur && !cur.custom && !incompat && <p className="hint">
          <span className={`lv-badge ${LVL[cur.level]}`}>{lvlName(cur.level)}</span> {cur.desc}
          {' '}<b>{cur.host ? t('auto.switchesAndHosts') : `Router ${routers.find((r) => r.name === router)?.title || ''}`} {t('auto.ipGuideTail')}</b>
        </p>}
        {incompat && <p className="hint warn">
          ⚠ «{cur.label}» {t('auto.incompatPre')} <b>FRR</b> {t('auto.incompatPost')}
        </p>}
        <div className="row-end">
          <span className="muted small grow">{cur ? <>{t('auto.labOf')} <b>{cur.label}</b>{cur.custom ? ` · ${t('auto.readyCopy')}` : ` · ${t('auto.nNodesRandom', { count })}`}</> : t('auto.topicsAvailable', { n: filtered.length })}</span>
          <button type="button" className="ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="primary" type="submit" disabled={!name || !scenario || incompat}><Ui name="links" size={15} /> {t('auto.genLabTitle')}</button>
        </div>
      </form>
    </div>
  )
}

// ------------------------------------------------------------------ automatic layout
export function autoLayout(lab, mode, kindOf) {
  const nodes = lab.nodes.filter((n) => !n.pinned)
  if (!nodes.length) return {}
  const names = nodes.map((n) => n.name)
  const adj = Object.fromEntries(lab.nodes.map((n) => [n.name, new Set()]))
  for (const l of lab.links) {
    const [a, b] = l.endpoints.map((e) => e.slice(0, e.indexOf(':')))
    adj[a]?.add(b); adj[b]?.add(a)
  }
  const pos = {}
  const cx = 500, cy = 360
  if (mode === 'circle') {
    const r = Math.max(200, names.length * 38)
    names.forEach((n, i) => { pos[n] = { x: cx + r * Math.cos((2 * Math.PI * i) / names.length - Math.PI / 2), y: cy + r * Math.sin((2 * Math.PI * i) / names.length - Math.PI / 2) } })
  } else if (mode === 'grid') {
    const cols = Math.ceil(Math.sqrt(names.length))
    names.forEach((n, i) => { pos[n] = { x: 120 + (i % cols) * 190, y: 100 + Math.floor(i / cols) * 170 } })
  } else { // layers: internet/externals on top, then breadth-first from the most connected devices
    const deg = (n) => adj[n]?.size || 0
    const tops = names.filter((n) => ['nat', 'external'].includes(kindOf(n)))
    const maxDeg = Math.max(...names.map(deg))
    const roots = tops.length ? tops : names.filter((n) => deg(n) === maxDeg)
    const level = {}
    const queue = roots.map((n) => { level[n] = 0; return n })
    while (queue.length) {
      const n = queue.shift()
      for (const m of adj[n] || []) if (level[m] === undefined && names.includes(m)) { level[m] = level[n] + 1; queue.push(m) }
    }
    const extra = Math.max(0, ...Object.values(level)) + 1
    for (const n of names) if (level[n] === undefined) level[n] = extra
    const rows = {}
    for (const n of names) (rows[level[n]] ||= []).push(n)
    const widest = Math.max(...Object.values(rows).map((r) => r.length))
    for (const [lv, row] of Object.entries(rows)) {
      row.sort((a, b) => [...adj[a]].join().localeCompare([...adj[b]].join()) || a.localeCompare(b))
      row.forEach((n, i) => { pos[n] = { x: 120 + ((widest - row.length) / 2 + i) * 180, y: 90 + Number(lv) * 170 } })
    }
  }
  return Object.fromEntries(Object.entries(pos).map(([k, v]) => [k, { x: Math.round(v.x), y: Math.round(v.y) }]))
}
