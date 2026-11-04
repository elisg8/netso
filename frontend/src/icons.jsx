// NetSO's own icon set: duotone device icons (each type has its colour), classic 3D device icons
// (drawn here, in the spirit of network diagrams) and duotone UI glyphs.
import { useId, useSyncExternalStore } from 'react'
import { apiUrl } from './api.js'

// ------------------------------------------------------------- device icon style (per browser)
const ICON_STYLES = ['modern', 'classic', 'line', 'rack']
let iconStyle = (() => {
  try { const v = localStorage.getItem('netso.icons'); return ICON_STYLES.includes(v) ? v : 'line' } catch { return 'line' }
})()
const subs = new Set()
export function setIconStyle(v) {
  iconStyle = v
  try { localStorage.setItem('netso.icons', v) } catch { /* ignore */ }
  subs.forEach((f) => f())
}
export const useIconStyle = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f) }, () => iconStyle)

// ------------------------------------------------------------- devices (32x32)
// body: soft-filled outline in the type colour; lines: light details; dots: solid accents
const DEVICES = {
  router: {
    c: '#5f8bff',
    body: <path d="M16 3.2 27.2 9.6v12.8L16 28.8 4.8 22.4V9.6z" />,
    lines: <path d="M9.5 16h5M14.5 16l5.8-5.3M14.5 16l5.8 5.3M17.3 10.2l3.3.3-.2 3.2M17.3 21.8l3.3-.3-.2-3.2" />,
    dots: <circle cx="9.3" cy="16" r="1.7" />,
  },
  switch: {
    c: '#34d399',
    body: <rect x="3.5" y="8" width="25" height="16" rx="4.5" />,
    lines: <path d="M8.5 13.2h13.5M19 10.6l3 2.6-3 2.6M23.5 18.8H10M13 16.2l-3 2.6 3 2.6" />,
  },
  l3switch: {
    c: '#2dd4bf',
    body: <rect x="3.5" y="7" width="25" height="18" rx="4.5" />,
    lines: <path d="M10.5 11.5l11 9M21.5 11.5l-11 9M10.3 14.3v-2.9h2.9M21.7 14.3v-2.9h-2.9M10.3 17.7v2.9h2.9M21.7 17.7v2.9h-2.9" />,
  },
  bridge: {
    c: '#a3e635',
    body: <rect x="3.5" y="15" width="25" height="10" rx="3.5" />,
    lines: <path d="M9 15V9M16 15V9M23 15V9M8 20h2M13 20h2M18 20h2M23 20h1" />,
    dots: <><circle cx="9" cy="7.5" r="1.9" /><circle cx="16" cy="7.5" r="1.9" /><circle cx="23" cy="7.5" r="1.9" /></>,
  },
  firewall: {
    c: '#fb7185',
    body: <path d="M16 3.5 26.5 7.5v7.3c0 6.6-4.4 11.3-10.5 13.7C9.9 26.1 5.5 21.4 5.5 14.8V7.5z" />,
    lines: <path d="M9.5 12.3h13M9.5 17.3h13M12 22.3h8M13.5 12.3v5M18.5 12.3v5M16 17.3v5" />,
  },
  server: {
    c: '#a78bfa',
    body: <><rect x="6" y="4" width="20" height="7.2" rx="2.2" /><rect x="6" y="12.4" width="20" height="7.2" rx="2.2" /><rect x="6" y="20.8" width="20" height="7.2" rx="2.2" /></>,
    lines: <path d="M15 7.6h7M15 16h7M15 24.4h7" />,
    dots: <><circle cx="10.2" cy="7.6" r="1.3" /><circle cx="10.2" cy="16" r="1.3" /><circle cx="10.2" cy="24.4" r="1.3" /></>,
  },
  host: {
    c: '#94a3b8',
    body: <rect x="3.8" y="4.8" width="24.4" height="16.6" rx="3" />,
    lines: <path d="M9 10.3l3 2.6-3 2.6M14.2 16h5.5M12.5 27h7M16 21.4V27" />,
  },
  container: {
    c: '#22d3ee',
    body: <path d="M16 3.8 27 9.9v12.2L16 28.2 5 22.1V9.9z" />,
    lines: <path d="M5 9.9l11 6.1 11-6.1M16 16v12.2M8.8 14.6v7M12.4 16.6v7" />,
  },
  cloud: {
    c: '#38bdf8',
    body: <path d="M9.4 25.5h13.8a5.6 5.6 0 0 0 .9-11.1A8.1 8.1 0 0 0 8.4 12.9a6.3 6.3 0 0 0 1 12.6z" />,
    lines: <path d="M12.2 21h7.6M12.2 21l3.8-4M19.8 21 16 17" />,
    dots: <><circle cx="12.2" cy="21" r="1.4" /><circle cx="19.8" cy="21" r="1.4" /><circle cx="16" cy="16.8" r="1.4" /></>,
  },
}
export const DEVICE_COLORS = Object.fromEntries(Object.entries(DEVICES).map(([k, v]) => [k, v.c]))

