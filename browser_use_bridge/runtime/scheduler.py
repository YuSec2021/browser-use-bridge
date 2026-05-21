from __future__ import annotations

import asyncio
import heapq
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class SchedulerStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DRAINING = "draining"
    STOPPED = "stopped"


class QueueFull(Exception):
    """Raised when task submission would exceed the configured queue size limit."""
    pass


SchedulerStatus = SchedulerStatus  # alias for backward compatibility
SchedulerState = SchedulerStatus    # alias per contract


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class SchedulerSnapshot:
    state: SchedulerStatus
    active_tasks: int
    queued_tasks: int
    pool_size: int
    max_concurrent_per_browser: int
    timestamp: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "active_tasks": self.active_tasks,
            "queued_tasks": self.queued_tasks,
            "pool_size": self.pool_size,
            "max_concurrent_per_browser": self.max_concurrent_per_browser,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SchedulerSnapshot:
        return SchedulerSnapshot(
            state=SchedulerStatus(data.get("state", SchedulerStatus.IDLE)),
            active_tasks=data.get("active_tasks", 0),
            queued_tasks=data.get("queued_tasks", 0),
            pool_size=data.get("pool_size", 0),
            max_concurrent_per_browser=data.get("max_concurrent_per_browser", 1),
            timestamp=data.get("timestamp", _utc_now()),
        )


@dataclass(frozen=True)
class SchedulerEvent:
    task_id: str
    timestamp: str = field(default_factory=_utc_now)


@dataclass(frozen=True)
class TaskSubmitted(SchedulerEvent):
    priority: int = 0


@dataclass(frozen=True)
class TaskStarted(SchedulerEvent):
    pass


@dataclass(frozen=True)
class TaskProgress(SchedulerEvent):
    step: str = ""


@dataclass(frozen=True)
class TaskCompleted(SchedulerEvent):
    result: Any = None


@dataclass(frozen=True)
class TaskFailed(SchedulerEvent):
    error: str = ""


@dataclass(frozen=True)
class TaskCancelled(SchedulerEvent):
    pass


@dataclass
class _QueuedTask:
    priority: int
    counter: int
    task_id: str
    coro: asyncio.Task
    future: TaskFuture
    submitted_at: float

    def __lt__(self, other: _QueuedTask) -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.counter < other.counter


class TaskFuture(Generic[T]):
    """An awaitable future for a scheduled task with cancellation and timeout support."""

    def __init__(
        self,
        task_id: str,
        coro: asyncio.Task[T],
        events: list[SchedulerEvent],
        event_callback: Callable[[SchedulerEvent], None] | None = None,
    ) -> None:
        self._task_id = task_id
        self._coro = coro
        self._events = events
        self._event_callback = event_callback
        self._result: T | None = None
        self._exc: BaseException | None = None
        self._cancelled = False
        self._done = False
        self._loop = coro.get_loop()
        coro.add_done_callback(self._on_done)

    def _on_done(self, _: asyncio.Task[T]) -> None:
        if self._done:
            return
        self._done = True
        try:
            exc = self._coro.exception()
        except asyncio.CancelledError:
            self._cancelled = True
            return
        if self._coro.cancelled():
            self._cancelled = True
        elif exc is not None:
            self._exc = exc
        else:
            self._result = self._coro.result()

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> bool:
        if self._done:
            return False
        self._cancelled = True
        self._done = True
        self._coro.cancel()
        return True

    def result(self, timeout: float | None = None) -> T:
        if self._exc is not None:
            raise self._exc
        if self._cancelled:
            raise asyncio.CancelledError()
        if self._done:
            assert self._result is not None
            return self._result
        if timeout is not None:
            import sys
            import time
            start = time.monotonic()
            while not self._done:
                if time.monotonic() - start >= timeout:
                    raise asyncio.TimeoutError()
                if sys.platform == "win32":
                    import threading
                    threading.Event().wait(min(0.01, timeout))
                else:
                    time.sleep(0.01)
            if self._exc is not None:
                raise self._exc
            if self._cancelled:
                raise asyncio.CancelledError()
            assert self._result is not None
            return self._result
        raise asyncio.TimeoutError("TaskFuture.result() requires await or timeout")

    def __await__(self) -> Any:
        while not self._done:
            yield from asyncio.sleep(0.01).__await__()
        if self._exc is not None:
            raise self._exc
        if self._cancelled:
            raise asyncio.CancelledError()
        assert self._result is not None
        return self._result


