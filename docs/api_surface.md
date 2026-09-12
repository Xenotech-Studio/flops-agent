# API surface audit (2026-09-12)

Source data: every `flops_agent` import under `backend/` in the product repo
(`/home/ubuntu/flowseries/Flops`), excluding `backend/tests/` (already migrated
in step ①). 72 import statements, ~50 distinct symbols across 15 modules.

Classification legend:

- **PUBLIC** — belongs in the supported contract. Either already exported (top
  level `__init__.py` or a submodule's own `__all__`), or exported as part of
  this pass.
- **STYLE** — the symbol is already public; the product import reaches into an
  internal submodule path instead of the shorter public one. No kernel change;
  product-side import rewrite deferred to phase 2.
- **PRODUCT** — Flops-specific, or already earmarked by `docs/TODO.md` for
  removal from the framework. Not touched in this pass; carried into step ③.

## Facade / engine

| Symbol | Classification | Rationale | Action |
|---|---|---|---|
| `Run`, `RunStatus`, `Delivery`, `LogWrite` (`engine.execution`) | PUBLIC (stable) | Already in top-level `__init__.__all__`. | none — product imports these via `engine.execution` in several files (STYLE, see below) |
| `Interaction`, `StepPlan`, `ToolAction`, `ToolGate`, `TurnDecision`, `InteractionRequest` (`engine.interaction`) | PUBLIC (stable) | Already in top-level `__init__.__all__`. | none |
| `Runner` (`engine.runner`), `Runtime` (`engine.runtime`) | PUBLIC (stable) | Already in top-level `__init__.__all__`. | none |
| `LLMStreamRetryPolicy` (`engine.runtime`) | **PUBLIC — gap, exported now** | Configuration dataclass a third-party provider integrator needs to tune mid-stream retry; currently missing from both `engine/runtime.py`'s own `__all__` (`["Runtime"]` only) and the top-level facade. This is the exact example the task brief called out. | Added to `engine/runtime.py.__all__` and re-exported from top-level `__init__.py` (tag: stable). |
| `StreamAccumulator` (`engine.stream`) | **PUBLIC — gap, exported now** | Companion to `SampleDeepseekClient`; anyone wiring a custom `LLMStreamClient` needs it to accumulate deltas. Already has its own `__all__` in `engine/stream.py` but was never promoted past the submodule. | Re-exported from top-level `__init__.py` (tag: stable). |
| `compaction` (submodule, `engine.compaction`) | PUBLIC (stable, submodule-level contract) | `engine/compaction.py` has a full `__all__`; `engine/__init__.py` intentionally re-exports nothing (facade cherry-picks from submodules directly instead). Importing the submodule itself is the correct pattern. | none |

## Entities

| Symbol | Classification | Rationale | Action |
|---|---|---|---|
| `Session` (`entities.session`), `Query`, `Contributor` (`entities.query`), `Agent` (`entities.agent`) | PUBLIC (stable) | Already in top-level `__init__.__all__`. | none — product imports these via the submodule path in several files (STYLE) |
| `events` (submodule, `entities.events`) | PUBLIC (stable, submodule-level contract) | Individual event classes are already top-level exports; importing the `events` module as a namespace (`from flops_agent.entities import events as ev`) is a normal, supported pattern for exhaustive `isinstance` checks. | none |
| `Session.id_field = "_msg_id"` (default value) | **PRODUCT** | Class attribute is documented as overridable ("products may override it for another message shape"), but the *default* bakes in Flops's own message-id field name. Not a defect by itself (it's a default, not a hardcode), but flagged because the brief named it explicitly. | Listed for step ③; no change now. Already generically tracked by `docs/TODO.md` item 8 ("Remove product-shaped assumptions from Session"). |
| `Session.is_companion()` / `Session.is_user_turn()` hardcoding the `"isMeta"` key | **PRODUCT** | Unlike `id_field`, `"isMeta"` is a string literal inside the method bodies, not a class attribute — a product with a different meta-message convention cannot override it without re-implementing both methods. This is the concrete instance behind the brief's `isMeta` example. | Listed for step ③: turn into a class attribute (e.g. `is_meta_field`) alongside `id_field`. Tracked by `docs/TODO.md` item 8. |
| `"isMeta"` terminology leaking into `tools/on_executor.py` tool-description prompt text (3 sites) | **PRODUCT** | The bundled on-executor tool descriptions (surfaced to the LLM) reference "an isMeta message" by name — framework-bundled tool copy should not assume a product's internal field name. | Listed for step ③; low priority (prompt copy, not code contract). |

## Seams

