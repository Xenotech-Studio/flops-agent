"""Local HTTP/runtime acceptance for replaceable reasoning-only attempts."""
import asyncio
import json
import time
from contextlib import asynccontextmanager

import pytest

from flops_agent import Runtime, Session, Query, RunStatus, MemoryInbox
from flops_agent.engine.runner import Runner
from flops_agent.engine.runtime import LLMStreamRetryPolicy
from flops_agent.entities import events as ev
from flops_agent.entities.contracts import TextStreamChunk, ReasoningStreamChunk, ToolCallStreamChunk, FinishStreamChunk
from flops_agent.providers.openai import OpenAIStreamClient


@pytest.fixture(autouse=True)
def local_http_only(monkeypatch):
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy', '127.0.0.1,localhost')


def frame(delta=None, usage=None, finish=None):
    obj = {"choices": [{"delta": delta or {}, "finish_reason": finish}]}
    if usage is not None:
        obj["usage"] = usage
    return 'data: ' + json.dumps(obj) + '\n\n'


class HttpFixture:
    def __init__(self, initial='', preheaders=False, first_status=200):
        self.initial, self.preheaders, self.first_status = initial, preheaders, first_status
        self.requests = []
        self.accepted = asyncio.Queue()
        self.closed = []
        self.release = asyncio.Event()
        self.handlers = set()
        self.final = frame({'content': 'new answer'}) + frame(finish='stop') + 'data: [DONE]\n\n'
        self.server = None

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        closed = asyncio.Event()
        try:
            header = await reader.readuntil(b'\r\n\r\n')
            length = next(int(h.split(b':', 1)[1]) for h in header.split(b'\r\n') if h.lower().startswith(b'content-length:'))
            self.requests.append(json.loads(await reader.readexactly(length)))
            index = len(self.requests)
            self.closed.append(closed)
            if index == 1 and self.preheaders:
                await self.accepted.put(index)
                await reader.read()
                return
            status = self.first_status if index == 1 else 200
            writer.write(f'HTTP/1.1 {status} OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n'.encode())
            if status != 200:
                writer.write(b'{"error":"fixture unavailable"}')
                await writer.drain()
                await self.accepted.put(index)
                return
            if index == 1:
                writer.write(self.initial.encode())
                await writer.drain()
                await self.accepted.put(index)
                eof = asyncio.create_task(reader.read())
                release = asyncio.create_task(self.release.wait())
                try:
                    await asyncio.wait([eof, release], return_when=asyncio.FIRST_COMPLETED)
                    if release.done():
                        writer.write(self.final.encode())
                        await writer.drain()
                finally:
                    for t in (eof, release):
                        if not t.done(): t.cancel()
                    await asyncio.gather(eof, release, return_exceptions=True)
            else:
                await self.accepted.put(index)
                writer.write(self.final.encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()
            self.handlers.discard(task)

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0)
        self.url = 'http://127.0.0.1:' + str(self.server.sockets[0].getsockname()[1]) + '/v1'
        return self

    async def __aexit__(self, *_):
        self.release.set()
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.handlers): task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)


class TrackingRunner(Runner):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.resets = 0
        self.reset_ready = asyncio.Event()
        self.chunks_seen = asyncio.Queue()
        kw['runtime'].observed_runner = self

    async def on_chunk(self, chunk):
        await super().on_chunk(chunk)
        self.chunks_seen.put_nowait(chunk)

    async def on_llm_steer(self):
        self.resets += 1
        self.reset_ready.set()
        await super().on_llm_steer()


def runtime_for(upstream, **kwargs):
    return Runtime(llm=OpenAIStreamClient(api_key='local-fixture', model='fixture', base_url=upstream.url), runner=TrackingRunner, **kwargs)


