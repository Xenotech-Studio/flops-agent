import { useEffect, useRef, useState } from 'react'

const installExample = `# Python ≥ 3.10; use a virtual environment
pip install flops-agent`

const repositoryExample = `git clone https://github.com/Xenotech-Studio/flops-agent.git
cd flops-agent
pip install -e ".[providers]"
python docs/sample_product/server.py`

const codeExample = `import asyncio
from collections.abc import AsyncIterator

from flops_agent import (
    InMemoryDatabase, Query, Run, Runtime, Session,
    StreamChunk, TextDelta, TextStreamChunk,
)


class DemoLLM:
    # Demo model: fixed text only, with no model or network calls.
    async def acompletion(
        self, **kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        async def stream() -> AsyncIterator[StreamChunk]:
            for text in ("Hello, ", "this is an independent background run."):
                await asyncio.sleep(0)
                yield TextStreamChunk(text=text)
        return stream()


# Assemble once at startup; this database stores data only in memory.
runtime = Runtime(llm=DemoLLM(), database=InMemoryDatabase())


async def main() -> None:
    session: Session = await runtime.load_session("demo-session")
    run: Run = runtime.start(session, Query.text("Demonstrate one run."))

    # start() has started work; subscribe() only observes events.
    async for delivery in run.subscribe():
        if isinstance(delivery.event, TextDelta):
            print(delivery.event.text, end="", flush=True)
    print(f"\\nRun status: {run.status.value}")


asyncio.run(main())`


function CopyButton({ text }: { text: string }) {
  const [status, setStatus] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => () => clearTimeout(timer.current), [])

  async function copy() {
    clearTimeout(timer.current)
    try {
      await navigator.clipboard.writeText(text)
      setStatus('copied')
    } catch {
      setStatus('failed')
    }
    timer.current = setTimeout(() => setStatus('idle'), 4000)
  }

  return (
    <span className="copy-control">
      <button className="copy-button" type="button" onClick={copy}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" stroke="currentColor" />
          <path d="M10.5 3.5v-1h-8v8h1" stroke="currentColor" strokeLinejoin="round" />
        </svg>
        Copy code
      </button>
      <span className={`copy-feedback${status === 'failed' ? ' copy-error' : ''}`} role="status">
        {status === 'copied' ? 'Copied' : status === 'failed' ? 'Copy failed; select the text manually' : ''}
      </span>
    </span>
  )
}

function CodeBlock({ title, language, code }: { title: string; language: string; code: string }) {
  return (
    <figure className="code-window">
      <figcaption className="code-bar">
        <span><span className="code-language">{language}</span>{title}</span>
        <CopyButton text={code} />
      </figcaption>
      <pre tabIndex={0} aria-label={`${title} code`}><code>{code}</code></pre>
    </figure>
  )
}

function SectionHeading({ number, label, title }: { number: string; label: string; title: string }) {
  return (
    <div className="section-heading">
      <p className="eyebrow"><span>{number}</span>{label}</p>
      <h2>{title}</h2>
    </div>
  )
}