| Symbol | Classification | Rationale | Action |
|---|---|---|---|
| `sync_session`, `InMemoryDatabase` (`seams.database`) | PUBLIC (stable) | Already top-level. | none — imported via `seams.database` directly in some files (STYLE) |
| `ToolContext` (`seams.executor`, re-exported from `tools.registry`) | PUBLIC (stable) | Same class object re-exported for the executor seam's convenience; already top-level. Not a duplicate type. | none |
| `CompactionRecord` (`seams.compaction_store`) | PUBLIC (stable, submodule-level contract) | Has its own `__all__`; more specialized than the "quick start" facade, deliberately not promoted to the top-level flat namespace (same tier as `RunStore`/`InMemoryRunStore`, which *are* top-level — inconsistency is minor and left as-is; promoting it would be a reasonable future addition but nothing depends on it beyond current style). | none |
| `ExecutorLink` (`executor`) | PUBLIC (stable) | Already top-level. | none |

## Tools

| Symbol | Classification | Rationale | Action |
|---|---|---|---|
| `ToolRouter`, `kwargs_adapter`, `TOOL_REGISTRY`, `register_tool`, `get_tools_for_domain`, `get_tool_handler`, `register_unified_handler`, `get_unified_handler`, `get_available_domains`, `get_tool_catalog_json`, `get_opened_package_prompts`, `dispatch_tool`, `ToolContext`, `ROUTE_EXECUTOR`, `register_on_executor_package` | PUBLIC (stable) | Already exported from `tools/__init__.__all__` (and most also from the top-level facade). | none — product reaches into `tools.registry` / `tools.dispatch` / `tools.on_executor` directly in places (STYLE) |
| `DEFAULT_REGISTRY` (`tools.registry`) | **PUBLIC — gap, exported now** | The single shared registry instance; used constantly by product (`agent/tools/__init__.py`, `agent/tools/tool_routing.py`, `agent/tools/chat_toolset.py`) and already top-level, but missing from `tools/__init__.__all__` itself — inconsistent with every other registry symbol. | Added to `tools/__init__.py`. |
| `DOMAIN_INFO`, `PACKAGE_SYSTEM_PROMPTS` (`tools.registry`) | **PUBLIC — gap, exported now** | Generic mechanism: empty dicts at import time, filled by whichever product registers packages (`DEFAULT_REGISTRY.packages` / `.package_prompts`). No Flops copy lives in the kernel; the docstring already describes this as framework machinery. Used by `agent/tools/__init__.py::init_tool_registry`. | Added to `tools/__init__.py`. |
| `register_package` (`tools.registry`, module-level convenience wrapper) | **PUBLIC — gap, exported now** | Same "delegate to `DEFAULT_REGISTRY`" pattern as `register_tool`/`get_tools_for_domain`/etc., all of which are already exported; this one was the sole omission. Currently only called by the kernel's own `tools/on_executor.py` and tests, but it's part of the same contract. | Added to `tools/__init__.py`. |
| `register_navigation_tools` (`tools.navigation`), `register_ask_user_question` (`tools.ask_user`) | PUBLIC (stable) | Already top-level. | none — product imports via submodule path (STYLE) |
| `tool_call_to_openai`, `parse_tool_arguments` (`tools.schema`) | **PUBLIC — gap, exported now** | `tools/schema.py` already has a full `__all__` (module-level contract exists), but neither name is re-exported from `tools/__init__.py`, unlike every other tools symbol product uses. | Added to `tools/__init__.py`. |

## Safety

| Symbol | Classification | Rationale | Action |
|---|---|---|---|
| `Verdict`, `fallback_decision`, `scan` (`safety`) | PUBLIC (stable) | Already top-level. | none — product imports via `flops_agent.safety` directly (STYLE, arguably fine since `safety` is itself the subpackage name, not a deeper internal path) |

## Crypto

