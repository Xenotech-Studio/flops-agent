# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Optional database `synchronize_session` capability for atomic, identity-aware
  reconciliation when independent writers append to a live session. Existing
  database implementations retain their count-based synchronization behavior.

- Optional `prepare_message` for `stable_compaction.project`, `plan` and
  `choose_recent_boundary`. Callers may pass stored rows (for example still
  encrypted); only the retained tail and the older rows a rule actually reads
  (reinjected tool results, the calls naming them, tool names the window cannot
  resolve) are prepared, with results byte-identical to preparing everything.
  Identity-chain (v3) generations only; content-digest generations prepare all
  rows. `ProjectionConfig.expand_canonical_to_llm_messages` accepts a precomputed
  `tool_call_names` map.
- Identity-chain history verification for stable compaction generations
  (`history_digest_version` 3). With `identity_field`, `initial_state` and every
  later generation hash only the persistence identity each message carries, never
  its content; `adopt_identity` re-keys a content-verified v2 generation without
  changing its decisions. Sound only when the persistence layer issues a new
  identity whenever a message's model-visible content changes. A summarized prefix
  containing unidentified (uncommitted) turns is refused with
  `history_identity_incomplete`. v2 content generations are unchanged.
- Typed boundary contracts for provider stream chunks, tool calls and outcomes,
  dispatch records, and run metadata.
- Interaction events in the public agent-event union and contract tests for the
  supported import surface.

### Changed

- Normalized provider chunks at the framework boundary and tightened runtime
  contracts for embedding applications.
- Clarified neutral Session and tool-package extension points.

### Removed

- **Breaking:** the OpenAI/LiteLLM duck-shape fallback in `StreamAccumulator`.
  `LLMStreamClient.acompletion` must now yield `StreamChunk` values; a provider
  SDK object raises `TypeError` instead of being silently adapted. Translate
  wire formats inside the client, as `OpenAIStreamClient` does.

## [0.2.0] - 2026-09-14

### Added

- An independently installable service-agent kernel with run lifecycle,
  streaming SSE, cancellation, suspension, persistence, recovery, compaction,
  and tool dispatch primitives.
- Explicit `TransportKey` value-object injection for transport cryptography.
- A `src/` package layout, typed public facade, runnable sample product, and
  tutorial documentation for framework users.

### Changed

- Removed deployment-specific assumptions so the kernel accepts explicit values
  and application-provided seams instead of reading host configuration.
- Corrected lifecycle, stream-retry, compaction, crypto, and persistence edge
  cases discovered while stabilizing the public API.

[Unreleased]: https://github.com/Xenotech-Studio/flops-agent/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Xenotech-Studio/flops-agent/releases/tag/v0.2.0
## Continue reading

You have reached the end of this reading path. After checking the released version, return to [Learn](README.md) to work through the complete flow, or to [Reference](reference.md) to look up interfaces and limits.
