"""Generation-scoped context projections. No persistence or provider dependencies.

Canonical history is an append-only input. A generation records decisions, not
rewritten messages. Unlisted (new) tool results always receive their first-use
budget; only an explicitly committed compaction can age retained tool results.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Callable, Dict, List, Optional, cast

from typing_extensions import override

from .compaction import (
    CompactionPolicy, ContextProjection, FormatMetadata, ProjectionConfig,
    SUMMARY_SYSTEM_TAG, adjust_tail_start_for_tool_protocol,
    build_context_projection,
    content_text_and_image_count, format_reinject_system_content,
    gather_recent_tool_results_for_reinject,
    _projected_message_weight, _projection_wire_tokens,  # pyright: ignore[reportPrivateUsage] -- shared engine accounting primitives
)
from ..seams.compaction_store import CompactionRecord

STRATEGY_VERSION = "stable-tool-generations-v1"
STATE_KEY = "context_projection_state_v1"


class ProjectionFailure(ValueError):
    """A safe, content-free reason to keep the previous generation / refuse send."""


@dataclass
class SummaryFoldTelemetry:
    """Content-free counters for one logical summary task.

    The product layer may persist these values.  They deliberately contain no
    transcript, summary, exception text, or provider credentials.
    """

    model_calls: int = 0
    segmented_calls: int = 0
    split_events: int = 0
    max_depth: int = 0
    initial_overflow: bool = False


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


def summary_reserve_tokens(config: ProjectionConfig, policy: CompactionPolicy) -> int:
    """Upper-bound room for the summary that does not exist yet: its system-message
    framing plus the summary call's output ceiling (``max_output_tokens``)."""
    framing = _projection_wire_tokens(config, [{"role": "system", "content": SUMMARY_SYSTEM_TAG}])
    return framing + max(0, int(policy.max_output_tokens))


@dataclass
class BoundaryChoice:
    """Result of :func:`choose_recent_boundary` (all numbers in projected wire tokens)."""

    boundary: int
    start: int
    estimated_tokens: int
    start_tokens: int
    reserve_tokens: int
    selection: str


def choose_recent_boundary(config: ProjectionConfig, policy: CompactionPolicy,
                           messages: List[Dict[str, Any]], previous_end: int, *,
                           target_tokens: int, fixed_overhead_tokens: int,
                           format_metadata: FormatMetadata, fold_to_start: bool = False) -> BoundaryChoice:
    """Pick the summary boundary by walking from the recent-tail boundary toward older
    messages only.

    * Start = the boundary that keeps the latest ``policy.reserved_tail_messages`` raw
      messages, aligned so a tool call and its results are never separated (alignment
      can only keep *more* messages).
    * Every candidate is measured with one measure — fixed overhead + summary reserve
      (:func:`summary_reserve_tokens`) + the projected wire of the retained tail under
      the aged tool tiers the committed generation will use. Optional tool
      restorations are excluded: they yield to the target at commit time.
    * If the start already reaches the target it is chosen (compaction never gives up
      because the target is out of reach). Otherwise the boundary moves one aligned
      message at a time toward older history, keeping as much raw history as possible,
      and stops at the last boundary before keeping one more message would exceed the
      target.
    * Candidates never reach back to (or before) the active summary's coverage; when
      nothing older than the recent tail is left to fold, ``irreducible_tail_floor``.
    """
    count = len(messages)
    recent = max(0, int(policy.reserved_tail_messages))
    if count - recent <= previous_end:
        raise ProjectionFailure("irreducible_tail_floor")
    latest_start = count - recent
    start = adjust_tail_start_for_tool_protocol(messages, latest_start)
    if start > latest_start or any(
        adjust_tail_start_for_tool_protocol(messages, index) > index
        for index in range(max(1, start), count) if messages[index].get("role") == "tool"
    ):
        raise ProjectionFailure("recent_tail_contains_orphan_tool_result")
    if start <= previous_end:
        raise ProjectionFailure("irreducible_tail_floor")
    aged = FrozenConfig(**{field.name: getattr(config, field.name) for field in fields(ProjectionConfig)},
        tiers_by_identity={tool_identity(i, messages[i]): tier for i, tier in config.compute_tool_tiers(messages).items()})
    reserve = summary_reserve_tokens(aged, policy)
    base = max(0, int(fixed_overhead_tokens)) + reserve

    def total(boundary: int) -> int:
        tail = _expand(aged, messages, boundary, count, count, format_metadata,
                       hard_max_chars=policy.verbatim_hard_max_chars)
        return base + _projection_wire_tokens(aged, tail)

    start_tokens = total(start)
    if fold_to_start:
        return BoundaryChoice(start, start, start_tokens, start_tokens, reserve, "forced_recent_boundary")
    if start_tokens >= target_tokens:
        return BoundaryChoice(start, start, start_tokens, start_tokens, reserve, "start_reaches_target")
    chosen, chosen_tokens = start, start_tokens
    candidate = start - 1
    while candidate > previous_end:
        aligned = adjust_tail_start_for_tool_protocol(messages, candidate)
        if aligned <= previous_end or aligned > candidate:
            break
        if aligned <= candidate:
            candidate_tokens = total(aligned)
            if candidate_tokens > target_tokens:
                return BoundaryChoice(chosen, start, chosen_tokens, start_tokens, reserve, "extended_within_target")
            chosen, chosen_tokens = aligned, candidate_tokens
        candidate = min(aligned, candidate) - 1
    return BoundaryChoice(chosen, start, chosen_tokens, start_tokens, reserve, "all_older_history_fits")


