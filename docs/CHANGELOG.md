# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Typed boundary contracts for provider stream chunks, tool calls and outcomes,
  dispatch records, and run metadata.
- Interaction events in the public agent-event union and contract tests for the
  supported import surface.

### Changed

- Normalized provider chunks at the framework boundary and tightened runtime
  contracts for embedding applications.
- Clarified neutral Session and tool-package extension points.

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
