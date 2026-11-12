import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow, ReactFlowProvider, Background, BackgroundVariant, Controls, MiniMap, Handle, Position, NodeToolbar, NodeResizer,
  applyNodeChanges, useReactFlow, ConnectionMode, BaseEdge, EdgeLabelRenderer, useInternalNode,
} from '@xyflow/react'
import { CONSOLES, NIC_MODELS, api, apiUrl, byTitle, consoleLabel, interfaceNames, splitEp, tplTitle, uploadTo, wsUrl } from './api.js'
import Icon, { DEVICE_COLORS, LINE_ICONS, Ui, lineLabel, setIconStyle, useIconStyle } from './icons.jsx'
import { THEMES, isLight, setTheme, useTheme } from './theme.js'
import Console, { ConsoleWindows, POPUP_KINDS, consoleKind, openConsoleWindow } from './Console.jsx'
import { WiresharkWindows } from './Wireshark.jsx'
import { EXPORT_FORMATS, SnapshotsDialog, downloadExport } from './Interop.jsx'
import { GenerateDialog, InventoryDialog, SaveConfigsDialog, autoLayout } from './Automation.jsx'

const PREFIX = { router: 'r', switch: 'sw', l3switch: 'sw', bridge: 'br', firewall: 'fw', server: 'srv', host: 'pc', container: 'ct', cloud: 'net' }
const KINDS = ['qemu', 'dynamips', 'iol', 'docker', 'bridge', 'external', 'nat', 'vxlan']
const SHARED = ['bridge', 'external', 'nat', 'vxlan']
const KIND_LABEL = { qemu: 'Macchine virtuali', dynamips: 'Router classici (Dynamips)', iol: 'Cisco IOL', docker: 'Container', bridge: 'Switch', external: 'Rete esterna', nat: 'Internet (NAT)', vxlan: 'Verso altro server (VXLAN)' }
const ACTION_LABEL = { start: 'Avvio', stop: 'Arresto', wipe: 'Azzeramento' }
const NAME_RE = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/
const NODE_NAME_RE = /^[A-Za-z][A-Za-z0-9 _-]{0,31}$/ // node names may contain single spaces

// Handlers shared with the canvas nodes (toolbar) without threading them through node data.
const NodeActions = createContext({})

const isVm = (tpl) => !!tpl && ['qemu', 'docker', 'dynamips', 'iol'].includes(tpl.kind)
const hasDisks = (tpl) => ['qemu', 'dynamips', 'iol'].includes(tpl?.kind)

function canDo(tpl, ns, action) {
  const running = ns?.status === 'running'
  if (action === 'start') return isVm(tpl) && !running
  if (action === 'stop') return isVm(tpl) && running
  if (action === 'wipe') return hasDisks(tpl) && !running
  if (action === 'console') return running && !!(ns?.console_port || ns?.container || (ns?.kind === 'qemu' && consoleKind(ns) !== 'telnet'))
  return false
}

const usedIfaces = (lab, node) => new Set(lab.links.flatMap((x) => x.endpoints).map(splitEp).filter(([n]) => n === node).map(([, i]) => i))

function freeName(lab, prefix, taken = new Set()) {
  let i = 1
  while (lab.nodes.some((n) => n.name === `${prefix}${i}`) || taken.has(`${prefix}${i}`)) i++
  return `${prefix}${i}`
}

function condSummary(l) {
  const p = []
  if (l.delay_ms != null) p.push(`${l.delay_ms}ms`)
  if (l.jitter_ms != null) p.push(`±${l.jitter_ms}`)
  if (l.loss_pct != null) p.push(`${l.loss_pct}%`)
  if (l.rate_kbit != null) p.push(`${l.rate_kbit}k`)
  return p.join(' ')
}

