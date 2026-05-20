from browser_use_bridge.browser.pool import BrowserHandle, BrowserPool, ChromeLauncher, PoolStatus
from browser_use_bridge.config import BrowserProfile

__all__ = [
    "BrowserHandle",
    "BrowserPool",
    "BrowserProfile",
    "ChromeLauncher",
    "PoolStatus",
]

try:
    from browser_use_bridge.browser.events import (
        BrowserConnectedEvent,
        BrowserCrashedEvent,
        BrowserDisconnectedEvent,
        BrowserEvent,
        BrowserSecurityError,
        DomUpdatedEvent,
        TabClosedEvent,
        TabCreatedEvent,
        TabSwitchedEvent,
    )
    from browser_use_bridge.browser.session import BrowserSession, BrowserTab, EventBus, SessionManager
    from browser_use_bridge.browser.views import BrowserStateSummary
except ImportError:
    pass
else:
    __all__.extend(
        [
            "BrowserConnectedEvent",
            "BrowserCrashedEvent",
            "BrowserDisconnectedEvent",
            "BrowserEvent",
            "BrowserSecurityError",
            "BrowserSession",
            "BrowserStateSummary",
            "BrowserTab",
            "DomUpdatedEvent",
            "EventBus",
            "SessionManager",
            "TabClosedEvent",
            "TabCreatedEvent",
            "TabSwitchedEvent",
        ]
    )
