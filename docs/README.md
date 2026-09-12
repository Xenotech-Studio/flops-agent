# flops_agent documentation

flops_agent is for people building agents that run as services. It is more than
“call a model and yield text”: browser refreshes, a second device, long-running
tools, cancellation, and process restarts are ordinary paths.

This is a short course, not an API index. Run a deterministic example first, then
learn the objects and add network, storage, and deployment concerns one layer at a
time. By Article 8, you should be able to distinguish lifecycle mechanics owned by
the framework from HTTP, authentication, storage, and business policy owned by
the product.

Read in this order. Each article depends only on earlier ones and continues with
the same sample_product.

1. [Quick start: run an agent in five minutes](01-quick-start.md) — install, start, and subscribe to a first turn.
2. [Core concepts](02-core-concepts.md) — build the mental model for Run, Session, Runner, and Runtime.
3. [Streaming and SSE](03-streaming-and-sse.md) — events, cursors, reconnection, and standard wire format.
4. [Sessions and persistence](04-sessions-and-persistence.md) — Database, fine-grained writes, and RunStore.
5. [Cancellation and suspension](05-cancellation-and-suspension.md) — stopping work, human input, and input during a turn.
6. [Restart recovery](06-recovery.md) — shutdown, recovery orchestration, and product hooks.
7. [Extending the framework](07-extending.md) — where product policy belongs.
8. [Worked example](08-worked-example.md) — map the course to three independently understandable product roles.

Follow the next link at each article's end on a first read. For a public name,
treat the package [__init__.py](../src/flops_agent/__init__.py) as the contract and relevant tests
as executable examples. Do not use this series as an encyclopedia.

For the full inventory behind that contract — every symbol a known embedding
product uses, why it is or isn't exported, and what's flagged as product-shaped
and not framework material — see [api_surface.md](api_surface.md).

For active framework work, read only [TODO.md](TODO.md). It is the sole todo
source; this series does not duplicate it.

## Scope of this guide

“Framework” here means flops_agent. The embedding application is the product
layer. Ask whether a third party building a non-Flops agent also needs a feature:
general runtime mechanics belong in the framework; HTTP routes, accounts, tool
catalogs, provider choice, and safety thresholds are product policy.

The former ARCHITECTURE.md and RECOVERY.md material is now part of this path:
concepts and boundaries are in Articles 2, 4, and 7; the recovery recipe is in
Article 6. Example code remains in [sample_product/](sample_product/), with tests.
It demonstrates the three role boundaries: server and executor run independently,
while the HTML is a client reference for real product endpoints rather than a
ready-made web service.
