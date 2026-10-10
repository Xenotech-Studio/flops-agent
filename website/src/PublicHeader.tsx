import ThemeToggle from './ThemeToggle'
// The header stays mounted across page changes so CSS can interpolate its width.
// This UI event opens the existing Docs search without coupling it to routing.
export const DOCS_SEARCH_EVENT = 'flops-docs-search'

export default function PublicHeader({ docs }: { docs: boolean }) {
  return <>
    <a className="skip-link" href={docs ? '#docs-content' : '#content'}>Skip to content</a>
    <header className={`site-header ${docs ? 'docs-header' : 'home-header'}`} id="top">
      <div className="site-header-inner">
        <a className="brand" href={docs ? '/' : '#top'} aria-label="flops-agent home">
          <img className="brand-mark" src="/favicon-64.png?v=9339b14ffac0" width="31" height="31" alt="" aria-hidden="true" />
          <span>flops-agent</span>
        </a>
        <nav className="header-nav" aria-label="Main navigation">
          <a href={docs ? '/' : '#quick-start'}>{docs ? 'Home' : 'Quick start'}</a>
          <a href="/docs" aria-current={docs ? 'page' : undefined}>Docs</a>
        </nav>
        <div className="header-actions">
        {docs ? <button className="docs-search-button" onClick={() => window.dispatchEvent(new Event(DOCS_SEARCH_EVENT))}>Search docs <kbd>⌘ K / Ctrl K</kbd></button> :
          <div className="public-links" aria-label="Project links">
            <a href="https://github.com/Xenotech-Studio/flops-agent" title="Public GitHub repository · MIT">GitHub <span className="external-mark" aria-hidden="true">↗</span></a>
            <a href="https://pypi.org/project/flops-agent/" title="PyPI · flops-agent 0.2.0">PyPI <span className="external-mark" aria-hidden="true">↗</span></a>
            <span className="link-status">MIT · 0.2.0</span>
          </div>}
          <ThemeToggle />
        </div>
      </div>
    </header>
  </>
}