// ------------------------------------------------------------- "NetSO Line" pack (48x48), flat outline like CML
// Drawn for NetSO. Colours come from CSS (--ico-bg / --ico-fg) so the pack follows the light/dark theme.
const V = (x, y, dx, dy) => `M${x - dx - dy * 0.8},${y - dy + dx * 0.8}L${x},${y}L${x - dx + dy * 0.8},${y - dy - dx * 0.8}`  // arrow head at (x,y) pointing (dx,dy)
const TILE = <rect x="5" y="5" width="38" height="38" rx="9" className="lb" />
const LINE = {
  router: <><circle cx="24" cy="24" r="19" className="lb" /><path d={`M26 22l7-7${V(33, 15, 3.2, -3.2)}M22 26l-7 7${V(15, 33, -3.2, 3.2)}M15 15l6 6${V(21, 21, 3.2, 3.2)}M33 33l-6-6${V(27, 27, -3.2, -3.2)}`} /></>,
  switch: <>{TILE}<path d={`M12 19h9${V(12, 19, -4, 0)}M27 19h9${V(36, 19, 4, 0)}M12 29h9${V(12, 29, -4, 0)}M27 29h9${V(36, 29, 4, 0)}`} /></>,
  // L3 / multilayer switch, EVE-NG style: a central hub with eight arrows radiating outward
  l3switch: <>{TILE}<circle cx="24" cy="24" r="5" /><path d={`M32 24l9 0${V(41, 24, 4, 0)}M16 24l-9 0${V(7, 24, -4, 0)}M24 16v-9${V(24, 7, 0, -4)}M24 32v9${V(24, 41, 0, 4)}M29.6 18.4l6.4-6.4${V(36, 12, 2.9, -2.9)}M18.4 18.4l-6.4-6.4${V(12, 12, -2.9, -2.9)}M29.6 29.6l6.4 6.4${V(36, 36, 2.9, 2.9)}M18.4 29.6l-6.4 6.4${V(12, 36, -2.9, 2.9)}`} /></>,
  bridge: <><rect x="4" y="15" width="40" height="18" rx="6" className="lb" /><path d="M11 24h3M18 24h3M25 24h3M32 24h4" strokeWidth="3.2" /></>,
  firewall: <>{TILE}<path d="M5 17h38M5 29h38M17 5v12M31 5v12M24 17v12M17 29v14M31 29v14" /></>,
  server: <><rect x="12" y="4" width="24" height="40" rx="5" className="lb" /><path d="M17 13h14M17 19h14M17 25h8" /><circle cx="18" cy="36" r="1.6" className="lf" /><circle cx="24" cy="36" r="1.6" className="lf" /></>,
  host: <><rect x="6" y="9" width="36" height="24" rx="3" className="lb" /><path d="M24 33v6M15 40h18" /></>,
  laptop: <><rect x="10" y="11" width="28" height="19" rx="2.5" className="lb" /><path d="M4 36h40l-3-6H7z" className="lb" /></>,
  container: <><path d="M24 5l17 9.5v19L24 43 7 33.5v-19z" className="lb" /><path d="M7 14.5l17 9.5 17-9.5M24 24v19M13 21v12M18 24v12" /></>,
  // Docker: a solid whale (our own drawing)
  docker: <>
    <g className="lf">
      {/* whale body + fishtail in one outline: rounded head on the left, domed back,
          two tail flukes flaring up and down on the right with a notch between them */}
      <path d="M6 26c0-7 6-12 15-12s15 4 16 10l6-5c.6-.5 1.5 0 1.5.8v12c0 .8-.9 1.3-1.5.8l-6-5c-1 6-7 10-16 10S6 33 6 26z" />
      {/* spout droplets from the blowhole near the head */}
      <circle cx="16" cy="9.5" r="1.3" />
      <circle cx="19.2" cy="7.2" r="1.1" />
      <circle cx="13.5" cy="7.5" r="1" />
    </g>
    {/* eye */}
    <circle cx="13.5" cy="24.5" r="1.5" className="lb" stroke="none" />
  </>,
  cloud: <path d="M14 37h21a9 9 0 0 0 1.5-17.9A12.5 12.5 0 0 0 12.7 17 10 10 0 0 0 14 37z" className="lb" />,
  globe: <><circle cx="24" cy="24" r="18" className="lb" /><path d="M6 24h36M24 6c-6 6-6 30 0 36M24 6c6 6 6 30 0 36M9 14h30M9 34h30" /></>,
  ap: <><rect x="8" y="28" width="32" height="12" rx="5" className="lb" /><path d="M17 20a10 10 0 0 1 14 0M12 15a17 17 0 0 1 24 0" /><circle cx="24" cy="34" r="1.6" className="lf" /></>,
  phone: <><rect x="7" y="18" width="34" height="22" rx="5" className="lb" /><path d="M11 18v-5a4 4 0 0 1 4-4h18a4 4 0 0 1 4 4v5M15 26h4M22 26h4M29 26h4M15 32h4M22 32h4M29 32h4" /></>,
  lb: <>{TILE}<path d={`M11 24h9M20 24l13-9${V(33, 15, 4, -2.8)}M20 24h13${V(33, 24, 4, 0)}M20 24l13 9${V(33, 33, 4, 2.8)}`} /><circle cx="11" cy="24" r="1.8" className="lf" /></>,
  storage: <><ellipse cx="24" cy="11" rx="15" ry="5.5" className="lb" /><path d="M9 11v26c0 3 6.7 5.5 15 5.5s15-2.5 15-5.5V11" className="lb" /><path d="M9 20c0 3 6.7 5.5 15 5.5s15-2.5 15-5.5M9 29c0 3 6.7 5.5 15 5.5s15-2.5 15-5.5" /></>,
  vm: <>{TILE}<path d="M14 15h20v14H14zM20 34h8" /><path d="M18 20l3 2-3 2M23 25h4" /></>,
}
export const LINE_ICONS = Object.keys(LINE)
const LINE_LABEL = { router: 'router', switch: 'switch', l3switch: 'switch L3', bridge: 'hub/bridge', firewall: 'firewall', server: 'server', host: 'desktop',
  laptop: 'laptop', container: 'container', docker: 'Docker', cloud: 'cloud', globe: 'internet', ap: 'access point', phone: 'telefono IP', lb: 'load balancer', storage: 'storage', vm: 'VM' }
