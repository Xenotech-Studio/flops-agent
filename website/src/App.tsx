import { useEffect, useRef, useState } from 'react'

const installExample = `# Python ≥ 3.10，建议在虚拟环境中执行
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
    # 演示用假 LLM：只返回固定文本，不调用模型或网络。
    async def acompletion(
        self, **kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        async def stream() -> AsyncIterator[StreamChunk]:
            for text in ("你好，", "这是一轮独立的后台执行。"):
                await asyncio.sleep(0)
                yield TextStreamChunk(text=text)
        return stream()


# 在应用启动时装配一次；这里的数据库仅在内存中保存。
runtime = Runtime(llm=DemoLLM(), database=InMemoryDatabase())


async def main() -> None:
    session: Session = await runtime.load_session("demo-session")
    run: Run = runtime.start(session, Query.text("演示一次执行。"))

    # start() 已启动工作；subscribe() 只观察事件。
    async for delivery in run.subscribe():
        if isinstance(delivery.event, TextDelta):
            print(delivery.event.text, end="", flush=True)
    print(f"\\nRun status: {run.status.value}")


asyncio.run(main())`

const documents = [
  { number: '01', title: '五分钟运行一个 Agent', original: 'Run an Agent in Five Minutes', file: '01-quick-start.md' },
  { number: '02', title: '核心概念', original: 'Core Concepts', file: '02-core-concepts.md' },
  { number: '03', title: '流式事件与 SSE', original: 'Streaming and SSE', file: '03-streaming-and-sse.md' },
  { number: '04', title: '会话与持久化', original: 'Sessions and Persistence', file: '04-sessions-and-persistence.md' },
  { number: '05', title: '取消、挂起与继续执行', original: 'Cancellation, Suspension, and Continuing a Turn', file: '05-cancellation-and-suspension.md' },
  { number: '06', title: '进程重启后的恢复', original: 'Restart Recovery', file: '06-recovery.md' },
  { number: '07', title: '扩展框架', original: 'Extend the Framework', file: '07-extending.md' },
  { number: '08', title: '完整示例导读', original: 'Worked Example', file: '08-worked-example.md' },
  { number: 'API', title: '公开 API 契约', original: 'Public API surface', file: 'api_surface.md' },
]

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
        复制代码
      </button>
      <span className={`copy-feedback${status === 'failed' ? ' copy-error' : ''}`} role="status">
        {status === 'copied' ? '已复制' : status === 'failed' ? '复制失败，请手动选择文本' : ''}
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
      <pre tabIndex={0} aria-label={`${title} 代码`}><code>{code}</code></pre>
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
      <a className="skip-link" href="#content">跳到正文</a>

      <header className="site-header" id="top">
        <a className="brand" href="#top" aria-label="flops-agent 首页">
          <img className="brand-mark" src="/favicon-64.png?v=9339b14ffac0" width="31" height="31" alt="" aria-hidden="true" />
          <span>flops-agent</span>
        </a>
        <nav className="header-nav" aria-label="主导航">
          <a href="#quick-start">快速开始</a>
          <a href="#docs">文档</a>
        </nav>
        <div className="public-links" aria-label="项目链接">
          <a href="https://github.com/Xenotech-Studio/flops-agent" title="GitHub 公开仓库 · MIT">GitHub <span className="external-mark" aria-hidden="true">↗</span></a>
          <a href="https://pypi.org/project/flops-agent/" title="PyPI · flops-agent 0.2.0">PyPI <span className="external-mark" aria-hidden="true">↗</span></a>
          <span className="link-status">MIT · 0.2.0</span>
        </div>
      </header>

      <main id="content" className="page-column">
        <section className="hero" aria-labelledby="hero-title">
          <p className="eyebrow hero-eyebrow"><span className="status-dot" aria-hidden="true" />独立的 Python 框架<span className="eyebrow-separator">/</span>flops_agent</p>
          <h1 id="hero-title">开箱即用的云端 agent 框架</h1>
          <p className="hero-copy" id="hero-copy">它跑在你自己的服务端上：把 agent 跑成服务该有的那套运行机制（执行与订阅、取消与恢复、多端续接、远端工具）都是现成的——模型、存储、执行器由你接。</p>
          <ul className="metadata" aria-label="项目元信息">
            <li>MIT 开源</li><li>PyPI 0.2.0</li><li>Development Status: <strong>Beta</strong></li><li>Python ≥ 3.10</li>
          </ul>
          <div className="hero-actions">
            <a className="button button-primary" href="#quick-start">看 5 分钟例子 <span aria-hidden="true">↓</span></a>
            <a className="button button-secondary" href="#non-goals">它不做什么 <span aria-hidden="true">↗</span></a>
          </div>
          <div className="hero-principle">
            <p className="hero-story">为持续运行的 AI 服务，提供清晰的执行生命周期。</p>
            <span>任务持续执行</span><span className="principle-divider" aria-hidden="true" /><span>客户端按需查看</span>
            <p>刷新或断开订阅，不会终止已经启动的任务；跨设备续接由产品层接入身份、存储与路由。</p>
          </div>
        </section>

        <a className="product-card" href="https://flops.xenotech.studio/intro">
          <strong className="product-card-title">flops-agent 是一个开源内核。</strong>
          <span>如果你需要一个开箱即用的产品，看这里 → <strong>Flops</strong></span>
        </a>

        <nav className="section-nav" aria-label="页面目录">
          <a href="#boundaries">它是什么</a><a href="#pitfalls">四个坑</a><a href="#why">省掉哪些工作</a><a href="#value">它凭什么</a><a href="#showcase">参考实现</a><a href="#progress">当前进展</a>
        </nav>

        <section className="section" id="boundaries">
          <SectionHeading number="01" label="边界 / SCOPE" title="它是什么 / 不是什么" />
          <p className="section-intro">flops-agent 是供你嵌入自己应用的 Python 框架。它组织一轮任务从开始到结束的过程；账号、界面、模型和业务决策由你的产品层提供。</p>
          <div className="table-scroll" tabIndex={0} role="region" aria-label="框架与产品层职责对照表，可横向滚动">
            <table className="boundary-table">
              <caption className="sr-only">框架提供的机制与产品层需要完成的工作</caption>
              <thead><tr><th scope="col">框架给什么</th><th scope="col">产品层填什么</th></tr></thead>
              <tbody>
                <tr><td><strong>任务运行与控制</strong><p>让工作在后台推进，记录输出，处理停止、等待回答和恢复。你可以复用这些执行机制。</p></td><td><strong>你的服务与业务入口</strong><p>界面、账号、访问权限和服务路由。谁能启动或查看任务，由你的产品决定。</p></td></tr>
                <tr><td><strong>可替换的接入接口</strong><p>接上自己的模型、存储和工具执行端。你可以沿用自己的部署环境。</p></td><td><strong>实际运行的基础设施</strong><p>模型、密钥、生产存储与设备环境。你负责选择、配置和维护。</p></td></tr>
                <tr><td><strong>事件约定与基础加解密操作</strong><p>约定怎样描述任务进展、怎样加解密指定字段。你有明确的接入位置。</p></td><td><strong>业务与安全策略</strong><p>工具文案、安全阈值、字段选择和密钥管理。你决定使用范围与访问边界。</p></td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="section" id="pitfalls">
          <SectionHeading number="02" label="故事 / FROM LOOP TO SERVICE" title="把 agent 做成服务，会碰到四个坑。" />
          <p className="section-intro">本地循环跑通以后，下一步是让它接住真实的使用：人会离开，设备会掉线，数据需要保护，工具也会越来越多。</p>
          <div className="pitfalls-list">
            <article className="pitfall" aria-labelledby="pitfall-survival">
              <p className="pitfall-label">01 / 存活</p>
              <h3 id="pitfall-survival">进程会停，任务需要接续。</h3>
              <p>第一版常常只是本地一个循环。运行状态如果都留在内存里，进程一停，未完成的工作就失去了上下文。搬到服务端后，你还得把“任务在运行”和“有人正在看”分开：用户关掉页面，已经启动的任务仍应继续。</p>
              <p>断线重连时，客户端带上游标，从保留的事件日志回放，再接着看实时输出。服务重启则多一层要求：先持久保存会话与执行记录，再在启动时接上恢复入口，才能自动调度在途任务继续执行。恢复仍可能失败，工具副作用也需要幂等处理。</p>
              <p className="pitfall-answer">所以框架把执行与订阅分开，并提供重连回放与恢复编排。你接入持久化存储和产品层恢复函数；默认内存存储不会跨进程保留任务。</p>
            </article>
            <article className="pitfall" aria-labelledby="pitfall-hands">
              <p className="pitfall-label">02 / 手</p>
              <h3 id="pitfall-hands">服务端能想能说，手却在别的设备上。</h3>
              <p>读文件、跑命令、操作浏览器，往往要碰到用户的设备。直觉是每台设备跑一个 MCP server，再加内网穿透；如果把它理解成“云端逐台连进去”，连接方向就错了。设备在 NAT 后，会休眠、会断网，需要设备主动“拨号回家”，上线后注册自己、声明能力，断线后重新接入。</p>
              <p>接上隧道之后，还有三件事要补。连通不等于一个已注册、持续报活、带能力声明的活成员，身份、心跳与在线状态需要维护；隧道本身不负责确认任务是否投递、结果是否收到；多台设备同时在线时，还要按能力检查目标，处理“到底交给哪台”的歧义。</p>
              <p className="pitfall-answer">所以框架留出远程执行器接缝，让产品层接入设备主动连接，再复用工具派发、结果确认与恢复等待。设备目录、授权和目标选择由产品层完成；跨进程结果恢复还需持久化投递记录。当前 Flops 的普通新调用遇到离线设备会报错，不自动排队或换机。</p>
            </article>
            <article className="pitfall" aria-labelledby="pitfall-data">
              <p className="pitfall-label">03 / 数据</p>
              <h3 id="pitfall-data">对话与记忆，会流过你的库。</h3>
              <p>任务开始读写真实数据后，你需要决定哪些字段该加密、谁能解密，以及恢复任务时密钥留多久。以 Flops 当前接入为例，启用加密的对话与 agent 的指定敏感字段支持加密存储，客户端支持本地解密。</p>
              <p>日常登录采用客户端持有并解包用户主密钥、服务端保存加密信封的设计，同时保留离线恢复机制。云端在处理任务时会解密相关上下文；运行密钥可能为恢复和后台唤醒跨 run 暂存，清理也有失败边界。</p>
              <p>当前聊天请求中的用户消息与引用仍是明文 JSON 字段，传输依赖 HTTPS/TLS；运行密钥另用 RSA 包装。指定字段之外，压缩摘要、部分日志与工具结果暂存仍有明文路径，字段加密的覆盖范围需要逐项确认。</p>
              <p className="pitfall-answer">所以框架提供可组合的字段加密与传输密钥包装原语。受保护字段、认证和密钥存续由产品层决定；<a href="#privacy-boundaries">下文保留当前数据路径的具体边界</a>。</p>
            </article>
            <article className="pitfall" aria-labelledby="pitfall-capabilities">
              <p className="pitfall-label">04 / 能力</p>
              <h3 id="pitfall-capabilities">工具越来越多，别把所有能力一次塞给模型。</h3>
              <p>一个 agent 能做什么，取决于它能调用什么工具。起初把几个函数列进去就够了；业务多起来后，每轮任务需要的工具不同，设备能提供的能力也不同，模型不必一直背着整张工具目录。</p>
              <p>工具按包声明和注册，再按会话打开或关闭。模型只收到会话已打开或由产品层临时启用、且通过能力过滤的包内工具；根级基础工具另行提供，打开包不会顺带打开所有子包。可见性之外，真正派发时仍要检查目标设备、授权和安全策略。</p>
              <p className="pitfall-answer">所以框架负责工具包注册与会话开关，让你把自己的业务工具接进来。框架不捆绑你的业务，包的内容、执行方式与产品策略仍由你定义。</p>
            </article>
          </div>
        </section>

        <section className="section" id="why">
          <SectionHeading number="03" label="价值 / LESS TO BUILD" title="你会省掉的那几件事" />
          <p className="section-intro">如果你正在自己写 agent 循环，下面这些工作可以复用框架的机制。你的产品层仍需接好身份、存储、设备和业务策略。</p>
          <ol className="benefits-list">
            <li><span className="benefit-number" aria-hidden="true">01</span><div><h3>刷新页面，长任务继续做</h3><p>执行与订阅解耦，所以用户离开页面或网络掉线时，已启动的工作仍继续推进。</p><p className="otherwise">不然你得自己把后台任务与连接的生命周期拆开，处理无人订阅时的执行。</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">02</span><div><h3>停止、等待、继续，都有明确的状态</h3><p>取消、挂起、恢复、重连有各自的控制路径，所以你能按任务当前状态接上用户操作。</p><p className="otherwise">不然你得自己维护这些状态机，处理停止检查、待回答记录、重启恢复与遗漏输出的回放。</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">03</span><div><h3>换个客户端，还能接着看同一轮工作</h3><p>会话记录与输出日志为多端续接提供基础，所以你接好共享存储、身份与路由后，可以让不同客户端继续查看。</p><p className="otherwise">不然你得自己保存历史、定位同一轮任务，并记录每个客户端读到了哪里。</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">04</span><div><h3>需要设备的工作，可以交给别处执行</h3><p>远程执行器把工具工作交给另一个进程或设备，所以主循环可以留在你的服务里。</p><p className="otherwise">不然你得自己设计派发、增量输出、停止与恢复记录的接入方式；设备选择和授权仍需产品层完成。</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">05</span><div><h3>前后端对“发生了什么”有共同约定</h3><p>事件与 wire 格式是任务状态和传输消息的明确约定，所以界面可以区分文本、工具结果、等待与结束；Beta 阶段不承诺版本兼容。</p><p className="otherwise">不然你得自己定义消息类型、先后顺序与回放位置，并让服务端和客户端保持一致。</p></div></li>
            <li><span className="benefit-number" aria-hidden="true">06</span><div><h3>敏感字段有明确的加解密接入位置</h3><p>加密原语是基础加解密操作，字段加密接口让产品层显式选择要保护的数据，所以你可以接入自己的存储与密钥流程。</p><p className="otherwise">不然你得自己组织这些基础操作与字段映射；密钥管理、认证和哪些数据需要加密仍由产品层负责。</p></div></li>
          </ol>
        </section>

        <section className="section" id="value">
          <SectionHeading number="04" label="依据 / WHY THIS FRAMEWORK" title="它凭什么" />
          <p className="section-intro">四条价值，对应四组边界。框架提供可复用的机制，产品层把存储、安全与设备策略接起来。</p>
          <div className="value-list">
            <article className="value-item">
              <div className="value-heading"><span aria-hidden="true">01</span><h3>开源：flops-agent 采用 MIT 许可证</h3></div>
              <p>对你意味着：可以按许可证条款使用、修改和分发 flops-agent。框架代码已在 GitHub 公开，PyPI 已发布 <code>flops-agent</code> 0.2.0，可直接安装。这里的许可声明仅针对这个框架。</p>
              <p className="source-note">阅读：<a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/LICENSE">MIT 许可证</a> · <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/pyproject.toml">Python 包信息</a></p>
            </article>
            <article className="value-item">
              <div className="value-heading"><span aria-hidden="true">02</span><h3>面向云端：主循环在服务端推进</h3></div>
              <p>主 agent 循环由服务端运行：客户端提交输入、订阅输出，需要落到设备上的工具再交给执行端。对你意味着：框架作为 Python 库，可以部署在应用自己的服务里；客户端断线不会终止已启动的任务。</p>
            </article>
            <article className="value-item" id="privacy-boundaries">
              <div className="value-heading"><span aria-hidden="true">03</span><h3>敏感字段加密存储，客户端支持本地解密</h3></div>
              <p>支持对对话与 agent 记忆中的敏感字段加密存储、在客户端本地解密；云端 agent 在处理任务时可解密相关上下文，并保留恢复机制。对你意味着：可以在产品层接入这条存储与解密路径，同时需要明确哪些数据在任务处理时会成为明文。</p>
              <div className="implementation-note"><span className="small-label">Flops 产品层 · 当前数据路径</span><p>下面说明实际的数据处理边界；框架的基础加解密操作需要由产品层接入。</p></div>
              <dl className="privacy-facts">
                <div><dt>任务处理时</dt><dd>服务端运行一轮任务时，会解密并拿到相关明文：密钥写入运行时上下文，解密后的相关上下文交给模型与工具执行使用。因此，客户端支持本地解密，服务端也会解密相关上下文。</dd></div>
                <div><dt>聊天请求传输时</dt><dd>用户消息与引用仍以明文 JSON 字段发送，只有运行密钥使用 RSA 包装；因此，这些聊天字段的传输保护依赖 HTTPS/TLS 加密连接。SSE 加密帧保护流式输出，执行端工具链路是另一保护层，两者不能替代聊天请求的传输保护。</dd></div>
                <div><dt>运行密钥存续</dt><dd>日常登录由客户端持有并解包用户主密钥，服务端保存加密信封，同时保留离线恢复机制。运行密钥另可为恢复与后台唤醒暂存在服务器内存或 Linux keyring，部分路径跨 run 保留；清理是尽力执行，有失败边界。</dd></div>
                <div><dt>存储覆盖范围</dt><dd>仅对启用加密的对象处理指定敏感字段。普通元数据、压缩摘要、部分日志与工具结果暂存仍有明文路径；旧数据也存在兼容读取和写入路径，不能据字段加密推断所有历史副本已清理。</dd></div>
              </dl>
              <div className="value-boundary"><strong>框架只提供加密原语。</strong><p>AES-256-GCM 用于基础数据加解密；RSA-OAEP-SHA256 用于传输密钥包装；显式字段加解密让调用方指定要处理的字段。所以你仍需选择受保护字段，并把密钥与操作接入自己的存储路径。</p><p>密钥管理、账号认证、KDF（密钥派生）、密钥暂存和 Redis 存储都在产品层。所以接入这些原语之后，产品层仍要完成生成密钥、验证身份、管理密钥存续与实际存储的流程。</p></div>
            </article>
            <article className="value-item" id="device-boundaries">
              <div className="value-heading"><span aria-hidden="true">04</span><h3>多设备接入：个人电脑与云主机使用统一协议</h3></div>
              <p>个人电脑和云主机可以通过统一的执行端协议接入；任务能否执行，取决于该设备的能力、环境、授权和在线状态。对你意味着：可以把不同设备接进自己的系统，派发时仍要检查目标设备是否满足条件。</p>
              <div className="implementation-note"><span className="small-label">Flops 产品层 · 当前设备行为</span><p>下面的规则属于当前产品实现；在自己的系统里，设备发现、选择与授权仍需产品层完成。</p></div>
              <div className="table-scroll" tabIndex={0} role="region" aria-label="Flops 当前离线与恢复行为">
                <table className="device-table">
                  <caption className="sr-only">不同设备状态的当前处理方式及其影响</caption>
                  <thead><tr><th scope="col">遇到的情况</th><th scope="col">当前处理方式</th></tr></thead>
                  <tbody>
                    <tr><th scope="row">普通派发时，设备离线</th><td>直接返回可识别错误，不会自动排队。因此，产品需要向用户说明未派发成功。</td></tr>
                    <tr><th scope="row">绑定或会话所属设备离线</th><td>拒绝派发并请示是等待还是换机。因此，继续使用哪台设备需要明确决定。</td></tr>
                    <tr><th scope="row">任务已经派发</th><td>支持结果回放，不重复下发。因此，恢复时接回已有任务的结果。</td></tr>
                    <tr><th scope="row">运行中失联</th><td>有约 90 秒宽限，并在重连后对账，即核对已派发任务的状态与结果。因此，恢复要走核对流程；该时长是当前产品行为，不是框架时限承诺。</td></tr>
                  </tbody>
                </table>
              </div>
              <ul className="device-rules">
                <li><strong>浏览器工具必须显式指定设备。</strong>因此，产品需要明确告诉执行链路在哪台设备上操作浏览器。</li>
                <li><strong>资源节点走独立路由。</strong>因此，接入这类节点时需要处理其单独的派发路径。</li>
                <li><strong>能力由设备自己声明。</strong>缺少能力时直接报错，不会自动改投另一台；因此，产品需要检查能力并处理失败。</li>
                <li><strong>手机端目前只是客户端。</strong>当前未找到它作为通用工具执行端的依据，因此这里不把手机列为同等的执行设备。</li>
              </ul>
            </article>
          </div>
        </section>

        <section className="section" id="showcase">
          <SectionHeading number="05" label="实例 / SHOWCASE" title="Flops 只是其中一个实现" />
          <p className="section-intro">这页的重点是：别人能不能用 flops-agent 做自己的 agent 系统。你可以将框架嵌入自己的产品，接入自己的模型、存储、执行器与策略。</p>
          <div className="showcase-existing"><span className="small-label">已存在的实例 · Flops</span><h3>我们自己的实现，作为参考实现 / showcase</h3><p>Flops 是构建在 flops-agent 之上的一个具体产品，用来展示这些机制如何接入实际应用。目前只有 Flops 是已存在的实例；它的账号、安全与设备策略属于自己的产品层，不限定框架的其他用法。</p></div>
          <div className="possibilities"><p className="small-label">可以这么用（设想）</p><ul><li><strong>企业级 agent 自动化</strong><span>用框架组织企业自己的任务流程，接入企业自己的模型、存储、工具与权限策略。</span></li><li><strong>其他个人助理工具</strong><span>围绕个人需要构建自己的助理产品，选择自己的客户端、工具和运行环境。</span></li></ul><p className="aside-note">以上是使用方向的设想，尚不是已交付的实例或已验证的兼容范围。</p></div>
        </section>

        <section className="section" id="quick-start">
          <SectionHeading number="06" label="动手 / QUICK START" title="5 分钟，观察一轮真正的执行。" />
          <p className="section-intro">先用确定性的离线演示理解生命周期。安装依赖后，演示运行无需模型账号、网络连接或模型密钥。</p>
          <h3 className="step-heading"><span>01</span>从 PyPI 安装框架</h3>
          <p>使用 Python ≥ 3.10，建议先进入虚拟环境。安装的是 Python 库；完成安装后，按下一步保存并运行 <code>hello.py</code>。</p>
          <CodeBlock title="安装 flops-agent" language="SHELL" code={installExample} />
          <h3 className="step-heading"><span>02</span>先启动工作，再观察输出</h3>
          <p>完成上面的安装后，将下列完整代码保存为 <code>hello.py</code>。<code>DemoLLM</code> 是演示用假适配器，只生成固定文本；这里使用的 InMemoryDatabase 只在进程内保存会话，所以关闭程序后不会保留数据。</p>
          <p className="example-explanation"><code>start()</code> 启动后台任务，<code>subscribe()</code> 观察事件；所以读取输出的连接可以独立于执行过程。代码里的 <code>Runtime</code> 负责长期装配，<code>Session</code> 保存会话，<code>Query</code> 表达新输入，<code>Run</code> 是本轮执行的句柄；所以基础设施、会话和当前任务有各自的存放位置。</p>
          <CodeBlock title="hello.py" language="PYTHON" code={codeExample} />
          <CodeBlock title="运行自己的最小例子" language="SHELL" code="python hello.py" />
          <div className="expected-output"><span className="eyebrow">预期输出</span><pre><code>{'你好，这是一轮独立的后台执行。\nRun status: done'}</code></pre></div>
          <p className="aside-note">接入真实模型时，用自己的 <code>LLMStreamClient</code> 替换 DemoLLM；它约定了模型如何返回流式片段，所以主循环可以沿用。也可先执行 <code>pip install "flops-agent[providers]"</code>，再配置随包提供的 <code>OpenAIStreamClient</code>；它对接 OpenAI 兼容的流式协议，所以你可以在产品层填入端点、模型名称和密钥。本页不承诺未验证的供应商兼容范围。</p>
          <h3 className="step-heading"><span>03</span>可选：运行仓库示例</h3>
          <p><code>docs/sample_product/server.py</code> 随公开仓库提供，不包含在 PyPI 包中。要观察文本、工具和结束事件的 SSE 输出，先克隆仓库，再在仓库根目录安装源码依赖并运行脚本。</p>
          <CodeBlock title="获取源码并运行仓库示例" language="SHELL" code={repositoryExample} />
          <p className="aside-note"><code>server.py</code> 默认使用 ScriptedLLM；保持 <code>EXAMPLE_PROVIDER_API_KEY</code> 未设置即可运行离线演示。它不监听网络端口，执行器演示与 HTML 客户端参考仍需产品层接线，尚不是完整三进程成品。</p>
          <p className="source-note">阅读：<a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/01-quick-start.md">快速开始</a> · <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/api_surface.md">公开 API 契约</a></p>
        </section>

        <section className="section" id="progress">
          <SectionHeading number="07" label="状态 / PROJECT STATUS" title="当前进展，按已经存在的东西说。" />
          <p className="section-intro">框架代码与 PyPI 0.2.0 已公开发布。项目仍处于 Beta 阶段，API 不承诺版本兼容，生产接入需要产品层完成部署与运维。</p>
          <div className="progress-grid">
            <article className="progress-panel available"><h3><span aria-hidden="true">✓</span> 已具备</h3><ul>
              <li><strong>仓库已公开（MIT）、PyPI 已有 <code>flops-agent</code> 0.2.0，可 <code>pip install</code></strong><span>可直接执行 <code>pip install flops-agent</code>；源码、许可证与安装包均已有公开入口。</span></li>
              <li><strong>8 篇渐进文档 + API 契约</strong><span>API 契约列出公开接口，所以你能核对可以依赖的接入点；正文按 Quick start 到 Worked example 组织。</span></li>
              <li><strong>CI 与 PyPI 发布工作流</strong><span>CI 自动执行测试、类型检查和站点构建；PyPI 已发布 0.2.0，可直接下载和安装。</span></li>
              <li><strong>sample_product 演示</strong><span>展示服务端、执行器与客户端的职责边界。</span></li>
            </ul></article>
            <article className="progress-panel pending"><h3><span aria-hidden="true">—</span> 尚未具备</h3><ul>
              <li><strong>官方托管与一键部署</strong><span>目前交付框架，部署方案由产品层完成。</span></li>
              <li><strong>完整三进程成品</strong><span>HTTP / WebSocket 是服务间的传输连接，示例尚未接通；所以仍需补齐传输与恢复入口。</span></li>
              <li><strong>稳定 API 与服务承诺</strong><span>Beta 表示仍在演进，所以不承诺版本兼容；SLA 是服务级别保证，目前不提供。</span></li>
            </ul></article>
          </div>
          <p className="source-note">阅读：<a href="https://github.com/Xenotech-Studio/flops-agent">GitHub 公开仓库</a> · <a href="https://pypi.org/project/flops-agent/0.2.0/">PyPI 0.2.0</a> · <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/08-worked-example.md">完整示例导读</a></p>
        </section>

        <section className="section" id="non-goals">
          <SectionHeading number="08" label="非目标 / NON-GOALS" title="使用之前，先明确它不做什么。" />
          <ul className="non-goals-list">
            <li><span aria-hidden="true">01</span><div><h3>不是开箱即用的服务</h3><p>需要嵌入产品层，完成存储、执行器、部署和业务策略。</p></div></li>
            <li><span aria-hidden="true">02</span><div><h3>不内置模型与密钥</h3><p>提供适配接口与客户端；模型选择、Key 和密钥管理由你提供。</p></div></li>
            <li><span aria-hidden="true">03</span><div><h3>不含账号与 HTTP 服务</h3><p>标准 SSE 提供流式事件编码，所以你能复用输出格式；路由、认证、权限和传输仍属产品层。</p></div></li>
            <li><span aria-hidden="true">04</span><div><h3>不承诺 SLA</h3><p>SLA 是服务级别保证，目前不提供；所以你需要自行安排产品的可用性与响应保障。Beta 阶段也不承诺稳定的版本兼容性。</p></div></li>
          </ul>
        </section>

        <section className="section" id="mechanics">
          <SectionHeading number="09" label="接入 / MECHANICS" title="机制细节（想深入再看）" />
          <p className="section-intro">缝合口是可以替换实现的接口，所以你可以把模型、存储与执行环境接到同一套生命周期机制上。下面再看具体名称与限制。</p>
          <div className="table-scroll" tabIndex={0} role="region" aria-label="框架接口及其接入意义">
            <table className="seams-table">
              <caption className="sr-only">缝合口与对产品开发者的意义</caption>
              <thead><tr><th scope="col">接线点</th><th scope="col">对你意味着什么</th></tr></thead>
              <tbody>
                <tr><th scope="row"><code>LLMStreamClient</code></th><td>约定模型如何返回流式片段，所以更换模型接入时可以保留主循环。</td></tr>
                <tr><th scope="row"><code>ToolExecutor</code><br /><code>ExecutorLink</code></th><td>前者接收工具派发，后者提供远端连接的协议接入，所以工具可以在另一个进程或设备执行。设备选择与授权仍需产品层实现。</td></tr>
                <tr><th scope="row"><code>Database</code></th><td>约定会话的读取、追加、截断和更新，所以你可以接上自己的生产数据库，保留框架的会话写入机制。</td></tr>
                <tr><th scope="row"><code>RunStore</code></th><td>保存执行状态与回放日志，所以持久存储可以为重连和重启恢复提供依据。</td></tr>
                <tr><th scope="row"><code>Inbox</code></th><td>接收执行过程中到来的新输入，所以消息可以在当前一步或一轮结束的边界进入后续处理。</td></tr>
                <tr><th scope="row"><code>ToolRegistry</code></th><td>登记工具及其派发信息，所以你能组织自己的工具目录；工具文案、能力筛选和安全规则由产品层决定。</td></tr>
                <tr><th scope="row"><code>crypto</code></th><td>提供基础加解密与显式字段操作，所以你能接入自己的字段映射和存储流程。完整的数据处理边界见上文“敏感字段加密存储”。</td></tr>
              </tbody>
            </table>
          </div>
          <p className="aside-note"><code>Executor</code> 在这里是执行器角色的名称，所以不要把它当作可以直接导入的顶层类；工具派发实际接入 <code>ToolExecutor</code>，其作用见上表。</p>

          <div id="lifecycle" className="mechanics-subsection">
            <h3>一条执行时间线</h3>
            <p className="mechanics-intro">取消、挂起与恢复是按条件进入的分支，所以每轮任务不必依次经历所有状态。</p>
            <ol className="timeline">
              <li><span className="timeline-index" aria-hidden="true">1</span><div><h3>装配 <code>Runtime</code></h3><p>它保存长期复用的模型、工具和存储配置，所以无需每次请求都重建这些接入关系。</p></div></li>
              <li><span className="timeline-index" aria-hidden="true">2</span><div><h3>调用 <code>start(session, query)</code></h3><p>它立即返回本轮执行句柄，并让 Runner 在后台推进；Runner 是一轮任务的状态机，所以读取输出的连接不用驱动执行。</p></div></li>
              <li><span className="timeline-index" aria-hidden="true">3</span><div><h3>订阅模型与工具事件</h3><p><code>TextDelta → ToolCallStarted → ToolExecuting → ToolResult</code> 分别描述文本增量、工具调用开始、工具执行与结果；所以界面能按明确事件展示进展。<code>LoopFinished</code> 表示正常结束，所以客户端可以收起运行状态。</p></div></li>
              <li><span className="timeline-index" aria-hidden="true">4</span><div><h3>取消，或等待人工回答</h3><p><code>await run.stop()</code> 请求在检查点停止，所以它是明确的取消入口。<code>InteractionRequest</code> 表示工具请求人工输入，随后发出 <code>InteractionRequested</code> 与 <code>Suspended</code>；所以待回答的交互可以被记录并继续。</p></div></li>
              <li><span className="timeline-index" aria-hidden="true">5</span><div><h3>回答后继续，或在重启后恢复</h3><p><code>Query.answer(...)</code> 提交人工回答，所以后续执行能把回答纳入会话。<code>runtime.recover(resume)</code> 调度产品层的恢复入口，所以重启后可以按保存的事实复用原执行标识。</p></div></li>
            </ol>
          </div>
          <div className="callout"><strong>恢复依赖持久保存的数据与产品层的恢复入口。</strong><p>内存存储不跨进程重启保留数据，所以生产恢复需要持久化会话与执行日志。幂等性指重试时避免重复副作用，所以产品层执行器必须记录并复用已派发任务。<code>recover()</code> 返回已调度数量，所以不能把这个数字当作恢复成功数量。</p></div>
          <div className="mechanics-subsection">
            <h3>事件格式与重连的当前边界</h3>
            <p><code>WireCodec</code> 将事件编码为标准 SSE，也就是可逐条送达客户端的消息格式；所以产品可以复用输出约定，再接自己的服务路由。<code>run.subscribe(from_cursor=...)</code> 从保存的位置回放并继续订阅；cursor 是服务端给出的日志位置，所以客户端应保存它，不能靠数消息猜测。</p>
            <p className="aside-note">当前没有按 <code>run_id</code> 从共享存储还原只读 Run 的门面，也就是直接读取既有执行的统一入口；所以多 worker（多个服务进程）场景要由产品层定位执行所在进程，或实现自己的回放路由。默认 SSE 回放帧不补发 cursor，所以仅收到回放就关闭的客户端仍需产品层提供游标策略。</p>
          </div>
          <p className="source-note">阅读：<a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/02-core-concepts.md">核心概念</a> · <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/03-streaming-and-sse.md">流式事件与 SSE</a> · <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/05-cancellation-and-suspension.md">取消与挂起</a> · <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/06-recovery.md">重启恢复</a> · <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/07-extending.md">扩展框架</a> · <a href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/api_surface.md">公开 API 契约</a></p>
        </section>

        <section className="section docs-section" id="docs">
          <SectionHeading number="10" label="阅读 / DOCUMENTATION" title="从一轮执行，读到产品边界。" />
          <p className="section-intro">从快速开始到完整示例，再到公开 API 契约。以下文档均可在 GitHub 直接阅读。</p>
          <a className="docs-index" href="https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/README.md"><div><span className="small-label">阅读起点</span><strong>文档索引与阅读顺序</strong></div><span className="doc-open">在 GitHub 阅读 <span aria-hidden="true">↗</span></span></a>
          <ol className="docs-list">
            {documents.map((doc) => (
              <li key={doc.file}>
                <a className="doc-link" href={`https://github.com/Xenotech-Studio/flops-agent/blob/master/docs/${doc.file}`}>
                  <span className="doc-number" aria-hidden="true">{doc.number}</span>
                  <div className="doc-description"><h3>{doc.title}</h3><p lang="en">{doc.original}</p></div>
                  <span className="doc-open">阅读 <span aria-hidden="true">↗</span></span>
                </a>
              </li>
            ))}
          </ol>
        </section>
      </main>

      <footer className="site-footer page-column">
        <div className="footer-top"><a className="brand" href="#top">flops-agent</a><span>© 2026 Xenotech Studio · MIT License</span><a className="back-to-top" href="#top">回到顶部 ↑</a></div>
        <p>框架代码以 MIT 许可证公开，PyPI 已发布 0.2.0，支持 Python ≥ 3.10。</p>
      </footer>
    </>
  )
}

export default App
