"""Recent-boundary compaction selection and target-as-goal commit (synthetic history only)."""
import random
from copy import deepcopy

import pytest

from flops_agent.engine.compaction import (
    CompactionPolicy, ProjectionConfig, adjust_tail_start_for_tool_protocol,
)
from flops_agent.engine import stable_compaction as stable
from flops_agent.seams.compaction_store import CompactionRecord


def fmt(_meta):
    return ""


WINDOW = 100_000


def policy(**overrides):
    values = dict(enabled=True, reserved_tail_messages=10, max_output_tokens=1_000,
                  min_window_fraction=0.33, max_window_fraction=0.66)
    values.update(overrides)
    return CompactionPolicy(**values)


def chat(count, size=2_000):
    out = []
    for i in range(count):
        out.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "y" * size})
    return out


def tool_turns(count, size=2_000):
    out = [{"role": "user", "content": "start"}]
    for i in range(count):
        out.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"a{i}", "type": "function", "function": {"name": "search", "arguments": "{}"}},
            {"id": f"b{i}", "type": "function", "function": {"name": "search", "arguments": "{}"}}]})
        out.append({"role": "tool", "tool_call_id": f"a{i}", "content": "r" * size})
        out.append({"role": "tool", "tool_call_id": f"b{i}", "content": "s" * size})
    return out


def state_for(messages, pol, active=None):
    return stable.initial_state(ProjectionConfig(), pol, messages, active, format_metadata=fmt)


def choose(messages, pol, previous_end=0, target=None, fixed=0):
    return stable.choose_recent_boundary(
        ProjectionConfig(), pol, messages, previous_end,
        target_tokens=target if target is not None else pol.selection_target_tokens(WINDOW),
        fixed_overhead_tokens=fixed, format_metadata=fmt)


def linear_walk(messages, pol, previous_end, target, fixed):
    """Reference: walk one aligned boundary at a time from the recent boundary."""
    probe = choose(messages, pol, previous_end, target=0, fixed=fixed)
    start = probe.start

    def total(b):
        return choose(messages, policy(reserved_tail_messages=len(messages) - b), previous_end,
                      target=0, fixed=fixed).start_tokens if b < len(messages) else None

    if total(start) >= target:
        return start
    chosen = start
    b = start - 1
    while b > previous_end:
        if adjust_tail_start_for_tool_protocol(messages, b) == b:
            if total(b) > target:
                break
            chosen = b
        b -= 1
    return chosen


def test_start_at_or_over_target_is_chosen_not_abandoned():
    messages = chat(30, size=20_000)            # last 10 messages alone exceed 33%
    pol = policy()
    got = choose(messages, pol)
    assert got.boundary == got.start == len(messages) - 10
    assert got.selection == "start_reaches_target"
    assert got.estimated_tokens >= pol.selection_target_tokens(WINDOW)


def test_walks_toward_older_history_and_stops_before_exceeding():
    messages = chat(80, size=4_000)
    pol = policy()
    target = pol.selection_target_tokens(WINDOW)
    got = choose(messages, pol)
    assert got.boundary < got.start == len(messages) - 10
    assert got.estimated_tokens <= target
    # One more retained message would exceed the target.
    one_more = choose(messages, policy(reserved_tail_messages=len(messages) - got.boundary + 1), target=0)
    assert one_more.start_tokens > target


def test_tool_calls_and_results_stay_together_even_beyond_ten():
    messages = tool_turns(20, size=6_000)       # every turn = 3 messages
    pol = policy()
    got = choose(messages, pol, target=1)       # force "start" selection
    assert messages[got.boundary]["role"] == "assistant"
    assert len(messages) - got.boundary >= 10
    assert adjust_tail_start_for_tool_protocol(messages, got.boundary) == got.boundary
    extended = choose(messages, pol, target=pol.selection_target_tokens(WINDOW))
    assert messages[extended.boundary]["role"] != "tool"


def test_never_reaches_back_into_existing_summary_and_short_history_refuses():
    messages = chat(40, size=800)
    pol = policy()
    got = choose(messages, pol, previous_end=25, target=10**9)
    assert got.boundary == 26 and got.selection == "all_older_history_fits"
    with pytest.raises(stable.ProjectionFailure, match="irreducible_tail_floor"):
        choose(chat(9), pol)                    # fewer than 10 messages
    with pytest.raises(stable.ProjectionFailure, match="irreducible_tail_floor"):
        choose(messages, pol, previous_end=30)  # summary already covers up to the recent boundary


