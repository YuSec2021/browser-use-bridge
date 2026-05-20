from __future__ import annotations

import asyncio
import contextlib
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from browser_use_bridge.config import BrowserProfile


class PoolStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool_size: int
    active_count: int
    idle_count: int
    recovering_count: int
    average_wait_time_ms: float


@dataclass(frozen=True)
class BrowserHandle:
    id: str
    slot_index: int
    cdp_url: str
    browser: Any
    profile: BrowserProfile
    user_data_dir: str
    process: Any = None


@dataclass(frozen=True)
class ChromeLaunchResult:
    cdp_url: str
    port: int
    user_data_dir: str
    args: list[str]
    process: Any = None
    owned_process: bool = True
    reattached: bool = False


@dataclass
class _BrowserSlot:
    index: int
    port: int
    profile: BrowserProfile
    state: str = "idle"
    id: str = field(default_factory=lambda: f"browser-{uuid.uuid4().hex}")
    cdp_url: str = ""
    browser: Any = None
    process: Any = None
    user_data_dir: str = ""
    owned_process: bool = True
    launch_args: list[str] = field(default_factory=list)
    needs_recovery: bool = False
    recovery_task: asyncio.Task[None] | None = None

    def handle(self) -> BrowserHandle:
        return BrowserHandle(
            id=self.id,
            slot_index=self.index,
            cdp_url=self.cdp_url,
            browser=self.browser,
            profile=self.profile,
            user_data_dir=self.user_data_dir,
            process=self.process,
        )


class ChromeLauncher:
    """Launch or reattach to persistent Chrome instances for BrowserPool slots."""

    def __init__(self, cdp_wait_timeout: float = 10.0) -> None:
        self.cdp_wait_timeout = cdp_wait_timeout
        self.launches: list[ChromeLaunchResult] = []

    async def launch(
        self,
        *,
        chromium: Any,
        profile: BrowserProfile,
        slot_index: int,
        port: int,
        user_data_dir: str,
    ) -> ChromeLaunchResult:
        cdp_url = f"http://127.0.0.1:{port}"
        if await _cdp_available(cdp_url):
            result = ChromeLaunchResult(
                cdp_url=cdp_url,
                port=port,
                user_data_dir=user_data_dir,
                args=[],
                process=None,
                owned_process=False,
                reattached=True,
            )
            self.launches.append(result)
            return result

        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        args = build_chrome_args(
            executable_path=_chrome_executable_path(chromium, profile),
            profile=profile,
            port=port,
            user_data_dir=user_data_dir,
        )
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await _wait_for_cdp(cdp_url, process, timeout=self.cdp_wait_timeout)
        result = ChromeLaunchResult(
            cdp_url=cdp_url,
            port=port,
            user_data_dir=user_data_dir,
            args=args,
            process=process,
            owned_process=True,
        )
        self.launches.append(result)
        return result