// ------------------------------------------------------------ canvas node
function DeviceNode({ data, selected }) {
  const act = useContext(NodeActions)
  const iconStyle = useIconStyle()
  const [hover, setHover] = useState(false)
  const symbol = iconStyle === 'line' || data.icon?.startsWith('line:')
  const classic = iconStyle === 'classic' || symbol
  const k = data.size / 100
  const st = data.status || 'stopped'
  const running = st === 'running'
  const stLabel = running ? 'in esecuzione' : st === 'error' ? 'errore' : 'fermo'
  return (
    <div className={`devnode ${selected ? 'sel' : ''} st-${st} ${classic ? 'classic' : ''} ${data.pending ? `pend-${data.pending}` : ''}`}
      title={data.error || data.description || data.name} style={{ '--k': k, width: Math.max(86, 58 * k + 28) }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      {/* quick actions at hover / when selected: start-stop, console, edit */}
      <NodeToolbar isVisible={(hover || selected) && !act.multi} position={Position.Top} offset={10}>
        <div className="node-tb">
          {running
            ? <TbBtn title="Ferma" icon="stop" disabled={!data.vm} onClick={() => act.onAction?.(data.name, 'stop')} />
            : <TbBtn title="Avvia" icon="play" cls="go" disabled={!data.vm} onClick={() => act.onAction?.(data.name, 'start')} />}
          <TbBtn title="Consolle" icon="console" disabled={!(running && data.hasConsole)} onClick={() => act.onConsole?.(data.name)} />
          <TbBtn title="Modifica" icon="edit" onClick={() => act.onEdit?.(data.name)} />
        </div>
      </NodeToolbar>
      {/* EVE-NG style plug: appears on hover, drag it onto another node to connect */}
      <Handle type="source" position={Position.Top} className="hdl plug" title="Trascina su un altro nodo per collegarlo">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M8.5 3.5h7v4h1.8v5.2a2 2 0 0 1-2 2h-1.8v6h-3v-6H8.7a2 2 0 0 1-2-2V7.5h1.8z" fill="currentColor" /><path d="M10.3 3.5v2.4M12 3.5v2.4M13.7 3.5v2.4" stroke="#3a1606" strokeWidth="1.1" strokeLinecap="round" /></svg>
      </Handle>
      {/* while a link is being drawn the whole device accepts the drop, not just the small dot */}
      <Handle type="target" id="drop" position={Position.Top} className="hdl-drop" isConnectableStart={false} />
      <div className="chip">
        <Icon name={data.icon} size={Math.round((symbol ? 50 : classic ? 66 : 36) * k)} />
        {data.pending && <span className="boot-ring" />}
      </div>
      {/* EVE-NG style status next to the name: red square = off, green play = on */}
      <div className="lbl"><StatusMark st={data.pending ? 'pending' : st} title={data.pending === 'start' ? 'in avvio' : data.pending === 'stop' ? 'in arresto' : stLabel} />{data.name}{data.pinned && <Ui name="lock" size={10} />}</div>
    </div>
  )
}

function StatusMark({ st, title }) {
  return (
    <svg className={`stmark ${st}`} viewBox="0 0 12 12" width="12" height="12" aria-label={title}><title>{title}</title>
      {st === 'pending' ? <><circle cx="6" cy="6" r="4.6" className="pend-track" /><path d="M6 1.4a4.6 4.6 0 0 1 4.6 4.6" className="pend-arc" /></>
        : st === 'running' ? <path d="M2.5 1.2v9.6a.6.6 0 0 0 .9.5l7.6-4.8a.6.6 0 0 0 0-1L3.4.7a.6.6 0 0 0-.9.5z" />
        : st === 'error' ? <><rect x="1" y="1" width="10" height="10" rx="1.6" /><path d="M6 3.2v3.6M6 8.6v.1" className="bang" /></>
        : <rect x="1.2" y="1.2" width="9.6" height="9.6" rx="1.4" />}
    </svg>
  )
}

function TbBtn({ title, icon, onClick, cls = '', disabled }) {
  return (
    <button className={cls} title={title} disabled={disabled}
      onClick={(e) => { e.stopPropagation(); onClick?.() }}><Ui name={icon} size={16} /></button>
  )
}

// unique id for a console instance (several consoles can be open for the same node)
const cid = () => 'c' + Math.random().toString(36).slice(2, 9)

// ------------------------------------------------------------ drawings (annotations)
export const ANN = 'ann:'
export const ANN_COLORS = ['#e3e7f4', '#1a2032', '#8b7bff', '#5f8bff', '#38bdf8', '#34d399', '#a3e635', '#fbbf24', '#fb923c', '#f87171', '#f472b6']
const ANN_DEFAULTS = {
  text: { w: 200, h: 40, text: 'Testo', fill: null, border: 'none', font_size: 16, bold: true },
  rect: { w: 320, h: 200, fill: '#8b7bff', opacity: 0.12, color: '#8b7bff', border: 'dashed', radius: 14, text: '' },
  ellipse: { w: 260, h: 160, fill: '#38bdf8', opacity: 0.12, color: '#38bdf8', border: 'solid', text: '' },
  line: { w: 180, h: 2, color: '#fbbf24', border_width: 3, arrow: 'end' },
  image: { w: 240, h: 160 },
}
const ANN_BASE = { text: '', color: '#e3e7f4', fill: null, border: 'solid', border_width: 2, radius: 8, font_size: 14, bold: false, italic: false, align: 'center', opacity: 1, arrow: 'end', flip: false, image: null, z: -1, w: 160, h: 80 }
export const ANN_TYPES = [['text', 'Testo', 'edit'], ['rect', 'Rettangolo', 'stop'], ['ellipse', 'Ellisse', 'info'], ['line', 'Freccia', 'chev'], ['image', 'Immagine', 'save']]

function AnnArrow({ id, color }) {
  return (
    <marker id={id} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill={color} />
    </marker>
  )
}

function NoteNode({ id, data: a, selected }) {
  const act = useContext(NodeActions)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(a.text)
  useEffect(() => { if (!editing) setDraft(a.text) }, [a.text, editing])
  const dash = a.border === 'dashed' ? `${a.border_width * 4} ${a.border_width * 2.5}` : a.border === 'dotted' ? `0.1 ${a.border_width * 2.6}` : undefined
  const boxStyle = {
    background: a.fill ? `color-mix(in srgb, ${a.fill} ${Math.round(a.opacity * 100)}%, transparent)` : 'transparent',
    border: a.border === 'none' ? 'none' : `${a.border_width}px ${a.border} ${a.color}`,
    borderRadius: a.type === 'ellipse' ? '50%' : a.radius,
  }
  const text = (
    <div className={`ann-text al-${a.align}`} style={{ color: a.type === 'text' ? a.color : 'var(--text)', fontSize: a.font_size, fontWeight: a.bold ? 700 : 400, fontStyle: a.italic ? 'italic' : 'normal' }}>
      {editing
        ? <textarea className="nodrag nowheel" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
          onBlur={() => { setEditing(false); if (draft !== a.text) act.onAnn?.(a.id, { text: draft }) }}
          onKeyDown={(e) => { if (e.key === 'Escape') { setDraft(a.text); setEditing(false) } e.stopPropagation() }}
          style={{ fontSize: a.font_size, fontWeight: a.bold ? 700 : 400, textAlign: a.align }} />
        : a.text}
    </div>
  )
  const canEdit = ['text', 'rect', 'ellipse'].includes(a.type)
  return (
    <div className={`ann ann-${a.type} ${selected ? 'sel' : ''}`} onDoubleClick={(e) => { if (canEdit && !act.locked) { e.stopPropagation(); setEditing(true) } }}>
      <NodeResizer isVisible={selected && !act.locked} minWidth={20} minHeight={a.type === 'line' ? 2 : 20} lineClassName="ann-rz-line" handleClassName="ann-rz" />
      {a.type === 'line' ? (
        <svg width="100%" height="100%" style={{ overflow: 'visible' }}>
          <defs><AnnArrow id={`arw-${id}`} color={a.color} /></defs>
          <line x1={a.flip ? '100%' : '0'} y1="0" x2={a.flip ? '0' : '100%'} y2="100%" stroke={a.color} strokeWidth={a.border_width}
            strokeDasharray={dash} strokeLinecap="round" vectorEffect="non-scaling-stroke"
            markerStart={['start', 'both'].includes(a.arrow) ? `url(#arw-${id})` : undefined}
            markerEnd={['end', 'both'].includes(a.arrow) ? `url(#arw-${id})` : undefined} />
          {a.h < 6 && <line x1="0" y1="50%" x2="100%" y2="50%" stroke={a.color} strokeWidth={a.border_width} strokeDasharray={dash} strokeLinecap="round"
            markerStart={['start', 'both'].includes(a.arrow) ? `url(#arw-${id})` : undefined} markerEnd={['end', 'both'].includes(a.arrow) ? `url(#arw-${id})` : undefined} />}
        </svg>
      ) : a.type === 'image' ? (
        a.image ? <img src={a.image} alt="" draggable={false} style={{ opacity: a.opacity }} /> : <div className="ann-noimg"><Ui name="save" size={22} /> Immagine</div>
      ) : (
        <div className="ann-box" style={boxStyle}>{(a.text || editing) && text}</div>
      )}
    </div>
  )
}

const nodeTypes = { device: DeviceNode, note: NoteNode }

// Link between icon centres (straight, curved or elbow), with the interface name next to each end (EVE-NG style)
const CHIP_HALF = 29
export const LINK_COLORS = ['#8b93b8', '#8b7bff', '#5f8bff', '#38bdf8', '#34d399', '#a3e635', '#fbbf24', '#fb923c', '#f87171', '#f472b6', '#e2e8f0']

export function linkDash(style, w) {
  if (style === 'dashed') return `${w * 4} ${w * 2.5}`
  if (style === 'dotted') return `0.1 ${w * 2.4}`
  return undefined
}

// geometry of a link: svg path, label anchor, the points where the interface names go and
// the unit direction in which the cable leaves each node (dirs) to place the port LEDs
const unit = (x, y) => { const l = Math.hypot(x, y) || 1; return [x / l, y / l] }
export function linkGeometry(sx, sy, tx, ty, shape, off = 58, bend = null) {
  const len = Math.hypot(tx - sx, ty - sy) || 1
  const ux = (tx - sx) / len, uy = (ty - sy) / len
  if (shape === 'curved') {
    // `bend` is the user's curvature in %, signed (negative bends the other way); null = default arc
    const b = Math.max(-260, Math.min(260, (bend != null ? bend / 100 : 0.28) * len))
    const cx = (sx + tx) / 2 - uy * b, cy = (sy + ty) / 2 + ux * b
    const at = (t) => [(1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * tx, (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * ty]
    const k = Math.min(0.3, off / len)
    return { path: `M${sx},${sy} Q${cx},${cy} ${tx},${ty}`, mid: at(0.5), ends: [at(k), at(1 - k)], len, dirs: [unit(cx - sx, cy - sy), unit(cx - tx, cy - ty)] }
  }
  if (shape === 'elbow') {
    // `bend` (−100..100) slides the turn from one node to the other; null/0 = halfway
    const f = Math.max(0.12, Math.min(0.88, 0.5 + (bend != null ? bend : 0) / 220))
    const mx = sx + (tx - sx) * f, r = Math.min(14, Math.abs(tx - sx) / 2, Math.abs(ty - sy) / 2)
    const dx = Math.sign(tx - sx) || 1, dy = Math.sign(ty - sy) || 1
    const path = r < 1 ? `M${sx},${sy} H${mx} V${ty} H${tx}`
      : `M${sx},${sy} H${mx - dx * r} Q${mx},${sy} ${mx},${sy + dy * r} V${ty - dy * r} Q${mx},${ty} ${mx + dx * r},${ty} H${tx}`
    const e = Math.min(off, Math.max(66, Math.abs(mx - sx) * 0.7)) // never on top of the port LED
    return { path, mid: [mx, (sy + ty) / 2], ends: [[sx + dx * e, sy], [tx - dx * e, ty]], len, dirs: [[dx, 0], [-dx, 0]] }
  }
  return { path: `M${sx},${sy} L${tx},${ty}`, mid: [(sx + tx) / 2, (sy + ty) / 2], ends: [[sx + ux * off, sy + uy * off], [tx - ux * off, ty - uy * off]], len, dirs: [[ux, uy], [-ux, -uy]] }
}

// where the port sits: just outside the square icon tile (half side `half`) in the cable's direction
export function portPoint(cx, cy, [dx, dy], half, gap = 1) {
  // constant distance (circle): a diagonal cable must not throw the port out to the icon's corner
  const d = half + gap
  return [cx + dx * d, cy + dy * d]
}
export const portState = (st) => st === 'running' ? 'up' : st === 'error' ? 'err' : st === 'deploying' ? 'wait' : 'down'
const PORT_TITLE = { up: 'acceso', down: 'spento', err: 'errore', wait: 'in avvio' }

function CenterEdge({ id, source, target, data, selected, interactionWidth }) {
  const s = useInternalNode(source)
  const t = useInternalNode(target)
  const [hv, setHv] = useState(false)
  if (!s || !t) return null
  const half = (n) => CHIP_HALF * (n.data?.size || 100) / 100
  const c = (n) => [n.internals.positionAbsolute.x + (n.measured?.width || 0) / 2, n.internals.positionAbsolute.y + half(n)]
  let [sx, sy] = c(s)
  let [tx, ty] = c(t)
  const len0 = Math.hypot(tx - sx, ty - sy) || 1
  if (data.offset) { // parallel links between the same pair of nodes are spread apart
    const ux = (tx - sx) / len0, uy = (ty - sy) / len0
    const k = data.offset * (source < target ? 1 : -1)
    sx -= uy * k; sy += ux * k; tx -= uy * k; ty += ux * k
  }
  // pull each end in to the icon's edge so the cable touches the glyph, not its centre
  let px = sx, py = sy, qx = tx, qy = ty
  const seg = Math.hypot(tx - sx, ty - sy) || 1
  const inS = half(s) - 6, inT = half(t) - 6
  if (seg > inS + inT + 6) {
    const ex = (tx - sx) / seg, ey = (ty - sy) / seg
    px = sx + ex * inS; py = sy + ey * inS
    qx = tx - ex * inT; qy = ty - ey * inT
  }
  const g = linkGeometry(px, py, qx, qy, data.shape, 46, data.bend)
  // an ethernet port with a status LED at both ends of the cable, sitting ON the icon's edge
  const ports = [[sx, sy, g.dirs[0], data.aSt, data.a, half(s)], [tx, ty, g.dirs[1], data.bSt, data.b, half(t)]].map(([x, y, d, st, ifc, h]) => {
    const [px, py] = portPoint(x, y, d, h - 7)
    return { px, py, ang: Math.atan2(d[1], d[0]) * 180 / Math.PI, st: portState(st), ifc }
  })
  const bothUp = ports.every((p) => p.st === 'up')
  const [mx, my] = g.mid
  const w = data.width || 2
  // capture keeps its own red animated look; otherwise the user's colour / line type
  const style = data.cap ? undefined : {
    ...(data.color ? { stroke: data.color } : {}), strokeWidth: w,
    strokeDasharray: linkDash(data.style, w), strokeLinecap: data.style === 'dotted' ? 'round' : undefined,
  }
  const long = g.len > 150
  // each interface name sits ON its own cable, a little past the port: since every link leaves
  // the node at its own angle the names fan out along the wires and don't pile up
  // on the cable, just past the port; a cable heading DOWN pushes its label a bit further along
  // the wire so it clears the node's name badge (which sits right below the icon) instead of sitting on it
  const lab = (x, y, [dx, dy], h) => { const d = h + 20 + 16 * Math.max(0, dy); return [x + dx * d, y + dy * d] }
  const ifaceLabels = [
    [...lab(sx, sy, g.dirs[0], half(s)), data.a],
    [...lab(tx, ty, g.dirs[1], half(t)), data.b],
  ]
  const showIface = data.ifaceMode !== 'hover' || hv || selected
  const extra = [data.label && ['name', data.label], data.cap && ['capb', '● cattura'], data.cond && long && ['cond', `⏱ ${data.cond}`]].filter(Boolean)
  return (
    <>
      {selected && <path d={g.path} className="edge-halo" style={{ strokeWidth: w + 9 }} />}
      <BaseEdge id={id} path={g.path} interactionWidth={interactionWidth} style={style} />
      <path d={g.path} fill="none" stroke="transparent" strokeWidth={Math.max(16, w + 12)}
        style={{ pointerEvents: 'stroke', cursor: 'pointer' }}
        onMouseEnter={() => setHv(true)} onMouseLeave={() => setHv(false)} />
      {ports.map((p, i) => (
        <g key={i} className={`port st-${p.st} ${bothUp ? 'live' : ''}`} transform={`translate(${p.px} ${p.py}) rotate(${p.ang})`}>
          <title>{`${p.ifc}: ${PORT_TITLE[p.st]}`}</title>
          {/* a small RJ45 plug lying on the cable, its face plugged toward the node (−x), LED on the housing */}
          <path d="M-3 -2.6h5.4l1.6 1v3.2l-1.6 1H-3z" className="port-body" />
          <rect x="-3.2" y="-1.6" width="1.5" height="3.2" rx=".4" className="port-jack" />
          <rect x="1.1" y="-1.9" width="2.2" height="1.3" rx=".5" className="port-led" style={{ animationDelay: `${i * 0.35}s` }} />
        </g>
      ))}
      <EdgeLabelRenderer>
        {showIface && ifaceLabels.map(([x, y, txt], i) => (
          <div key={i} className={`elabel iface ${selected ? 'sel' : ''}`}
            style={{ transform: `translate(-50%,-50%) translate(${x}px,${y}px)` }}>{txt}</div>
        ))}
        {extra.map(([cls, txt], i) => (
          <div key={cls} className={`elabel ${cls}`} style={{
            transform: `translate(-50%,-50%) translate(${mx}px,${my + (long ? 0 : 18) + i * 18}px)`,
            ...(cls === 'name' && data.color ? { borderColor: data.color, color: data.color } : {}),
          }}>{txt}</div>
        ))}
      </EdgeLabelRenderer>
    </>
  )
}
const edgeTypes = { center: CenterEdge }

// ------------------------------------------------------------- lab editor
function Editor({ name, templates, onError, onBack, onReloadTemplates }) {
  const [lab, setLab] = useState(null)
  const [state, setState] = useState(null)
  const [warnings, setWarnings] = useState([])
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState('')
  const [rfNodes, setRfNodes] = useState([])
  const [selected, setSelected] = useState(null) // {type:'node'|'edge', id}
  const consKey = `netso.consoles.${name}`
  // every console is an independent instance {id, node}: you can open as many as you want, even several of the same node.
  // open consoles survive a refresh (restored per lab); instances of stopped nodes are dropped once state loads
  const [consoles, setConsoles] = useState(() => {
    try {
      const raw = JSON.parse(localStorage.getItem(consKey) || '[]')
      return raw.map((x) => (typeof x === 'string' ? { id: cid(), node: x } : x)).filter((x) => x && x.node)
    } catch { return [] }
  })
  const consCleaned = useRef(false)
  useEffect(() => { try { localStorage.setItem(consKey, JSON.stringify(consoles)) } catch { /* ignore */ } }, [consoles, consKey])
  useEffect(() => { if (state && !consCleaned.current) { consCleaned.current = true; setConsoles((c) => c.filter((x) => state.nodes?.[x.node]?.status === 'running')) } }, [state])
  const [dockH, setDockH] = useState(() => { try { return Number(localStorage.getItem('netso.dockH')) || 300 } catch { return 300 } })
  const startDockResize = (e) => {
    e.preventDefault()
    const y0 = e.clientY, h0 = dockH
    const clamp = (h) => Math.max(140, Math.min(innerHeight - 220, h))
    let last = h0
    const move = (ev) => { last = clamp(h0 + (y0 - ev.clientY)); setDockH(last) }
    const up = () => {
      removeEventListener('pointermove', move); removeEventListener('pointerup', up)
      try { localStorage.setItem('netso.dockH', String(last)) } catch { /* ignore */ }
    }
    addEventListener('pointermove', move); addEventListener('pointerup', up)
  }
  const [activeConsole, setActiveConsole] = useState(null)
  const [consoleMode, setConsoleMode] = usePref('netso.consoleMode', 'window') // 'window' = finestre mobili (predefinito) · 'dock' = in basso
  const [consoleTarget, setConsoleTarget] = usePref('netso.consoleTarget', 'browser') // 'browser' = terminale nel browser · 'native' = terminale del PC
  const [yaml, setYaml] = useState(null)
  const [menu, setMenu] = useState(null) // right-click menu {kind:'pane'|'node'|'edge'|'selection', x, y, id?, pos?}
  const [addDlg, setAddDlg] = useState(null) // {pos} "add a new node" dialog
  const [linkDlg, setLinkDlg] = useState(null) // {source, target} interface picker for a new link
  const [listDlg, setListDlg] = useState(null) // 'nodes' | 'links' | 'details' | 'status'
  const [cfgDlg, setCfgDlg] = useState(null) // {node?} startup-config editor
  const [capDlg, setCapDlg] = useState(null) // {node?, iface?} packet capture window
  const [extHelpSeen, setExtHelpSeen] = usePref('netso.extConsoleHelpSeen', false)
  const [showExtHelp, setShowExtHelp] = useState(false)
  const [caps, setCaps] = useState([]) // packet captures of this lab
  const [wsWins, setWsWins] = useState([]) // in-browser Wireshark windows [{session, peer, min}]
  const [wsSessions, setWsSessions] = useState([]) // Wireshark sessions running on the server (any tab)
  const [lockedByUser, setLocked] = useState(false)
  const [access, setAccess] = useState('owner')
  const readOnly = access === 'view'
  const locked = lockedByUser || readOnly
  const [linking, setLinking] = useState(false)
  const [panMode, setPanMode] = usePref('netso.panMode', true) // true = drag pans the sheet; false = drag rubber-band selects
  const iconStyle = useIconStyle()
  const theme = useTheme()
  const [ifaceLabels, setIfaceLabels] = usePref('netso.ifaceLabels', 'hover')
  const [showMap, setShowMap] = usePref('netso.minimap', false)
  const [zoom, setZoom] = useState(1)
  const [pending, setPending] = useState({}) // node -> 'start' | 'stop' while the request runs
  const [editNode, setEditNode] = useState(null)
  const [snapDlg, setSnapDlg] = useState(false)
  const [guideDlg, setGuideDlg] = useState(false)
  const [autoDlg, setAutoDlg] = useState(null) // {kind: 'save'|'inv'|'gen', ...}
  const flow = useReactFlow()
  const wrapper = useRef(null)
  const lastPointer = useRef(null) // last mouse position over the canvas, so new nodes land where you were looking
  const pendingSel = useRef(null) // names to select once freshly added nodes reach the canvas

  const tplByName = useMemo(() => Object.fromEntries(templates.map((t) => [t.name, t])), [templates])
  const running = state && Object.values(state.nodes || {}).some((n) => n.status === 'running')
  const selIds = rfNodes.filter((n) => n.selected && !n.id.startsWith(ANN)).map((n) => n.id)
  const selAnns = rfNodes.filter((n) => n.selected && n.id.startsWith(ANN)).map((n) => n.id.slice(ANN.length))

  const load = useCallback(async () => {
    try {
      const r = await api(`/labs/${name}`)
      setLab(r.lab); setState(r.state); setWarnings(r.warnings); setDirty(false); setAccess(r.access || 'owner')
    } catch (e) { onError(e) }
  }, [name, onError])

  const loadCaps = useCallback(async () => {
    try {
      const [list, ws] = await Promise.all([api(`/labs/${name}/captures`), api(`/labs/${name}/wireshark`)])
      // live Wireshark sessions count as running captures even before their file shows up
      for (const w of ws) if (w.live && !list.some((c) => c.id === w.id)) list.unshift({ ...w, wireshark: true, packets: null, size: 0, format: 'pcapng' })
      setCaps(list); setWsSessions(ws)
    } catch { /* ignore */ }
  }, [name])

  useEffect(() => { load(); loadCaps() }, [load, loadCaps])

  // poll runtime state (and captures, faster while their window is open)
  useEffect(() => {
    const id = setInterval(async () => {
      try { setState((await api(`/labs/${name}`)).state) } catch { /* ignore */ }
      loadCaps()
    }, 4000)
    return () => clearInterval(id)
  }, [name, loadCaps])
  useEffect(() => {
    if (!capDlg) return
    const id = setInterval(loadCaps, 1500)
    return () => clearInterval(id)
  }, [capDlg, loadCaps])

  // derive canvas nodes from the lab definition (keep selection/dragging flags)
  useEffect(() => {
    if (!lab) return
    setRfNodes((prev) => lab.nodes.map((n) => {
      const old = prev.find((p) => p.id === n.name)
      const tpl = tplByName[n.template]
      const ns = state?.nodes?.[n.name]
      return {
        ...(old || {}),
        id: n.name,
        type: 'device',
        position: old?.dragging ? old.position : { x: n.x, y: n.y },
        selected: pendingSel.current ? pendingSel.current.includes(n.name) : old?.selected,
        draggable: !n.pinned,
        data: {
          name: n.name, template: n.template, icon: n.icon || tpl?.icon || 'router',
          kind: tpl?.kind, vm: isVm(tpl), status: ns?.status, error: ns?.error,
          hasConsole: !!(ns?.console_port || ns?.container),
          size: n.size || 100, pinned: !!n.pinned, pending: pending[n.name], description: n.description,
        },
      }
    }).concat((lab.annotations || []).map((a) => {
      const id = ANN + a.id, old = prev.find((p) => p.id === id)
      const full = { ...ANN_BASE, ...a }
      return {
        id, type: 'note', data: full, draggable: !locked, selectable: true, connectable: false,
        position: old?.dragging || old?.resizing ? old.position : { x: a.x, y: a.y },
        width: old?.resizing ? old.width : full.w, height: old?.resizing ? old.height : Math.max(full.h, full.type === 'line' ? 2 : 10),
        zIndex: full.z < 0 ? -1 : 1000, selected: pendingSel.current ? pendingSel.current.includes(id) : old?.selected,
      }
    })))
    pendingSel.current = null
  }, [lab, state, tplByName, pending, locked])

  const edges = useMemo(() => {
    const pair = (l) => l.endpoints.map((e) => splitEp(e)[0]).sort().join('|')
    const total = {}, seen = {}
    for (const l of lab?.links || []) total[pair(l)] = (total[pair(l)] || 0) + 1
    return (lab?.links || []).map((l, i) => {
      const [a, ai] = splitEp(l.endpoints[0])
      const [b, bi] = splitEp(l.endpoints[1])
      const p = pair(l), k = seen[p] = (seen[p] ?? -1) + 1
      const up = state?.nodes?.[a]?.status === 'running' && state?.nodes?.[b]?.status === 'running'
      const cap = caps.some((c) => c.running && l.endpoints.includes(`${c.node}:${c.iface}`))
      const tag = (node, port) => {
        const c = lab.nodes.find((n) => n.name === node)?.ports?.[port]
        return c ? `${port} · ${c.mode === 'trunk' ? `trunk ${c.vlans || 'tutte'}` : `vlan ${c.vlan ?? 1}`}` : port
      }
      return {
        id: `l${i}`, source: a, target: b, type: 'center',
        data: {
          a: tag(a, ai), b: tag(b, bi), cond: condSummary(l), cap, offset: (k - (total[p] - 1) / 2) * 16,
          color: l.color, style: l.style, width: l.width, shape: l.shape, bend: l.bend, label: l.label,
          aSt: state?.nodes?.[a]?.status, bSt: state?.nodes?.[b]?.status, ifaceMode: ifaceLabels,
        },
        className: `${up ? 'edge-up' : ''} ${cap ? 'edge-cap' : ''}`,
        selected: selected?.type === 'edge' && selected.id === `l${i}`,
      }
    })
  }, [lab, state, selected, caps, ifaceLabels])

  const mutate = (fn) => { setLab((l) => { const c = structuredClone(l); fn(c); return c }); setDirty(true) }
  const tplOf = (nodeName) => tplByName[lab?.nodes.find((n) => n.name === nodeName)?.template]

  const onNodesChange = (changes) => {
    setRfNodes((nds) => applyNodeChanges(changes, nds))
    for (const c of changes) {
      const isAnn = c.id?.startsWith(ANN)
      if (c.type === 'position' && c.dragging === false) {
        const n = rfNodes.find((x) => x.id === c.id)
        if (n && isAnn) updateAnn(c.id.slice(ANN.length), { x: Math.round(n.position.x), y: Math.round(n.position.y) })
        else if (n) mutate((l) => { const ln = l.nodes.find((x) => x.name === c.id); ln.x = Math.round(n.position.x); ln.y = Math.round(n.position.y) })
      }
      if (c.type === 'dimensions' && isAnn && c.resizing === false && c.dimensions) {
        const n = rfNodes.find((x) => x.id === c.id)
        updateAnn(c.id.slice(ANN.length), { w: Math.round(c.dimensions.width), h: Math.round(c.dimensions.height), ...(n ? { x: Math.round(n.position.x), y: Math.round(n.position.y) } : {}) })
      }
      if (c.type === 'select' && c.selected) setSelected(isAnn ? { type: 'ann', id: c.id.slice(ANN.length) } : { type: 'node', id: c.id })
    }
  }

  // ---- drawings
  const updateAnn = (id, patch) => mutate((l) => {
    const a = (l.annotations || []).find((x) => x.id === id)
    if (a) for (const [k, v] of Object.entries(patch)) { if (v === undefined) delete a[k]; else a[k] = v }
  })
  const addAnn = (type, pos = viewCenter(), extra = {}) => {
    const used = new Set((lab.annotations || []).map((a) => a.id))
    let k = (lab.annotations || []).length + 1
    while (used.has(`a${k}`)) k++
    const a = { id: `a${k}`, type, x: Math.round(pos.x), y: Math.round(pos.y), ...ANN_DEFAULTS[type], ...extra }
    mutate((l) => { l.annotations = [...(l.annotations || []), a] })
    pendingSel.current = [ANN + a.id]; setSelected({ type: 'ann', id: a.id })
  }
  const pickImage = (pos) => {
    const inp = document.createElement('input')
    inp.type = 'file'; inp.accept = 'image/*'
    inp.onchange = () => {
      const f = inp.files[0]
      if (!f) return
      if (f.size > 2_000_000) return onError(new Error('Immagine troppo grande (max 2 MB)'))
      const rd = new FileReader()
      rd.onload = () => {
        const img = new Image()
        img.onload = () => { const k = Math.min(1, 360 / img.width); addAnn('image', pos, { image: rd.result, w: Math.round(img.width * k), h: Math.round(img.height * k) }) }
        img.src = rd.result
      }
      rd.readAsDataURL(f)
    }
    inp.click()
  }
  const addDrawing = (type, pos) => (type === 'image' ? pickImage(pos) : addAnn(type, pos))
  const removeAnns = (ids) => {
    const del = new Set(ids)
    mutate((l) => { l.annotations = (l.annotations || []).filter((a) => !del.has(a.id)) })
    setSelected((s) => (s?.type === 'ann' && del.has(s.id) ? null : s))
  }
  const duplicateAnn = (id) => {
    const a = (lab.annotations || []).find((x) => x.id === id)
    if (a) addAnn(a.type, { x: a.x + 30, y: a.y + 30 }, { ...a, id: undefined, x: undefined, y: undefined })
  }

  const selectOnly = (ids) => setRfNodes((nds) => nds.map((n) => ({ ...n, selected: ids.includes(n.id) })))
  const clearSelection = () => { setSelected(null); selectOnly([]) }
  const selectAll = () => { selectOnly(lab.nodes.map((n) => n.name)); if (lab.nodes[0]) setSelected({ type: 'node', id: lab.nodes[0].name }) }

  const nextIface = (l, nodeName) => {
    const n = l.nodes.find((x) => x.name === nodeName)
    const used = usedIfaces(l, nodeName)
    return interfaceNames(tplByName[n.template], n).find((i) => !used.has(i))
  }

  const onConnect = ({ source, target }) => {
    if (!source || !target || source === target) return
    if (!nextIface(lab, source) || !nextIface(lab, target)) return onError(new Error(`Nessuna interfaccia libera su ${!nextIface(lab, source) ? source : target}`))
    if (SHARED.includes(tplOf(source)?.kind) && SHARED.includes(tplOf(target)?.kind)) return onError(new Error('Non puoi collegare tra loro due switch/reti (bridge, esterna, NAT)'))
    setLinkDlg({ source, target })
  }

  const viewCenter = () => {
    const r = wrapper.current.getBoundingClientRect()
    return flow.screenToFlowPosition({ x: r.left + r.width / 2 - 43, y: r.top + r.height / 2 - 40 })
  }

  // where a new node should land: the last spot the mouse was over the canvas, else the view centre
  const dropPos = () => {
    const p = lastPointer.current
    return p ? flow.screenToFlowPosition({ x: p.x - 43, y: p.y - 29 }) : viewCenter()
  }

  // opts: {tpl, count, name, image, cpus, ram, nics}
  const addNodes = (opts, pos = viewCenter()) => {
    const tpl = tplByName[opts.tpl]
    const count = Math.max(1, opts.count || 1)
    const taken = new Set()
    const names = []
    for (let k = 0; k < count; k++) {
      const nm = count === 1 && opts.name && !lab.nodes.some((n) => n.name === opts.name)
        ? opts.name
        : freeName(lab, opts.name || PREFIX[tpl.icon] || 'n', taken)
      taken.add(nm); names.push(nm)
    }
    mutate((l) => names.forEach((nm, k) => {
      const node = { name: nm, template: opts.tpl, x: Math.round(pos.x + (k % 5) * 130), y: Math.round(pos.y + Math.floor(k / 5) * 130) }
      for (const f of ['image', 'cpus', 'ram', 'nics', 'console']) if (opts[f] != null && opts[f] !== '') node[f] = opts[f]
      if (node.image === tpl.image) delete node.image  // the default image is implicit
      l.nodes.push(node)
    }))
    pendingSel.current = names
    setSelected({ type: 'node', id: names[names.length - 1] })
  }

  const duplicateNode = (nodeName) => {
    const src = lab.nodes.find((n) => n.name === nodeName)
    const nm = freeName(lab, src.name.replace(/\d+$/, '') || 'n')
    mutate((l) => l.nodes.push({ ...structuredClone(src), name: nm, x: src.x + 60, y: src.y + 60 }))
    pendingSel.current = [nm]; setSelected({ type: 'node', id: nm })
  }

  const removeNodes = (names) => {
    const live = names.filter((n) => state?.nodes?.[n]?.status === 'running')
    const del = new Set(names.filter((n) => !live.includes(n)))
    if (live.length) onError(new Error(`Ferma prima: ${live.join(', ')}`))
    if (!del.size) return
    mutate((l) => {
      l.nodes = l.nodes.filter((n) => !del.has(n.name))
      l.links = l.links.filter((x) => !x.endpoints.some((e) => del.has(splitEp(e)[0])))
    })
    setSelected((s) => (s?.type === 'node' && del.has(s.id) ? null : s))
  }

  const removeLink = (idx) => {
    setSelected(null)
    // hot-unplug: on a running lab the server tears the live wiring down (and saves the lab)
    if (running) return run('Scollego il cavo…', () => api(`/labs/${name}/links/disconnect`, { method: 'POST', body: { index: idx } }))
    mutate((l) => l.links.splice(idx, 1))
  }

  const deleteSelected = () => {
    if (selected?.type === 'edge') return removeLink(Number(selected.id.slice(1)))
    if (locked) return
    const anns = selAnns.length ? selAnns : selected?.type === 'ann' ? [selected.id] : []
    if (anns.length) removeAnns(anns)
    const ids = selIds.length ? selIds : selected?.type === 'node' ? [selected.id] : []
    if (ids.length) removeNodes(ids)
  }

  const save = async (silent) => {
    try {
      const r = await api(`/labs/${name}`, { method: 'PUT', body: lab })
      setWarnings(r.warnings); setDirty(false)
      return true
    } catch (e) { if (!silent) onError(e); return false }
  }

  const run = async (label, fn) => {
    setBusy(label)
    try { await fn() } catch (e) { onError(e) } finally { setBusy(''); await load() }
  }

  const deploy = () => readOnly ? onError(new Error('Lab in sola lettura')) : run('Avvio in corso…', async () => {
    if (dirty && !(await save())) return
    const st = await api(`/labs/${name}/deploy`, { method: 'POST' })
    if (st.error) onError(new Error(st.error))
  })
  const destroy = () => run('Arresto in corso…', async () => {
    setConsoles([]); setActiveConsole(null); setWsWins([])
    await api(`/labs/${name}/destroy`, { method: 'POST' })
    loadCaps()
  })

  // start / stop / wipe one or more nodes, skipping the ones where the action makes no sense
  const bulk = (nodes, action) => {
    if (readOnly) return onError(new Error('Lab in sola lettura: chiedi al proprietario il permesso di modifica'))
    const list = nodes.filter((n) => canDo(tplOf(n), state?.nodes?.[n], action))
    if (!list.length) return
    if (action === 'wipe' && !confirm(`Azzerare i dischi di ${list.join(', ')}? Tutte le modifiche andranno perse.`)) return
    return run(`${ACTION_LABEL[action]} ${list.length === 1 ? list[0] : `${list.length} nodi`}…`, async () => {
      if (action === 'start' && dirty && !(await save())) return
      const errs = []
      for (const n of list) {
        setPending((p) => ({ ...p, [n]: action }))
        try { await api(`/labs/${name}/nodes/${n}/${action}`, { method: 'POST' }) } catch (e) { errs.push(`${n}: ${e.message}`) }
        setPending((p) => { const c = { ...p }; delete c[n]; return c })
      }
      if (errs.length) throw new Error(errs.join('\n'))
    })
  }
  const nodeAction = (node, action) => bulk([node], action)
  const exportNodeConfig = async (node) => {
    try {
      const r = await api(`/labs/${name}/configs`)
      const text = r.sets?.[r.active]?.[node]
      if (!text) throw new Error(`Nessuna startup config per ${node} nel set "${r.active}"`)
      const a = document.createElement('a')
      a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' })); a.download = `${name}_${node}.cfg`; a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 2000)
    } catch (e) { onError(e) }
  }

  const applyConditioning = () => run('Applico condizionamento…', async () => {
    if (dirty && !(await save())) return
    await api(`/labs/${name}/conditioning`, { method: 'POST' })
  })

  // startup-config ON/OFF and active set are written straight to lab.yml (no "unsaved" state)
  const enableConfigs = async (map) => {
    try {
      await api(`/labs/${name}/configs/enable`, { method: 'POST', body: { nodes: map } })
      setLab((l) => { const c = structuredClone(l); c.nodes.forEach((n) => { if (n.name in map) n.startup_config = map[n.name] }); return c })
    } catch (e) { onError(e) }
  }
  const setActiveSet = (s) => setLab((l) => ({ ...l, config_set: s }))

  // HTTP/HTTPS: the device's web page in a new browser tab, through a port forwarded by the server
  const openWeb = (n, kind) => {
    const w = window.open('', '_blank')
    api(`/labs/${name}/nodes/${encodeURIComponent(n)}/open`, { method: 'POST', body: {} })
      .then((r) => { if (w) w.location = `${kind}://${location.hostname}:${r.port}/` })
      .catch((e) => { w?.close(); onError(e) })
  }
  const openConsoles = (nodes) => {
    if (consoleTarget === 'native') return openNative(nodes)  // apri nel terminale del PC invece che nel browser
    let list = nodes.filter((n) => canDo(tplOf(n), state?.nodes?.[n], 'console'))
    list.filter((n) => ['http', 'https'].includes(consoleKind(state.nodes[n]))).forEach((n) => openWeb(n, consoleKind(state.nodes[n])))
    list = list.filter((n) => !['http', 'https'].includes(consoleKind(state.nodes[n])))
    // graphical consoles: a browser popup in dock mode; in window mode VNC stays in-page as a floating window
    const asPopup = (n) => { const k = consoleKind(state.nodes[n]); return POPUP_KINDS.includes(k) && !(consoleMode === 'window' && k === 'vnc') }
    list.filter(asPopup).forEach((n) => {
      if (!openConsoleWindow(name, n)) onError(new Error('Il browser ha bloccato la finestra: consenti i popup per NetSO'))
    })
    list = list.filter((n) => !asPopup(n))
    if (!list.length) return
    const inst = list.map((n) => ({ id: cid(), node: n }))  // no dedup: each open is its own window
    setConsoles((c) => [...c, ...inst])
    setActiveConsole(inst[inst.length - 1].id)
  }
  const openConsole = (node) => openConsoles([node])
  const closeConsole = (id) => {
    const rest = consoles.filter((c) => c.id !== id)
    setConsoles(rest)
    if (activeConsole === id) setActiveConsole(rest.length ? rest[rest.length - 1].id : null)
  }

  // open a console with the user's OWN desktop client (EVE-NG "native"): telnet/VNC/SPICE are bound to 0.0.0.0
  // on the server, so a telnet:// / vnc:// / spice:// link to this server reaches them directly.
  const nativeConsole = (ns) => {
    if (!ns || ns.status !== 'running') return null
    const host = location.hostname
    const k = consoleKind(ns)
    if (k === 'telnet' && ns.console_port) return { uri: `telnet://${host}:${ns.console_port}`, app: 'telnet' }
    if (k === 'vnc' && ns.vnc_display != null) return { uri: `vnc://${host}:${5900 + ns.vnc_display}`, app: 'VNC' }
    if (k === 'spice' && ns.spice_port) return { uri: `spice://${host}:${ns.spice_port}`, app: 'SPICE' }
    if ((k === 'rdp' || k === 'rdp-tls') && ns.mgmt_ip) return { uri: `rdp://${ns.mgmt_ip}:3389`, app: 'RDP' }
    return null
  }
  // terminal consoles (telnet/ssh) open in the user's OWN terminal via a tiny launcher file it downloads
  const termTarget = (ns, tpl) => {
    if (!ns || ns.status !== 'running') return null
    const k = consoleKind(ns)
    if (k === 'telnet' && ns.console_port) return { proto: 'telnet', host: location.hostname, port: ns.console_port }
    if (k === 'exec' && ns.mgmt_ip) return { proto: 'ssh', host: ns.mgmt_ip, port: 22, user: tpl?.console_user || 'root' }
    return null
  }
  const osKind = () => {
    const p = (navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || '').toLowerCase()
    return /win/.test(p) ? 'win' : /mac|iphone|ipad/.test(p) ? 'mac' : 'linux'
  }
  const launcherFile = (os, t, node) => {
    const safe = (node || 'node').replace(/[^\w.-]+/g, '_')
    const H = t.host, P = t.port
    const ssh = t.proto === 'ssh'
    const run = ssh
      ? { term: `ssh ${t.user}@${H}`, winGui: `putty -ssh ${t.user}@${H} -P ${P}`, winCli: `ssh ${t.user}@${H}` }
      : { term: `telnet ${H} ${P}`, winGui: `putty -telnet ${H} -P ${P}`, winCli: `telnet ${H} ${P}` }
    if (os === 'win') {
      const need = ssh ? 'un client SSH (PuTTY o OpenSSH)' : 'PuTTY o il «Client Telnet» di Windows'
      return { name: `console-${safe}.bat`, text: [
        '@echo off', `title NetSO - ${safe}`,
        `where putty >nul 2>nul && (start "" ${run.winGui} & exit /b)`,
        `where ${ssh ? 'ssh' : 'telnet'} >nul 2>nul && (start "" ${run.winCli} & exit /b)`,
        `echo Serve ${need}. Scarica PuTTY da https://www.putty.org`, 'pause', '',
      ].join('\r\n') }
    }
    if (os === 'mac') {
      const body = ssh
        ? `exec ${run.term}`
        : [`command -v telnet >/dev/null 2>&1 && exec ${run.term}`,
           `command -v nc >/dev/null 2>&1 && { echo "(telnet assente, uso nc)"; exec nc ${H} ${P}; }`,
           'echo "Manca telnet: installalo con  brew install telnet"',
           'read -n1 -r -p "Premi un tasto per chiudere..."'].join('\n')
      return { name: `console-${safe}.command`, text: ['#!/bin/bash', `# NetSO console: ${safe}`, 'clear',
        `echo "NetSO -> ${safe}  (${run.term})"`, body, ''].join('\n') }
    }
    return { name: `console-${safe}.sh`, text: ['#!/bin/sh', `# NetSO console: ${safe}`,
      'for T in x-terminal-emulator gnome-terminal konsole xfce4-terminal xterm; do',
      `  command -v "$T" >/dev/null 2>&1 && exec "$T" -e "${run.term}"`, 'done', `exec ${run.term}`, ''].join('\n') }
  }
  const openNative = (nodes) => {
    const os = osKind()
    const list = nodes.map((n) => ({ n, g: nativeConsole(state?.nodes?.[n]), t: termTarget(state?.nodes?.[n], tplOf(n)) }))
    const graphical = list.filter((x) => !x.t && x.g)  // VNC/SPICE/RDP: the OS viewer via its URI
    const terminals = list.filter((x) => x.t)           // telnet/ssh: a launcher the PC terminal runs
    if (!graphical.length && !terminals.length) { onError(new Error('Nessuna consolle esterna disponibile per questo nodo')); return }
    graphical.forEach(({ g }, i) => setTimeout(() => {
      const a = document.createElement('a'); a.href = g.uri; a.rel = 'noreferrer'; a.click()
    }, i * 400))
    terminals.forEach(({ n, t }, i) => setTimeout(() => {
      const f = launcherFile(os, t, n)
      const u = URL.createObjectURL(new Blob([f.text], { type: 'application/octet-stream' }))
      const a = document.createElement('a'); a.href = u; a.download = f.name; a.click()
      setTimeout(() => URL.revokeObjectURL(u), 5000)
    }, i * 500))
    if (terminals.length && !extHelpSeen) setShowExtHelp(true)
  }

  // ---- Wireshark in the browser
  const peerOf = (ep) => { const l = lab.links.find((x) => x.endpoints.includes(ep)); return l && l.endpoints.find((e) => e !== ep).replace(':', ' ') }
  const showWs = (session, peer) => setWsWins((ws) => [...ws.map((w) => ({ ...w, min: true })).filter((w) => w.session.id !== session.id), { session, peer, min: false }])
  const openWireshark = async (node, iface, filter = '') => {
    const open = wsWins.find((w) => w.session.live !== false && w.session.node === node && w.session.iface === iface)
    if (open) return showWs(open.session, open.peer)
    try {
      const s = await api(`/labs/${name}/wireshark`, { method: 'POST', body: { node, iface, filter } })
      showWs(s, peerOf(`${node}:${iface}`)); loadCaps()
    } catch (e) { onError(e) }
  }
  const openFileInWireshark = async (capture) => {
    try { showWs(await api(`/labs/${name}/wireshark/open`, { method: 'POST', body: { capture } })) } catch (e) { onError(e) }
  }
  // bring back a window, or attach a new one to a session left running (other tab, page reload)
  const restoreWs = (id) => {
    const w = wsWins.find((x) => x.session.id === id)
    const s = w?.session || wsSessions.find((x) => x.id === id)
    if (s) showWs(s, w?.peer || peerOf(`${s.node}:${s.iface}`))
  }
  const closeWs = async (id) => {
    setWsWins((ws) => ws.filter((w) => w.session.id !== id))
    try { await api(`/labs/${name}/wireshark/${encodeURIComponent(id)}`, { method: 'DELETE' }) } catch (e) { onError(e) }
    setTimeout(loadCaps, 600)
  }
  const wsMenu = (eps) => eps.filter((e) => isVm(tplOf(splitEp(e)[0]))).map((e) => {
    const [n, i] = splitEp(e)
    return { icon: 'capture', label: `Wireshark su ${n} ${i}`, disabled: state?.nodes?.[n]?.status !== 'running', onClick: () => openWireshark(n, i) }
  })

  const openYaml = async () => {
    if (dirty && !(await save())) return
    try { setYaml(await api(`/labs/${name}/yaml`, { text: true })) } catch (e) { onError(e) }
  }
  const saveYaml = async () => {
    try {
      await api(`/labs/${name}/yaml`, { method: 'PUT', body: { yaml } })
      setYaml(null); await load()
    } catch (e) { onError(e) }
  }


  const fit = () => flow.fitView({ padding: 0.3, maxZoom: 1.2, duration: 300 })
  const close = async () => {
    if (dirty && !readOnly && !(await save(true)) && !confirm('Salvataggio non riuscito: uscire perdendo le modifiche?')) return
    onBack()
  }

  // autosave: persist edits a moment after they happen, so nothing is lost if you leave without pressing Salva
  useEffect(() => {
    if (!dirty || readOnly) return
    const t = setTimeout(() => save(true), 1000)
    return () => clearTimeout(t)
  }, [dirty, lab, readOnly]) // eslint-disable-line react-hooks/exhaustive-deps
  // last resort: a hard reload/tab-close within that second still warns instead of silently dropping changes
  useEffect(() => {
    const warn = (e) => { if (dirty && !readOnly) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, readOnly])

  useEffect(() => {
    const onKey = (e) => {
      if (wsWins.some((w) => !w.min)) return // keys belong to Wireshark
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) || document.activeElement?.closest('.term')
      if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save() }
      if (typing || yaml !== null || addDlg || linkDlg || listDlg || cfgDlg || capDlg) return
      if (e.key === 'Delete' || e.key === 'Backspace') deleteSelected()
      if ((e.ctrlKey || e.metaKey) && e.key === 'a') { e.preventDefault(); selectAll() }
      if (e.key === 'Escape') { setMenu(null); clearSelection() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (!lab) return <div className="empty">Caricamento…</div>

  const allNames = lab.nodes.map((n) => n.name)
  const sharedTpls = templates.filter((t) => SHARED.includes(t.kind))

  // ---------------------------------------------------------- right-click menus
  const buildMenu = () => {
    if (!menu) return null
    if (menu.kind === 'pane') {
      return {
        items: [
          { icon: 'plus', label: 'Nodo', disabled: locked, onClick: () => setAddDlg({ pos: menu.pos, at: { x: menu.x, y: menu.y } }) },
          { icon: 'links', label: 'Rete', disabled: locked, sub: sharedTpls.map((t) => ({ dev: t.icon, label: tplTitle(t), onClick: () => addNodes({ tpl: t.name }, menu.pos) })) },
          { icon: 'save', label: 'Immagine', disabled: locked, onClick: () => addDrawing('image', { x: menu.pos.x + 43, y: menu.pos.y + 29 }) },
          { icon: 'edit', label: 'Testo', disabled: locked, onClick: () => addDrawing('text', { x: menu.pos.x + 43, y: menu.pos.y + 29 }) },
          { icon: 'chev', label: 'Linea', disabled: locked, onClick: () => addDrawing('line', { x: menu.pos.x + 43, y: menu.pos.y + 29 }) },
          { icon: 'stop', label: 'Forma', disabled: locked, sub: [
            { icon: 'stop', label: 'Rettangolo', onClick: () => addDrawing('rect', { x: menu.pos.x + 43, y: menu.pos.y + 29 }) },
            { icon: 'info', label: 'Ellisse', onClick: () => addDrawing('ellipse', { x: menu.pos.x + 43, y: menu.pos.y + 29 }) },
          ] },
          { sep: true },
          { icon: 'play', label: 'Nodi', sub: [
            { icon: 'play', label: 'Avvia tutti', onClick: () => bulk(allNames, 'start') },
            { icon: 'stop', label: 'Ferma tutti', onClick: () => bulk(allNames, 'stop') },
            { icon: 'console', label: 'Apri tutte le consolle', onClick: () => openConsoles(allNames) },
            { icon: 'config', label: 'Startup config', onClick: () => setCfgDlg({}) },
          ] },
          { icon: 'fit', label: 'Disponi', disabled: locked || !lab.nodes.length, sub: [
            { icon: 'fit', label: 'A livelli', onClick: () => arrange('layers') },
            { icon: 'refresh', label: 'In cerchio', onClick: () => arrange('circle') },
            { icon: 'nodes', label: 'A griglia', onClick: () => arrange('grid') },
            { icon: 'links', label: 'Genera topologia…', onClick: () => setAutoDlg({ kind: 'gen', origin: menu.pos }) },
          ] },
          { sep: true },
          { icon: 'select', label: 'Seleziona tutto', kbd: '⌘A', onClick: selectAll },
          { icon: 'save', label: 'Salva', kbd: '⌘S', disabled: !dirty, onClick: () => save() },
        ],
      }
    }
    if (menu.kind === 'more') return { title: 'Altre azioni', icon: 'more', items: moreItems }
    if (menu.kind === 'ann') {
      const a = (lab.annotations || []).find((x) => x.id === menu.id)
      if (!a) return null
      const label = Object.fromEntries(ANN_TYPES.map(([t, l]) => [t, l]))[a.type]
      return {
        title: label, icon: 'edit',
        items: [
          { icon: 'edit', label: 'Modifica (pannello a destra)', onClick: () => setSelected({ type: 'ann', id: a.id }) },
          a.z < 0 ? { icon: 'deploy', label: 'Porta sopra i dispositivi', disabled: locked, onClick: () => updateAnn(a.id, { z: 1 }) }
            : { icon: 'download', label: 'Porta dietro i dispositivi', disabled: locked, onClick: () => updateAnn(a.id, { z: -1 }) },
          { icon: 'copy', label: 'Duplica', disabled: locked, onClick: () => duplicateAnn(a.id) },
          { sep: true },
          { icon: 'trash', label: 'Elimina', kbd: '⌫', danger: true, disabled: locked, onClick: () => removeAnns([a.id]) },
        ],
      }
    }
    if (menu.kind === 'node') {
      const n = menu.id, ns = state?.nodes?.[n], tpl = tplOf(n), node = lab.nodes.find((x) => x.name === n)
      if (!node) return null
      const up = ns?.status === 'running'
      return {
        title: n, dev: node.icon || tpl?.icon, sub: tpl?.name,
        items: [
          isVm(tpl) && { icon: 'play', label: 'Avvia', cls: 'go', disabled: up || !!pending[n], onClick: () => nodeAction(n, 'start') },
          isVm(tpl) && { icon: 'stop', label: 'Ferma', cls: 'stop', disabled: !up || !!pending[n], onClick: () => nodeAction(n, 'stop') },
          isVm(tpl) && { icon: 'console', label: 'Consolle', disabled: !canDo(tpl, ns, 'console'), onClick: () => openConsole(n) },
          isVm(tpl) && up && (termTarget(ns, tpl) || nativeConsole(ns)) && { icon: 'expand', label: termTarget(ns, tpl) ? 'Consolle nel terminale del PC' : `Consolle esterna (${nativeConsole(ns).app})`, onClick: () => openNative([n]) },
          hasDisks(tpl) && { icon: 'wipe', label: 'Azzera dischi (wipe)', disabled: !canDo(tpl, ns, 'wipe'), onClick: () => nodeAction(n, 'wipe') },
          isVm(tpl) && { sep: true },
          { icon: node.pinned ? 'unlock' : 'lock', label: node.pinned ? 'Sblocca posizione' : 'Blocca posizione', disabled: locked,
            onClick: () => mutate((l) => { const x = l.nodes.find((y) => y.name === n); if (x.pinned) delete x.pinned; else x.pinned = true }) },
          { render: () => <SizeRow value={node.size || 100} disabled={locked}
            onChange={(v) => mutate((l) => { const x = l.nodes.find((y) => y.name === n); if (v === 100) delete x.size; else x.size = v })} /> },
          isVm(tpl) && { icon: 'config', label: 'Config', sub: [
            { icon: 'config', label: 'Startup config…', onClick: () => setCfgDlg({ node: n }) },
            { icon: 'save', label: 'Salva dal dispositivo…', disabled: !up, onClick: () => setAutoDlg({ kind: 'save', nodes: [n] }) },
            { icon: 'download', label: 'Esporta', onClick: () => exportNodeConfig(n) },
          ] },
          isVm(tpl) && usedIfaces(lab, n).size > 0 && { icon: 'capture', label: 'Cattura', sub: [
            ...wsMenu([...usedIfaces(lab, n)].map((i) => `${n}:${i}`)),
            { icon: 'nodes', label: 'Tabella pacchetti…', disabled: !up, onClick: () => setCapDlg({ node: n }) },
          ] },
          { sep: true },
          { icon: 'edit', label: 'Modifica…', kbd: '↵', onClick: () => setEditNode(n) },
          { icon: 'copy', label: 'Duplica', disabled: locked, onClick: () => duplicateNode(n) },
          { icon: 'trash', label: 'Elimina', kbd: '⌫', danger: true, disabled: up || locked, onClick: () => removeNodes([n]) },
        ],
      }
    }
    if (menu.kind === 'selection') {
      const ids = selIds
      return {
        title: `${ids.length} nodi selezionati`, icon: 'nodes',
        items: [
          { icon: 'play', label: 'Avvia selezionati', onClick: () => bulk(ids, 'start') },
          { icon: 'stop', label: 'Ferma selezionati', onClick: () => bulk(ids, 'stop') },
          { icon: 'console', label: 'Apri consolle', onClick: () => openConsoles(ids) },
          { icon: 'expand', label: 'Consolle nel terminale del PC', onClick: () => openNative(ids) },
          { icon: 'wipe', label: 'Azzera selezionati', onClick: () => bulk(ids, 'wipe') },
          { sep: true },
          { icon: 'trash', label: 'Elimina selezionati', danger: true, disabled: locked, onClick: () => removeNodes(ids) },
        ],
      }
    }
    if (menu.kind === 'edge') {
      const idx = Number(menu.id.slice(1)), l = lab.links[idx]
      if (!l) return null
      return {
        title: `${splitEp(l.endpoints[0])[0]} ⟷ ${splitEp(l.endpoints[1])[0]}`, icon: 'links',
        items: [
          { icon: 'edit', label: 'Modifica e aspetto (colore, linea)…', onClick: () => setSelected({ type: 'edge', id: menu.id }) },
          { icon: 'links', label: 'Tipo linea', disabled: locked, sub: LINE_TYPES.map(([k, label]) => ({
            label: `${label}${(l.style || 'solid') === k ? '  ✓' : ''}`,
            onClick: () => mutate((lk) => { const ln = lk.links[idx]; if (k === 'solid') delete ln.style; else ln.style = k }) })) },
          { icon: 'links', label: 'Forma linea', disabled: locked, sub: LINE_SHAPES.map(([k, label]) => ({
            label: `${label}${(l.shape || 'straight') === k ? '  ✓' : ''}`,
            onClick: () => mutate((lk) => { const ln = lk.links[idx]; if (k === 'straight') delete ln.shape; else ln.shape = k }) })) },
          running && { icon: 'bolt', label: 'Applica condizionamento', onClick: applyConditioning },
          { icon: 'capture', label: 'Cattura', sub: [
            ...wsMenu(l.endpoints),
            { icon: 'nodes', label: 'Tabella pacchetti…', onClick: () => { const e = l.endpoints.find((x) => isVm(tplOf(splitEp(x)[0]))); setCapDlg(e ? { node: splitEp(e)[0], iface: splitEp(e)[1] } : {}) } },
          ] },
          { sep: true },
          { icon: 'trash', label: 'Elimina', danger: true, disabled: locked, onClick: () => removeLink(idx) },
        ],
      }
    }
    return null
  }

  // ---------------------------------------------------------- "more" menu of the top bar
  const moreItems = [
    { icon: 'save', label: dirty ? 'Salva ora' : 'Tutto salvato', disabled: readOnly || !dirty, onClick: () => save() },
    { sep: true },
    { icon: 'nodes', label: 'Lab', sub: [
      { icon: 'wipe', label: 'Azzera tutti i nodi', onClick: () => bulk(allNames, 'wipe') },
      { icon: 'console', label: 'Apri tutte le consolle', onClick: () => openConsoles(allNames) },
      { icon: 'bolt', label: 'Applica condizionamento', disabled: !running, onClick: applyConditioning },
      { icon: locked ? 'unlock' : 'lock', label: locked ? 'Sblocca lab' : 'Blocca lab', onClick: () => setLocked((v) => !v) },
      { icon: 'refresh', label: 'Ricarica dal server', onClick: () => (!dirty || confirm('Scartare le modifiche non salvate?')) && load() },
      { icon: 'info', label: 'Dettagli', onClick: () => setListDlg('details') },
      { icon: 'copy', label: 'Istantanee…', onClick: () => setSnapDlg(true) },
    ] },
    { icon: 'links', label: 'Automazione', sub: [
      { icon: 'download', label: 'Salva config dai dispositivi…', onClick: () => setAutoDlg({ kind: 'save' }) },
      { icon: 'nodes', label: 'Inventario (Ansible, SSH)…', onClick: () => setAutoDlg({ kind: 'inv' }) },
      { icon: 'links', label: 'Genera topologia…', disabled: locked, onClick: () => setAutoDlg({ kind: 'gen', origin: viewCenter() }) },
      { icon: 'fit', label: 'Disponi a livelli', disabled: locked, onClick: () => arrange('layers') },
      { icon: 'refresh', label: 'Disponi in cerchio', disabled: locked, onClick: () => arrange('circle') },
      { icon: 'nodes', label: 'Disponi a griglia', disabled: locked, onClick: () => arrange('grid') },
    ] },
    { icon: 'code', label: 'File', sub: [
      { icon: 'code', label: 'Modifica YAML', onClick: openYaml },
      ...EXPORT_FORMATS.map((f) => ({ icon: f.icon, label: `Esporta ${f.label}`, onClick: async () => { if (dirty && !(await save())) return; downloadExport(name, f.id) } })),
    ] },
    { dev: 'router', label: 'Icone', sub: [
      { dev: 'router', label: `Moderne${iconStyle === 'modern' ? '  ✓' : ''}`, onClick: () => setIconStyle('modern') },
      { dev: 'router', label: `Classiche 3D${iconStyle === 'classic' ? '  ✓' : ''}`, onClick: () => setIconStyle('classic') },
      { dev: 'router', label: `NetSO Line${iconStyle === 'line' ? '  ✓' : ''}`, onClick: () => setIconStyle('line') },
      { dev: 'router', label: `NetSO Rack${iconStyle === 'rack' ? '  ✓' : ''}`, onClick: () => setIconStyle('rack') },
    ] },
    { icon: 'fit', label: 'Vista', sub: [
      { icon: 'links', label: `Nomi interfacce: ${ifaceLabels === 'always' ? 'sempre' : 'al passaggio'}`, onClick: () => setIfaceLabels((v) => (v === 'always' ? 'hover' : 'always')) },
      { icon: 'status', label: 'Stato del server', onClick: () => setListDlg('status') },
    ] },
    { sep: true },
    { icon: 'destroy', label: 'Destroy lab', danger: true, disabled: !running || !!busy, onClick: () => confirm('Fermare tutti i nodi e rimuovere le reti del lab?') && destroy() },
    { icon: 'exit', label: 'Chiudi lab', onClick: close },
  ]
  const arrange = (mode) => {
    const pos = autoLayout(lab, mode, (n) => tplOf(n)?.kind)
    if (!Object.keys(pos).length) return
    mutate((l) => l.nodes.forEach((n) => { if (pos[n.name]) Object.assign(n, pos[n.name]) }))
    setTimeout(fit, 80)
  }
  const addGenerated = (g) => {
    mutate((l) => { l.nodes.push(...g.nodes); l.links.push(...g.links) })
    pendingSel.current = g.nodes.map((n) => n.name)
    setAutoDlg(null)
    setTimeout(fit, 120)
  }
  const openMore = (e) => {
    const r = e.currentTarget.getBoundingClientRect()
    setMenu({ kind: 'more', x: r.right - 250, y: r.bottom + 6 })
  }

  const menuDef = buildMenu()

  const selNode = selected?.type === 'node' ? lab.nodes.find((n) => n.name === selected.id) : null
  const selLink = selected?.type === 'edge' ? lab.links[Number(selected.id.slice(1))] : null
  const multi = selIds.length > 1
  const selAnn = selected?.type === 'ann' && !multi ? (lab.annotations || []).find((a) => a.id === selected.id) : null
  const showInspector = multi || selLink || selAnn
  const generated = lab.generated || !!lab.instructions // generated labs carry a guide; others get free notes
  const upCount = Object.values(state?.nodes || {}).filter((n) => n.status === 'running').length

  // drop a device from the palette onto the canvas
  const onCanvasDrop = (e) => {
    const t = e.dataTransfer.getData(DRAG_TPL)
    if (!t || locked) return
    e.preventDefault()
    if (t.startsWith(ANN)) return addDrawing(t.slice(ANN.length), flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }))
    addNodes({ tpl: t }, flow.screenToFlowPosition({ x: e.clientX - 43, y: e.clientY - 29 }))
  }

  return (
    <div className={`editor ${consoles.length && consoleMode === 'dock' ? 'has-dock' : ''} labels-${ifaceLabels}`}>
      <header className="ed-top">
        <div className="ed-left">
          <button className="icon-btn" title="Torna al Workspace" onClick={close}><Ui name="back" /></button>
          <button className="lab-name" title="Dettagli del lab" onClick={() => setListDlg('details')}>{lab.name}</button>
          <span className={`pill ${state?.status}`}>{state?.status === 'running' ? 'avviato' : state?.status === 'error' ? 'errore' : 'fermo'}</span>
          {dirty && <span className="pill dirty">non salvato</span>}
          {readOnly ? <span className="pill"><Ui name="lock" size={11} /> sola lettura</span>
            : locked && <span className="pill"><Ui name="lock" size={11} /> bloccato</span>}
        </div>

        <nav className="ed-tools">
          <div className="seg">
            <TopBtn icon="plus" label="Nodo" title="Aggiungi nodi (scegli template e quantità)" disabled={locked} onClick={() => setAddDlg({ pos: dropPos(), at: lastPointer.current })} />
            <TopBtn icon="nodes" label="Nodi" title="Tabella dei nodi" onClick={() => setListDlg('nodes')} />
            <TopBtn icon="links" label="Collegamenti" title="Tabella dei collegamenti" onClick={() => setListDlg('links')} />
            <TopBtn icon="config" label="Config" title="Startup config" onClick={() => setCfgDlg({})} />
            <TopBtn icon="capture" label="Cattura" title="Cattura pacchetti / Wireshark" badge={caps.some((c) => c.running)} onClick={() => setCapDlg({})} />
          </div>
          <div className="seg">
            <TopBtn icon="play" label="Avvia tutto" title="Avvia tutti i nodi" cls="go" disabled={!lab.nodes.length || !!busy || readOnly} onClick={() => bulk(allNames, 'start')} />
            <TopBtn icon="stop" label="Ferma tutto" title="Ferma tutti i nodi" cls="stop" disabled={!running || !!busy || readOnly} onClick={() => bulk(allNames, 'stop')} />
          </div>
          <div className="seg">
            <TopBtn icon="exit" label="Esci" title="Torna al Workspace" onClick={close} />
            <TopBtn icon="status" label="Stato server" title="Stato del server" onClick={() => setListDlg('status')} />
          </div>
        </nav>

        <div className="ed-right">
          {busy && <span className="busy"><span className="spin" />{busy}</span>}
          {readOnly ? null : running
            ? <button className="danger" onClick={() => confirm('Fermare tutti i nodi e rimuovere le reti del lab?') && destroy()} disabled={!!busy}><Ui name="power" size={15} /> Destroy</button>
            : <button className="primary" onClick={deploy} disabled={!!busy || lab.nodes.length === 0}><Ui name="deploy" size={15} /> Deploy</button>}
          <ThemeButton />
          <button className="icon-btn" title="Altre azioni" onClick={openMore}><Ui name="more" /></button>
        </div>
      </header>

      {warnings.length > 0 && <div className="warning">⚠ {warnings.join(' · ')}</div>}
      {state?.error && <div className="warning err">✖ {state.error}</div>}

      <div className="workspace">
        {guideDlg && <GuidePanel lab={lab} generated={generated} readOnly={readOnly} onClose={() => setGuideDlg(false)}
          onChange={(field, t) => { mutate((l) => { l[field] = t }); save() }} />}
        <div className="ed-center">
          <div className={`canvas ${linking ? 'linking' : ''}`} ref={wrapper}
            onMouseMove={(e) => { lastPointer.current = { x: e.clientX, y: e.clientY } }}
            onDragOver={(e) => { if (e.dataTransfer.types.includes(DRAG_TPL)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' } }}
            onDrop={onCanvasDrop}>
            <NodeActions.Provider value={{ onAction: nodeAction, onConsole: openConsole, onDelete: removeNodes, onEdit: setEditNode, multi, locked, onAnn: updateAnn }}>
              <ReactFlow
                nodes={rfNodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
                onNodesChange={onNodesChange} onConnect={onConnect}
                onConnectStart={() => setLinking(true)} onConnectEnd={() => setLinking(false)}
                onEdgeClick={(_, e) => { selectOnly([]); setSelected({ type: 'edge', id: e.id }) }}
                onPaneClick={() => { clearSelection(); setMenu(null) }}
                onNodeClick={(_, n) => setSelected(n.id.startsWith(ANN) ? { type: 'ann', id: n.id.slice(ANN.length) } : { type: 'node', id: n.id })}
                onPaneContextMenu={(e) => {
                  e.preventDefault()
                  setMenu({ kind: 'pane', x: e.clientX, y: e.clientY, pos: flow.screenToFlowPosition({ x: e.clientX - 43, y: e.clientY - 29 }) })
                }}
                onNodeContextMenu={(e, n) => {
                  e.preventDefault()
                  if (n.id.startsWith(ANN)) {
                    selectOnly([n.id]); setSelected({ type: 'ann', id: n.id.slice(ANN.length) })
                    return setMenu({ kind: 'ann', id: n.id.slice(ANN.length), x: e.clientX, y: e.clientY })
                  }
                  if (selIds.length > 1 && selIds.includes(n.id)) return setMenu({ kind: 'selection', x: e.clientX, y: e.clientY })
                  selectOnly([n.id]); setSelected({ type: 'node', id: n.id })
                  setMenu({ kind: 'node', id: n.id, x: e.clientX, y: e.clientY })
                }}
                onSelectionContextMenu={(e) => { e.preventDefault(); setMenu({ kind: 'selection', x: e.clientX, y: e.clientY }) }}
                onEdgeContextMenu={(e, ed) => {
                  e.preventDefault(); selectOnly([]); setSelected({ type: 'edge', id: ed.id })
                  setMenu({ kind: 'edge', id: ed.id, x: e.clientX, y: e.clientY })
                }}
                onNodeDoubleClick={(_, n) => { if (!n.id.startsWith(ANN) && state?.nodes?.[n.id]?.status === 'running' && isVm(tplOf(n.id))) { openConsole(n.id); clearSelection() } }}
                onMove={(_, v) => setZoom(v.zoom)}
                connectionMode={ConnectionMode.Loose} connectionLineType="straight"
                nodesDraggable={!locked} nodesConnectable={!locked}
                panOnDrag={panMode ? [0, 1] : [1]} selectionOnDrag={!panMode} panOnScroll zoomOnScroll zoomOnDoubleClick={false}
                fitView fitViewOptions={{ padding: 0.3, maxZoom: 1.2 }}
                deleteKeyCode={null} selectionKeyCode="Shift" proOptions={{ hideAttribution: true }}
                colorMode={isLight(theme) ? 'light' : 'dark'}
              >
                <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} color={isLight(theme) ? '#c9cfdd' : '#262d45'} />
                <Controls showInteractive={false} position="bottom-left" />
                {showMap && <MiniMap pannable zoomable position="bottom-right"
                  nodeColor={(n) => (n.data.status === 'running' ? 'var(--accent)' : isLight(theme) ? '#c3c9d8' : '#475569')}
                  maskColor={isLight(theme) ? '#eef1f8aa' : '#0a0c1499'} />}
              </ReactFlow>
            </NodeActions.Provider>

          </div>

          <footer className="statusbar">
            <span><b>{lab.nodes.length}</b> nodi</span>
            <span><b>{lab.links.length}</b> collegamenti</span>
            <span className={upCount ? 'ok-t' : ''}><b>{upCount}</b> accesi</span>
            <div className="spacer" />
            <button className={`sb-btn ${guideDlg ? 'on' : ''}`}
              title={generated ? 'Mostra/nascondi la guida del lab (obiettivo, passi, soluzione)' : 'Mostra/nascondi le note del lab'}
              onClick={() => setGuideDlg((v) => !v)}>
              <Ui name="info" size={13} /> {generated ? 'Guida' : 'Note'}
              {(generated ? lab.instructions : lab.notes) ? <span className="sb-dot" /> : null}
            </button>
            <button className="sb-btn" title={consoleMode === 'window' ? 'Consolle in finestre mobili sul foglio — clicca per metterle in basso' : 'Consolle in basso (dock) — clicca per aprirle in finestre mobili'}
              onClick={() => setConsoleMode((v) => (v === 'window' ? 'dock' : 'window'))}><Ui name="console" size={13} /> {consoleMode === 'window' ? 'Finestre' : 'In basso'}</button>
            <button className={`sb-btn ${consoleTarget === 'native' ? 'on' : ''}`} title={consoleTarget === 'browser' ? 'Consolle nel browser — clicca per aprirle nel terminale del PC' : 'Consolle nel terminale del PC — clicca per aprirle nel browser'}
              onClick={() => setConsoleTarget((v) => (v === 'browser' ? 'native' : 'browser'))}><Ui name="expand" size={13} /> {consoleTarget === 'browser' ? 'Browser' : 'Terminale PC'}</button>
            <button className="sb-btn" title={panMode ? 'Il trascinamento sposta il foglio — clicca per selezionare i nodi a riquadro' : 'Il trascinamento seleziona i nodi a riquadro — clicca per spostare il foglio'}
              onClick={() => setPanMode((v) => !v)}><Ui name={panMode ? 'hand' : 'select'} size={13} /> {panMode ? 'Sposta' : 'Seleziona'}</button>
            <button className={`sb-btn ${ifaceLabels === 'always' ? 'on' : ''}`} title="Mostra sempre i nomi delle interfacce"
              onClick={() => setIfaceLabels((v) => (v === 'always' ? 'hover' : 'always'))}><Ui name="links" size={13} /> Interfacce</button>
            <button className={`sb-btn ${showMap ? 'on' : ''}`} title="Minimappa" onClick={() => setShowMap((v) => !v)}><Ui name="fit" size={13} /> Mappa</button>
            <button className="sb-btn" title="Adatta alla vista" onClick={fit}>{Math.round(zoom * 100)}%</button>
          </footer>
        </div>

        {showInspector && (
          <aside className="inspector">
            <button className="icon-btn insp-x" title="Chiudi" onClick={clearSelection}><Ui name="close" size={16} /></button>
            {selAnn ? (
              <AnnPanel key={selAnn.id} a={{ ...ANN_BASE, ...selAnn }} locked={locked} onChange={(patch) => updateAnn(selAnn.id, patch)}
                onDelete={() => removeAnns([selAnn.id])} onDuplicate={() => duplicateAnn(selAnn.id)}
                onImage={() => {
                  const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*'
                  inp.onchange = () => { const f = inp.files[0]; if (!f) return; if (f.size > 2_000_000) return onError(new Error('Immagine troppo grande (max 2 MB)'))
                    const rd = new FileReader(); rd.onload = () => updateAnn(selAnn.id, { image: rd.result }); rd.readAsDataURL(f) }
                  inp.click()
                }} />
            ) : multi ? (
              <MultiPanel ids={selIds} state={state} locked={locked}
                onBulk={(a) => bulk(selIds, a)} onConsole={() => openConsoles(selIds)} onDelete={() => removeNodes(selIds)} />
            ) : selNode ? (
              <NodePanel
                key={selNode.name} node={selNode} tpl={tplByName[selNode.template]} ns={state?.nodes?.[selNode.name]}
                lab={lab} locked={running || locked}
                onChange={(patch) => mutate((l) => Object.assign(l.nodes.find((n) => n.name === selNode.name), patch))}
                onRename={(newName) => {
                  if (!NODE_NAME_RE.test(newName) || lab.nodes.some((n) => n.name === newName)) return onError(new Error('Nome non valido o già usato'))
                  mutate((l) => {
                    l.nodes.find((n) => n.name === selNode.name).name = newName
                    l.links.forEach((x) => { x.endpoints = x.endpoints.map((e) => { const [n, i] = splitEp(e); return n === selNode.name ? `${newName}:${i}` : e }) })
                  })
                  api(`/labs/${name}/configs/rename-node`, { method: 'POST', body: { old: selNode.name, new: newName } }).catch(onError)
                  selectOnly([newName])
                  setSelected({ type: 'node', id: newName })
                }}
                onAction={(a) => nodeAction(selNode.name, a)}
                onConsole={() => openConsole(selNode.name)}
                configSet={lab.config_set || 'default'}
                onEnableConfig={(on) => enableConfigs({ [selNode.name]: on })}
                onEditConfig={() => setCfgDlg({ node: selNode.name })}
                onDelete={() => removeNodes([selNode.name])}
                onEdit={() => setEditNode(selNode.name)}
                canEdit={!locked} running={running} onApply={applyConditioning}
              />
            ) : (
              <div className="panel">
                <div className="panel-head"><span className="ph-ico"><Ui name="links" size={20} /></span><div><h3>Collegamento</h3><small>{splitEp(selLink.endpoints[0])[0]} ⟷ {splitEp(selLink.endpoints[1])[0]}</small></div></div>
                <Section title="Interfacce">
                  <LinkEnd lab={lab} tplByName={tplByName} ep={selLink.endpoints[0]} other={selLink.endpoints[1]} locked={running || locked}
                    onChange={(v) => mutate((l) => { l.links[Number(selected.id.slice(1))].endpoints[0] = v })} />
                  <LinkEnd lab={lab} tplByName={tplByName} ep={selLink.endpoints[1]} other={selLink.endpoints[0]} locked={running || locked}
                    onChange={(v) => mutate((l) => { l.links[Number(selected.id.slice(1))].endpoints[1] = v })} />
                  {running && <p className="hint">Le interfacce cambiano al prossimo deploy.</p>}
                </Section>
                <Section title="Aspetto">
                  <LinkLook link={selLink} locked={locked}
                    onChange={(patch) => mutate((l) => {
                      const ln = l.links[Number(selected.id.slice(1))]
                      for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete ln[k]; else ln[k] = v }
                    })} />
                </Section>
                <Section title="Condizionamento" defaultOpen={false}>
                  <LinkImpairments link={selLink}
                    onChange={(patch) => mutate((l) => Object.assign(l.links[Number(selected.id.slice(1))], patch))}
                    running={running} onApply={applyConditioning} />
                </Section>
                <Section title="Cattura pacchetti" defaultOpen={false}>
                  <div className="cap-btns">
                    {selLink.endpoints.filter((e) => isVm(tplOf(splitEp(e)[0]))).map((e) => {
                      const [n, i] = splitEp(e), on = caps.some((c) => c.running && c.node === n && c.iface === i)
                      return (
                        <button key={e} className={on ? 'on' : ''} disabled={state?.nodes?.[n]?.status !== 'running'} onClick={() => openWireshark(n, i)} title="Apri Wireshark su questa interfaccia">
                          <span className={`rec ${on ? 'on' : ''}`} /> <Ui name="capture" size={15} /> Wireshark su {n} <code>{i}</code>
                        </button>
                      )
                    })}
                    <button className="ghost" onClick={() => { const e = selLink.endpoints.find((x) => isVm(tplOf(splitEp(x)[0]))); setCapDlg(e ? { node: splitEp(e)[0], iface: splitEp(e)[1] } : {}) }}>
                      <Ui name="nodes" size={15} /> Tabella pacchetti nel browser…
                    </button>
                  </div>
                </Section>
                <button className="danger wide" onClick={deleteSelected} disabled={running || locked}><Ui name="trash" size={15} /> Elimina</button>
              </div>
            )}
          </aside>
        )}
      </div>

      {menuDef && <ContextMenu x={menu.x} y={menu.y} {...menuDef} onClose={() => setMenu(null)} />}

      {consoles.length > 0 && consoleMode === 'dock' && (
        <section className="dock" style={{ height: dockH }}>
          <div className="dock-grip" title="Trascina per ridimensionare la consolle" onPointerDown={startDockResize} />
          <div className="tabs">
            {consoles.map((c) => (
              <div key={c.id} className={`tab ${c.id === activeConsole ? 'on' : ''}`} onClick={() => setActiveConsole(c.id)}>
                <Ui name="console" size={13} />{c.node}<button className="x" onClick={(e) => { e.stopPropagation(); closeConsole(c.id) }}>×</button>
              </div>
            ))}
          </div>
          <div className="terms">
            {consoles.map((c) => <Console key={c.id} lab={name} node={c.node} visible={c.id === activeConsole} kind={consoleKind(state?.nodes?.[c.node])} />)}
          </div>
        </section>
      )}
      {consoles.length > 0 && consoleMode === 'window' && (
        <ConsoleWindows lab={name} items={consoles} kindOf={(node) => consoleKind(state?.nodes?.[node])} onClose={closeConsole} />
      )}

      {editNode && lab.nodes.some((n) => n.name === editNode) && (
        <NodeEditDialog node={lab.nodes.find((n) => n.name === editNode)} tpl={tplOf(editNode)} ns={state?.nodes?.[editNode]}
          lab={lab} locked={locked} running={state?.nodes?.[editNode]?.status === 'running'} configSet={lab.config_set || 'default'}
          onClose={() => setEditNode(null)} onError={onError} onReloadTemplates={onReloadTemplates}
          onSave={(patch, newName) => {
            if (newName !== editNode && (!NODE_NAME_RE.test(newName) || lab.nodes.some((n) => n.name === newName))) return onError(new Error('Nome non valido o già usato'))
            mutate((l) => {
              const x = l.nodes.find((y) => y.name === editNode)
              for (const [k, v] of Object.entries(patch)) { if (v == null || v === '' || (Array.isArray(v) && !v.length)) delete x[k]; else x[k] = v }
              if (newName !== editNode) {
                x.name = newName
                l.links.forEach((ln) => { ln.endpoints = ln.endpoints.map((e) => { const [nn, i] = splitEp(e); return nn === editNode ? `${newName}:${i}` : e }) })
              }
            })
            if (newName !== editNode) {
              api(`/labs/${name}/configs/rename-node`, { method: 'POST', body: { old: editNode, new: newName } }).catch(onError)
              selectOnly([newName]); setSelected({ type: 'node', id: newName })
            }
            setEditNode(null)
          }}
          onEnableConfig={(on) => enableConfigs({ [editNode]: on })}
          onEditConfig={() => { setEditNode(null); setCfgDlg({ node: editNode }) }}
          onLive={(patch) => mutate((l) => { const x = l.nodes.find((y) => y.name === editNode); for (const [k, v] of Object.entries(patch)) { if (v === undefined) delete x[k]; else x[k] = v } })}
          onApplyNow={applyConditioning} labRunning={running} />
      )}
      {autoDlg?.kind === 'save' && <SaveConfigsDialog name={name} lab={lab} state={state} tplByName={tplByName} initial={autoDlg.nodes}
        onClose={() => setAutoDlg(null)} onError={onError} onSaved={() => load()}
        onOpenConfigs={() => { setAutoDlg(null); setCfgDlg({ node: autoDlg.nodes?.[0] }) }} />}
      {autoDlg?.kind === 'inv' && <InventoryDialog name={name} onClose={() => setAutoDlg(null)} onError={onError}
        onEditNode={(n) => { setAutoDlg(null); setEditNode(n) }} />}
      {autoDlg?.kind === 'gen' && <GenerateDialog name={name} templates={templates} origin={autoDlg.origin} onClose={() => setAutoDlg(null)}
        onError={onError} onAdd={addGenerated} />}
      {snapDlg && <SnapshotsDialog lab={name} dirty={dirty} onSave={() => save()} onClose={() => setSnapDlg(false)} onError={onError}
        onRestored={() => { setSnapDlg(false); load() }} />}
      {showExtHelp && (
        <div className="modal-bg" onClick={() => setShowExtHelp(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <ModalHead icon="expand" title="Consolle nel terminale del PC" onClose={() => setShowExtHelp(false)} />
            <p className="hint">NetSO ha scaricato un file che apre la consolle nel terminale del tuo computer. Aprilo così:</p>
            <ul className="ext-help">
              <li className={osKind() === 'mac' ? 'on' : ''}><b>macOS</b> — file <code>.command</code>: al primo uso tasto destro → <b>Apri</b> (per superare Gatekeeper). Serve <code>telnet</code> (<code>brew install telnet</code>).</li>
              <li className={osKind() === 'win' ? 'on' : ''}><b>Windows</b> — file <code>.bat</code>: doppio click. Serve <b>PuTTY</b> (consigliato) o il «Client Telnet» di Windows abilitato.</li>
              <li className={osKind() === 'linux' ? 'on' : ''}><b>Linux</b> — file <code>.sh</code>: <code>chmod +x</code> e doppio click, oppure eseguilo dal terminale.</li>
            </ul>
            <label className="ext-help-ck"><input type="checkbox" onChange={(e) => setExtHelpSeen(e.target.checked)} /> Non mostrare più</label>
            <div className="row-end"><button className="primary" onClick={() => setShowExtHelp(false)}>Ho capito</button></div>
          </div>
        </div>
      )}
      {addDlg && <AddNodeDialog templates={templates} lab={lab} at={addDlg.at} onClose={() => setAddDlg(null)}
        onAdd={(opts) => { addNodes(opts, addDlg.pos); setAddDlg(null) }} />}

      {linkDlg && <LinkDialog lab={lab} tplByName={tplByName} {...linkDlg} onClose={() => setLinkDlg(null)}
        onCreate={(a, b) => {
          setLinkDlg(null)
          // hot-plug: on a running lab the server wires the new cable live
          if (running) return run('Collego il cavo…', () => api(`/labs/${name}/links/connect`, { method: 'POST', body: { endpoints: [a, b] } }))
          mutate((l) => l.links.push({ endpoints: [a, b] }))
        }} />}

      {listDlg === 'nodes' && (
        <NodesDialog lab={lab} state={state} tplByName={tplByName} locked={locked}
          onClose={() => setListDlg(null)}
          onChange={(nodeName, patch) => mutate((l) => Object.assign(l.nodes.find((n) => n.name === nodeName), patch))}
          onAction={nodeAction} onConsole={openConsole} onDelete={(n) => removeNodes([n])}
          onLocate={(n) => { setListDlg(null); selectOnly([n]); setSelected({ type: 'node', id: n }); flow.fitView({ nodes: [{ id: n }], maxZoom: 1.3, duration: 300 }) }} />
      )}
      {listDlg === 'links' && (
        <LinksDialog lab={lab} state={state} locked={locked || running} onClose={() => setListDlg(null)}
          canCapture={(e) => isVm(tplOf(splitEp(e)[0])) && state?.nodes?.[splitEp(e)[0]]?.status === 'running'}
          onCapture={(e) => { setListDlg(null); const [n, i] = splitEp(e); setCapDlg({ node: n, iface: i }) }}
          onSelect={(i) => { setListDlg(null); selectOnly([]); setSelected({ type: 'edge', id: `l${i}` }) }}
          onDelete={removeLink} />
      )}
      {cfgDlg && (
        <StartupConfigDialog name={name} lab={lab} tplByName={tplByName} initialNode={cfgDlg.node}
          onClose={() => setCfgDlg(null)} onError={onError} onEnable={enableConfigs} onActive={setActiveSet} />
      )}
      {capDlg && (
        <CaptureDialog name={name} lab={lab} state={state} tplByName={tplByName} caps={caps} initial={capDlg}
          onClose={() => setCapDlg(null)} onChanged={loadCaps} onError={onError}
          onWireshark={(ep, filter) => { setCapDlg(null); openWireshark(...splitEp(ep), filter) }}
          onOpenFile={(id) => { setCapDlg(null); openFileInWireshark(id) }}
          onShowWs={(id) => { setCapDlg(null); restoreWs(id) }} onStopWs={closeWs} />
      )}
      <WiresharkWindows lab={name} wins={wsWins}
        onMinimize={(w) => setWsWins((ws) => ws.map((x) => (x === w ? { ...x, min: true } : x)))}
        onRestore={(w) => showWs(w.session, w.peer)}
        onClose={(w) => closeWs(w.session.id)}
        onNewTab={(w) => { window.open(`#/wireshark/${encodeURIComponent(name)}/${encodeURIComponent(w.session.id)}`, '_blank'); setWsWins((ws) => ws.filter((x) => x !== w)) }} />
      {listDlg === 'status' && <StatusDialog onClose={() => setListDlg(null)} onError={onError} />}
      {listDlg === 'details' && (
        <DetailsDialog lab={lab} state={state} tplByName={tplByName} warnings={warnings} onClose={() => setListDlg(null)}
          onDescription={(d) => mutate((l) => { l.description = d })} />
      )}

      {yaml !== null && (
        <div className="modal-bg" onClick={() => setYaml(null)}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <ModalHead icon="code" title="Definizione YAML" onClose={() => setYaml(null)} />
            <textarea className="code" value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} />
            <div className="row-end">
              <button className="ghost" onClick={() => setYaml(null)}>Annulla</button>
              <button className="primary" onClick={saveYaml}>Applica</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------- top bar, palette, inspector sections
const DRAG_TPL = 'application/x-netso-template'

// small persisted UI preference (per browser)
function usePref(key, initial) {
  const [v, setV] = useState(() => { try { const x = localStorage.getItem(key); return x == null ? initial : JSON.parse(x) } catch { return initial } })
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(v)) } catch { /* ignore */ } }, [key, v])
  return [v, setV]
}

function TopBtn({ icon, label, title, onClick, disabled, badge, cls = '' }) {
  return (
    <button className={`top-btn ${cls}`} title={title} disabled={disabled} onClick={onClick}>
      <span className="tb-ico"><Ui name={icon} size={16} />{badge && <span className="tb-badge" />}</span>
      <span className="tb-lbl">{label}</span>
    </button>
  )
}

export function ThemeButton() {
  const theme = useTheme()
  const [open, setOpen] = useState(false)
  return (
    <div className="theme-pick">
      <button className="icon-btn theme-btn" title="Tema colori" onClick={() => setOpen((v) => !v)}><Ui name="palette" /></button>
      {open && <>
        <div className="theme-pop-bg" onMouseDown={() => setOpen(false)} />
        <div className="theme-pop">
          {THEMES.map((t) => (
            <button key={t.id} className={`theme-row ${t.id === theme ? 'on' : ''}`} type="button"
              onClick={() => { setTheme(t.id); setOpen(false) }}>
              <span className="theme-sw" style={{ background: t.bg }}><i style={{ background: t.accent }} /></span>
              <span className="grow">{t.label}</span>
              {t.id === theme && <Ui name="check" size={14} />}
            </button>
          ))}
        </div>
      </>}
    </div>
  )
}

// collapsible inspector section; remembers open/closed per title for the session
const sectionOpen = new Map()
function Section({ title, defaultOpen = true, children, extra }) {
  const [open, setOpen] = useState(() => sectionOpen.get(title) ?? defaultOpen)
  const toggle = () => setOpen((o) => { sectionOpen.set(title, !o); return !o })
  return (
    <section className={`sect ${open ? 'open' : ''}`}>
      <button className="sect-head" onClick={toggle}><Ui name="chev" size={13} /><span className="grow">{title}</span>{extra}</button>
      {open && <div className="sect-body">{children}</div>}
    </section>
  )
}

// entry that opens a second-level menu on hover (keeps the first level short, as in EVE-NG/PNETLab)
function SubMenu({ it, i, onClose }) {
  const [open, setOpen] = useState(false)
  const [left, setLeft] = useState(false)
  const ref = useRef(null)
  const show = () => { const r = ref.current.getBoundingClientRect(); setLeft(r.right + 240 > innerWidth); setOpen(true) }
  return (
    <div className="ctx-subwrap" ref={ref} style={{ '--i': i }} onMouseEnter={show} onMouseLeave={() => setOpen(false)}>
      <button className={open ? 'on' : ''} disabled={it.disabled} onClick={show}>
        <span className="g">{it.dev ? <Icon name={it.dev} size={17} /> : <Ui name={it.icon} size={16} />}</span>
        <span className="grow">{it.label}</span><Ui name="chev" size={12} />
      </button>
      {open && !it.disabled && <div className={`menu-box ctx-sub ${left ? 'left' : ''}`}><MenuItems items={it.sub} onClose={onClose} /></div>}
    </div>
  )
}

function MenuItems({ items, onClose }) {
  return items.filter(Boolean).map((it, i) => it.sep ? <span key={i} className="sep" />
    : it.sub ? <SubMenu key={i} it={it} i={i} onClose={onClose} />
    : it.head ? <div key={i} className="ctx-sec" style={{ '--i': i }}>{it.head}</div>
    : it.render ? <div key={i} className="ctx-row" style={{ '--i': i }}>{it.render(onClose)}</div>
    : (
      <button key={i} className={`${it.danger ? 'danger' : ''} ${it.cls || ''}`} disabled={it.disabled} style={{ '--i': i }}
        onClick={() => { onClose?.(); it.onClick() }}>
        <span className="g">{it.dev ? <Icon name={it.dev} size={17} /> : <Ui name={it.icon} size={16} />}</span>
        <span className="grow">{it.label}</span>
        {it.kbd && <kbd>{it.kbd}</kbd>}
      </button>
    ))
}

export function ContextMenu({ x, y, title, icon, dev, sub, items, onClose }) {
  const ref = useRef(null)
  const [pos, setPos] = useState({ left: x, top: y })
  useLayoutEffect(() => {
    // offset sizes ignore the opening animation's scale
    const w = ref.current.offsetWidth, h = ref.current.offsetHeight
    setPos({ left: Math.max(8, Math.min(x, innerWidth - w - 8)), top: Math.max(8, Math.min(y, innerHeight - h - 8)) })
  }, [x, y])
  useEffect(() => {
    const k = (e) => e.key === 'Escape' && onClose()
    addEventListener('keydown', k)
    return () => removeEventListener('keydown', k)
  }, [onClose])
  return (
    <>
      <div className="ctx-backdrop" onMouseDown={onClose} onContextMenu={(e) => { e.preventDefault(); onClose() }} />
      <div className="ctx menu-box" ref={ref} style={pos} onContextMenu={(e) => e.preventDefault()}>
        {title && <div className="ctx-head">{dev ? <Icon name={dev} size={20} /> : <Ui name={icon} size={16} />}<span>{title}</span>{sub && <small>{sub}</small>}</div>}
        <MenuItems items={items} onClose={onClose} />
      </div>
    </>
  )
}

export function ModalHead({ icon, title, sub, onClose }) {
  return (
    <div className="modal-head">
      <span className="mh-ico"><Ui name={icon} size={18} /></span>
      <div className="grow"><h3>{title}</h3>{sub && <small className="muted">{sub}</small>}</div>
      <button className="icon-btn" onClick={onClose} title="Chiudi"><Ui name="close" size={16} /></button>
    </div>
  )
}

// ------------------------------------------------------------- dialogs
// searchable device picker (PNETLab "Template" box)
function TplSelect({ list, value, onChange }) {
  const [open, setOpen] = useState(!value)
  const [q, setQ] = useState('')
  const cur = list.find((t) => t.name === value)
  const ql = q.trim().toLowerCase()
  const shown = list.filter((t) => !ql || `${tplTitle(t)} ${t.name}`.toLowerCase().includes(ql))
  return (
    <div className="tsel">
      <button type="button" className="tsel-btn" onClick={() => setOpen((v) => !v)}>
        {cur ? <><Icon name={cur.icon} size={20} /><span className="grow">{tplTitle(cur)}</span></> : <span className="grow muted">Scegli…</span>}
        <Ui name="chev" size={12} />
      </button>
      {open && (
        <div className="tsel-pop">
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cerca"
            onKeyDown={(e) => { if (e.key === 'Enter' && shown[0]) { e.preventDefault(); onChange(shown[0]); setOpen(false) } }} />
          <div className="tsel-list">
            {shown.map((t) => (
              <div key={t.name} className={`tsel-row ${t.name === value ? 'on' : ''} ${t.ready === false ? 'unsup' : ''}`}
                onClick={() => { onChange(t); setOpen(false); setQ('') }}>
                <Icon name={t.icon} size={18} />{tplTitle(t)}
              </div>
            ))}
            {!shown.length && <div className="tsel-row muted">Nessuno</div>}
          </div>
        </div>
      )}
    </div>
  )
}

function AddNodeDialog({ templates, lab, at, onClose, onAdd }) {
  const boxRef = useRef(null)
  const [pos, setPos] = useState(at ? { position: 'fixed', margin: 0, left: at.x, top: at.y } : null)
  useLayoutEffect(() => {
    if (!at || !boxRef.current) return
    const w = boxRef.current.offsetWidth, h = boxRef.current.offsetHeight
    setPos({ position: 'fixed', margin: 0,
      left: Math.max(8, Math.min(at.x, innerWidth - w - 8)),
      top: Math.max(8, Math.min(at.y, innerHeight - h - 8)) })
  }, [at])
  const [all, setAll] = useState(false)
  const list = templates.filter((t) => all || t.ready !== false).sort(byTitle)
  const [tplName, setTplName] = useState(null)
  const empty = { count: 1, name: '', image: '', cpus: '', ram: '', nics: '', console: '' }
  const [f, setF] = useState(empty)
  const tpl = templates.find((t) => t.name === tplName)
  const set = (k) => (e) => setF((o) => ({ ...o, [k]: e.target.value }))
  const choices = tpl?.choices || []
  const defImage = !tpl ? '' : choices.includes(tpl.image) || !choices.length ? tpl.image : choices[choices.length - 1]
  const pick = (t) => { setTplName(t.name); setF(empty) }
  const prefix = PREFIX[tpl?.icon] || 'n'
  const nameOk = !f.name || NODE_NAME_RE.test(f.name)
  const vm = isVm(tpl)

  const submit = (e) => {
    e?.preventDefault()
    if (!tpl || !nameOk) return
    const num = (v) => (v === '' ? undefined : Number(v))
    const image = f.image || defImage
    onAdd({
      tpl: tpl.name, count: Math.min(50, Math.max(1, Number(f.count) || 1)), name: f.name || undefined,
      image: vm && image !== tpl.image ? image : undefined, cpus: num(f.cpus), ram: num(f.ram), nics: num(f.nics), console: f.console || undefined,
    })
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <form className="modal add-node" ref={boxRef} style={pos || undefined} onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <ModalHead icon="plus" title="Aggiungi nodo" onClose={onClose} />
        <div className="field"><span className="row-between">Template
          <label className="check small"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Mostra anche senza immagine</label></span>
          <TplSelect list={list} value={tplName} onChange={pick} />
        </div>
        {tpl && <>
          {tpl.ready === false && <p className="hint warn">{tpl.kind === 'iol'
            ? (tpl.problems?.length ? tpl.problems.join(' · ')
              : 'Nessun file IOL (.bin) sul server NetSO: importali da Dispositivi → Immagini → PNETLab (sorgente root@IP-PNETLAB:/opt/unetlab/addons).')
            : 'Immagine non presente sul server NetSO: caricala o importala da Dispositivi → Immagini.'}</p>}
          <div className="grid2">
            <label className="field"><span>Numero di nodi</span><input type="number" min="1" max="50" value={f.count} onChange={set('count')} /></label>
            {vm ? (
              <label className="field"><span>Immagine</span>
                {choices.length ? (
                  <select value={f.image || defImage} onChange={set('image')}>
                    {choices.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                ) : <input placeholder={tpl.image} value={f.image} onChange={set('image')} />}
              </label>
            ) : <label className="field"><span>Porte</span><input type="number" min="1" max="64" placeholder={tpl.nics} value={f.nics} onChange={set('nics')} /></label>}
          </div>
          <label className="field"><span>{Number(f.count) > 1 ? 'Prefisso nome' : 'Nome'}</span>
            <input className={nameOk ? '' : 'bad'} placeholder={Number(f.count) > 1 ? prefix : freeName(lab, prefix)} value={f.name} onChange={set('name')} /></label>
          {vm && <div className={tpl.kind === 'qemu' ? 'grid4' : 'grid3'}>
            <label className="field"><span>CPU</span><input type="number" min="1" placeholder={tpl.cpus} value={f.cpus} onChange={set('cpus')} /></label>
            <label className="field"><span>RAM (MB)</span><input type="number" min="64" step="64" placeholder={tpl.ram} value={f.ram} onChange={set('ram')} /></label>
            <label className="field"><span>Ethernet</span><input type="number" min="1" max="32" placeholder={tpl.nics} value={f.nics} onChange={set('nics')} /></label>
            {tpl.kind === 'qemu' && <label className="field"><span>Consolle</span>
              <select value={f.console} onChange={set('console')}>
                <option value="">{consoleLabel(tpl.console)}</option>
                {CONSOLES.filter(([c]) => c !== tpl.console).map(([c, l]) => <option key={c} value={c}>{l}</option>)}
              </select></label>}
          </div>}
        </>}
        <div className="row-end">
          <button type="button" className="ghost" onClick={onClose}>Annulla</button>
          <button className="primary" type="submit" disabled={!tpl || !nameOk}><Ui name="plus" size={15} /> Aggiungi</button>
        </div>
      </form>
    </div>
  )
}

function LinkDialog({ lab, tplByName, source, target, onClose, onCreate }) {
  const free = (node) => {
    const n = lab.nodes.find((x) => x.name === node)
    const used = usedIfaces(lab, node)
    return interfaceNames(tplByName[n.template], n).filter((i) => !used.has(i))
  }
  const fa = free(source), fb = free(target)
  const [ia, setIa] = useState(fa[0])
  const [ib, setIb] = useState(fb[0])
  const side = (node, list, val, setVal) => {
    const n = lab.nodes.find((x) => x.name === node)
    return (
      <div className="link-side">
        <div className="chip sm"><Icon name={tplByName[n.template]?.icon} size={24} /></div>
        <b>{node}</b>
        <small className="muted">{n.template}</small>
        <select value={val} onChange={(e) => setVal(e.target.value)}>{list.map((o) => <option key={o}>{o}</option>)}</select>
      </div>
    )
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); onCreate(`${source}:${ia}`, `${target}:${ib}`) }}>
        <ModalHead icon="links" title="Nuovo collegamento" sub="Scegli le interfacce da collegare" onClose={onClose} />
        <div className="link-pick">
          {side(source, fa, ia, setIa)}
          <div className="link-wire" />
          {side(target, fb, ib, setIb)}
        </div>
        <div className="row-end">
          <button type="button" className="ghost" onClick={onClose}>Annulla</button>
          <button className="primary" type="submit" autoFocus>Collega</button>
        </div>
      </form>
    </div>
  )
}

function NodesDialog({ lab, state, tplByName, locked, onClose, onChange, onAction, onConsole, onDelete, onLocate }) {
  const num = (n, k) => (e) => onChange(n, { [k]: e.target.value === '' ? undefined : Number(e.target.value) })
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal xl" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="nodes" title="Nodi configurati" sub={`${lab.nodes.length} nodi nel lab`} onClose={onClose} />
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th /><th>Nome</th><th>Template</th><th>Immagine</th><th>CPU</th><th>RAM</th><th>NIC</th><th>Accesso</th><th /></tr></thead>
            <tbody>
              {lab.nodes.map((n) => {
                const tpl = tplByName[n.template], ns = state?.nodes?.[n.name], st = ns?.status || 'stopped'
                const vm = isVm(tpl), live = st === 'running'
                return (
                  <tr key={n.name}>
                    <td><span className={`dot ${st}`} /></td>
                    <td><button className="link-btn" onClick={() => onLocate(n.name)}><Icon name={tpl?.icon} size={16} /> {n.name}</button></td>
                    <td className="muted">{n.template}</td>
                    <td>{vm ? <input className="cell" placeholder={tpl.image} value={n.image || ''} onChange={(e) => onChange(n.name, { image: e.target.value || undefined })} /> : '—'}</td>
                    <td>{vm ? <input className="cell n" type="number" min="1" placeholder={tpl.cpus} value={n.cpus ?? ''} onChange={num(n.name, 'cpus')} /> : '—'}</td>
                    <td>{vm ? <input className="cell n" type="number" min="64" step="64" placeholder={tpl.ram} value={n.ram ?? ''} onChange={num(n.name, 'ram')} /> : '—'}</td>
                    <td><input className="cell n" type="number" min="1" max="32" placeholder={tpl?.nics} value={n.nics ?? ''} disabled={live || locked} onChange={num(n.name, 'nics')} /></td>
                    <td className="mono small">{live && ns.console_port ? `telnet :${ns.console_port}` : live && ns.container ? 'docker' : '—'}</td>
                    <td className="row-acts">
                      {vm && (live
                        ? <TbBtn title="Ferma" icon="stop" onClick={() => onAction(n.name, 'stop')} />
                        : <TbBtn title="Avvia" icon="play" cls="go" onClick={() => onAction(n.name, 'start')} />)}
                      {vm && <TbBtn title="Consolle" icon="console" disabled={!canDo(tpl, ns, 'console')} onClick={() => { onClose(); onConsole(n.name) }} />}
                      {hasDisks(tpl) && <TbBtn title="Azzera dischi" icon="wipe" disabled={live} onClick={() => onAction(n.name, 'wipe')} />}
                      <TbBtn title="Elimina" icon="trash" cls="danger" disabled={live || locked} onClick={() => onDelete(n.name)} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {!lab.nodes.length && <p className="hint">Nessun nodo.</p>}
        </div>
        <p className="hint">Le modifiche a CPU/RAM/immagine valgono al prossimo avvio del nodo. Ricorda di salvare.</p>
      </div>
    </div>
  )
}

function LinksDialog({ lab, state, locked, onClose, onSelect, onDelete, canCapture, onCapture }) {
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="links" title="Collegamenti" sub={`${lab.links.length} collegamenti nel lab`} onClose={onClose} />
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th /><th>Estremo A</th><th /><th>Estremo B</th><th>Condizionamento</th><th /></tr></thead>
            <tbody>
              {lab.links.map((l, i) => {
                const [a, ai] = splitEp(l.endpoints[0]), [b, bi] = splitEp(l.endpoints[1])
                const up = state?.nodes?.[a]?.status === 'running' && state?.nodes?.[b]?.status === 'running'
                return (
                  <tr key={i}>
                    <td><span className={`dot ${up ? 'running' : ''}`} /></td>
                    <td><b>{a}</b> <code>{ai}</code></td>
                    <td className="muted">⟷</td>
                    <td><b>{b}</b> <code>{bi}</code></td>
                    <td className="mono small">{condSummary(l) || <span className="muted">ideale</span>}</td>
                    <td className="row-acts">
                      <TbBtn title="Cattura pacchetti" icon="capture" disabled={!l.endpoints.some(canCapture)} onClick={() => onCapture(l.endpoints.find(canCapture))} />
                      <TbBtn title="Modifica" icon="edit" onClick={() => onSelect(i)} />
                      <TbBtn title="Elimina" icon="trash" cls="danger" disabled={locked} onClick={() => onDelete(i)} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {!lab.links.length && <p className="hint">Nessun collegamento. Collega due nodi trascinando dal pallino che appare sopra il nodo.</p>}
        </div>
      </div>
    </div>
  )
}

function DetailsDialog({ lab, state, tplByName, warnings, onClose, onDescription }) {
  const live = lab.nodes.filter((n) => state?.nodes?.[n.name]?.status === 'running')
  const res = (list, k) => list.reduce((s, n) => s + (n[k] ?? tplByName[n.template]?.[k] ?? 0), 0)
  const vms = lab.nodes.filter((n) => isVm(tplByName[n.template]))
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="info" title={lab.name} sub="Dettagli del lab" onClose={onClose} />
        <label className="field"><span>Descrizione</span>
          <textarea rows={3} value={lab.description || ''} onChange={(e) => onDescription(e.target.value)} /></label>
        <div className="stat-grid">
          <div><b>{lab.nodes.length}</b><span>nodi</span></div>
          <div><b>{live.length}</b><span>attivi</span></div>
          <div><b>{lab.links.length}</b><span>collegamenti</span></div>
          <div><b>{res(vms, 'cpus')}</b><span>vCPU totali</span></div>
          <div><b>{(res(vms, 'ram') / 1024).toFixed(1)}</b><span>GB RAM totali</span></div>
          <div><b>{(res(live.filter((n) => isVm(tplByName[n.template])), 'ram') / 1024).toFixed(1)}</b><span>GB RAM in uso</span></div>
        </div>
        {warnings.length > 0 && <div className="warning small">⚠ {warnings.join(' · ')}</div>}
        <p className="hint">Doppio clic su un nodo avviato apre la consolle. Canc elimina la selezione, ⌘S salva, ⌘A seleziona tutto, Esc deseleziona.</p>
      </div>
    </div>
  )
}

// ------------------------------------------------------------- startup configs (EVE-NG style)
const supportsConfig = (tpl) => !!tpl && !!tpl.config_file && tpl.config_method && tpl.config_method !== 'none'

function configHint(tpl) {
  if (!supportsConfig(tpl)) return 'Questo template non ha un metodo per ricevere la startup-config.'
  if (tpl.config_method === 'disk') return `Disco FAT con ${tpl.config_file}: il nodo la legge al boot se la NVRAM è vuota (nodo nuovo o dopo «Azzera»).`
  if (tpl.config_method === 'iso') return `CD-ROM con ${tpl.config_file}: letta al primo boot (nodo nuovo o dopo «Azzera»).`
  return `Copiata in ${tpl.config_file} prima di avviare il container (a ogni avvio).`
}

function Toggle({ on, onChange, disabled, title }) {
  return (
    <button type="button" className={`tgl ${on ? 'on' : ''}`} disabled={disabled} title={title}
      onClick={(e) => { e.stopPropagation(); onChange(!on) }}>
      <span className="knob" /><span className="txt">{on ? 'ON' : 'OFF'}</span>
    </button>
  )
}

function StartupConfigDialog({ name, lab, tplByName, initialNode, onClose, onError, onEnable, onActive }) {
  const nodes = lab.nodes.filter((n) => isVm(tplByName[n.template]))
  const [data, setData] = useState(null) // {active, sets: {set: {node: text}}}
  const [cur, setCur] = useState(lab.config_set || 'default')
  const [sel, setSel] = useState(initialNode || nodes.find((n) => supportsConfig(tplByName[n.template]))?.name || nodes[0]?.name || null)
  const [draft, setDraft] = useState('')
  const [changed, setChanged] = useState(false)
  const [ask, setAsk] = useState(null) // {mode: 'add' | 'rename', value}
  const fileRef = useRef(null)

  const reload = useCallback(async () => {
    try { setData(await api(`/labs/${name}/configs`)) } catch (e) { onError(e) }
  }, [name, onError])
  useEffect(() => { reload() }, [reload])
  useEffect(() => { if (data) { setDraft(data.sets[cur]?.[sel] || ''); setChanged(false) } }, [data, cur, sel])

  const saveCfg = async () => {
    if (!sel || !changed) return true
    try {
      await api(`/labs/${name}/configs/${cur}/nodes/${sel}`, { method: 'PUT', body: { config: draft } })
      setData((d) => ({ ...d, sets: { ...d.sets, [cur]: { ...d.sets[cur], [sel]: draft } } }))
      setChanged(false)
      return true
    } catch (e) { onError(e); return false }
  }
  // never lose an edited config when switching node/set or closing
  const go = async (fn) => { if (await saveCfg()) fn() }
  useEffect(() => {
    const k = (e) => e.key === 'Escape' && !ask && go(onClose)
    addEventListener('keydown', k)
    return () => removeEventListener('keydown', k)
  })

  const call = async (path, opts, after) => {
    try { const r = await api(`/labs/${name}/configs${path}`, opts); await reload(); after?.(r) } catch (e) { onError(e) }
  }
  const submitAsk = (e) => {
    e.preventDefault()
    const v = ask.value.trim()
    if (!NAME_RE.test(v)) return onError(new Error('Nome non valido: lettere, numeri, - e _ (inizia con una lettera)'))
    if (ask.mode === 'add') call('', { method: 'POST', body: { name: v, copy_from: cur } }, () => setCur(v))
    else call(`/${cur}/rename`, { method: 'POST', body: { new: v } }, () => { if (data.active === cur) onActive(v); setCur(v) })
    setAsk(null)
  }
  const remove = () => {
    if (!confirm(`Eliminare il config set «${cur}» con tutte le sue configurazioni?`)) return
    call(`/${cur}`, { method: 'DELETE' }, () => { if (data.active === cur) onActive('default'); setCur('default') })
  }
  const activate = () => call(`/${cur}/activate`, { method: 'POST' }, () => onActive(cur))
  const exportSet = async () => {
    try {
      const text = await api(`/labs/${name}/configs/${cur}/export`, { text: true })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(new Blob([text], { type: 'text/yaml' }))
      a.download = `${name}-${cur}.yml`
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) { onError(e) }
  }
  const importSet = async (e) => {
    const f = e.target.files[0]
    e.target.value = ''
    if (f) call('/import', { method: 'POST', body: { yaml: await f.text() } }, (r) => setCur(r.config_set))
  }

  const supported = nodes.filter((n) => supportsConfig(tplByName[n.template]))
  const allOn = supported.length > 0 && supported.every((n) => n.startup_config)
  const selNode = nodes.find((n) => n.name === sel)
  const selTpl = tplByName[selNode?.template]
  const sets = data ? Object.keys(data.sets) : [cur]

  return (
    <div className="modal-bg" onClick={() => go(onClose)}>
      <div className="modal xl cfg" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="config" title="Startup config" sub="Configurazioni con cui i nodi si avviano, raggruppate in set" onClose={() => go(onClose)} />
        <div className="cfg-bar">
          <span className="muted small">Config da modificare</span>
          <select value={cur} onChange={(e) => { const v = e.target.value; go(() => setCur(v)) }}>
            {sets.map((s) => <option key={s} value={s}>{data?.active === s ? `✓ ${s}` : s}</option>)}
          </select>
          {data && (data.active === cur
            ? <span className="pill running">attivo</span>
            : <button className="ghost" onClick={activate}><Ui name="bolt" size={15} /> Imposta come attivo</button>)}
          <div className="spacer" />
          {ask ? (
            <form className="cfg-ask" onSubmit={submitAsk}>
              <input autoFocus value={ask.value} placeholder={ask.mode === 'add' ? 'nome nuovo set' : 'nuovo nome'}
                onChange={(e) => setAsk({ ...ask, value: e.target.value })} />
              <button className="primary" type="submit">{ask.mode === 'add' ? 'Crea' : 'Rinomina'}</button>
              <button type="button" className="ghost" onClick={() => setAsk(null)}>Annulla</button>
            </form>
          ) : (
            <div className="cfg-acts">
              <button className="ghost" onClick={() => go(() => setAsk({ mode: 'add', value: '' }))} title={`Nuovo set, copia di «${cur}»`}><Ui name="plus" size={15} /> Aggiungi</button>
              <button className="ghost" disabled={cur === 'default'} onClick={() => setAsk({ mode: 'rename', value: cur })}><Ui name="edit" size={15} /> Rinomina</button>
              <button className="ghost" disabled={cur === 'default'} onClick={remove}><Ui name="trash" size={15} /> Elimina</button>
              <button className="ghost" onClick={() => fileRef.current.click()}><Ui name="deploy" size={15} /> Importa</button>
              <button className="ghost" onClick={() => go(exportSet)}><Ui name="download" size={15} /> Esporta</button>
              <input ref={fileRef} type="file" accept=".yml,.yaml" hidden onChange={importSet} />
            </div>
          )}
        </div>

        <div className="cfg-body">
          <div className="cfg-nodes">
            <div className="cfg-row all">
              <span className="grow">Cambia tutti</span>
              <Toggle on={allOn} disabled={!supported.length}
                onChange={(on) => onEnable(Object.fromEntries(supported.map((n) => [n.name, on])))} />
            </div>
            {nodes.map((n) => {
              const tpl = tplByName[n.template], ok = supportsConfig(tpl)
              const has = !!data?.sets[cur]?.[n.name]
              return (
                <div key={n.name} className={`cfg-row ${sel === n.name ? 'on' : ''}`} onClick={() => go(() => setSel(n.name))}>
                  <Icon name={tpl?.icon} size={18} />
                  <span className="grow">{n.name}{has && <span className="has-cfg" title="Ha una configurazione in questo set" />}</span>
                  <Toggle on={!!n.startup_config} disabled={!ok} title={ok ? '' : 'Non supportata da questo template'}
                    onChange={(on) => onEnable({ [n.name]: on })} />
                </div>
              )
            })}
            {!nodes.length && <p className="hint">Nessuna VM o container nel lab.</p>}
          </div>

          <div className="cfg-edit">
            {selNode ? <>
              <div className="cfg-edit-head">
                <Icon name={selTpl?.icon} size={20} />
                <div className="grow"><b>{selNode.name}</b> <span className="muted small">· set «{cur}»{selNode.startup_config ? '' : ' · startup-config OFF'}</span></div>
                {changed && <span className="pill dirty">non salvata</span>}
                <button className="primary" disabled={!changed} onClick={saveCfg}><Ui name="save" size={14} /> Salva</button>
              </div>
              <textarea className="code" spellCheck={false} value={draft} placeholder={`! configurazione di ${selNode.name}\nhostname ${selNode.name}\n!`}
                onChange={(e) => { setDraft(e.target.value); setChanged(true) }}
                onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); e.stopPropagation(); saveCfg() } }} />
              <p className="hint">{configHint(selTpl)}</p>
            </> : <p className="hint">Seleziona un nodo.</p>}
          </div>
        </div>
      </div>
    </div>
  )
}

// minimal markdown: headings, bold, inline code, code fences, lists
function mdInline(text) {
  const out = []
  let rest = text
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/
  while (rest) {
    const m = rest.match(re)
    if (!m) { out.push(rest); break }
    if (m.index > 0) out.push(rest.slice(0, m.index))
    const tok = m[0]
    out.push(tok.startsWith('**') ? <b key={out.length}>{tok.slice(2, -2)}</b> : <code key={out.length}>{tok.slice(1, -1)}</code>)
    rest = rest.slice(m.index + tok.length)
  }
  return out
}
function Markdown({ text }) {
  const lines = (text || '').split('\n'), blocks = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.startsWith('```')) {
      const code = []; i++
      while (i < lines.length && !lines[i].startsWith('```')) { code.push(lines[i]); i++ }
      i++; blocks.push(<pre key={blocks.length} className="md-code">{code.join('\n')}</pre>); continue
    }
    if (line.startsWith('### ')) { blocks.push(<h5 key={blocks.length}>{mdInline(line.slice(4))}</h5>); i++; continue }
    if (line.startsWith('## ')) { blocks.push(<h4 key={blocks.length}>{mdInline(line.slice(3))}</h4>); i++; continue }
    if (line.startsWith('# ')) { blocks.push(<h3 key={blocks.length}>{mdInline(line.slice(2))}</h3>); i++; continue }
    // GFM-style table: a header row with | and a --- separator line beneath
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(lines[i + 1])) {
      const cells = (r) => r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim())
      const head = cells(line); i += 2
      const rows = []
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) { rows.push(cells(lines[i])); i++ }
      blocks.push(
        <table key={blocks.length} className="md-table">
          <thead><tr>{head.map((h, j) => <th key={j}>{mdInline(h)}</th>)}</tr></thead>
          <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{mdInline(c)}</td>)}</tr>)}</tbody>
        </table>,
      )
      continue
    }
    if (/^\s*([-*]|\d+\.)\s/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i])) {
        const m = lines[i].match(/^(\s*)([-*]|\d+\.)\s(.*)$/)
        items.push(<li key={items.length} style={{ marginLeft: Math.floor(m[1].length / 4) * 16 }}>{mdInline(m[3])}</li>)
        i++
      }
      blocks.push(<ul key={blocks.length} className="md-list">{items}</ul>); continue
    }
    if (line.trim() === '') { i++; continue }
    blocks.push(<p key={blocks.length}>{mdInline(line)}</p>); i++
  }
  return <div className="md">{blocks}</div>
}