export const lineLabel = (n) => LINE_LABEL[n] || n

// LineIcon (supports several outline packs) is defined below, after the extra packs.

const deviceOf = (name) => DEVICES[name] || (name === 'docker' && DEVICES.container) || DEVICES.router

// ---- "NetSO Rack": a second flat-outline pack in the same spirit as NetSO Line, but devices drawn as
// front-view appliances with ports (our own drawings). 48x48, same stroke/theme as LINE.
const port = (x, y) => <rect key={`${x},${y}`} x={x} y={y} width="3.4" height="3.4" rx=".6" className="lf" />
const portRow = (y, xs) => xs.map((x) => port(x, y))
const LINE_RACK = {
  router: <>
    <rect x="5" y="19" width="38" height="15" rx="4" className="lb" />
    <path d={`M15 19v-6${V(15, 13, 0, -4)}M27 13v6${V(27, 19, 0, 4)}`} />
    <path d="M11 29h4M18 29h4M25 29h4" />
    <circle cx="37.5" cy="25.5" r="1.6" className="lf" />
  </>,
  switch: <>
    <rect x="4" y="15" width="40" height="18" rx="4" className="lb" />
    {portRow(20, [9, 14, 19, 24, 29, 34])}
    {portRow(26, [9, 14, 19, 24, 29, 34])}
  </>,
  l3switch: <>
    <rect x="4" y="17" width="40" height="16" rx="4" className="lb" />
    {portRow(25, [9, 14, 19, 24, 29, 34])}
    <path d={`M16 17v-5${V(16, 12, 0, -4)}M32 12v5${V(32, 17, 0, 4)}`} />
  </>,
  bridge: <>
    <rect x="6" y="19" width="36" height="12" rx="6" className="lb" />
    <path d={`M14 25h8${V(22, 25, 4, 0)}M34 25h-8${V(26, 25, -4, 0)}`} />
    <circle cx="11" cy="25" r="1.6" className="lf" /><circle cx="37" cy="25" r="1.6" className="lf" />
  </>,
  firewall: <>
    <rect x="8" y="9" width="32" height="30" rx="4" className="lb" />
    <path d="M8 19h32M8 29h32M18 9v10M30 9v10M24 19v10M18 29v10M30 29v10" />
  </>,
  server: <>
    <rect x="13" y="5" width="22" height="38" rx="4" className="lb" />
    <path d="M18 13h12M18 21h12M18 29h12" />
    <circle cx="19.5" cy="37" r="1.5" className="lf" /><circle cx="24.5" cy="37" r="1.5" className="lf" />
  </>,
  host: <>
    <rect x="6" y="9" width="36" height="23" rx="3" className="lb" />
    <path d="M24 32v5M16 40h16M13 16l4.5 3.3-4.5 3.3M19 22.6h7" />
  </>,
  container: <>
    <rect x="6" y="15" width="36" height="20" rx="2.2" className="lb" />
    <path d="M12 15v20M18 15v20M24 15v20M30 15v20M36 15v20" />
  </>,
  cloud: <>
    <path d="M14 36h20a8 8 0 0 0 1-15.9A11 11 0 0 0 13 18a9 9 0 0 0 1 18z" className="lb" />
    <path d="M18 30l6-6 6 6" />
    <circle cx="18" cy="30" r="1.7" className="lf" /><circle cx="30" cy="30" r="1.7" className="lf" /><circle cx="24" cy="24" r="1.7" className="lf" />
  </>,
}
const LINE_PACKS = { line: LINE, rack: LINE_RACK }

