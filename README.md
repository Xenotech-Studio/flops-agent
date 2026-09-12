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
