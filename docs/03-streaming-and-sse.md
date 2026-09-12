# 3. Streaming and SSE: Connections May End; Execution Does Not

Browsers want typewriter output, models emit chunks, and tools may stream for a long time. Forwarding a model stream directly to HTTP looks simple until a client disconnects: who continues consuming the model, and where does a reconnecting client find missed output?

The framework first turns work into typed events, then gives those events to a Run. Live subscribers receive detailed events while the run retains a replayable log. That is why execution and transport are separate.

## Events: handle meaning before bytes

Common events include TextDelta, ReasoningDelta, ToolCallStarted, ToolResult, LoopFinished, Cancelled, and Error. Product code need not infer state from unstable text:

    from flops_agent import TextDelta, ToolResult

    async for delivery in run.subscribe():
        event = delivery.event
        if isinstance(event, TextDelta):
            render_text(event.text)
        elif isinstance(event, ToolResult):
            render_tool_result(event.name, event.result)

For a product-only notification, emit a ProductEvent(kind, payload). The framework preserves its position in the stream without making product semantics part of the general event model.

## Standard SSE: the endpoint is a small bridge

Runtime supplies a production-ready WireCodec. It maps framework events to a stable snake_case type, encodes JSON as SSE, and is exposed through runtime.sse_stream():

    async def sse_response(run, from_cursor: int = 0):
        async for line in runtime.sse_stream(run, from_cursor=from_cursor):
            yield line  # data: {"type":"text_delta","text":"Hello","cursor":1}

Live frames include a server-assigned cursor. The client must save the last received cursor and return it after a disconnection:

    async for line in runtime.sse_stream(run, from_cursor=last_cursor):
        yield line

Do not calculate a cursor by counting frames. Live output is raw events while the replay log may be coalesced; they need not have a one-to-one relationship. A cursor belongs to the log index space, which only the server knows.

A cursor is neither a run_id nor an HTTP route. The product endpoint must first authorize access to the run, then hand the resolved Run to the framework. A single-worker service can use the public run pool as a minimal router:

    async def reconnect(run_id: str, last_cursor: int):
        run = runtime.runs.get(run_id)
        if run is None:
            raise LookupError("The run is not in this process; use product routing or shared storage.")
        async for line in runtime.sse_stream(run, from_cursor=last_cursor):
            yield line

This describes object wiring, not authorization or a complete multi-worker solution. The framework does not yet expose a facade that hydrates a read-only Run from RunStore by run_id; multi-worker products must route to the worker owning the live run or solve replay in their own routing layer.

## Replay and live delivery

run.subscribe(from_cursor=0) replays the log first and then follows live output. Delivery.replayed is True for the recovered segment, which may already be coalesced; live deliveries are False and keep raw granularity. Render both, but do not infer deduplication from their event counts.

One current constraint matters: the default WireCodec injects cursor into live JSON frames only. Replay frames retain their original wire shape. If a frontend must persist a cursor after receiving only replay and an immediate close, use a product codec that includes a replay cursor or emit a product position frame. Never guess which cursor a final replay frame represents.

PassthroughCoalescer is the default, so small applications work without further configuration. For long tool output, provide a custom Coalescer implementing feed(event) and flush(). The framework writes its output to the log and flushes it when the run ends.

## Change the wire at the codec boundary

Most products should keep the standard wire and use ProductEvent for custom frames. To change a framework frame shape, subclass WireCodec:

    from flops_agent import Runtime, WireCodec

    class MyCodec(WireCodec):
        def event_to_wire(self, event):
            wire = super().event_to_wire(event)
            if wire and wire["type"] == "text_delta":
                wire["type"] = "delta"
            return wire

    runtime = Runtime(llm=my_llm, wire=MyCodec())

You may replace the protocol entirely by injecting an object with event_to_wire, to_sse, and delivery_to_sse. Product events that are already SSE strings pass through unchanged; if they also need a cursor, the product serializer must add it.

Next: [Sessions and persistence](04-sessions-and-persistence.md).
