"""On-demand preparation must not change projections or plans by a single byte.

Stored rows are simulated as "sealed": only role, tool_call_id and identity stay
readable, the body lives under ``_sealed`` and ``prepare`` restores it (like a
decryption). Every case projects/plans twice — all rows prepared up front versus
``prepare_message`` — and compares the full outputs, then checks which sealed rows
were actually opened.
"""
import json
from dataclasses import asdict, replace
from typing import Any, Dict, List

import pytest

from flops_agent.engine import stable_compaction as stable
from flops_agent.engine.compaction import CompactionPolicy, ProjectionConfig
from flops_agent.seams.compaction_store import CompactionRecord

FIELD = "_id"
VISIBLE = ("role", "tool_call_id", FIELD)


def fmt(meta):
    return f"sent_at={meta['timestamp']}" if meta.get("timestamp") else ""


def call(cid, name="t", args="{}"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


def history(rounds, *, tool_chars=600):
    out = [{"role": "user", "content": "instruction", "metadata": {"timestamp": "2026-11-20T00:00:00Z"}}]
    for i in range(rounds):
        out += [{"role": "assistant", "content": None, "tool_calls": [call(f"c{i}", "read_file" if i % 2 else "t",
                 json.dumps({"path": f"/f{i}"}))]},
                {"role": "tool", "tool_call_id": f"c{i}", "content": f"result {i} " + "x" * tool_chars},
                {"role": "assistant", "content": f"done {i}", "reasoning_content": f"why {i}"},
                {"role": "user", "content": f"next {i}", "metadata": {"timestamp": f"2026-11-20T00:{i:02d}:00Z"}}]
    for index, message in enumerate(out):
        message[FIELD] = f"id-{index}"
    return out


class Sealer:
    def __init__(self):
        self.opened: List[str] = []

    def seal(self, messages):
        return [{**{k: m[k] for k in VISIBLE if k in m}, "_sealed": json.dumps(m)} for m in messages]

    def __call__(self, row: Dict[str, Any]) -> Dict[str, Any]:
        if "_sealed" not in row:
            return dict(row)
        self.opened.append(row[FIELD])
        return json.loads(row["_sealed"])


def generation(messages, end, *, config=None, reinject=6):
    config = config or ProjectionConfig()
    policy = CompactionPolicy(reinject_tool_results_count=reinject, reinject_budget_chars=10**6)
    state = stable.initial_state(config, policy, messages[:1], None, format_metadata=fmt, identity_field=FIELD)
    record = CompactionRecord(f"s{end}", end, "summary text for the covered history")
    state = stable.next_generation(config, policy, messages, state, record, format_metadata=fmt,
                                   target_tokens=10**9, fixed_overhead_tokens=0)
    return state, record, policy


def dump(projection):
    return json.dumps(asdict(projection), ensure_ascii=False, sort_keys=True, default=str)


def both(messages, state, record, *, config=None):
    config = config or ProjectionConfig()
    eager = stable.project(config, messages, record, state, format_metadata=fmt, fixed_overhead_tokens=123)
    sealer = Sealer()
    lazy = stable.project(config, sealer.seal(messages), record, state, format_metadata=fmt,
                          fixed_overhead_tokens=123, prepare_message=sealer)
    return eager, lazy, sealer


def test_normal_generation_is_identical_and_opens_only_tail_and_reinjected_turns():
    messages = history(8)
    state, record, _ = generation(messages, 25)
    assert state["reinject_indices"]
    eager, lazy, sealer = both(messages, state, record)
    assert dump(lazy) == dump(eager)
    tail = {m[FIELD] for m in messages[25:]}
    reinjected = {messages[i][FIELD] for i in state["reinject_indices"]}
    parents = {messages[i - 1][FIELD] for i in state["reinject_indices"]}
    assert set(sealer.opened) == tail | reinjected | parents
    assert len(sealer.opened) == len(set(sealer.opened))      # each row prepared once per call


def test_without_reinjection_only_the_tail_is_opened():
    messages = history(8)
    state, record, _ = generation(messages, 25, reinject=0)
    assert not state["reinject_indices"]
    eager, lazy, sealer = both(messages, state, record)
    assert dump(lazy) == dump(eager)
    assert set(sealer.opened) == {m[FIELD] for m in messages[25:]}


def test_exempt_tool_names_and_tiers_match():
    config = replace(ProjectionConfig(), tool_soft_trim_exempt_names=frozenset({"read_file"}))
    messages = history(10, tool_chars=30_000)
    state, record, _ = generation(messages, 21, config=config)
    eager, lazy, _ = both(messages, state, record, config=config)
    assert dump(lazy) == dump(eager)


def test_cross_boundary_orphan_tool_result_resolves_its_name_from_older_history():
    """A tail tool result whose call sits before the boundary (protocol-violating legacy
    history): the name must still come from that older call, exactly as before."""
    config = replace(ProjectionConfig(), tool_soft_trim_exempt_names=frozenset({"read_file"}))
    messages = history(6, tool_chars=30_000)
    messages.insert(18, {"role": "user", "content": "orphan follows", FIELD: "orphan-user"})
    messages.insert(19, {"role": "tool", "tool_call_id": "c1", "content": "late " + "y" * 30_000,
                         FIELD: "orphan-tool"})
    state, record, _ = generation(messages, 17, config=config)
    eager, lazy, sealer = both(messages, state, record, config=config)
    assert dump(lazy) == dump(eager)
    assert messages[2][FIELD] in sealer.opened or messages[5][FIELD] in sealer.opened


def test_unresolvable_orphan_and_duplicate_ids_match():
    messages = history(6)
    messages.append({"role": "tool", "tool_call_id": "never-called", "content": "z" * 500, FIELD: "nc"})
    messages[13]["tool_calls"] = [call("c0", "renamed"), call("c0", "renamed-twice")]   # duplicate id, later wins
    messages.append({"role": "tool", "tool_call_id": "c0", "content": "dup " + "w" * 500, FIELD: "dup"})
    state, record, _ = generation(messages, 17)
    eager, lazy, _ = both(messages, state, record)
    assert dump(lazy) == dump(eager)


@pytest.mark.parametrize("damage", ["short_tool", "parent_unreadable", "parent_missing", "not_a_tool"])
def test_reinjection_edge_cases_match(damage):
    messages = history(8)
    state, record, _ = generation(messages, 25)
    index = state["reinject_indices"][-1]
    if damage == "short_tool":
        messages[index]["content"] = "tiny"
    elif damage == "parent_unreadable":
        messages[index - 1]["tool_calls"] = "[encrypted tool_calls — decrypt failed: InvalidTag]"
    elif damage == "parent_missing":
        messages[index - 1]["tool_calls"] = [call("other")]
    else:
        messages[index] = {"role": "user", "content": "x" * 400, FIELD: messages[index][FIELD]}
    eager, lazy, _ = both(messages, state, record)
    assert dump(lazy) == dump(eager)


@pytest.mark.parametrize("force", [False, True])
def test_plan_is_identical(force):
    config = ProjectionConfig()
    messages = history(20, tool_chars=4_000)
    state, record, policy = generation(messages, 25)
    policy = replace(policy, enabled=True, reserved_tail_messages=6)
    args = dict(format_metadata=fmt, window_usable=20_000, fixed_overhead_tokens=500, force=force,
                send_safety_tokens=10**9)
    eager = stable.plan(config, policy, messages, record, state, **args)
    sealer = Sealer()
    lazy = stable.plan(config, policy, sealer.seal(messages), record, state, prepare_message=sealer, **args)
    assert eager is not None and json.dumps(lazy, sort_keys=True) == json.dumps(eager, sort_keys=True)
    assert not set(sealer.opened) & {messages[i][FIELD] for i in range(25)} - {
        messages[i][FIELD] for i in state["reinject_indices"]} - {
        messages[i - 1][FIELD] for i in state["reinject_indices"]}


def test_plan_without_summary_matches():
    config = ProjectionConfig()
    messages = history(20, tool_chars=4_000)
    policy = CompactionPolicy(enabled=True, reserved_tail_messages=6)
    state = stable.initial_state(config, policy, messages, None, format_metadata=fmt, identity_field=FIELD)
    args = dict(format_metadata=fmt, window_usable=30_000, fixed_overhead_tokens=0, force=True)
    eager = stable.plan(config, policy, messages, None, state, **args)
    sealer = Sealer()
    lazy = stable.plan(config, policy, sealer.seal(messages), None, state, prepare_message=sealer, **args)
    assert json.dumps(lazy, sort_keys=True) == json.dumps(eager, sort_keys=True)


def test_content_digest_generations_still_verify_every_prepared_body():
    messages = [{k: v for k, v in m.items() if k != FIELD} for m in history(6)]
    state = stable.initial_state(ProjectionConfig(), CompactionPolicy(), messages[:1], None, format_metadata=fmt)
    record = CompactionRecord("s9", 9, "summary")
    state = stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, state, record,
                                   format_metadata=fmt, target_tokens=10**9, fixed_overhead_tokens=0)
    assert state["history_digest_version"] == stable.HISTORY_DIGEST_VERSION
    sealer = Sealer()
    sealed = [{**{k: m[k] for k in ("role", "tool_call_id") if k in m}, "_sealed": json.dumps(m),
               FIELD: str(i)} for i, m in enumerate(messages)]
    lazy = stable.project(ProjectionConfig(), sealed, record, state, format_metadata=fmt, prepare_message=sealer)
    eager = stable.project(ProjectionConfig(), messages, record, state, format_metadata=fmt)
    assert dump(lazy) == dump(eager)
    edited = [dict(m) for m in sealed]
    body = json.loads(edited[2]["_sealed"])
    body["content"] = "edited"
    edited[2]["_sealed"] = json.dumps(body)
    with pytest.raises(stable.ProjectionFailure, match="history_changed"):
        stable.project(ProjectionConfig(), edited, record, state, format_metadata=fmt, prepare_message=Sealer())


def test_stored_rows_are_never_modified():
    messages = history(8)
    state, record, _ = generation(messages, 25)
    sealer = Sealer()
    sealed = sealer.seal(messages)
    before = json.dumps(sealed, sort_keys=True)
    stable.project(ProjectionConfig(), sealed, record, state, format_metadata=fmt, prepare_message=sealer)
    assert json.dumps(sealed, sort_keys=True) == before
