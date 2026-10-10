# Integrate the example into your product

**Reader question:** The example runs. What is still missing before I can connect a browser to my product?

**Prerequisites:** Complete [streaming subscriptions](03-streaming-and-sse.md), [conversation storage](04-sessions-and-persistence.md), and [the tool walkthrough](07-extending.md). **Outcome:** Complete product boundaries along a request path and validate observable scenarios rather than just copying example files.

## Start with the working example

Run the offline command from [the five-minute guide](01-quick-start.md) again. This time, read the code with one question in mind: where does each input, tool result, and SSE frame cross a product boundary?

| Example location | What it demonstrates | What your product still implements |
|---|---|---|
| build_runtime in server.py | Assembling model, tools, storage, and Runner | Real model and persistent backend configuration |
| handle_chat_request | Loading Session, creating Query, starting Run, yielding SSE | HTTP routes, identity, and conversation access checks |
| ExecutorAdapter | Simulated incremental output | Real tool execution, authorization, and remote task correlation |
| handle_cancel | Requesting a stop by session | The product stop endpoint and ownership checks |
| on_startup | Where to call recover | Actual reconstruction inside resume |

`docs/sample_product/local_executor.py` and `frontend.html` illustrate the other two sides of the integration. Running server.py does not automatically connect all three into a network service. The sample command inspection is also only a demonstration, not a complete security guarantee.

## Try it: complete the request path

1. Assemble and retain Runtime at product startup. Use ScriptedLLM first to make the network integration reproducible, then switch to a real model.
2. Implement a submission endpoint in your Web framework: authenticate the user, obtain an authorized Session, construct Query, return run_id, and send SSE.
3. Save the server cursor in the client and reconnect by run_id. Route requests to a location that can provide the run or its persistent log.
4. Implement an explicit stop endpoint and propagate stop intent into the real executor. Do not equate disconnection with cancellation.
5. Inject Database and RunStore and verify persistence capabilities individually. Promise restart continuation only after completing [the recovery exercise](06-recovery.md).

These steps do not prescribe a Web framework or invent an existing serve API. Keep your product's HTTP layer and connect run handles to streaming responses.

## Check the result

| Scenario | What you should observe |
|---|---|
| Consecutive questions in one conversation | The second turn loads the first turn's history |
| A tool call | Incremental output is visible and the final result enters history |
| Closing the browser and reconnecting | Execution does not stop on disconnect; the protocol determines replay position |
| Clicking stop | The run eventually reaches stopped and the executor receives cancellation intent |
| Another user requests the same run | Product authorization rejects access instead of trusting run_id alone |
| A process restart | Persisted history remains; recovery success or failure has an observable terminal state |

Turn these scenarios into your product's integration tests before expanding tools, memory, or multi-process routing. One successful example run does not replace these checks.

## Next steps

Learn now covers the complete path: run, integrate, preserve state, extend, and verify. Look up methods in the [API contract](api_surface.md), diagnose behavior in [errors and limits](errors-and-limits.md), and check [released changes](CHANGELOG.md) before upgrading.

## Code evidence

- `docs/sample_product/server.py:172`: build_runtime; `:187`: request handler; `:200`: stopping; `:208`: recovery placeholder.
- `docs/sample_product/server.py:69`: a simulated executor, not a real command execution service.
- `src/flops_agent/engine/runtime.py:1041`, `:1154`: execution and recovery entry points.
