# 8. Worked Example

Every previous article refers to the same small product. Its source is in
[sample_product/](sample_product/). Read it through this page rather than guessing
from file names.

## Run it first

From the repository root:

    python docs/sample_product/server.py
    python docs/sample_product/local_executor.py

The first command uses deterministic ScriptedLLM by default, so it needs no
network or API key. With DEEPSEEK_API_KEY it also demonstrates a real model call.
The second command is a self-contained executor demonstration. frontend.html is
a minimal browser reference for consuming SSE, saving a cursor, reconnecting, and
a cancel button; it should not import the Python server.

The sample has three roles, not a complete web application bound to ports:
server.py is an in-process server demo, local_executor.py is an executor demo, and
the HTML assumes the product has implemented /chat and /cancel. A real three-process
HTTP/WebSocket deployment adds product-owned transport and authentication.

## Map the concepts to code

build_runtime() in server.py is the assembly point from Articles 1 and 2. It
chooses the LLM, InMemoryDatabase, InMemoryRunStore, tool schemas, executor, and
SampleRunner. A real product replaces deployment facts there.

handle_chat_request() has the endpoint shape from Article 3: load a Session,
translate HTTP input into Query, call runtime.start(), then iterate
runtime.sse_stream(run). The endpoint contains no agent loop; closing its
connection does not affect the run.

The demo does not implement reconnect by old run_id: handle_chat_request() always
starts a new run, while frontend.html only shows how a browser saves live cursors.
That is deliberate. Adding reconnection requires run authorization, finding a live
run in the right worker or shared routing, and the replay cursor strategy from
Article 3.

SampleRunner.before_tool() is the Article 7 policy extension. It rejects dangerous
commands before ToolExecuting. The rejection returns to the model as a tool result;
it is not an after-the-fact executor check.

ExecutorAdapter is the server-side ToolExecutor seam. To keep one file runnable it
simulates execution and emits output through ctx.stream_sink. Its comments identify
the only location to replace with WebSocket forwarding. local_executor.py shows the
run_tool, tool_delta, and cancel_tool messages an executor should understand, but
does not open a real socket to server.py.

NotebookMemory and Agent(...) demonstrate identity and memory: reads become system
prompt input; writes are scheduled after completion.

on_startup() intentionally keeps an empty resume hook. Key acquisition and the
entry point used to restart a run are deployment facts; follow Article 6 to supply
your own implementation.

## Use it as a starting point

Copy the assembly shape in build_runtime() and the endpoint shape in
handle_chat_request(), then replace one concern at a time: memory database with
your Database, memory RunStore with a shared RunStore, inline executor with a
remote ToolExecutor, and ScriptedLLM with your LLMStreamClient. Preserve the
start-to-subscribe/SSE lifecycle as each replacement is made.

Do not copy InMemoryDatabase, InMemoryRunStore, or the small SafetyInspector into
production unchanged. They prove protocol semantics, run lifecycle, and the gate
location; production storage, authorization, audit rules, and HTTP/WebSocket
transport remain product responsibilities.

tests/test_sample_product.py is the regression test that keeps the example aligned
with the public API.

Back to the [documentation index](README.md).