class BrowserPool:
    """Async pool of independent persistent Chrome instances attached through CDP."""

    def __init__(
        self,
        pool_size: int = 2,
        profile: BrowserProfile | None = None,
        *,
        base_port: int = 9222,
        launcher: ChromeLauncher | None = None,
        playwright_factory: Callable[[], Any] | None = None,
    ) -> None:
        if pool_size < 1:
            raise ValueError("pool_size must be at least 1")
        self.pool_size = pool_size
        self.profile = profile or BrowserProfile()
        self.base_port = base_port
        self.launcher = launcher or ChromeLauncher()
        self._playwright_factory = playwright_factory

        self._condition = asyncio.Condition()
        self._slots: list[_BrowserSlot] = []
        self._playwright: Any = None
        self._started = False
        self._shutdown = False
        self._temp_profile_root: tempfile.TemporaryDirectory[str] | None = None
        self._wait_times_ms: list[float] = []

    async def start(self) -> "BrowserPool":
        async with self._condition:
            if self._started and not self._shutdown:
                return self
            self._shutdown = False
            if self._playwright is None:
                self._playwright = await self._start_playwright()

            chromium = self._playwright.chromium
            slots: list[_BrowserSlot] = []
            for index in range(self.pool_size):
                slot = await self._create_slot(index, chromium)
                slots.append(slot)
            self._slots = slots
            self._started = True
            self._condition.notify_all()
            return self

    async def acquire(self) -> BrowserHandle:
        started_waiting = time.perf_counter()
        async with self._condition:
            while True:
                if self._shutdown:
                    raise RuntimeError("BrowserPool is shut down")
                if not self._started:
                    raise RuntimeError("BrowserPool has not been started")

                for slot in self._slots:
                    if slot.state != "idle":
                        continue
                    if await self._is_slot_healthy(slot):
                        slot.state = "active"
                        self._record_wait_time(started_waiting)
                        return slot.handle()
                    await self._mark_recovering_locked(slot)

                await self._condition.wait()

    async def release(self, handle: BrowserHandle) -> None:
        async with self._condition:
            slot = self._slot_for_handle(handle)
            if slot is None:
                raise ValueError(f"Unknown browser handle: {handle.id}")
            if slot.state != "active":
                return

            if slot.needs_recovery or not await self._is_slot_healthy(slot):
                await self._mark_recovering_locked(slot)
            else:
                slot.state = "idle"
            self._condition.notify_all()

    def status(self) -> PoolStatus:
        if not self._started and not self._slots:
            pool_size = self.pool_size
        else:
            pool_size = len(self._slots)
        active_count = sum(1 for slot in self._slots if slot.state == "active")
        idle_count = sum(1 for slot in self._slots if slot.state == "idle")
        recovering_count = sum(1 for slot in self._slots if slot.state == "recovering" or slot.needs_recovery)
        average_wait = sum(self._wait_times_ms) / len(self._wait_times_ms) if self._wait_times_ms else 0.0
        return PoolStatus(
            pool_size=pool_size,
            active_count=active_count,
            idle_count=idle_count,
            recovering_count=recovering_count,
            average_wait_time_ms=average_wait,
        )

    async def shutdown(self) -> None:
        async with self._condition:
            self._shutdown = True
            self._started = False
            self._condition.notify_all()
            slots = list(self._slots)

        for slot in slots:
            if slot.recovery_task is not None and not slot.recovery_task.done():
                slot.recovery_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await slot.recovery_task
            await self._close_slot(slot)

        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await _maybe_await(self._playwright.stop())
            self._playwright = None

        if self._temp_profile_root is not None:
            self._temp_profile_root.cleanup()
            self._temp_profile_root = None

        async with self._condition:
            self._slots = []
            self._condition.notify_all()

    async def _start_playwright(self) -> Any:
        if self._playwright_factory is not None:
            manager = self._playwright_factory()
        else:
            from playwright.async_api import async_playwright

            manager = async_playwright()
        if hasattr(manager, "start"):
            return await _maybe_await(manager.start())
        return await _maybe_await(manager)

    async def _create_slot(self, index: int, chromium: Any) -> _BrowserSlot:
        port = self._slot_port(index)
        user_data_dir = self._slot_user_data_dir(index)
        profile = self.profile.model_copy(update={"cdp_port": port, "user_data_dir": user_data_dir})
        launch = await self.launcher.launch(
            chromium=chromium,
            profile=profile,
            slot_index=index,
            port=port,
            user_data_dir=user_data_dir,
        )
        browser = await chromium.connect_over_cdp(launch.cdp_url)
        slot = _BrowserSlot(
            index=index,
            port=port,
            profile=profile,
            id=f"browser-{index}-{uuid.uuid4().hex}",
            cdp_url=launch.cdp_url,
            browser=browser,
            process=launch.process,
            user_data_dir=launch.user_data_dir,
            owned_process=launch.owned_process,
            launch_args=list(launch.args),
        )
        with contextlib.suppress(Exception):
            browser.on("disconnected", lambda: asyncio.create_task(self._handle_disconnected(slot.id)))
        return slot

    def _slot_port(self, index: int) -> int:
        if self.profile.cdp_port is not None:
            return self.profile.cdp_port + index
        return self.base_port + index

    def _slot_user_data_dir(self, index: int) -> str:
        configured_root = self.profile.user_data_dir_base or self.profile.user_data_dir
        if configured_root:
            root = Path(configured_root).expanduser()
            if self.pool_size == 1 and self.profile.user_data_dir and not self.profile.user_data_dir_base:
                return str(root)
            return str(root / f"browser-{index}")
        if self._temp_profile_root is None:
            self._temp_profile_root = tempfile.TemporaryDirectory(prefix="browser-use-bridge-pool-")
        return str(Path(self._temp_profile_root.name) / f"browser-{index}")

    def _slot_for_handle(self, handle: BrowserHandle) -> _BrowserSlot | None:
        return next((slot for slot in self._slots if slot.id == handle.id), None)

    async def _is_slot_healthy(self, slot: _BrowserSlot) -> bool:
        if slot.needs_recovery:
            return False
        if slot.process is not None and hasattr(slot.process, "poll") and slot.process.poll() is not None:
            return False
        browser = slot.browser
        is_connected = getattr(browser, "is_connected", None)
        if callable(is_connected):
            return bool(await _maybe_await(is_connected()))
        if is_connected is not None:
            return bool(is_connected)
        return browser is not None

    async def _mark_recovering_locked(self, slot: _BrowserSlot) -> None:
        slot.state = "recovering"
        slot.needs_recovery = True
        if slot.recovery_task is None or slot.recovery_task.done():
            slot.recovery_task = asyncio.create_task(self._recover_slot(slot))
        self._condition.notify_all()

    async def _recover_slot(self, slot: _BrowserSlot) -> None:
        await self._close_slot(slot)
        async with self._condition:
            if self._shutdown:
                return
        chromium = self._playwright.chromium
        replacement = await self._create_slot(slot.index, chromium)
        async with self._condition:
            if self._shutdown:
                await self._close_slot(replacement)
                return
            slot.id = replacement.id
            slot.cdp_url = replacement.cdp_url
            slot.browser = replacement.browser
            slot.process = replacement.process
            slot.user_data_dir = replacement.user_data_dir
            slot.owned_process = replacement.owned_process
            slot.launch_args = replacement.launch_args
            slot.profile = replacement.profile
            slot.state = "idle"
            slot.needs_recovery = False
            self._condition.notify_all()

    async def _handle_disconnected(self, slot_id: str) -> None:
        async with self._condition:
            slot = next((candidate for candidate in self._slots if candidate.id == slot_id), None)
            if slot is None or self._shutdown:
                return
            slot.needs_recovery = True
            if slot.state == "idle":
                await self._mark_recovering_locked(slot)
            self._condition.notify_all()

    async def _close_slot(self, slot: _BrowserSlot) -> None:
        if slot.browser is not None:
            with contextlib.suppress(Exception):
                await _maybe_await(slot.browser.close())
        if slot.owned_process and slot.process is not None:
            await _terminate_process(slot.process)

    def _record_wait_time(self, started_waiting: float) -> None:
        elapsed_ms = (time.perf_counter() - started_waiting) * 1000
        self._wait_times_ms.append(elapsed_ms)


