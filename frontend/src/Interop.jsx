// Import from containerlab / CML / GNS3 / EVE-NG-PNETLab, export, and lab snapshots.
import { useEffect, useRef, useState } from 'react'
import { api, apiUrl } from './api.js'
import Icon, { Ui } from './icons.jsx'
import { ModalHead } from './LabView.jsx'

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
    const f = { filename: 'incollato.yml', data: toBase64(new TextEncoder().encode(text)) }
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
  const tplByName = Object.fromEntries(templates.map((t) => [t.name, t]))

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal xl import-dlg" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="upload" title="Importa un lab" sub={`da containerlab, CML, GNS3, EVE-NG/PNETLab o NetSO · in ${folder || 'root'}`} onClose={onClose} />

        {!preview ? (
          <>
            <div className={`drop ${drag ? 'on' : ''} ${busy ? 'busy' : ''}`} onClick={() => input.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); load(e.dataTransfer.files[0]) }}>
              {busy ? <><span className="spin" /><p>Analisi del file…</p></> : <>
                <Ui name="upload" size={34} />
                <p><b>Trascina qui il file del lab</b> oppure fai clic per sceglierlo</p>
                <div className="src-chips">{SOURCES.map(([n, ext]) => <span key={n}><b>{n}</b> {ext}</span>)}</div>
              </>}
              <input ref={input} type="file" hidden accept=".yml,.yaml,.gns3,.gns3project,.zip,.unl,.json,.xml" onChange={(e) => load(e.target.files[0])} />
            </div>
            <button className="ghost paste-tgl" onClick={() => setPaste((v) => !v)}><Ui name="code" size={14} /> {paste ? 'Nascondi' : 'Oppure incolla il testo del file'}</button>
            {paste && <>
              <textarea className="code" style={{ minHeight: 200 }} placeholder="Incolla qui un lab containerlab, CML o NetSO (YAML)…" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
              <div className="row-end"><button className="primary" disabled={!text.trim() || busy} onClick={loadText}>Analizza</button></div>
            </>}
          </>
        ) : (
          <>
            <div className="imp-head">
              <span className={`fmt-badge f-${preview.format}`}>{preview.format_label}</span>
              <span className="muted small">{file.filename} · {preview.nodes.length} nodi · {preview.links} collegamenti{preview.configs.length ? ` · ${preview.configs.length} config` : ''}</span>
              <div className="spacer" />
              <button className="ghost mini" onClick={() => { setPreview(null); setFile(null) }}>Cambia file</button>
            </div>
            <label className="field"><span>Nome del lab in NetSO</span>
              <input value={name} pattern="[A-Za-z][A-Za-z0-9_\-]{0,31}" onChange={(e) => setName(e.target.value)} className={preview.exists ? 'bad' : ''} />
              {preview.exists && <small className="err-t">Esiste già un lab con questo nome: cambialo per importare.</small>}
            </label>
            <div className="tbl-wrap imp-tbl">
              <table className="tbl">
                <thead><tr><th>Nodo</th><th>Nel file originale</th><th>Diventa in NetSO</th><th>Immagine</th></tr></thead>
                <tbody>
                  {preview.nodes.map((n) => (
                    <tr key={n.name}>
                      <td><span className="dev-cell"><Icon name={tplByName[n.template]?.icon} size={22} /><b>{n.name}</b></span></td>
                      <td className="muted small mono">{n.source}</td>
                      <td>
                        <select className="cell" value={n.template} onChange={(e) => setTpl(n.name, e.target.value)} disabled={busy || preview.format === 'netso'}>
                          {templates.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                        </select>
                      </td>
                      <td className="mono small">{n.image || <span className="muted">del template</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {preview.warnings.length > 0 && (
              <details className="imp-warn" open={preview.warnings.length <= 4}>
                <summary>{preview.warnings.length} avvisi</summary>
                <ul>{preview.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </details>
            )}
            <p className="hint">Le immagini non vengono importate: le VM useranno le immagini presenti sul server (stesso nome della cartella, come in EVE-NG).</p>
            <div className="row-end">
              <button className="ghost" onClick={onClose}>Annulla</button>
              <button className="primary" disabled={busy || preview.exists || !name} onClick={doImport}>{busy ? <span className="spin" /> : <Ui name="upload" size={15} />} Importa</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

const fmtWhen = (t) => new Date(t * 1000).toLocaleString('it-IT', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })

export function SnapshotsDialog({ lab, dirty, onSave, onRestored, onClose, onError }) {
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
    if (!confirm(`Ripristinare "${s.name}"? Topologia, startup config e dischi delle VM tornano come al ${fmtWhen(s.created)}. Le modifiche successive andranno perse.`)) return
    act(s.name, async () => { await api(`/labs/${encodeURIComponent(lab)}/snapshots/${s.name}/restore`, { method: 'POST' }); onRestored() })
  }
  const remove = (s) => confirm(`Eliminare l'istantanea "${s.name}"?`) && act(s.name, () => api(`/labs/${encodeURIComponent(lab)}/snapshots/${s.name}`, { method: 'DELETE' }))
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="copy" title="Istantanee del lab" sub="Topologia, startup config e dischi delle VM in un momento preciso" onClose={onClose} />
        <form className="snap-new" onSubmit={create}>
          <input required placeholder="Nome (es. base, ospf-ok)" pattern="[A-Za-z][A-Za-z0-9_\-]{0,31}" value={name} onChange={(e) => setName(e.target.value)} />
          <input placeholder="Descrizione (facoltativa)" value={desc} onChange={(e) => setDesc(e.target.value)} />
          <button className="primary" disabled={!!busy}>{busy === 'create' ? <span className="spin" /> : <Ui name="plus" size={15} />} Crea istantanea</button>
        </form>
        <p className="hint">Le VM devono essere spente. I container ripartono dalla loro immagine: il loro stato interno non viene salvato.</p>
        <div className="snap-list">
          {list === null ? <span className="spin" /> : list.length === 0 ? <p className="muted">Nessuna istantanea ancora.</p> : list.map((s, i) => (
            <div key={s.name} className="snap-row" style={{ '--i': i }}>
              <span className="snap-dot" />
              <div className="grow">
                <b>{s.name}</b> <span className="muted small">{fmtWhen(s.created)} · {s.nodes} nodi · {s.disks.length} dischi VM</span>
                {s.description && <div className="small">{s.description}</div>}
              </div>
              <button className="mini" disabled={!!busy} onClick={() => restore(s)}>{busy === s.name ? <span className="spin" /> : <Ui name="refresh" size={13} />} Ripristina</button>
              <button className="mini danger" disabled={!!busy} onClick={() => remove(s)} title="Elimina"><Ui name="trash" size={13} /></button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
