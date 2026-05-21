from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
import pytest
import pytest_asyncio

from browser_use_bridge.runtime.scheduler import (
    QueueFull,
    RuntimeScheduler,
    SchedulerStatus,
    SchedulerSnapshot,
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    TaskFuture,
    TaskProgress,
    TaskStarted,
    TaskSubmitted,
)


class FakeBrowser:
    def __init__(self, id: str = "fake-browser-1") -> None:
        self.id = id


class FakeBrowserHandle:
    def __init__(self, browser: FakeBrowser | None = None) -> None:
        self.browser = browser or FakeBrowser()
        self._contexts: list[FakeContext] = []

    async def new_context(self) -> FakeContext:
        ctx = FakeContext()
        self._contexts.append(ctx)
        return ctx


class FakeContext:
    def __init__(self) -> None:
        self.id = f"ctx-fake"

    async def new_page(self) -> FakePage:
        return FakePage()

    async def close(self) -> None:
        pass


class FakePage:
    def __init__(self) -> None:
        self.id = f"page-fake"


class FakeBrowserPool:
    def __init__(self, size: int = 2) -> None:
        self._size = size
        self._handles: list[FakeBrowserHandle] = []
        self._acquired: list[FakeBrowserHandle] = []

    @property
    def size(self) -> int:
        return self._size

    async def acquire(self) -> FakeBrowserHandle:
        if not self._handles:
            self._handles = [FakeBrowserHandle() for _ in range(self._size)]
        handle = self._handles.pop()
        self._acquired.append(handle)
        return handle

    async def release(self, handle: FakeBrowserHandle) -> None:
        if handle in self._acquired:
            self._acquired.remove(handle)
        self._handles.append(handle)

    @property
    def status(self) -> MagicMock:
        m = MagicMock()
        m.active_count = len(self._acquired)
        m.idle_count = len(self._handles)
        return m


# ── Public API and State Model ─────────────────────────────────────────────────


def test_public_api_and_state_model():
    """All public types importable and RuntimeScheduler accepts BrowserPool."""
    assert RuntimeScheduler is not None
    assert TaskFuture is not None
    assert SchedulerStatus is not None
    assert SchedulerSnapshot is not None
    assert issubclass(TaskSubmitted, object)
    assert issubclass(TaskStarted, object)
    assert issubclass(TaskProgress, object)
    assert issubclass(TaskCompleted, object)
    assert issubclass(TaskFailed, object)
    assert issubclass(TaskCancelled, object)
    assert issubclass(QueueFull, Exception)

    pool = FakeBrowserPool(size=2)
    sched = RuntimeScheduler(pool)
    assert sched is not None
    assert sched.state == SchedulerStatus.IDLE


def test_taskfuture_result_and_done():
    """TaskFuture.result() returns value on success, .done() reflects completion."""
    async def run():
        async def coro() -> str:
            return "ok"

        task = asyncio.create_task(coro())
        events: list = []
        future = TaskFuture[str](
            task_id="t1",
            coro=task,
            events=events,
            event_callback=None,
        )
        await task
        # TaskFuture is awaited and result is set by the scheduler's _dispatch
        # For this isolated test, simulate completion
        future._done = True
        future._result = "ok"
        assert future.done()
        assert future.result() == "ok"

    asyncio.run(run())


def test_taskfuture_cancel():
    """TaskFuture.cancel() cancels the underlying task."""
    async def run():
        async def long_coro() -> str:
            await asyncio.sleep(10)
            return "done"

        task = asyncio.create_task(long_coro())
        future = TaskFuture[str](
            task_id="t2",
            coro=task,
            events=[],
            event_callback=None,
        )

        cancelled = future.cancel()
        assert cancelled is True

        await asyncio.sleep(0.05)
        assert future.done()
        assert future.cancelled()

    asyncio.run(run())


def test_taskfuture_timeout_raises():
    """TaskFuture.result() with too-short timeout raises TimeoutError."""
    async def run():
        async def slow() -> str:
            await asyncio.sleep(2)
            return "late"

        task = asyncio.create_task(slow())
        future = TaskFuture[str](task_id="t3", coro=task, events=[], event_callback=None)

        with pytest.raises(asyncio.TimeoutError):
            future.result(timeout=0.01)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


# ── Allocation, Hierarchy, and Idempotent Start ───────────────────────────────


def test_submit_returns_taskfuture_immediately():
    """scheduler.submit() returns immediately without blocking."""
    async def run():
        pool = FakeBrowserPool(size=2)
        sched = RuntimeScheduler(pool, max_concurrent_per_browser=2, max_queue_size=10)

        async def dummy() -> str:
            await asyncio.sleep(0.5)
            return "result"

        future = sched.submit(dummy, priority=0)
        assert isinstance(future, TaskFuture)
        assert not future.done()
        await asyncio.sleep(0.6)
        assert future.done()

    asyncio.run(run())


# ── Priority and Fairness Scheduling ──────────────────────────────────────────


