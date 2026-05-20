from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from browser_use_bridge.browser.pool import (
    BrowserPool,
    BrowserProfile,
    ChromeLaunchResult,
    PoolStatus,
    build_chrome_args,
)


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0


class FakeBrowser:
    def __init__(self, cdp_url: str) -> None:
        self.cdp_url = cdp_url
        self.connected = True
        self.closed = False
        self.callbacks: dict[str, Any] = {}

    def is_connected(self) -> bool:
        return self.connected

    def on(self, event: str, callback: Any) -> None:
        self.callbacks[event] = callback

    async def close(self) -> None:
        self.closed = True
        self.connected = False


class FakeChromium:
    executable_path = "/fake/chromium"

    def __init__(self) -> None:
        self.connected_urls: list[str] = []
        self.launch_calls = 0
        self.browsers: list[FakeBrowser] = []

    async def connect_over_cdp(self, cdp_url: str) -> FakeBrowser:
        self.connected_urls.append(cdp_url)
        browser = FakeBrowser(cdp_url)
        self.browsers.append(browser)
        return browser

    async def launch(self, *_: Any, **__: Any) -> None:
        self.launch_calls += 1
        raise AssertionError("BrowserPool must attach with connect_over_cdp, not chromium.launch")


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class RecordingLauncher:
    def __init__(self, *, block_after: int | None = None) -> None:
        self.launches: list[ChromeLaunchResult] = []
        self.profiles: list[BrowserProfile] = []
        self.processes: list[FakeProcess] = []
        self.block_after = block_after
        self._blocked: list[asyncio.Future[None]] = []

    async def launch(
        self,
        *,
        chromium: Any,
        profile: BrowserProfile,
        slot_index: int,
        port: int,
        user_data_dir: str,
    ) -> ChromeLaunchResult:
        if self.block_after is not None and len(self.launches) >= self.block_after:
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._blocked.append(future)
            await future
        process = FakeProcess()
        args = build_chrome_args(
            executable_path=chromium.executable_path,
            profile=profile,
            port=port,
            user_data_dir=user_data_dir,
        )
        result = ChromeLaunchResult(
            cdp_url=f"http://127.0.0.1:{port}",
            port=port,
            user_data_dir=user_data_dir,
            args=args,
            process=process,
            owned_process=True,
        )
        self.launches.append(result)
        self.profiles.append(profile)
        self.processes.append(process)
        return result

    def unblock_recovery(self) -> None:
        for future in self._blocked:
            if not future.done():
                future.set_result(None)


def test_public_api_and_status_model(tmp_path: Path) -> None:
    profile = BrowserProfile(headless=True, user_data_dir_base=str(tmp_path))
    pool = BrowserPool(pool_size=2, profile=profile)

    initial = pool.status()

    assert isinstance(initial, PoolStatus)
    assert initial.pool_size == 2
    assert initial.active_count == 0
    assert initial.idle_count == 0
    assert initial.recovering_count == 0
    assert isinstance(initial.average_wait_time_ms, float)


def test_status_after_startup_reports_two_idle_browsers(tmp_path: Path) -> None:
    playwright = FakePlaywright()
    launcher = RecordingLauncher()

    async def run() -> PoolStatus:
        pool = BrowserPool(
            pool_size=2,
            profile=BrowserProfile(user_data_dir_base=str(tmp_path)),
            launcher=launcher,
            playwright_factory=lambda: playwright,
        )
        await pool.start()
        status = pool.status()
        await pool.shutdown()
        return status

    status = asyncio.run(run())

    assert status.pool_size == 2
    assert status.active_count == 0
    assert status.idle_count == 2
    assert status.recovering_count == 0


def test_startup_uses_persistent_chrome_and_cdp_attach(tmp_path: Path) -> None:
    playwright = FakePlaywright()
    launcher = RecordingLauncher()

    async def run() -> BrowserPool:
        pool = BrowserPool(
            pool_size=2,
            profile=BrowserProfile(user_data_dir_base=str(tmp_path)),
            base_port=9333,
            launcher=launcher,
            playwright_factory=lambda: playwright,
        )
        await pool.start()
        return pool

    pool = asyncio.run(run())

    ports = [launch.port for launch in launcher.launches]
    assert ports == [9333, 9334]
    assert len(set(launch.user_data_dir for launch in launcher.launches)) == 2
    assert all("--user-data-dir=" in " ".join(launch.args) for launch in launcher.launches)
    assert playwright.chromium.connected_urls == ["http://127.0.0.1:9333", "http://127.0.0.1:9334"]
    assert playwright.chromium.launch_calls == 0
    asyncio.run(pool.shutdown())


