from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from browser_use.browser.session import BrowserSession


class BaseWatchdog:
    def __init__(self, session: "BrowserSession") -> None:
        self.session = session
        self.is_running = False

    async def start(self) -> None:
        self.is_running = True

    async def stop(self) -> None:
        self.is_running = False


class PopupWatchdog(BaseWatchdog):
    async def start(self) -> None:
        await super().start()
        context = self.session._context
        if context is None:
            return
        context.on("page", self._attach_page)
        for page in context.pages:
            self._attach_page(page)

    def _attach_page(self, page: Any) -> None:
        page.on("dialog", lambda dialog: asyncio.create_task(self._accept_dialog(dialog)))

    async def _accept_dialog(self, dialog: Any) -> None:
        with contextlib.suppress(Exception):
            await dialog.accept()


class CrashWatchdog(BaseWatchdog):
    async def start(self) -> None:
        await super().start()


__all__ = ["BaseWatchdog", "CrashWatchdog", "PopupWatchdog"]
