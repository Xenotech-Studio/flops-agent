# sample_product

This is a runnable reference for the documentation series, not another framework. Follow the [documentation reading order](../README.md) first, then read the [worked-product guide](../08-worked-example.md).

- `server.py`: how a product server assembles Runtime, starts a Run, and emits SSE; it uses an offline scripted demo unless `EXAMPLE_PROVIDER_API_KEY` is set and the placeholder provider values are replaced.
- `local_executor.py`: the independent executor-process protocol shape and cancellation self-demo.
- `frontend.html`: how a client saves server cursors, consumes SSE, and requests cancellation; it assumes the product exposes the corresponding HTTP endpoints.

Run from the repository root:

```bash
python docs/sample_product/server.py
python docs/sample_product/local_executor.py
```

The three files deliberately do not couple through Python imports; in production they may be separate services or repositories. The server and executor are independently runnable demos, while product code wires the HTTP/WebSocket transport.
