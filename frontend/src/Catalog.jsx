// Devices & images: template catalog (create / edit / import CML node definitions and GNS3 appliances)
// and the image manager (upload with conversion, download from URL, CML refplat ISO, checksums, Docker images).
import { useCallback, useEffect, useRef, useState } from 'react'
import { CONSOLES, NIC_MODELS, api, interfaceNames, uploadTo } from './api.js'
import Icon, { DEVICE_COLORS, LINE_ICONS, Ui, lineLabel } from './icons.jsx'
import { ModalHead } from './LabView.jsx'

const KIND_LABEL = { qemu: 'VM QEMU', dynamips: 'Dynamips', iol: 'Cisco IOL', docker: 'Container', bridge: 'Switch', external: 'Rete esterna', nat: 'NAT', vxlan: 'VXLAN' }
const fmtSize = (b) => (b == null ? '' : b < 1024 ** 2 ? `${(b / 1024).toFixed(0)} KB` : b < 1024 ** 3 ? `${(b / 1024 ** 2).toFixed(1)} MB` : `${(b / 1024 ** 3).toFixed(2)} GB`)
const IMG_RE = '[A-Za-z0-9][A-Za-z0-9._+\\-]{0,99}'

function toBase64(buf) {
  const bytes = new Uint8Array(buf)
  let s = ''
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000))
  return btoa(s)
}

// raw streamed upload with progress (the server writes the body straight to disk)
function uploadFile(folder, file, onProgress) {
  return uploadTo(`/api/images/${encodeURIComponent(folder)}/upload?filename=${encodeURIComponent(file.name)}`, file, onProgress)
}

export default function CatalogDialog({ templates, onClose, onError, onChanged, initialTab = 'devices', admin = true }) {
  const [tab, setTab] = useState(initialTab)
  const [images, setImages] = useState(null)
  const [jobs, setJobs] = useState([])
  const [uploads, setUploads] = useState([]) // client-side upload progress
  const loadImages = useCallback(() => api('/images').then(setImages).catch(onError), [onError])
  const loadJobs = useCallback(() => api('/jobs').then(setJobs).catch(() => {}), [])
  useEffect(() => { loadImages(); loadJobs() }, [loadImages, loadJobs])
  const running = jobs.some((j) => j.status === 'running')
  useEffect(() => {
    if (!running) return
    const id = setInterval(loadJobs, 1000)
    return () => clearInterval(id)
  }, [running, loadJobs])
  const wasRunning = useRef(false)
  useEffect(() => { if (wasRunning.current && !running) { loadImages(); onChanged() } wasRunning.current = running }, [running, loadImages, onChanged])

  const upload = async (folder, files, url) => {
    for (const f of files) {
      const id = `${folder}/${f.name}/${Date.now()}`
      setUploads((u) => [...u, { id, label: `${folder}/${f.name}`, progress: 0 }])
      const onProgress = (p) => setUploads((u) => u.map((x) => (x.id === id ? { ...x, progress: p } : x)))
      try {
        await (url ? uploadTo(url(f), f, onProgress) : uploadFile(folder, f, onProgress))
      } catch (e) { onError(e) }
      setUploads((u) => u.filter((x) => x.id !== id))
      loadJobs(); loadImages()
    }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal xl catalog" onClick={(e) => e.stopPropagation()}>
        <div className="cat-head">
          <div className="seg">
            <button className={tab === 'devices' ? 'on' : ''} onClick={() => setTab('devices')}>Dispositivi <span className="badge">{templates.length}</span></button>
            <button className={tab === 'images' ? 'on' : ''} onClick={() => setTab('images')}>Immagini <span className="badge">{images?.length ?? '…'}</span></button>
            <button className={tab === 'docker' ? 'on' : ''} onClick={() => setTab('docker')}>Docker</button>
          </div>
          <div className="grow" />
          <button className="icon-btn" onClick={onClose} title="Chiudi"><Ui name="close" size={16} /></button>
        </div>
        {(jobs.length > 0 || uploads.length > 0) && <JobList jobs={jobs} uploads={uploads} onDismiss={(id) => api(`/jobs/${id}`, { method: 'DELETE' }).then(loadJobs)} />}
        {tab === 'devices' && <DevicesTab templates={templates} images={images} onChanged={onChanged} onError={onError} admin={admin} />}
        {tab === 'images' && <ImagesTab images={images} templates={templates} onUpload={upload} onReload={() => { loadImages(); loadJobs() }} onError={onError} admin={admin} />}
        {tab === 'docker' && <DockerTab onUpload={upload} onJobs={loadJobs} running={running} onError={onError} admin={admin} />}
      </div>
    </div>
  )
}