def plan(config: ProjectionConfig, policy: CompactionPolicy, messages: List[Dict[str, Any]],
         active: Optional[CompactionRecord], state: Dict[str, Any], *, format_metadata: FormatMetadata,
         window_usable: int, fixed_overhead_tokens: int, force: bool = False,
         send_safety_tokens: int = 0) -> Optional[Dict[str, Any]]:
    """Decide whether to compact and where (see :func:`choose_recent_boundary`).

    ``target_tokens`` (window × ``min_window_fraction``) is a selection goal only.
    ``force`` permits a checkpoint below the automatic trigger. In that case it folds
    everything before the recent boundary, so a manual checkpoint or provider-overflow
    recovery can reduce the request even when local accounting reports low usage.
    ``send_safety_tokens`` (optional, product's final request line): when even the
    recent tail with no summary at all cannot fit under it, no summary can help, so the
    plan is refused before spending a model call.
    """
    current = project(config, messages, active, state, format_metadata=format_metadata,
                      fixed_overhead_tokens=fixed_overhead_tokens)
    trigger = policy.thresholds(window_usable)[0]
    if not policy.enabled or (not force and current.total_tokens <= trigger):
        return None
    previous_end = active.covers_exclusive_end if active else 0
    target = policy.selection_target_tokens(window_usable)
    choice = choose_recent_boundary(config, policy, messages, previous_end, target_tokens=target,
        fixed_overhead_tokens=fixed_overhead_tokens, format_metadata=format_metadata,
        fold_to_start=force and current.total_tokens <= trigger)
    if send_safety_tokens > 0 and choice.start_tokens - choice.reserve_tokens > send_safety_tokens:
        raise ProjectionFailure("recent_tail_exceeds_send_safety")
    return {"prev_end": previous_end, "prev_text": active.summary_text if active else "",
            "new_until": choice.boundary, "target_tokens": target, "trigger_tokens": trigger,
            "minimum_tail_tokens": 0, "current_tokens": current.total_tokens,
            "estimated_tokens": choice.estimated_tokens, "start_boundary": choice.start,
            "summary_reserve_tokens": choice.reserve_tokens, "selection": choice.selection}


def plan_exact_boundary(config: ProjectionConfig, policy: CompactionPolicy,
                        messages: List[Dict[str, Any]], active: Optional[CompactionRecord],
                        state: Dict[str, Any], *, format_metadata: FormatMetadata,
                        window_usable: int, fixed_overhead_tokens: int,
                        exact_tail_start: int, send_safety_tokens: int = 0) -> Dict[str, Any]:
    """Plan a manual compaction whose raw tail starts at an authoritative boundary.

    Unlike :func:`plan`, this never searches for a cheaper boundary.  The caller
    supplies a durable run boundary and receives a refusal if the boundary cannot
    be honored exactly.  This is deliberately unsuitable for automatic compaction.
    """
    if not policy.enabled:
        raise ProjectionFailure("disabled")
    previous_end = active.covers_exclusive_end if active else 0
    boundary = int(exact_tail_start)
    if boundary < 0 or boundary > len(messages):
        raise ProjectionFailure("extreme_run_boundary_stale")
    if previous_end > boundary:
        raise ProjectionFailure("extreme_boundary_before_active_summary")
    if previous_end == boundary:
        raise ProjectionFailure("extreme_already_at_boundary")
    if adjust_tail_start_for_tool_protocol(messages, boundary) != boundary:
        raise ProjectionFailure("extreme_boundary_splits_tool_pair")
    target = policy.selection_target_tokens(window_usable)
    aged = FrozenConfig(**{field.name: getattr(config, field.name) for field in fields(ProjectionConfig)},
        tiers_by_identity={tool_identity(i, messages[i]): tier for i, tier in config.compute_tool_tiers(messages).items()})
    tail = _expand(aged, messages, boundary, len(messages), len(messages),
                   format_metadata, hard_max_chars=policy.verbatim_hard_max_chars)
    if send_safety_tokens > 0 and max(0, fixed_overhead_tokens) + _projection_wire_tokens(aged, tail) > send_safety_tokens:
        raise ProjectionFailure("extreme_fixed_and_run_budget_unattainable")
    return {"prev_end": previous_end, "prev_text": active.summary_text if active else "",
            "new_until": boundary, "target_tokens": target, "trigger_tokens": 0,
            "minimum_tail_tokens": 0}


