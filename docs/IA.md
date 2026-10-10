# Documentation information architecture: from first run to independent lookup

## Design principles and publication boundaries

This repository-only document records the design and verification plan. It is not in the website publication manifest. The design was completed before the content and navigation implementation.

Readers first need to decide whether the framework fits, then achieve a reproducible success, build a mental model, and extend by product task. Learn teaches tasks; Reference provides exact contracts and boundaries. Each page states its reader question, prerequisites, and outcome, with verification and a next step. Beginners do not need to read the entire API first.

The top-level areas are **Learn / Reference**. The first Learn chapter is **The basics**; the remaining chapters and task titles are also English. The public OSS repository and website use English throughout. Code identifiers keep their original names. Stable section labels such as Try it, Check the result, Boundaries, Next steps, and Code evidence support both readers and agents.

Public capability claims use `v0.2.0` as the baseline and are checked against the current implementation. Changes on the current branch relative to that tag include new model-call hooks in runner and additional compaction behavior. These increments are not described as released capabilities. The build continues to strip CHANGELOG's Unreleased section. TODO, DESIGN_NOTES_FROM_DOCS, this file, and other internal plans stay outside the manifest.

## Information architecture and page promises

Prerequisites describe a suggested reading order, not access restrictions. All URLs remain flat.

| Area / chapter | File / URL | Task-oriented title | Problem solved | Audience | Prerequisites | Outcome |
|---|---|---|---|---|---|---|
| Learn / The basics | README.md / /docs | Decide whether the framework fits your task | Clarify framework and product responsibilities | New readers | None | Choose a learning path and recognize example boundaries |
| Same chapter | 01-quick-start.md / /docs/01-quick-start | Run a task in five minutes | Obtain observable output from an offline script | Python beginners to the framework | Introduction (/docs) | Install and run the example; identify tool and memory output |
| Same chapter | 02-core-concepts.md / /docs/02-core-concepts | Understand how a task runs | Derive four minimal concepts from a successful run | Readers who ran the example | 01 | Distinguish Runtime, Session, Query, and Run; start another turn |
| Learn / Build a service | connect-model.md / /docs/connect-model | Connect your own model | Replace the deterministic double with a real client | Integration developers | 02 | Configure a compatible endpoint and check its first output |
| Same chapter | 03-streaming-and-sse.md / /docs/03-streaming-and-sse | Keep receiving results after a disconnect | Separate task lifetime from network lifetime | Service and frontend developers | 02 | Reconnect using run_id and server cursor; recognize replay limits |
| Same chapter | 05-cancellation-and-suspension.md / /docs/05-cancellation-and-suspension | Stop work and accept user input | Choose stop, answer, or queue according to intent | Interaction developers | 03 | Stop work, answer suspension, and enqueue input with the correct parameter |
| Learn / Save and recover | 04-sessions-and-persistence.md / /docs/04-sessions-and-persistence | Save a conversation and continue it | Separate conversation storage from run logs | Multi-turn application developers | 02 | Reload the same conversation and select storage boundaries |
| Same chapter | 06-recovery.md / /docs/06-recovery | Recover tasks after a process restart | Separate recording, reconstruction, and idempotency checks | Developers with persistent storage | 04, 05 | Implement resume and design an interruption exercise |
| Same chapter | context-window.md / /docs/context-window | Control model input in long conversations | Distinguish projection from stored summaries | Developers facing context limits | 04 | Choose trimming or summarization without destroying history |
| Learn / Extend and integrate | 07-extending.md / /docs/07-extending | Give your agent a tool | Complete the shortest tool integration loop | Developers who can start Runtime | 02 | Register a function and verify its result enters later model input |
| Same chapter | customize-runner.md / /docs/customize-runner | Put product policy at the right extension point | Choose Runner, Memory, or Executor by need | Product customization developers | 07, 05 | Add a gate without copying the execution loop |
| Same chapter | 08-worked-example.md / /docs/08-worked-example | Integrate the example into your product | Complete real service boundaries around the sample | Readers with basic and tool integration | 03, 04, 07 | List integration work and validate product scenarios |
| Reference / Start here | reference.md / /docs/reference | Find the contract you need | Route lookup questions to the right reference | Humans and agents | None; beginners start with Learn | Locate methods, limits, terms, or versions |
| Reference / Contracts and limits | api_surface.md / /docs/api_surface | API contract | Specify stable entry points, argument semantics, and protocols | Adapter implementers | 02 recommended; independently readable | Choose the right call and locate authoritative implementation |
| Same chapter | errors-and-limits.md / /docs/errors-and-limits | Diagnose failures and check limits | Map symptoms to checks and capability boundaries | Developers debugging an integration | Relevant Learn page | Distinguish concurrency, stops, replay, storage, and recovery failures |
| Reference / Terms and releases | glossary.md / /docs/glossary | Use consistent runtime and storage terminology | Resolve ambiguity around session, run, cursor, and related terms | All readers and agents | None | Use consistent terminology and search keywords |
| Same chapter | CHANGELOG.md / /docs/CHANGELOG | Changelog | Look up released version changes | Upgrade and compatibility reviewers | None | Confirm release scope without treating unreleased work as available |

