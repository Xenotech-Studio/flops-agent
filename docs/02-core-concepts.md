# 2. Core Concepts: Why Run, Session, Runner, and Runtime Exist

A program that sends a prompt to a model has input and output. A service agent has two independent lifetimes: whether work continues, and whether a particular client is watching. If both live in one generator, closing a browser stops work and a refresh has no reliable place to resume.

\`flops_agent\` separates them. A runner drives work; a run lets any number of clients observe it.

\`\`\`text
request -> Query ---+
stored history -> Session ---+-> Runner (one turn's state machine)
agent identity -> Agent ---+                              |
                                                           v
Runtime assembles the pieces at startup -> Run (events and state) -> SSE/subscribers
                                                           |
                                                  Database / RunStore
\`\`\`

## Six names to remember

\`Runtime\` is the long-lived assembly object. It knows the model client, tools, database, executor, and runner class. It does not hold a user's current input or temporary UI state.

\`Session\` is a conversation's history and metadata. It can be loaded, saved, truncated, or continued. Regeneration is not a special flag: edit the session, then continue it with an empty \`Query\`.

\`Query\` is the new contribution to this conversation. \`Query.text("...")\` is a user message; \`Query.answer(...)\` answers a suspended question; \`Query.event(...)\` advances a turn from an external event. Pass \`None\` when there is no new contribution and the existing session state determines whether to continue or regenerate.

\`Run\` is the handle for work in progress. It has an id, state, event log, and \`stop()\` method. It is not a generator waiting to be iterated. A runner advances it in a background task, while subscribers may arrive late, leave, or coexist.

\`Runner\` is a state machine created for one run. The default runner calls the LLM, assembles and executes tools, persists history, and finishes. Products subclass it only when they need a policy change.

\`Agent\` describes who the assistant is: persona, model preference, and memory. One database, model client, and executor can serve several agents. \`Event\` is the typed vocabulary through which the framework speaks to the outside world.

Their distinct lifetimes are the reason they are separate objects:

| Object | Typical lifetime | What does not belong there |
|---|---|---|
| \`Runtime\` | Process or application instance | A user's current input or UI state |
| \`Agent\` | Lifetime of a persona configuration | Database connections or mutable conversation history |
| \`Session\` | Persisted across requests and processes | Background tasks or HTTP connections |
| \`Query\` | One entry call | Existing history or a reconnection cursor |
| \`Run\` / \`Runner\` | One execution | Long-term truth for the next turn |

A \`Runtime\` accepts one \`agent\` at construction. If a product selects a persona per request, it must choose the agent first and explicitly derive a runtime with \`runtime.with_overrides(agent=chosen_agent)\`, or assemble a runtime for that persona. \`with_overrides()\` preserves every other Runtime configuration slot, including persistence, inbox, wire, retry, and lifecycle-hook settings. Do not put the current agent in a global variable or a temporary session field.

## The smallest service lifecycle

\`\`\`python
session = await runtime.load_session("conv-42", owner_id="u-7")
run = runtime.start(session, Query.text("Look up the weather in Shanghai."))

# The HTTP/SSE layer observes; it does not drive execution.
async for delivery in run.subscribe():
    send_to_client(delivery)
\`\`\`

\`start()\` creates a background task and returns immediately. The runner completes model and tool work even with no subscriber. If this subscriber disconnects, another may attach to the same \`Run\`.

Only one local Run may be active for a session. Starting another raises
\`SessionRunActiveError\`, whose \`run_id\` identifies the existing Run; products
that need per-session queuing should catch it and schedule their own retry.

\`Query\` expresses only what is new:

\`\`\`python
runtime.start(session, Query.text("A new question"))
runtime.start(session, Query.answer({"approved": True}))
runtime.start(session, Query.event({"kind": "job_finished"}))
runtime.start(session)  # Continue or regenerate from existing history.
\`\`\`

The final call is intentional. To regenerate, first change \`Session\` (for example, remove the previous answer), then start with no new query. The framework derives placement from history rather than a \`regenerate=True\` flag that could drift from the real state.

## How one run advances

The default runner roughly does four things per step:

1. Build an LLM request from the session, query, agent, and visible tools.
2. Consume model chunks and immediately emit \`TextDelta\`, tool-call deltas, and related events.
3. Persist complete text or tool calls; if tools are requested, execute them and persist their results.
4. Emit \`LoopFinished\` when no more work remains, otherwise let the model inspect tool results in another step.

A model call is therefore one step, not necessarily one run. A run can contain multiple model steps and multiple tool calls.

## Who owns each layer

The framework owns general lifecycle mechanics: background execution and subscriptions, events, session writes, cancellation checkpoints, suspension and resume orchestration, reconnect logs, and standard SSE. Products provide deployment facts: a model vendor, database, remote executor, authentication, HTTP routes, tool copy, and safety policy.

A useful test is: would a third party building an independent agent still need a browser disconnect not to stop a long-running tool? That belongs in the framework. Membership permissions belong in the product.

Next: [Streaming and SSE](03-streaming-and-sse.md).
