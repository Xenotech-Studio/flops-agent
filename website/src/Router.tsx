import { useEffect, useState } from 'react'
import App from './App'
import Docs from './Docs'
import PublicHeader from './PublicHeader'

export default function Router() {
  const [path, setPath] = useState(location.pathname)
  useEffect(() => {
    const update = () => { setPath(location.pathname); if (!location.hash) window.scrollTo(0, 0) }
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      const anchor = (event.target as Element).closest('a')
      if (!anchor || anchor.target || anchor.hasAttribute('download')) return
      const url = new URL(anchor.href)
      if (url.origin !== location.origin || !/^\/(?:docs(?:\/[^/]+)?\/?)?$/.test(url.pathname)) return
      if (url.pathname === location.pathname && url.hash) return
      event.preventDefault()
      history.pushState(null, '', url)
      update()
      requestAnimationFrame(() => {
        if (url.hash) document.getElementById(decodeURIComponent(url.hash.slice(1)))?.scrollIntoView()
        else document.querySelector<HTMLElement>('main')?.focus({ preventScroll: true })
      })
    }
    window.addEventListener('popstate', update)
    document.addEventListener('click', click)
    return () => { window.removeEventListener('popstate', update); document.removeEventListener('click', click) }
  }, [])
  useEffect(() => {
    if (path === '/') document.title = 'flops-agent — 开箱即用的云端 agent 框架'
  }, [path])
  return <>
    <PublicHeader docs={path !== '/'} />
    {path === '/' ? <App /> : <Docs key={path} path={path} />}
  </>
}
