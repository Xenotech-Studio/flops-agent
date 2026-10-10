# Find the contract you need

**Reader question:** I know what I want to do. Where should I look up parameters, limits, and versions?

**Prerequisites:** None; new users should start with [Learn](README.md). **Outcome:** Find the authoritative entry point for a question without rereading the entire tutorial sequence.

## Look up a question

| Question | Where to look |
|---|---|
| How do I start, stop, subscribe to, or recover a Run? | Runtime and session operations in the [API contract](api_surface.md) |
| What must Database or RunStore implement? | Storage and extension protocols in the [API contract](api_surface.md) |
| Why does work continue after disconnection, or why is a run handle missing? | [Diagnose failures and check limits](errors-and-limits.md) |
| What do cursor, Run, and Session mean? | [The glossary](glossary.md) |
| Has a change been released? | [Changelog](CHANGELOG.md) |

Reference supports lookup rather than replacing procedural tutorials. Each Learn page has Code evidence with repository paths and line numbers. For implementation details, check the source for the relevant version.

## Version boundaries

These docs use released v0.2.0 capabilities as their baseline and cross-check the local clone's implementation. A source branch can continue to evolve. Finding a new method on that branch does not prove that your installed release contains it. Compare release notes and the installed version before upgrading.

Both the Changelog page and its Markdown export contain released version sections only.

## Entry points for agents

The [documentation index](/docs-index.json) includes each page's title, section, group, canonical_url, markdown_url, and searchable headings. Section is either learn or reference. Group and manifest order describe the learning sequence.

Each page's Raw Markdown link and Copy page Markdown control use the same build-generated text, with section and canonical metadata at the top. Source URLs look like `/docs-source/api_surface.md`; they are not redirects to GitHub. This page is available at `/docs-source/reference.md`.

Changing sections or chapters does not change existing page URLs. Return to [Learn](README.md) for a guided sequence, or go directly to [errors and limits](errors-and-limits.md) when debugging.

## Code evidence

- `src/flops_agent/__init__.py:1`: the public package facade and its boundaries.
- `src/flops_agent/engine/runtime.py:1041`: actual start parameters; the API page locates the remaining entry points individually.