// docked side panel (left), so the guide/notes stay visible while you work on the topology
function GuidePanel({ lab, generated, readOnly, onClose, onChange }) {
  const field = generated ? 'instructions' : 'notes'
  const cur = lab[field] || ''
  const [edit, setEdit] = useState(!cur && !readOnly)
  const [text, setText] = useState(cur)
  useEffect(() => { setText(lab[field] || '') }, [lab, field]) // eslint-disable-line react-hooks/exhaustive-deps
  const title = generated ? 'Guida del lab' : 'Note del lab'
  const sub = generated ? 'Obiettivo, passi e soluzione' : 'Appunti liberi su questo lab'
  return (
    <aside className="guide-dock">
      <div className="guide-dock-head">
        <span className="gd-ico"><Ui name="info" size={17} /></span>
        <div className="grow"><h3>{title}</h3><small className="muted">{sub}</small></div>
        {!readOnly && !edit && <button className="icon-btn" title="Modifica" onClick={() => setEdit(true)}><Ui name="edit" size={15} /></button>}
        <button className="icon-btn" title="Chiudi" onClick={onClose}><Ui name="close" size={16} /></button>
      </div>
      <div className="guide-dock-body">
        {edit ? <>
          <textarea className="code guide-edit" value={text} onChange={(e) => setText(e.target.value)}
            placeholder={generated ? '# Titolo\n## Obiettivo\n…\n## Soluzione\n```\nconfig\n```' : 'Scrivi qui le tue note (markdown)…'} />
          <div className="row-end">
            {cur && <button type="button" className="ghost" onClick={() => { setText(cur); setEdit(false) }}>Annulla</button>}
            {!readOnly && <button type="button" className="primary" onClick={() => { onChange(field, text); setEdit(false) }}>Salva</button>}
          </div>
        </> : (cur ? <div className="guide-view"><Markdown text={cur} /></div>
          : <p className="hint">{generated ? 'Questo lab non ha ancora una guida.' : 'Nessuna nota.'} {readOnly ? '' : 'Clicca ✎ per scrivere.'}</p>)}
      </div>
    </aside>
  )
}