def summary_history(config: ProjectionConfig, messages: List[Dict[str, Any]], state: Dict[str, Any],
                    plan_: Dict[str, Any], *, format_metadata: FormatMetadata) -> List[Dict[str, Any]]:
    """Return the complete, pre-generation-change history projection to summarize.

    Images remain represented by a count because the summary endpoint is text
    only.  No size cap is applied here: the provider is the authority on its
    context window, and :func:`fold_summary_on_context_overflow` handles an
    explicit overflow without silently discarding history.
    """
    cfg = frozen_config(config, state)
    wire = _expand(cfg, messages, int(plan_["prev_end"]), int(plan_["new_until"]),
                   int(state["boundary_count"]), format_metadata, summary_input=True)
    # Summary endpoint is text-only; never serialize image/base64 blobs into text.
    normalized: List[Dict[str, Any]] = []
    for msg in wire:
        text, images = content_text_and_image_count(msg.get("content"))
        normalized.append({**msg, "content": text, **({"image_count": images} if images else {})})
    return normalized


def summary_transcript_from_history(previous_summary: str, history: List[Dict[str, Any]]) -> str:
    """Serialize one complete chronological summary segment without truncation."""
    return json.dumps({"previous_summary": previous_summary, "new_history": history}, ensure_ascii=False)


def summary_transcript(config: ProjectionConfig, messages: List[Dict[str, Any]], state: Dict[str, Any],
                       plan_: Dict[str, Any], *, format_metadata: FormatMetadata, max_chars: int = 0) -> str:
    """Compatibility wrapper returning the uncapped summary transcript.

    ``max_chars`` remains in the public signature for callers compiled against
    the previous API, but no longer rejects or truncates input.  Provider
    context overflow is handled only after a real summary attempt.
    """
    _ = max_chars
    history = summary_history(config, messages, state, plan_, format_metadata=format_metadata)
    return summary_transcript_from_history(str(plan_.get("prev_text") or ""), history)


def _summary_split_index(history: List[Dict[str, Any]]) -> Optional[int]:
    """Choose the character-balanced chronological boundary nearest the middle.

    A boundary is ineligible when a tool result on the right belongs to an
    assistant tool call on the left.  This keeps the call/result protocol unit
    intact even for batches with several tool results.
    """
    if len(history) < 2:
        return None
    weights = [max(1, len(json.dumps(msg, ensure_ascii=False, separators=(",", ":")))) for msg in history]
    total = sum(weights)
    prefix = 0
    candidates: List[tuple[int, int]] = []

    def call_ids(items: List[Dict[str, Any]]) -> set[str]:
        identities: set[str] = set()
        for message in items:
            raw_calls = message.get("tool_calls")
            if not isinstance(raw_calls, list):
                continue
            for raw_call in cast(List[Any], raw_calls):
                if not isinstance(raw_call, dict):
                    continue
                identity = cast(Dict[str, Any], raw_call).get("id")
                if identity:
                    identities.add(str(identity))
        return identities

    right_result_counts = Counter(
        str(message.get("tool_call_id"))
        for message in history
        if message.get("role") == "tool" and message.get("tool_call_id")
    )
    crossing_ids: set[str] = set()
    for index in range(1, len(history)):
        prefix += weights[index - 1]
        moved = history[index - 1]
        for identity in call_ids([moved]):
            if right_result_counts[identity] > 0:
                crossing_ids.add(identity)
        if moved.get("role") == "tool" and moved.get("tool_call_id"):
            identity = str(moved["tool_call_id"])
            right_result_counts[identity] -= 1
            if right_result_counts[identity] <= 0:
                crossing_ids.discard(identity)
        if history[index].get("role") == "tool":
            continue
        if crossing_ids:
            continue
        candidates.append((abs(total - 2 * prefix), index))
    return min(candidates)[1] if candidates else None


