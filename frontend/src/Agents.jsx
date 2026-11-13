// NetSO Assistant: a floating chat with your configured LLM, optionally grounded on the current lab/node.
// Providers (Anthropic, OpenAI, Google, Perplexity, Ollama, Hermes…) are configured in Settings, not here.
import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import { t, useLang } from './i18n.js'
import { Ui } from './icons.jsx'
import { ModalHead } from './LabView.jsx'

export function AgentFab({ lab }) {
  useLang()
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState(() => {
    try { const s = JSON.parse(localStorage.getItem('netso.agentFab') || 'null'); if (s && typeof s.x === 'number') return s } catch { /* ignore */ }
    return null // null = default spot from CSS (bottom-right)
  })
  // drag to move it anywhere (so it never covers a button); a plain click still opens the chat
  const onDown = (e) => {
    if (e.button !== 0) return
    e.preventDefault()
    const r = e.currentTarget.getBoundingClientRect()
    const s = { px: e.clientX, py: e.clientY, dx: e.clientX - r.left, dy: e.clientY - r.top, w: r.width, h: r.height, moved: false }
    const move = (ev) => {
      if (Math.hypot(ev.clientX - s.px, ev.clientY - s.py) > 3) s.moved = true
      setPos({ x: Math.max(8, Math.min(ev.clientX - s.dx, innerWidth - s.w - 8)),
               y: Math.max(8, Math.min(ev.clientY - s.dy, innerHeight - s.h - 8)) })
    }
    const up = () => {
      removeEventListener('mousemove', move); removeEventListener('mouseup', up)
      if (s.moved) setPos((p) => { try { localStorage.setItem('netso.agentFab', JSON.stringify(p)) } catch { /* ignore */ } return p })
      else setOpen((v) => !v)
    }
    addEventListener('mousemove', move); addEventListener('mouseup', up)
  }
  const style = pos ? { left: pos.x, top: pos.y, right: 'auto', bottom: 'auto' } : undefined
  return (
    <>
      <button className={`agent-fab ${open ? 'on' : ''}`} title={t('agents.fabTitle')} style={style} onMouseDown={onDown}>
        <Ui name="nerd" size={24} />
      </button>
      {open && <ChatPanel lab={lab} onClose={() => setOpen(false)} />}
    </>
  )
}

