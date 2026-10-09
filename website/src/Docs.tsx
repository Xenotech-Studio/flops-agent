import { useEffect, useRef, useState } from 'react'
import data from './content/docs/_meta.json'
import './Docs.css'
const pages = data.pages
const groups = [...new Set(pages.map(p => p.group))]

function Search({ close }: { close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const tokens = query.toLowerCase().trim().split(/\s+/).filter(Boolean)
  const results = pages.filter(p => tokens.every(t => `${p.title} ${p.group} ${p.markdown}`.toLowerCase().includes(t)))
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    dialog.current?.showModal()
    return () => { dialog.current?.close(); opener?.focus() }
  }, [])
  useEffect(() => { document.getElementById(`search-result-${selected}`)?.scrollIntoView({ block: 'nearest' }) }, [selected])
  return <dialog ref={dialog} className="docs-search" aria-label="搜索文档" onCancel={close} onClick={e => { if (e.target === e.currentTarget) close() }}>
    <div className="docs-search-row"><input autoFocus aria-label="搜索文档" placeholder="搜索标题、章节或正文…" value={query} onChange={e => { setQuery(e.target.value); setSelected(0) }} onKeyDown={e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); if (results.length) setSelected((selected + (e.key === 'ArrowDown' ? 1 : -1) + results.length) % results.length) }
      if (e.key === 'Enter' && !e.nativeEvent.isComposing) document.getElementById(`search-result-${selected}`)?.click()
    }} /><button onClick={close} aria-label="关闭搜索">关闭</button></div>
    <div className="docs-results">{results.map((p, index) => <a id={`search-result-${index}`} key={p.slug} href={p.href} className={selected === index ? 'selected' : ''} onClick={close}><small>{p.group}</small><strong>{p.title}</strong><span>{p.summary}</span></a>)}{!results.length && <p role="status">没有找到相关文档</p>}</div>
    <p className="docs-search-help">↑↓ 选择 · Enter 打开 · Esc 关闭</p>
  </dialog>
}

export default function Docs({ path }: { path: string }) {
  const normalized = path.replace(/\/$/, '')
  const page = pages.find(p => p.href === normalized || (p.slug === 'overview' && normalized === '/docs/overview'))
  const [menu, setMenu] = useState(false)
  const [search, setSearch] = useState(false)
  const [copy, setCopy] = useState('复制本页 Markdown')
  const [active, setActive] = useState('')
  useEffect(() => {
    document.title = `${page?.title ?? '文档未找到'} · flops-agent`
    const key = (event: KeyboardEvent) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearch(s => !s) } }
    window.addEventListener('keydown', key)
    if (location.hash) { try { document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView() } catch { /* Malformed URL fragment. */ } }
    const update = () => {
      let current = page?.headings[0]?.id ?? ''
      for (const heading of page?.headings ?? []) if ((document.getElementById(heading.id)?.getBoundingClientRect().top ?? Infinity) < 140) current = heading.id
      if (window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2) current = page?.headings.at(-1)?.id ?? current
      setActive(current)
    }
    update(); window.addEventListener('scroll', update, { passive: true })
    return () => { window.removeEventListener('keydown', key); window.removeEventListener('scroll', update) }
  }, [page])
  async function copyMarkdown() {
    try { await navigator.clipboard.writeText(page!.publicMarkdown); setCopy('已复制') }
    catch { setCopy('复制失败，请打开 Markdown 原文') }
  }
  const index = page ? pages.indexOf(page) : -1
  return <div className="docs-page">
    <a className="skip-link" href="#docs-content">跳到正文</a>
    <header className="site-header docs-header"><a className="brand" href="/"><img className="brand-mark" src="/favicon-64.png?v=9339b14ffac0" width="31" height="31" alt="" />flops-agent</a><nav className="header-nav" aria-label="主导航"><a href="/#quick-start">快速开始</a><a href="/docs" aria-current="page">文档</a></nav><button className="docs-search-button" onClick={() => setSearch(true)}>搜索文档 <kbd>⌘ K / Ctrl K</kbd></button></header>
    <div className="docs-layout">
      <aside className="docs-sidebar"><button className="docs-menu-toggle" aria-expanded={menu} aria-controls="docs-navigation" onClick={() => setMenu(!menu)}>文档目录 <span>{menu ? '−' : '+'}</span></button><nav id="docs-navigation" className={menu ? 'open' : ''} aria-label="文档目录">{groups.map(group => <div className="docs-group" key={group}><p>{group}</p>{pages.filter(p => p.group === group).map(p => <a key={p.slug} href={p.href} aria-current={p === page ? 'page' : undefined}>{p.title}</a>)}</div>)}</nav><a className="docs-repo" href="https://github.com/Xenotech-Studio/flops-agent">GitHub ↗</a></aside>
      <main id="docs-content" tabIndex={-1} className="docs-main">{page ? <>
        <div className="docs-breadcrumb"><a href="/docs">文档</a><span>/</span><span>{page.group}</span></div>
        <div className="docs-tools"><button onClick={copyMarkdown}><span aria-live="polite">{copy}</span></button><a href={`/docs-source/${page.slug}.md`}>Markdown 原文 ↗</a></div>
        <article className="docs-prose" lang={page.overview ? 'zh-CN' : 'en'} dangerouslySetInnerHTML={{ __html: page.html }} />
        <nav className="docs-pagination" aria-label="前后篇">{index > 0 ? <a href={pages[index - 1].href}><small>← 上一篇</small>{pages[index - 1].title}</a> : <span />}{index < pages.length - 1 && <a href={pages[index + 1].href}><small>下一篇 →</small>{pages[index + 1].title}</a>}</nav>
        <footer className="docs-footer">flops-agent · MIT <a href="/docs-index.json">文档索引 JSON ↗</a></footer>
      </> : <div className="docs-not-found"><p>404 / DOCUMENTATION</p><h1>文档未找到</h1><p>该地址不在公开文档清单中。</p><a href="/docs">返回文档概览 →</a></div>}</main>
      {page && <aside className="docs-outline"><nav aria-label="本页目录"><p>本页目录</p>{page.headings.map(h => <a key={h.id} href={`#${h.id}`} data-level={h.level} aria-current={active === h.id ? 'location' : undefined}>{h.text}</a>)}</nav></aside>}
    </div>{search && <Search close={() => setSearch(false)} />}
  </div>
}