def test_selection_matches_one_by_one_walk():
    rng = random.Random(7)
    for _ in range(12):
        messages = []
        for i in range(rng.randint(15, 60)):
            if rng.random() < 0.3:
                k = rng.randint(1, 3)
                messages.append({"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"c{i}-{j}", "type": "function", "function": {"name": "x", "arguments": "{}"}}
                    for j in range(k)]})
                messages.extend({"role": "tool", "tool_call_id": f"c{i}-{j}",
                                 "content": "z" * rng.randint(10, 9_000)} for j in range(k))
            else:
                messages.append({"role": rng.choice(["user", "assistant"]), "content": "w" * rng.randint(10, 9_000)})
        pol = policy()
        previous_end = rng.randint(0, 5)
        fixed = rng.randint(0, 5_000)
        target = rng.randint(2_000, 40_000)
        try:
            got = choose(messages, pol, previous_end, target=target, fixed=fixed)
        except stable.ProjectionFailure:
            continue
        assert got.boundary == linear_walk(messages, pol, previous_end, target, fixed)


def test_plan_uses_recent_boundary_and_refuses_when_tail_alone_breaks_send_safety():
    pol = policy()
    messages = chat(80, size=6_000)
    state = state_for(messages, pol)
    planned = stable.plan(ProjectionConfig(), pol, messages, None, state, format_metadata=fmt,
                          window_usable=WINDOW, fixed_overhead_tokens=0)
    assert planned is not None
    assert planned["target_tokens"] == int(WINDOW * 0.33)
    assert planned["minimum_tail_tokens"] == 0
    assert planned["new_until"] <= planned["start_boundary"] == len(messages) - 10
    huge = chat(30, size=40_000)
    state = state_for(huge, pol)
    with pytest.raises(stable.ProjectionFailure, match="recent_tail_exceeds_send_safety"):
        stable.plan(ProjectionConfig(), pol, huge, None, state, format_metadata=fmt,
                    window_usable=WINDOW, fixed_overhead_tokens=0, send_safety_tokens=50_000)


def _commit(messages, pol, record, **kwargs):
    state = state_for(messages, pol)
    original = deepcopy(state)
    try:
        return stable.next_generation(ProjectionConfig(), pol, messages, state, record,
                                      format_metadata=fmt, fixed_overhead_tokens=0, **kwargs)
    finally:
        assert state == original                 # previous generation never mutated


def test_target_is_a_goal_when_send_safety_is_given():
    pol = policy()
    messages = chat(30, size=8_000)
    record = CompactionRecord("cs-1", 20, "summary text " * 50)
    # Historic strict gate without a safety line.
    with pytest.raises(stable.ProjectionFailure, match="post_compaction_target_unattainable"):
        _commit(messages, pol, record, target_tokens=1_000)
    accepted = _commit(messages, pol, record, target_tokens=1_000, send_safety_tokens=10**6,
                       baseline_tokens=10**7)
    assert accepted["target_missed"] is True and accepted["committed_tokens"] > 1_000
    with pytest.raises(stable.ProjectionFailure, match="post_compaction_send_safety_exceeded"):
        _commit(messages, pol, record, target_tokens=1_000, send_safety_tokens=1_500)
    with pytest.raises(stable.ProjectionFailure, match="post_compaction_no_reduction"):
        _commit(messages, pol, record, target_tokens=1_000, send_safety_tokens=10**6, baseline_tokens=1_200)
    with pytest.raises(stable.ProjectionFailure, match="post_compaction_send_safety_exceeded"):
        _commit(messages, pol, record, target_tokens=10**6, send_safety_tokens=1_500)
    with pytest.raises(stable.ProjectionFailure, match="post_compaction_no_reduction"):
        _commit(messages, pol, record, target_tokens=10**6, send_safety_tokens=10**6, baseline_tokens=1_200)
    met = _commit(messages, pol, record, target_tokens=10**6, send_safety_tokens=10**6)
    assert "target_missed" not in met


def test_forced_run_folds_to_recent_boundary_below_trigger():
    pol = policy()
    messages = chat(40, size=400)
    state = state_for(messages, pol)
    assert stable.plan(ProjectionConfig(), pol, messages, None, state, format_metadata=fmt,
                       window_usable=WINDOW, fixed_overhead_tokens=0) is None
    forced = stable.plan(ProjectionConfig(), pol, messages, None, state, format_metadata=fmt,
                         window_usable=WINDOW, fixed_overhead_tokens=0, force=True)
    assert forced is not None
    assert forced["new_until"] == forced["start_boundary"] == len(messages) - 10
    assert forced["selection"] == "forced_recent_boundary"
