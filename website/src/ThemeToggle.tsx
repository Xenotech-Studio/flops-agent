import { useEffect, useRef, useState } from 'react'

const STORAGE_KEY = 'flops-agent.colorTheme'
const modes = ['system', 'light', 'dark'] as const
type Mode = typeof modes[number]
const labels = { system: '自动（跟随系统）', light: '亮色', dark: '暗色' }
function normalize(value: string | null): Mode {
  return value === 'light' || value === 'dark' ? value : 'system'
}
function read(): Mode {
  try { return normalize(localStorage.getItem(STORAGE_KEY)) } catch { return 'system' }
}
function apply(mode: Mode) {
  const theme = mode === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : mode
  document.documentElement.dataset.theme = theme
  document.documentElement.dataset.themeMode = mode
  document.documentElement.style.colorScheme = theme
  document.documentElement.style.backgroundColor = theme === 'dark' ? '#141414' : '#ffffff'
}
export default function ThemeToggle() {
  const [mode, setMode] = useState<Mode>(read)
  const current = useRef(mode)
  function change(next: Mode, persist = true) {
    current.current = next
    apply(next) // Paint the effective theme before asking React to update the icon.
    if (persist) { try { localStorage.setItem(STORAGE_KEY, next) } catch { /* Session-only when storage is blocked. */ } }
    setMode(next)
  }
  function cycle() { change(modes[(modes.indexOf(current.current) + 1) % modes.length]) }
  useEffect(() => {
    apply(current.current)
    const system = matchMedia('(prefers-color-scheme: dark)')
    const follow = () => { if (current.current === 'system') apply('system') }
    const storage = (event: StorageEvent) => { if (event.key === STORAGE_KEY || event.key === null) change(normalize(event.newValue), false) }
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === 'F1') { event.preventDefault(); if (!event.repeat) cycle() }
    }
    system.addEventListener('change', follow)
    window.addEventListener('storage', storage)
    window.addEventListener('keydown', key)
    return () => { system.removeEventListener('change', follow); window.removeEventListener('storage', storage); window.removeEventListener('keydown', key) }
  }, [])
  const description = `主题：${labels[mode]}；点击切换为${labels[modes[(modes.indexOf(mode) + 1) % modes.length]]}`
  return <button type="button" className="theme-toggle" onClick={cycle} aria-label={description} title={description} aria-keyshortcuts="Control+F1 Meta+F1" data-mode={mode}>
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {mode === 'light' ? <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></> : mode === 'dark' ? <path d="M20 15.5A8.5 8.5 0 0 1 8.5 4a8.5 8.5 0 1 0 11.5 11.5Z" /> : <><circle cx="12" cy="12" r="8" fill="var(--code-panel-fg)" /><path d="M12 4a8 8 0 0 1 0 16Z" fill="var(--code-panel-bar)" stroke="none" /><path d="m15 3-6 18" strokeWidth="3" className="theme-icon-cut" /><path d="m15 3-6 18" /></>}
    </svg>
  </button>
}
