# Control model input in long conversations

**Reader question:** How can I reduce growing model input without deleting the user's factual record?

**Prerequisites:** Understand [conversation persistence](04-sessions-and-persistence.md). **Outcome:** Distinguish temporary projections from stored summaries and choose a verifiable integration path for your product.

## Choose a strategy first

| Need | Approach | Does it change conversation history? |
|---|---|---|
| Reduce old tool output or keep only needed fields | Read-time projection | It should not |
| Replace a long span of input with a summary | Summary generation, storage, and projection together | Keep original history and store summaries separately |
| Honor an explicit deletion or rewind request | Session editing interfaces | Yes; handle consent and persistence |

The default `Runner.project_messages()` returns history itself. It runs whenever a model request is assembled; it is not an automatic background compaction service.

## Try it: make projection reproducible

Override project_messages in a Runner and return a new list for this request. This is an integration sketch: your product must implement project_for_model; it is not a framework function.

```python
from copy import deepcopy
from flops_agent import Runner

class ProductRunner(Runner):
    async def project_messages(self):
        history = deepcopy(self.messages)
        return project_for_model(history)
```

A projection should produce the same result for the same input. Start by shortening text fields in old tool results, retaining the corresponding calls, result ids, roles, and ordering. Do not simply take the last N messages: that can separate an assistant tool call from its tool result. A deep copy prevents edits to nested fields from modifying Session.

## Add the storage path when you need summaries

`flops_agent.engine.compaction` provides components including ProjectionConfig and CompactionPolicy; CompactionStore stores summary records. Your product must still supply the model window size, the summarization model, the permitted write timing, and a persistent backend, then include summaries in request projection.

There is no automatic `Runtime(compaction=True)` switch. This article does not pretend to supply a complete summarization service: without your window and storage constraints, it cannot safely choose which history to cover. Define the covered interval, recent history to retain, summary version, and fallback view before connecting these components.

## Check the result

Project the same history containing tool calls twice. The results should match, and the original Session should remain unchanged field by field. Check that every retained tool result has its matching call, recent user requests remain, and the serialized input meets the target model's requirements. If summarization fails, use your product's conservative fallback view rather than deleting original history.

Character or token estimates do not guarantee provider billing or context validation. Verify that the actual request is accepted and meets your product's input budget.

## Next steps

To add a tool, continue to [the tool walkthrough](07-extending.md). To customize other runtime policy, see [choosing an extension point](customize-runner.md).

## Code evidence

- `src/flops_agent/engine/runner.py:1040`, `:1070`: projection and request construction.
- `src/flops_agent/engine/compaction.py:187`, `:1196`: projection configuration and compaction policy components.
- `src/flops_agent/seams/compaction_store.py:1`: separate summary storage; this article describes only the basic interfaces already present in v0.2.0.
