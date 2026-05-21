from __future__ import annotations

import asyncio
import json
from dataclasses import is_dataclass
from typing import Any

import pytest

from browser_use_bridge.browser.pool import BrowserHandle
from browser_use_bridge.config import BrowserProfile
from browser_use_bridge.runtime import Session as RuntimeSession
from browser_use_bridge.runtime.session import (
    Session,
    SessionCleanupPolicy,
    SessionEnded,
    SessionError,
    SessionLifecycleState,
    SessionRecovered,
    SessionStarted,
    SessionState,
)


class FakePage:
    def __init__(self, page_id: str) -> None:
        self.id = page_id


class FakeContext:
    def __init__(self, context_id: str, *, fail_page: bool = False) -> None:
        self.id = context_id
        self.fail_page = fail_page
        self.new_page_calls = 0
        self.closed = False
        self.cookies_cleared = False
        self.pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        self.new_page_calls += 1
        if self.fail_page:
            raise RuntimeError("page creation failed")
        page = FakePage(f"page-{self.id}-{self.new_page_calls}")
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True

    async def clear_cookies(self) -> None:
        self.cookies_cleared = True


class FakeBrowser:
    def __init__(
        self,
        browser_id: str,
        *,
        fail_context: bool = False,
        fail_page: bool = False,
    ) -> None:
        self.browser_id = browser_id
        self.fail_context = fail_context
        self.fail_page = fail_page
        self.new_context_calls = 0
        self.contexts: list[FakeContext] = []

    async def new_context(self) -> FakeContext:
        self.new_context_calls += 1
        if self.fail_context:
            raise RuntimeError("context creation failed")
        context = FakeContext(
            f"context-{self.browser_id}-{self.new_context_calls}",
            fail_page=self.fail_page,
        )
        self.contexts.append(context)
        return context


class FakePool:
    def __init__(self, *, fail_context: bool = False, fail_page: bool = False) -> None:
        self.browser = FakeBrowser("browser-1", fail_context=fail_context, fail_page=fail_page)
        self.handle = BrowserHandle(
            id="browser-1",
            slot_index=0,
            cdp_url="http://127.0.0.1:9222",
            browser=self.browser,
            profile=BrowserProfile(),
            user_data_dir="/tmp/fake-browser",
        )
        self.acquire_calls = 0
        self.release_calls = 0
        self.active = False

    async def acquire(self) -> BrowserHandle:
        self.acquire_calls += 1
        if self.active:
            raise RuntimeError("pool is saturated")
        self.active = True
        return self.handle

    async def release(self, handle: BrowserHandle) -> None:
        assert handle is self.handle
        if self.active:
            self.release_calls += 1
        self.active = False

    def status(self) -> Any:
        class Status:
            pass

        self_ref = self
        status = Status()
        status.active_count = 1 if self_ref.active else 0
        status.idle_count = 0 if self_ref.active else 1
        return status


def test_public_api_and_state_model() -> None:
    assert RuntimeSession is Session
    assert is_dataclass(SessionState)
    state = SessionState(
        task_id="task-1",
        session_id="session-1",
        browser_id="browser-1",
        context_id="context-1",
        page_id="page-1",
        state=SessionLifecycleState.ACTIVE,
        metadata={"user": "alice"},
    )

    payload = state.to_dict()
    assert payload["task_id"] == "task-1"
    assert payload["state"] == "active"
    assert payload["metadata"] == {"user": "alice"}
    assert SessionState.from_json(state.to_json()) == state


def test_allocation_hierarchy_and_idempotent_start() -> None:
    async def run() -> tuple[Session, FakePool]:
        pool = FakePool()
        session = Session(pool=pool, task_id="task-1", metadata={"source": "test"})
        await session.start()
        await session.start()
        return session, pool

    session, pool = asyncio.run(run())

    assert pool.status().active_count == 1
    assert pool.status().idle_count == 0
    assert pool.acquire_calls == 1
    assert pool.browser.new_context_calls == 1
    assert pool.browser.contexts[0].new_page_calls == 1
    assert session.state.task_id == "task-1"
    assert session.state.session_id.startswith("session-")
    assert session.state.browser_id == "browser-1"
    assert session.state.context_id == "context-browser-1-1"
    assert session.state.page_id == "page-context-browser-1-1-1"
    assert session.state.state == SessionLifecycleState.ACTIVE
    assert session.context is pool.browser.contexts[0]
    assert session.active_page is pool.browser.contexts[0].pages[0]