def build_chrome_args(
    *,
    executable_path: str,
    profile: BrowserProfile,
    port: int,
    user_data_dir: str,
) -> list[str]:
    args = [
        executable_path,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-popup-blocking",
        f"--window-size={profile.viewport.width},{profile.viewport.height}",
    ]
    if profile.headless:
        args.append("--headless=new")
    proxy_server = _proxy_server(profile.proxy)
    if proxy_server:
        args.append(f"--proxy-server={proxy_server}")
    if profile.ignore_certificate_errors:
        args.append("--ignore-certificate-errors")
    if profile.disable_web_security:
        args.append("--disable-web-security")
    if profile.disable_site_isolation_trials:
        args.append("--disable-site-isolation-trials")
    if profile.no_sandbox:
        args.append("--no-sandbox")
    args.extend(profile.extra_chrome_args)
    args.append("about:blank")
    return args


def _chrome_executable_path(chromium: Any, profile: BrowserProfile) -> str:
    if profile.chrome_executable_path:
        return profile.chrome_executable_path
    executable_path = getattr(chromium, "executable_path", None)
    if executable_path:
        return str(executable_path)
    return "chromium"


def _proxy_server(proxy: str | dict[str, Any] | None) -> str | None:
    if proxy is None:
        return None
    if isinstance(proxy, str):
        return proxy
    server = proxy.get("server")
    return str(server) if server else None


async def _cdp_available(cdp_url: str) -> bool:
    try:
        await asyncio.to_thread(_read_url, f"{cdp_url.rstrip('/')}/json/version", 0.25)
        return True
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


async def _wait_for_cdp(cdp_url: str, process: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process is not None and hasattr(process, "poll") and process.poll() is not None:
            raise RuntimeError(f"Browser process exited before CDP was ready: {process.returncode}")
        try:
            await asyncio.to_thread(_read_url, f"{cdp_url.rstrip('/')}/json/version", 0.5)
            return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            await asyncio.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for browser CDP endpoint at {cdp_url}") from last_error


def _read_url(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


async def _terminate_process(process: Any) -> None:
    if hasattr(process, "poll") and process.poll() is not None:
        return
    if hasattr(process, "terminate"):
        process.terminate()
    if hasattr(process, "wait"):
        try:
            await asyncio.to_thread(process.wait, 5)
            return
        except TypeError:
            await asyncio.to_thread(process.wait)
            return
        except subprocess.TimeoutExpired:
            pass
    if hasattr(process, "kill"):
        process.kill()
        with contextlib.suppress(Exception):
            if hasattr(process, "wait"):
                await asyncio.to_thread(process.wait, 5)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


__all__ = [
    "BrowserHandle",
    "BrowserPool",
    "BrowserProfile",
    "ChromeLauncher",
    "ChromeLaunchResult",
    "PoolStatus",
    "build_chrome_args",
    "find_free_port",
]