## Mapping old articles to the new structure

| Old article | Action | Destination and reason |
|---|---|---|
| README | Rewrite | A real introduction and Learn landing page; remove the internal TODO link and the build-time substitute overview so repository and website agree |
| 01 | Rewrite and split | Keep the offline success path; move model configuration to connect-model and core objects to 02 to reduce first-step cognitive load |
| 02 | Rewrite | Explain four concepts along an execution timeline instead of opening with an entity inventory; add a complete two-turn example |
| 03 | Rewrite | Follow first connection, disconnect, and reconnect; consolidate protocol details in API and limits in the troubleshooting reference |
| 04 | Rewrite and split | Focus on saving and continuing; consolidate projection and summarization in context-window and method lists in API |
| 05 | Rewrite | Separate three user intents; correct Runtime.deliver's when parameter and clarify that a suspended run is not still running |
| 06 | Rewrite | State persistence prerequisites before resume and the exercise; identify the empty sample callback and distinguish scheduled from successful recovery |
| 07 | Split and rewrite | Keep the tool loop; move Runner, Memory, and Executor to customize-runner and merge compaction with 04's material in context-window |
| 08 | Rewrite | Upgrade a directory map into integration steps and executable acceptance scenarios; do not claim the sample includes a complete HTTP service or remote execution |
| api_surface | Rewrite | Replace a symbol inventory with task-oriented contracts; split failure boundaries into errors-and-limits and terminology into glossary |
| CHANGELOG | Retain | Preserve historical release content; publication metadata states the reader question; the build strips Unreleased and its reference definition |

No old page is abandoned or simply placed unchanged under a new heading. Repeated tutorial API inventories are consolidated in Reference, with links in both directions.

## Content gaps and new writing tasks

### connect-model: connect your own model
- Explain the difference between the offline double and real model requests, including network prerequisites.
- Provide a complete Python entry point using product-supplied environment variables, never real credentials.
- Explain the optional providers dependency and compatible endpoint format.
- Give success checks and starting points for authentication or format failures.

### context-window: control model input in long conversations
- Distinguish persisted history from the temporary model view.
- Explain project_messages and tool-call/result pairing constraints.
- Describe the model, window, storage, and write policy needed for summaries.
- State that Runtime(compaction=True) is not an automatic switch; describe only released baseline capabilities.

### customize-runner: put product policy at the right extension point
- Map requirements to Runner, Memory, and Executor.
- Provide an embeddable before_tool gate and explain denial results.
- Explain recall, asynchronous maintenance, and product storage responsibilities.
- Explain remote cancellation, dispatch records, and business idempotency boundaries.

