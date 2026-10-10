# Diagnose failures and check limits

**Reader question:** When something fails or behaves unexpectedly, which layer should I check first?

**Prerequisites:** You are integrating a flow from Learn. **Outcome:** Locate the responsible boundary from observable symptoms instead of blindly recreating Runtime or repeating tasks.

## Execution and connections

| Symptom | Check first | Next action |
|---|---|---|
| start raises SessionRunActiveError | Is there already a local active Run for this session_id? | Wait, stop, or queue input; do not overwrite the conversation with parallel runs |
| Work continues after the browser closes | Did you only close the subscription? | This is expected; call stop_session explicitly to stop work |
| runs.get(run_id) returns None | Is this a different process, or has the run finished? | Use product routing and persistent replay, not another start for the same user task |
| A run reaches failed | Run.error, the Error event, and product logs | Check model, tool, and storage boundaries; do not retry side effects unconditionally |
| Stop does not end a tool immediately | Does the executor propagate stop intent? | Stopping is cooperative, not forced termination of arbitrary external processes |

Code evidence: `src/flops_agent/engine/runtime.py:1041`; `src/flops_agent/engine/execution.py:714`, `:693`; `src/flops_agent/engine/runner.py:189`.

RunPool uses session_id as its local lookup key. Identical session ids from different users can therefore conflict. Use collision-free conversation ids and perform authorization independently. Cross-process exclusion is not a guarantee of the local pool.

## Replay and input

Live output and replay need not have a one-to-one relationship. Save the server's cursor; do not deduplicate or infer positions by counting frames. Default replay frames do not receive a new cursor, so the product protocol must define how to save position when a client receives only replay.

`Runtime.deliver(..., when="step")` only enqueues input; it does not start a task. Without an active Run, input waits for a later run. If a custom Inbox has no push method, use its backend enqueue API. Answer a suspended interaction with Query.answer; an ordinary additional message is not a substitute for the pending tool result.

Code evidence: `src/flops_agent/wire.py:170`; `src/flops_agent/engine/runtime.py:876`; `src/flops_agent/engine/runner.py:844`.

## Storage and recovery

In-memory data disappearing at exit is expected, not a problem recover can fix. Confirm persistent backends and recovery capabilities before inspecting the number scheduled and each task's final state. A positive schedule count can still include failed callbacks.

When reusing run_id, create_run must retain the original log. Business-tool idempotency requires the executor to correlate the original task. Recovery orchestration does not promise cross-system exactly-once execution or elect a recovery leader across instances.

If history editing raises TruncationNeedsConsent, obtain consent to discard later content first. Do not default to consent=True just to bypass the exception. An unknown message id can raise MessageNotFound; reload history and verify the selected message.

Code evidence: `src/flops_agent/seams/run_store.py:25`; `src/flops_agent/engine/runtime.py:1154`; `src/flops_agent/entities/session.py:100`, `:138`.

## Configuration and product responsibilities

If the model returns complete text, incompatible chunks, or no tool-call support, inspect the provider adapter first. Automatic function schemas infer only string parameters; complex arguments need explicit schemas and business validation. Context compaction requires product integration, not an automatic Runtime switch.

Identity authentication, HTTP routes, user authorization, real tool permissions, persistent backends, and credential acquisition belong to the product. owner_id, run_id, and cursor are not authorization proofs on their own. The example's static safety check is not a complete security policy.

Code evidence: `src/flops_agent/providers/openai.py:116`; `src/flops_agent/tools/schema.py:42`; `src/flops_agent/engine/runner.py:1040`.

## Next steps

Once you locate the problem, check parameters in the [API contract](api_surface.md). Add a reproducible integration scenario using [the product checklist](08-worked-example.md).