@pytest.mark.parametrize('initial,preheaders', [
    ('', True), ('', False),
    (frame({'role': 'assistant', 'content': ''}), False),
    (frame(usage={'prompt_tokens': 77, 'completion_tokens': 999}), False),
    (frame({'reasoning_content': 'temporary thought'}), False),
    (frame({'tool_calls': [{'index': 0, 'function': {}}]}), False),
])
def test_http_wait_is_closed_and_reissued_without_first_return(initial, preheaders):
    async def case():
        async with HttpFixture(initial, preheaders) as upstream:
            rt = runtime_for(upstream)
            session = Session('early')
            run = rt.start(session, Query.text('original'))
            assert await asyncio.wait_for(upstream.accepted.get(), 2) == 1
            # Let the actual seam consume initial telemetry/reasoning before steering.
            if 'temporary thought' in initial or 'prompt_tokens' in initial or 'tool_calls' in initial:
                await asyncio.wait_for(rt.observed_runner.chunks_seen.get(), 1)
            started = time.monotonic()
            rt.deliver('early', {'role': 'user', 'content': 'additional'}, when='step')
            assert await asyncio.wait_for(upstream.accepted.get(), 2) == 2
            elapsed = time.monotonic() - started
            assert elapsed < 1.5
            await asyncio.wait_for(upstream.closed[0].wait(), 1)
            assert not upstream.release.is_set()
            assert await asyncio.wait_for(run.wait(), 2) is RunStatus.DONE
            assert [m['content'] for m in upstream.requests[1]['messages']] == ['original', 'additional']
            assert [m['content'] for m in session.messages] == ['original', 'additional', 'new answer']
            assert session.messages[-1].get('reasoning_content', '') == ''
            assert rt.observed_runner.usage is None
            assert rt.observed_runner.resets == 1
            assert not any([isinstance(d.event, ev.LLMStreamRetrying) async for d in run])
            print(f'local HTTP preheaders={preheaders} cancel/reissue_ms={elapsed*1000:.1f}')
    asyncio.run(case())


@pytest.mark.parametrize('delta', [
    {'content': 'visible'},
    {'tool_calls': [{'index': 0, 'id': 'call_fixture', 'function': {}}]},
    {'tool_calls': [{'index': 0, 'function': {'name': 'probe'}}]},
    {'tool_calls': [{'index': 0, 'function': {'arguments': '{}'}}]},
])
def test_reasoning_then_text_or_tool_commits_attempt(delta):
    async def case():
        async with HttpFixture(frame({'reasoning_content': 'thought'}) + frame(delta)) as upstream:
            rt = runtime_for(upstream)
            session = Session('committed')
            run = rt.start(session, Query.text('original'))
            await asyncio.wait_for(upstream.accepted.get(), 2)
            await asyncio.wait_for(rt.observed_runner.chunks_seen.get(), 1)
            await asyncio.wait_for(rt.observed_runner.chunks_seen.get(), 1)
            rt.deliver('committed', {'role': 'user', 'content': 'later'}, when='step')
            # A barrier on the consumer event loop, not a timing-based sleep.
            for _ in range(4):
                await asyncio.sleep(0)
            assert len(upstream.requests) == 1
            assert not upstream.closed[0].is_set()
            upstream.release.set()
            assert await asyncio.wait_for(run.wait(), 2) is RunStatus.DONE
            assert rt.observed_runner.resets == 0
            assert sum(m.get('content') == 'later' for m in session.messages) == 1
    asyncio.run(case())


def test_stop_wins_and_input_stays_queued():
    async def case():
        async with HttpFixture(frame({'reasoning_content': 'temporary'})) as upstream:
            rt = runtime_for(upstream)
            session = Session('stop')
            run = rt.start(session, Query.text('original'))
            await asyncio.wait_for(upstream.accepted.get(), 2)
            rt.deliver('stop', {'role': 'user', 'content': 'pending'}, when='step')
            await run.stop()
            assert await asyncio.wait_for(run.wait(), 2) is RunStatus.STOPPED
            assert len(upstream.requests) == 1
            assert [m['content'] for m in await rt.inbox.poll(session, 'turn')] == ['pending']
            await asyncio.wait_for(upstream.closed[0].wait(), 1)
    asyncio.run(case())