| Symbol | Classification | Rationale | Action |
|---|---|---|---|
| `aes_gcm_encrypt`, `aes_gcm_decrypt` (`crypto.aes`) | PUBLIC (stable) | Already in `crypto/__init__.__all__`. | none — product imports via `crypto.aes` directly in 6 files (STYLE) |
| `decrypt_with_transport_priv`, `TransportError`, `public_key_pem` (`crypto.transport`) | PUBLIC (stable) | Already in `crypto/__init__.__all__`. | none — product imports via `crypto.transport` directly in 4 files (STYLE) |
| `decrypt_message_for_use`, `encrypt_message_for_storage`, `merge_preexisting_ciphertext` (`crypto.message_crypto`) | **PUBLIC — gap, exported now** | Stateless, explicit-key-parameter functions (`k_conv` passed in, no contextvar reliance) — this is the "keys as an explicit parameter" shape `docs/TODO.md` item 3 wants more of, not less. Already has its own `__all__`; simply never promoted to `crypto/__init__.py` despite `crypto/__init__.py`'s docstring describing this exact category of primitive. Used in 5 production files (`store.py`, `hybrid_store.py`, `task_events.py`, `fork.py`, `test_resume_titles_access_suspend.py`). | Added to `crypto/__init__.py`. |
| `get_active_kconv`, `set_active_kconv`, `clear_active_kconv`, `get_active_kagent`, `set_active_kagent`, `clear_active_kagent` (`crypto.crypto_context`) | **PRODUCT (do not export)** | Contextvar-based "ambient active key" plumbing tied to Flops's specific request-scoping and hot-reload-recovery model. `docs/TODO.md` item 3 already reads: *"Make keys the only key path. Move deployment-specific crypto context, key stash, and SSE crypto out of the framework."* Exporting this now would move in the opposite direction from the framework's own stated target design, even though it is the single heaviest-used crypto symbol group in product code today (17 call sites). | **Not exported.** Flagged for step ③: redesign as explicit key-passing (per TODO item 3) and relocate to the product repo; until then it stays an internal, unexported path that the product happens to import directly. |
| `keystash` (submodule, `crypto.keystash`) | **PRODUCT (do not export)** | Linux user-keyring hot-reload survival mechanism — inherently tied to Flops's `uvicorn --reload` deployment shape. Explicitly named in `docs/TODO.md` item 3 ("key stash... out of the framework"). Has its own `__all__` (`put`/`take`/`peek`/`evict`/`available`) but is not re-exported from `crypto/__init__.py`, and should not be. | **Not exported.** Flagged for step ③ removal/relocation, already tracked by TODO item 3. |
| `crypto.sse_crypto` (module; `_wrap_sse_chunk_with_kconv` etc.) | **PRODUCT (do not export)** | Depends on `crypto_context.get_active_kconv`; currently has **zero** production call sites in `backend/` (grepped, none found) — looks like unused/aspirational code carried over from the mirror. Explicitly named in the same TODO item 3 ("SSE crypto... out of the framework"). | **Not exported, not otherwise touched.** Flagged for step ③: either finish wiring it into the product and then decide, or delete it as dead weight — out of scope for this pass (read-only). |
| `transport` (submodule, `crypto.transport`) | PUBLIC (stable, submodule-level contract) | Used as a namespace import (`from flops_agent.crypto import transport`) in one file; all its individual names are already exported. | none |

## Top-level facade access pattern

| Symbol | Classification | Rationale | Action |
|---|---|---|---|
| `Interaction`, `InteractionRequest`, `Runner`, `StepPlan` imported both from `flops_agent` (top level, correct) **and** from `flops_agent.engine.interaction` in the same file (`agent/conversation_system/flops_runner.py`) | STYLE | Same file uses both the public facade and a redundant submodule import for overlapping names. No kernel change needed — the facade already covers all of them. | Deferred to phase 2: consolidate into a single top-level import in that file. |

## Summary counts

- Distinct production symbols inventoried: 51
- (a) PUBLIC, already correctly exported, no kernel change needed: 34
- (a) PUBLIC, gap found and fixed in this pass: 10
  (`LLMStreamRetryPolicy`, `StreamAccumulator`, `DEFAULT_REGISTRY`, `DOMAIN_INFO`,
  `PACKAGE_SYSTEM_PROMPTS`, `register_package`, `tool_call_to_openai`,
  `parse_tool_arguments`, plus `decrypt_message_for_use` and
  `encrypt_message_for_storage`/`merge_preexisting_ciphertext` grouped as one
  `crypto.message_crypto` gap)
- (b) STYLE — product-side import path should be tightened in phase 2 (no kernel change): the majority of the 72 call sites; concentrated in `crypto.aes`/`crypto.transport` (10 sites), `entities.session`/`entities.query`/`entities.agent` (6 sites), `engine.execution`/`engine.interaction`/`engine.runner`/`engine.runtime` (9 sites), and `tools.registry`/`tools.dispatch`/`tools.schema`/`tools.on_executor`/`tools.navigation`/`tools.ask_user` (10 sites).
- (c) PRODUCT / flag for step ③: 3 groups — `crypto.crypto_context` (6 symbols,
  17 call sites), `crypto.keystash` (1 submodule, several call sites),
  `crypto.sse_crypto` (unused, 0 call sites) — all three already tracked by
  `docs/TODO.md` item 3; plus `Session.id_field`/`is_companion`/`is_user_turn`
  hardcoding `_msg_id`/`isMeta` (tracked by TODO item 8), plus `isMeta` prompt
  copy in `tools/on_executor.py`.

No new (c) findings outside what `docs/TODO.md` already tracks — this audit
corroborates items 3 and 8 with the concrete product call-site evidence rather
than surfacing anything unknown.
