# 框架文档

从一轮执行，读到产品边界。flops-agent 面向将 agent 作为服务运行的开发者：先运行确定性的离线示例，再逐步接入网络、存储与自己的业务策略。

## 入门

1. [Run an Agent in Five Minutes](01-quick-start.md) — 安装、启动并订阅第一轮执行。
2. [Core Concepts: Why Run, Session, Runner, and Runtime Exist](02-core-concepts.md) — 理解核心对象与生命周期。

## 核心机制

3. [Streaming and SSE: Connections May End; Execution Does Not](03-streaming-and-sse.md) — 事件、游标与重连。
4. [Sessions and Persistence: Treat History as Evolving Data](04-sessions-and-persistence.md) — 会话、细粒度写入与执行存储。
5. [Cancellation, Suspension, and Continuing a Turn](05-cancellation-and-suspension.md) — 停止、等待回答与执行中输入。
6. [Restart Recovery: Continue After a Process Dies](06-recovery.md) — 关闭、恢复编排与产品回调。

## 扩展与参考

7. [Extend the Framework: Tools, Runners, Memory, and Executors](07-extending.md) — 接入工具、策略、记忆与远端执行器。
8. [Worked Example](08-worked-example.md) — 对照示例理解服务端、执行器与客户端职责。

[API 契约](api_surface.md)列出公开接口与扩展边界；[Changelog](CHANGELOG.md)记录版本变更。

## 阅读边界

框架提供执行生命周期、事件与扩展接口。HTTP 路由、账号与授权、实际存储、模型供应商及业务安全策略由产品层接入。示例用于说明这些边界，不是开箱即用的生产服务。

本概览依据仓库 docs/README.md 编写。教程与 API 正文来自构建时的本地仓库；可使用页面上的 Markdown 出口获取对应内容。