function ChatPanel({ lab, onClose }) {
  useLang()
  const chatKey = `netso.chat.${lab || '_'}`
  const readPref = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v) } catch { return d } }
  const [agents, setAgents] = useState([])
  const [agent, setAgent] = useState(() => readPref('netso.chat.agent', ''))
  // {role:'user'|'assistant', text, transcript?} — kept in localStorage so the chat survives a refresh/close
  const [messages, setMessages] = useState(() => readPref(chatKey, []))
  const [input, setInput] = useState('')
  const [useCtx, setUseCtx] = useState(() => readPref('netso.chat.ctx', !!lab))
  const [live, setLive] = useState(() => readPref('netso.chat.live', !!lab))   // default ON when a lab is open
  const [node, setNode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [cfgOpen, setCfgOpen] = useState(false)
  const [applied, setApplied] = useState({})   // `${msg}:${proposal}` -> {busy|done|error|output|backup}
  const scroll = useRef(null)
  useEffect(() => { api('/agents').then((d) => setAgents(d.agents.filter((a) => a.enabled))).catch((e) => setErr(e.message)) }, [])
  useEffect(() => { if (agents.length && !agents.some((a) => a.id === agent)) setAgent(agents[0].id) }, [agents, agent])
  useEffect(() => { scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' }) }, [messages, busy])
  // remember the chat and the choices across refresh/close
  useEffect(() => { try { localStorage.setItem(chatKey, JSON.stringify(messages.slice(-40))) } catch { /* ignore */ } }, [messages, chatKey])
  useEffect(() => { try { localStorage.setItem('netso.chat.live', JSON.stringify(live)) } catch { /* ignore */ } }, [live])
  useEffect(() => { try { localStorage.setItem('netso.chat.ctx', JSON.stringify(useCtx)) } catch { /* ignore */ } }, [useCtx])
  useEffect(() => { if (agent) try { localStorage.setItem('netso.chat.agent', JSON.stringify(agent)) } catch { /* ignore */ } }, [agent])
  const curAgent = agents.find((a) => a.id === agent)

  const send = async (e) => {
    e?.preventDefault()
    const q = input.trim()
    if (!agent || !q || busy) return
    const history = messages.map((m) => ({ role: m.role, text: m.text }))
    setMessages((m) => [...m, { role: 'user', text: q }])
    setInput(''); setBusy(true); setErr(null)
    try {
      const body = { prompt: q, history, ...(useCtx && lab ? { lab, node: node || undefined, live } : {}) }
      const r = await api(`/agents/${agent}/ask`, { method: 'POST', body })
      setMessages((m) => [...m, { role: 'assistant', text: r.text || t('agents.noReply'), transcript: r.transcript || [], proposals: r.proposals || [] }])
    } catch (e2) { setErr(e2.message) } finally { setBusy(false) }
  }

  // apply a config proposal on the node (the user clicked «Applica»); the server backs up the running-config first
  const applyProposal = async (key, p) => {
    if (!lab || applied[key]?.busy || applied[key]?.done) return
    setApplied((s) => ({ ...s, [key]: { busy: true } }))
    try {
      const r = await api(`/labs/${encodeURIComponent(lab)}/nodes/${encodeURIComponent(p.node)}/config`, { method: 'POST', body: { lines: p.lines } })
      setApplied((s) => ({ ...s, [key]: { done: true, output: r.output, backup: r.backup } }))
    } catch (e2) { setApplied((s) => ({ ...s, [key]: { error: e2.message } })) }
  }

  return (
    <div className="agent-panel chat">
      <div className="agent-head">
        <Ui name="nerd" size={18} /><b className="grow">NetSO Assistant</b>
        {messages.length > 0 && <button className="icon-btn" title={t('agents.newConversation')} onClick={() => { setMessages([]); try { localStorage.removeItem(chatKey) } catch { /* ignore */ } }}><Ui name="refresh" size={15} /></button>}
        <button className="icon-btn" title={t('common.close')} onClick={onClose}><Ui name="close" size={15} /></button>
      </div>

      {!agents.length
        ? <div className="chat-scroll"><p className="hint">{t('agents.noProviderHintPre')}<b>{t('agents.noProviderHintPath')}</b>{t('agents.noProviderHintPost')}</p></div>
        : <>
          <div className="chat-bar">
            <select value={agent} onChange={(e) => setAgent(e.target.value)}>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.model}</option>)}
            </select>
            {lab && <button type="button" className={`chat-cfg-btn ${useCtx ? 'on' : ''}`} title={t('agents.labContext')} onClick={() => setCfgOpen((v) => !v)}>
              <Ui name="config" size={14} />{useCtx ? (live ? 'lab + live' : 'lab') : t('agents.generic')}</button>}
          </div>
          {lab && cfgOpen && <div className="chat-cfg">
            <label className="check small"><input type="checkbox" checked={useCtx} onChange={(e) => setUseCtx(e.target.checked)} /> {t('agents.watchLabPre')}<code>{lab}</code>{t('agents.watchLabPost')}</label>
            {useCtx && <label className="check small"><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> {t('agents.liveDiag')}</label>}
            {useCtx && <input placeholder={t('agents.stuckNode')} value={node} onChange={(e) => setNode(e.target.value)} />}
          </div>}

          <div className="chat-scroll" ref={scroll}>
            {messages.length === 0 && <div className="chat-empty">
              <Ui name="nerd" size={30} />
              <p>{lab ? <>{t('agents.emptyLabPre')}<code>{lab}</code>{t('agents.emptyLabPost')}</> : t('agents.emptyNoLab')}</p>
            </div>}
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                {m.role === 'assistant' && m.transcript?.length > 0 && <details className="agent-diag">
                  <summary>{m.transcript.length === 1 ? t('agents.cmdRunOne') : t('agents.cmdRunMany', { n: m.transcript.length })}</summary>
                  {m.transcript.map((tr, j) => (
                    <div key={j} className="diag-cmd"><div className="diag-line"><b>{tr.node}</b> <code>{tr.cmd}</code></div><pre>{tr.output}</pre></div>
                  ))}
                </details>}
                <div className="bubble">{m.text}
                  {m.role === 'assistant' && <button className="bubble-copy" title={t('agents.copy')} onClick={() => navigator.clipboard?.writeText(m.text)}><Ui name="copy" size={13} /></button>}
                </div>
                {m.role === 'assistant' && m.proposals?.map((p, pi) => {
                  const key = `${i}:${pi}`, st = applied[key] || {}
                  return (
                    <div key={pi} className="agent-conf">
                      <div className="conf-head"><Ui name="config" size={13} /> {t('agents.proposedConfigFor')} <b>{p.node}</b></div>
                      <pre>{p.lines.join('\n')}</pre>
                      {st.done
                        ? <div className="conf-ok"><Ui name="check" size={13} /> {t('agents.appliedOn', { node: p.node })}{st.backup ? <small>{t('agents.backupSaved')}</small> : ''}</div>
                        : <button className="primary small conf-apply" disabled={st.busy || !lab} title={lab ? t('agents.applyOn', { node: p.node }) : t('agents.openLabToApply')}
                            onClick={() => applyProposal(key, p)}>
                            {st.busy ? <span className="spin" /> : <><Ui name="deploy" size={13} /> {t('agents.applyOn', { node: p.node })}</>}
                          </button>}
                      {st.error && <div className="conf-err">{st.error}</div>}
                      {st.done && st.output && <details className="conf-out"><summary>output</summary><pre>{st.output}</pre></details>}
                    </div>
                  )
                })}
              </div>
            ))}
            {busy && <div className="msg assistant"><div className="bubble typing"><span /><span /><span /></div></div>}
          </div>

          {err && <div className="agent-err" onClick={() => setErr(null)}>{err}</div>}
          <form className="chat-input" onSubmit={send}>
            <textarea rows={1} value={input} placeholder={lab ? t('agents.writeOnLab', { lab }) : t('agents.writeMessage')}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(e) } }} />
            <button className="primary send" type="submit" disabled={busy || !input.trim()} title={t('agents.send')}>
              {busy ? <span className="spin" /> : <Ui name="deploy" size={16} />}
            </button>
          </form>
        </>}
    </div>
  )
}

