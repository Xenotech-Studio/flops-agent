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

The `docs-sync-publish.yml` workflow mirrors default-branch documentation changes only.
The owner must add `FLOPS_AGENT_DOCS_SYNC_TOKEN` under this repository's
Settings → Secrets and variables → Actions: a fine-grained PAT scoped to
`Xenotech-Studio/flops-agent-docs` with Contents read/write permission.
No CI job deploys the site. See the new repository's `MIGRATION.md` for handoff steps.

`deploy/nginx-flopsruntime.conf` stays here temporarily for the existing server.
The eventual target is to serve the new repository's manually built `website/dist/`
(or copy that output into the existing release directory); the owner will handle
that later. This migration does not change nginx or the live deployment.
