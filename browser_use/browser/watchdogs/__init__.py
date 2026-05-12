from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
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


class DownloadWatchdog(BaseWatchdog):
    async def start(self) -> None:
        await super().start()
        context = self.session._context
        if context is None:
            return
        context.on("page", self._attach_page)
        for page in context.pages:
            self._attach_page(page)

    def _attach_page(self, page: Any) -> None:
        setattr(page, "_browser_use_session", self.session)
        page.on("download", lambda download: asyncio.create_task(self._save_download(download)))
        asyncio.create_task(self._allow_downloads(page))

    async def _allow_downloads(self, page: Any) -> None:
        downloads_path = getattr(self.session.profile, "downloads_path", None)
        if not downloads_path:
            return
        Path(downloads_path).mkdir(parents=True, exist_ok=True)
        context = getattr(page, "context", None)
        if context is None:
            return
        cdp_session = None
        with contextlib.suppress(Exception):
            cdp_session = await context.new_cdp_session(page)
        if cdp_session is None:
            return
        try:
            with contextlib.suppress(Exception):
                await cdp_session.send(
                    "Browser.setDownloadBehavior",
                    {
                        "behavior": "allow",
                        "downloadPath": str(downloads_path),
                        "eventsEnabled": True,
                    },
                )
            with contextlib.suppress(Exception):
                await cdp_session.send(
                    "Page.setDownloadBehavior",
                    {"behavior": "allow", "downloadPath": str(downloads_path)},
                )
        finally:
            with contextlib.suppress(Exception):
                await cdp_session.detach()

    async def _save_download(self, download: Any) -> None:
        downloads_path = getattr(self.session.profile, "downloads_path", None)
        if not downloads_path:
            return
        target = Path(downloads_path) / download.suggested_filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Exception):
            await download.save_as(str(target))


__all__ = ["BaseWatchdog", "CrashWatchdog", "DownloadWatchdog", "PopupWatchdog"]
