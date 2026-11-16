"""Generation-scoped context projections. No persistence or provider dependencies.

Canonical history is an append-only input. A generation records decisions, not
rewritten messages. Unlisted (new) tool results always receive their first-use
budget; only an explicitly committed compaction can age retained tool results.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Dict, List, Optional, cast

from typing_extensions import override

from .compaction import (
    CompactionPolicy, ContextProjection, FormatMetadata, ProjectionConfig,
    SUMMARY_SYSTEM_TAG, adjust_tail_start_for_tool_protocol,
    build_context_projection, compute_ideal_covers_exclusive_end,
    content_text_and_image_count, format_reinject_system_content,
    gather_recent_tool_results_for_reinject,
    _projected_message_weight, _projection_wire_tokens,  # pyright: ignore[reportPrivateUsage] -- shared engine accounting primitives
)
from ..seams.compaction_store import CompactionRecord

STRATEGY_VERSION = "stable-tool-generations-v1"
STATE_KEY = "context_projection_state_v1"


class ProjectionFailure(ValueError):
    """A safe, content-free reason to keep the previous generation / refuse send."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def parameters(config: ProjectionConfig, policy: CompactionPolicy) -> Dict[str, Any]:
    projection: Dict[str, Any] = {}
    for field in fields(ProjectionConfig):
        value = getattr(config, field.name)
        if not callable(value):
            projection[field.name] = sorted(cast(frozenset[str], value)) if isinstance(value, frozenset) else value
    return {"projection": projection, "compaction": asdict(policy), "renderer": STRATEGY_VERSION}


def tool_identity(index: int, message: Dict[str, Any]) -> str:
    # Do not use a DB msg_id: it may be assigned after the first request.
    return digest([index, message.get("role"), message.get("tool_call_id")])


def history_digest(messages: List[Dict[str, Any]]) -> str:
    # Exclude usage, persisted IDs and transport metadata added after a send.
    keys = ("role", "content", "tool_calls", "tool_call_id", "reasoning_content", "thinking", "reasoning")
    return digest([{key: msg[key] for key in keys if key in msg} for msg in messages])


@dataclass
class FrozenConfig(ProjectionConfig):
    tiers_by_identity: Optional[Dict[str, str]] = None

    @override
    def compute_tool_tiers(self, messages: List[Dict[str, Any]]) -> Dict[int, str]:
        frozen = self.tiers_by_identity or {}
        return {i: frozen.get(tool_identity(i, msg), "full")
                for i, msg in enumerate(messages) if msg.get("role") == "tool"}

    @override
    def apply_image_recency_policy(self, llm_msgs: List[Dict[str, Any]], keep: Optional[int] = None) -> List[Dict[str, Any]]:
        # Applied explicitly to the fixed boundary below, never a moving tail.
        return llm_msgs


def frozen_config(config: ProjectionConfig, state: Dict[str, Any]) -> FrozenConfig:
    if state.get("strategy_version") != STRATEGY_VERSION or state.get("schema_version") != 1:
        raise ProjectionFailure("unsupported_context_strategy")
    saved = cast(Dict[str, Any], state["parameters"])["projection"]
    kwargs = {field.name: getattr(config, field.name) for field in fields(ProjectionConfig)}
    kwargs.update(saved)
    kwargs["tool_soft_trim_exempt_names"] = frozenset(kwargs["tool_soft_trim_exempt_names"])
    return FrozenConfig(**kwargs, tiers_by_identity=state["tool_tiers"])