// live area sparkline (values are percentages 0..100), CML-style
function Spark({ data }) {
  const W = 240, H = 46, n = data.length
  const y = (v) => H - (Math.max(0, Math.min(100, v)) / 100) * (H - 4) - 2
  const pts = data.map((v, i) => [n < 2 ? 0 : (i / (n - 1)) * W, y(v)])
  const line = pts.map(([x, yy], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${yy.toFixed(1)}`).join(' ')
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      {[25, 50, 75].map((g) => <line key={g} className="spark-grid" x1="0" x2={W} y1={y(g)} y2={y(g)} />)}
      {n >= 2 && <path className="spark-area" d={`${line} L${W} ${H} L0 ${H} Z`} />}
      {n >= 2 && <path className="spark-line" d={line} />}
    </svg>
  )
}

export function StatusDialog({ onClose, onError }) {
  const [s, setS] = useState(null)
  const [hist, setHist] = useState({ cpu: [], mem: [], disk: [] })
  useEffect(() => {
    let live = true
    const load = () => api('/status').then((d) => {
      if (!live) return
      setS(d)
      const cpu = d.load && d.host ? Math.round((d.load[0] / (d.host.cpus || 1)) * 100) : null
      const mem = d.memory ? d.memory.percent : null
      const disk = d.disks?.home ? d.disks.home.percent : null
      setHist((hh) => ({
        cpu: cpu == null ? hh.cpu : [...hh.cpu, cpu].slice(-60),
        mem: mem == null ? hh.mem : [...hh.mem, mem].slice(-60),
        disk: disk == null ? hh.disk : [...hh.disk, disk].slice(-60),
      }))
    }).catch((e) => live && onError(e))
    load()
    const id = setInterval(load, 2000)   // live, CML-style
    return () => { live = false; clearInterval(id) }
  }, [onError])
  const h = s?.host
  const chk = (ok, label) => <div className={`chk-row ${ok ? 'ok' : 'ko'}`}><span>{ok ? '✓' : '✗'}</span>{label}</div>
  const chart = (label, cur, data, detail) => {
    const cls = cur == null ? 'na' : cur > 90 ? 'hot' : cur > 75 ? 'warn' : 'ok'
    return (
      <div className={`chart-card ${cls}`} key={label}>
        <div className="chart-top"><span>{label}</span><b>{cur == null ? 'n/d' : `${cur}%`}</b></div>
        <Spark data={data} />
        <div className="chart-sub">{detail}</div>
      </div>
    )
  }
  const uptime = (sec) => {
    if (!sec) return ''
    const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600), mm = Math.floor((sec % 3600) / 60)
    return ' · attivo da ' + [d && `${d}g`, (d || hh) && `${hh}h`, `${mm}m`].filter(Boolean).join(' ')
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="status" title="Stato server" sub={h ? `${h.hostname} · NetSO v${s.version}${uptime(s.uptime_seconds)}` : 'Caricamento…'} onClose={onClose} />
        {!h ? <div className="chk-grid"><span className="spin" /></div> : <>
          <div className="stat-grid">
            <div><b>{s.labs.running}/{s.labs.total}</b><span>lab accesi</span></div>
            <div><b>{s.labs.nodes_running}</b><span>nodi accesi</span></div>
            <div><b>{h.cpus}</b><span>CPU</span></div>
          </div>
          <div className="chart-grid">
            {chart('Carico CPU', s.load ? Math.round((s.load[0] / (h.cpus || 1)) * 100) : null, hist.cpu,
              s.load ? `load ${s.load.map((x) => x.toFixed(2)).join(' · ')}` : 'solo su Linux')}
            {chart('Memoria', s.memory ? s.memory.percent : null, hist.mem,
              s.memory ? `${(s.memory.used_mb / 1024).toFixed(1)} / ${(s.memory.total_mb / 1024).toFixed(1)} GB` : 'solo su Linux')}
            {chart('Disco', s.disks?.home ? s.disks.home.percent : null, hist.disk,
              s.disks?.home ? `${s.disks.home.used_gb} / ${s.disks.home.total_gb} GB · liberi ${s.disks.home.free_gb}` : '')}
          </div>
          <div className="chk-grid">
            {chk(h.kvm, 'KVM (accelerazione VM)')}{chk(h.qemu, 'QEMU')}{chk(h.docker, 'Docker')}{chk(h.telnet, 'telnet')}
          </div>
        </>}
      </div>
    </div>
  )
}

// ------------------------------------------------------------- inspector panels
function LinkEnd({ lab, tplByName, ep, other, onChange, locked }) {
  const [node, iface] = splitEp(ep)
  const n = lab.nodes.find((x) => x.name === node)
  const used = new Set(lab.links.flatMap((x) => x.endpoints).filter((e) => e !== ep && e !== other).map(splitEp).filter(([nn]) => nn === node).map(([, i]) => i))
  const opts = interfaceNames(tplByName[n?.template], n).filter((i) => !used.has(i) || i === iface)
  return (
    <label className="field">
      <span>{node}</span>
      <select value={iface} disabled={locked} onChange={(e) => onChange(`${node}:${e.target.value}`)}>
        {opts.map((o) => <option key={o}>{o}</option>)}
      </select>
    </label>
  )
}

const LINE_TYPES = [['solid', 'Continua'], ['dashed', 'Tratteggiata'], ['dotted', 'Punteggiata']]
const LINE_SHAPES = [['straight', 'Dritta'], ['curved', 'Curva'], ['elbow', 'A gomito']]

// Canvas look of a link: colour, line type, width, shape, label
function LinkLook({ link, locked, onChange }) {
  const color = link.color || null
  const w = link.width || 2
  const preview = (type, shape) => {
    const g = shape ? linkGeometry(6, shape === 'elbow' ? 20 : 16, 58, shape === 'elbow' ? 6 : 16, shape, 0) : null
    return (
      <svg width="64" height="24" viewBox="0 0 64 24" aria-hidden="true">
        <path d={g ? g.path : 'M6,12 L58,12'} fill="none" stroke={color || 'currentColor'} strokeWidth={Math.min(w, 4)}
          strokeDasharray={linkDash(type ?? link.style, Math.min(w, 4))} strokeLinecap={(type ?? link.style) === 'dotted' ? 'round' : 'butt'} />
      </svg>
    )
  }
  return (
    <div className="look">
      <h4>Aspetto</h4>
      <div className="swatches">
        <button className={`sw auto ${!color ? 'on' : ''}`} title="Colore del tema" disabled={locked} onClick={() => onChange({ color: null })}><Ui name="refresh" size={12} /></button>
        {LINK_COLORS.map((c) => (
          <button key={c} className={`sw ${color === c ? 'on' : ''}`} style={{ background: c }} title={c} disabled={locked} onClick={() => onChange({ color: c })} />
        ))}
        <label className={`sw pick ${color && !LINK_COLORS.includes(color) ? 'on' : ''}`} title="Colore personalizzato" style={color && !LINK_COLORS.includes(color) ? { background: color } : undefined}>
          <Ui name="plus" size={12} />
          <input type="color" disabled={locked} value={color || '#8b7bff'} onChange={(e) => onChange({ color: e.target.value })} />
        </label>
      </div>
      <div className="seg3">
        {LINE_TYPES.map(([k, label]) => (
          <button key={k} className={(link.style || 'solid') === k ? 'on' : ''} disabled={locked} title={label}
            onClick={() => onChange({ style: k === 'solid' ? null : k })}>{preview(k, null)}<span>{label}</span></button>
        ))}
      </div>
      <div className="seg3">
        {LINE_SHAPES.map(([k, label]) => (
          <button key={k} className={(link.shape || 'straight') === k ? 'on' : ''} disabled={locked} title={label}
            onClick={() => onChange({ shape: k === 'straight' ? null : k })}>{preview(null, k)}<span>{label}</span></button>
        ))}
      </div>
      {(link.shape === 'curved' || link.shape === 'elbow') && (() => {
        const def = link.shape === 'curved' ? 28 : 0
        const v = link.bend ?? def
        return (
          <label className="field range"><span>Curvatura <b>{v}</b></span>
            <input type="range" min="-100" max="100" value={v} disabled={locked}
              onChange={(e) => { const n = Number(e.target.value); onChange({ bend: n === def ? null : n }) }} />
          </label>
        )
      })()}
      <label className="field range"><span>Spessore <b>{w}px</b></span>
        <input type="range" min="1" max="8" value={w} disabled={locked} onChange={(e) => onChange({ width: Number(e.target.value) === 2 ? null : Number(e.target.value) })} />
      </label>
      <label className="field"><span>Etichetta</span>
        <input maxLength={40} placeholder="es. uplink, WAN, trunk…" value={link.label || ''} disabled={locked} onChange={(e) => onChange({ label: e.target.value })} />
      </label>
    </div>
  )
}

function LinkImpairments({ link, onChange, running, onApply }) {
  const num = (k) => (e) => onChange({ [k]: e.target.value === '' ? undefined : Number(e.target.value) })
  const active = link.delay_ms != null || link.jitter_ms != null || link.loss_pct != null || link.rate_kbit != null
  return (
    <div className="impair">
      <h4>Condizionamento <small className="muted">(tc netem)</small></h4>
      <div className="grid2">
        <label className="field"><span>Ritardo ms</span>
          <input type="number" min="0" step="1" value={link.delay_ms ?? ''} onChange={num('delay_ms')} /></label>
        <label className="field"><span>Jitter ms</span>
          <input type="number" min="0" step="1" value={link.jitter_ms ?? ''} onChange={num('jitter_ms')} /></label>
        <label className="field"><span>Perdita %</span>
          <input type="number" min="0" max="100" step="0.1" value={link.loss_pct ?? ''} onChange={num('loss_pct')} /></label>
        <label className="field"><span>Banda kbit</span>
          <input type="number" min="0" step="100" value={link.rate_kbit ?? ''} onChange={num('rate_kbit')} /></label>
      </div>
      {running && <button className="wide" onClick={onApply}><Ui name="bolt" size={15} /> Applica ora</button>}
      {!active && <p className="hint">Vuoto = cavo ideale. Il ritardo vale per direzione (RTT ≈ 2×).</p>}
    </div>
  )
}

function MultiPanel({ ids, state, locked, onBulk, onConsole, onDelete }) {
  return (
    <div className="panel">
      <div className="panel-head"><span className="ph-ico"><Ui name="nodes" size={20} /></span><div><h3>{ids.length} nodi selezionati</h3><small>Azioni di gruppo</small></div></div>
      <div className="actions">
        <button className="primary" onClick={() => onBulk('start')}><Ui name="play" size={14} /> Avvia</button>
        <button onClick={() => onBulk('stop')}><Ui name="stop" size={14} /> Ferma</button>
        <button onClick={onConsole}><Ui name="console" size={14} /> Consolle</button>
        <button className="ghost" onClick={() => onBulk('wipe')}><Ui name="wipe" size={14} /> Azzera</button>
      </div>
      <ul className="nodes-list">
        {ids.map((n) => <li key={n}><span className={`dot ${state?.nodes?.[n]?.status || 'stopped'}`} /><span className="grow">{n}</span></li>)}
      </ul>
      <button className="danger wide" onClick={onDelete} disabled={locked}><Ui name="trash" size={15} /> Elimina selezionati</button>
    </div>
  )
}

// VLANs of a switch node: one row per connected port (access VLAN or trunk)
function SwitchPorts({ node, lab, canEdit, running, onChange, onApply, bare }) {
  const ports = node.ports || {}
  const rows = lab.links.map((l) => l.endpoints.map(splitEp)).filter((eps) => eps.some(([n]) => n === node.name))
    .map((eps) => { const me = eps.find(([n]) => n === node.name), other = eps.find((e) => e !== me); return { port: me[1], peer: `${other[0]} ${other[1]}` } })
  const set = (port, cfg) => {
    const next = { ...ports }
    if (cfg === null) delete next[port]; else next[port] = cfg
    onChange({ ports: Object.keys(next).length ? next : undefined })
  }
  const body = (
    <>
      {!rows.length ? <p className="hint">Collega dei dispositivi allo switch per configurare le porte.</p> : (
        <div className="vlan-tbl">
          {rows.map(({ port, peer }) => {
            const c = ports[port]
            const mode = c?.mode || 'none'
            return (
              <div key={port} className="vlan-row">
                <div className="vr-head"><code>{port}</code><span className="muted small">→ {peer}</span></div>
                <div className="vr-cfg">
                  <select value={mode} disabled={!canEdit} onChange={(e) => set(port, e.target.value === 'none' ? null : e.target.value === 'access' ? { mode: 'access', vlan: c?.vlan || 10 } : { mode: 'trunk', vlans: c?.vlans || '1-4094', native: c?.native || 1 })}>
                    <option value="none">predefinita (VLAN 1)</option><option value="access">access</option><option value="trunk">trunk</option>
                  </select>
                  {mode === 'access' && <input type="number" min="1" max="4094" title="VLAN" value={c.vlan} disabled={!canEdit} onChange={(e) => set(port, { ...c, vlan: Number(e.target.value) || 1 })} />}
                  {mode === 'trunk' && <>
                    <input className="mono" title="VLAN permesse (es. 10,20,30-40)" value={c.vlans} disabled={!canEdit} onChange={(e) => set(port, { ...c, vlans: e.target.value })} />
                    <input type="number" min="1" max="4094" title="VLAN nativa (senza tag)" value={c.native} disabled={!canEdit} onChange={(e) => set(port, { ...c, native: Number(e.target.value) || 1 })} />
                  </>}
                </div>
              </div>
            )
          })}
        </div>
      )}
      <p className="hint">Access = una VLAN senza tag · Trunk = VLAN con tag (es. <code>10,20,30-40</code>) + nativa senza tag. Senza configurazione lo switch non separa le VLAN.</p>
      {running && <button type="button" className="wide" onClick={onApply}><Ui name="bolt" size={14} /> Salva e applica ora</button>}
    </>
  )
  return bare ? body : (
    <Section title="VLAN delle porte" extra={Object.keys(ports).length ? <span className="pill running">802.1Q</span> : null}>{body}</Section>
  )
}

// side panel of a drawing
function AnnPanel({ a, locked, onChange, onDelete, onDuplicate, onImage }) {
  const label = Object.fromEntries(ANN_TYPES.map(([t, l]) => [t, l]))[a.type]
  const swatches = (key, allowNone) => (
    <div className="swatches">
      {allowNone && <button className={`sw auto ${!a[key] ? 'on' : ''}`} title="Nessuno" disabled={locked} onClick={() => onChange({ [key]: null })}><Ui name="close" size={11} /></button>}
      {ANN_COLORS.map((c) => <button key={c} className={`sw ${a[key] === c ? 'on' : ''}`} style={{ background: c }} disabled={locked} onClick={() => onChange({ [key]: c })} />)}
      <label className="sw pick" title="Colore personalizzato" style={a[key] && !ANN_COLORS.includes(a[key]) ? { background: a[key] } : undefined}>
        <Ui name="plus" size={11} /><input type="color" disabled={locked} value={a[key] || '#8b7bff'} onChange={(e) => onChange({ [key]: e.target.value })} />
      </label>
    </div>
  )
  const seg = (key, opts) => (
    <div className="seg-mini">{opts.map(([v, l]) => <button key={String(v)} className={a[key] === v ? 'on' : ''} disabled={locked} onClick={() => onChange({ [key]: v })}>{l}</button>)}</div>
  )
  return (
    <div className="panel">
      <div className="panel-head"><span className="ph-ico"><Ui name="edit" size={20} /></span><div><h3>{label}</h3><small>Disegno sul foglio · non influisce sulla rete</small></div></div>
      {['text', 'rect', 'ellipse'].includes(a.type) && (
        <Section title="Testo">
          <textarea rows={3} value={a.text} disabled={locked} placeholder="Scrivi qui (anche doppio clic sul foglio)" onChange={(e) => onChange({ text: e.target.value })} />
          <div className="ann-row">
            <label className="field range"><span>Dimensione <b>{a.font_size}</b></span><input type="range" min="8" max="64" value={a.font_size} disabled={locked} onChange={(e) => onChange({ font_size: Number(e.target.value) })} /></label>
            <div className="seg-mini">
              <button className={a.bold ? 'on' : ''} disabled={locked} onClick={() => onChange({ bold: !a.bold })}><b>G</b></button>
              <button className={a.italic ? 'on' : ''} disabled={locked} onClick={() => onChange({ italic: !a.italic })}><i>C</i></button>
            </div>
          </div>
          {seg('align', [['left', 'Sinistra'], ['center', 'Centro'], ['right', 'Destra']])}
        </Section>
      )}
      <Section title="Colori">
        <div className="field"><span>{a.type === 'text' ? 'Colore del testo' : a.type === 'line' ? 'Colore della linea' : 'Bordo'}</span>{swatches('color')}</div>
        {['rect', 'ellipse', 'text'].includes(a.type) && <div className="field"><span>Riempimento</span>{swatches('fill', true)}</div>}
        {(a.fill || a.type === 'image') && <label className="field range"><span>Opacità <b>{Math.round(a.opacity * 100)}%</b></span>
          <input type="range" min="5" max="100" value={Math.round(a.opacity * 100)} disabled={locked} onChange={(e) => onChange({ opacity: Number(e.target.value) / 100 })} /></label>}
      </Section>
      {a.type !== 'image' && (
        <Section title={a.type === 'line' ? 'Linea' : 'Bordo'}>
          {seg('border', a.type === 'line' ? [['solid', 'Continua'], ['dashed', 'Tratteggiata'], ['dotted', 'Punteggiata']] : [['solid', 'Continuo'], ['dashed', 'Tratteggiato'], ['dotted', 'Punti'], ['none', 'Nessuno']])}
          <label className="field range"><span>Spessore <b>{a.border_width}px</b></span><input type="range" min="1" max="10" value={a.border_width} disabled={locked} onChange={(e) => onChange({ border_width: Number(e.target.value) })} /></label>
          {a.type === 'rect' && <label className="field range"><span>Angoli arrotondati <b>{a.radius}px</b></span><input type="range" min="0" max="60" value={a.radius} disabled={locked} onChange={(e) => onChange({ radius: Number(e.target.value) })} /></label>}
          {a.type === 'line' && <>
            <div className="field"><span>Frecce</span>{seg('arrow', [['none', 'Nessuna'], ['end', '→'], ['start', '←'], ['both', '↔']])}</div>
            <button className="wide" disabled={locked} onClick={() => onChange({ flip: !a.flip })}><Ui name="refresh" size={14} /> Inverti diagonale</button>
          </>}
        </Section>
      )}
      {a.type === 'image' && <Section title="Immagine"><button className="wide" disabled={locked} onClick={onImage}><Ui name="upload" size={14} /> Cambia immagine</button></Section>}
      <Section title="Livello">
        {seg('z', [[-1, 'Dietro i dispositivi'], [1, 'Sopra i dispositivi']])}
      </Section>
      <div className="row-end" style={{ marginTop: 14 }}>
        <button disabled={locked} onClick={onDuplicate}><Ui name="copy" size={14} /> Duplica</button>
        <button className="danger" disabled={locked} onClick={onDelete}><Ui name="trash" size={14} /> Elimina</button>
      </div>
    </div>
  )
}

// icon size control used inside the node's right-click menu (EVE-NG style "(px)" field)
function SizeRow({ value, onChange, disabled }) {
  return (
    <div className="size-row">
      <span className="g"><Ui name="fit" size={16} /></span>
      <span className="grow">Dimensione icona</span>
      <button className="mini" disabled={disabled || value <= 50} onClick={() => onChange(Math.max(50, value - 25))}>−</button>
      <b>{value}%</b>
      <button className="mini" disabled={disabled || value >= 250} onClick={() => onChange(Math.min(250, value + 25))}>+</button>
    </div>
  )
}

const ICON_NAMES = Object.keys(DEVICE_COLORS)

// Full node editor (PNETLab "Edit node" style), opened from the right-click menu or double click
function NodeEditDialog({ node, tpl, ns, lab, locked, running, configSet, onClose, onSave, onError, onEnableConfig, onEditConfig, onLive, onApplyNow, labRunning, onReloadTemplates }) {
  const [f, setF] = useState(() => ({
    name: node.name, description: node.description || '', image: node.image || '', icon: node.icon || '',
    cpus: node.cpus ?? '', ram: node.ram ?? '', nics: node.nics ?? '', nic_model: node.nic_model || '', console: node.console || '',
    mac: node.mac || '', extra: (node.extra_args || []).join(' '), size: node.size || 100, pinned: !!node.pinned,
    host_bridge: node.host_bridge || '', env: Object.entries(node.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
    mgmt_ip: node.mgmt_ip || '', mgmt_iface: node.mgmt_iface || '',
  }))
  const set = (k) => (e) => setF((o) => ({ ...o, [k]: e?.target ? (e.target.type === 'checkbox' ? e.target.checked : e.target.value) : e }))
  // custom icons the user uploads (SVG/PNG…): stored on the server, usable as this node's icon
  const [customIcons, setCustomIcons] = useState([])
  const loadIcons = useCallback(() => api('/icons').then(setCustomIcons).catch(() => {}), [])
  useEffect(() => { loadIcons() }, [loadIcons])
  const iconInput = useRef(null)
  const addIcon = async (file) => {
    if (!file) return
    const base = file.name.replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9 _.\-]/g, '-').slice(0, 48) || 'icona'
    try {
      const r = await uploadTo(`/api/icons/${encodeURIComponent(base)}/upload?filename=${encodeURIComponent(file.name)}`, file)
      await loadIcons(); set('icon')(`custom:${r.name}`)
    } catch (err) { onError(err) }
  }
  const delIcon = async (n) => {
    try {
      await api(`/icons/${encodeURIComponent(n)}`, { method: 'DELETE' })
      if (f.icon === `custom:${n}`) set('icon')('')
      loadIcons()
    } catch (err) { onError(err) }
  }
  if (!tpl) return null
  const qemu = tpl.kind === 'qemu', docker = tpl.kind === 'docker', vm = qemu || docker || tpl.kind === 'dynamips' || tpl.kind === 'iol'
  const hard = locked || running // things that need the node off
  const ifaces = interfaceNames(tpl, { nics: f.nics === '' ? undefined : Number(f.nics) })
  // images of the same type (EVE-NG/PNETLab naming <type>-<version>), or the Docker images on the server
  const imgOpts = (tpl.choices || []).map((c) => ({ name: c }))
  const num = (v) => (v === '' ? undefined : Number(v))
  const submit = (e) => {
    e.preventDefault()
    const env = Object.fromEntries(f.env.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => [l.split('=')[0], l.slice(l.indexOf('=') + 1)]))
    onSave({
      description: f.description.trim() || undefined, image: f.image.trim() || undefined, icon: f.icon || undefined,
      cpus: num(f.cpus), ram: num(f.ram), nics: num(f.nics), nic_model: f.nic_model || undefined, console: f.console || undefined,
      mac: f.mac.trim() || undefined, extra_args: f.extra.trim() ? f.extra.trim().split(/\s+/) : undefined,
      size: Number(f.size) === 100 ? undefined : Number(f.size), pinned: f.pinned || undefined,
      host_bridge: f.host_bridge.trim() || undefined, env: docker && Object.keys(env).length ? env : undefined,
      mgmt_ip: f.mgmt_ip.trim() || undefined, mgmt_iface: qemu ? f.mgmt_iface || undefined : undefined,
    }, f.name.trim())
  }
  return (
    <div className="modal-bg sheet-bg" onClick={onClose}>
      <form className="modal sheet" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="sheet-head">
          <span className="sheet-ico"><Icon name={f.icon || tpl.icon} size={34} /></span>
          <div className="grow"><h3>Modifica nodo</h3><small className="muted">{tpl.name}</small></div>
          <span className={`pill ${ns?.status || 'stopped'}`}>{running ? 'acceso' : 'spento'}</span>
          <button type="button" className="icon-btn" onClick={onClose} title="Chiudi"><Ui name="close" size={16} /></button>
        </div>

        <div className="sheet-body">
          {running && vm && <div className="warning small">Il nodo è acceso: nome, interfacce, NIC e MAC si possono cambiare solo da spento; CPU/RAM/immagine valgono al prossimo avvio.</div>}

          <fieldset><legend>Generale</legend>
            <div className="grid2">
              <label className="field"><span>Nome</span><input required pattern="[A-Za-z][A-Za-z0-9 _\-]{0,31}" disabled={hard} value={f.name} onChange={set('name')} /></label>
              <label className="field"><span>Template</span><input value={tpl.name} disabled /></label>
            </div>
            <label className="field"><span>Descrizione</span><input placeholder={tpl.description} value={f.description} onChange={set('description')} /></label>
          </fieldset>

          {vm && <fieldset><legend>Hardware</legend>
            <label className="field"><span>Immagine <button type="button" className="icon-btn inline" title="Aggiorna elenco" onClick={(e) => { e.preventDefault(); onReloadTemplates?.() }}><Ui name="refresh" size={13} /></button></span>
              <select value={f.image} onChange={set('image')}>
                <option value="">{tpl.image}{imgOpts.some((i) => i.name === tpl.image) ? '' : ' — non presente'}</option>
                {imgOpts.filter((i) => i.name !== tpl.image).map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
                {f.image && !imgOpts.some((i) => i.name === f.image) && <option value={f.image}>{f.image} — non presente</option>}
              </select>
            </label>
            <div className="grid3">
              <label className="field"><span>CPU</span><input type="number" min="1" max="64" placeholder={tpl.cpus} value={f.cpus} onChange={set('cpus')} /></label>
              <label className="field"><span>RAM (MB)</span><input type="number" min="64" step="64" placeholder={tpl.ram} value={f.ram} onChange={set('ram')} /></label>
              <label className="field"><span>Interfacce</span><input type="number" min="1" max="32" placeholder={tpl.nics} disabled={hard} value={f.nics} onChange={set('nics')} /></label>
            </div>
            <p className="hint ifaces">{ifaces.slice(0, 12).join(' · ')}{ifaces.length > 12 ? ` … (${ifaces.length})` : ''}</p>
            {qemu && <div className="grid3">
              <label className="field"><span>Modello NIC</span>
                <select disabled={hard} value={f.nic_model} onChange={set('nic_model')}>
                  <option value="">{tpl.nic_model} (template)</option>
                  {NIC_MODELS.filter((m) => m !== tpl.nic_model).map((m) => <option key={m}>{m}</option>)}
                </select>
              </label>
              <label className="field"><span>Consolle</span>
                <select value={f.console} onChange={set('console')}>
                  <option value="">{consoleLabel(tpl.console)} (template)</option>
                  {CONSOLES.filter(([c]) => c !== tpl.console).map(([c, l]) => <option key={c} value={c}>{l}</option>)}
                </select>
              </label>
              <label className="field"><span>Primo MAC</span><input placeholder="automatico" disabled={hard} value={f.mac} onChange={set('mac')}
                pattern="([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}" title="aa:bb:cc:dd:ee:ff — le altre interfacce seguono (+1)" /></label>
            </div>}
            {qemu && <label className="field"><span>Opzioni QEMU aggiuntive</span>
              <input className="mono" placeholder={tpl.extra_args?.join(' ') || 'es. -cpu host -smp sockets=1'} value={f.extra} onChange={set('extra')} /></label>}
            {docker && <label className="field"><span>Variabili d'ambiente <small className="muted">(una per riga, NOME=valore)</small></span>
              <textarea className="mono" rows={3} value={f.env} onChange={set('env')} /></label>}
          </fieldset>}

          {vm && <fieldset><legend>Gestione</legend>
            <div className="grid2">
              <label className="field"><span>IP di gestione {docker && <small className="muted">(vuoto = automatico)</small>}</span>
                <input className="mono" placeholder={docker ? '172.20.20.x automatico' : 'es. 172.20.20.11'} value={f.mgmt_ip} onChange={set('mgmt_ip')} disabled={docker && running} /></label>
              {qemu ? <label className="field"><span>Interfaccia sulla rete di gestione</span>
                <select value={f.mgmt_iface} disabled={hard} onChange={set('mgmt_iface')}>
                  <option value="">nessuna</option>
                  {ifaces.filter((i) => !lab.links.some((l) => l.endpoints.includes(`${node.name}:${i}`))).map((i) => <option key={i}>{i}</option>)}
                </select></label> : <div className="field"><span>Interfaccia</span><input value="eth0 (rete di gestione Docker)" disabled /></div>}
            </div>
            <p className="hint">{qemu ? 'L\'interfaccia scelta va sulla rete 172.20.20.0/24 insieme ai container; l\'IP va configurato anche dentro il dispositivo (es. nella startup config). Serve per Ansible/SSH e per l\'inventario.' : 'eth0 è sempre sulla rete di gestione 172.20.20.0/24.'}</p>
          </fieldset>}

          {tpl.kind === 'bridge' && <fieldset className="live"><legend>VLAN delle porte</legend>
            <SwitchPorts node={node} lab={lab} canEdit={!locked} running={labRunning} onChange={onLive} onApply={onApplyNow} bare />
          </fieldset>}
          {tpl.kind === 'nat' && <fieldset className="live"><legend>Rete NAT (internet)</legend>
            <label className="field"><span>Subnet del lab</span>
              <input placeholder="automatica (172.31.x.0/24)" value={node.subnet || ''} disabled={locked || running} onChange={(e) => onLive({ subnet: e.target.value || undefined })} /></label>
            {ns?.subnet && <div className="kv"><span>Rete</span><b className="mono">{ns.subnet}</b><span>Gateway / DNS</span><b className="mono">{ns.subnet.replace(/\.0\/\d+$/, '.1')}</b><span>DHCP</span><b className="mono">.10 – .254</b></div>}
            <p className="hint">Nei dispositivi: DHCP oppure un IP della subnet con gateway <code>.1</code>.</p>
          </fieldset>}
          {tpl.kind === 'vxlan' && <fieldset className="live"><legend>Collegamento verso un altro server (VXLAN)</legend>
            <div className="grid3">
              <label className="field"><span>IP dell'altro server</span><input className="mono" placeholder="192.168.12.50" value={node.remote || ''} disabled={locked}
                onChange={(e) => onLive({ remote: e.target.value || undefined })} /></label>
              <label className="field"><span>VNI</span><input type="number" min="1" max="16777215" placeholder="5001" value={node.vni ?? ''} disabled={locked}
                onChange={(e) => onLive({ vni: e.target.value === '' ? undefined : Number(e.target.value) })} /></label>
              <label className="field"><span>Porta UDP</span><input type="number" placeholder="4789" value={node.dstport ?? ''} disabled={locked}
                onChange={(e) => onLive({ dstport: e.target.value === '' ? undefined : Number(e.target.value) })} /></label>
            </div>
            <p className="hint">Sull'altro server crea lo stesso nodo con l'IP di questo server e lo stesso VNI. MTU 1450 sul tunnel.</p>
          </fieldset>}
          {tpl.kind === 'external' && <fieldset><legend>Rete esterna</legend>
            <label className="field"><span>Bridge dell'host</span><input placeholder={tpl.host_bridge} disabled={hard} value={f.host_bridge} onChange={set('host_bridge')} /></label>
          </fieldset>}

          {vm && <fieldset><legend>Startup config</legend>
            <div className="cfg-inline">
              <Toggle on={!!node.startup_config} disabled={!supportsConfig(tpl)} onChange={onEnableConfig} />
              <span className="grow muted small">{supportsConfig(tpl) ? <>All'avvio usa la config del set <b>{configSet}</b></> : 'Non supportata da questo template'}</span>
              <button type="button" className="mini" onClick={onEditConfig}><Ui name="config" size={14} /> Modifica config</button>
            </div>
          </fieldset>}

          <fieldset><legend>Icona</legend>
            <div className="icon-pick">
              <button type="button" className={!f.icon ? 'on' : ''} title={`Del template (${tpl.icon})`} onClick={() => set('icon')('')}><Icon name={tpl.icon} size={28} /><small>auto</small></button>
              {ICON_NAMES.map((ic) => (
                <button type="button" key={ic} className={f.icon === ic ? 'on' : ''} title={ic} onClick={() => set('icon')(ic)}><Icon name={ic} size={28} /><small>{ic}</small></button>
              ))}
            </div>
            <span className="pick-sec">Pack NetSO Line</span>
            <div className="icon-pick">
              {LINE_ICONS.map((ic) => (
                <button type="button" key={ic} className={f.icon === `line:${ic}` ? 'on' : ''} title={lineLabel(ic)} onClick={() => set('icon')(`line:${ic}`)}>
                  <Icon name={`line:${ic}`} size={30} /><small>{lineLabel(ic)}</small></button>
              ))}
            </div>
            <span className="pick-sec">Icone personali <small className="muted">(SVG, PNG… caricate da te)</small></span>
            <div className="icon-pick">
              {customIcons.map((ic) => (
                <button type="button" key={ic.name} className={`has-del ${f.icon === `custom:${ic.name}` ? 'on' : ''}`} title={ic.name} onClick={() => set('icon')(`custom:${ic.name}`)}>
                  <Icon name={`custom:${ic.name}`} size={30} /><small>{ic.name}</small>
                  <span className="pick-del" title="Elimina icona" onClick={(e) => { e.stopPropagation(); delIcon(ic.name) }}>×</span>
                </button>
              ))}
              <button type="button" className="icon-add" onClick={() => iconInput.current?.click()}><Ui name="upload" size={22} /><small>Aggiungi…</small></button>
              <input ref={iconInput} type="file" hidden accept=".svg,.png,.jpg,.jpeg,.gif,.webp" onChange={(e) => { addIcon(e.target.files[0]); e.target.value = '' }} />
            </div>
          </fieldset>

          <fieldset><legend>Disegno</legend>
            <div className="grid2">
              <label className="field range"><span>Dimensione icona <b>{f.size}%</b></span>
                <input type="range" min="50" max="250" step="5" value={f.size} onChange={set('size')} /></label>
              <label className="check"><input type="checkbox" checked={f.pinned} onChange={set('pinned')} /> Blocca la posizione sul foglio</label>
            </div>
          </fieldset>
        </div>

        <div className="sheet-foot">
          <span className="muted small">{lab.links.filter((l) => l.endpoints.some((e) => splitEp(e)[0] === node.name)).length} collegamenti · {ns?.console_port ? `telnet :${ns.console_port}` : 'consolle non attiva'}</span>
          <div className="spacer" />
          <button type="button" className="ghost" onClick={onClose}>Annulla</button>
          <button className="primary" disabled={locked}><Ui name="save" size={15} /> Applica</button>
        </div>
      </form>
    </div>
  )
}

function NodePanel({ node, tpl, ns, lab, locked, onChange, onRename, onAction, onConsole, onDelete, configSet, onEnableConfig, onEditConfig, onEdit, canEdit, running, onApply }) {
  const [nameDraft, setNameDraft] = useState(node.name)
  const status = ns?.status || 'stopped'
  const host = location.hostname
  const links = lab.links.map((l) => l.endpoints.map(splitEp)).filter((eps) => eps.some(([n]) => n === node.name))
  const num = (k) => (e) => onChange({ [k]: e.target.value === '' ? undefined : Number(e.target.value) })

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="ph-ico"><Icon name={node.icon || tpl?.icon} size={30} /></span>
        <div>
          <h3>{node.name}</h3>
          <small>{tpl?.description || node.template}</small>
        </div>
      </div>
      <div className={`status-line ${status}`}><span className={`dot ${status}`} />{status === 'running' ? 'In esecuzione' : status === 'error' ? 'Errore' : 'Fermo'}</div>
      {ns?.error && <div className="warning err small">{ns.error}</div>}

      {isVm(tpl) && (
        <div className="actions">
          {status === 'running'
            ? <><button onClick={() => onAction('stop')}><Ui name="stop" size={14} /> Ferma</button>
                {(ns?.console_port || ns?.container) && <button className="primary" onClick={onConsole}><Ui name="console" size={14} /> Consolle</button>}</>
            : <button className="primary" onClick={() => onAction('start')}><Ui name="play" size={14} /> Avvia</button>}
          {hasDisks(tpl) && status !== 'running' && <button className="ghost" onClick={() => onAction('wipe')}><Ui name="wipe" size={14} /> Azzera</button>}
        </div>
      )}

      {status === 'running' && (ns?.console_port || ns?.vnc_display != null) && (
        <div className="access">
          {ns.console_port && <div><span>Telnet</span><code>telnet {host} {ns.console_port}</code></div>}
          {ns.vnc_display != null && <div><span>VNC</span><code>{host}:{5900 + ns.vnc_display}</code></div>}
        </div>
      )}

      <button className="wide edit-all" onClick={onEdit}><Ui name="edit" size={15} /> Modifica tutte le proprietà…</button>
      <Section title="Proprietà">
      <label className="field"><span>Nome</span>
        <input value={nameDraft} disabled={locked} onChange={(e) => setNameDraft(e.target.value)}
          onBlur={() => nameDraft !== node.name && onRename(nameDraft)}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
      </label>
      <label className="field"><span>Template</span><input value={node.template} disabled /></label>
      {isVm(tpl) && <>
        <label className="field"><span>Immagine</span>
          <input placeholder={tpl.image} value={node.image || ''} onChange={(e) => onChange({ image: e.target.value || undefined })} />
        </label>
        <div className="grid3">
          <label className="field"><span>CPU</span><input type="number" min="1" placeholder={tpl.cpus} value={node.cpus ?? ''} onChange={num('cpus')} /></label>
          <label className="field"><span>RAM MB</span><input type="number" min="64" step="64" placeholder={tpl.ram} value={node.ram ?? ''} onChange={num('ram')} /></label>
          <label className="field"><span>NIC</span><input type="number" min="1" max="32" placeholder={tpl.nics} value={node.nics ?? ''} onChange={num('nics')} disabled={locked} /></label>
        </div>
      </>}
      {tpl?.kind === 'external' && (
        <label className="field"><span>Bridge host</span>
          <input placeholder={tpl.host_bridge} value={node.host_bridge || ''} disabled={locked} onChange={(e) => onChange({ host_bridge: e.target.value || undefined })} />
        </label>
      )}
      {status === 'running' && isVm(tpl) && <p className="hint">CPU/RAM/immagine valgono al prossimo avvio del nodo.</p>}
      </Section>

      {isVm(tpl) && <>
        <Section title="Startup config">
        <div className="cfg-inline">
          <Toggle on={!!node.startup_config} disabled={!supportsConfig(tpl)} onChange={onEnableConfig}
            title={supportsConfig(tpl) ? '' : 'Questo template non supporta la startup-config'} />
          <span className="grow muted small">{supportsConfig(tpl) ? <>set <b>{configSet}</b></> : 'non supportata'}</span>
          <button className="mini" onClick={onEditConfig}><Ui name="config" size={14} /> Modifica</button>
        </div>
        </Section>
      </>}

      {tpl?.kind === 'bridge' && <SwitchPorts node={node} lab={lab} canEdit={canEdit} running={running} onChange={onChange} onApply={onApply} />}
      {tpl?.kind === 'vxlan' && (
        <Section title="Collegamento remoto (VXLAN)">
          <div className="grid2">
            <label className="field"><span>IP dell'altro server</span><input className="mono" placeholder="192.168.12.50" value={node.remote || ''} disabled={locked}
              onChange={(e) => onChange({ remote: e.target.value || undefined })} /></label>
            <label className="field"><span>VNI</span><input type="number" min="1" max="16777215" placeholder="5001" value={node.vni ?? ''} disabled={locked}
              onChange={(e) => onChange({ vni: e.target.value === '' ? undefined : Number(e.target.value) })} /></label>
          </div>
          <label className="field"><span>Porta UDP</span><input type="number" placeholder="4789" value={node.dstport ?? ''} disabled={locked}
            onChange={(e) => onChange({ dstport: e.target.value === '' ? undefined : Number(e.target.value) })} /></label>
          <p className="hint">Sull'altro server NetSO crea lo stesso nodo VXLAN con l'IP di <b>questo</b> server e lo <b>stesso VNI</b>: i dispositivi collegati ai due nodi sono sullo stesso cavo. MTU 1450 sul tunnel (abbassa l'MTU nei dispositivi o alza quella della rete tra i server).</p>
        </Section>
      )}
      {tpl?.kind === 'nat' && (
        <Section title="Rete NAT (internet)">
          <label className="field"><span>Subnet del lab</span>
            <input placeholder="automatica (172.31.x.0/24)" value={node.subnet || ''} disabled={locked} onChange={(e) => onChange({ subnet: e.target.value || undefined })} /></label>
          {ns?.subnet ? (
            <div className="kv"><span>Rete</span><b className="mono">{ns.subnet}</b><span>Gateway</span><b className="mono">{ns.subnet.replace(/\.0\/\d+$/, '.1')}</b>
              <span>DHCP</span><b className="mono">.10 – .254</b><span>DNS</span><b className="mono">{ns.subnet.replace(/\.0\/\d+$/, '.1')}</b></div>
          ) : <p className="hint">Al deploy il server crea la rete, il DHCP e il NAT verso la sua connessione internet.</p>}
          <p className="hint">Nei dispositivi: DHCP (es. <code>ip dhcp</code> sul VPC, <code>ip address dhcp</code> su IOS) oppure un IP della subnet con gateway <code>.1</code>.</p>
        </Section>
      )}
      <Section title={`Collegamenti (${links.length})`}>
      {links.length === 0 ? <p className="hint">Nessuno — trascina la spina arancione del nodo su un altro nodo.</p> : (
        <ul className="links">
          {links.map((eps, i) => {
            const me = eps.find(([n]) => n === node.name), other = eps.find((e) => e !== me)
            return <li key={i}><code>{me[1]}</code> → {other[0]} <code>{other[1]}</code></li>
          })}
        </ul>
      )}
      </Section>
      <button className="danger wide" onClick={onDelete} disabled={locked || status === 'running'}><Ui name="trash" size={15} /> Elimina nodo</button>
    </div>
  )
}

// ------------------------------------------------------------- packet capture
const MAX_PKTS = 20000
const ROW_H = 22
const BPF_PRESETS = ['icmp or icmp6', 'arp', 'not arp and not stp and not lldp', 'tcp port 179', 'ospf', 'udp port 53 or udp port 67',
  'stp', 'ether proto 0x88cc', 'tcp port 22 or tcp port 23', 'vlan', 'host 10.0.0.1']
const PROTO_CLS = {
  ICMP: 'icmp', ICMPv6: 'icmp', ARP: 'arp', TCP: 'tcp', SSH: 'tcp', Telnet: 'tcp', HTTP: 'tcp', TLS: 'tcp',
  UDP: 'udp', DNS: 'svc', DHCP: 'svc', DHCPv6: 'svc', NTP: 'svc', SNMP: 'svc', Syslog: 'svc', TFTP: 'svc', mDNS: 'svc',
  OSPF: 'rt', BGP: 'rt', EIGRP: 'rt', RIP: 'rt', 'IS-IS': 'rt', LDP: 'rt', PIM: 'rt', BFD: 'rt', VRRP: 'rt', HSRP: 'rt',
  STP: 'l2', CDP: 'l2', LLDP: 'l2', LACP: 'l2', DTP: 'l2', VTP: 'l2', LOOP: 'l2',
}
const fmtSize = (b) => (b == null ? '' : b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(1)} MB`)
const fmtClock = (t) => t ? new Date(t * 1000).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''

// endpoints a capture can run on: link ends that belong to a VM or a container
function captureEndpoints(lab, tplByName) {
  const kind = (node) => tplByName[lab.nodes.find((n) => n.name === node)?.template]?.kind
  return lab.links.flatMap((l) => {
    const [a, b] = l.endpoints
    return [[a, b], [b, a]].filter(([ep]) => ['qemu', 'docker'].includes(kind(splitEp(ep)[0]))).map(([ep, peer]) => ({ ep, peer }))
  })
}

function CaptureDialog({ name, lab, state, tplByName, caps, initial, onClose, onChanged, onError, onWireshark, onOpenFile, onShowWs, onStopWs }) {
  const eps = useMemo(() => captureEndpoints(lab, tplByName), [lab, tplByName])
  const live = (ep) => state?.nodes?.[splitEp(ep)[0]]?.status === 'running'
  const runningCap = (ep) => caps.find((c) => c.running && !c.wireshark && `${c.node}:${c.iface}` === ep)
  const firstEp = initial?.node && (eps.find((e) => e.ep === `${initial.node}:${initial.iface}`) || eps.find((e) => splitEp(e.ep)[0] === initial.node))
  const [ep, setEp] = useState(firstEp?.ep || eps.find((e) => live(e.ep))?.ep || eps[0]?.ep || '')
  const [bpf, setBpf] = useState('')
  const [cur, setCur] = useState(() => (initial?.iface && runningCap(`${initial.node}:${initial.iface}`)?.id) || caps.find((c) => c.running)?.id || caps[0]?.id || null)
  const [info, setInfo] = useState(null)
  const [gone, setGone] = useState(false)
  const [, setTick] = useState(0)
  const [view, setView] = useState('')
  const [auto, setAuto] = useState(true)
  const [selPkt, setSelPkt] = useState(null)
  const [shark, setShark] = useState(false)
  const [copied, setCopied] = useState(false)
  const pk = useRef([])
  const body = useRef(null)
  const [scroll, setScroll] = useState({ top: 0, h: 400 })
  const started = useRef(false)

  const start = async (endpoint = ep, filter = bpf) => {
    if (!endpoint) return
    const [node, iface] = splitEp(endpoint)
    try {
      const c = await api(`/labs/${name}/captures`, { method: 'POST', body: { node, iface, filter } })
      await onChanged(); setCur(c.id)
    } catch (e) { onError(e) }
  }
  // opened from a link end (EVE-NG style): start capturing right away
  useEffect(() => {
    if (started.current) return
    started.current = true
    const e = initial?.auto && initial?.iface && `${initial.node}:${initial.iface}`
    if (e && !runningCap(e) && live(e)) start(e, '')
  })  // eslint-disable-line react-hooks/exhaustive-deps

  // live packets of the selected capture
  useEffect(() => {
    pk.current = []; setSelPkt(null); setInfo(null); setGone(false); setAuto(true); setTick((t) => t + 1)
    if (!cur) return
    const ws = new WebSocket(wsUrl(`/labs/${name}/captures/${encodeURIComponent(cur)}/live`))
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data)
      if (m.error) { setGone(true); return }
      if (m.packets.length) {
        pk.current.push(...m.packets)
        if (pk.current.length > MAX_PKTS) pk.current.splice(0, pk.current.length - MAX_PKTS)
      }
      setInfo(m.info); setTick((t) => t + 1)
    }
    return () => ws.close()
  }, [cur, name])

  useEffect(() => {
    const k = (e) => e.key === 'Escape' && onClose()
    addEventListener('keydown', k)
    return () => removeEventListener('keydown', k)
  }, [onClose])

  const terms = view.toLowerCase().split(/\s+/).filter(Boolean)
  const rows = terms.length
    ? pk.current.filter((p) => { const h = `${p.proto} ${p.src} ${p.dst} ${p.info}`.toLowerCase(); return terms.every((t) => (t[0] === '!' ? !h.includes(t.slice(1)) : h.includes(t))) })
    : pk.current
  const t0 = pk.current[0]?.t || 0

  useLayoutEffect(() => {
    const el = body.current
    if (el && auto) el.scrollTop = el.scrollHeight
  })
  const onScroll = (e) => {
    const el = e.currentTarget
    setScroll({ top: el.scrollTop, h: el.clientHeight })
    const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < ROW_H * 2
    if (atEnd !== auto) setAuto(atEnd)
  }
  const first = Math.max(0, Math.floor(scroll.top / ROW_H) - 10)
  const last = Math.min(rows.length, Math.ceil((scroll.top + scroll.h) / ROW_H) + 10)

  const capObj = caps.find((c) => c.id === cur)
  const ci = info || capObj
  const curEp = ci && `${ci.node}:${ci.iface}`
  const peer = eps.find((e) => e.ep === curEp)?.peer
  const act = async (path, method = 'POST') => {
    try { await api(`/labs/${name}/captures/${encodeURIComponent(cur)}${path}`, { method }); await onChanged() } catch (e) { onError(e) }
  }
  const remove = async () => {
    if (!confirm(`Eliminare la cattura ${cur}?`)) return
    await act('', 'DELETE'); setCur(caps.find((c) => c.id !== cur)?.id || null)
  }
  const sharkCmd = curEp ? `curl -sN '${apiUrl(`/labs/${name}/capture-stream`, { node: ci.node, iface: ci.iface, ...(ci.filter ? { filter: ci.filter } : {}) })}' | wireshark -k -i -` : ''
  const copy = async () => {
    try { await navigator.clipboard.writeText(sharkCmd); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* no clipboard */ }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal xl cap" onClick={(e) => e.stopPropagation()}>
        <ModalHead icon="capture" title="Cattura pacchetti" sub="tcpdump sulle interfacce dei collegamenti, in diretta e in file .pcap" onClose={onClose} />
        <div className="cap-body">
          <div className="cap-side">
            <form className="cap-new" onSubmit={(e) => { e.preventDefault(); start() }}>
              <div className="ctx-sec">Nuova cattura</div>
              <label className="field"><span>Interfaccia</span>
                <select value={ep} onChange={(e) => setEp(e.target.value)}>
                  {eps.map((e) => {
                    const [n, i] = splitEp(e.ep), [pn, pi] = splitEp(e.peer)
                    return <option key={e.ep} value={e.ep} disabled={!live(e.ep)}>{n} {i} → {pn} {pi}{live(e.ep) ? '' : ' (fermo)'}</option>
                  })}
                </select>
              </label>
              <label className="field"><span>Filtro (sintassi tcpdump / BPF)</span>
                <input list="bpf-presets" className="mono" placeholder="tutto il traffico" value={bpf} onChange={(e) => setBpf(e.target.value)} />
                <datalist id="bpf-presets">{BPF_PRESETS.map((p) => <option key={p} value={p} />)}</datalist>
              </label>
              <div className="cap-go">
                <button className="primary" type="button" disabled={!ep || !live(ep)} onClick={() => onWireshark(ep, bpf)} title="Apri la vera interfaccia di Wireshark nel browser">
                  <Ui name="capture" size={15} /> Wireshark
                </button>
                <button type="submit" disabled={!ep || !live(ep)} title="Tabella leggera dei pacchetti in questa finestra">
                  <span className="rec-dot" /> {runningCap(ep) ? 'Mostra tabella' : 'Tabella'}
                </button>
              </div>
              {!eps.length && <p className="hint">Nessun collegamento su VM o container da catturare.</p>}
              {eps.length > 0 && !eps.some((e) => live(e.ep)) && <p className="hint">Avvia i nodi per poter catturare.</p>}
            </form>
            <div className="ctx-sec">Catture</div>
            <div className="cap-list">
              {caps.map((c) => (
                <div key={c.id} className={`cap-row ${c.id === cur ? 'on' : ''}`} onClick={() => setCur(c.id)} title={c.id}>
                  <span className={`rec ${c.running ? 'on' : ''}`} />
                  <div className="grow">
                    <b>{c.node} <code>{c.iface}</code></b>
                    <small>{fmtClock(c.started)} · {c.packets != null ? `${c.packets} pacchetti · ` : ''}{fmtSize(c.size)}{c.filter ? ` · ${c.filter}` : ''}</small>
                  </div>
                </div>
              ))}
              {!caps.length && <p className="hint">Nessuna cattura. Puoi avviarne una anche col tasto destro su un collegamento.</p>}
            </div>
          </div>

          <div className="cap-main">
            {ci ? <>
              <div className="cap-bar">
                <span className={`rec ${ci.running ? 'on' : ''}`} />
                <div className="grow cap-title">
                  <div><b>{ci.node} <code>{ci.iface}</code></b>{peer && <span className="muted"> ⟷ {splitEp(peer)[0]} <code>{splitEp(peer)[1]}</code></span>}</div>
                  <small className="muted">{ci.running ? 'in corso' : 'fermata'} · {ci.packets ?? '?'} pacchetti · {fmtSize(ci.size)}{ci.filter ? <> · filtro <code>{ci.filter}</code></> : ''}</small>
                </div>
                {capObj?.wireshark && ci.running && <button className="ghost" onClick={() => onShowWs(cur)}><Ui name="capture" size={15} /> Mostra Wireshark</button>}
                {ci.running && <button className="ghost danger-t" onClick={() => (capObj?.wireshark ? onStopWs(cur) : act('/stop'))}><Ui name="stop" size={14} /> Ferma</button>}
                {!ci.running && <button className="ghost" onClick={() => onOpenFile(cur)} title="Apri il file nel Wireshark integrato"><Ui name="capture" size={15} /> Apri in Wireshark</button>}
                <a className="btn ghost" href={apiUrl(`/labs/${name}/captures/${encodeURIComponent(cur)}/pcap`)} download><Ui name="download" size={15} /> .{ci.format || 'pcap'}</a>
                {!capObj?.wireshark && <button className={`ghost ${shark ? 'on' : ''}`} onClick={() => setShark((v) => !v)} disabled={!live(curEp)} title="Apri la stessa interfaccia in Wireshark sul tuo PC"><Ui name="capture" size={15} /> Wireshark sul PC</button>}
                <button className="icon-btn" title="Elimina cattura" onClick={remove}><Ui name="trash" size={15} /></button>
              </div>
              {ci.error && <div className="warning err small">✖ tcpdump: {ci.error}</div>}
              {shark && (
                <div className="cap-shark">
                  <code className="mono">{sharkCmd}</code>
                  <button className="ghost" onClick={copy}><Ui name="copy" size={14} /> {copied ? 'Copiato' : 'Copia'}</button>
                  <p className="hint">Incolla nel terminale del tuo PC: Wireshark riceve i pacchetti in diretta dal server. Su macOS usa <code>/Applications/Wireshark.app/Contents/MacOS/Wireshark</code> al posto di <code>wireshark</code>.</p>
                </div>
              )}
              <div className="cap-tools">
                <input className="mono grow" placeholder="Filtra la vista: es. icmp 10.0.0.1 !arp" value={view} onChange={(e) => setView(e.target.value)} />
                <button className={`ghost ${auto ? 'on' : ''}`} title="Scorrimento automatico" onClick={() => setAuto((v) => !v)}><Ui name={auto ? 'pause' : 'play'} size={14} /> {auto ? 'Segui' : 'In pausa'}</button>
                <span className="muted small">{rows.length}{terms.length ? ` / ${pk.current.length}` : ''} righe</span>
              </div>
              <div className="pk">
                <div className="pk-row pk-head"><span>N.</span><span>Tempo</span><span>Sorgente</span><span>Destinazione</span><span>Proto</span><span>Lung.</span><span>Info</span></div>
                <div className="pk-body" ref={body} onScroll={onScroll}>
                  <div style={{ height: rows.length * ROW_H, position: 'relative' }}>
                    {rows.slice(first, last).map((p, k) => (
                      <div key={p.n} className={`pk-row p-${PROTO_CLS[p.proto] || 'other'} ${selPkt?.n === p.n ? 'on' : ''}`}
                        style={{ top: (first + k) * ROW_H }} onClick={() => { setSelPkt(p); setAuto(false) }}>
                        <span>{p.n}</span><span>{(p.t - t0).toFixed(3)}</span><span>{p.src}</span><span>{p.dst}</span>
                        <span>{p.proto}</span><span>{p.len}</span><span>{p.info}</span>
                      </div>
                    ))}
                  </div>
                  {!rows.length && (
                    <p className="hint pk-empty">{gone
                      ? capObj?.wireshark ? 'Questa cattura è registrata dal Wireshark integrato: guardala lì.' : 'I pacchetti di questa cattura non sono in memoria: premi «Apri in Wireshark» o scarica il file.'
                      : ci.running ? 'In attesa di pacchetti…' : 'Nessun pacchetto.'}</p>
                  )}
                </div>
              </div>
              {selPkt && (
                <div className="pk-detail">
                  <div className="kv">
                    <span>Pacchetto</span><b>#{selPkt.n} · {fmtClock(selPkt.t)}.{String(Math.round((selPkt.t % 1) * 1e6)).padStart(6, '0')}</b>
                    <span>Da → a</span><b className="mono">{selPkt.src} → {selPkt.dst}</b>
                    <span>Protocollo</span><b>{selPkt.proto} · {selPkt.len} byte</b>
                  </div>
                  <code className="mono">{selPkt.info}</code>
                </div>
              )}
            </> : (
              <div className="cap-empty">
                <Ui name="capture" size={40} />
                <p>Scegli un'interfaccia a sinistra e premi <b>Wireshark</b> (interfaccia completa)<br />o <b>Tabella</b> (vista leggera qui), oppure tasto destro su un collegamento.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function LabView(props) {
  return <ReactFlowProvider><Editor {...props} /></ReactFlowProvider>
}
