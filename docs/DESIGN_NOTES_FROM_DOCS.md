# Design observations from writing the documentation

This file records APIs and boundaries that were difficult to explain linearly to a
new reader. It is not a todo list and does not replace [TODO.md](TODO.md), the
sole source of framework todos.

1. Runtime.stop() and Runtime.stop_session() overlap and their names obscure the choice. Both act on session_id; the first only checks the local pool and returns bool, while the second also handles cross-process RunStore stop intent and returns run_id or None. The public surface should converge or mark one as a lower-level compatibility API.
2. WireCodec.delivery_to_sse() adds cursor to live deliveries but not replay, while replay may be coalesced and live events are raw. A replay-complete position or a uniform cursor carrier would make the client contract safer.
3. RunStore combines required methods with many optional capabilities and uses getattr capability discovery. Splitting base logging from optional stop, recovery, and dispatch protocols would expose deployment requirements and improve type checking.
4. Human-in-the-loop has two near-neighbor entry points: a tool returns InteractionRequest for a durable answer, while Runner.after_execute() returns Interaction for post-execution flow. A decision table or clearer names would make the correct path easier to choose.
5. Agent is designed as runtime-independent identity, but Runtime takes one agent. Serving multiple personas requires with_overrides(agent=...) or a derived runtime; an explicit per-request selection path would better match the concept.
6. Query.answer() depends on an existing pending marker. Without one, it ends normally with LoopFinished(reason="no_pending_interaction"). HTTP products must translate that empty normal result into a stale or conflict response.
7. Compaction has general components but no short Runtime integration path. A product must assemble window discovery, summary routing, storage, and projection itself, so a core capability still has high adoption ceremony.
8. sample_product documents server, executor, and frontend roles, but only server and executor are runnable demos. The HTML cannot connect directly to server.py, so it is a seam reference rather than a copy-and-run three-process product.
9. Runtime.with_overrides() does not preserve agent, inbox, wire, and some policies by default. Overriding only a model can silently lose identity, which conflicts with the method name.
10. Runtime.sse_stream() accepts a live Run. A local pool can find one by run_id, but there is no public RunStore facade to hydrate a read-only replay run for cross-worker or completed-run reconnection.
11. A RunStore recovery must treat creation with an existing run_id as idempotent because Run construction calls create_run(). That requirement is implicit rather than encoded in a method name or type contract.