def trace(state: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("strategy_version", "schema_version", "parameters_sha256", "generation", "projection_id",
            "reason", "active_summary_id", "tail_start", "boundary_count", "target_tokens", "committed_tokens")
    return {key: state[key] for key in keys if key in state}


def initial_state(config: ProjectionConfig, policy: CompactionPolicy, messages: List[Dict[str, Any]],
                  active: Optional[CompactionRecord], *, format_metadata: FormatMetadata,
                  window_usable: int = 0, fixed_overhead_tokens: int = 0) -> Dict[str, Any]:
    # Adoption is an explicit boundary, not a claim that legacy prefixes were stable.
    legacy = len(messages) > 1 or active is not None
    selected = build_context_projection(config, messages, active, format_metadata=format_metadata,
        min_tail_tokens=policy.verbatim_min_tail_tokens(window_usable),
        min_tail_messages=policy.verbatim_min_tail_messages,
        max_total_tokens=policy.verbatim_hard_max_tokens(window_usable),
        fixed_overhead_tokens=fixed_overhead_tokens)
    params = parameters(config, policy)
    tiers = config.compute_tool_tiers(messages) if legacy else {}
    state: Dict[str, Any] = {
        "schema_version": 1, "strategy_version": STRATEGY_VERSION,
        "generation": 0, "projection_id": uuid.uuid4().hex,
        "reason": "legacy_adoption" if legacy else "new_session",
        "parameters": params, "parameters_sha256": digest(params),
        "active_summary_id": active.id if active else None,
        "tail_start": selected.tail_start, "boundary_count": len(messages),
        "boundary_history_sha256": history_digest(messages[selected.tail_start:]),
        "tool_tiers": {tool_identity(i, messages[i]): tier for i, tier in tiers.items() if i >= selected.tail_start},
        # No duplicate restoration of outputs already present in the selected tail.
        "reinject_indices": [],
    }
    return state


@dataclass
class StableProjection(ContextProjection):
    policy_trace: Optional[Dict[str, Any]] = None


def validate_history(state: Dict[str, Any], messages: List[Dict[str, Any]]) -> None:
    count = int(state["boundary_count"])
    if len(messages) < count or history_digest(messages[int(state["tail_start"]):count]) != state["boundary_history_sha256"]:
        raise ProjectionFailure("history_changed_since_context_boundary")


def _expand(config: FrozenConfig, messages: List[Dict[str, Any]], start: int, end: int,
            boundary: int, fmt: FormatMetadata, *, summary_input: bool = False,
            hard_max_chars: int = 0, injection_chars: int = 0) -> List[Dict[str, Any]]:
    cfg = replace(config, user_msg_soft_trim_enabled=False, toolcall_args_soft_trim_enabled=False) if summary_input else config
    tiers = cfg.compute_tool_tiers(messages)
    # Keep the existing user regime; only tool/image decisions become generation-scoped.
    override_ = cfg.latest_user_regime_b_override(messages, start, end, fmt, tool_tiers=tiers,
        inj_chars=injection_chars, overhead_chars=0, hard_max_chars=hard_max_chars)
    overrides = {override_[0]: override_[1]} if override_ else None
    split = min(end, max(start, boundary))
    old = cfg.expand_canonical_to_llm_messages(messages, start_idx=start, end_idx=split,
        format_metadata=fmt, tool_tiers=tiers, user_trim_overrides=overrides)
    old = ProjectionConfig.apply_image_recency_policy(cfg, old)
    new = cfg.expand_canonical_to_llm_messages(messages, start_idx=split, end_idx=end,
        format_metadata=fmt, tool_tiers=tiers, user_trim_overrides=overrides)
    for message in new:
        # New images keep their first per-message representation until a boundary.
        old.extend(ProjectionConfig.apply_image_recency_policy(cfg, [message]))
    return old


def project(config: ProjectionConfig, messages: List[Dict[str, Any]], active: Optional[CompactionRecord],
            state: Dict[str, Any], *, format_metadata: FormatMetadata, fixed_overhead_tokens: int = 0,
            check_history: bool = True) -> StableProjection:
    if check_history:
        validate_history(state, messages)
    cfg = frozen_config(config, state)
    if (active.id if active else None) != state["active_summary_id"]:
        raise ProjectionFailure("torn_context_generation")
    start = int(state["tail_start"])
    if not 0 <= start <= len(messages) or adjust_tail_start_for_tool_protocol(messages, start) != start:
        raise ProjectionFailure("invalid_context_tail_boundary")
    summary = SUMMARY_SYSTEM_TAG + active.summary_text.strip() if active else ""
    prefix: List[Dict[str, Any]] = [{"role": "system", "content": summary}] if active else []
    picks: List[tuple[int, str, str]] = []
    retained = {str(msg.get("tool_call_id")) for msg in messages[start:] if msg.get("role") == "tool"}
    for index in cast(List[int], state["reinject_indices"]):
        if index >= start or str(messages[index].get("tool_call_id")) in retained:
            continue
        item = gather_recent_tool_results_for_reinject(cfg, messages, index + 1, k=1, budget_chars=2**31 - 1)
        if item and item[0][0] == index:
            picks.extend(item)
    reinject = format_reinject_system_content(picks) or ""
    if reinject:
        prefix.append({"role": "system", "content": reinject})
    tail = _expand(cfg, messages, start, len(messages), int(state["boundary_count"]), format_metadata,
        hard_max_chars=int(state["parameters"]["compaction"]["verbatim_hard_max_chars"]),
        injection_chars=len(summary) + len(reinject))
    wire = prefix + tail
    return StableProjection(messages=wire, tail_start=start,
        tail_tokens=sum(_projected_message_weight(cfg, msg, "token") for msg in tail),
        tail_chars=sum(_projected_message_weight(cfg, msg, "char") for msg in tail),
        summary_tokens=cfg.estimate_tokens(summary), summary_chars=len(summary),
        reinject_tokens=cfg.estimate_tokens(reinject), reinject_chars=len(reinject),
        total_tokens=max(0, fixed_overhead_tokens) + _projection_wire_tokens(cfg, wire), policy_trace=trace(state))


def plan(config: ProjectionConfig, policy: CompactionPolicy, messages: List[Dict[str, Any]],
         active: Optional[CompactionRecord], state: Dict[str, Any], *, format_metadata: FormatMetadata,
         window_usable: int, fixed_overhead_tokens: int) -> Optional[Dict[str, Any]]:
    current = project(config, messages, active, state, format_metadata=format_metadata,
                      fixed_overhead_tokens=fixed_overhead_tokens)
    trigger, target_lo, target_hi = policy.thresholds(window_usable)
    if not policy.enabled or current.total_tokens <= trigger:
        return None
    previous_end = active.covers_exclusive_end if active else 0
    # Age only the candidate tail; the summary still reads the pre-boundary state.
    until = compute_ideal_covers_exclusive_end(policy, config, messages, target_lo, target_hi,
        format_metadata, overhead=fixed_overhead_tokens + policy.max_output_tokens, window_usable=window_usable)
    aged = FrozenConfig(**{field.name: getattr(config, field.name) for field in fields(ProjectionConfig)},
        tiers_by_identity={tool_identity(i, messages[i]): tier for i, tier in config.compute_tool_tiers(messages).items()})
    def tail_tokens(start: int) -> int:
        tail = _expand(aged, messages, start, len(messages), len(messages), format_metadata,
                       hard_max_chars=policy.verbatim_hard_max_chars)
        return sum(_projected_message_weight(aged, msg, "token") for msg in tail)
    floor = min(policy.verbatim_min_tail_tokens(window_usable), tail_tokens(0))
    while until > previous_end and tail_tokens(until) < floor:
        until = adjust_tail_start_for_tool_protocol(messages, until - 1)
    if until <= previous_end:
        raise ProjectionFailure("irreducible_tail_floor")
    minimum_tail = _expand(aged, messages, until, len(messages), len(messages), format_metadata,
                           hard_max_chars=policy.verbatim_hard_max_chars)
    if fixed_overhead_tokens + _projection_wire_tokens(aged, minimum_tail) > target_hi:
        raise ProjectionFailure("irreducible_fixed_and_tail_budget")
    return {"prev_end": previous_end, "prev_text": active.summary_text if active else "",
            "new_until": until, "target_tokens": target_hi, "trigger_tokens": trigger,
            "minimum_tail_tokens": floor}


def summary_transcript(config: ProjectionConfig, messages: List[Dict[str, Any]], state: Dict[str, Any],
                       plan_: Dict[str, Any], *, format_metadata: FormatMetadata, max_chars: int) -> str:
    cfg = frozen_config(config, state)
    wire = _expand(cfg, messages, int(plan_["prev_end"]), int(plan_["new_until"]),
                   int(state["boundary_count"]), format_metadata, summary_input=True)
    # Summary endpoint is text-only; never serialize image/base64 blobs into text.
    normalized: List[Dict[str, Any]] = []
    for msg in wire:
        text, images = content_text_and_image_count(msg.get("content"))
        normalized.append({**msg, "content": text, **({"image_count": images} if images else {})})
    transcript = json.dumps({"previous_summary": plan_["prev_text"], "new_history": normalized}, ensure_ascii=False)
    if max_chars > 0 and len(transcript) > max_chars:
        raise ProjectionFailure("summary_input_budget_exceeded")
    return transcript


def next_generation(config: ProjectionConfig, policy: CompactionPolicy, messages: List[Dict[str, Any]],
                    previous: Dict[str, Any], record: CompactionRecord, *, format_metadata: FormatMetadata,
                    target_tokens: int, fixed_overhead_tokens: int, minimum_tail_tokens: int = 0) -> Dict[str, Any]:
    start = adjust_tail_start_for_tool_protocol(messages, record.covers_exclusive_end)
    if start != record.covers_exclusive_end:
        raise ProjectionFailure("summary_splits_tool_pair")
    params = parameters(config, policy)
    tiers = config.compute_tool_tiers(messages)
    state: Dict[str, Any] = {**previous, "generation": int(previous["generation"]) + 1,
        "projection_id": uuid.uuid4().hex, "reason": "context_threshold",
        "parameters": params, "parameters_sha256": digest(params),
        "active_summary_id": record.id, "tail_start": start, "boundary_count": len(messages),
        "boundary_history_sha256": history_digest(messages[start:]),
        "tool_tiers": {tool_identity(i, messages[i]): tier for i, tier in tiers.items() if i >= start},
        "target_tokens": target_tokens, "reinject_indices": []}
    picks = gather_recent_tool_results_for_reinject(config, messages, start,
        k=policy.reinject_tool_results_count, budget_chars=policy.reinject_budget_chars)
    retained = {str(msg.get("tool_call_id")) for msg in messages[start:] if msg.get("role") == "tool"}
    seen: set[str] = set(retained)
    # Keep at most one restoration for each call, entirely outside the tail.
    selected: List[int] = []
    for index, _label, _text in reversed(picks):
        identity = str(messages[index].get("tool_call_id"))
        if identity not in seen:
            selected.append(index)
            seen.add(identity)
    state["reinject_indices"] = sorted(selected)
    while True:
        result = project(config, messages, record, state, format_metadata=format_metadata,
                         fixed_overhead_tokens=fixed_overhead_tokens)
        if result.tail_tokens < minimum_tail_tokens:
            raise ProjectionFailure("post_compaction_tail_floor_unattainable")
        if result.total_tokens <= target_tokens:
            state["committed_tokens"] = result.total_tokens
            return state
        if not state["reinject_indices"]:
            raise ProjectionFailure("post_compaction_target_unattainable")
        # Optional whole restorations may be removed; instructions/pairs never sliced.
        state["reinject_indices"].pop(0)