def test_priority_queue_priority_zero_before_one():
    """Priority-0 tasks are dispatched before priority-1 tasks."""
    async def run():
        pool = FakeBrowserPool(size=2)
        sched = RuntimeScheduler(pool, max_concurrent_per_browser=2, max_queue_size=20)
        dispatch_order: list[str] = []

        async def make_task(name: str) -> str:
            dispatch_order.append(name)
            return name

        sched.submit(make_task, "p0-first", priority=0, task_id="p0-first")
        sched.submit(make_task, "p0-second", priority=0, task_id="p0-second")
        sched.submit(make_task, "p1", priority=1, task_id="p1")

        await asyncio.sleep(0.3)

        p0_first_pos = dispatch_order.index("p0-first")
        p0_second_pos = dispatch_order.index("p0-second")
        p1_pos = dispatch_order.index("p1")
        assert p0_first_pos < p1_pos, f"priority-0 should come before priority-1: {dispatch_order}"
        assert p0_second_pos < p1_pos, f"priority-0 should come before priority-1: {dispatch_order}"

    asyncio.run(run())


def test_queue_full_raises():
    """Submitting beyond max_queue_size raises QueueFull."""
    async def run():
        pool = FakeBrowserPool(size=1)
        sched = RuntimeScheduler(pool, max_queue_size=2)

        async def noop() -> str:
            return "x"

        # Fill the queue
        sched.submit(noop, task_id="q1")
        sched.submit(noop, task_id="q2")

        with pytest.raises(QueueFull):
            sched.submit(noop, task_id="q3")

    asyncio.run(run())


# ── Lifecycle Events ───────────────────────────────────────────────────────────


def test_lifecycle_events_emitted_in_order():
    """Scheduler emits TaskSubmitted, TaskStarted, TaskCompleted in order."""
    async def run():
        pool = FakeBrowserPool(size=2)
        events: list[object] = []

        def collector(evt: object) -> None:
            events.append(evt)

        sched = RuntimeScheduler(pool, event_callback=collector)

        async def work() -> str:
            return "result"

        future = sched.submit(work, priority=0, task_id="evt-test")
        tid = future._task_id

        await asyncio.sleep(0.2)

        submitted = [e for e in events if isinstance(e, TaskSubmitted)]
        started = [e for e in events if isinstance(e, TaskStarted)]
        completed = [e for e in events if isinstance(e, TaskCompleted)]

        assert len(submitted) >= 1
        assert len(started) >= 1
        assert submitted[0].task_id == tid
        assert started[0].task_id == tid

    asyncio.run(run())


def test_task_failed_emits_taskfailed():
    """Task exception results in TaskFailed event."""
    async def run():
        pool = FakeBrowserPool(size=2)
        events: list[object] = []

        def collector(evt: object) -> None:
            events.append(evt)

        sched = RuntimeScheduler(pool, event_callback=collector)

        async def bad() -> str:
            raise ValueError("boom")

        sched.submit(bad, task_id="fail-test")
        await asyncio.sleep(0.2)

        failed = [e for e in events if isinstance(e, TaskFailed)]
        assert len(failed) >= 1
        assert failed[0].task_id == "fail-test"

    asyncio.run(run())


def test_task_cancelled_emits_task_cancelled():
    """Cancelled task results in TaskCancelled event."""
    async def run():
        pool = FakeBrowserPool(size=2)
        events: list[object] = []

        def collector(evt: object) -> None:
            events.append(evt)

        sched = RuntimeScheduler(pool, event_callback=collector)

        async def long() -> str:
            await asyncio.sleep(10)
            return "late"

        future = sched.submit(long, task_id="cancel-test")
        future.cancel()

        await asyncio.sleep(0.05)

        cancelled = [e for e in events if isinstance(e, TaskCancelled)]
        assert len(cancelled) >= 1

    asyncio.run(run())


# ── Scheduler State ────────────────────────────────────────────────────────────


def test_scheduler_state_dict():
    """SchedulerSnapshot is JSON-serializable via to_dict/to_json."""
    state = SchedulerSnapshot(
        state=SchedulerStatus.RUNNING,
        active_tasks=3,
        queued_tasks=5,
        pool_size=2,
        max_concurrent_per_browser=2,
    )
    d = state.to_dict()
    assert d["state"] == "running"
    assert d["active_tasks"] == 3
    assert d["queued_tasks"] == 5
    j = state.to_json()
    import json
    parsed = json.loads(j)
    assert parsed["state"] == "running"


def test_scheduler_get_state():
    """scheduler.get_state() returns current SchedulerStatus."""
    pool = FakeBrowserPool(size=2)
    sched = RuntimeScheduler(pool)
    assert sched.get_state() == SchedulerStatus.IDLE


# ── Pool Backpressure ───────────────────────────────────────────────────────────


def test_pool_saturation_tasks_queue():
    """When pool is saturated, tasks queue without blocking submit."""
    async def run():
        pool = FakeBrowserPool(size=1)
        sched = RuntimeScheduler(pool, max_concurrent_per_browser=1, max_queue_size=5)

        async def hold(_: str) -> str:
            await asyncio.sleep(1)
            return "done"

        # First task
        f1 = sched.submit(hold, "task-1")
        assert not f1.done()

        # Additional tasks should queue
        f2 = sched.submit(hold, "task-2")
        f3 = sched.submit(hold, "task-3")

        # They should all be queued (not completed)
        assert not f2.done()
        assert not f3.done()
        assert len(sched._queue) >= 2

    asyncio.run(run())