function JobList({ jobs, uploads, onDismiss }) {
  return (
    <div className="jobs">
      {uploads.map((u) => (
        <div key={u.id} className="job running"><Ui name="upload" size={14} /><span className="grow">Caricamento {u.label}</span>
          <span className="bar"><i style={{ width: `${Math.round(u.progress * 100)}%` }} /></span><b>{Math.round(u.progress * 100)}%</b></div>
      ))}
      {jobs.map((j) => (
        <div key={j.id} className={`job ${j.status}`}>
          {j.status === 'running' ? <span className="spin" /> : <Ui name={j.status === 'done' ? 'select' : 'destroy'} size={14} />}
          <span className="grow"><b>{j.label}</b> <span className="muted small">{j.message}</span></span>
          {j.status === 'running' && <span className={`bar ${j.progress == null ? 'indet' : ''}`}><i style={{ width: `${Math.round((j.progress ?? 0.3) * 100)}%` }} /></span>}
          {j.status !== 'running' && <button className="icon-btn" title="Nascondi" onClick={() => onDismiss(j.id)}><Ui name="close" size={13} /></button>}
        </div>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ devices
function DevicesTab({ templates, images, onChanged, onError, admin }) {
  const [q, setQ] = useState('')
  const [edit, setEdit] = useState(null) // {tpl, isNew}
  const [importing, setImporting] = useState(false)
  const have = new Set((images || []).filter((i) => i.disks.length).map((i) => i.name))
  const f = q.trim().toLowerCase()
  const shown = templates.filter((t) => !f || `${t.name} ${t.description} ${t.vendor || ''}`.toLowerCase().includes(f))
  const remove = async (t) => {
    const restore = t.origin === 'modificato'
    if (!confirm(restore ? `Ripristinare "${t.name}" com'era in origine?` : `Eliminare il dispositivo "${t.name}"?`)) return
    try { await api(`/templates/${encodeURIComponent(t.name)}`, { method: 'DELETE' }); onChanged() } catch (e) { onError(e) }
  }
  return (
    <>
      <div className="cat-bar">
        <label className="pal-search grow"><Ui name="search" size={14} /><input placeholder="Cerca…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        {admin && <button onClick={() => setImporting(true)} title="Importa node definition CML o appliance GNS3"><Ui name="upload" size={15} /> Importa</button>}
        {admin && <button className="primary" onClick={() => setEdit({ tpl: { name: '', kind: 'qemu', icon: 'router', cpus: 1, ram: 1024, nics: 4, nic_model: 'virtio-net-pci', disk_bus: 'virtio', console: 'telnet', iface_prefix: 'eth', iface_start: 0, config_method: 'none', privileged: true }, isNew: true })}>
          <Ui name="plus" size={15} /> Nuovo</button>}
      </div>
      <div className="tbl-wrap cat-tbl">
        <table className="tbl">
          <thead><tr><th>Dispositivo</th><th>Tipo</th><th>Immagine</th><th>Risorse</th><th /><th /></tr></thead>
          <tbody>
            {shown.map((t) => {
              const ok = !['qemu', 'iol', 'dynamips'].includes(t.kind) ? true : images ? have.has(t.image) : null
              return (
                <tr key={t.name} title={[t.vendor, t.description, t.source].filter(Boolean).join(' · ')}>
                  <td><span className="dev-cell"><Icon name={t.icon} size={20} /><b>{t.name}</b>{t.origin && t.origin !== 'integrato' && <span className="muted small">{t.origin}</span>}</span></td>
                  <td className="muted">{KIND_LABEL[t.kind] || t.kind}</td>
                  <td><code className="small">{t.image || '—'}</code></td>
                  <td className="muted small">{['qemu', 'docker', 'iol', 'dynamips'].includes(t.kind) ? `${t.cpus} CPU · ${t.ram} MB · ${t.nics} int.` : `${t.nics} porte`}</td>
                  <td>{ok === null ? <span className="spin" /> : <span className={`dot-ok ${ok ? 'on' : 'off'}`} title={ok ? 'Pronto' : 'Immagine mancante'} />}</td>
                  <td className="row-acts">{admin && <>
                    <button title="Modifica" onClick={() => setEdit({ tpl: t, isNew: false })}><Ui name="edit" size={15} /></button>
                    <button title="Duplica" onClick={() => setEdit({ tpl: { ...t, name: `${t.name}-copia`.slice(0, 32), source: null, origin: undefined }, isNew: true })}><Ui name="copy" size={15} /></button>
                    {t.origin !== 'integrato' && <button className="danger" title={t.origin === 'modificato' ? 'Ripristina originale' : 'Elimina'} onClick={() => remove(t)}><Ui name={t.origin === 'modificato' ? 'refresh' : 'trash'} size={15} /></button>}
                  </>}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {edit && <TemplateEditor tpl={edit.tpl} isNew={edit.isNew} images={images} onClose={() => setEdit(null)} onError={onError}
        onSaved={() => { setEdit(null); onChanged() }} />}
      {importing && <DefImport onClose={() => setImporting(false)} onError={onError} onSaved={() => { setImporting(false); onChanged() }} />}
    </>
  )
}

const TPL_FIELDS = ['name', 'kind', 'description', 'icon', 'image', 'cpus', 'ram', 'nics', 'nic_model', 'disk_bus', 'console', 'iface_prefix', 'iface_start',
  'machine', 'cpu_model', 'extra_args', 'env', 'cmd', 'privileged', 'config_method', 'config_file', 'config_label', 'interfaces', 'image_files', 'source', 'vendor', 'shell', 'host_bridge']

function TemplateEditor({ tpl, isNew, images, onClose, onSaved, onError }) {
  const [f, setF] = useState(() => ({
    ...Object.fromEntries(TPL_FIELDS.map((k) => [k, tpl[k] ?? ''])),
    extra_args: (tpl.extra_args || []).join(' '), cmd: (tpl.cmd || []).join(' '), shell: (tpl.shell || []).join(' '),
    env: Object.entries(tpl.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'), interfaces: (tpl.interfaces || []).join('\n'),
    privileged: tpl.privileged ?? true, iface_start: tpl.iface_start ?? 0,
  }))
  const set = (k) => (e) => setF((o) => ({ ...o, [k]: e?.target ? (e.target.type === 'checkbox' ? e.target.checked : e.target.value) : e }))
  const [customIcons, setCustomIcons] = useState([])
  const loadIcons = useCallback(() => api('/icons').then(setCustomIcons).catch(() => {}), [])
  useEffect(() => { loadIcons() }, [loadIcons])
  const iconInput = useRef(null)
  const addIcon = async (file) => {
    if (!file) return
    const base = file.name.replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9 _.\-]/g, '-').slice(0, 48) || 'icona'
    try {
      const r = await uploadTo(`/api/icons/${encodeURIComponent(base)}/upload?filename=${encodeURIComponent(file.name)}`, file, () => {})
      await loadIcons()
      set('icon')(`custom:${r.name}`)
    } catch (err) { onError(err) }
  }
  const delIcon = async (n) => {
    try {
      await api(`/icons/${encodeURIComponent(n)}`, { method: 'DELETE' })
      if (f.icon === `custom:${n}`) set('icon')('router')
      loadIcons()
    } catch (err) { onError(err) }
  }
  const qemu = f.kind === 'qemu', docker = f.kind === 'docker'
  const preview = interfaceNames({ ...f, nics: Number(f.nics) || 1, iface_start: Number(f.iface_start) || 0, interfaces: f.interfaces.trim() ? f.interfaces.split('\n').map((s) => s.trim()).filter(Boolean) : null })
  const submit = async (e) => {
    e.preventDefault()
    const body = {
      ...Object.fromEntries(TPL_FIELDS.map((k) => [k, f[k] === '' ? undefined : f[k]])),
      cpus: Number(f.cpus) || 1, ram: Number(f.ram) || 512, nics: Number(f.nics) || 1, iface_start: Number(f.iface_start) || 0,
      extra_args: f.extra_args.trim() ? f.extra_args.trim().split(/\s+/) : [], cmd: f.cmd.trim() ? f.cmd.trim().split(/\s+/) : undefined,
      shell: f.shell.trim() ? f.shell.trim().split(/\s+/) : undefined,
      env: Object.fromEntries(f.env.split('\n').map((l) => l.trim()).filter((l) => l.includes('=')).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)])),
      interfaces: f.interfaces.trim() ? f.interfaces.split('\n').map((s) => s.trim()).filter(Boolean) : undefined,
      image_files: tpl.image_files || undefined, config_file: f.config_method === 'none' ? undefined : f.config_file || undefined,
    }
    try {
      await api(isNew ? '/templates' : `/templates/${encodeURIComponent(tpl.name)}`, { method: isNew ? 'POST' : 'PUT', body })
      onSaved()
    } catch (err) { onError(err) }
  }
  const imgOpts = (images || []).filter((i) => i.disks.length).map((i) => i.name)
  return (
    <div className="modal-bg sheet-bg" onClick={onClose}>
      <form className="modal sheet" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="sheet-head">
          <span className="sheet-ico"><Icon name={f.icon || 'router'} size={34} /></span>
          <div className="grow"><h3>{isNew ? 'Nuovo dispositivo' : `Modifica ${tpl.name}`}</h3>
            <small className="muted">{tpl.origin === 'integrato' ? 'Dispositivo integrato: le modifiche si salvano come tua versione, ripristinabile' : tpl.source || 'Template personale'}</small></div>
          <button type="button" className="icon-btn" onClick={onClose}><Ui name="close" size={16} /></button>
        </div>
        <div className="sheet-body">
          <fieldset><legend>Generale</legend>
            <div className="grid2">
              <label className="field"><span>Nome</span><input required pattern="[A-Za-z][A-Za-z0-9_\-]{0,31}" value={f.name} onChange={set('name')} placeholder="es. juniper-vsrx" /></label>
              <label className="field"><span>Tipo</span>
                <select value={f.kind} onChange={set('kind')} disabled={!isNew}>
                  <option value="qemu">VM QEMU/KVM</option><option value="docker">Container Docker</option>
                </select></label>
            </div>
            <div className="grid2">
              <label className="field"><span>Descrizione</span><input value={f.description} onChange={set('description')} /></label>
              <label className="field"><span>Vendor</span><input value={f.vendor} onChange={set('vendor')} placeholder="es. Juniper" /></label>
            </div>
            <div className="field"><span>Icona</span>
              <div className="icon-pick">{Object.keys(DEVICE_COLORS).map((ic) => (
                <button type="button" key={ic} className={f.icon === ic ? 'on' : ''} onClick={() => set('icon')(ic)}><Icon name={ic} size={26} /><small>{ic}</small></button>
              ))}</div>
              <span className="pick-sec">Pack NetSO Line</span>
              <div className="icon-pick">{LINE_ICONS.map((ic) => (
                <button type="button" key={ic} className={f.icon === `line:${ic}` ? 'on' : ''} title={lineLabel(ic)} onClick={() => set('icon')(`line:${ic}`)}><Icon name={`line:${ic}`} size={28} /><small>{lineLabel(ic)}</small></button>
              ))}</div>
              <span className="pick-sec">Icone personali <small className="muted">(SVG, PNG… caricate da te)</small></span>
              <div className="icon-pick">
                {customIcons.map((ic) => (
                  <button type="button" key={ic.name} className={`has-del ${f.icon === `custom:${ic.name}` ? 'on' : ''}`} title={ic.name} onClick={() => set('icon')(`custom:${ic.name}`)}>
                    <Icon name={`custom:${ic.name}`} size={28} /><small>{ic.name}</small>
                    <span className="pick-del" title="Elimina icona" onClick={(e) => { e.stopPropagation(); delIcon(ic.name) }}>×</span>
                  </button>
                ))}
                <button type="button" className="icon-add" onClick={() => iconInput.current?.click()}><Ui name="upload" size={22} /><small>Aggiungi…</small></button>
                <input ref={iconInput} type="file" hidden accept=".svg,.png,.jpg,.jpeg,.gif,.webp" onChange={(e) => { addIcon(e.target.files[0]); e.target.value = '' }} />
              </div>
            </div>
          </fieldset>
          <fieldset><legend>{qemu ? 'Macchina virtuale' : 'Container'}</legend>
            <label className="field"><span>{qemu ? 'Cartella immagine (in /opt/netso/images)' : 'Immagine Docker'}</span>
              {qemu ? <>
                <input list="img-folders" required value={f.image} onChange={set('image')} placeholder="es. vsrx-23.4" />
                <datalist id="img-folders">{imgOpts.map((i) => <option key={i} value={i} />)}</datalist>
              </> : <input required value={f.image} onChange={set('image')} placeholder="es. ghcr.io/nokia/srlinux:latest" />}
            </label>
            <div className="grid3">
              <label className="field"><span>CPU</span><input type="number" min="1" value={f.cpus} onChange={set('cpus')} /></label>
              <label className="field"><span>RAM (MB)</span><input type="number" min="64" step="64" value={f.ram} onChange={set('ram')} /></label>
              <label className="field"><span>Interfacce</span><input type="number" min="1" max="32" value={f.nics} onChange={set('nics')} /></label>
            </div>
            {qemu && <div className="grid3">
              <label className="field"><span>Modello NIC</span><select value={f.nic_model} onChange={set('nic_model')}>{NIC_MODELS.map((m) => <option key={m}>{m}</option>)}</select></label>
              <label className="field"><span>Bus disco</span><select value={f.disk_bus} onChange={set('disk_bus')}><option>virtio</option><option>ide</option><option>sata</option></select></label>
              <label className="field"><span>Consolle</span><select value={f.console} onChange={set('console')}>{CONSOLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            </div>}
            {qemu && <div className="grid2">
              <label className="field"><span>Machine</span><input value={f.machine} onChange={set('machine')} placeholder="pc" /></label>
              <label className="field"><span>Modello CPU</span><input value={f.cpu_model} onChange={set('cpu_model')} placeholder="host" /></label>
            </div>}
            {qemu && <label className="field"><span>Opzioni QEMU aggiuntive</span><input className="mono" value={f.extra_args} onChange={set('extra_args')} /></label>}
            {docker && <>
              <div className="grid2">
                <label className="field"><span>Comando di avvio</span><input className="mono" value={f.cmd} onChange={set('cmd')} placeholder="vuoto = quello dell'immagine" /></label>
                <label className="field"><span>Comando della consolle</span><input className="mono" value={f.shell} onChange={set('shell')} placeholder="es. vtysh, sr_cli, Cli (vuoto = bash)" /></label>
              </div>
              <label className="field"><span>Variabili d'ambiente (NOME=valore, una per riga)</span><textarea className="mono" rows={3} value={f.env} onChange={set('env')} /></label>
              <label className="check"><input type="checkbox" checked={f.privileged} onChange={set('privileged')} /> Container privilegiato</label>
            </>}
          </fieldset>
          <fieldset><legend>Nomi delle interfacce</legend>
            <div className="grid2">
              <label className="field"><span>Prefisso</span><input value={f.iface_prefix} onChange={set('iface_prefix')} placeholder="es. Gi0/ , ge-0/0/ , eth" /></label>
              <label className="field"><span>Primo numero</span><input type="number" min="0" value={f.iface_start} onChange={set('iface_start')} /></label>
            </div>
            <label className="field"><span>…oppure l'elenco esatto (uno per riga, vince sul prefisso)</span>
              <textarea className="mono" rows={3} value={f.interfaces} onChange={set('interfaces')} placeholder={'Management0/0\nGigabitEthernet0/0\nGigabitEthernet0/1'} /></label>
            <p className="hint ifaces">{preview.slice(0, 10).join(' · ')}{preview.length > 10 ? ' …' : ''}</p>
          </fieldset>
          <fieldset><legend>Startup config</legend>
            <div className="grid3">
              <label className="field"><span>Come arriva al nodo</span>
                <select value={f.config_method} onChange={set('config_method')}>
                  <option value="none">non supportata</option>
                  {qemu && <option value="disk">disco FAT (es. IOSv)</option>}
                  {qemu && <option value="iso">CD-ROM ISO (es. CSR, cloud-init)</option>}
                  {docker && <option value="copy">copia nel container</option>}
                </select></label>
              <label className="field"><span>{docker ? 'Percorso nel container' : 'Nome del file'}</span><input value={f.config_file} disabled={f.config_method === 'none'} onChange={set('config_file')} placeholder={docker ? '/etc/frr/frr.conf' : 'ios_config.txt'} /></label>
              {qemu && <label className="field"><span>Etichetta volume</span><input value={f.config_label} disabled={f.config_method === 'none'} onChange={set('config_label')} placeholder="config" /></label>}
            </div>
          </fieldset>
          {tpl.image_files?.length > 0 && <fieldset><legend>File immagine attesi</legend>
            <ul className="exp-files">{tpl.image_files.map((x, i) => <li key={i}><code>{x.filename}</code> <span className="muted small">{x.version}{x.md5 ? ` · md5 ${x.md5}` : ''}</span></li>)}</ul>
          </fieldset>}
        </div>
        <div className="sheet-foot">
          <div className="spacer" />
          <button type="button" className="ghost" onClick={onClose}>Annulla</button>
          <button className="primary"><Ui name="save" size={15} /> {isNew ? 'Crea dispositivo' : 'Salva'}</button>
        </div>
      </form>
    </div>
  )
}

function DefImport({ onClose, onSaved, onError }) {
  const [files, setFiles] = useState(null)
  const [res, setRes] = useState(null)
  const [pick, setPick] = useState(new Set())
  const input = useRef(null)
  const load = async (list) => {
    const fs = await Promise.all([...list].map(async (fl) => ({ filename: fl.name, data: toBase64(await fl.arrayBuffer()) })))
    setFiles(fs)
    try {
      const r = await api('/templates/import', { method: 'POST', body: { files: fs } })
      setRes(r); setPick(new Set(r.templates.map((t) => t.name)))
    } catch (e) { onError(e) }
  }
  const save = async () => {
    try { await api('/templates/import', { method: 'POST', body: { files, save: true, pick: [...pick] } }); onSaved() } catch (e) { onError(e) }
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="upload" title="Importa definizioni di dispositivi" sub="Node/image definition di Cisco CML (.yaml) · appliance GNS3 (.gns3a) · template NetSO" onClose={onClose} />
        {!res ? (
          <div className="drop" onClick={() => input.current?.click()} onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); load(e.dataTransfer.files) }}>
            <Ui name="upload" size={30} />
            <p><b>Trascina qui i file</b> (anche più insieme) oppure fai clic per sceglierli</p>
            <div className="src-chips"><span><b>CML</b> node-definitions/*.yaml</span><span><b>CML</b> virl-base-images/*/*.yaml</span><span><b>GNS3</b> appliances/*.gns3a</span></div>
            <input ref={input} type="file" multiple hidden accept=".yaml,.yml,.gns3a,.json" onChange={(e) => load(e.target.files)} />
          </div>
        ) : <>
          <div className="def-list">
            {res.templates.map((t) => (
              <label key={t.name} className={`def-row ${pick.has(t.name) ? 'on' : ''}`}>
                <input type="checkbox" checked={pick.has(t.name)} onChange={() => setPick((p) => { const n = new Set(p); n.has(t.name) ? n.delete(t.name) : n.add(t.name); return n })} />
                <Icon name={t.icon} size={28} />
                <span className="grow"><b>{t.name}</b> <span className="muted small">{t.source} · {KIND_LABEL[t.kind]} · {t.ram} MB · {t.nics} int.</span>
                  <small className="mono muted">{t.iface_names.slice(0, 4).join(', ')}{t.iface_names.length > 4 ? '…' : ''} · immagine <b>{t.image}</b></small></span>
              </label>
            ))}
            {!res.templates.length && <p className="muted">Nessun dispositivo trovato nei file.</p>}
          </div>
          {res.warnings.length > 0 && <details className="imp-warn" open><summary>{res.warnings.length} note</summary><ul>{res.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details>}
          <div className="row-end">
            <button className="ghost" onClick={() => { setRes(null); setFiles(null) }}>Altri file</button>
            <button className="primary" disabled={!pick.size} onClick={save}><Ui name="plus" size={15} /> Aggiungi {pick.size} dispositivi</button>
          </div>
        </>}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ images
function ImagesTab({ images, templates, onUpload, onReload, onError, admin }) {
  const [mode, setMode] = useState(null) // 'upload' | 'url' | 'pnet' | 'iso'
  const needed = templates.filter((t) => t.kind === 'qemu' && t.image && !(images || []).some((i) => i.name === t.image && i.disks.length))
  const md5 = (name) => api(`/images/${encodeURIComponent(name)}/checksum`, { method: 'POST' }).then(onReload).catch(onError)
  const remove = async (img) => {
    if (!confirm(`Eliminare l'immagine "${img.name}" (${fmtSize(img.size)})?`)) return
    try { await api(`/images/${encodeURIComponent(img.name)}`, { method: 'DELETE' }) } catch (e) {
      if (!String(e.message).startsWith('Usata da') || !confirm(`${e.message}\n\nEliminarla comunque? I nodi che la usano non partiranno più.`)) return onError(e)
      try { await api(`/images/${encodeURIComponent(img.name)}?force=true`, { method: 'DELETE' }) } catch (e2) { return onError(e2) }
    }
    onReload()
  }
  return (
    <>
      <div className="cat-bar">
        <div className="grow" />
        {admin && <>
          <button className={mode === 'pnet' ? 'on' : ''} onClick={() => setMode(mode === 'pnet' ? null : 'pnet')}><Ui name="folder" size={15} /> PNETLab</button>
          <button className={mode === 'url' ? 'on' : ''} onClick={() => setMode(mode === 'url' ? null : 'url')}><Ui name="download" size={15} /> URL</button>
          <button className={mode === 'iso' ? 'on' : ''} onClick={() => setMode(mode === 'iso' ? null : 'iso')}><Ui name="config" size={15} /> ISO CML</button>
          <button className={mode === 'iol' ? 'on' : ''} onClick={() => setMode(mode === 'iol' ? null : 'iol')}><Ui name="lock" size={15} /> Licenza IOL</button>
          <button className="primary" onClick={() => setMode(mode === 'upload' ? null : 'upload')}><Ui name="upload" size={15} /> Carica</button>
        </>}
      </div>
      {mode === 'upload' && <UploadForm needed={needed} onUpload={(n, fs) => { setMode(null); onUpload(n, fs) }} />}
      {mode === 'url' && <UrlForm needed={needed} onDone={() => { setMode(null); onReload() }} onError={onError} />}
      {mode === 'pnet' && <PnetForm onDone={() => { setMode(null); onReload() }} onError={onError} />}
      {mode === 'iol' && <IolLicenseForm onDone={() => setMode(null)} onError={onError} />}
      {mode === 'iso' && <RefplatForm onUpload={onUpload} onDone={() => { setMode(null); onReload() }} onError={onError} />}
      <div className="tbl-wrap cat-tbl">
        <table className="tbl">
          <thead><tr><th>Immagine</th><th>File</th><th>Dimensione</th><th>Usata da</th><th /></tr></thead>
          <tbody>
            {images === null && <tr><td colSpan={5}><span className="spin" /></td></tr>}
            {images?.length === 0 && <tr><td colSpan={5} className="muted">Nessuna immagine</td></tr>}
            {images?.map((img) => (
              <tr key={img.name}>
                <td><b className="mono">{img.name}</b>{!img.disks.length && <div><span className="pill error">nessun disco</span></div>}</td>
                <td className="small">{img.files.map((f) => (
                  <div key={f.name} className="img-file"><code>{f.name}</code> <span className="muted">{fmtSize(f.size)}</span>
                    {f.verified && <span className="pill running" title={`MD5 ${f.md5}`}>✓ {f.verified.template} {f.verified.version}</span>}
                    {f.md5 && !f.verified && <span className="muted mono" title="MD5">{f.md5.slice(0, 10)}…</span>}
                    {f.partial && <span className="pill dirty">incompleto</span>}</div>
                ))}</td>
                <td className="muted small">{fmtSize(img.size)}</td>
                <td className="small muted" title={img.used_by.join('\n')}>{img.used_by.length ? img.used_by.map((u) => u.replace(/^(template|lab) /, '')).join(', ') : '—'}</td>
                <td className="row-acts">{admin && <>
                  <button title="Calcola MD5 e verifica" onClick={() => md5(img.name)}><Ui name="select" size={15} /></button>
                  <button className="danger" title="Elimina" onClick={() => remove(img)}><Ui name="trash" size={15} /></button>
                </>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// The user's own IOL license (iourc), tied to this server's host name. NetSO never generates one.
function IolLicenseForm({ onDone, onError }) {
  const [lic, setLic] = useState(null)
  const [text, setText] = useState('')
  useEffect(() => { api('/images/iol-license').then((l) => { setLic(l); setText(l.text) }).catch(onError) }, [onError])
  const save = async (value) => {
    try { const l = await api('/images/iol-license', { method: 'PUT', body: { text: value } }); setLic(l); setText(l.text); if (value) onDone() } catch (e) { onError(e) }
  }
  if (!lic) return <div className="cat-form"><span className="spin" /></div>
  return (
    <form className="cat-form" onSubmit={(e) => { e.preventDefault(); save(text) }}>
      <p className="hint" style={{ margin: 0 }}>Licenza IOL per l'host <code>{lic.hostname}</code> {lic.present ? <span className="pill running">presente</span> : <span className="pill dirty">mancante</span>}</p>
      <label className="field"><span>iourc</span>
        <textarea rows={4} className="mono" value={text} onChange={(e) => setText(e.target.value)} placeholder={`[license]\n${lic.hostname} = <la tua chiave>;`} /></label>
      <div className="row-end">
        {lic.present && <button type="button" className="danger" onClick={() => save('')}><Ui name="trash" size={15} /> Rimuovi</button>}
        <button className="primary" disabled={!text.trim()}><Ui name="save" size={15} /> Salva licenza</button>
      </div>
    </form>
  )
}

function FolderInput({ value, onChange, needed }) {
  return <>
    <input required list="img-needed" pattern={IMG_RE} value={value} onChange={(e) => onChange(e.target.value)} placeholder="Cartella, es. vios-adventerprisek9-m-15.6.2T" />
    <datalist id="img-needed">{needed.map((t) => <option key={t.name} value={t.image}>{t.name}</option>)}</datalist>
  </>
}

function UploadForm({ needed, onUpload }) {
  const [name, setName] = useState('')
  const [files, setFiles] = useState([])
  return (
    <form className="cat-form" onSubmit={(e) => { e.preventDefault(); onUpload(name, files) }}>
      <label className="field"><span>Cartella dell'immagine (= campo «immagine» del dispositivo)</span><FolderInput value={name} onChange={setName} needed={needed} /></label>
      <label className="field"><span>Disco/i (qcow2, vmdk, vdi, vhd, img, ova: diventano qcow2) oppure IOS/IOL (.image, .bin)</span>
        <input type="file" multiple required accept=".qcow2,.vmdk,.vdi,.vhd,.vhdx,.img,.raw,.ova,.iso,.bin,.image" onChange={(e) => setFiles([...e.target.files])} /></label>
      <div className="row-end"><button className="primary" disabled={!name || !files.length}><Ui name="upload" size={15} /> Carica {files.length > 1 ? `${files.length} file` : ''}</button></div>
    </form>
  )
}

function UrlForm({ needed, onDone, onError }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const go = async (e) => {
    e.preventDefault()
    try { await api('/images/fetch', { method: 'POST', body: { name, url } }); onDone() } catch (err) { onError(err) }
  }
  return (
    <form className="cat-form" onSubmit={go}>
      <div className="grid2">
        <label className="field"><span>Cartella dell'immagine</span><FolderInput value={name} onChange={setName} needed={needed} /></label>
        <label className="field"><span>Indirizzo del file (il server lo scarica)</span><input required type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/disco.qcow2" /></label>
      </div>
      <div className="row-end"><button className="primary" disabled={!name || !url}><Ui name="download" size={15} /> Scarica sul server</button></div>
    </form>
  )
}

function RefplatForm({ onUpload, onDone, onError }) {
  const [isos, setIsos] = useState(null)
  const [scan, setScan] = useState(null)
  const [sel, setSel] = useState(null)
  const [pick, setPick] = useState(new Set())
  const loadIsos = () => api('/images/iso').then(setIsos).catch(onError)
  useEffect(() => { loadIsos() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const open = async (iso) => {
    setSel(iso); setScan(null)
    try { const s = await api(`/images/refplat/${encodeURIComponent(iso)}`); setScan(s); setPick(new Set(s.images.filter((i) => !i.present).map((i) => i.id))) } catch (e) { onError(e) }
  }
  const go = async () => {
    try { await api(`/images/refplat/${encodeURIComponent(sel)}`, { method: 'POST', body: { images: [...pick] } }); onDone() } catch (e) { onError(e) }
  }
  return (
    <div className="cat-form">
      <p className="small">La ISO <b>refplat</b> di Cisco CML (scaricata dal tuo account Cisco) contiene i dischi e le definizioni di IOSv, IOSvL2, CSR/Cat8000v, ASAv, NX-OSv, IOS-XRv… NetSO li importa come immagini e dispositivi.</p>
      <div className="grid2">
        <label className="field"><span>Carica la ISO sul server</span>
          <input type="file" accept=".iso" onChange={async (e) => { if (e.target.files[0]) { await onUpload('_iso', [e.target.files[0]]); loadIsos() } }} /></label>
        <div className="field"><span>…oppure copiala in <code>/opt/netso/images/_iso/</code> e scegli</span>
          <div className="iso-list">{isos === null ? <span className="spin" /> : isos.length === 0 ? <span className="muted small">nessuna ISO</span>
            : isos.map((i) => <button key={i.name} className={sel === i.name ? 'on' : ''} onClick={() => open(i.name)}><Ui name="config" size={14} /> {i.name} <span className="muted">{fmtSize(i.size)}</span></button>)}</div>
        </div>
      </div>
      {sel && !scan && <span className="spin" />}
      {scan && <>
        <div className="def-list">{scan.images.map((i) => (
          <label key={i.id} className={`def-row ${pick.has(i.id) ? 'on' : ''}`}>
            <input type="checkbox" checked={pick.has(i.id)} onChange={() => setPick((p) => { const n = new Set(p); n.has(i.id) ? n.delete(i.id) : n.add(i.id); return n })} />
            <span className="grow"><b>{i.label || i.id}</b> <span className="muted small mono">{i.id} · {i.node_definition_id}</span></span>
            {i.present && <span className="pill running">già presente</span>}
          </label>
        ))}</div>
        <div className="row-end"><button className="primary" disabled={!pick.size} onClick={go}><Ui name="download" size={15} /> Importa {pick.size} immagini</button></div>
      </>}
    </div>
  )
}

function PnetForm({ onDone, onError }) {
  const [source, setSource] = useState(() => { try { return localStorage.getItem('netso.pnetSource') || '' } catch { return '' } })
  const [scan, setScan] = useState(null)
  const [busy, setBusy] = useState(false)
  const [pick, setPick] = useState(new Set())
  const [move, setMove] = useState(false)
  const look = async (e) => {
    e.preventDefault()
    setBusy(true); setScan(null)
    try {
      const s = await api('/images/pnet/scan', { method: 'POST', body: { source } })
      setScan(s); setPick(new Set(s.items.filter((i) => !i.present).map((i) => i.src)))
      try { localStorage.setItem('netso.pnetSource', source) } catch { /* ignore */ }
    } catch (err) { onError(err) }
    setBusy(false)
  }
  const go = async () => {
    try { await api('/images/pnet/import', { method: 'POST', body: { source, pick: [...pick], move } }); onDone() } catch (e) { onError(e) }
  }
  const toggle = (k) => setPick((p) => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n })
  const total = scan ? scan.items.filter((i) => pick.has(i.src)).reduce((a, i) => a + i.size, 0) : 0
  return (
    <div className="cat-form">
      <p className="small">Prende le tue immagini da un server <b>PNETLab / EVE-NG</b> (<code>/opt/unetlab/addons</code>: <code>qemu</code>, <code>dynamips</code>, <code>iol</code>) e le mette nelle cartelle di NetSO, collegandole al dispositivo giusto (vios → Cisco IOSv, c7200 → 7200…) o creandone uno nuovo.</p>
      <form className="grid2" onSubmit={look}>
        <label className="field"><span>Server PNETLab (SSH con chiave) oppure cartella sul server NetSO</span>
          <input required value={source} onChange={(e) => setSource(e.target.value)} placeholder="root@192.168.12.50:/opt/unetlab/addons" /></label>
        <div className="field"><span>&nbsp;</span><button className="primary" disabled={!source || busy}>{busy ? <span className="spin" /> : <Ui name="search" size={15} />} Cerca immagini</button></div>
      </form>
      <p className="hint">Una volta sola sul server NetSO: <code>sudo ssh-keygen</code> · <code>sudo ssh-copy-id root@IP-PNETLAB</code></p>
      {scan && <>
        <div className="def-list">{scan.items.map((i) => (
          <label key={i.src} className={`def-row ${pick.has(i.src) ? 'on' : ''}`}>
            <input type="checkbox" disabled={i.present} checked={pick.has(i.src)} onChange={() => toggle(i.src)} />
            <span className="grow"><b className="mono">{i.folder}</b> <span className="muted small">{{ dynamips: 'Dynamips', iol: 'IOL' }[i.kind] || 'QEMU'} · {i.files.join(', ')} · {fmtSize(i.size)}</span></span>
            {i.present ? <span className="pill running">già presente</span> : <span className={`pill ${i.template ? 'running' : 'dirty'}`}>{i.action}</span>}
          </label>
        ))}</div>
        {scan.notes.map((n) => <p key={n} className="hint">{n}</p>)}
        <div className="row-end">
          {!scan.remote && <label className="check small" style={{ paddingBottom: 0, alignSelf: "center" }}><input type="checkbox" checked={move} onChange={(e) => setMove(e.target.checked)} /> Sposta invece di copiare</label>}
          <button className="primary" disabled={!pick.size} onClick={go}><Ui name="download" size={15} /> Importa {pick.size} immagini ({fmtSize(total)})</button>
        </div>
      </>}
    </div>
  )
}

function DockerTab({ onUpload, onJobs, running, onError, admin }) {
  const [data, setData] = useState(null)
  const [mode, setMode] = useState(null) // 'pull' | 'load'
  const [ref, setRef] = useState('')
  const [tag, setTag] = useState('')
  const [file, setFile] = useState(null)
  const load = useCallback(() => api('/images/docker').then(setData).catch(onError), [onError])
  useEffect(() => { load() }, [load])
  const wasRunning = useRef(running)
  useEffect(() => { if (wasRunning.current && !running) load(); wasRunning.current = running }, [running, load])
  const pull = async (r) => {
    try { await api('/images/docker/pull', { method: 'POST', body: { ref: r } }); onJobs() } catch (e) { onError(e) }
  }
  const remove = async (img) => {
    if (!confirm(`Eliminare l'immagine Docker "${img.ref}" (${img.size})?`)) return
    try { await api('/images/docker/remove', { method: 'POST', body: { ref: img.ref } }) } catch (e) {
      if (!String(e.message).startsWith('Usata da') || !confirm(`${e.message}\n\nEliminarla comunque?`)) return onError(e)
      try { await api('/images/docker/remove', { method: 'POST', body: { ref: img.ref, force: true } }) } catch (e2) { return onError(e2) }
    }
    load()
  }
  const sendTar = async (e) => {
    e.preventDefault()
    setMode(null)
    await onUpload('docker', [file], (f) => `/api/images/docker-load?filename=${encodeURIComponent(f.name)}${tag ? `&tag=${encodeURIComponent(tag)}` : ''}`)
    setFile(null); setTag('')
  }
  const missing = (data?.templates || []).filter((t) => !t.present && t.pullable)
  return (
    <>
      <div className="cat-bar">
        <div className="grow" />
        {admin && <>
          {missing.length > 0 && <button disabled={running} onClick={() => missing.forEach((t) => pull(t.ref))}><Ui name="download" size={15} /> Mancanti ({missing.length})</button>}
          <button onClick={() => setMode(mode === 'load' ? null : 'load')}><Ui name="upload" size={15} /> .tar</button>
          <button className="primary" onClick={() => setMode(mode === 'pull' ? null : 'pull')}><Ui name="download" size={15} /> Pull</button>
        </>}
      </div>
      {data && !data.available && <p className="hint">Docker non è installato su questo server.</p>}
      {mode === 'pull' && (
        <form className="cat-form" onSubmit={(e) => { e.preventDefault(); pull(ref.trim()); setMode(null); setRef('') }}>
          <label className="field"><span>Immagine (registry/nome:tag)</span>
            <input required autoFocus value={ref} onChange={(e) => setRef(e.target.value)} placeholder="quay.io/frrouting/frr:10.2.1 · ghcr.io/nokia/srlinux:latest · alpine:3.20" /></label>
          <div className="row-end"><button className="primary" disabled={!ref.trim()}><Ui name="download" size={15} /> Scarica sul server</button></div>
        </form>
      )}
      {mode === 'load' && (
        <form className="cat-form" onSubmit={sendTar}>
          <div className="grid2">
            <label className="field"><span>File .tar / .tar.gz / .tar.xz</span>
              <input type="file" required accept=".tar,.gz,.xz,.tgz" onChange={(e) => setFile(e.target.files[0] || null)} /></label>
            <label className="field"><span>Nome:tag</span>
              <input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="ceos:4.32.0F" list="docker-wanted" />
              <datalist id="docker-wanted">{(data?.templates || []).filter((t) => !t.present).map((t) => <option key={t.template} value={t.ref}>{t.template}</option>)}</datalist></label>
          </div>
          <div className="row-end"><button className="primary" disabled={!file}><Ui name="upload" size={15} /> Carica e importa</button></div>
        </form>
      )}
      {(data?.templates || []).some((t) => !t.present) && (
        <div className="chip-row"><span className="muted small">Mancano:</span>{data.templates.filter((t) => !t.present).map((t) => (
          t.pullable && admin
            ? <button key={t.template} className="tag-chip" disabled={running} title={`docker pull ${t.ref}`} onClick={() => pull(t.ref)}><Ui name="download" size={12} /> {t.template}</button>
            : <span key={t.template} className="tag-chip off" title={`${t.ref}: dal sito del vendor, poi .tar`}>{t.template}</span>
        ))}</div>
      )}
      <div className="tbl-wrap cat-tbl">
        <table className="tbl">
          <thead><tr><th>Immagine</th><th>Dimensione</th><th>Creata</th><th>Usata da</th><th /></tr></thead>
          <tbody>
            {data === null && <tr><td colSpan={5}><span className="spin" /></td></tr>}
            {data?.images.length === 0 && <tr><td colSpan={5} className="muted">Nessuna immagine</td></tr>}
            {data?.images.map((img) => (
              <tr key={img.ref + img.id}>
                <td><b className="mono">{img.ref}</b></td>
                <td className="small">{img.size}</td>
                <td className="small muted">{img.created}</td>
                <td className="small muted" title={img.used_by.join('\n')}>{img.used_by.length ? img.used_by.map((u) => u.replace(/^(template|lab) /, '')).join(', ') : '—'}</td>
                <td className="row-acts">{admin && <button className="danger" title="Elimina" onClick={() => remove(img)}><Ui name="trash" size={15} /></button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// Standalone manager for user-uploaded icons (Sistema → Icone personalizzate). The device editor
// also lets you pick these; this is the discoverable place to upload and remove them.
export function IconsDialog({ onClose, onError }) {
  const [icons, setIcons] = useState([])
  const load = useCallback(() => api('/icons').then(setIcons).catch(onError), [onError])
  useEffect(() => { load() }, [load])
  const input = useRef(null)
  const add = async (files) => {
    for (const file of files) {
      const base = file.name.replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9 _.\-]/g, '-').slice(0, 48) || 'icona'
      try { await uploadTo(`/api/icons/${encodeURIComponent(base)}/upload?filename=${encodeURIComponent(file.name)}`, file, () => {}) } catch (e) { onError(e) }
    }
    load()
  }
  const del = async (n) => { try { await api(`/icons/${encodeURIComponent(n)}`, { method: 'DELETE' }); load() } catch (e) { onError(e) } }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="save" title="Icone personalizzate" sub="Carica le tue icone (SVG, PNG, JPG…): le ritrovi nel pannello icona di ogni dispositivo" onClose={onClose} />
        <div className="icon-pick">
          {icons.map((ic) => (
            <button key={ic.name} type="button" className="has-del" title={ic.name}>
              <Icon name={`custom:${ic.name}`} size={32} /><small>{ic.name}</small>
              <span className="pick-del" title="Elimina" onClick={() => del(ic.name)}>×</span>
            </button>
          ))}
          <button type="button" className="icon-add" onClick={() => input.current?.click()}><Ui name="upload" size={24} /><small>Aggiungi…</small></button>
          <input ref={input} type="file" hidden multiple accept=".svg,.png,.jpg,.jpeg,.gif,.webp" onChange={(e) => { add([...e.target.files]); e.target.value = '' }} />
        </div>
        {!icons.length && <p className="hint">Nessuna icona personalizzata ancora. Caricane una e la assegni dal pannello icona di un dispositivo (Dispositivi → Modifica).</p>}
      </div>
    </div>
  )
}