class RuntimeScheduler:
    """Dispatches tasks to the browser pool with priority queuing and fairness scheduling."""

    def __init__(
        self,
        pool,
        *,
        max_concurrent_per_browser: int = 1,
        max_queue_size: int = 100,
        event_callback: Callable[[SchedulerEvent], None] | None = None,
    ) -> None:
        self._pool = pool
        self._max_concurrent_per_browser = max_concurrent_per_browser
        self._max_queue_size = max_queue_size
        self._event_callback = event_callback
        self._queue: list[_QueuedTask] = []
        self._tasks: dict[str, _QueuedTask] = {}
        self._state = SchedulerStatus.IDLE
        self._counter = 0
        self._lock = asyncio.Lock()
        self._worker_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> SchedulerStatus:
        return self._state

    def _emit(self, event: SchedulerEvent) -> None:
        if self._event_callback:
            self._event_callback(event)

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, bytes):
            return f"<bytes {len(value)}>"
        if hasattr(value, "__class__") and value.__class__.__name__ == "function":
            return f"<function {value.__name__}>"
        return repr(value)

    def submit(
        self,
        task_fn: Callable[..., asyncio.Coroutine[Any, Any, T]],
        *args: Any,
        priority: int = 0,
        task_id: str | None = None,
    ) -> TaskFuture[T]:
        tid = task_id or str(uuid.uuid4())
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def _run() -> T:
            self._emit(TaskStarted(task_id=tid))
            return await task_fn(*args)

        task = loop.create_task(_run())
        events: list[SchedulerEvent] = []
        future = TaskFuture[T](
            task_id=tid,
            coro=task,
            events=events,
            event_callback=self._emit,
        )

        queued = _QueuedTask(
            priority=priority,
            counter=self._counter,
            task_id=tid,
            coro=task,
            future=future,
            submitted_at=loop.time(),
        )
        self._counter += 1

        if len(self._queue) >= self._max_queue_size:
            raise QueueFull(f"Queue limit reached ({self._max_queue_size})")

        heapq.heappush(self._queue, queued)
        self._tasks[tid] = queued
        self._emit(TaskSubmitted(task_id=tid, priority=priority))

        if self._state == SchedulerStatus.IDLE:
            self._start_worker(loop)

        return future

    def _start_worker(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._state = SchedulerStatus.RUNNING
            self._worker_task = loop.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        idle_count = 0
        while idle_count < 10:
            if not self._queue:
                idle_count += 1
                await asyncio.sleep(0.05)
                continue
            idle_count = 0
            queued = heapq.heappop(self._queue)
            await self._dispatch(queued)
            if not self._queue and self._state == SchedulerStatus.DRAINING:
                break
        self._state = SchedulerStatus.IDLE

    async def _dispatch(self, queued: _QueuedTask) -> None:
        exc = None
        result = None
        try:
            result = await queued.coro
        except asyncio.CancelledError:
            self._emit(TaskCancelled(task_id=queued.task_id))
        except BaseException as e:
            exc = e

        if exc is not None:
            self._emit(TaskFailed(task_id=queued.task_id, error=str(exc)))
            queued.future._exc = exc
            queued.future._done = True
        elif not queued.future._cancelled:
            self._emit(TaskCompleted(task_id=queued.task_id, result=self._json_safe(result)))
            queued.future._result = result
            queued.future._done = True

        self._tasks.pop(queued.task_id, None)

    async def _stop(self) -> None:
        async with self._lock:
            self._state = SchedulerStatus.DRAINING
            while self._tasks:
                await asyncio.sleep(0.05)
            self._state = SchedulerStatus.STOPPED

    def get_state(self) -> SchedulerStatus:
        return self._state

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "active_tasks": sum(1 for q in self._tasks.values() if not q.coro.done()),
            "queued_tasks": len(self._queue),
            "pool_size": getattr(self._pool, "size", 0),
            "max_concurrent_per_browser": self._max_concurrent_per_browser,
            "timestamp": _utc_now(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)