function LineIcon({ name, size, pack = 'line' }) {
  const set = LINE_PACKS[pack] || LINE
  return (
    <svg className="dev-ico line" width={size} height={size} viewBox="0 0 48 48" aria-hidden="true"
      fill="none" stroke="var(--ico-fg)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {set[name] || (name === 'docker' && set.container) || set.router}
    </svg>
  )
}

// an icon the user uploaded (served by the API as a plain image)
function CustomIcon({ name, size }) {
  return <img className="dev-ico custom" src={apiUrl(`/icons/${encodeURIComponent(name)}`)} width={size} height={size} alt=""
    style={{ objectFit: 'contain' }} />
}

export default function Icon({ name, size = 28 }) {
  const style = useIconStyle()
  if (name?.startsWith('custom:')) return <CustomIcon name={name.slice(7)} size={size} />  // user-uploaded icon
  if (name?.startsWith('line:')) return <LineIcon name={name.slice(5)} size={size} />  // pack icon chosen for this node
  if (style === 'line') return <LineIcon name={name} size={size} />
  if (style === 'rack') return <LineIcon name={name} size={size} pack="rack" />
  if (style === 'classic') return <ClassicIcon name={name} size={size} />
  const d = deviceOf(name)
  return (
    <svg className="dev-ico" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <g fill={d.c} fillOpacity=".17" stroke={d.c}>{d.body}</g>
      <g fill="none" stroke="#eef1ff" strokeOpacity=".92">{d.lines}</g>
      {d.dots && <g fill={d.c} stroke="none">{d.dots}</g>}
    </svg>
  )
}


// ------------------------------------------------------------- classic 3D devices (48x48)
// arrow from (x1,y1) to (x2,y2) with a filled head, flattened to sit on a top face
function arrow(x1, y1, x2, y2, h = 3.4) {
  const a = Math.atan2(y2 - y1, x2 - x1), w = h * 0.62
  const bx = x2 - Math.cos(a) * h, by = y2 - Math.sin(a) * h
  const px = -Math.sin(a) * w, py = Math.cos(a) * w
  return <g key={`${x1},${y1}`}><path d={`M${x1},${y1}L${bx},${by}`} /><path d={`M${x2},${y2}L${bx + px},${by + py}L${bx - px},${by - py}Z`} fill="#fff" stroke="none" /></g>
}

const PAL = {
  steel: ['#c9d6e6', '#6f87a3', '#3d5068'],
  teal: ['#bfe7e6', '#4f9a9c', '#2b5f63'],
  brick: ['#f2a28f', '#c2513e', '#7e2b20'],
  grey: ['#dfe4ec', '#8c96a6', '#4f5868'],
  green: ['#cde8c0', '#6a9a57', '#3d6230'],
  cyan: ['#bfe9f7', '#3aa6c9', '#1d6683'],
}