def test_acquire_release_and_backpressure(tmp_path: Path) -> None:
    playwright = FakePlaywright()
    launcher = RecordingLauncher()

    async def run() -> tuple[str, str, str, PoolStatus, PoolStatus]:
        pool = BrowserPool(
            pool_size=2,
            profile=BrowserProfile(user_data_dir_base=str(tmp_path)),
            launcher=launcher,
            playwright_factory=lambda: playwright,
        )
        await pool.start()
        first = await pool.acquire()
        second = await pool.acquire()
        saturated = pool.status()
        pending = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0)
        assert not pending.done()
        await pool.release(first)
        third = await asyncio.wait_for(pending, timeout=1)
        after_reacquire = pool.status()
        await pool.release(second)
        await pool.release(third)
        await pool.shutdown()
        return first.id, second.id, third.id, saturated, after_reacquire

    first_id, second_id, third_id, saturated, after_reacquire = asyncio.run(run())

    assert first_id != second_id
    assert third_id == first_id
    assert saturated.active_count == 2
    assert saturated.idle_count == 0
    assert after_reacquire.active_count == 2
    assert after_reacquire.idle_count == 0


def test_health_crash_recovery_replaces_unhealthy_browser(tmp_path: Path) -> None:
    playwright = FakePlaywright()
    launcher = RecordingLauncher(block_after=1)

    async def run() -> tuple[PoolStatus, PoolStatus, str, str]:
        pool = BrowserPool(
            pool_size=1,
            profile=BrowserProfile(user_data_dir_base=str(tmp_path)),
            launcher=launcher,
            playwright_factory=lambda: playwright,
        )
        await pool.start()
        handle = await pool.acquire()
        handle.browser.connected = False
        await pool.release(handle)
        recovering = pool.status()
        assert recovering.recovering_count == 1
        while not launcher._blocked:
            await asyncio.sleep(0)
        launcher.unblock_recovery()
        replacement = await asyncio.wait_for(pool.acquire(), timeout=1)
        recovered = pool.status()
        await pool.release(replacement)
        await pool.shutdown()
        return recovering, recovered, handle.id, replacement.id

    recovering, recovered, original_id, replacement_id = asyncio.run(run())

    assert recovering.recovering_count == 1
    assert recovered.active_count == 1
    assert recovered.recovering_count == 0
    assert replacement_id != original_id
    assert len(launcher.launches) == 2


def test_profile_launcher_args_include_ports_proxy_and_security_toggles(tmp_path: Path) -> None:
    playwright = FakePlaywright()
    launcher = RecordingLauncher()
    profile = BrowserProfile(
        headless=True,
        viewport={"width": 1440, "height": 900},
        user_data_dir_base=str(tmp_path),
        proxy={"server": "http://proxy.internal:8080"},
        ignore_certificate_errors=True,
        disable_web_security=True,
        disable_site_isolation_trials=True,
        no_sandbox=True,
        extra_chrome_args=["--disable-gpu"],
    )

    async def run() -> ChromeLaunchResult:
        pool = BrowserPool(pool_size=1, profile=profile, launcher=launcher, playwright_factory=lambda: playwright)
        await pool.start()
        launch = launcher.launches[0]
        await pool.shutdown()
        return launch

    launch = asyncio.run(run())
    args = " ".join(launch.args)

    assert "--remote-debugging-port=9222" in args
    assert f"--user-data-dir={tmp_path}" in args
    assert "--headless=new" in args
    assert "--window-size=1440,900" in args
    assert "--proxy-server=http://proxy.internal:8080" in args
    assert "--ignore-certificate-errors" in args
    assert "--disable-web-security" in args
    assert "--disable-site-isolation-trials" in args
    assert "--no-sandbox" in args
    assert "--disable-gpu" in args


def test_idempotent_startup_shutdown_and_stale_waiters(tmp_path: Path) -> None:
    playwright = FakePlaywright()
    launcher = RecordingLauncher()

    async def run() -> tuple[int, bool, bool, bool]:
        pool = BrowserPool(
            pool_size=1,
            profile=BrowserProfile(user_data_dir_base=str(tmp_path)),
            launcher=launcher,
            playwright_factory=lambda: playwright,
        )
        await pool.start()
        await pool.start()
        handle = await pool.acquire()
        pending = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0)
        assert not pending.done()
        await pool.shutdown()
        with pytest.raises(RuntimeError):
            await pending
        return len(launcher.launches), launcher.processes[0].terminated, playwright.stopped, handle.browser.closed

    launches, terminated, stopped, closed = asyncio.run(run())

    assert launches == 1
    assert terminated is True
    assert stopped is True
    assert closed is True
