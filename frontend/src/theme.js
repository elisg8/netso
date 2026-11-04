// Colour themes, remembered per browser; the first time it follows the system light/dark setting.
import { useSyncExternalStore } from 'react'

// each theme has a full set of CSS variables in styles.css (:root[data-theme="<id>"])
export const THEMES = [
  { id: 'dark', label: 'Scuro', bg: '#0a0c14', accent: '#8b7bff', light: false },
  { id: 'light', label: 'Chiaro', bg: '#f3f5fa', accent: '#6d5cf5', light: true },
  { id: 'oceano', label: 'Oceano', bg: '#091423', accent: '#38bdf8', light: false },
  { id: 'foresta', label: 'Foresta', bg: '#0a140e', accent: '#34d399', light: false },
  { id: 'ambra', label: 'Ambra', bg: '#15100a', accent: '#f59e0b', light: false },
  { id: 'carta', label: 'Carta', bg: '#f4efe4', accent: '#0f766e', light: true },
  { id: 'dracula', label: 'Dracula', bg: '#282a36', accent: '#bd93f9', light: false },
]
const IDS = THEMES.map((t) => t.id)

const KEY = 'netso.theme'
let theme = (() => {
  try {
    const saved = localStorage.getItem(KEY)
    if (IDS.includes(saved)) return saved
  } catch { /* ignore */ }
  return matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
})()
const subs = new Set()

export const isLight = (id = theme) => THEMES.find((t) => t.id === id)?.light ?? false

function apply() {
  document.documentElement.dataset.theme = theme
  const t = THEMES.find((x) => x.id === theme) || THEMES[0]
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t.bg)
}
apply()

export function setTheme(v) {
  if (!IDS.includes(v)) return
  theme = v
  try { localStorage.setItem(KEY, v) } catch { /* ignore */ }
  apply()
  subs.forEach((f) => f())
}
// kept for the old keyboard/toggle paths: flip between light and dark
export const toggleTheme = () => setTheme(isLight() ? 'dark' : 'light')
export const useTheme = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f) }, () => theme)
