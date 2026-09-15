type Feature = {
  title: string
  description: string
}

const features: Feature[] = [
  {
    title: 'Typed contracts',
    description: 'Typed public APIs and strict type checking keep integration boundaries clear before runtime.',
  },
  {
    title: 'Streaming by design',
    description: 'Subscribe to structured events and connect them to SSE with cursors and reconnection in mind.',
  },
  {
    title: 'Control a live run',
    description: 'Start work immediately, then cancel, suspend, resume, and accept input while it is in flight.',
  },
  {
    title: 'Persistence and recovery',
    description: 'Sessions and runs have seams for durable storage, restart recovery, and long-lived work.',
  },
  {
    title: 'Provider-agnostic',
    description: 'Use any OpenAI-compatible streaming provider, or replace the LLM client seam with your own adapter.',
  },
  {
    title: 'Learn progressively',
    description: 'An eight-part guide starts with a runnable agent and layers in service concerns one at a time.',
  },
]

const codeExample = `# pip install flops_agent
import asyncio

from flops_agent import Runtime

runtime = Runtime(llm=my_llm)

async def main():
    async for event in runtime.ask("Introduce yourself in one sentence."):
        text = getattr(event, "text", None)
        if text:
            print(text, end="", flush=True)

asyncio.run(main())`

function ExternalArrow() {
  return <span aria-hidden="true">↗</span>
}

function App() {
  return (
    <>
      <a className="skip-link" href="#content">Skip to content</a>

      <header className="site-header">
        <a className="brand" href="#top" aria-label="flops_agent home">
          <span className="brand-mark" aria-hidden="true">ƒ</span>
          <span>flops_agent</span>
        </a>
        <nav aria-label="Primary navigation">
          <a href="#features">Features</a>
          <a href="#quick-start">Quick start</a>
          <a href="#docs">Docs</a>
        </nav>
      </header>

      <main id="content">
        <section className="hero" id="top" aria-labelledby="hero-title">
          <p className="eyebrow"><span aria-hidden="true" />Python 3.10+ · MIT licensed</p>
          <h1 id="hero-title">The runtime for agents that have to keep running.</h1>
          <p className="hero-copy">
            flops_agent is a service-type agent runtime that brings execution lifecycle,
            streaming, cancellation, suspension, persistence, and restart recovery into one
            reusable foundation.
          </p>
          <div className="install"><code>pip install flops_agent</code></div>
          <div className="hero-actions">
            <a className="button button-primary" href="https://github.com/Xenotech-Studio/flops-agent">
              View on GitHub <ExternalArrow />
            </a>
            <a className="button button-secondary" href="https://pypi.org/project/flops-agent/">
              Get it from PyPI <ExternalArrow />
            </a>
          </div>
          <p className="microcopy">Bring your model, storage, tools, and policy. Keep the runtime mechanics reusable.</p>
        </section>

        <section className="features section" id="features" aria-labelledby="features-title">
          <div className="section-heading">
            <p className="eyebrow">Built for service-shaped agents</p>
            <h2 id="features-title">Own the product layer. Reuse the hard parts.</h2>
            <p>
              The framework keeps lifecycle mechanics explicit, so an embedding product can
              focus on its own model, data, tools, authentication, and policy.
            </p>
          </div>
          <div className="feature-grid">
            {features.map((feature, index) => (
              <article className="feature-card" key={feature.title}>
                <span className="feature-number">{String(index + 1).padStart(2, '0')}</span>
                <h3>{feature.title}</h3>
                <p>{feature.description}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="quick-start section" id="quick-start" aria-labelledby="quick-start-title">
          <div className="section-heading">
            <p className="eyebrow">A small starting point</p>
            <h2 id="quick-start-title">Start a turn. Stream what happens.</h2>
            <p>Use the bundled OpenAI-compatible client, or pass an LLM implementation at the same seam.</p>
          </div>
          <div className="code-window">
            <div className="window-bar" aria-hidden="true"><span /><span /><span /><code>hello.py</code></div>
            <pre><code>{codeExample}</code></pre>
          </div>
          <p className="code-note">
            <code>ask()</code> is the convenient one-shot entry point. For browser or API services,
            use <code>start()</code> and subscribe to the returned run.
          </p>
        </section>

        <section className="docs-callout section" id="docs" aria-labelledby="docs-title">
          <div>
            <p className="eyebrow">Documentation</p>
            <h2 id="docs-title">A course for building durable agent services.</h2>
            <p>
              Follow the eight chapters from a deterministic first run through streaming,
              sessions, cancellation, recovery, and extension points.
            </p>
          </div>
          <a className="button button-primary" href="https://github.com/Xenotech-Studio/flops-agent/tree/master/docs">
            Read the docs <span aria-hidden="true">→</span>
          </a>
        </section>
      </main>

      <footer className="site-footer">
        <span>flops_agent</span>
        <span>MIT License</span>
        <a href="https://github.com/Xenotech-Studio/flops-agent">GitHub</a>
      </footer>
    </>
  )
}

export default App