// ---- Settings dialog: manage the LLM providers (opened from Sistema → NetSO Assistant)
export function AgentsSettingsDialog({ onClose, onError }) {
  useLang()
  const [data, setData] = useState({ agents: [], kinds: [] })
  const [edit, setEdit] = useState(null)
  const load = () => api('/agents').then(setData).catch((e) => onError?.(e))
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  const err = (e) => onError?.(e)
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="nerd" title={t('agents.dialogTitle')} sub={t('agents.dialogSub')} onClose={onClose} />
        {edit
          ? <AgentForm agent={edit} kinds={data.kinds} onCancel={() => setEdit(null)} onSaved={() => { setEdit(null); load() }} onError={err} />
          : <AgentsTab data={data} onAdd={() => setEdit({})} onEdit={setEdit} onReload={load} onError={err} />}
      </div>
    </div>
  )
}

function AgentsTab({ data, onAdd, onEdit, onReload, onError }) {
  useLang()
  const kindLabel = (k) => data.kinds.find((x) => x.id === k)?.label || k
  const toggle = async (a) => { try { await api('/agents', { method: 'POST', body: { ...a, enabled: !a.enabled } }); onReload() } catch (e) { onError(e) } }
  const del = async (a) => { if (confirm(t('agents.confirmDelete', { name: a.name }))) { try { await api(`/agents/${a.id}`, { method: 'DELETE' }); onReload() } catch (e) { onError(e) } } }
  return (
    <div className="agent-body">
      <div className="agent-list">
        {data.agents.map((a) => (
          <div key={a.id} className={`agent-row ${a.enabled ? '' : 'off'}`}>
            <label className="sw" title={a.enabled ? t('agents.active') : t('agents.inactive')}><input type="checkbox" checked={a.enabled} onChange={() => toggle(a)} /><span /></label>
            <span className="grow"><b>{a.name}</b><small className="muted">{kindLabel(a.kind)}{a.auth === 'cli' ? ` · ${a.command}` : a.model ? ` · ${a.model}` : ''}{a.auth === 'key' && !a.has_key ? t('agents.noKey') : ''}</small></span>
            <button className="icon-btn" title={t('agents.edit')} onClick={() => onEdit(a)}><Ui name="edit" size={14} /></button>
            <button className="icon-btn" title={t('common.delete')} onClick={() => del(a)}><Ui name="trash" size={14} /></button>
          </div>
        ))}
        {!data.agents.length && <p className="hint">{t('agents.noProviders')}</p>}
      </div>
      <button className="primary full" onClick={onAdd}><Ui name="plus" size={15} /> {t('agents.addProvider')}</button>
    </div>
  )
}