function box(id, [top, mid, dark], x, y, w, h, d) {
  return <>
    <path d={`M${x},${y}l${d},${-d * 0.7}h${w}l${-d},${d * 0.7}z`} fill={top} stroke={dark} strokeWidth=".8" />
    <path d={`M${x + w},${y}l${d},${-d * 0.7}v${h}l${-d},${d * 0.7}z`} fill={dark} stroke={dark} strokeWidth=".8" />
    <rect x={x} y={y} width={w} height={h} fill={`url(#${id}f)`} stroke={dark} strokeWidth=".8" />
    <defs><linearGradient id={`${id}f`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={mid} /><stop offset="1" stopColor={dark} /></linearGradient></defs>
  </>
}

const CLASSIC = {
  router: (id) => {
    const [top, mid, dark] = PAL.steel
    return <>
      <defs>
        <linearGradient id={`${id}s`} x1="0" x2="1"><stop offset="0" stopColor={dark} /><stop offset=".45" stopColor={mid} /><stop offset="1" stopColor={dark} /></linearGradient>
        <radialGradient id={`${id}t`} cx=".4" cy=".35" r=".8"><stop offset="0" stopColor="#eef3f9" /><stop offset="1" stopColor={top} /></radialGradient>
      </defs>
      <path d="M5 20v10c0 4.6 8.5 8.3 19 8.3s19-3.7 19-8.3V20" fill={`url(#${id}s)`} stroke={dark} strokeWidth=".8" />
      <ellipse cx="24" cy="20" rx="19" ry="8.3" fill={`url(#${id}t)`} stroke={dark} strokeWidth=".8" />
      <g stroke="#fff" strokeWidth="1.9" strokeLinecap="round" style={{ filter: 'drop-shadow(0 .6px .4px #0006)' }}>
        {arrow(26.5, 18.6, 35, 15.4)}{arrow(21.5, 21.4, 13, 24.6)}{arrow(11.5, 16, 19.8, 18.4)}{arrow(36.5, 24, 28.2, 21.6)}
      </g>
    </>
  },
  switch: (id) => <>
    {box(id, PAL.steel, 4, 20, 32, 14, 9)}
    <g stroke="#fff" strokeWidth="1.7" strokeLinecap="round">{arrow(11, 16.6, 30, 16.6)}{arrow(37.5, 15.2, 18.5, 15.2)}</g>
    <g fill="#24324a">{[8, 13, 18, 23, 28].map((x) => <rect key={x} x={x} y="27" width="3.6" height="3" rx=".6" />)}</g>
  </>,
  l3switch: (id) => <>
    {box(id, PAL.teal, 4, 20, 32, 14, 9)}
    <g stroke="#fff" strokeWidth="1.6" strokeLinecap="round">{arrow(23, 15.8, 31, 14)}{arrow(20, 17.3, 12, 19.1)}{arrow(12.5, 14, 19.5, 15.4)}{arrow(36, 18, 28.5, 16.4)}</g>
    <g fill="#163b3f">{[8, 13, 18, 23, 28].map((x) => <rect key={x} x={x} y="27" width="3.6" height="3" rx=".6" />)}</g>
  </>,
  bridge: (id) => <>
    {box(id, PAL.green, 3, 24, 34, 9, 8)}
    <g fill="#1f3518">{[7, 12, 17, 22, 27, 32].map((x) => <rect key={x} x={x - 1} y="27" width="3.4" height="3" rx=".6" />)}</g>
    <g stroke="#fff" strokeWidth="1.5" strokeLinecap="round">{arrow(10, 20.8, 32, 20.8, 3)}</g>
  </>,
  firewall: (id) => <>
    {box(id, PAL.brick, 6, 15, 28, 24, 8)}
    <g stroke="#f8d3c9" strokeWidth="1" strokeOpacity=".8" fill="none">
      <path d="M6 21h28M6 27h28M6 33h28M13 15v6M27 15v6M20 21v6M13 27v6M27 27v6M20 33v6" />
    </g>
  </>,
  server: (id) => <>
    {box(id, PAL.grey, 13, 10, 17, 32, 7)}
    <g stroke="#2a3140" strokeWidth="1.3" strokeLinecap="round"><path d="M16 15h11M16 19h11M16 23h11" /></g>
    <circle cx="17" cy="36" r="1.4" fill="#4ade80" /><circle cx="21" cy="36" r="1.4" fill="#60a5fa" />
  </>,
  host: (id) => <>
    <defs><linearGradient id={`${id}scr`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#7cc4ff" /><stop offset="1" stopColor="#2563eb" /></linearGradient></defs>
    <path d="M18 34h12l2 6H16z" fill="#8c96a6" stroke="#4f5868" strokeWidth=".8" />
    <rect x="11" y="40" width="26" height="3" rx="1.2" fill="#6b7586" />
    <rect x="5" y="7" width="38" height="27" rx="2.5" fill="#dfe4ec" stroke="#4f5868" strokeWidth=".9" />
    <rect x="8" y="10" width="32" height="20" rx="1" fill={`url(#${id}scr)`} />
    <path d="M12 15l4 3-4 3M18 22h7" stroke="#fff" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
  </>,
  container: (id) => <>
    {box(id, PAL.cyan, 5, 18, 30, 20, 8)}
    <g stroke="#0e3f52" strokeWidth="1.2" strokeOpacity=".75"><path d="M10 20v16M15 20v16M20 20v16M25 20v16M30 20v16" /></g>
  </>,
  cloud: (id) => <>
    <defs><linearGradient id={`${id}c`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#f2f8ff" /><stop offset="1" stopColor="#9cc9f2" /></linearGradient></defs>
    <path d="M13 38h23a8 8 0 0 0 1.4-15.9A11.5 11.5 0 0 0 15.1 19 9.5 9.5 0 0 0 13 38z" fill={`url(#${id}c)`} stroke="#4a7fb0" strokeWidth="1.1" />
    <path d="M17 31.5h14M17 31.5l7-6.5 7 6.5" stroke="#2f6aa3" strokeWidth="1.4" fill="none" strokeLinecap="round" />
    <circle cx="17" cy="31.5" r="2" fill="#2f6aa3" /><circle cx="31" cy="31.5" r="2" fill="#2f6aa3" /><circle cx="24" cy="25" r="2" fill="#2f6aa3" />
  </>,
}

function ClassicIcon({ name, size }) {
  const id = useId().replace(/:/g, '')
  const draw = CLASSIC[name] || (name === 'docker' && CLASSIC.container) || CLASSIC.router
  return (
    <svg className="dev-ico classic" width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      {draw(id)}
    </svg>
  )
}

// ------------------------------------------------------------- UI glyphs (24x24)
// <F> is the soft duotone layer, the rest is the outline
const F = (p) => <path fill="currentColor" fillOpacity=".2" stroke="none" {...p} />
const FC = (p) => <circle fill="currentColor" fillOpacity=".2" stroke="none" {...p} />
const FR = (p) => <rect fill="currentColor" fillOpacity=".2" stroke="none" {...p} />
const FOLDER = 'M3 7.6A2.6 2.6 0 0 1 5.6 5h3.6l2.2 2.2h7A2.6 2.6 0 0 1 21 9.8v7.6a2.6 2.6 0 0 1-2.6 2.6H5.6A2.6 2.6 0 0 1 3 17.4z'

const ui = {
  plus: <><FC cx="12" cy="12" r="9" /><path d="M12 7.5v9M7.5 12h9" /></>,
  minus: <path d="M6 12h12" />,
  close: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  chev: <path d="M9.5 6.5 15 12l-5.5 5.5" />,
  back: <path d="M14.5 6.5 9 12l5.5 5.5" />,
  more: <><circle cx="5.5" cy="12" r="1.5" fill="currentColor" /><circle cx="12" cy="12" r="1.5" fill="currentColor" /><circle cx="18.5" cy="12" r="1.5" fill="currentColor" /></>,
  nodes: <><FR x="3.5" y="3.5" width="7" height="7" rx="2" /><FR x="13.5" y="13.5" width="7" height="7" rx="2" /><rect x="3.5" y="3.5" width="7" height="7" rx="2" /><rect x="13.5" y="3.5" width="7" height="7" rx="2" /><rect x="3.5" y="13.5" width="7" height="7" rx="2" /><rect x="13.5" y="13.5" width="7" height="7" rx="2" /></>,
  links: <><FC cx="6" cy="6.5" r="2.6" /><FC cx="18" cy="6.5" r="2.6" /><FC cx="12" cy="18" r="2.6" /><circle cx="6" cy="6.5" r="2.6" /><circle cx="18" cy="6.5" r="2.6" /><circle cx="12" cy="18" r="2.6" /><path d="M8.6 6.5h6.8M7.3 8.8l3.4 6.9M16.7 8.8l-3.4 6.9" /></>,
  refresh: <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5V9H15" />,
  fit: <><FR x="8" y="8" width="8" height="8" rx="1.5" /><path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15" /><rect x="8" y="8" width="8" height="8" rx="1.5" /></>,
  info: <><FC cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="9" /><path d="M12 11v5.5" /><circle cx="12" cy="7.8" r=".6" fill="currentColor" /></>,
  save: <><F d="M5 4h11l3 3v11.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 18.5z" /><path d="M5 4h11l3 3v11.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 18.5zM8.5 4v4.5h6V4M8 20v-5.5h8V20" /></>,
  exit: <><F d="M14 4H6.5A1.5 1.5 0 0 0 5 5.5v13A1.5 1.5 0 0 0 6.5 20H14z" /><path d="M14 4H6.5A1.5 1.5 0 0 0 5 5.5v13A1.5 1.5 0 0 0 6.5 20H14M11 12h9.5M17 8.5l3.5 3.5-3.5 3.5" /></>,
  open: <><F d="M5 6.5A1.5 1.5 0 0 1 6.5 5H11v14H6.5A1.5 1.5 0 0 1 5 17.5z" /><path d="M18.5 13.5v4a1.5 1.5 0 0 1-1.5 1.5H6.5A1.5 1.5 0 0 1 5 17.5V7A1.5 1.5 0 0 1 6.5 5.5h4M14 4h6v6M20 4l-8.5 8.5" /></>,
  lock: <><FR x="5" y="10.5" width="14" height="10" rx="2.5" /><rect x="5" y="10.5" width="14" height="10" rx="2.5" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5M12 14.5v2" /></>,
  unlock: <><FR x="5" y="10.5" width="14" height="10" rx="2.5" /><rect x="5" y="10.5" width="14" height="10" rx="2.5" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 6.7-1.4M12 14.5v2" /></>,
  play: <><F d="M7.5 5.2v13.6a1 1 0 0 0 1.5.9l11-6.8a1 1 0 0 0 0-1.8L9 4.3a1 1 0 0 0-1.5.9z" /><path d="M7.5 5.2v13.6a1 1 0 0 0 1.5.9l11-6.8a1 1 0 0 0 0-1.8L9 4.3a1 1 0 0 0-1.5.9z" /></>,
  stop: <><FR x="5.5" y="5.5" width="13" height="13" rx="3" /><rect x="5.5" y="5.5" width="13" height="13" rx="3" /></>,
  pause: <><FR x="6" y="5" width="4" height="14" rx="1.3" /><FR x="14" y="5" width="4" height="14" rx="1.3" /><rect x="6" y="5" width="4" height="14" rx="1.3" /><rect x="14" y="5" width="4" height="14" rx="1.3" /></>,
  wipe: <><F d="M3.8 15.2 13 6l6 6-6.8 6.8H7.4z" /><path d="M3.8 15.2 13 6l6 6-6.8 6.8H7.4zM8.6 10.4l6 6M12.5 20h7.5" /></>,
  console: <><FR x="3" y="4.5" width="18" height="15" rx="2.5" /><rect x="3" y="4.5" width="18" height="15" rx="2.5" /><path d="M7 10l3 2.5L7 15M12.5 15.5h4.5" /></>,
  trash: <><F d="M6.5 7.5h11l-.9 11.2a1.5 1.5 0 0 1-1.5 1.3H8.9a1.5 1.5 0 0 1-1.5-1.3z" /><path d="M4.5 7.5h15M9.5 7.5V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2v2.3M6.5 7.5l.9 11.2a1.5 1.5 0 0 0 1.5 1.3h6.2a1.5 1.5 0 0 0 1.5-1.3l.9-11.2M10.2 11v5.5M13.8 11v5.5" /></>,
  edit: <><F d="M14.5 5.5 18.5 9.5 9 19H5v-4z" /><path d="M14.5 5.5 18.5 9.5 9 19H5v-4zM12.5 7.5l4 4M13 19.5h6" /></>,
  copy: <><FR x="8.5" y="8.5" width="11.5" height="11.5" rx="2.2" /><rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.2" /><path d="M15.5 8.5V6A2 2 0 0 0 13.5 4H6A2 2 0 0 0 4 6v7.5a2 2 0 0 0 2 2h2.5" /></>,
  select: <><F d="M11 11l8.5 3-3.6 1.6L14.3 19z" /><path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M12 4h1M17 4h1.5A1.5 1.5 0 0 1 20 5.5V7M4 12v1M4 17v1.5A1.5 1.5 0 0 0 5.5 20H7M11 11l8.5 3-3.6 1.6L14.3 19z" /></>,
  code: <path d="M8.5 7.5 4 12l4.5 4.5M15.5 7.5 20 12l-4.5 4.5M13.2 5.5l-2.4 13" />,
  download: <><F d="M4 14.5h16v3a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5z" /><path d="M12 4v10M7.5 9.5 12 14l4.5-4.5M4 14.5v3A2.5 2.5 0 0 0 6.5 20h11a2.5 2.5 0 0 0 2.5-2.5v-3" /></>,
  upload: <><F d="M4 14.5h16v3a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5z" /><path d="M12 14V4M7.5 8.5 12 4l4.5 4.5M4 14.5v3A2.5 2.5 0 0 0 6.5 20h11a2.5 2.5 0 0 0 2.5-2.5v-3" /></>,
  bolt: <><F d="M13.5 3 5.5 13.5h5.5L10 21l8.5-11h-5.5z" /><path d="M13.5 3 5.5 13.5h5.5L10 21l8.5-11h-5.5z" /></>,
  deploy: <><F d="M12 3c3.3 1.9 4.8 5.3 4.8 8.8L15 15.5H9l-1.8-3.7C7.2 8.3 8.7 4.9 12 3z" /><path d="M12 3c3.3 1.9 4.8 5.3 4.8 8.8L15 15.5H9l-1.8-3.7C7.2 8.3 8.7 4.9 12 3zM9 15.5l-2.5 2 .5-4.2M15 15.5l2.5 2-.5-4.2M10.5 18.5 12 21l1.5-2.5" /><circle cx="12" cy="9.5" r="1.6" /></>,
  power: <><FC cx="12" cy="13" r="8" /><path d="M12 3.5v8M7.3 6.8a7.2 7.2 0 1 0 9.4 0" /></>,
  destroy: <><FC cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></>,
  config: <><FC cx="9" cy="7" r="2.3" /><FC cx="15" cy="12" r="2.3" /><FC cx="8" cy="17" r="2.3" /><path d="M4 7h2.7M11.3 7H20M4 12h8.7M17.3 12H20M4 17h1.7M10.3 17H20" /><circle cx="9" cy="7" r="2.3" /><circle cx="15" cy="12" r="2.3" /><circle cx="8" cy="17" r="2.3" /></>,
  status: <><FR x="5" y="12" width="3.5" height="7" rx="1" /><FR x="10.25" y="7" width="3.5" height="12" rx="1" /><FR x="15.5" y="4" width="3.5" height="15" rx="1" /><path d="M3.5 20.5h17" /><rect x="5" y="12" width="3.5" height="7" rx="1" /><rect x="10.25" y="7" width="3.5" height="12" rx="1" /><rect x="15.5" y="4" width="3.5" height="15" rx="1" /></>,
  capture: <><FC cx="10.5" cy="10.5" r="6.8" /><circle cx="10.5" cy="10.5" r="6.8" /><path d="M15.4 15.4 20.5 20.5M6.3 10.6h1.6l1.2-2.4 1.9 4.8 1.2-2.4h1.6" /></>,
  folder: <><F d={FOLDER} /><path d={FOLDER} /></>,
  folderPlus: <><F d={FOLDER} /><path d={FOLDER} /><path d="M12 10.5v6M9 13.5h6" /></>,
  move: <><F d={FOLDER} /><path d={FOLDER} /><path d="M8.5 13.5h7M13 11l2.5 2.5L13 16" /></>,
  home: <><F d="M5 10.2 12 4.5l7 5.7V19a1 1 0 0 1-1 1h-3.5v-5h-5v5H6a1 1 0 0 1-1-1z" /><path d="M3.5 11.5 12 4.5l8.5 7M5 10.2V19a1 1 0 0 0 1 1h3.5v-5h5v5H18a1 1 0 0 0 1-1v-8.8" /></>,
  user: <><FC cx="12" cy="8" r="4" /><F d="M4.5 20.5a7.5 7.5 0 0 1 15 0z" /><circle cx="12" cy="8" r="4" /><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0" /></>,
  key: <><FC cx="8" cy="15.5" r="4.2" /><circle cx="8" cy="15.5" r="4.2" /><path d="M11 12.5 20 3.5M16.5 7l2.5 2.5M14.5 9l2 2" /></>,
  sun: <><FC cx="12" cy="12" r="4.2" /><circle cx="12" cy="12" r="4.2" /><path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M5.5 18.5l1.4-1.4M17.1 6.9l1.4-1.4" /></>,
  moon: <><F d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z" /><path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z" /></>,
  search: <><FC cx="11" cy="11" r="6.8" /><circle cx="11" cy="11" r="6.8" /><path d="M16 16l4.5 4.5" /></>,
  palette: <><F d="M12 3.5a8.5 8.5 0 0 0 0 17c1.4 0 2-1 2-2.1 0-1.2 1-2 2.2-2H18a3 3 0 0 0 3-3c0-4.7-4-6.9-9-6.9z" /><path d="M12 3.5a8.5 8.5 0 0 0 0 17c1.4 0 2-1 2-2.1 0-1.2 1-2 2.2-2H18a3 3 0 0 0 3-3c0-4.7-4-6.9-9-6.9z" /><circle cx="7.6" cy="11.4" r=".9" fill="currentColor" stroke="none" /><circle cx="9.8" cy="7.8" r=".9" fill="currentColor" stroke="none" /><circle cx="14.2" cy="7.8" r=".9" fill="currentColor" stroke="none" /></>,
  check: <path d="M4.5 12.5l4.5 4.5 10.5-11" />,
  hand: <path d="M8.5 11V5.4a1.3 1.3 0 0 1 2.6 0V10m0-.6V4.2a1.3 1.3 0 0 1 2.6 0V10m0-.4V5.1a1.3 1.3 0 0 1 2.6 0V11m0-2.4a1.3 1.3 0 0 1 2.6 0V13a6 6 0 0 1-6 6h-1a6 6 0 0 1-4.3-1.8l-3-3a1.4 1.4 0 0 1 2-2l1.3 1.3" />,
  expand: <path d="M4 9V5a1 1 0 0 1 1-1h4M20 9V5a1 1 0 0 0-1-1h-4M4 15v4a1 1 0 0 0 1 1h4M20 15v4a1 1 0 0 1-1 1h-4" />,
  // nerd: face with big round glasses and a little smile
  nerd: <><FC cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="9" />
    <circle cx="8.6" cy="10.8" r="2.6" /><circle cx="15.4" cy="10.8" r="2.6" />
    <path d="M11.2 10.6h1.6M6 10.1 4.2 9.2M18 10.1l1.8-.9" />
    <circle cx="8.6" cy="10.8" r=".8" fill="currentColor" stroke="none" /><circle cx="15.4" cy="10.8" r=".8" fill="currentColor" stroke="none" />
    <path d="M9 15.5c1.1 1.3 4.9 1.3 6 0" /></>,
}

export function Ui({ name, size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      {ui[name] || ui.more}
    </svg>
  )
}