def test_end_releases_pool_and_default_cleanup_is_idempotent() -> None:
    async def run() -> tuple[Session, FakePool, FakeContext]:
        pool = FakePool()
        session = Session(pool=pool, task_id="task-1")
        await session.start()
        context = session.context
        await session.end()
        await session.end()
        return session, pool, context

    session, pool, context = asyncio.run(run())

    assert context.closed is True
    assert pool.release_calls == 1
    assert pool.status().active_count == 0
    assert pool.status().idle_count == 1
    assert session.state.state == SessionLifecycleState.ENDED
    assert session.state.browser_id == "browser-1"
    assert session.state.context_id == "context-browser-1-1"
    assert session.state.page_id == "page-context-browser-1-1-1"


def test_preserve_cleanup_keeps_context_but_releases_browser() -> None:
    async def run() -> tuple[Session, FakePool, FakeContext]:
        pool = FakePool()
        session = Session(pool=pool, task_id="task-1", cleanup=SessionCleanupPolicy.PRESERVE)
        await session.start()
        context = session.context
        await session.end()
        return session, pool, context

    session, pool, context = asyncio.run(run())

    assert context.closed is False
    assert session.context is context
    assert pool.release_calls == 1
    assert session.state.state == SessionLifecycleState.ENDED


def test_lifecycle_events_are_emitted_in_order() -> None:
    async def run() -> list[Any]:
        events: list[Any] = []
        pool = FakePool()
        session = Session(pool=pool, task_id="task-1")
        session.subscribe(events.append)
        await session.start()
        await session.end()
        return events

    events = asyncio.run(run())

    assert [event.__class__ for event in events] == [SessionStarted, SessionEnded]
    assert events[0].task_id == "task-1"
    assert events[0].browser_id == "browser-1"
    assert events[0].context_id == "context-browser-1-1"
    assert events[0].page_id == "page-context-browser-1-1-1"
    assert events[1].browser_id == "browser-1"


def test_error_during_page_creation_releases_browser_and_emits_event() -> None:
    async def run() -> tuple[Session, FakePool, list[Any]]:
        events: list[Any] = []
        pool = FakePool(fail_page=True)
        session = Session(pool=pool, task_id="task-1")
        session.subscribe(events.append)
        with pytest.raises(RuntimeError, match="page creation failed"):
            await session.start()
        return session, pool, events

    session, pool, events = asyncio.run(run())

    assert pool.release_calls == 1
    assert pool.status().active_count == 0
    assert session.state.state == SessionLifecycleState.ERROR
    assert session.state.error == "page creation failed"
    assert [event.__class__ for event in events] == [SessionError]
    assert events[0].error == "page creation failed"


def test_recovery_emits_recovered_and_preserves_session_id() -> None:
    async def run() -> tuple[Session, list[Any]]:
        events: list[Any] = []
        pool = FakePool()
        state = SessionState(
            task_id="task-1",
            session_id="session-fixed",
            state=SessionLifecycleState.ERROR,
        )
        session = Session.from_state(state, pool, cleanup=SessionCleanupPolicy.PRESERVE)
        session.subscribe(events.append)
        await session.recover()
        return session, events

    session, events = asyncio.run(run())

    assert session.state.session_id == "session-fixed"
    assert session.state.state == SessionLifecycleState.ACTIVE
    assert session.state.browser_id == "browser-1"
    assert [event.__class__ for event in events] == [SessionRecovered]


def test_persistence_json_is_scalar_and_rehydratable() -> None:
    async def run() -> tuple[str, SessionState]:
        pool = FakePool()
        session = Session(
            pool=pool,
            task_id="task-1",
            metadata={"nested": {"n": 1}, "callback": lambda: None},
        )
        await session.start()
        return session.to_json(), session.to_state()

    payload, state = asyncio.run(run())
    decoded = json.loads(payload)
    restored = SessionState.from_json(payload)

    assert decoded["task_id"] == "task-1"
    assert decoded["session_id"] == state.session_id
    assert decoded["browser_id"] == "browser-1"
    assert decoded["context_id"] == "context-browser-1-1"
    assert decoded["page_id"] == "page-context-browser-1-1-1"
    assert decoded["state"] == "active"
    assert decoded["metadata"]["nested"] == {"n": 1}
    assert isinstance(decoded["metadata"]["callback"], str)
    assert restored == state
