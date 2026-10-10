import { useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import App from './App'
import Docs from './Docs'
import PublicHeader from './PublicHeader'

export default function Router() {
  const [path, setPath] = useState(location.pathname)
  const [headerPath, setHeaderPath] = useState(location.pathname)
  const content = useRef<HTMLDivElement>(null)
  const displayed = useRef(path)
  useEffect(() => {
    let generation = 0
    let animation: Animation | undefined
    const previousRestoration = history.scrollRestoration
    history.scrollRestoration = 'manual'
    const land = (hash: string) => {
      if (hash) {
        try { document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView({ behavior: 'instant' }) } catch { /* Invalid fragment. */ }
      } else window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
      document.querySelector<HTMLElement>('main')?.focus({ preventScroll: true })
    }
    const update = async () => {
      const ticket = ++generation
      animation?.cancel()
      const target = location.pathname
      const hash = location.hash
      const node = content.current!
      const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
      const style = getComputedStyle(node)
      const time = style.getPropertyValue('--route-duration').trim()
      // Production CSS may normalize 180ms to .18s. WAAPI always takes ms.
      const duration = Number.parseFloat(time) * (time.endsWith('ms') ? 1 : 1000) / 2
      const easing = style.getPropertyValue('--route-easing').trim()
      const started = performance.now()
      const changed = target !== displayed.current
      flushSync(() => setHeaderPath(target))
      if (changed && !reduced) {
        animation = node.animate([{ opacity: 1 }, { opacity: 0 }], { duration, easing, fill: 'forwards' })
        try { await animation.finished } catch { return }
      }
      if (ticket !== generation) return
      flushSync(() => { displayed.current = target; setPath(target) })
      land(hash)
      animation?.cancel()
      if (changed && !reduced) {
        animation = node.animate([{ opacity: 0 }, { opacity: 1 }], { duration: Math.max(0, duration * 2 - (performance.now() - started)), easing })
        try { await animation.finished } catch { /* A newer navigation takes over. */ }
      }
    }
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      const anchor = (event.target as Element).closest('a')
      if (!anchor || anchor.target || anchor.hasAttribute('download')) return
      const url = new URL(anchor.href)
      if (url.origin !== location.origin || !/^\/(?:docs(?:\/[^/]+)?\/?)?$/.test(url.pathname)) return
      if (url.pathname === location.pathname && url.hash) return
      event.preventDefault()
      history.pushState(null, '', url)
      void update()
    }
    const onPopState = () => { void update() }
    const media = matchMedia('(prefers-reduced-motion: reduce)')
    const onMotionChange = () => { if (media.matches) void update() }
    media.addEventListener('change', onMotionChange)
    window.addEventListener('popstate', onPopState)
    document.addEventListener('click', click)
    return () => {
      ++generation; animation?.cancel()
      history.scrollRestoration = previousRestoration
      media.removeEventListener('change', onMotionChange)
      window.removeEventListener('popstate', onPopState)
      document.removeEventListener('click', click)
    }
  }, [])
  useEffect(() => {
    if (path === '/') document.title = 'flops-agent — 开箱即用的云端 agent 框架'
  }, [path])
  return <>
    <PublicHeader docs={headerPath !== '/'} />
    <div className="route-content" ref={content}>
      {path === '/' ? <App /> : <Docs key={path} path={path} />}
    </div>
  </>
}