function AgentForm({ agent, kinds, onCancel, onSaved, onError }) {
  useLang()
  const isNew = !agent.id
  const [f, setF] = useState(() => ({
    id: agent.id, name: agent.name || '', kind: agent.kind || 'anthropic',
    base_url: agent.base_url || '', model: agent.model || '', command: agent.command || '', api_key: '', enabled: agent.enabled ?? true,
  }))
  const set = (k) => (e) => setF((o) => ({ ...o, [k]: e.target ? (e.target.type === 'checkbox' ? e.target.checked : e.target.value) : e }))
  const kind = kinds.find((k) => k.id === f.kind) || {}
  const auth = kind.auth || 'key'
  const save = async (e) => {
    e.preventDefault()
    try { await api('/agents', { method: 'POST', body: f }); onSaved() } catch (ex) { onError(ex) }
  }
  return (
    <form className="agent-body" onSubmit={save}>
      <label className="field"><span>{t('agents.name')}</span><input required autoFocus value={f.name} onChange={set('name')} placeholder={t('agents.namePlaceholder')} /></label>
      <label className="field"><span>{t('agents.type')}</span>
        <select value={f.kind} onChange={set('kind')}>{kinds.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}</select></label>
      {auth === 'cli'
        ? <>
          <label className="field"><span>{t('agents.command')}</span>
            <input value={f.command} onChange={set('command')} placeholder={kind.command || 'es. claude -p'} /></label>
          <p className="hint">{t('agents.cmdHint1')}<b>{t('agents.cmdHintBold')}</b>{t('agents.cmdHint3')}<code>{'{prompt}'}</code>{t('agents.cmdHint5')}<code>claude -p</code>{t('agents.cmdHint7')}<code>gemini -p {'{prompt}'}</code>, <code>ollama run llama3</code>.</p>
        </>
        : <>
          <div className="grid2">
            <label className="field"><span>{t('agents.model')}</span><input value={f.model} onChange={set('model')} placeholder={kind.model || ''} /></label>
            <label className="field"><span>{t('agents.baseUrl')}</span><input value={f.base_url} onChange={set('base_url')} placeholder={kind.base || ''} /></label>
          </div>
          <label className="field"><span>{t('agents.apiKey')} {auth !== 'key' && <small className="muted">{t('agents.notRequired')}</small>}</span>
            <input type="password" value={f.api_key} onChange={set('api_key')} placeholder={agent.has_key ? t('agents.keySaved') : (auth === 'key' ? t('agents.pasteKey') : t('agents.optional'))} /></label>
        </>}
      <div className="row-end">
        <button type="button" className="ghost" onClick={onCancel}>{t('common.cancel')}</button>
        <button className="primary" type="submit">{isNew ? t('agents.add') : t('common.save')}</button>
      </div>
    </form>
  )
}
