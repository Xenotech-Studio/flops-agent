"""Agent entity unit tests -- how "who this assistant is" gets into the prompt, and when memory gets updated.

Two main threads:
1. **The direction and order in which identity enters the request**: Runner *reads* from Agent
   (persona / recall / tools / model), and organizes it into the system message via the
   overridable ``Runner.build_system_prompt`` (persona first, memory after, only one message);
   Agent has no idea what the request looks like.
2. **The failure direction for memory** (if it can't be read, remember a little less -- never
   turn an otherwise normal reply into an error).
"""
import asyncio
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import Agent, Query, Runner, Runtime, Session  # noqa: E402


class FakeMemory:
    def __init__(self, text="remembers the user's name is Alice", explode_recall=False, explode_remember=False):
        self.text = text
        self.explode_recall = explode_recall
        self.explode_remember = explode_remember
        self.remembered = []
        self.recalled_queries = []

    async def recall(self, session, *, query=None):
        if self.explode_recall:
            raise RuntimeError("memory backend crashed")
        self.recalled_queries.append(query)
        return self.text

    async def remember(self, session):
        if self.explode_remember:
            raise RuntimeError("distillation failed")
        self.remembered.append(session)


def T(name):
    return {"type": "function", "function": {"name": name}}


def build(agent=None, *, messages=None, tools=None, model=None, query=None, runner_cls=Runner):
    """Assemble one request through the real Runner.build_request (build_request only reads runtime/session/query)."""
    runtime = Runtime(agent=agent, tools=tools, model=model, runner=runner_cls)
    session = Session("s1", messages=list(messages or []))
    runner = runner_cls(runtime=runtime, session=session,
                        query=Query.text(query) if query else None, run=SN())
    return asyncio.run(runner.build_request()), session


def compose(agent=None, **kw):
    return build(agent, **kw)[0]


def system_of(request):
    msgs = request["messages"]
    return msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else None


# ── Identity into the prompt ─────────────────────────────────────────────────

def test_instructions_become_the_system_message():
    r = compose(Agent(instructions="You are a rigorous assistant"))
    assert system_of(r) == "You are a rigorous assistant"
    print("test_instructions_become_the_system_message OK")


def test_persona_comes_before_memory():
    """The persona is a stable self-description, memory is a fact that changes over time.

    Reversing the order would make the persona look like it's correcting the memory that was just read.
    """
    r = compose(Agent(instructions="You are an assistant", memory=FakeMemory("the user's name is Alice")))
    assert system_of(r).index("You are an assistant") < system_of(r).index("the user's name is Alice")
    print("test_persona_comes_before_memory OK")


def test_existing_system_message_is_merged_not_duplicated():
    """Multiple system messages are handled inconsistently across providers (merged / only the first
    one honored / error) -- whether the persona takes effect shouldn't depend on which model is swapped in."""
    r = compose(Agent(instructions="You are an assistant"),
                messages=[{"role": "system", "content": "pre-existing instructions"},
                          {"role": "user", "content": "hi"}])
    roles = [m["role"] for m in r["messages"]]
    assert roles.count("system") == 1
    assert "You are an assistant" in system_of(r) and "pre-existing instructions" in system_of(r)
    print("test_existing_system_message_is_merged_not_duplicated OK")


def test_no_system_message_added_when_there_is_nothing_to_say():
    """With no persona and no memory, an empty system message shouldn't be inserted -- that's pure noise."""
    r = compose(Agent(), messages=[{"role": "user", "content": "hi"}])
    assert [m["role"] for m in r["messages"]] == ["user"]
    print("test_no_system_message_added_when_there_is_nothing_to_say OK")


def test_history_order_is_preserved():
    r = compose(Agent(instructions="P"),
                messages=[{"role": "user", "content": "1"},
                          {"role": "assistant", "content": "2"}])
    assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant"]
    print("test_history_order_is_preserved OK")


def test_identity_never_touches_the_stored_history():
    """Identity is a wire-layer concern: it's injected into the request but **never written into**
    session history -- otherwise every step persisted to storage would add another system message,
    growing without bound."""
    history = [{"role": "system", "content": "pre-existing instructions"}, {"role": "user", "content": "hi"}]
    r, session = build(Agent(instructions="You are an assistant"), messages=history)
    assert "You are an assistant" in system_of(r)
    assert session.messages == history, "session history should be unchanged"
    print("test_identity_never_touches_the_stored_history OK")


# ── Two override points: Agent supplies the text, Runner decides the organization ──

def test_persona_is_an_override_point_on_agent():
    """Products whose persona varies per session or needs to be read from storage should override
    persona() rather than modify Runner."""
    class DynamicAgent(Agent):
        async def persona(self, session, *, query=None):
            return f"You are the dedicated assistant for {session.session_id}"

    r = compose(DynamicAgent(instructions="(the static persona should be ignored)"))
    assert system_of(r) == "You are the dedicated assistant for s1"
    print("test_persona_is_an_override_point_on_agent OK")