### reference: find the contract you need
- Route questions to reference pages without duplicating tutorials.
- Describe the agent index, raw Markdown, and section metadata.
- Distinguish the release baseline, source verification, and unreleased capabilities.

### errors-and-limits: diagnose failures and check limits
- Distinguish concurrent-run conflicts, disconnected subscriptions, and stops.
- Describe replay cursors, process routing, and memory-store limits.
- Explain recovery scheduling, idempotency, authorization, and encryption responsibilities.
- Give observable symptoms, checks, and code locations for each group.

### glossary: use consistent runtime and storage terminology
- Define Runtime, Session, Query, and Run briefly without circular definitions.
- Distinguish events, Delivery, cursor, and SSE.
- Distinguish Database, RunStore, Memory, and CompactionStore.
- Link each group to tutorials or contracts.

## URLs and migration

Retain `/docs`, `/docs/01-quick-start`, `/docs/02-core-concepts`, `/docs/03-streaming-and-sse`, `/docs/04-sessions-and-persistence`, `/docs/05-cancellation-and-suspension`, `/docs/06-recovery`, `/docs/07-extending`, `/docs/08-worked-example`, `/docs/api_surface`, and `/docs/CHANGELOG`. Keep the existing `/docs/overview` alias too.

Add `/docs/connect-model`, `/docs/context-window`, `/docs/customize-runner`, `/docs/reference`, `/docs/errors-and-limits`, and `/docs/glossary`. No slug is renamed, no old URL needs a 301, and no 404 is expected. Do not introduce nested section paths: navigation ownership does not change document identity. Rewriting headings changes some fragments, so manifest aliases preserve known historical heading links. They land at the corresponding topic or a section linking to its new article.

## Implementation and self-review criteria

The manifest drives section and chapter order: docs-sections.json contains only learn/reference; array order in docs-sources.json determines chapter and page order, group names the chapter, and overview selects the tab destination. Each page exports consistent source, section, and canonical metadata. The build no longer substitutes README.

Check every page's promise, prerequisites, result, and next step. Run the offline example and new complete Python examples, and verify source evidence. Scan public content for private addresses, credential paths, deployment details, and internal plans. Verify every page, deep refresh, raw MIME type, manifest consistency, and old URL. Recheck themes, the six-size type scale, header alignment, bleed, transitions, and narrow layouts. The IA implementation evidence is in /home/ubuntu/tmp/fw-ia/; English-language verification is in /home/ubuntu/tmp/fw-en/.

## Implementation record

Completed: 10 old articles substantially rewritten, 6 added, and historical CHANGELOG content retained; the website has 17 pages. The substitute overview source was removed; the website reads docs/README.md directly. Each page or publication note states a reader question, prerequisites, and outcome. Learn pages provide checks and next steps; Reference provides lookup destinations.

The 43 pre-redesign H2/H3 fragments remain addressable. Manifest heading_aliases map them to semantic sections without new routes or layout height. Moved topics link onward. The index exports heading_aliases so agents can interpret old links. The English correction also preserves the headings introduced by the redesign as non-visible compatibility identifiers; alias labels and all reader-facing content are English.

Verified and corrected: Runtime.deliver uses when; minimal function schemas infer string parameters only; RunPool is not a long-term lookup service for finished runs; recover returns a schedule count rather than a success count; sample_product does not provide a complete ready-to-use HTTP or recovery service. The documentation preserves these narrower claims.

Each page includes source evidence. The two-turn example, tool loop, and allow/deny gate passed against v0.2.0 source. Existing sample and runtime tests passed 34 cases; Markdown and Unreleased filtering passed 6 tests. Requests using real provider credentials, product-owned persistent backends, and remote execution environments were outside that executable scope. The docs state their prerequisites rather than presenting them as validated deployments.

No page was left half-written, and no unchanged old course was passed off as a completed new page. Keeping CHANGELOG preserves historical facts. Original public checks, screenshots, and transition samples are in /home/ubuntu/tmp/fw-ia/; the English correction has its own evidence directory above.
