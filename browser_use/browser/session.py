from __future__ import annotations

import asyncio
import contextlib
import inspect
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from browser_use.config import BrowserProfile

from .events import (
    BrowserConnectedEvent,
    BrowserCrashedEvent,
    BrowserDisconnectedEvent,
    BrowserEvent,
    BrowserSecurityError,
    TabClosedEvent,
    TabCreatedEvent,
)


EventCallback = Callable[[BrowserEvent], Any]


@dataclass
class BrowserTab:
    id: str
    url: str = "about:blank"
    title: str = ""
    page: Any = None


class EventBus:
    """Small async-friendly event bus matching the event surface needed here."""

    def __init__(self) -> None:
        self._callbacks: list[EventCallback] = []
        self._events: list[BrowserEvent] = []
        self._waiters: list[tuple[str, asyncio.Future[BrowserEvent]]] = []

    def subscribe(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    def emit(self, event: BrowserEvent) -> None:
        self._events.append(event)
        event_name = event.__class__.__name__

        remaining: list[tuple[str, asyncio.Future[BrowserEvent]]] = []
        for expected_name, future in self._waiters:
            if not future.done() and expected_name == event_name:
                future.set_result(event)
            elif not future.done():
                remaining.append((expected_name, future))
        self._waiters = remaining

        for callback in list(self._callbacks):
            result = callback(event)
            if inspect.isawaitable(result):
                asyncio.create_task(result)

    async def wait_for(self, event_name: str, timeout: float | None = None) -> BrowserEvent:
        for event in reversed(self._events):
            if event.__class__.__name__ == event_name:
                return event

        loop = asyncio.get_running_loop()
        future: asyncio.Future[BrowserEvent] = loop.create_future()
        self._waiters.append((event_name, future))
        return await asyncio.wait_for(future, timeout=timeout)


class SessionManager:
    """Single source of truth for tabs owned or observed by a browser session."""

    def __init__(self) -> None:
        self._tabs: dict[str, BrowserTab] = {}
        self._page_to_tab_id: dict[int, str] = {}
        self.active_tab_id: str | None = None

    @property
    def tabs(self) -> list[BrowserTab]:
        return list(self._tabs.values())

    def add_page(self, page: Any) -> tuple[BrowserTab, bool]:
        page_key = id(page)
        existing_id = self._page_to_tab_id.get(page_key)
        if existing_id is not None:
            tab = self._tabs[existing_id]
            tab.url = getattr(page, "url", tab.url)
            return tab, False

        tab = BrowserTab(id=str(uuid.uuid4()), url=getattr(page, "url", "about:blank"), page=page)
        self._tabs[tab.id] = tab
        self._page_to_tab_id[page_key] = tab.id
        self.active_tab_id = tab.id
        return tab, True

    def remove_tab(self, tab_id: str) -> BrowserTab | None:
        tab = self._tabs.pop(tab_id, None)
        if tab is None:
            return None
        self._page_to_tab_id.pop(id(tab.page), None)
        if self.active_tab_id == tab_id:
            self.active_tab_id = next(reversed(self._tabs), None) if self._tabs else None
        return tab

    def remove_page(self, page: Any) -> BrowserTab | None:
        tab_id = self._page_to_tab_id.get(id(page))
        if tab_id is None:
            return None
        return self.remove_tab(tab_id)

    def get_tab(self, tab_id: str) -> BrowserTab:
        try:
            return self._tabs[tab_id]
        except KeyError as exc:
            raise KeyError(f"Unknown tab id: {tab_id}") from exc

    def get_active_tab(self) -> BrowserTab:
        if self.active_tab_id is None:
            raise RuntimeError("Browser session has no active tab")
        return self.get_tab(self.active_tab_id)

    def set_active(self, tab_id: str) -> BrowserTab:
        tab = self.get_tab(tab_id)
        self.active_tab_id = tab_id
        return tab

    async def refresh_tab(self, tab: BrowserTab) -> BrowserTab:
        tab.url = getattr(tab.page, "url", tab.url)
        with contextlib.suppress(Exception):
            tab.title = await tab.page.title()
        return tab


class BrowserSession:
    def __init__(
        self,
        profile: BrowserProfile | None = None,
        cdp_url: str | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.profile = profile or BrowserProfile()
        self.cdp_url = cdp_url
        self.event_bus = event_bus or EventBus()
        self.session_manager = SessionManager()
        self.browser_pid: int | None = None
        self.is_closed = False

        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._process: subprocess.Popen[Any] | None = None
        self._temp_profile: tempfile.TemporaryDirectory[str] | None = None
        self._closing = False
        self._disconnected_emitted = False
        self._watchdogs: list[Any] = []

    @property
    def tabs(self) -> list[BrowserTab]:
        return self.session_manager.tabs

    def on_event(self, callback: EventCallback) -> None:
        self.event_bus.subscribe(callback)

    async def wait_for_event(self, event_name: str, timeout: float | None = None) -> BrowserEvent:
        return await self.event_bus.wait_for(event_name, timeout=timeout)

    async def start(self) -> "BrowserSession":
        if self._browser is not None and not self.is_closed:
            return self

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        if self.cdp_url:
            connect_url = self.cdp_url
        else:
            connect_url = await self._launch_browser_process()

        self._browser = await self._playwright.chromium.connect_over_cdp(connect_url)
        self._browser.on("disconnected", lambda: asyncio.create_task(self._handle_disconnected()))
        self._context = await self._resolve_context()
        self._context.on("page", lambda page: asyncio.create_task(self._register_page(page)))

        await self._install_watchdogs()
        for page in self._context.pages:
            await self._register_page(page)
        if not self.tabs:
            await self._register_page(await self._context.new_page())

        self.is_closed = False
        self.event_bus.emit(BrowserConnectedEvent(session=self, cdp_url=connect_url))
        return self

    async def navigate(self, url: str) -> None:
        self._ensure_started()
        self._enforce_allowed_domain(url)
        page = self.session_manager.get_active_tab().page
        await page.goto(url, wait_until="load")
        await self.session_manager.refresh_tab(self.session_manager.get_active_tab())

    async def open_tab(self, url: str | None = None) -> BrowserTab:
        self._ensure_started()
        if url is not None:
            self._enforce_allowed_domain(url)
            blank_tab = self._reusable_blank_tab()
            if blank_tab is not None:
                await blank_tab.page.goto(url, wait_until="load")
                self.session_manager.set_active(blank_tab.id)
                await self.session_manager.refresh_tab(blank_tab)
                return blank_tab
        page = await self._context.new_page()
        await self._apply_viewport(page)
        tab, _ = await self._register_page(page)
        if url is not None:
            await page.goto(url, wait_until="load")
        await self.session_manager.refresh_tab(tab)
        return tab

    async def switch_tab(self, tab_id: str) -> BrowserTab:
        tab = self.session_manager.set_active(tab_id)
        with contextlib.suppress(Exception):
            await tab.page.bring_to_front()
        await self.session_manager.refresh_tab(tab)
        return tab

    async def close_tab(self, tab_id: str) -> None:
        tab = self.session_manager.get_tab(tab_id)
        with contextlib.suppress(Exception):
            await tab.page.close()
        removed = self.session_manager.remove_tab(tab_id)
        if removed is not None:
            self.event_bus.emit(TabClosedEvent(session=self, tab_id=removed.id, url=removed.url))

    async def get_title(self) -> str:
        self._ensure_started()
        tab = self.session_manager.get_active_tab()
        tab.title = await tab.page.title()
        return tab.title

    async def get_current_url(self) -> str:
        self._ensure_started()
        tab = self.session_manager.get_active_tab()
        tab.url = getattr(tab.page, "url", tab.url)
        return tab.url

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        self._ensure_started()
        page = self.session_manager.get_active_tab().page
        if arg is None:
            return await page.evaluate(expression)
        return await page.evaluate(expression, arg)

    async def close(self) -> None:
        if self.is_closed:
            return

        self._closing = True
        for watchdog in self._watchdogs:
            with contextlib.suppress(Exception):
                await watchdog.stop()

        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()

        await self._stop_owned_process()

        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()

        if self._temp_profile is not None:
            self._temp_profile.cleanup()
            self._temp_profile = None

        self.is_closed = True
        self._browser = None
        self._context = None
        if not self._disconnected_emitted:
            self._emit_disconnected()

    async def _launch_browser_process(self) -> str:
        port = self.profile.cdp_port or _find_free_port()
        user_data_dir = self.profile.user_data_dir
        if user_data_dir is None:
            self._temp_profile = tempfile.TemporaryDirectory(prefix="browser-use-")
            user_data_dir = self._temp_profile.name
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        args = [
            self._playwright.chromium.executable_path,
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-popup-blocking",
        ]
        if self.profile.headless:
            args.append("--headless=new")
        proxy_server = _proxy_server(self.profile.proxy)
        if proxy_server:
            args.append(f"--proxy-server={proxy_server}")
        args.append("about:blank")

        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.browser_pid = self._process.pid
        cdp_url = f"http://127.0.0.1:{port}"
        await _wait_for_cdp(cdp_url, self._process)
        return cdp_url

    async def _resolve_context(self) -> Any:
        contexts = self._browser.contexts
        if contexts:
            context = contexts[0]
        else:
            context = await self._browser.new_context(viewport=self.profile.viewport.model_dump())
        return context

    async def _register_page(self, page: Any) -> tuple[BrowserTab, bool]:
        await self._apply_viewport(page)
        page.on("close", lambda: asyncio.create_task(self._handle_page_closed(page)))
        page.on("framenavigated", lambda _frame: self._refresh_page_url(page))
        tab, created = self.session_manager.add_page(page)
        await self.session_manager.refresh_tab(tab)
        self.session_manager.active_tab_id = tab.id
        if created:
            self.event_bus.emit(TabCreatedEvent(session=self, tab_id=tab.id, url=tab.url))
        return tab, created

    async def _handle_page_closed(self, page: Any) -> None:
        tab = self.session_manager.remove_page(page)
        if tab is not None:
            self.event_bus.emit(TabClosedEvent(session=self, tab_id=tab.id, url=tab.url))

    async def _handle_disconnected(self) -> None:
        if not self._closing and not self.is_closed:
            self.event_bus.emit(BrowserCrashedEvent(session=self, pid=self.browser_pid))
        self._emit_disconnected()

    async def _install_watchdogs(self) -> None:
        from browser_use.browser.watchdogs import CrashWatchdog, PopupWatchdog

        self._watchdogs = [PopupWatchdog(self), CrashWatchdog(self)]
        for watchdog in self._watchdogs:
            await watchdog.start()

    async def _apply_viewport(self, page: Any) -> None:
        if self.cdp_url and self.profile == BrowserProfile():
            return
        viewport = self.profile.viewport
        with contextlib.suppress(Exception):
            await page.set_viewport_size({"width": viewport.width, "height": viewport.height})

    def _refresh_page_url(self, page: Any) -> None:
        tab_id = self.session_manager._page_to_tab_id.get(id(page))
        if tab_id is not None:
            self.session_manager._tabs[tab_id].url = getattr(page, "url", self.session_manager._tabs[tab_id].url)

    def _ensure_started(self) -> None:
        if self._browser is None or self._context is None:
            raise RuntimeError("BrowserSession has not been started")

    def _reusable_blank_tab(self) -> BrowserTab | None:
        tabs = self.session_manager.tabs
        if len(tabs) != 1:
            return None
        tab = tabs[0]
        page_url = getattr(tab.page, "url", tab.url)
        if page_url in {"", "about:blank", "chrome://newtab/"}:
            return tab
        return None

    def _enforce_allowed_domain(self, url: str) -> None:
        allowed_domains = self.profile.allowed_domains
        if not allowed_domains:
            return

        parsed = urlparse(url)
        hostname = parsed.hostname
        if hostname is None:
            return

        normalized_allowed = {domain.lower() for domain in allowed_domains}
        hostname = hostname.lower()
        if hostname in normalized_allowed:
            return
        if any(hostname.endswith(f".{domain}") for domain in normalized_allowed):
            return
        raise BrowserSecurityError(f"Navigation to {hostname!r} is not allowed")

    async def _stop_owned_process(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                await asyncio.to_thread(self._process.wait, 5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self._process.wait, 5)
        self._process = None

    def _emit_disconnected(self) -> None:
        if self._disconnected_emitted:
            return
        self._disconnected_emitted = True
        self.event_bus.emit(BrowserDisconnectedEvent(session=self))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _proxy_server(proxy: str | dict[str, Any] | None) -> str | None:
    if proxy is None:
        return None
    if isinstance(proxy, str):
        return proxy
    server = proxy.get("server")
    return str(server) if server else None


async def _wait_for_cdp(cdp_url: str, process: subprocess.Popen[Any], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    version_url = f"{cdp_url.rstrip('/')}/json/version"
    last_error: Exception | None = None

    while asyncio.get_running_loop().time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Browser process exited before CDP was ready: {process.returncode}")
        try:
            await asyncio.to_thread(_read_url, version_url)
            return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            await asyncio.sleep(0.1)

    raise TimeoutError(f"Timed out waiting for browser CDP endpoint at {cdp_url}") from last_error


def _read_url(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=0.5) as response:
        return response.read()


__all__ = ["BrowserSession", "BrowserTab", "EventBus", "SessionManager"]