def test_build_system_prompt_is_an_override_point_on_runner():
    """"How the system message is organized" and "how the request is assembled" are not the same
    layer: a product that wants to add its own section / change the separator overrides
    build_system_prompt, without needing to touch build_request."""
    class BracketRunner(Runner):
        def build_system_prompt(self, agent_soul, agent_memory):
            return f"[{agent_soul}|{agent_memory}|platform rules]"

    r = compose(Agent(instructions="P", memory=FakeMemory("M")), runner_cls=BracketRunner)
    assert system_of(r) == "[P|M|platform rules]"
    print("test_build_system_prompt_is_an_override_point_on_runner OK")


# ── Memory recall ─────────────────────────────────────────────────────────────

def test_recall_sees_the_current_query():
    """Being able to see the current turn's query is what lets a product fetch only the relevant
    part of memory instead of stuffing in everything every time."""
    mem = FakeMemory()
    compose(Agent(memory=mem), query="what's the weather today")
    assert [q.content for q in mem.recalled_queries] == ["what's the weather today"]
    print("test_recall_sees_the_current_query OK")


def test_recall_failure_degrades_instead_of_breaking_the_turn():
    """A reply where memory couldn't be recalled reads like "I'm a bit forgetful today"; a reply
    that errors out when recall fails reads like "this feature is broken".

    The former is still usable by the user, the latter isn't -- and the memory backend is often
    an optional component anyway.
    """
    r = compose(Agent(instructions="You are an assistant", memory=FakeMemory(explode_recall=True)))
    assert system_of(r) == "You are an assistant", "the persona is unaffected, only memory is missing"
    print("test_recall_failure_degrades_instead_of_breaking_the_turn OK")


def test_empty_memory_adds_nothing():
    r = compose(Agent(instructions="P", memory=FakeMemory("   ")))
    assert system_of(r) == "P"
    print("test_empty_memory_adds_nothing OK")


# ── Memory updates ────────────────────────────────────────────────────────────

def test_remember_runs_in_the_background():
    """Must not be awaited -- remember is often several LLM calls, and waiting on it would make
    the subscriber's stream wait several extra seconds, with the UI still spinning after the
    user has already finished reading the reply."""
    mem = FakeMemory()

    async def _go():
        agent = Agent(memory=mem)
        task = agent.schedule_remember({"sid": "s1"})
        assert mem.remembered == [], "should not already be done at call time -- that would mean it was awaited synchronously"
        await task
        return mem.remembered

    assert asyncio.run(_go()) == [{"sid": "s1"}]
    print("test_remember_runs_in_the_background OK")


def test_remember_failure_is_swallowed():
    """A failed memory update just means remembering a bit less next time; letting it turn an
    otherwise normal reply into an error would be putting the cart before the horse."""
    async def _go():
        agent = Agent(memory=FakeMemory(explode_remember=True))
        await agent.schedule_remember({})      # should not raise

    asyncio.run(_go())
    print("test_remember_failure_is_swallowed OK")


def test_no_memory_means_nothing_scheduled():
    async def _go():
        return Agent().schedule_remember({})

    assert asyncio.run(_go()) is None
    print("test_no_memory_means_nothing_scheduled OK")


def test_scheduling_outside_an_event_loop_is_a_noop():
    """A product might tear down in a synchronous context -- this shouldn't blow up because of that."""
    assert Agent(memory=FakeMemory()).schedule_remember({}) is None
    print("test_scheduling_outside_an_event_loop_is_a_noop OK")


# ── Seam with the loop ────────────────────────────────────────────────────────

def test_runner_only_remembers_completed_turns():
    """A turn that was interrupted/failed has incomplete content; distilling it would write
    half-baked facts into long-term memory."""
    import inspect

    from flops_agent.engine.runner import Runner

    src = inspect.getsource(Runner.finalize)
    assert "schedule_remember" in src and "RunStatus.DONE" in src
    print("test_runner_only_remembers_completed_turns OK")


def test_session_agent_wins_over_runtime_agent():
    """A single process can host multiple personas -- the session-level one must be able to override the runtime-level one."""
    from flops_agent.engine.runner import Runner

    runner = Runner.__new__(Runner)
    runner.session = type("S", (), {"agent": "session-agent"})()
    runner.runtime = type("R", (), {"agent": "runtime-agent"})()
    assert runner.agent == "session-agent"

    runner.session = type("S", (), {"agent": None})()
    assert runner.agent == "runtime-agent"
    print("test_session_agent_wins_over_runtime_agent OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} AGENT TESTS PASSED")