function App() {
  return (
    <>
      <div className="home-layout">
      <main id="content" className="page-column">
        <section className="hero" aria-labelledby="hero-title">
          <p className="eyebrow hero-eyebrow"><span className="status-dot" aria-hidden="true" />A standalone Python framework<span className="eyebrow-separator">/</span>flops_agent</p>
          <h1 id="hero-title">A ready-to-integrate framework for server-side agents</h1>
          <p className="hero-copy" id="hero-copy">Run it on your own server. Execution and subscriptions, cancellation and recovery, reconnecting clients, and remote tools have defined runtime mechanisms; you connect the model, storage, and executor.</p>
          <ul className="metadata" aria-label="Project metadata">
            <li>MIT open source</li><li>PyPI 0.2.0</li><li>Development Status: <strong>Beta</strong></li><li>Python ≥ 3.10</li>
          </ul>
          <div className="hero-actions">
            <a className="button button-primary" href="#quick-start">Try the five-minute example <span aria-hidden="true">↓</span></a>
            <a className="button button-secondary" href="#non-goals">What it does not do <span aria-hidden="true">↗</span></a>
          </div>
          <div className="hero-principle">
            <p className="hero-story">A clear execution lifecycle for AI services that keep running.</p>
            <span>Tasks keep running</span><span className="principle-divider" aria-hidden="true" /><span>Clients subscribe as needed</span>
            <p>Refreshing or ending a subscription does not stop an existing task. Your product supplies identity, storage, and routing for reconnection across devices.</p>
          </div>
        </section>

        <a className="product-card" href="https://flops.xenotech.studio/intro">
          <strong className="product-card-title">flops-agent is an open-source kernel.</strong>
          <span>For a ready-to-use product, see → <strong>Flops</strong></span>
        </a>

        <nav className="section-nav" aria-label="On this page">
          <a href="#boundaries">Scope</a><a href="#pitfalls">Four challenges</a><a href="#why">Less to build</a><a href="#value">Why this framework</a><a href="#showcase">Showcase</a><a href="#progress">Project status</a>
        </nav>

        <section className="section" id="boundaries">
          <SectionHeading number="01" label="SCOPE" title="What it is, and what it is not" />
          <p className="section-intro">flops-agent is a Python framework you embed in your application. It organizes a task from start to finish; your product supplies accounts, UI, models, and business decisions.</p>
          <div className="table-scroll" tabIndex={0} role="region" aria-label="Framework and product responsibilities; scroll horizontally">
            <table className="boundary-table">
              <caption className="sr-only">Mechanisms provided by the framework and work owned by the product</caption>
              <thead><tr><th scope="col">The framework provides</th><th scope="col">Your product supplies</th></tr></thead>
              <tbody>
                <tr><td><strong>Task execution and control</strong><p>Advance work in the background, record output, and handle stopping, waiting for answers, and recovery. Reuse these execution mechanisms.</p></td><td><strong>Service and business entry points</strong><p>UI, accounts, access permissions, and service routing. Your product decides who can start or observe tasks.</p></td></tr>
                <tr><td><strong>Replaceable integration interfaces</strong><p>Connect your own models, storage, and tool executors while keeping your deployment environment.</p></td><td><strong>The infrastructure that runs them</strong><p>Models, credentials, production storage, and device environments. You select, configure, and maintain them.</p></td></tr>
                <tr><td><strong>Event contracts and cryptographic primitives</strong><p>Defined ways to describe task progress and encrypt or decrypt selected fields, with explicit integration points.</p></td><td><strong>Business and security policy</strong><p>Tool descriptions, safety thresholds, field selection, and key management. You define scope and access boundaries.</p></td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="section" id="pitfalls">
          <SectionHeading number="02" label="FROM LOOP TO SERVICE" title="Four challenges when an agent becomes a service." />
          <p className="section-intro">Once a local loop works, it must handle real use: people leave, devices disconnect, data needs protection, and the tool catalog grows.</p>
          <div className="pitfalls-list">
            <article className="pitfall" aria-labelledby="pitfall-survival">
              <p className="pitfall-label">01 / CONTINUITY</p>
              <h3 id="pitfall-survival">Processes stop. Tasks need a way to resume.</h3>
              <p>A first version is often just a local loop. If all execution state is in memory, unfinished work loses its context when the process stops. Moving to a server also requires separating execution from observation: closing a page should not stop a task that has already started.</p>
              <p>On reconnection, the client supplies a cursor, replays retained log entries, and follows live output. Service restarts require more: persist conversations and execution records, then connect a startup recovery entry point to schedule interrupted work. Recovery can still fail, and tool side effects require idempotency.</p>
              <p className="pitfall-answer">The framework separates execution from subscriptions and provides replay and recovery orchestration. You supply persistent storage and product recovery callbacks; default in-memory storage does not retain tasks across processes.</p>
            </article>
            <article className="pitfall" aria-labelledby="pitfall-hands">
              <p className="pitfall-label">02 / EXECUTION</p>
              <h3 id="pitfall-hands">The server can reason, but the tools may be elsewhere.</h3>
              <p>Reading files, running commands, and using browsers often requires access to a user device. Running an MCP server on each device and adding a tunnel can seem sufficient, but treating this as the cloud connecting inward to each device gets the direction wrong. Devices behind NAT can sleep or lose connectivity; they need to connect outward, register, advertise capabilities, and reconnect.</p>
              <p>A tunnel still leaves three tasks. Connectivity alone does not establish a registered, live member with declared capabilities: identity, heartbeats, and availability need maintenance. The tunnel does not confirm task delivery or receipt of results. With multiple devices online, dispatch must check capabilities and resolve which device should receive the work.</p>
              <p className="pitfall-answer">The framework provides a remote-executor boundary so products can connect devices that initiate outbound connections, then reuse tool dispatch, result acknowledgement, and recovery waiting. Device directories, authorization, and target selection belong to the product; cross-process result recovery also requires persisted dispatch records. In the current Flops product, ordinary new calls to offline devices fail rather than queue or switch devices automatically.</p>
            </article>
            <article className="pitfall" aria-labelledby="pitfall-data">
              <p className="pitfall-label">03 / DATA</p>
              <h3 id="pitfall-data">Conversations and memory pass through your storage.</h3>
              <p>Once tasks read and write real data, decide which fields to encrypt, who can decrypt them, and how long recovery keys should remain available. In the current Flops integration, selected sensitive conversation and agent fields support encrypted storage when encryption is enabled, with local client decryption.</p>
              <p>During normal login, the client holds and unwraps the user master key while the server stores encrypted envelopes; offline recovery is also retained. The server decrypts relevant context while processing tasks. Runtime keys may remain across runs for recovery and background wakeups, and cleanup can fail.</p>
              <p>User messages and references in current chat requests are plaintext JSON fields protected in transit by HTTPS/TLS; runtime keys are separately wrapped with RSA. Outside selected fields, summaries, some logs, and temporary tool results still have plaintext paths. Verify encryption coverage field by field.</p>
              <p className="pitfall-answer">The framework provides composable field-encryption and transport-key-wrapping primitives. Your product chooses protected fields, authentication, and key lifetime; <a href="#privacy-boundaries">the concrete data-path boundaries are documented below</a>.</p>
            </article>
            <article className="pitfall" aria-labelledby="pitfall-capabilities">
              <p className="pitfall-label">04 / CAPABILITIES</p>
              <h3 id="pitfall-capabilities">A growing tool catalog does not belong in every model request.</h3>
              <p>An agent can do only what its tools allow. A few functions are enough initially, but larger products need different tools for different tasks and devices. The model need not carry the entire catalog on every turn.</p>
              <p>Tools are declared and registered in packages, then opened or closed per session. The model receives tools from packages opened by the session or temporarily enabled by the product, filtered by capabilities. Root tools are supplied separately; opening a package does not open every child package. Actual dispatch must still check target devices, authorization, and safety policy.</p>
              <p className="pitfall-answer">The framework manages tool registration and session package controls so you can connect business tools. It does not bundle your business logic; package contents, execution, and product policy remain yours.</p>
            </article>
          </div>
        </section>

        <section className="section" id="why">
          <SectionHeading number="03" label="LESS TO BUILD" title="Work you can reuse" />
          <p className="section-intro">If you are writing your own agent loop, these mechanisms can save implementation work. Your product still connects identity, storage, devices, and business policy.</p>
          <ol className="benefits-list">
            <li><span className="benefit-number" aria-hidden="true">01</span><div><h3>Long tasks survive page refreshes</h3><p>Execution is independent of subscriptions, so work continues when users leave a page or lose their connection.</p><p className="otherwise">Otherwise, you must separate background-task and connection lifetimes yourself and handle execution with no subscribers.</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">02</span><div><h3>Explicit states for stopping, waiting, and continuing</h3><p>Cancellation, suspension, recovery, and reconnection have distinct control paths, so user actions can follow the current task state.</p><p className="otherwise">Otherwise, you must maintain those state machines, stop checks, pending-answer records, restart recovery, and missed-output replay yourself.</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">03</span><div><h3>Follow the same task from another client</h3><p>Conversation records and output logs support reconnection across clients. Connect shared storage, identity, and routing to let another client continue observing.</p><p className="otherwise">Otherwise, you must persist history, locate the same execution, and track each client's read position.</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">04</span><div><h3>Send device-dependent work elsewhere</h3><p>A remote executor runs tool work in another process or device while the main loop stays in your service.</p><p className="otherwise">Otherwise, you must design integration for dispatch, incremental output, stopping, and recovery records. Device selection and authorization still belong to your product.</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">05</span><div><h3>A shared vocabulary for what happened</h3><p>Events and wire formats define task state and transport messages, letting the UI distinguish text, tool results, waiting, and completion. Version compatibility is not guaranteed during Beta.</p><p className="otherwise">Otherwise, you must define message types, ordering, and replay positions and keep server and client implementations aligned.</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">06</span><div><h3>Explicit encryption boundaries for sensitive fields</h3><p>Cryptographic primitives supply basic operations, and field encryption lets the product explicitly choose protected data. Connect your own storage and key workflows.</p><p className="otherwise">Otherwise, you must organize the primitives and field mappings yourself. Key management, authentication, and encryption scope still belong to your product.</p></div></li>
          </ol>
        </section>

        <section className="section" id="value">
          <SectionHeading number="04" label="WHY THIS FRAMEWORK" title="Why this framework" />
          <p className="section-intro">Four benefits, each with defined boundaries. The framework supplies reusable mechanisms; your product connects storage, security, and device policy.</p>
          <div className="value-list">
            <article className="value-item">
              <div className="value-heading"><span aria-hidden="true">01</span><h3>Open source under the MIT license</h3></div>
              <p>You can use, modify, and distribute flops-agent under the license terms. The framework source is public on GitHub, and PyPI provides <code>flops-agent</code> 0.2.0 for installation. This licensing statement applies only to this framework.</p>
              <p className="source-note">Read: <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/LICENSE">MIT license</a> · <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/pyproject.toml">Python package metadata</a></p>
            </article>
            <article className="value-item">
              <div className="value-heading"><span aria-hidden="true">02</span><h3>Server-side execution</h3></div>
              <p>The main agent loop runs on the server. Clients submit input and subscribe to output; device-dependent tools go to an executor. As a Python library, the framework fits inside your application service, and client disconnection does not stop an existing task.</p>
            </article>
            <article className="value-item" id="privacy-boundaries">
              <div className="value-heading"><span aria-hidden="true">03</span><h3>Encrypted sensitive fields with local client decryption</h3></div>
              <p>Sensitive conversation and agent-memory fields can be encrypted at rest and decrypted locally by the client. The server-side agent can decrypt relevant context while processing a task, with recovery mechanisms retained. Your product can integrate this path while explicitly identifying which data becomes plaintext during execution.</p>
              <div className="implementation-note"><span className="small-label">Flops product layer · current data paths</span><p>These are the actual data-processing boundaries. The product must integrate the framework's cryptographic primitives.</p></div>
              <dl className="privacy-facts">
                <div><dt>During task processing</dt><dd>The server decrypts relevant data while running a task. Keys enter runtime context, and decrypted context is supplied to the model and tool execution. Local client decryption therefore coexists with server-side decryption of relevant context.</dd></div>
                <div><dt>During chat-request transmission</dt><dd>User messages and references are sent as plaintext JSON fields; only runtime keys are RSA-wrapped. These chat fields rely on HTTPS/TLS for transport protection. Encrypted SSE frames protect streaming output, and executor tool connections form a separate protection layer; neither replaces chat-request transport protection.</dd></div>
                <div><dt>Runtime key lifetime</dt><dd>During normal login, the client holds and unwraps the user master key and the server stores encrypted envelopes, with offline recovery retained. Runtime keys may also remain in server memory or the Linux keyring for recovery and background wakeups, sometimes across runs. Cleanup is best-effort and can fail.</dd></div>
                <div><dt>Storage coverage</dt><dd>Only designated sensitive fields on encryption-enabled objects are handled. Ordinary metadata, summaries, some logs, and temporary tool results still have plaintext paths. Legacy data also has compatibility read and write paths; field encryption does not prove that every historical copy has been removed.</dd></div>
              </dl>
              <div className="value-boundary"><strong>The framework provides cryptographic primitives only.</strong><p>AES-256-GCM handles basic encryption and decryption; RSA-OAEP-SHA256 wraps transport keys; explicit field operations let callers select the fields. You still choose protected data and connect keys and operations to your storage path.</p><p>Key management, account authentication, KDFs (key derivation), key retention, and Redis storage belong to the product. Integrating the primitives still leaves key generation, identity verification, key lifetime, and actual storage to your application.</p></div>
            </article>
            <article className="value-item" id="device-boundaries">
              <div className="value-heading"><span aria-hidden="true">04</span><h3>A shared executor protocol for personal and cloud machines</h3></div>
              <p>Personal computers and cloud hosts can connect through the same executor protocol. Whether work can run depends on the device's capabilities, environment, authorization, and availability. Your product can connect different devices while checking target suitability at dispatch.</p>
              <div className="implementation-note"><span className="small-label">Flops product layer · current device behavior</span><p>These rules describe the current product implementation. In your system, device discovery, selection, and authorization remain product responsibilities.</p></div>
              <div className="table-scroll" tabIndex={0} role="region" aria-label="Current Flops offline and recovery behavior">
                <table className="device-table">
                  <caption className="sr-only">How device states are handled and what that means</caption>
                  <thead><tr><th scope="col">Situation</th><th scope="col">Current behavior</th></tr></thead>
                  <tbody>
                    <tr><th scope="row">Device offline during ordinary dispatch</th><td>Returns an identifiable error without automatic queuing. The product needs to explain that dispatch did not succeed.</td></tr>
                    <tr><th scope="row">The bound or session-owned device is offline</th><td>Rejects dispatch and asks whether to wait or switch devices. The next target requires an explicit decision.</td></tr>
                    <tr><th scope="row">Task already dispatched</th><td>Supports result replay without redispatching. Recovery reconnects to the existing task's result.</td></tr>
                    <tr><th scope="row">Connection lost during execution</th><td>Allows a grace period of about 90 seconds, then reconciles dispatched task state and results on reconnection. Recovery must use that reconciliation path. This duration describes the current product, not a framework guarantee.</td></tr>
                  </tbody>
                </table>
              </div>
              <ul className="device-rules">
                <li><strong>Browser tools require an explicit device.</strong> The product must identify the device on which the browser should operate.</li>
                <li><strong>Resource nodes use separate routing.</strong> Integrating these nodes requires their own dispatch path.</li>
                <li><strong>Devices declare their own capabilities.</strong> Missing capabilities cause an error rather than automatic rerouting. The product must check capabilities and handle failures.</li>
                <li><strong>Phones are currently clients only.</strong> There is no verified basis here for treating them as general-purpose tool executors, so they are not listed as equivalent execution devices.</li>
              </ul>
            </article>
          </div>
        </section>

        <section className="section" id="showcase">
          <SectionHeading number="05" label="SHOWCASE" title="Flops is one implementation" />
          <p className="section-intro">The question is whether others can build their own agent systems with flops-agent. Embed the framework in your product and connect your own models, storage, executors, and policies.</p>
          <div className="showcase-existing"><span className="small-label">Existing implementation · Flops</span><h3>Our implementation, provided as a reference and showcase</h3><p>Flops is a product built on flops-agent that demonstrates integration in a real application. It is currently the only existing showcase. Its account, security, and device policies belong to its product layer and do not constrain other framework uses.</p></div>
          <div className="possibilities"><p className="small-label">Possible uses (ideas)</p><ul><li><strong>Enterprise agent automation</strong><span>Organize enterprise workflows with the framework and connect the organization's models, storage, tools, and authorization policies.</span></li><li><strong>Other personal assistant products</strong><span>Build an assistant around personal needs, choosing your own clients, tools, and runtime environment.</span></li></ul><p className="aside-note">These are possible directions, not delivered implementations or a verified compatibility matrix.</p></div>
        </section>

        <section className="section" id="quick-start">
          <SectionHeading number="06" label="QUICK START" title="Observe a real run in five minutes." />
          <p className="section-intro">Understand the lifecycle with a deterministic offline demo. After installing dependencies, running it requires no model account, network connection, or model key.</p>
          <h3 className="step-heading"><span>01</span>Install the framework from PyPI</h3>
          <p>Use Python ≥ 3.10, preferably in a virtual environment. This installs a Python library. Next, save and run <code>hello.py</code>.</p>
          <CodeBlock title="Install flops-agent" language="SHELL" code={installExample} />
          <h3 className="step-heading"><span>02</span>Start work, then observe output</h3>
          <p>After installation, save the complete code below as <code>hello.py</code>. <code>DemoLLM</code> is a demo adapter that generates fixed text. InMemoryDatabase keeps conversations only in this process, so data does not survive program exit.</p>
          <p className="example-explanation"><code>start()</code> starts background work, while <code>subscribe()</code> observes events, separating the output connection from execution. In this example, <code>Runtime</code> holds reusable configuration, <code>Session</code> stores the conversation, <code>Query</code> represents new input, and <code>Run</code> is the handle for this run. Infrastructure, conversation state, and execution each have their own object.</p>
          <CodeBlock title="hello.py" language="PYTHON" code={codeExample} />
          <CodeBlock title="Run your minimal example" language="SHELL" code="python hello.py" />
          <div className="expected-output"><span className="eyebrow">Expected output</span><pre><code>{'Hello, this is an independent background run.\nRun status: done'}</code></pre></div>
          <p className="aside-note">For a real model, replace DemoLLM with your own <code>LLMStreamClient</code>. Its streaming-chunk contract lets you retain the main loop. Alternatively, run <code>pip install "flops-agent[providers]"</code>, then configure the bundled <code>OpenAIStreamClient</code>. It implements an OpenAI-compatible streaming protocol; supply the endpoint, model name, and key in your product. This page does not claim unverified provider compatibility.</p>
          <h3 className="step-heading"><span>03</span>Optional: run the repository example</h3>
          <p><code>docs/sample_product/server.py</code> ships with the public repository, not the PyPI package. To observe SSE text, tool, and completion events, clone the repository, install source dependencies from its root, and run the script.</p>
          <CodeBlock title="Get the source and run the example" language="SHELL" code={repositoryExample} />
          <p className="aside-note"><code>server.py</code> uses ScriptedLLM by default. Leave <code>EXAMPLE_PROVIDER_API_KEY</code> unset for the offline demo. It does not listen on a port. The executor demo and HTML client reference still need product integration; this is not a complete three-process application.</p>
          <p className="source-note">Read: <a href="/docs/01-quick-start">Quick start</a> · <a href="/docs/api_surface">Public API contract</a></p>
        </section>

        <section className="section" id="progress">
          <SectionHeading number="07" label="PROJECT STATUS" title="Status based on what exists today." />
          <p className="section-intro">The source and PyPI 0.2.0 are publicly released. The project remains in Beta with no version-compatibility guarantee. Production integration requires deployment and operations work in your product.</p>
          <div className="progress-grid">
            <article className="progress-panel available"><h3><span aria-hidden="true">✓</span> Available</h3><ul>
              <li><strong>Public repository (MIT), with <code>flops-agent</code> 0.2.0 on PyPI, installable using <code>pip install</code></strong><span>You can run <code>pip install flops-agent</code> directly. Source, license, and package all have public entry points.</span></li>
              <li><strong>Eight progressive tutorials and an API contract</strong><span>The API contract lists public interfaces you can check before integrating. The tutorial sequence runs from Quick start to Worked example.</span></li>
              <li><strong>CI and PyPI release workflows</strong><span>CI runs tests, type checks, and website builds. PyPI 0.2.0 is available to download and install.</span></li>
              <li><strong>sample_product demo</strong><span>Shows the responsibilities of the server, executor, and client.</span></li>
            </ul></article>
            <article className="progress-panel pending"><h3><span aria-hidden="true">—</span> Not included</h3><ul>
              <li><strong>Official hosting or one-click deployment</strong><span>The deliverable is a framework; deployment belongs to the product.</span></li>
              <li><strong>A complete three-process application</strong><span>The example does not wire up HTTP/WebSocket transport between services. Transport and recovery entry points still need implementation.</span></li>
              <li><strong>Stable API and service commitments</strong><span>Beta means the project is evolving, without guaranteed version compatibility. No service-level agreement (SLA) is offered.</span></li>
            </ul></article>
          </div>
          <p className="source-note">Read: <a href="https://github.com/Xenotech-Studio/flops-agent">Public GitHub repository</a> · <a href="https://pypi.org/project/flops-agent/0.2.0/">PyPI 0.2.0</a> · <a href="/docs/08-worked-example">Worked example guide</a></p>
        </section>

        <section className="section" id="non-goals">
          <SectionHeading number="08" label="NON-GOALS" title="Know what it does not provide before you start." />
          <ul className="non-goals-list">
            <li><span aria-hidden="true">01</span><div><h3>Not a ready-to-use service</h3><p>Embed it in a product and complete storage, executors, deployment, and business policy.</p></div></li>
            <li><span aria-hidden="true">02</span><div><h3>No bundled models or credentials</h3><p>Adapters and clients are provided; model selection, credentials, and key management are yours.</p></div></li>
            <li><span aria-hidden="true">03</span><div><h3>No account system or HTTP server</h3><p>Standard SSE supplies streaming-event encoding. Reuse that format while providing routes, authentication, authorization, and transport in your product.</p></div></li>
            <li><span aria-hidden="true">04</span><div><h3>No SLA</h3><p>No service-level agreement is offered. You must arrange product availability and response guarantees. Beta also carries no stable version-compatibility promise.</p></div></li>
          </ul>
        </section>

        <section className="section" id="mechanics">
          <SectionHeading number="09" label="MECHANICS" title="Integration details, when you need them" />
          <p className="section-intro">Replaceable interfaces let you connect models, storage, and execution environments to the same lifecycle mechanisms. Here are the names and boundaries.</p>
          <div className="table-scroll" tabIndex={0} role="region" aria-label="Framework interfaces and their integration roles">
            <table className="seams-table">
              <caption className="sr-only">Extension boundaries and what they mean for product developers</caption>
              <thead><tr><th scope="col">Integration point</th><th scope="col">What it provides</th></tr></thead>
              <tbody>
                <tr><th scope="row"><code>LLMStreamClient</code></th><td>Defines how models return streaming chunks, so the main loop can stay when a model integration changes.</td></tr>
                <tr><th scope="row"><code>ToolExecutor</code><br /><code>ExecutorLink</code></th><td>The first receives tool dispatch; the second provides a remote-connection protocol boundary. Tools can execute in another process or device. Device selection and authorization remain product responsibilities.</td></tr>
                <tr><th scope="row"><code>Database</code></th><td>Defines conversation reads, appends, truncation, and updates so you can connect a production database and retain framework persistence behavior.</td></tr>
                <tr><th scope="row"><code>RunStore</code></th><td>Stores execution state and replay logs. Persistent storage can support reconnection and restart recovery.</td></tr>
                <tr><th scope="row"><code>Inbox</code></th><td>Receives input arriving during execution so messages can enter subsequent processing at step or turn boundaries.</td></tr>
                <tr><th scope="row"><code>ToolRegistry</code></th><td>Registers tools and dispatch information to organize your catalog. Tool descriptions, capability filtering, and safety rules belong to your product.</td></tr>
                <tr><th scope="row"><code>crypto</code></th><td>Provides cryptographic primitives and explicit field operations for your mappings and storage flow. See the sensitive-field section above for the full data-processing boundaries.</td></tr>
              </tbody>
            </table>
          </div>
          <p className="aside-note"><code>Executor</code> is an executor role here, not an importable top-level class. Tool dispatch actually connects through <code>ToolExecutor</code>, described in the table above.</p>

          <div id="lifecycle" className="mechanics-subsection">
            <h3>An execution timeline</h3>
            <p className="mechanics-intro">Cancellation, suspension, and recovery are conditional branches. A task does not pass through every state in sequence.</p>
            <ol className="timeline">
              <li><span className="timeline-index" aria-hidden="true">1</span><div><h3>Assemble <code>Runtime</code></h3><p>It retains reusable model, tool, and storage configuration, avoiding reconstruction on every request.</p></div></li>
              <li><span className="timeline-index" aria-hidden="true">2</span><div><h3>Call <code>start(session, query)</code></h3><p>It returns a run handle immediately and advances Runner in the background. Runner manages the task state machine; the output connection does not drive execution.</p></div></li>
              <li><span className="timeline-index" aria-hidden="true">3</span><div><h3>Subscribe to model and tool events</h3><p><code>TextDelta → ToolCallStarted → ToolExecuting → ToolResult</code> describe text deltas, tool-call starts, tool execution, and results, giving the UI explicit progress events. <code>LoopFinished</code> marks normal completion so the client can clear its running indicator.</p></div></li>
              <li><span className="timeline-index" aria-hidden="true">4</span><div><h3>Stop, or wait for a human answer</h3><p><code>await run.stop()</code> requests a stop at a checkpoint and is an explicit cancellation entry point. <code>InteractionRequest</code> means a tool requests human input, followed by <code>InteractionRequested</code> and <code>Suspended</code>, allowing the pending interaction to be recorded and continued.</p></div></li>
              <li><span className="timeline-index" aria-hidden="true">5</span><div><h3>Continue after an answer, or recover after restart</h3><p><code>Query.answer(...)</code> submits a human answer for subsequent execution to incorporate into the conversation. <code>runtime.recover(resume)</code> schedules the product recovery entry point so a restart can reuse the original execution identity from persisted evidence.</p></div></li>
            </ol>
          </div>
          <div className="callout"><strong>Recovery depends on persisted data and a product recovery entry point.</strong><p>In-memory stores do not survive process restarts; production recovery needs persisted conversations and run logs. Idempotency avoids repeated side effects on retries, so the product executor must record and reuse dispatched tasks. <code>recover()</code> returns the number scheduled, not the number successfully recovered.</p></div>
          <div className="mechanics-subsection">
            <h3>Current event-format and reconnection limits</h3>
            <p><code>WireCodec</code> encodes events as standard SSE messages that can arrive incrementally. Products can reuse the output contract and connect their own service routing. <code>run.subscribe(from_cursor=...)</code> replays from the saved position and follows live output. cursor is a server-provided log position: save it rather than guessing from message counts.</p>
            <p className="aside-note">There is currently no facade that hydrates a read-only Run from shared storage by <code>run_id</code>. Multi-worker products must locate the owning process or implement replay routing. Default SSE replay frames do not receive a new cursor, so a client that closes after receiving only replay still needs a product-defined cursor policy.</p>
          </div>
          <p className="source-note">Read: <a href="/docs/02-core-concepts">Core concepts</a> · <a href="/docs/03-streaming-and-sse">Streaming and SSE</a> · <a href="/docs/05-cancellation-and-suspension">Cancellation and suspension</a> · <a href="/docs/06-recovery">Restart recovery</a> · <a href="/docs/07-extending">Extending the framework</a> · <a href="/docs/api_surface">Public API contract</a></p>
        </section>

        <section className="section docs-section" id="docs">
          <SectionHeading number="10" label="DOCUMENTATION" title="From one run to product boundaries." />
          <p className="section-intro">Follow the guides from quick start to integration to understand execution, storage, and extension boundaries.</p>
          <a className="docs-index" href="/docs"><div><span className="small-label">Start here</span><strong>Explore the docs</strong></div><span className="doc-open">Start reading →</span></a>
        </section>
      </main>
      </div>

      <footer className="site-footer page-column">
        <div className="footer-top"><a className="brand" href="#top">flops-agent</a><span>© 2026 Xenotech Studio · MIT License</span><a className="back-to-top" href="#top">Back to top ↑</a></div>
        <p>The framework is open source under MIT, with version 0.2.0 on PyPI and support for Python ≥ 3.10.</p>
      </footer>
    </>
  )
}

export default App