def fold_summary_on_context_overflow(
    previous_summary: str,
    history: List[Dict[str, Any]],
    summarize: Callable[[str, List[Dict[str, Any]]], str],
    is_context_overflow: Callable[[BaseException], bool],
    *,
    telemetry: Optional[SummaryFoldTelemetry] = None,
    max_depth: int = 12,
    max_calls: int = 64,
) -> str:
    """Summarize all history, splitting only after an explicit provider overflow.

    The first call always receives ``previous_summary`` plus the complete
    ``history``.  On an explicit context-window rejection, the earlier half is
    folded into an in-memory summary before the later half.  A child may split
    recursively.  Other failures propagate unchanged.  The caller alone owns
    persistence, so no intermediate result can advance a generation.
    """
    if max_depth < 1 or max_calls < 1:
        raise ValueError("summary split limits must be positive")
    stats = telemetry if telemetry is not None else SummaryFoldTelemetry()

    def run(previous: str, segment: List[Dict[str, Any]], depth: int) -> str:
        if depth > max_depth:
            raise ProjectionFailure("summary_split_depth_exceeded")
        if stats.model_calls >= max_calls:
            raise ProjectionFailure("summary_split_call_budget_exceeded")
        stats.model_calls += 1
        if depth > 0:
            stats.segmented_calls += 1
        stats.max_depth = max(stats.max_depth, depth)
        try:
            result = summarize(previous, segment)
        except Exception as exc:
            if not is_context_overflow(exc):
                raise
            overflow_error: Exception = exc
            if depth == 0:
                stats.initial_overflow = True
        else:
            clean = str(result or "").strip()
            if not clean:
                raise ProjectionFailure("empty_summary_segment")
            return clean

        # Leave the provider exception handler before issuing child requests.
        # Otherwise an unrelated child error receives the parent overflow as
        # implicit ``__context__`` and can be misclassified as another overflow.
        boundary = _summary_split_index(segment)
        if boundary is None:
            raise ProjectionFailure("summary_segment_indivisible") from overflow_error
        if depth >= max_depth:
            raise ProjectionFailure("summary_split_depth_exceeded") from overflow_error
        stats.split_events += 1
        earlier = run(previous, segment[:boundary], depth + 1)
        return run(earlier, segment[boundary:], depth + 1)

    return run(previous_summary, history, 0)


def next_generation(config: ProjectionConfig, policy: CompactionPolicy, messages: List[Dict[str, Any]],
                    previous: Dict[str, Any], record: CompactionRecord, *, format_metadata: FormatMetadata,
                    target_tokens: int, fixed_overhead_tokens: int, minimum_tail_tokens: int = 0,
                    reason: str = "context_threshold", send_safety_tokens: int = 0,
                    baseline_tokens: Optional[int] = None) -> Dict[str, Any]:
    """Build the candidate generation for an already generated summary.

    Optional tool restorations are dropped (oldest first) until ``target_tokens`` is met.
    Without ``send_safety_tokens`` the target is a hard gate (historic behaviour). With it,
    the target is a goal: once no optional restoration is left, a result above the target
    is still accepted when it fits the send safety line and — if ``baseline_tokens`` (the
    previous generation's projection of the same history) is given — is strictly smaller
    than it, so a generation can never grow the request. ``target_missed`` records that.
    """
    start = adjust_tail_start_for_tool_protocol(messages, record.covers_exclusive_end)
    if start != record.covers_exclusive_end:
        raise ProjectionFailure("summary_splits_tool_pair")
    params = parameters(config, policy)
    tiers = config.compute_tool_tiers(messages)
    state: Dict[str, Any] = {**previous, "generation": int(previous["generation"]) + 1,
        "projection_id": uuid.uuid4().hex, "reason": reason,
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
        within_safety = send_safety_tokens <= 0 or result.total_tokens <= send_safety_tokens
        reduced = baseline_tokens is None or result.total_tokens < baseline_tokens
        meets_target = result.total_tokens <= target_tokens
        if within_safety and reduced and (meets_target or send_safety_tokens > 0 and not state["reinject_indices"]):
            state["committed_tokens"] = result.total_tokens
            if meets_target:
                state.pop("target_missed", None)
            else:
                state["target_missed"] = True
            return state
        if not state["reinject_indices"]:
            if not within_safety:
                raise ProjectionFailure("post_compaction_send_safety_exceeded")
            if not reduced:
                raise ProjectionFailure("post_compaction_no_reduction")
            raise ProjectionFailure("post_compaction_target_unattainable")
        # Optional whole restorations may be removed; instructions/pairs never sliced.
        state["reinject_indices"].pop(0)
