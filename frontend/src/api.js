// Minimal API client. The login session token is kept in localStorage.
const KEY = 'netso.token'

export const getToken = () => {
  try { return localStorage.getItem(KEY) || '' } catch { return '' }
}
export const setToken = (t) => {
  try { localStorage.setItem(KEY, t) } catch { /* ignore */ }
}

export class AuthError extends Error {}

export async function api(path, { method = 'GET', body, text = false, noAuth = false } = {}) {
  const headers = {}
  const token = getToken()
  if (token && !noAuth) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`/api${path}`, {
    method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && !noAuth) throw new AuthError('Accesso richiesto')
  if (!res.ok) {
    let msg = res.statusText
    try { msg = (await res.json()).detail || msg } catch { /* not json */ }
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return text ? res.text() : res.json()
}

// websocket URL for an /api path, carrying the token as a query parameter
export function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const t = getToken()
  return `${proto}://${location.host}/api${path}${t ? `${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(t)}` : ''}`
}

// plain URL for an /api path (downloads, streams), carrying the token as a query parameter
export function apiUrl(path, params = {}) {
  const q = new URLSearchParams({ ...params, ...(getToken() ? { token: getToken() } : {}) }).toString()
  return `${location.origin}/api${path}${q ? `?${q}` : ''}`
}

// streaming PUT upload of a raw file (images, custom icons…) with progress and the auth token
export function uploadTo(url, file, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest()
    x.open('PUT', url)
    const t = getToken()
    if (t) x.setRequestHeader('Authorization', `Bearer ${t}`)
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
    x.onload = () => {
      let body = {}
      try { body = JSON.parse(x.responseText) } catch { /* not json */ }
      x.status < 300 ? resolve(body) : reject(new Error(body.detail || x.statusText))
    }
    x.onerror = () => reject(new Error('Caricamento interrotto'))
    x.send(file)
  })
}

export function consoleUrl(lab, node) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const t = getToken()
  return `${proto}://${location.host}/api/labs/${encodeURIComponent(lab)}/nodes/${encodeURIComponent(node)}/console${t ? `?token=${encodeURIComponent(t)}` : ''}`
}

// QEMU network cards (qemu-system-x86_64 -device help), as in the EVE-NG/PNETLab node editor
export const NIC_MODELS = ['e1000', 'e1000-82540em', 'e1000-82544gc', 'e1000-82545em', 'e1000e', 'i82550', 'i82551', 'i82557a', 'i82557b',
  'i82557c', 'i82558a', 'i82558b', 'i82559a', 'i82559b', 'i82559c', 'i82559er', 'i82562', 'i82801', 'ne2k_pci', 'pcnet', 'rtl8139',
  'tulip', 'virtio-net-pci', 'vmxnet3']
// VM console types ("primary console")
export const CONSOLES = [['telnet', 'TELNET'], ['rdp', 'RDP'], ['rdp-tls', 'RDP-TLS'], ['vnc', 'VNC'], ['winbox', 'WinBox'], ['ssh', 'SSH'],
  ['http', 'HTTP'], ['https', 'HTTPS'], ['spice', 'SPICE']]
export const consoleLabel = (c) => (CONSOLES.find((x) => x[0] === c) || [c, (c || '').toUpperCase()])[1]

// display name of a device (PNETLab style: "Cisco vIOS Router"); old imports kept it in the description
export const tplTitle = (t) => (!t ? '' : t.title || (t.source === 'EVE-NG/PNETLab' && t.description && !t.description.startsWith('Importato') ? t.description.replace(/ \(importato da EVE-NG\/PNETLab\)$/, '') : t.name))
export const byTitle = (a, b) => tplTitle(a).localeCompare(tplTitle(b), 'it', { sensitivity: 'base' })

// Interface names for a node, same rules as the backend
export function interfaceNames(tpl, node) {
  if (!tpl) return []
  const n = node?.nics ?? tpl.nics
  const out = []
  if (['bridge', 'external', 'nat'].includes(tpl.kind)) for (let i = 1; i <= n; i++) out.push(`port${i}`)
  else if (tpl.kind === 'iol') for (let i = 0; i < n; i++) out.push(`e${Math.floor(i / 4)}/${i % 4}`) // Ethernet<bay>/<unit>
  else if (tpl.interfaces?.length) { // explicit names, then continue the numbering of the last one
    out.push(...tpl.interfaces.slice(0, n))
    const last = tpl.interfaces[tpl.interfaces.length - 1], m = last.match(/^(.*?)(\d+)$/)
    for (let k = 1; out.length < n; k++) out.push(m ? `${m[1]}${Number(m[2]) + k}` : `${last}-${k}`)
  } else if (tpl.kind === 'docker' && tpl.iface_prefix === 'eth' && !tpl.iface_start) for (let i = 1; i <= n; i++) out.push(`eth${i}`)
  else for (let i = 0; i < n; i++) out.push(`${tpl.iface_prefix}${tpl.iface_start + i}`)
  return out
}

export const splitEp = (ep) => {
  const i = ep.indexOf(':')
  return [ep.slice(0, i), ep.slice(i + 1)]
}
