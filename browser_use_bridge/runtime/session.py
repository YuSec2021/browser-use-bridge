from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from browser_use_bridge.browser.pool import BrowserHandle, BrowserPool


class SessionLifecycleState(str, Enum):
    PENDING = "pending"
    IDLE = "idle"
    ACTIVE = "active"
    RECOVERING = "recovering"
    ENDED = "ended"
    ERROR = "error"


class SessionCleanupPolicy(str, Enum):
    CLOSE = "close"
    CLEAR = "clear"
    PRESERVE = "preserve"


@dataclass(frozen=True)
class SessionState:
    task_id: str
    session_id: str
    browser_id: str | None = None
    context_id: str | None = None
    page_id: str | None = None
    created_at: str = field(default_factory=lambda: _utc_now())
    last_active: str = field(default_factory=lambda: _utc_now())
    state: SessionLifecycleState = SessionLifecycleState.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", SessionLifecycleState(self.state))
        object.__setattr__(self, "metadata", _json_safe(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["metadata"] = _json_safe(self.metadata)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SessionState":
        data = dict(payload)
        data["state"] = SessionLifecycleState(data.get("state", SessionLifecycleState.PENDING))
        data["metadata"] = _json_safe(data.get("metadata") or {})
        return cls(**data)

    @classmethod
    def from_json(cls, payload: str) -> "SessionState":
        return cls.from_dict(json.loads(payload))


@dataclass(frozen=True)
class SessionLifecycleEvent:
    task_id: str
    session_id: str
    browser_id: str | None = None
    context_id: str | None = None
    page_id: str | None = None
    timestamp: str = field(default_factory=lambda: _utc_now())


@dataclass(frozen=True)
class SessionStarted(SessionLifecycleEvent):
    pass


@dataclass(frozen=True)
class SessionEnded(SessionLifecycleEvent):
    cleanup: str = SessionCleanupPolicy.CLOSE.value


@dataclass(frozen=True)
class SessionError(SessionLifecycleEvent):
    error: str = ""


@dataclass(frozen=True)
class SessionRecovered(SessionLifecycleEvent):
    pass


EventCallback = Callable[[SessionLifecycleEvent], Any]


class SessionEventBus:
    """Small async event surface compatible with bubus-style subscribe/dispatch flows."""

    def __init__(self) -> None:
        self._callbacks: list[EventCallback] = []
        self._events: list[SessionLifecycleEvent] = []
        self._waiters: list[tuple[str, asyncio.Future[SessionLifecycleEvent]]] = []

    @property
    def events(self) -> list[SessionLifecycleEvent]:
        return list(self._events)

    def subscribe(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    async def emit(self, event: SessionLifecycleEvent) -> None:
        self._events.append(event)
        event_name = event.__class__.__name__

        remaining: list[tuple[str, asyncio.Future[SessionLifecycleEvent]]] = []
        for expected_name, future in self._waiters:
            if not future.done() and expected_name == event_name:
                future.set_result(event)
            elif not future.done():
                remaining.append((expected_name, future))
        self._waiters = remaining

        for callback in list(self._callbacks):
            result = callback(event)
            if inspect.isawaitable(result):
                await result

    async def publish(self, event: SessionLifecycleEvent) -> None:
        await self.emit(event)

    async def dispatch(self, event: SessionLifecycleEvent) -> None:
        await self.emit(event)

    async def wait_for(
        self,
        event: str | type[SessionLifecycleEvent],
        timeout: float | None = None,
    ) -> SessionLifecycleEvent:
        event_name = event if isinstance(event, str) else event.__name__
        for emitted in reversed(self._events):
            if emitted.__class__.__name__ == event_name:
                return emitted

        loop = asyncio.get_running_loop()
        future: asyncio.Future[SessionLifecycleEvent] = loop.create_future()
        self._waiters.append((event_name, future))
        return await asyncio.wait_for(future, timeout=timeout)


class Session:
    """Task-bound browser session allocated from one BrowserPool browser lease."""

    def __init__(
        self,
        pool: BrowserPool | None = None,
        task_id: str | None = None,
        *,
        browser_pool: BrowserPool | None = None,
        session_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        cleanup: str | SessionCleanupPolicy = SessionCleanupPolicy.CLOSE,
        event_bus: SessionEventBus | None = None,
        state: SessionState | Mapping[str, Any] | None = None,
    ) -> None:
        resolved_pool = pool or browser_pool
        if resolved_pool is None:
            raise TypeError("Session requires a BrowserPool via pool or browser_pool")
        if task_id is None and state is None:
            raise TypeError("Session requires task_id when state is not provided")

        self.pool = resolved_pool
        self.cleanup = _coerce_cleanup(cleanup)
        self.event_bus = event_bus or SessionEventBus()

        if state is not None:
            restored = state if isinstance(state, SessionState) else SessionState.from_dict(state)
            self._state = restored
        else:
            self._state = SessionState(
                task_id=str(task_id),
                session_id=session_id or f"session-{uuid.uuid4().hex}",
                metadata=_json_safe(metadata or {}),
            )

        self._browser_handle: BrowserHandle | None = None
        self._context: Any = None
        self._page: Any = None
        self._end_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def task_id(self) -> str:
        return self._state.task_id

    @property
    def session_id(self) -> str:
        return self._state.session_id

    @property
    def browser_handle(self) -> BrowserHandle | None:
        return self._browser_handle

    @property
    def browser(self) -> Any:
        return self._browser_handle.browser if self._browser_handle is not None else None

    @property
    def context(self) -> Any:
        return self._context

    @property
    def page(self) -> Any:
        return self._page

    @property
    def active_page(self) -> Any:
        return self._page

    def subscribe(self, callback: EventCallback) -> None:
        self.event_bus.subscribe(callback)

    def on_event(self, callback: EventCallback) -> None:
        self.subscribe(callback)

    async def wait_for_event(
        self,
        event: str | type[SessionLifecycleEvent],
        timeout: float | None = None,
    ) -> SessionLifecycleEvent:
        return await self.event_bus.wait_for(event, timeout=timeout)

    async def start(self) -> "Session":
        async with self._start_lock:
            if self._state.state == SessionLifecycleState.ACTIVE:
                return self
            if self._browser_handle is not None:
                return self
            await self._allocate(SessionStarted, SessionLifecycleState.ACTIVE)
            return self

    async def end(
        self,
        *,
        cleanup: str | SessionCleanupPolicy | None = None,
        preserve_context: bool | None = None,
    ) -> "Session":
        async with self._end_lock:
            if self._state.state == SessionLifecycleState.ENDED:
                return self

            handle = self._browser_handle
            if preserve_context:
                policy = SessionCleanupPolicy.PRESERVE
            else:
                policy = _coerce_cleanup(cleanup or self.cleanup)
            if handle is not None:
                try:
                    await self._cleanup_context(policy)
                finally:
                    await self.pool.release(handle)
                    self._browser_handle = None
                    if policy != SessionCleanupPolicy.PRESERVE:
                        self._context = None
                        self._page = None

            self._replace_state(state=SessionLifecycleState.ENDED)
            await self.event_bus.emit(
                SessionEnded(
                    task_id=self.task_id,
                    session_id=self.session_id,
                    browser_id=self._state.browser_id,
                    context_id=self._state.context_id,
                    page_id=self._state.page_id,
                    cleanup=policy.value,
                )
            )
            return self

    async def recover(self, pool: BrowserPool | None = None) -> "Session":
        async with self._start_lock:
            if (
                self._state.state == SessionLifecycleState.ACTIVE
                and self._browser_handle is not None
            ):
                return self
            if pool is not None:
                self.pool = pool
            self._replace_state(state=SessionLifecycleState.RECOVERING, error=None)
            await self._allocate(SessionRecovered, SessionLifecycleState.ACTIVE)
            return self

    def to_state(self) -> SessionState:
        return self._state

    def to_json(self) -> str:
        return self._state.to_json()

    @classmethod
    def from_state(
        cls,
        state: SessionState | Mapping[str, Any] | str,
        pool: BrowserPool,
        *,
        cleanup: str | SessionCleanupPolicy = SessionCleanupPolicy.CLOSE,
        event_bus: SessionEventBus | None = None,
    ) -> "Session":
        if isinstance(state, str):
            restored = SessionState.from_json(state)
        elif isinstance(state, SessionState):
            restored = state
        else:
            restored = SessionState.from_dict(state)
        return cls(
            pool=pool,
            task_id=restored.task_id,
            cleanup=cleanup,
            event_bus=event_bus,
            state=restored,
        )

    async def _allocate(
        self,
        event_type: type[SessionStarted] | type[SessionRecovered],
        success_state: SessionLifecycleState,
    ) -> None:
        handle: BrowserHandle | None = None
        context: Any = None
        page: Any = None
        try:
            handle = await self.pool.acquire()
            browser = handle.browser
            new_context = getattr(browser, "new_context", None)
            if not callable(new_context):
                raise RuntimeError("Acquired browser does not support new_context()")
            context = await _maybe_await(new_context())

            new_page = getattr(context, "new_page", None)
            if not callable(new_page):
                raise RuntimeError("Session context does not support new_page()")
            page = await _maybe_await(new_page())

            self._browser_handle = handle
            self._context = context
            self._page = page
            self._replace_state(
                browser_id=handle.id,
                context_id=_object_identity(context, "context"),
                page_id=_object_identity(page, "page"),
                state=success_state,
                error=None,
            )
            await self.event_bus.emit(
                event_type(
                    task_id=self.task_id,
                    session_id=self.session_id,
                    browser_id=self._state.browser_id,
                    context_id=self._state.context_id,
                    page_id=self._state.page_id,
                )
            )
        except Exception as exc:
            if context is not None:
                with contextlib.suppress(Exception):
                    await _maybe_await(context.close())
            if handle is not None:
                with contextlib.suppress(Exception):
                    await self.pool.release(handle)
            self._browser_handle = None
            self._context = None
            self._page = None
            self._replace_state(
                browser_id=getattr(handle, "id", self._state.browser_id),
                state=SessionLifecycleState.ERROR,
                error=str(exc),
            )
            await self.event_bus.emit(
                SessionError(
                    task_id=self.task_id,
                    session_id=self.session_id,
                    browser_id=self._state.browser_id,
                    context_id=self._state.context_id,
                    page_id=self._state.page_id,
                    error=str(exc),
                )
            )
            raise

    async def _cleanup_context(self, policy: SessionCleanupPolicy) -> None:
        if self._context is None or policy == SessionCleanupPolicy.PRESERVE:
            return
        if policy == SessionCleanupPolicy.CLEAR:
            clear_cookies = getattr(self._context, "clear_cookies", None)
            if callable(clear_cookies):
                await _maybe_await(clear_cookies())
                return
        close = getattr(self._context, "close", None)
        if callable(close):
            await _maybe_await(close())

    def _replace_state(self, **updates: Any) -> None:
        payload = self._state.to_dict()
        payload.update(updates)
        payload["last_active"] = _utc_now()
        if "metadata" in payload:
            payload["metadata"] = _json_safe(payload["metadata"])
        self._state = SessionState.from_dict(payload)


def _coerce_cleanup(value: str | SessionCleanupPolicy) -> SessionCleanupPolicy:
    if isinstance(value, SessionCleanupPolicy):
        return value
    normalized = str(value).lower()
    if normalized in {"reuse", "keep", "none", "preserve"}:
        return SessionCleanupPolicy.PRESERVE
    return SessionCleanupPolicy(normalized)


def _object_identity(value: Any, prefix: str) -> str:
    for attr in ("id", "guid", "_guid"):
        candidate = getattr(value, attr, None)
        if candidate:
            return str(candidate)
    return f"{prefix}-{uuid.uuid4().hex}"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    return str(value)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "Session",
    "SessionCleanupPolicy",
    "SessionError",
    "SessionEnded",
    "SessionEventBus",
    "SessionLifecycleEvent",
    "SessionLifecycleState",
    "SessionRecovered",
    "SessionStarted",
    "SessionState",
]
