from __future__ import annotations

from dataclasses import asdict, is_dataclass

import pytest

from browser_use import Tab, TabManager
from browser_use.browser import EventBus
from browser_use.browser.events import TabClosedEvent, TabCreatedEvent, TabSwitchedEvent
from browser_use.tools import Tools


def test_tab_manager_is_public_snapshot_api() -> None:
    assert is_dataclass(Tab)
    data = asdict(Tab(id="tab-a", url="https://example.test/a", title="A", active=True, parent_id="root"))

    for field in ["id", "url", "title", "active", "parent_id", "created_at", "last_active"]:
        assert field in data

    manager = TabManager()
    for method_name in [
        "open_tab",
        "close_tab",
        "switch_tab",
        "list_tabs",
        "get_tab",
        "get_active_tab",
        "preserve_context",
        "get_preserved_context",
    ]:
        assert hasattr(manager, method_name)


@pytest.mark.asyncio
async def test_tab_manager_lifecycle_context_and_events() -> None:
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    manager = TabManager(event_bus=bus)

    first = await manager.open_tab("https://example.test/one", title="One")
    second = await manager.open_tab("https://example.test/two", title="Two", focus=False, parent_id=first.id)
    assert [(tab.id, tab.active, tab.parent_id) for tab in manager.list_tabs()] == [
        (first.id, True, None),
        (second.id, False, first.id),
    ]

    manager.preserve_context(first.id, {"dom_hash": "one", "focused_index": 3})
    switched = await manager.switch_tab(second.id)
    assert switched.id == second.id
    assert manager.get_active_tab().id == second.id
    assert manager.get_preserved_context(first.id) == {"dom_hash": "one", "focused_index": 3}

    closed = await manager.close_tab(second.id)
    assert closed.id == second.id
    assert manager.get_active_tab().id == first.id

    assert [event.__class__ for event in events] == [
        TabCreatedEvent,
        TabCreatedEvent,
        TabSwitchedEvent,
        TabClosedEvent,
    ]
    assert events[1].parent_id == first.id
    assert events[2].previous_tab_id == first.id
    assert await bus.wait_for("TabSwitchedEvent", timeout=0.1) is events[2]


@pytest.mark.asyncio
async def test_tab_tool_actions_are_registered_and_executable() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []
            self.tabs = [
                {"id": "tab-1", "url": "https://example.test/one", "title": "One", "active": True, "parent_id": None}
            ]

        async def open_tab(self, url: str, focus: bool = True) -> dict[str, object]:
            self.calls.append(("open_tab", url, focus))
            tab = {"id": "tab-2", "url": url, "title": "Two", "active": focus, "parent_id": "tab-1"}
            self.tabs.append(tab)
            return tab

        async def close_tab(self, tab_id: str) -> dict[str, object]:
            self.calls.append(("close_tab", tab_id))
            self.tabs = [tab for tab in self.tabs if tab["id"] != tab_id]
            return {"id": tab_id, "url": "https://example.test/two", "title": "Two", "active": False, "parent_id": "tab-1"}

        async def switch_tab(self, tab_id: str) -> dict[str, object]:
            self.calls.append(("switch_tab", tab_id))
            for tab in self.tabs:
                tab["active"] = tab["id"] == tab_id
            return next(tab for tab in self.tabs if tab["id"] == tab_id)

        def list_tabs(self) -> list[dict[str, object]]:
            self.calls.append(("list_tabs",))
            return list(self.tabs)

    tools = Tools()
    metadata = {action["name"]: action for action in tools.list_actions()}
    assert {"open_tab", "close_tab", "switch_tab", "list_tabs"} <= metadata.keys()
    assert metadata["open_tab"]["schema"]["properties"]["focus"]["default"] is True
    assert metadata["close_tab"]["schema"]["required"] == ["tab_id"]

    session = FakeSession()
    opened = await tools.execute_action(
        {"open_tab": {"url": "https://example.test/two", "focus": False}},
        browser_session=session,
    )
    listed = await tools.execute_action({"list_tabs": {}}, browser_session=session)
    switched = await tools.execute_action({"switch_tab": {"tab_id": "tab-2"}}, browser_session=session)
    closed = await tools.execute_action({"close_tab": {"tab_id": "tab-2"}}, browser_session=session)

    assert opened["tab_id"] == "tab-2"
    assert listed["tabs"][1]["id"] == "tab-2"
    assert switched["active"] is True
    assert closed["tab_id"] == "tab-2"
    assert session.calls == [
        ("open_tab", "https://example.test/two", False),
        ("list_tabs",),
        ("switch_tab", "tab-2"),
        ("close_tab", "tab-2"),
    ]
