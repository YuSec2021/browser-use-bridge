from __future__ import annotations

import inspect
import random
from typing import Any


class HumanCursor:
    def __init__(self, page: Any, cfg: Any) -> None:
        self._page = page
        self._cfg = cfg
        self._cursor = _create_cursor(page)

    async def move_and_click(self, point: dict[str, Any]) -> None:
        await self._move_to(point)
        await self._wait(random.randint(20, 90))
        await self._page.mouse.down()
        await self._wait(random.randint(30, 110))
        await self._page.mouse.up()

    async def _move_to(self, point: dict[str, Any]) -> None:
        cursor = self._cursor
        if inspect.isawaitable(cursor):
            cursor = await cursor
            self._cursor = cursor
        move_to = getattr(cursor, "move_to", None)
        if callable(move_to):
            await move_to({"x": point["x"], "y": point["y"]})
            return
        await cursor.move({"x": point["x"], "y": point["y"]})

    async def _wait(self, milliseconds: int) -> None:
        wait_for_timeout = getattr(self._page, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            await wait_for_timeout(milliseconds)


def _create_cursor(page: Any) -> Any:
    from python_ghost_cursor.playwright_async import create_cursor

    return create_cursor(page)
