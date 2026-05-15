from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class BrowserSecurityError(RuntimeError):
    """Raised when browser security policy blocks an operation."""


@dataclass
class BrowserEvent:
    session: Any


@dataclass
class BrowserConnectedEvent(BrowserEvent):
    cdp_url: str | None = None


@dataclass
class BrowserDisconnectedEvent(BrowserEvent):
    pass


@dataclass
class BrowserCrashedEvent(BrowserEvent):
    pid: int | None = None


@dataclass
class TabCreatedEvent(BrowserEvent):
    tab_id: str = ""
    url: str = ""
    title: str = ""
    parent_id: str | None = None
    active: bool = False


@dataclass
class TabClosedEvent(BrowserEvent):
    tab_id: str = ""
    url: str = ""
    title: str = ""
    active: bool = False


@dataclass
class TabSwitchedEvent(BrowserEvent):
    tab_id: str = ""
    previous_tab_id: str | None = None
    url: str = ""
    title: str = ""


@dataclass
class DomUpdatedEvent(BrowserEvent):
    url: str = ""
    title: str = ""


__all__ = [
    "BrowserConnectedEvent",
    "BrowserCrashedEvent",
    "BrowserDisconnectedEvent",
    "BrowserEvent",
    "BrowserSecurityError",
    "DomUpdatedEvent",
    "TabClosedEvent",
    "TabCreatedEvent",
    "TabSwitchedEvent",
]