def test_real_http_error_backoff_can_be_steered():
    async def case():
        async with HttpFixture(first_status=503) as upstream:
            rt = runtime_for(upstream, llm_stream_retry=LLMStreamRetryPolicy(max_attempts=2, backoffs=(10,), is_retriable=lambda exc: getattr(getattr(exc, "response", None), "status_code", None) == 503))
            session = Session('backoff')
            run = rt.start(session, Query.text('original'))
            async for d in run:
                if isinstance(d.event, ev.LLMStreamRetrying):
                    break
            started = time.monotonic()
            rt.deliver('backoff', {'role': 'user', 'content': 'during backoff'}, when='step')
            assert await asyncio.wait_for(run.wait(), 2) is RunStatus.DONE
            assert time.monotonic()-started < 1.5
            assert len(upstream.requests) == 2
            assert upstream.requests[1]['messages'][-1]['content'] == 'during backoff'
            assert [d.event.attempt async for d in run if isinstance(d.event, ev.LLMStreamRetrying)] == [1]
    asyncio.run(case())


def test_late_cancel_resistant_chunk_never_reaches_accounting():
    async def case():
        ready = asyncio.Event()
        cleanup = asyncio.Event()
        class Client:
            calls = 0
            async def acompletion(self, **kw):
                self.calls += 1
                attempt = self.calls
                async def stream():
                    if attempt == 1:
                        ready.set()
                        yield ReasoningStreamChunk('discard')
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            yield TextStreamChunk('late must not leak')
                        finally:
                            cleanup.set()
                    else:
                        assert cleanup.is_set()
                        yield TextStreamChunk('accepted')
                return stream()
        rt = Runtime(llm=Client(), runner=TrackingRunner)
        session = Session('late')
        run = rt.start(session, Query.text('original'))
        await ready.wait()
        rt.deliver('late', {'role': 'user', 'content': 'one'}, when='step')
        rt.deliver('late', {'role': 'user', 'content': 'two'}, when='step')
        assert await asyncio.wait_for(run.wait(), 1) is RunStatus.DONE
        assert [m['content'] for m in session.messages] == ['original', 'one', 'two', 'accepted']
        assert not any([isinstance(d.event, ev.TextDelta) and 'late' in d.event.text async for d in run])
    asyncio.run(case())


@pytest.mark.parametrize('steer_first', [True, False])
def test_same_tick_first_text_vs_steer_has_defined_gate(steer_first):
    async def case():
        ready, output = asyncio.Event(), asyncio.Event()
        class Client:
            calls = 0
            async def acompletion(self, **kw):
                self.calls += 1
                n = self.calls
                async def stream():
                    if n == 1:
                        ready.set()
                        yield ReasoningStreamChunk('temporary')
                        await output.wait()
                        yield TextStreamChunk('first')
                        await asyncio.Event().wait()
                    else:
                        yield TextStreamChunk('second')
                return stream()
        rt = Runtime(llm=Client(), runner=TrackingRunner)
        session = Session('race')
        run = rt.start(session, Query.text('original'))
        await ready.wait()
        if steer_first:
            rt.deliver('race', {'role': 'user', 'content': 'injection'}, when='step')
            # Wait for cancellation, then release the obsolete generation.
            await asyncio.wait_for(rt.observed_runner.reset_ready.wait(), 1)
            output.set()
            assert await asyncio.wait_for(run.wait(), 1) is RunStatus.DONE
        else:
            output.set()
            await asyncio.wait_for(rt.observed_runner.chunks_seen.get(), 1)
            await asyncio.wait_for(rt.observed_runner.chunks_seen.get(), 1)
            rt.deliver('race', {'role': 'user', 'content': 'injection'}, when='step')
            await asyncio.sleep(0)
            assert rt.observed_runner.resets == 0
            await run.stop()
            await run.wait()
    asyncio.run(case())


def test_http_tool_execution_is_not_preempted_or_duplicated():
    async def case():
        started, release_tool = asyncio.Event(), asyncio.Event()
        calls = []
        async def probe():
            calls.append('executed')
            started.set()
            await release_tool.wait()
            return {'ok': True}
        tool = {'tool_calls': [{'index': 0, 'id': 'call1', 'function': {'name': 'probe', 'arguments': '{}'}}]}
        async with HttpFixture(frame(tool) + frame(finish='tool_calls')) as upstream:
            rt = runtime_for(upstream, tools=[{'schema': {'type':'function', 'function':{'name':'probe','parameters':{'type':'object','properties':{}}}}, 'fn': probe}])
            s = Session('tool-running');run = rt.start(s, Query.text('original'))
            await asyncio.wait_for(upstream.accepted.get(), 2)
            upstream.release.set()
            await asyncio.wait_for(started.wait(), 1)
            rt.deliver('tool-running', {'role':'user','content':'arrived during tool'}, when='step')
            assert calls == ['executed'] and len(upstream.requests) == 1
            release_tool.set()
            assert await asyncio.wait_for(run.wait(), 2) is RunStatus.DONE
            assert calls == ['executed'] and rt.observed_runner.resets == 0
            assert sum(m['role'] == 'tool' for m in s.messages) == 1
            assert sum(m.get('content') == 'arrived during tool' for m in s.messages) == 1
            assert upstream.requests[1]['messages'][-1]['content'] == 'arrived during tool'
    asyncio.run(case())


