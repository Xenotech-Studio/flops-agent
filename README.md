# flops_agent

flops_agent is a service-agent framework. It puts execution lifecycle, event
streaming, SSE, cancellation, suspension, persistence, and restart recovery in
one reusable runtime. The product layer supplies its model, storage, tools, and
policy.

Start with the [documentation index](docs/README.md), then follow the
[five-minute quick start](docs/01-quick-start.md).

```bash
pip install -e ".[providers]"
python docs/sample_product/server.py
```

The framework has one todo source: [docs/TODO.md](docs/TODO.md).

## Documentation site

English documentation remains authored in this repository's `docs/`.
The website, publication manifests, translations and site build validation have moved to
[Xenotech-Studio/flops-agent-docs](https://github.com/Xenotech-Studio/flops-agent-docs).
Do not edit the generated `docs/en/` mirror there directly.

By default, `sync-docs.yml` in `flops-agent-docs` pulls this repository's `docs/**`
every 30 minutes without any token or secret. It also supports `workflow_dispatch`
for an immediate manual sync.

This repository's `docs-sync-publish.yml` remains an optional immediate push channel
for default-branch documentation changes. Without `FLOPS_AGENT_DOCS_SYNC_TOKEN`,
it skips the sync and succeeds. To enable immediate pushes, add that Actions secret
as a fine-grained PAT with Contents read/write permission on
`Xenotech-Studio/flops-agent-docs`.

The live site at https://flops-agent.xenotech.studio serves build output from
`flops-agent-docs`, including Chinese documentation at `/zh-CN/docs/<slug>`.
Site configuration and deployment belong to that repository, including
`deploy/nginx-flopsdocs.conf`. The site is built and deployed manually on the
server; no CI job deploys it.
