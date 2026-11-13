// Tiny custom i18n, remembered per browser (same shape as theme.js).
// Two dictionaries (en / it) built by merging the per-area partials in ./locales.
// Default language is English; Italian keeps the original wording.
//
// Usage in a component:
//   import { t, useLang } from './i18n.js'
//   function Thing() { useLang(); return <button>{t('common.save')}</button> }
// Call useLang() once in a component that renders translated text so it
// re-renders when the language changes; t(key, vars?) does the lookup.
import { useSyncExternalStore } from 'react'
import * as common from './locales/common.js'
import * as login from './locales/login.js'

export const LANGS = [
  { id: 'en', label: 'English' },
  { id: 'it', label: 'Italiano' },
]
const IDS = LANGS.map((l) => l.id)
const KEY = 'netso.lang'

const DICT = {
  en: { ...common.en, ...login.en },
  it: { ...common.it, ...login.it },
}

let lang = (() => {
  try {
    const saved = localStorage.getItem(KEY)
    if (IDS.includes(saved)) return saved
  } catch { /* ignore */ }
  return 'en'
})()
const subs = new Set()

function apply() { document.documentElement.lang = lang }
apply()

export function setLang(v) {
  if (!IDS.includes(v)) return
  lang = v
  try { localStorage.setItem(KEY, v) } catch { /* ignore */ }
  apply()
  subs.forEach((f) => f())
}

export const getLang = () => lang
export const useLang = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f) }, () => lang)

// Look up a key for the current language; fall back to English, then the key
// itself. Pass vars to fill {name} placeholders, e.g. t('x', { n: 3 }).
export function t(key, vars) {
  let s = DICT[lang]?.[key] ?? DICT.en[key] ?? key
  if (vars) for (const k in vars) s = s.split(`{${k}}`).join(String(vars[k]))
  return s
}
