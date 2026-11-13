// Import from containerlab / CML / GNS3 / EVE-NG-PNETLab, export, and lab snapshots.
import { useEffect, useRef, useState } from 'react'
import { api, apiUrl } from './api.js'
import Icon, { Ui } from './icons.jsx'
import { ModalHead } from './LabView.jsx'
import { getLang, t, useLang } from './i18n.js'

export const EXPORT_FORMATS = [
  { id: 'netso', label: 'NetSO (YAML)', icon: 'download' },
  { id: 'clab', label: 'containerlab (.clab.yml)', icon: 'download' },
  { id: 'cml', label: 'Cisco CML (YAML)', icon: 'download' },
]

export function downloadExport(lab, format = 'netso') {
  const a = document.createElement('a')
  a.href = apiUrl(`/labs/${encodeURIComponent(lab)}/export`, { format })
  a.click()
}

const SOURCES = [
  ['containerlab', '.clab.yml'], ['Cisco CML', '.yaml'], ['GNS3', '.gns3 / .gns3project'], ['EVE-NG / PNETLab', '.unl'], ['NetSO', '.yml'],
]

function toBase64(buf) {
  const bytes = new Uint8Array(buf)
  let s = ''
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000))
  return btoa(s)
}

export function ImportDialog({ folder, templates, onClose, onCreated, onError }) {
  useLang()
  const [file, setFile] = useState(null) // {filename, data}
  const [preview, setPreview] = useState(null)
  const [name, setName] = useState('')
  const [overrides, setOverrides] = useState({})
  const [busy, setBusy] = useState(false)
  const [drag, setDrag] = useState(false)
  const [paste, setPaste] = useState(false)
  const [text, setText] = useState('')
  const input = useRef(null)

  const run = async (f, ov, nm) => {
    setBusy(true)
    try {
      const p = await api('/import/preview', { method: 'POST', body: { ...f, overrides: ov, name: nm || undefined } })
      setPreview(p)
      if (!nm) setName(p.name)
    } catch (e) { onError(e); if (!preview) setFile(null) } finally { setBusy(false) }
  }
  const load = async (fl) => {
    if (!fl) return
    const f = { filename: fl.name, data: toBase64(await fl.arrayBuffer()) }
    setFile(f); setOverrides({}); setPreview(null); setName('')
    run(f, {}, '')
  }
  const loadText = () => {
    const f = { filename: 'pasted.yml', data: toBase64(new TextEncoder().encode(text)) }
    setFile(f); setOverrides({}); setPreview(null); setName('')
    run(f, {}, '')
  }
  useEffect(() => { // re-check the name (already used?) a moment after typing
    if (!file || !preview || name === preview.name) return
    const id = setTimeout(() => run(file, overrides, name), 450)
    return () => clearTimeout(id)
  }, [name]) // eslint-disable-line react-hooks/exhaustive-deps

  const setTpl = (node, tpl) => { const ov = { ...overrides, [node]: tpl }; setOverrides(ov); run(file, ov, name) }
  const doImport = async () => {
    setBusy(true)
    try {
      const r = await api('/import', { method: 'POST', body: { ...file, overrides, name, folder } })
      onCreated(r.name, r.warnings)
    } catch (e) { onError(e) } finally { setBusy(false) }
  }
  const tplByName = Object.fromEntries(templates.map((tpl) => [tpl.name, tpl]))

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal xl import-dlg" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="upload" title={t('interop.importTitle')} sub={t('interop.importSub', { folder: folder || 'root' })} onClose={onClose} />

        {!preview ? (
          <>
            <div className={`drop ${drag ? 'on' : ''} ${busy ? 'busy' : ''}`} onClick={() => input.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); load(e.dataTransfer.files[0]) }}>
              {busy ? <><span className="spin" /><p>{t('interop.analyzing')}</p></> : <>
                <Ui name="upload" size={34} />
                <p><b>{t('interop.dropTitle')}</b> {t('interop.dropOr')}</p>
                <div className="src-chips">{SOURCES.map(([n, ext]) => <span key={n}><b>{n}</b> {ext}</span>)}</div>
              </>}
              <input ref={input} type="file" hidden accept=".yml,.yaml,.gns3,.gns3project,.zip,.unl,.json,.xml" onChange={(e) => load(e.target.files[0])} />
            </div>
            <button className="ghost paste-tgl" onClick={() => setPaste((v) => !v)}><Ui name="code" size={14} /> {paste ? t('interop.hide') : t('interop.orPaste')}</button>
            {paste && <>
              <textarea className="code" style={{ minHeight: 200 }} placeholder={t('interop.pastePlaceholder')} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
              <div className="row-end"><button className="primary" disabled={!text.trim() || busy} onClick={loadText}>{t('interop.analyze')}</button></div>
            </>}
          </>
        ) : (
          <>
            <div className="imp-head">
              <span className={`fmt-badge f-${preview.format}`}>{preview.format_label}</span>
              <span className="muted small">{file.filename} · {t('interop.summary', { nodes: preview.nodes.length, links: preview.links })}{preview.configs.length ? t('interop.summaryConfigs', { n: preview.configs.length }) : ''}</span>
              <div className="spacer" />
              <button className="ghost mini" onClick={() => { setPreview(null); setFile(null) }}>{t('interop.changeFile')}</button>
            </div>
            <label className="field"><span>{t('interop.labName')}</span>
              <input value={name} pattern="[A-Za-z][A-Za-z0-9_\-]{0,31}" onChange={(e) => setName(e.target.value)} className={preview.exists ? 'bad' : ''} />
              {preview.exists && <small className="err-t">{t('interop.nameExists')}</small>}
            </label>
            <div className="tbl-wrap imp-tbl">
              <table className="tbl">
                <thead><tr><th>{t('interop.colNode')}</th><th>{t('interop.colSource')}</th><th>{t('interop.colBecomes')}</th><th>{t('interop.colImage')}</th></tr></thead>
                <tbody>
                  {preview.nodes.map((n) => (
                    <tr key={n.name}>
                      <td><span className="dev-cell"><Icon name={tplByName[n.template]?.icon} size={22} /><b>{n.name}</b></span></td>
                      <td className="muted small mono">{n.source}</td>
                      <td>
                        <select className="cell" value={n.template} onChange={(e) => setTpl(n.name, e.target.value)} disabled={busy || preview.format === 'netso'}>
                          {templates.map((tpl) => <option key={tpl.name} value={tpl.name}>{tpl.name}</option>)}
                        </select>
                      </td>
                      <td className="mono small">{n.image || <span className="muted">{t('interop.fromTemplate')}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {preview.warnings.length > 0 && (
              <details className="imp-warn" open={preview.warnings.length <= 4}>
                <summary>{t('interop.warnings', { n: preview.warnings.length })}</summary>
                <ul>{preview.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </details>
            )}
            <p className="hint">{t('interop.imagesHint')}</p>
            <div className="row-end">
              <button className="ghost" onClick={onClose}>{t('common.cancel')}</button>
              <button className="primary" disabled={busy || preview.exists || !name} onClick={doImport}>{busy ? <span className="spin" /> : <Ui name="upload" size={15} />} {t('interop.import')}</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

const fmtWhen = (ts) => new Date(ts * 1000).toLocaleString(getLang() === 'it' ? 'it-IT' : 'en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })

export function SnapshotsDialog({ lab, dirty, onSave, onRestored, onClose, onError }) {
  useLang()
  const [list, setList] = useState(null)
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [busy, setBusy] = useState('')
  const load = () => api(`/labs/${encodeURIComponent(lab)}/snapshots`).then(setList).catch(onError)
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (label, fn) => { setBusy(label); try { await fn() } catch (e) { onError(e) } finally { setBusy(''); load() } }
  const create = (e) => {
    e.preventDefault()
    act('create', async () => {
      if (dirty && !(await onSave())) return
      await api(`/labs/${encodeURIComponent(lab)}/snapshots`, { method: 'POST', body: { name, description: desc } })
      setName(''); setDesc('')
    })
  }
  const restore = (s) => {
    if (!confirm(t('interop.confirmRestore', { name: s.name, when: fmtWhen(s.created) }))) return
    act(s.name, async () => { await api(`/labs/${encodeURIComponent(lab)}/snapshots/${s.name}/restore`, { method: 'POST' }); onRestored() })
  }
  const remove = (s) => confirm(t('interop.confirmDeleteSnap', { name: s.name })) && act(s.name, () => api(`/labs/${encodeURIComponent(lab)}/snapshots/${s.name}`, { method: 'DELETE' }))
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="copy" title={t('interop.snapTitle')} sub={t('interop.snapSub')} onClose={onClose} />
        <form className="snap-new" onSubmit={create}>
          <input required placeholder={t('interop.snapNamePh')} pattern="[A-Za-z][A-Za-z0-9_\-]{0,31}" value={name} onChange={(e) => setName(e.target.value)} />
          <input placeholder={t('interop.snapDescPh')} value={desc} onChange={(e) => setDesc(e.target.value)} />
          <button className="primary" disabled={!!busy}>{busy === 'create' ? <span className="spin" /> : <Ui name="plus" size={15} />} {t('interop.snapCreate')}</button>
        </form>
        <p className="hint">{t('interop.snapHint')}</p>
        <div className="snap-list">
          {list === null ? <span className="spin" /> : list.length === 0 ? <p className="muted">{t('interop.snapNone')}</p> : list.map((s, i) => (
            <div key={s.name} className="snap-row" style={{ '--i': i }}>
              <span className="snap-dot" />
              <div className="grow">
                <b>{s.name}</b> <span className="muted small">{fmtWhen(s.created)} · {t('interop.snapMeta', { n: s.nodes, disks: s.disks.length })}</span>
                {s.description && <div className="small">{s.description}</div>}
              </div>
              <button className="mini" disabled={!!busy} onClick={() => restore(s)}>{busy === s.name ? <span className="spin" /> : <Ui name="refresh" size={13} />} {t('interop.restore')}</button>
              <button className="mini danger" disabled={!!busy} onClick={() => remove(s)} title={t('common.delete')}><Ui name="trash" size={13} /></button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
