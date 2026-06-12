from __future__ import annotations

import math
import random
from typing import Any


async def human_scroll(page: Any, amount: int, cfg: Any) -> None:
    steps = max(3, int(cfg.scroll_steps))
    done = 0

    for index in range(1, steps + 1):
        progress = index / steps
        eased = 0.5 * (1 - math.cos(math.pi * progress))
        target = int(amount * eased)
        delta = target - done
        done = target
        await page.mouse.wheel(0, delta)
        await page.wait_for_timeout(random.randint(15, 45))
