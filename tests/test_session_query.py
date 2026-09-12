"""Session / Query unit tests -- verifying the internal consistency of the new architecture's core judgments.

Two design claims are pinned down here:

1. **"Continuing a reply" versus "regenerating" needs no declaration from the caller** -- it can
   be determined purely from session state (``ends_with_open_reply``). The root cause of the
   "continued content got appended twice" bug was exactly that a flag and the actual state
   could drift out of sync.
2. **Truncation's misfire guard is a framework property**, and the criterion is "would this drop
   a message the user themselves wrote" -- dropping the assistant's reply is precisely the point
   of regeneration, while dropping user output is what requires consent.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent.entities.query import Contributor, Query  # noqa: E402
from flops_agent.entities.session import (  # noqa: E402
    MessageNotFound,
    Session,
    TruncationNeedsConsent,
)


def U(mid, text="q", meta=False):
    m = {"_msg_id": mid, "role": "user", "content": text}
    if meta:
        m["isMeta"] = True
    return m


def A(mid, text="a", tool_calls=None):
    m = {"_msg_id": mid, "role": "assistant", "content": text}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return m


def sess(*msgs):
    """A session with the misfire guard switch (off by default, explicitly turned on here to test its behavior)."""
    return Session("c1", owner_id="u1", messages=list(msgs), guard_user_message_loss=True)


# ── "Continue vs. regenerate" is determined from state ───────────────────────

def test_open_reply_detected_from_state():
    assert sess(U("u1"), A("a1")).ends_with_open_reply is True      # tail is a reply -> continue
    assert sess(U("u1")).ends_with_open_reply is False              # tail is a question -> start a new turn
    assert sess().ends_with_open_reply is False                     # empty session
    print("test_open_reply_detected_from_state OK")


def test_tool_call_turn_is_not_an_open_reply():
    # An assistant turn with tool_calls is an intermediate "waiting on tool results" state; the
    # next step is executing the tool, not continuing the reply text
    s = sess(U("u1"), A("a1", tool_calls=[{"id": "c1"}]))
    assert s.ends_with_open_reply is False
    print("test_tool_call_turn_is_not_an_open_reply OK")


# ── Addressing by id ──────────────────────────────────────────────────────────

def test_index_of_and_not_found():
    s = sess(U("u1"), A("a1"), U("u2"))
    assert s.index_of("u2") == 2
    try:
        s.index_of("nope")
    except MessageNotFound:
        print("test_index_of_and_not_found OK")
        return
    raise AssertionError("should have raised MessageNotFound")


# ── Truncation misfire guard: the criterion is "would user output be lost" ───

def test_truncate_after_drops_assistant_freely():
    # The normal case for regeneration: only the assistant's reply is dropped, no consent needed
    s = sess(U("u1"), A("a1"))
    assert s.truncate_after("u1") == 1
    assert [m["_msg_id"] for m in s.messages] == ["u1"]
    print("test_truncate_after_drops_assistant_freely OK")


def test_truncate_after_blocks_when_user_messages_lost():
    s = sess(U("u1"), A("a1"), U("u2"), A("a2"), U("u3"))
    try:
        s.truncate_after("u1")
    except TruncationNeedsConsent as e:
        assert e.dropped == 4
        assert e.dropped_user_messages == 2       # u2 / u3
        assert e.total == 5
        assert len(s.messages) == 5               # no consent -> nothing changed
        print("test_truncate_after_blocks_when_user_messages_lost OK")
        return
    raise AssertionError("should have required consent")


def test_truncate_after_proceeds_with_consent():
    s = sess(U("u1"), A("a1"), U("u2"), A("a2"))
    assert s.truncate_after("u1", consent=True) == 3
    assert [m["_msg_id"] for m in s.messages] == ["u1"]
    print("test_truncate_after_proceeds_with_consent OK")


def test_meta_user_messages_are_not_user_output():
    # System-injected meta messages (task events, skill body, ...) also have role user, but they are not user output
    s = sess(U("u1"), A("a1"), U("m1", meta=True))
    assert s.truncate_after("u1") == 2             # no consent needed
    print("test_meta_user_messages_are_not_user_output OK")


def test_truncate_before_includes_target():
    s = sess(U("u1"), A("a1"), U("u2"), A("a2"))
    assert s.truncate_before("u2", consent=True) == 2
    assert [m["_msg_id"] for m in s.messages] == ["u1", "a1"]
    print("test_truncate_before_includes_target OK")


def test_truncate_before_target_user_counts_as_loss():
    # Regenerate after editing: the target user message itself gets dropped, so consent is required by default too
    s = sess(U("u1"), A("a1"))
    try:
        s.truncate_before("u1")
    except TruncationNeedsConsent as e:
        assert e.dropped_user_messages == 1
        print("test_truncate_before_target_user_counts_as_loss OK")
        return
    raise AssertionError("should have required consent")


# ── Query: only a "contribution" counts as a Query ───────────────────────────

def test_query_emptiness():
    assert Query().is_empty is True                       # empty = continue running from the current state
    assert Query.text("  ").is_empty is True
    assert Query.text("hi").is_empty is False
    assert Query(attachments=[{"url": "x"}]).is_empty is False
    assert Query(refs=[{"doc": "d1"}]).is_empty is False
    assert Query(content=[{"type": "text"}]).is_empty is False
    print("test_query_emptiness OK")


def test_query_contributor_and_initiation():
    assert Query.text("hi").by is Contributor.USER
    assert Query.answer(["yes"]).by is Contributor.ANSWER
    assert Query.event({"task": "done"}).by is Contributor.SYSTEM_EVENT
    assert Query.text("hi").is_user_initiated is True
    assert Query.answer(["yes"]).is_user_initiated is True   # an answer is also something the user is waiting on
    assert Query.event({}).is_user_initiated is False       # nobody is waiting
    print("test_query_contributor_and_initiation OK")


def test_guard_defaults_off():
    """The switch defaults to off -- a protection that needs product-layer cooperation shouldn't trip up someone who hasn't adopted it."""
    s = Session("c1", messages=[U("u1"), A("a1"), U("u2")])   # off by default
    assert s.truncate_after("u1") == 2          # drops u2 too, without blocking
    assert [m["_msg_id"] for m in s.messages] == ["u1"]
    assert Session("c2").guard_user_message_loss is False     # off by default: someone who hasn't adopted it won't be blocked for no reason
    print("test_guard_defaults_off OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} SESSION/QUERY TESTS PASSED")