def test_second_steer_while_closing_coalesces_in_order_and_open_can_cancel():
    async def case():
        opened, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        seen = []
        class Client:
            async def acompletion(self, **request):
                seen.append([dict(m) for m in request['messages']])
                if len(seen) == 1:
                    opened.set()
                    try:
                        await asyncio.Event().wait()  # inside acompletion, before handle
                    except asyncio.CancelledError:
                        closing.set()
                        await release.wait()
                        raise
                async def stream(): yield TextStreamChunk('new')
                return stream()
        rt = Runtime(llm=Client(), runner=TrackingRunner)
        s = Session('coalesce');run = rt.start(s, Query.text('original'))
        await opened.wait()
        rt.deliver('coalesce', {'role':'user', 'content':'one'}, when='step')
        await asyncio.wait_for(closing.wait(), 1)
        rt.deliver('coalesce', {'role':'user', 'content':'two'}, when='step')
        release.set()
        assert await asyncio.wait_for(run.wait(), 1) is RunStatus.DONE
        assert len(seen) == 2
        assert [m['content'] for m in seen[1]] == ['original', 'one', 'two']
    asyncio.run(case())


@pytest.mark.parametrize('chunk', [TextStreamChunk('body'), ToolCallStreamChunk(index=0, id='valid-id')])
def test_gate_is_closed_before_awaiting_chunk_hook_even_with_ready_steer(chunk):
    async def case():
        in_hook, release = asyncio.Event(), asyncio.Event()
        class Client:
            calls = 0
            async def acompletion(self, **request):
                self.calls += 1
                async def stream():
                    yield ReasoningStreamChunk('summary')
                    yield chunk
                    await asyncio.Event().wait()
                return stream()
        class HookRunner(TrackingRunner):
            async def on_chunk(self, item):
                if item is chunk:
                    in_hook.set();await release.wait()
                await super().on_chunk(item)
        rt = Runtime(llm=Client(), runner=HookRunner)
        s = Session('hook-race');run = rt.start(s, Query.text('original'))
        await in_hook.wait()
        rt.deliver('hook-race', {'role':'user','content':'new'}, when='step')
        assert rt.observed_runner._steer_output_started
        release.set()
        await asyncio.wait_for(rt.observed_runner.chunks_seen.get(), 1)
        await asyncio.wait_for(rt.observed_runner.chunks_seen.get(), 1)
        await run.stop();await asyncio.wait_for(run.wait(), 1)
        assert rt.llm.calls == 1 and rt.observed_runner.resets == 0
        assert (await rt.inbox.poll(s, 'turn'))[0]['content'] == 'new'
    asyncio.run(case())


def test_failed_close_does_not_start_a_second_generation_or_consume_input():
    async def case():
        opened = asyncio.Event()
        class BrokenClose:
            def __aiter__(self):return self
            async def __anext__(self):
                opened.set();await asyncio.Event().wait()
            async def aclose(self):raise RuntimeError('fixture close failed')
        class Client:
            calls=0
            async def acompletion(self, **request):
                self.calls+=1;return BrokenClose()
        rt=Runtime(llm=Client(), llm_stream_retry=LLMStreamRetryPolicy(is_retriable=lambda exc: True))
        s=Session('bad-close');run=rt.start(s, Query.text('original'))
        await opened.wait();rt.deliver('bad-close', {'role':'user','content':'pending'},when='step')
        assert await asyncio.wait_for(run.wait(),1) is RunStatus.FAILED
        assert rt.llm.calls==1
        assert (await rt.inbox.poll(s,'turn'))[0]['content']=='pending'
        assert [m['role'] for m in s.messages]==['user']
    asyncio.run(case())
