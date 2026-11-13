// Devices & images: template catalog (create / edit / import CML node definitions and GNS3 appliances)
// and the image manager (upload with conversion, download from URL, CML refplat ISO, checksums, Docker images).
import { useCallback, useEffect, useRef, useState } from 'react'
import { CONSOLES, NIC_MODELS, api, interfaceNames, uploadTo } from './api.js'
import Icon, { DEVICE_COLORS, LINE_ICONS, Ui, lineLabel } from './icons.jsx'
import { ModalHead } from './LabView.jsx'
import { t, useLang } from './i18n.js'

// Only 'external' is prose; the rest are technical identifiers, identical in both languages.
const kindLabel = (k) => ({ qemu: 'VM QEMU', dynamips: 'Dynamips', iol: 'Cisco IOL', docker: 'Container', bridge: 'Switch', external: t('catalog.kindExternal'), nat: 'NAT', vxlan: 'VXLAN' }[k])
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
  useLang()
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
            <button className={tab === 'devices' ? 'on' : ''} onClick={() => setTab('devices')}>{t('catalog.tabDevices')} <span className="badge">{templates.length}</span></button>
            <button className={tab === 'images' ? 'on' : ''} onClick={() => setTab('images')}>{t('catalog.tabImages')} <span className="badge">{images?.length ?? '…'}</span></button>
            <button className={tab === 'docker' ? 'on' : ''} onClick={() => setTab('docker')}>Docker</button>
          </div>
          <div className="grow" />
          <button className="icon-btn" onClick={onClose} title={t('common.close')}><Ui name="close" size={16} /></button>
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
  useLang()
  return (
    <div className="jobs">
      {uploads.map((u) => (
        <div key={u.id} className="job running"><Ui name="upload" size={14} /><span className="grow">{t('catalog.uploading', { label: u.label })}</span>
          <span className="bar"><i style={{ width: `${Math.round(u.progress * 100)}%` }} /></span><b>{Math.round(u.progress * 100)}%</b></div>
      ))}
      {jobs.map((j) => (
        <div key={j.id} className={`job ${j.status}`}>
          {j.status === 'running' ? <span className="spin" /> : <Ui name={j.status === 'done' ? 'select' : 'destroy'} size={14} />}
          <span className="grow"><b>{j.label}</b> <span className="muted small">{j.message}</span></span>
          {j.status === 'running' && <span className={`bar ${j.progress == null ? 'indet' : ''}`}><i style={{ width: `${Math.round((j.progress ?? 0.3) * 100)}%` }} /></span>}
          {j.status !== 'running' && <button className="icon-btn" title={t('catalog.hide')} onClick={() => onDismiss(j.id)}><Ui name="close" size={13} /></button>}
        </div>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ devices
function DevicesTab({ templates, images, onChanged, onError, admin }) {
  useLang()
  const [q, setQ] = useState('')
  const [edit, setEdit] = useState(null) // {tpl, isNew}
  const [importing, setImporting] = useState(false)
  const have = new Set((images || []).filter((i) => i.disks.length).map((i) => i.name))
  const f = q.trim().toLowerCase()
  const shown = templates.filter((tpl) => !f || `${tpl.name} ${tpl.description} ${tpl.vendor || ''}`.toLowerCase().includes(f))
  const remove = async (tpl) => {
    const restore = tpl.origin === 'modificato'
    if (!confirm(restore ? t('catalog.confirmRestore', { name: tpl.name }) : t('catalog.confirmDeleteDevice', { name: tpl.name }))) return
    try { await api(`/templates/${encodeURIComponent(tpl.name)}`, { method: 'DELETE' }); onChanged() } catch (e) { onError(e) }
  }
  return (
    <>
      <div className="cat-bar">
        <label className="pal-search grow"><Ui name="search" size={14} /><input placeholder={t('catalog.searchPlaceholder')} value={q} onChange={(e) => setQ(e.target.value)} /></label>
        {admin && <button onClick={() => setImporting(true)} title={t('catalog.importDefTitle')}><Ui name="upload" size={15} /> {t('catalog.import')}</button>}
        {admin && <button className="primary" onClick={() => setEdit({ tpl: { name: '', kind: 'qemu', icon: 'router', cpus: 1, ram: 1024, nics: 4, nic_model: 'virtio-net-pci', disk_bus: 'virtio', console: 'telnet', iface_prefix: 'eth', iface_start: 0, config_method: 'none', privileged: true }, isNew: true })}>
          <Ui name="plus" size={15} /> {t('catalog.new')}</button>}
      </div>
      <div className="tbl-wrap cat-tbl">
        <table className="tbl">
          <thead><tr><th>{t('catalog.colDevice')}</th><th>{t('catalog.colType')}</th><th>{t('catalog.colImage')}</th><th>{t('catalog.colResources')}</th><th /><th /></tr></thead>
          <tbody>
            {shown.map((tpl) => {
              const ok = !['qemu', 'iol', 'dynamips'].includes(tpl.kind) ? true : images ? have.has(tpl.image) : null
              return (
                <tr key={tpl.name} title={[tpl.vendor, tpl.description, tpl.source].filter(Boolean).join(' · ')}>
                  <td><span className="dev-cell"><Icon name={tpl.icon} size={20} /><b>{tpl.name}</b>{tpl.origin && tpl.origin !== 'integrato' && <span className="muted small">{tpl.origin}</span>}</span></td>
                  <td className="muted">{kindLabel(tpl.kind) || tpl.kind}</td>
                  <td><code className="small">{tpl.image || '—'}</code></td>
                  <td className="muted small">{['qemu', 'docker', 'iol', 'dynamips'].includes(tpl.kind) ? t('catalog.resVm', { cpus: tpl.cpus, ram: tpl.ram, nics: tpl.nics }) : t('catalog.resPorts', { nics: tpl.nics })}</td>
                  <td>{ok === null ? <span className="spin" /> : <span className={`dot-ok ${ok ? 'on' : 'off'}`} title={ok ? t('catalog.ready') : t('catalog.imageMissing')} />}</td>
                  <td className="row-acts">{admin && <>
                    <button title={t('catalog.edit')} onClick={() => setEdit({ tpl, isNew: false })}><Ui name="edit" size={15} /></button>
                    <button title={t('catalog.duplicate')} onClick={() => setEdit({ tpl: { ...tpl, name: `${tpl.name}-copia`.slice(0, 32), source: null, origin: undefined }, isNew: true })}><Ui name="copy" size={15} /></button>
                    {tpl.origin !== 'integrato' && <button className="danger" title={tpl.origin === 'modificato' ? t('catalog.restoreOriginal') : t('common.delete')} onClick={() => remove(tpl)}><Ui name={tpl.origin === 'modificato' ? 'refresh' : 'trash'} size={15} /></button>}
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
  useLang()
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
          <div className="grow"><h3>{isNew ? t('catalog.newDevice') : t('catalog.editName', { name: tpl.name })}</h3>
            <small className="muted">{tpl.origin === 'integrato' ? t('catalog.builtinNote') : tpl.source || t('catalog.personalTemplate')}</small></div>
          <button type="button" className="icon-btn" onClick={onClose}><Ui name="close" size={16} /></button>
        </div>
        <div className="sheet-body">
          <fieldset><legend>{t('catalog.general')}</legend>
            <div className="grid2">
              <label className="field"><span>{t('catalog.name')}</span><input required pattern="[A-Za-z][A-Za-z0-9_\-]{0,31}" value={f.name} onChange={set('name')} placeholder={t('catalog.phDeviceName')} /></label>
              <label className="field"><span>{t('catalog.type')}</span>
                <select value={f.kind} onChange={set('kind')} disabled={!isNew}>
                  <option value="qemu">{t('catalog.optQemu')}</option><option value="docker">{t('catalog.optDocker')}</option>
                </select></label>
            </div>
            <div className="grid2">
              <label className="field"><span>{t('catalog.description')}</span><input value={f.description} onChange={set('description')} /></label>
              <label className="field"><span>{t('catalog.vendor')}</span><input value={f.vendor} onChange={set('vendor')} placeholder={t('catalog.phVendor')} /></label>
            </div>
            <div className="field"><span>{t('catalog.icon')}</span>
              <div className="icon-pick">{Object.keys(DEVICE_COLORS).map((ic) => (
                <button type="button" key={ic} className={f.icon === ic ? 'on' : ''} onClick={() => set('icon')(ic)}><Icon name={ic} size={26} /><small>{ic}</small></button>
              ))}</div>
              <span className="pick-sec">{t('catalog.packLine')}</span>
              <div className="icon-pick">{LINE_ICONS.map((ic) => (
                <button type="button" key={ic} className={f.icon === `line:${ic}` ? 'on' : ''} title={lineLabel(ic)} onClick={() => set('icon')(`line:${ic}`)}><Icon name={`line:${ic}`} size={28} /><small>{lineLabel(ic)}</small></button>
              ))}</div>
              <span className="pick-sec">{t('catalog.personalIcons')} <small className="muted">{t('catalog.personalIconsHint')}</small></span>
              <div className="icon-pick">
                {customIcons.map((ic) => (
                  <button type="button" key={ic.name} className={`has-del ${f.icon === `custom:${ic.name}` ? 'on' : ''}`} title={ic.name} onClick={() => set('icon')(`custom:${ic.name}`)}>
                    <Icon name={`custom:${ic.name}`} size={28} /><small>{ic.name}</small>
                    <span className="pick-del" title={t('catalog.deleteIcon')} onClick={(e) => { e.stopPropagation(); delIcon(ic.name) }}>×</span>
                  </button>
                ))}
                <button type="button" className="icon-add" onClick={() => iconInput.current?.click()}><Ui name="upload" size={22} /><small>{t('catalog.add')}</small></button>
                <input ref={iconInput} type="file" hidden accept=".svg,.png,.jpg,.jpeg,.gif,.webp" onChange={(e) => { addIcon(e.target.files[0]); e.target.value = '' }} />
              </div>
            </div>
          </fieldset>
          <fieldset><legend>{qemu ? t('catalog.virtualMachine') : t('catalog.container')}</legend>
            <label className="field"><span>{qemu ? t('catalog.imageFolder') : t('catalog.dockerImage')}</span>
              {qemu ? <>
                <input list="img-folders" required value={f.image} onChange={set('image')} placeholder={t('catalog.phImageFolder')} />
                <datalist id="img-folders">{imgOpts.map((i) => <option key={i} value={i} />)}</datalist>
              </> : <input required value={f.image} onChange={set('image')} placeholder={t('catalog.phDockerImage')} />}
            </label>
            <div className="grid3">
              <label className="field"><span>{t('catalog.cpu')}</span><input type="number" min="1" value={f.cpus} onChange={set('cpus')} /></label>
              <label className="field"><span>{t('catalog.ramMb')}</span><input type="number" min="64" step="64" value={f.ram} onChange={set('ram')} /></label>
              <label className="field"><span>{t('catalog.interfaces')}</span><input type="number" min="1" max="32" value={f.nics} onChange={set('nics')} /></label>
            </div>
            {qemu && <div className="grid3">
              <label className="field"><span>{t('catalog.nicModel')}</span><select value={f.nic_model} onChange={set('nic_model')}>{NIC_MODELS.map((m) => <option key={m}>{m}</option>)}</select></label>
              <label className="field"><span>{t('catalog.diskBus')}</span><select value={f.disk_bus} onChange={set('disk_bus')}><option>virtio</option><option>ide</option><option>sata</option></select></label>
              <label className="field"><span>{t('catalog.console')}</span><select value={f.console} onChange={set('console')}>{CONSOLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            </div>}
            {qemu && <div className="grid2">
              <label className="field"><span>{t('catalog.machine')}</span><input value={f.machine} onChange={set('machine')} placeholder="pc" /></label>
              <label className="field"><span>{t('catalog.cpuModel')}</span><input value={f.cpu_model} onChange={set('cpu_model')} placeholder="host" /></label>
            </div>}
            {qemu && <label className="field"><span>{t('catalog.extraQemu')}</span><input className="mono" value={f.extra_args} onChange={set('extra_args')} /></label>}
            {docker && <>
              <div className="grid2">
                <label className="field"><span>{t('catalog.startCommand')}</span><input className="mono" value={f.cmd} onChange={set('cmd')} placeholder={t('catalog.phStartCommand')} /></label>
                <label className="field"><span>{t('catalog.consoleCommand')}</span><input className="mono" value={f.shell} onChange={set('shell')} placeholder={t('catalog.phConsoleCommand')} /></label>
              </div>
              <label className="field"><span>{t('catalog.envVars')}</span><textarea className="mono" rows={3} value={f.env} onChange={set('env')} /></label>
              <label className="check"><input type="checkbox" checked={f.privileged} onChange={set('privileged')} /> {t('catalog.privilegedContainer')}</label>
            </>}
          </fieldset>
          <fieldset><legend>{t('catalog.interfaceNames')}</legend>
            <div className="grid2">
              <label className="field"><span>{t('catalog.prefix')}</span><input value={f.iface_prefix} onChange={set('iface_prefix')} placeholder={t('catalog.phPrefix')} /></label>
              <label className="field"><span>{t('catalog.firstNumber')}</span><input type="number" min="0" value={f.iface_start} onChange={set('iface_start')} /></label>
            </div>
            <label className="field"><span>{t('catalog.exactList')}</span>
              <textarea className="mono" rows={3} value={f.interfaces} onChange={set('interfaces')} placeholder={'Management0/0\nGigabitEthernet0/0\nGigabitEthernet0/1'} /></label>
            <p className="hint ifaces">{preview.slice(0, 10).join(' · ')}{preview.length > 10 ? ' …' : ''}</p>
          </fieldset>
          <fieldset><legend>{t('catalog.startupConfig')}</legend>
            <div className="grid3">
              <label className="field"><span>{t('catalog.howReaches')}</span>
                <select value={f.config_method} onChange={set('config_method')}>
                  <option value="none">{t('catalog.optNone')}</option>
                  {qemu && <option value="disk">{t('catalog.optDisk')}</option>}
                  {qemu && <option value="iso">{t('catalog.optIso')}</option>}
                  {docker && <option value="copy">{t('catalog.optCopy')}</option>}
                </select></label>
              <label className="field"><span>{docker ? t('catalog.pathInContainer') : t('catalog.fileName')}</span><input value={f.config_file} disabled={f.config_method === 'none'} onChange={set('config_file')} placeholder={docker ? '/etc/frr/frr.conf' : 'ios_config.txt'} /></label>
              {qemu && <label className="field"><span>{t('catalog.volumeLabel')}</span><input value={f.config_label} disabled={f.config_method === 'none'} onChange={set('config_label')} placeholder="config" /></label>}
            </div>
          </fieldset>
          {tpl.image_files?.length > 0 && <fieldset><legend>{t('catalog.expectedImageFiles')}</legend>
            <ul className="exp-files">{tpl.image_files.map((x, i) => <li key={i}><code>{x.filename}</code> <span className="muted small">{x.version}{x.md5 ? ` · md5 ${x.md5}` : ''}</span></li>)}</ul>
          </fieldset>}
        </div>
        <div className="sheet-foot">
          <div className="spacer" />
          <button type="button" className="ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="primary"><Ui name="save" size={15} /> {isNew ? t('catalog.createDevice') : t('common.save')}</button>
        </div>
      </form>
    </div>
  )
}

function DefImport({ onClose, onSaved, onError }) {
  useLang()
  const [files, setFiles] = useState(null)
  const [res, setRes] = useState(null)
  const [pick, setPick] = useState(new Set())
  const input = useRef(null)
  const load = async (list) => {
    const fs = await Promise.all([...list].map(async (fl) => ({ filename: fl.name, data: toBase64(await fl.arrayBuffer()) })))
    setFiles(fs)
    try {
      const r = await api('/templates/import', { method: 'POST', body: { files: fs } })
      setRes(r); setPick(new Set(r.templates.map((tpl) => tpl.name)))
    } catch (e) { onError(e) }
  }
  const save = async () => {
    try { await api('/templates/import', { method: 'POST', body: { files, save: true, pick: [...pick] } }); onSaved() } catch (e) { onError(e) }
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="upload" title={t('catalog.importDefsTitle')} sub={t('catalog.importDefsSub')} onClose={onClose} />
        {!res ? (
          <div className="drop" onClick={() => input.current?.click()} onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); load(e.dataTransfer.files) }}>
            <Ui name="upload" size={30} />
            <p><b>{t('catalog.dropFiles')}</b> {t('catalog.dropFilesRest')}</p>
            <div className="src-chips"><span><b>CML</b> node-definitions/*.yaml</span><span><b>CML</b> virl-base-images/*/*.yaml</span><span><b>GNS3</b> appliances/*.gns3a</span></div>
            <input ref={input} type="file" multiple hidden accept=".yaml,.yml,.gns3a,.json" onChange={(e) => load(e.target.files)} />
          </div>
        ) : <>
          <div className="def-list">
            {res.templates.map((tpl) => (
              <label key={tpl.name} className={`def-row ${pick.has(tpl.name) ? 'on' : ''}`}>
                <input type="checkbox" checked={pick.has(tpl.name)} onChange={() => setPick((p) => { const n = new Set(p); n.has(tpl.name) ? n.delete(tpl.name) : n.add(tpl.name); return n })} />
                <Icon name={tpl.icon} size={28} />
                <span className="grow"><b>{tpl.name}</b> <span className="muted small">{tpl.source} · {kindLabel(tpl.kind)} · {t('catalog.ramInt', { ram: tpl.ram, nics: tpl.nics })}</span>
                  <small className="mono muted">{tpl.iface_names.slice(0, 4).join(', ')}{tpl.iface_names.length > 4 ? '…' : ''} · {t('catalog.imageWord')} <b>{tpl.image}</b></small></span>
              </label>
            ))}
            {!res.templates.length && <p className="muted">{t('catalog.noDeviceInFiles')}</p>}
          </div>
          {res.warnings.length > 0 && <details className="imp-warn" open><summary>{t('catalog.nNotes', { n: res.warnings.length })}</summary><ul>{res.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details>}
          <div className="row-end">
            <button className="ghost" onClick={() => { setRes(null); setFiles(null) }}>{t('catalog.otherFiles')}</button>
            <button className="primary" disabled={!pick.size} onClick={save}><Ui name="plus" size={15} /> {t('catalog.addNDevices', { n: pick.size })}</button>
          </div>
        </>}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ images
function ImagesTab({ images, templates, onUpload, onReload, onError, admin }) {
  useLang()
  const [mode, setMode] = useState(null) // 'upload' | 'url' | 'pnet' | 'iso'
  const needed = templates.filter((tpl) => tpl.kind === 'qemu' && tpl.image && !(images || []).some((i) => i.name === tpl.image && i.disks.length))
  const md5 = (name) => api(`/images/${encodeURIComponent(name)}/checksum`, { method: 'POST' }).then(onReload).catch(onError)
  const remove = async (img) => {
    if (!confirm(t('catalog.confirmDeleteImage', { name: img.name, size: fmtSize(img.size) }))) return
    try { await api(`/images/${encodeURIComponent(img.name)}`, { method: 'DELETE' }) } catch (e) {
      if (!String(e.message).startsWith('Used by') || !confirm(`${e.message}\n\n${t('catalog.deleteAnywayNodes')}`)) return onError(e)
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
          <button className={mode === 'iol' ? 'on' : ''} onClick={() => setMode(mode === 'iol' ? null : 'iol')}><Ui name="lock" size={15} /> {t('catalog.iolLicense')}</button>
          <button className="primary" onClick={() => setMode(mode === 'upload' ? null : 'upload')}><Ui name="upload" size={15} /> {t('catalog.upload')}</button>
        </>}
      </div>
      {mode === 'upload' && <UploadForm needed={needed} onUpload={(n, fs) => { setMode(null); onUpload(n, fs) }} />}
      {mode === 'url' && <UrlForm needed={needed} onDone={() => { setMode(null); onReload() }} onError={onError} />}
      {mode === 'pnet' && <PnetForm onDone={() => { setMode(null); onReload() }} onError={onError} />}
      {mode === 'iol' && <IolLicenseForm onDone={() => setMode(null)} onError={onError} />}
      {mode === 'iso' && <RefplatForm onUpload={onUpload} onDone={() => { setMode(null); onReload() }} onError={onError} />}
      <div className="tbl-wrap cat-tbl">
        <table className="tbl">
          <thead><tr><th>{t('catalog.colImage')}</th><th>{t('catalog.colFile')}</th><th>{t('catalog.colSize')}</th><th>{t('catalog.colUsedBy')}</th><th /></tr></thead>
          <tbody>
            {images === null && <tr><td colSpan={5}><span className="spin" /></td></tr>}
            {images?.length === 0 && <tr><td colSpan={5} className="muted">{t('catalog.noImages')}</td></tr>}
            {images?.map((img) => (
              <tr key={img.name}>
                <td><b className="mono">{img.name}</b>{!img.disks.length && <div><span className="pill error">{t('catalog.noDisk')}</span></div>}</td>
                <td className="small">{img.files.map((f) => (
                  <div key={f.name} className="img-file"><code>{f.name}</code> <span className="muted">{fmtSize(f.size)}</span>
                    {f.verified && <span className="pill running" title={`MD5 ${f.md5}`}>✓ {f.verified.template} {f.verified.version}</span>}
                    {f.md5 && !f.verified && <span className="muted mono" title="MD5">{f.md5.slice(0, 10)}…</span>}
                    {f.partial && <span className="pill dirty">{t('catalog.incomplete')}</span>}</div>
                ))}</td>
                <td className="muted small">{fmtSize(img.size)}</td>
                <td className="small muted" title={img.used_by.join('\n')}>{img.used_by.length ? img.used_by.map((u) => u.replace(/^(template|lab) /, '')).join(', ') : '—'}</td>
                <td className="row-acts">{admin && <>
                  <button title={t('catalog.calcMd5')} onClick={() => md5(img.name)}><Ui name="select" size={15} /></button>
                  <button className="danger" title={t('common.delete')} onClick={() => remove(img)}><Ui name="trash" size={15} /></button>
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
  useLang()
  const [lic, setLic] = useState(null)
  const [text, setText] = useState('')
  useEffect(() => { api('/images/iol-license').then((l) => { setLic(l); setText(l.text) }).catch(onError) }, [onError])
  const save = async (value) => {
    try { const l = await api('/images/iol-license', { method: 'PUT', body: { text: value } }); setLic(l); setText(l.text); if (value) onDone() } catch (e) { onError(e) }
  }
  if (!lic) return <div className="cat-form"><span className="spin" /></div>
  return (
    <form className="cat-form" onSubmit={(e) => { e.preventDefault(); save(text) }}>
      <p className="hint" style={{ margin: 0 }}>{t('catalog.iolLicenseFor')} <code>{lic.hostname}</code> {lic.present ? <span className="pill running">{t('catalog.present')}</span> : <span className="pill dirty">{t('catalog.missing')}</span>}</p>
      <label className="field"><span>iourc</span>
        <textarea rows={4} className="mono" value={text} onChange={(e) => setText(e.target.value)} placeholder={t('catalog.phIourc', { host: lic.hostname })} /></label>
      <div className="row-end">
        {lic.present && <button type="button" className="danger" onClick={() => save('')}><Ui name="trash" size={15} /> {t('catalog.remove')}</button>}
        <button className="primary" disabled={!text.trim()}><Ui name="save" size={15} /> {t('catalog.saveLicense')}</button>
      </div>
    </form>
  )
}

function FolderInput({ value, onChange, needed }) {
  useLang()
  return <>
    <input required list="img-needed" pattern={IMG_RE} value={value} onChange={(e) => onChange(e.target.value)} placeholder={t('catalog.phFolder')} />
    <datalist id="img-needed">{needed.map((tpl) => <option key={tpl.name} value={tpl.image}>{tpl.name}</option>)}</datalist>
  </>
}

function UploadForm({ needed, onUpload }) {
  useLang()
  const [name, setName] = useState('')
  const [files, setFiles] = useState([])
  return (
    <form className="cat-form" onSubmit={(e) => { e.preventDefault(); onUpload(name, files) }}>
      <label className="field"><span>{t('catalog.imageFolderField')}</span><FolderInput value={name} onChange={setName} needed={needed} /></label>
      <label className="field"><span>{t('catalog.disksField')}</span>
        <input type="file" multiple required accept=".qcow2,.vmdk,.vdi,.vhd,.vhdx,.img,.raw,.ova,.iso,.bin,.image" onChange={(e) => setFiles([...e.target.files])} /></label>
      <div className="row-end"><button className="primary" disabled={!name || !files.length}><Ui name="upload" size={15} /> {t('catalog.upload')} {files.length > 1 ? t('catalog.nFiles', { n: files.length }) : ''}</button></div>
    </form>
  )
}

function UrlForm({ needed, onDone, onError }) {
  useLang()
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const go = async (e) => {
    e.preventDefault()
    try { await api('/images/fetch', { method: 'POST', body: { name, url } }); onDone() } catch (err) { onError(err) }
  }
  return (
    <form className="cat-form" onSubmit={go}>
      <div className="grid2">
        <label className="field"><span>{t('catalog.imageFolderUrl')}</span><FolderInput value={name} onChange={setName} needed={needed} /></label>
        <label className="field"><span>{t('catalog.fileAddress')}</span><input required type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={t('catalog.phFileUrl')} /></label>
      </div>
      <div className="row-end"><button className="primary" disabled={!name || !url}><Ui name="download" size={15} /> {t('catalog.downloadToServer')}</button></div>
    </form>
  )
}

function RefplatForm({ onUpload, onDone, onError }) {
  useLang()
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
      <p className="small">{t('catalog.refplatIntro1')} <b>refplat</b>{t('catalog.refplatIntro2')}</p>
      <div className="grid2">
        <label className="field"><span>{t('catalog.uploadIso')}</span>
          <input type="file" accept=".iso" onChange={async (e) => { if (e.target.files[0]) { await onUpload('_iso', [e.target.files[0]]); loadIsos() } }} /></label>
        <div className="field"><span>{t('catalog.orCopyIn')} <code>/opt/netso/images/_iso/</code> {t('catalog.andChoose')}</span>
          <div className="iso-list">{isos === null ? <span className="spin" /> : isos.length === 0 ? <span className="muted small">{t('catalog.noIso')}</span>
            : isos.map((i) => <button key={i.name} className={sel === i.name ? 'on' : ''} onClick={() => open(i.name)}><Ui name="config" size={14} /> {i.name} <span className="muted">{fmtSize(i.size)}</span></button>)}</div>
        </div>
      </div>
      {sel && !scan && <span className="spin" />}
      {scan && <>
        <div className="def-list">{scan.images.map((i) => (
          <label key={i.id} className={`def-row ${pick.has(i.id) ? 'on' : ''}`}>
            <input type="checkbox" checked={pick.has(i.id)} onChange={() => setPick((p) => { const n = new Set(p); n.has(i.id) ? n.delete(i.id) : n.add(i.id); return n })} />
            <span className="grow"><b>{i.label || i.id}</b> <span className="muted small mono">{i.id} · {i.node_definition_id}</span></span>
            {i.present && <span className="pill running">{t('catalog.alreadyPresent')}</span>}
          </label>
        ))}</div>
        <div className="row-end"><button className="primary" disabled={!pick.size} onClick={go}><Ui name="download" size={15} /> {t('catalog.importNImages', { n: pick.size })}</button></div>
      </>}
    </div>
  )
}

function PnetForm({ onDone, onError }) {
  useLang()
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
      <p className="small">{t('catalog.pnetIntro1')} <b>PNETLab / EVE-NG</b> (<code>/opt/unetlab/addons</code>: <code>qemu</code>, <code>dynamips</code>, <code>iol</code>){t('catalog.pnetIntro2')}</p>
      <form className="grid2" onSubmit={look}>
        <label className="field"><span>{t('catalog.pnetSource')}</span>
          <input required value={source} onChange={(e) => setSource(e.target.value)} placeholder="root@192.168.12.50:/opt/unetlab/addons" /></label>
        <div className="field"><span>&nbsp;</span><button className="primary" disabled={!source || busy}>{busy ? <span className="spin" /> : <Ui name="search" size={15} />} {t('catalog.searchImages')}</button></div>
      </form>
      <p className="hint">{t('catalog.onceOnly')} <code>sudo ssh-keygen</code> · <code>sudo ssh-copy-id root@IP-PNETLAB</code></p>
      {scan && <>
        <div className="def-list">{scan.items.map((i) => (
          <label key={i.src} className={`def-row ${pick.has(i.src) ? 'on' : ''}`}>
            <input type="checkbox" disabled={i.present} checked={pick.has(i.src)} onChange={() => toggle(i.src)} />
            <span className="grow"><b className="mono">{i.folder}</b> <span className="muted small">{{ dynamips: 'Dynamips', iol: 'IOL' }[i.kind] || 'QEMU'} · {i.files.join(', ')} · {fmtSize(i.size)}</span></span>
            {i.present ? <span className="pill running">{t('catalog.alreadyPresent')}</span> : <span className={`pill ${i.template ? 'running' : 'dirty'}`}>{i.action}</span>}
          </label>
        ))}</div>
        {scan.notes.map((n) => <p key={n} className="hint">{n}</p>)}
        <div className="row-end">
          {!scan.remote && <label className="check small" style={{ paddingBottom: 0, alignSelf: "center" }}><input type="checkbox" checked={move} onChange={(e) => setMove(e.target.checked)} /> {t('catalog.moveInsteadCopy')}</label>}
          <button className="primary" disabled={!pick.size} onClick={go}><Ui name="download" size={15} /> {t('catalog.importNImagesSize', { n: pick.size, size: fmtSize(total) })}</button>
        </div>
      </>}
    </div>
  )
}

function DockerTab({ onUpload, onJobs, running, onError, admin }) {
  useLang()
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
    if (!confirm(t('catalog.confirmDeleteDocker', { ref: img.ref, size: img.size }))) return
    try { await api('/images/docker/remove', { method: 'POST', body: { ref: img.ref } }) } catch (e) {
      if (!String(e.message).startsWith('Used by') || !confirm(`${e.message}\n\n${t('catalog.deleteAnyway')}`)) return onError(e)
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
  const missing = (data?.templates || []).filter((tpl) => !tpl.present && tpl.pullable)
  return (
    <>
      <div className="cat-bar">
        <div className="grow" />
        {admin && <>
          {missing.length > 0 && <button disabled={running} onClick={() => missing.forEach((tpl) => pull(tpl.ref))}><Ui name="download" size={15} /> {t('catalog.missingN', { n: missing.length })}</button>}
          <button onClick={() => setMode(mode === 'load' ? null : 'load')}><Ui name="upload" size={15} /> .tar</button>
          <button className="primary" onClick={() => setMode(mode === 'pull' ? null : 'pull')}><Ui name="download" size={15} /> Pull</button>
        </>}
      </div>
      {data && !data.available && <p className="hint">{t('catalog.dockerNotInstalled')}</p>}
      {mode === 'pull' && (
        <form className="cat-form" onSubmit={(e) => { e.preventDefault(); pull(ref.trim()); setMode(null); setRef('') }}>
          <label className="field"><span>{t('catalog.imageRegistry')}</span>
            <input required autoFocus value={ref} onChange={(e) => setRef(e.target.value)} placeholder="quay.io/frrouting/frr:10.2.1 · ghcr.io/nokia/srlinux:latest · alpine:3.20" /></label>
          <div className="row-end"><button className="primary" disabled={!ref.trim()}><Ui name="download" size={15} /> {t('catalog.downloadToServer')}</button></div>
        </form>
      )}
      {mode === 'load' && (
        <form className="cat-form" onSubmit={sendTar}>
          <div className="grid2">
            <label className="field"><span>File .tar / .tar.gz / .tar.xz</span>
              <input type="file" required accept=".tar,.gz,.xz,.tgz" onChange={(e) => setFile(e.target.files[0] || null)} /></label>
            <label className="field"><span>{t('catalog.nameTag')}</span>
              <input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="ceos:4.32.0F" list="docker-wanted" />
              <datalist id="docker-wanted">{(data?.templates || []).filter((tpl) => !tpl.present).map((tpl) => <option key={tpl.template} value={tpl.ref}>{tpl.template}</option>)}</datalist></label>
          </div>
          <div className="row-end"><button className="primary" disabled={!file}><Ui name="upload" size={15} /> {t('catalog.uploadImport')}</button></div>
        </form>
      )}
      {(data?.templates || []).some((tpl) => !tpl.present) && (
        <div className="chip-row"><span className="muted small">{t('catalog.missingColon')}</span>{data.templates.filter((tpl) => !tpl.present).map((tpl) => (
          tpl.pullable && admin
            ? <button key={tpl.template} className="tag-chip" disabled={running} title={`docker pull ${tpl.ref}`} onClick={() => pull(tpl.ref)}><Ui name="download" size={12} /> {tpl.template}</button>
            : <span key={tpl.template} className="tag-chip off" title={t('catalog.fromVendorSite', { ref: tpl.ref })}>{tpl.template}</span>
        ))}</div>
      )}
      <div className="tbl-wrap cat-tbl">
        <table className="tbl">
          <thead><tr><th>{t('catalog.colImage')}</th><th>{t('catalog.colSize')}</th><th>{t('catalog.colCreated')}</th><th>{t('catalog.colUsedBy')}</th><th /></tr></thead>
          <tbody>
            {data === null && <tr><td colSpan={5}><span className="spin" /></td></tr>}
            {data?.images.length === 0 && <tr><td colSpan={5} className="muted">{t('catalog.noImages')}</td></tr>}
            {data?.images.map((img) => (
              <tr key={img.ref + img.id}>
                <td><b className="mono">{img.ref}</b></td>
                <td className="small">{img.size}</td>
                <td className="small muted">{img.created}</td>
                <td className="small muted" title={img.used_by.join('\n')}>{img.used_by.length ? img.used_by.map((u) => u.replace(/^(template|lab) /, '')).join(', ') : '—'}</td>
                <td className="row-acts">{admin && <button className="danger" title={t('common.delete')} onClick={() => remove(img)}><Ui name="trash" size={15} /></button>}</td>
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
  useLang()
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
        <ModalHead icon="save" title={t('catalog.customIconsTitle')} sub={t('catalog.customIconsSub')} onClose={onClose} />
        <div className="icon-pick">
          {icons.map((ic) => (
            <button key={ic.name} type="button" className="has-del" title={ic.name}>
              <Icon name={`custom:${ic.name}`} size={32} /><small>{ic.name}</small>
              <span className="pick-del" title={t('common.delete')} onClick={() => del(ic.name)}>×</span>
            </button>
          ))}
          <button type="button" className="icon-add" onClick={() => input.current?.click()}><Ui name="upload" size={24} /><small>{t('catalog.add')}</small></button>
          <input ref={input} type="file" hidden multiple accept=".svg,.png,.jpg,.jpeg,.gif,.webp" onChange={(e) => { add([...e.target.files]); e.target.value = '' }} />
        </div>
        {!icons.length && <p className="hint">{t('catalog.noCustomIcons')}</p>}
      </div>
    </div>
  )
}
